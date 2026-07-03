import pandas as pd
import math
from collections import defaultdict
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp
from constants import (
    TRUCK_CAPACITIES, LOADING_TIMES, UNLOADING_TIMES,
    MH_DOCK_OVERHEAD, DH_DOCK_OVERHEAD,
    CONTRACT_CONFIG, MIXED_CONTRACT_MODE, SOLVER_TIME_LIMIT, MAX_STOPS_PER_ROUTE,
    resolve_truck_type, get_truck_capacity, calculate_vehicles_needed
)


def solve_for_mh(mh_name, mh_lat, mh_lon, mh_sop, coords_map,
                 dist_lookup, time_lookup, valid_locations):
    """
    Solve the VRP for a single Material Hub.

    In MIXED_CONTRACT_MODE the solver creates BOTH 12H and 24H
    virtual vehicles in the same pool. The optimizer picks the
    cheapest combination — 12H vehicles are cheaper when fully
    utilized (single trip, 660 min budget), 24H vehicles are used
    when 2 trips are needed.

    Returns:
        (routes_output, milk_run_pairs) — lists of dicts ready for CSV.
    """
    print(f"\n--- Solving for MH: {mh_name} ---")

    # ─── 1. Build Nodes (with split-delivery logic) ──────────────────
    if mh_name not in valid_locations:
        print(f"Skipping MH {mh_name} (Not found in distance matrix)")
        return [], []

    nodes = [{
        'name': mh_name,
        'original_name': mh_name,
        'demand': 0,
        'max_cap': 99999,
        'lat': mh_lat,
        'lon': mh_lon,
    }]

    for _, row in mh_sop.iterrows():
        dh = str(row['DH']).strip()
        if dh not in valid_locations:
            print(f"Skipping DH {dh} (Not found in distance matrix)")
            continue
        demand = int(row['w1']) if pd.notnull(row['w1']) else 0
        restriction_str = str(row['Vehicle restrictions']).strip()
        max_cap = get_truck_capacity(restriction_str)

        lat_lon = coords_map.get(dh, "0,0")
        try:
            lat, lon = [float(x.strip()) for x in str(lat_lon).split(',')]
        except (ValueError, AttributeError):
            lat, lon = 0.0, 0.0

        if demand == 0:
            nodes.append({
                'name': dh, 'original_name': dh, 'demand': 0,
                'max_cap': max_cap, 'lat': lat, 'lon': lon,
            })
        else:
            remaining = demand
            part = 1
            while remaining > 0:
                load = min(remaining, max_cap)
                name = f"{dh}_part{part}" if demand > max_cap else dh
                is_split = demand > max_cap
                nodes.append({
                    'name': name, 'original_name': dh, 'demand': load,
                    'max_cap': max_cap, 'lat': lat, 'lon': lon,
                    'is_split': is_split,
                })
                remaining -= load
                part += 1

    n = len(nodes)
    print(f"Total Nodes for {mh_name}: {n} (1 MH + {n - 1} DHs)")

    if n <= 1:
        print(f"No DH nodes for {mh_name}. Skipping.")
        return [], []

    # ─── 2. Build Distance & Time Matrices ───────────────────────────
    distance_matrix = [[0.0] * n for _ in range(n)]
    time_matrix = [[0.0] * n for _ in range(n)]

    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            orig_i = nodes[i]['original_name']
            orig_j = nodes[j]['original_name']
            if orig_i == orig_j:
                continue

            d = dist_lookup.get((orig_i, orig_j), -1.0)
            t = time_lookup.get((orig_i, orig_j), -1.0)

            if d < 0:
                print(f"Warning: Missing distance {orig_i} -> {orig_j}. "
                      f"Using penalty.")
                d = 99999.0
            if t < 0:
                t = (d / 30.0) * 60.0 if d < 99999 else 99999.0

            # FORCE SPLIT DELIVERIES TO BE DIRECT (1-STOP) ONLY
            # If either i or j is a split node (and neither is the depot 0),
            # heavily penalize the transit so the solver never links them in a milk run.
            if i != 0 and j != 0:
                if nodes[i].get('is_split') or nodes[j].get('is_split'):
                    d = 9999999.0
                    t = 9999999.0

            distance_matrix[i][j] = d
            time_matrix[i][j] = t

    # ─── 3. Build Mixed Vehicle Pool (12H + 24H) ─────────────────────
    total_demand = sum(node['demand'] for node in nodes)
    allowed_max_caps = set(node['max_cap'] for node in nodes[1:])

    vehicle_capacities = []
    vehicle_types = []
    vehicle_time_budgets = []
    vehicle_contract_types = []  # '12H' or '24H' per virtual vehicle

    # Decide which contract types to include
    if MIXED_CONTRACT_MODE:
        contract_types_to_use = ['12H', '24H']
    else:
        # Fallback: only 24H
        contract_types_to_use = ['24H']

    for contract_key in contract_types_to_use:
        contract = CONTRACT_CONFIG[contract_key]
        num_trips = contract['num_trips']
        minutes_per_trip = contract['minutes_per_trip']

        for t_type, cap in TRUCK_CAPACITIES.items():
            if not any(cap <= mc for mc in allowed_max_caps):
                continue

            max_needed = calculate_vehicles_needed(
                total_demand, t_type, buffer=2
            )

            # For 24H: each physical truck → 2 virtual vehicles
            # For 12H: each physical truck → 1 virtual vehicle
            total_virtual = max_needed * num_trips

            for _ in range(total_virtual):
                vehicle_capacities.append(cap)
                vehicle_types.append(t_type)
                vehicle_time_budgets.append(minutes_per_trip)
                vehicle_contract_types.append(contract_key)

    num_vehicles = len(vehicle_capacities)

    # Count for logging
    count_12h = vehicle_contract_types.count('12H')
    count_24h = vehicle_contract_types.count('24H')
    print(f"Vehicle pool for {mh_name}: {num_vehicles} virtual vehicles "
          f"(12H: {count_12h}, 24H: {count_24h})")

    # ─── 4. OR-Tools Setup ───────────────────────────────────────────
    depot_index = 0
    manager = pywrapcp.RoutingIndexManager(n, num_vehicles, depot_index)
    routing = pywrapcp.RoutingModel(manager)

    # --- Distance callback (arc cost) ---
    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(distance_matrix[from_node][to_node] * 1000)

    transit_cb_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_cb_index)

    # --- Capacity dimension (demand) ---
    def demand_callback(from_index):
        from_node = manager.IndexToNode(from_index)
        return int(nodes[from_node]['demand'])

    demand_cb_index = routing.RegisterUnaryTransitCallback(demand_callback)
    routing.AddDimensionWithVehicleCapacity(
        demand_cb_index,
        0,
        [int(c) for c in vehicle_capacities],
        True,
        'Capacity'
    )

    # ─── 5. Time Dimension (Vehicle-Dependent) ───────────────────────
    type_to_time_cb = {}

    for t_type in TRUCK_CAPACITIES:
        load_t = LOADING_TIMES[t_type]
        unload_t = UNLOADING_TIMES[t_type]

        def _make_time_cb(loading_time, unloading_time):
            def time_cb(from_index, to_index):
                from_node = manager.IndexToNode(from_index)
                to_node = manager.IndexToNode(to_index)
                travel = int(time_matrix[from_node][to_node])

                if from_node == 0:
                    service = MH_DOCK_OVERHEAD + loading_time
                else:
                    service = DH_DOCK_OVERHEAD + unloading_time
                return travel + service
            return time_cb

        cb = _make_time_cb(load_t, unload_t)
        type_to_time_cb[t_type] = routing.RegisterTransitCallback(cb)

    vehicle_time_cb_indices = [
        type_to_time_cb[vehicle_types[v]]
        for v in range(num_vehicles)
    ]

    max_time_budget = max(vehicle_time_budgets) if vehicle_time_budgets else 660
    routing.AddDimensionWithVehicleTransits(
        vehicle_time_cb_indices,
        0,
        max_time_budget,
        True,
        'Time'
    )

    time_dimension = routing.GetDimensionOrDie('Time')
    for v in range(num_vehicles):
        time_dimension.CumulVar(routing.End(v)).SetMax(
            vehicle_time_budgets[v]
        )

    # ─── 6. Max Stops Dimension ───────────────────────────────────────
    def stops_callback(from_index):
        from_node = manager.IndexToNode(from_index)
        # Depot (node 0) doesn't count as a stop. DHs count as 1 stop.
        return 0 if from_node == 0 else 1

    stops_cb_index = routing.RegisterUnaryTransitCallback(stops_callback)
    routing.AddDimension(
        stops_cb_index,
        0,  # no slack
        MAX_STOPS_PER_ROUTE,  # vehicle maximum capacities
        True,  # start cumul to zero
        'Stops'
    )

    # ─── 7. Fixed Cost & Vehicle Restrictions ────────────────────────
    # 24H vehicles are CHEAPER per trip than 12H vehicles of the same truck type
    # so the solver prefers them. 12H is a fallback when 24H isn't feasible.
    # Multiply by 100000 so vehicle capacity minimization dominates travel distance
    for v in range(num_vehicles):
        base_cost = int(vehicle_capacities[v])
        if vehicle_contract_types[v] == '24H':
            # 24H is cheapest — solver will prefer it
            routing.SetFixedCostOfVehicle(base_cost * 100000, v)
        else:
            # 12H costs 3x — solver picks it only as a fallback
            routing.SetFixedCostOfVehicle(base_cost * 300000, v)

    for i in range(1, n):
        idx = manager.NodeToIndex(i)
        routing.AddDisjunction([idx], 999999999)
        max_cap = nodes[i]['max_cap']
        for v in range(num_vehicles):
            if vehicle_capacities[v] > max_cap:
                routing.VehicleVar(idx).RemoveValue(v)

    # ─── 8. Solve ────────────────────────────────────────────────────
    print(f"Solving VRP for {mh_name}... (up to {SOLVER_TIME_LIMIT} seconds)")
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    )
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_parameters.time_limit.seconds = SOLVER_TIME_LIMIT

    solution = routing.SolveWithParameters(search_parameters)

    if not solution:
        print(f"No solution found for {mh_name}!")
        return [], []

    print(f"Solution found for {mh_name}!")

    # ─── 9. Extract Active Routes ────────────────────────────────────
    raw_routes = []

    for v in range(num_vehicles):
        index = routing.Start(v)
        if routing.IsEnd(solution.Value(routing.NextVar(index))):
            continue  # Vehicle not used

        route_nodes_list = []
        route_load = 0
        route_dist = 0.0
        route_travel_time = 0.0
        num_dh_stops = 0
        is_split_delivery = False

        while not routing.IsEnd(index):
            node_idx = manager.IndexToNode(index)
            route_nodes_list.append(nodes[node_idx]['name'])
            route_load += nodes[node_idx]['demand']
            if nodes[node_idx].get('is_split'):
                is_split_delivery = True
            if node_idx != 0:
                num_dh_stops += 1

            prev_index = index
            index = solution.Value(routing.NextVar(index))
            prev_node = manager.IndexToNode(prev_index)
            next_node = manager.IndexToNode(index)
            route_dist += distance_matrix[prev_node][next_node]
            route_travel_time += time_matrix[prev_node][next_node]

        route_nodes_list.append(mh_name)  # Return to depot

        cap = vehicle_capacities[v]
        weight_util = (route_load / cap * 100) if cap > 0 else 0

        truck_type = vehicle_types[v]
        contract_type = vehicle_contract_types[v]
        loading = LOADING_TIMES[truck_type]
        unloading = UNLOADING_TIMES[truck_type]

        mh_overhead = MH_DOCK_OVERHEAD
        total_loading = loading
        total_dh_overhead = DH_DOCK_OVERHEAD * num_dh_stops
        total_unloading = unloading * num_dh_stops
        total_trip_time = (
            mh_overhead + total_loading
            + route_travel_time
            + total_dh_overhead + total_unloading
        )

        time_budget = vehicle_time_budgets[v]
        time_util = (total_trip_time / time_budget * 100) \
            if time_budget > 0 else 0

        route_type = "Direct" if num_dh_stops == 1 else "Milk Run"

        raw_routes.append({
            'vehicle_index': v,
            'contract_type': contract_type,
            'truck_type': truck_type,
            'capacity': cap,
            'route_nodes': route_nodes_list,
            'route_load': route_load,
            'weight_util': round(weight_util, 1),
            'num_dh_stops': num_dh_stops,
            'route_type': route_type,
            'route_dist': round(route_dist, 2),
            'mh_overhead': mh_overhead,
            'loading_time': total_loading,
            'travel_time': round(route_travel_time, 1),
            'dh_overhead': total_dh_overhead,
            'unloading_time': total_unloading,
            'trip_time': round(total_trip_time, 1),
            'time_budget': time_budget,
            'time_util': round(time_util, 1),
            'is_split_delivery': is_split_delivery,
        })

    # ─── 10. Post-Hoc Physical Vehicle Pairing ────────────────────────
    # Separate active routes by contract type.
    # 12H routes: each is 1 physical vehicle (1 trip).
    # 24H routes: pair every 2 of the same truck type into 1 physical
    #             vehicle (Trip 1 + Trip 2). Odd ones out get an empty
    #             Trip 2 row for visibility.

    routes_12h = [r for r in raw_routes if r['contract_type'] == '12H']
    routes_24h = [r for r in raw_routes if r['contract_type'] == '24H']

    # 80% Time Utilization Check removed

    processed_24h = routes_24h

    routes_output = []
    milk_run_pairs = []
    physical_counter = 0

    # --- 12H vehicles: 1 physical vehicle = 1 trip ---
    for r in routes_12h:
        physical_counter += 1
        phys_id = f"{mh_name}_V{physical_counter:03d}"
        route_str = " -> ".join(r['route_nodes'])

        routes_output.append({
            "MH_Name": mh_name,
            "Physical_Vehicle_ID": phys_id,
            "Contract_Type": "12H",
            "Trip_Number": 1,
            "Assigned_Truck": r['truck_type'],
            "Truck_Capacity": r['capacity'],
            "Total_Load": r['route_load'],
            "Weight_Utilization_%": r['weight_util'],
            "Num_Stops": r['num_dh_stops'],
            "Route_Type": r['route_type'],
            "Total_Distance_km": r['route_dist'],
            "MH_Overhead_min": r['mh_overhead'],
            "Loading_Time_min": r['loading_time'],
            "Travel_Time_min": r['travel_time'],
            "DH_Overhead_min": r['dh_overhead'],
            "Unloading_Time_min": r['unloading_time'],
            "Total_Trip_Time_min": r['trip_time'],
            "Time_Budget_min": r['time_budget'],
            "Time_Utilization_%": r['time_util'],
            "Is_Split_Delivery": r['is_split_delivery'],
            "Route": route_str,
        })

        dhs_only = r['route_nodes'][1:-1]
        if len(dhs_only) > 1:
            for s_idx, dh in enumerate(dhs_only):
                milk_run_pairs.append({
                    "MH_Name": mh_name,
                    "Physical_Vehicle_ID": phys_id,
                    "Trip_Number": 1,
                    "Assigned_Truck": r['truck_type'],
                    "Stop_Sequence": s_idx + 1,
                    "DH_Name": dh,
                })

    # --- 24H vehicles: pair 2 active routes → 1 physical vehicle ---
    type_groups_24h_final = defaultdict(list)
    for r in processed_24h:
        type_groups_24h_final[r['truck_type']].append(r)

    for t_type, routes in type_groups_24h_final.items():
        for i in range(0, len(routes), 2):
            physical_counter += 1
            phys_id = f"{mh_name}_V{physical_counter:03d}"

            for trip_offset in range(2):
                if i + trip_offset < len(routes):
                    r = routes[i + trip_offset]
                    trip_num = trip_offset + 1
                    route_str = " -> ".join(r['route_nodes'])

                    routes_output.append({
                        "MH_Name": mh_name,
                        "Physical_Vehicle_ID": phys_id,
                        "Contract_Type": "24H",
                        "Trip_Number": trip_num,
                        "Assigned_Truck": r['truck_type'],
                        "Truck_Capacity": r['capacity'],
                        "Total_Load": r['route_load'],
                        "Weight_Utilization_%": r['weight_util'],
                        "Num_Stops": r['num_dh_stops'],
                        "Route_Type": r['route_type'],
                        "Total_Distance_km": r['route_dist'],
                        "MH_Overhead_min": r['mh_overhead'],
                        "Loading_Time_min": r['loading_time'],
                        "Travel_Time_min": r['travel_time'],
                        "DH_Overhead_min": r['dh_overhead'],
                        "Unloading_Time_min": r['unloading_time'],
                        "Total_Trip_Time_min": r['trip_time'],
                        "Time_Budget_min": r['time_budget'],
                        "Time_Utilization_%": r['time_util'],
                        "Is_Split_Delivery": r['is_split_delivery'],
                        "Route": route_str,
                    })

                    dhs_only = r['route_nodes'][1:-1]
                    if len(dhs_only) > 1:
                        for s_idx, dh in enumerate(dhs_only):
                            milk_run_pairs.append({
                                "MH_Name": mh_name,
                                "Physical_Vehicle_ID": phys_id,
                                "Trip_Number": trip_num,
                                "Assigned_Truck": r['truck_type'],
                                "Stop_Sequence": s_idx + 1,
                                "DH_Name": dh,
                            })

                elif trip_offset == 1:
                    # Empty Trip 2 (24H vehicle, only Trip 1 was needed)
                    first_route = routes[i]
                    routes_output.append({
                        "MH_Name": mh_name,
                        "Physical_Vehicle_ID": phys_id,
                        "Contract_Type": "24H",
                        "Trip_Number": 2,
                        "Assigned_Truck": first_route['truck_type'],
                        "Truck_Capacity": first_route['capacity'],
                        "Total_Load": 0,
                        "Weight_Utilization_%": 0.0,
                        "Num_Stops": 0,
                        "Route_Type": "—",
                        "Total_Distance_km": 0.0,
                        "MH_Overhead_min": 0,
                        "Loading_Time_min": 0,
                        "Travel_Time_min": 0.0,
                        "DH_Overhead_min": 0,
                        "Unloading_Time_min": 0,
                        "Total_Trip_Time_min": 0.0,
                        "Time_Budget_min": CONTRACT_CONFIG['24H']['minutes_per_trip'],
                        "Time_Utilization_%": 0.0,
                        "Is_Split_Delivery": False,
                        "Route": "—",
                    })

    # ── Summary stats ──
    active_12h = len(routes_12h)
    active_24h_trips = len(routes_24h)
    physical_24h = math.ceil(active_24h_trips / 2)
    print(f"Routes generated: {len(routes_output)} rows")
    print(f"  12H vehicles: {active_12h} physical (1 trip each)")
    print(f"  24H vehicles: {physical_24h} physical "
          f"({active_24h_trips} active trips)")
    print(f"  Total physical: {physical_counter}")

    return routes_output, milk_run_pairs


def build_solver():
    """Load data, solve VRP for each MH, and write output CSVs."""
    print("Loading Data...")

    # 1. Load S&OP
    sop = pd.read_excel('../data/inputs/details.xlsx', sheet_name='S&OP')
    sop['MH '] = sop['MH '].astype(str).str.strip()

    # 2. Load Coordinates from data.tsv
    data_tsv = pd.read_csv('../data/inputs/data.tsv', sep='\t')
    if 'DH Lat Long' not in data_tsv.columns:
        data_tsv = pd.read_csv('../data/inputs/data.tsv', sep=',')

    coords_map = {}
    for _, row in data_tsv.iterrows():
        coords_map[str(row['destination_store_name']).strip()] = \
            row['DH Lat Long']

    mh_coords_map = {}
    mh_data = data_tsv[
        ['origin_store_name', 'Origin Lat Long']
    ].drop_duplicates().dropna()
    for _, row in mh_data.iterrows():
        mh_coords_map[str(row['origin_store_name']).strip()] = \
            row['Origin Lat Long']

    # 3. Load Distance & Time Matrix
    print("Loading Distance & Time Matrix...")
    dist_df = pd.read_csv('../data/outputs/dh_distance_matrix.csv')
    valid_locations = set(dist_df['Origin_DH']).union(set(dist_df['Dest_DH']))

    dist_lookup = {}
    time_lookup = {}
    for _, row in dist_df.iterrows():
        key = (row['Origin_DH'], row['Dest_DH'])
        dist_lookup[key] = row['Distance_km']
        time_lookup[key] = row['Time_mins']

    # 4. Solve per MH
    unique_mhs = sop['MH '].unique()
    all_routes = []
    all_milk_runs = []

    for mh in unique_mhs:
        mh_sop = sop[sop['MH '] == mh]
        lat_lon = mh_coords_map.get(mh, "0,0")
        try:
            lat, lon = [float(x.strip()) for x in str(lat_lon).split(',')]
        except (ValueError, AttributeError):
            lat, lon = 0.0, 0.0

        r_out, m_pairs = solve_for_mh(
            mh, lat, lon, mh_sop, coords_map, dist_lookup, time_lookup,
            valid_locations
        )
        all_routes.extend(r_out)
        all_milk_runs.extend(m_pairs)

    # 5. Write output
    if all_routes:
        pd.DataFrame(all_routes).to_csv(
            "../data/outputs/optimized_routes.csv", index=False
        )
        pd.DataFrame(all_milk_runs).to_csv(
            "../data/outputs/milk_run_pairs.csv", index=False
        )
        print("\nDone! Check optimized_routes.csv and milk_run_pairs.csv")
    else:
        print("\nNo routes were generated across any MH.")


if __name__ == "__main__":
    build_solver()
