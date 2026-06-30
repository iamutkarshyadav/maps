import pandas as pd
import requests
import json
from ortools.constraint_solver import routing_enums_pb2
from ortools.constraint_solver import pywrapcp

TRUCK_CAPACITIES = {
    '6FT_TRUCK': 1000,
    '8FT_TRUCK': 2000,
    '10FT_TRUCK': 3000,
    '14FT_TRUCK': 4667,
    '17FT_TRUCK': 5467,
    '20FT_TRUCK': 7600,
    '22FT_TRUCK': 8400,
    '24FT_TRUCK': 8934,
    '32FT_TRUCK': 12000,
    '40FT_TRUCK': 16800
}

# Assume 15 trucks of each type are available (unlimited pool)
AVAILABLE_TRUCKS_PER_TYPE = 15
DISTANCE_MULTIPLIER = 1.05

def get_gh_distance(lat1, lon1, lat2, lon2):
    if lat1 == lat2 and lon1 == lon2: return 0.0
    try:
        url = f"http://127.0.0.1:8989/route?point={lat1},{lon1}&point={lat2},{lon2}&profile=truck&points_encoded=false"
        resp = requests.get(url)
        dist = resp.json()['paths'][0]['distance'] / 1000.0
        return dist * DISTANCE_MULTIPLIER
    except:
        return 99999.0

def build_solver():
    print("Loading Data...")
    
    # 1. Load S&OP
    sop = pd.read_excel('details.xlsx', sheet_name='S&OP')
    
    # 2. Load Coordinates from data.tsv
    data_tsv = pd.read_csv('data.tsv', sep='\t')
    if 'DH Lat Long' not in data_tsv.columns:
        data_tsv = pd.read_csv('data.tsv', sep=',')
        
    coords_map = {}
    for _, row in data_tsv.iterrows():
        coords_map[row['destination_store_name']] = row['DH Lat Long']
        
    # Depot
    depot_name = "BLR-DRY-MH-SUMADHURA"
    depot_lat, depot_lon = 13.14193, 77.86832
    
    nodes = [{'name': depot_name, 'demand': 0, 'max_cap': 99999, 'lat': depot_lat, 'lon': depot_lon}]
    
    for _, row in sop.iterrows():
        dh = row['DH']
        demand = int(row['w1']) if pd.notnull(row['w1']) else 0
        restriction_str = str(row['Vehicle restrictions']).strip()
        max_cap = TRUCK_CAPACITIES.get(restriction_str, 16800)
        
        lat_lon = coords_map.get(dh, "0,0")
        try:
            lat, lon = [float(x.strip()) for x in str(lat_lon).split(',')]
        except:
            lat, lon = 0.0, 0.0
            
        if demand == 0:
            nodes.append({'name': dh, 'original_name': dh, 'demand': 0, 'max_cap': max_cap, 'lat': lat, 'lon': lon})
        else:
            remaining = demand
            part = 1
            while remaining > 0:
                load = min(remaining, max_cap)
                name = f"{dh}_part{part}" if demand > max_cap else dh
                nodes.append({'name': name, 'original_name': dh, 'demand': load, 'max_cap': max_cap, 'lat': lat, 'lon': lon})
                remaining -= load
                part += 1
        
    n = len(nodes)
    print(f"Total Nodes: {n} (1 Depot + {n-1} DHs)")
    
    # 3. Load Distance Matrix
    print("Building Distance Matrix...")
    dist_df = pd.read_csv('dh_distance_matrix.csv')
    dist_lookup = {}
    for _, row in dist_df.iterrows():
        dist_lookup[(row['Origin_DH'], row['Dest_DH'])] = row['Distance_km']
        
    distance_matrix = [[0.0]*n for _ in range(n)]
    
    for i in range(n):
        for j in range(n):
            if i == j:
                distance_matrix[i][j] = 0.0
            elif i == 0:
                distance_matrix[i][j] = get_gh_distance(nodes[0]['lat'], nodes[0]['lon'], nodes[j]['lat'], nodes[j]['lon'])
            elif j == 0:
                distance_matrix[i][j] = get_gh_distance(nodes[i]['lat'], nodes[i]['lon'], nodes[0]['lat'], nodes[0]['lon'])
            else:
                orig_i = nodes[i]['original_name']
                orig_j = nodes[j]['original_name']
                if orig_i == orig_j:
                    distance_matrix[i][j] = 0.0
                else:
                    d = dist_lookup.get((orig_i, orig_j), -1.0)
                    if d < 0:
                        d = get_gh_distance(nodes[i]['lat'], nodes[i]['lon'], nodes[j]['lat'], nodes[j]['lon'])
                        dist_lookup[(orig_i, orig_j)] = d
                    distance_matrix[i][j] = d
                
    # 4. OR-Tools Setup
    print("Setting up OR-Tools Model...")
    
    # Create fleet
    vehicle_capacities = []
    vehicle_types = []
    for t_type, cap in TRUCK_CAPACITIES.items():
        for _ in range(AVAILABLE_TRUCKS_PER_TYPE):
            vehicle_capacities.append(cap)
            vehicle_types.append(t_type)
            
    num_vehicles = len(vehicle_capacities)
    depot_index = 0
    
    manager = pywrapcp.RoutingIndexManager(n, num_vehicles, depot_index)
    routing = pywrapcp.RoutingModel(manager)
    
    # Distance Callback
    def distance_callback(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(distance_matrix[from_node][to_node] * 1000) # scale to meters
        
    transit_callback_index = routing.RegisterTransitCallback(distance_callback)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)
    
    # Demand Callback
    def demand_callback(from_index):
        from_node = manager.IndexToNode(from_index)
        return int(nodes[from_node]['demand'])
        
    demand_callback_index = routing.RegisterUnaryTransitCallback(demand_callback)
    
    routing.AddDimensionWithVehicleCapacity(
        demand_callback_index,
        0,  
        [int(c) for c in vehicle_capacities],
        True,  
        'Capacity'
    )
    
    # Add Fixed Costs to prioritize geographic clustering (only cluster if detour is small)
    for v in range(num_vehicles):
        routing.SetFixedCostOfVehicle(int(vehicle_capacities[v] * 2), v)
        
    # Apply Site-Dependency Constraints (Vehicle Restrictions)
    for i in range(1, n):
        idx = manager.NodeToIndex(i)
        
        # Allow dropping node if absolutely necessary (e.g. impossible constraint)
        routing.AddDisjunction([idx], 999999999)
        
        max_cap = nodes[i]['max_cap']
        for v in range(num_vehicles):
            if vehicle_capacities[v] > max_cap:
                routing.VehicleVar(idx).RemoveValue(v)
                
    # Solve
    print("Solving VRP... (This will take up to 60 seconds)")
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    search_parameters.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    search_parameters.time_limit.seconds = 60
    
    solution = routing.SolveWithParameters(search_parameters)
    
    if not solution:
        print("No solution found! The constraints might be mathematically impossible.")
        return
        
    print("Solution Found! Exporting...")
    
    # Export Outputs
    routes_output = []
    milk_run_pairs = []
    
    for v in range(num_vehicles):
        index = routing.Start(v)
        if routing.IsEnd(solution.Value(routing.NextVar(index))):
            continue
            
        route_nodes = []
        route_load = 0
        route_dist = 0.0
        
        while not routing.IsEnd(index):
            node_idx = manager.IndexToNode(index)
            route_nodes.append(nodes[node_idx]['name'])
            route_load += nodes[node_idx]['demand']
            
            prev_index = index
            index = solution.Value(routing.NextVar(index))
            route_dist += distance_matrix[manager.IndexToNode(prev_index)][manager.IndexToNode(index)]
            
        route_nodes.append(depot_name)
        
        cap = vehicle_capacities[v]
        utilization = (route_load / cap) * 100
        
        routes_output.append({
            "Vehicle_ID": v,
            "Assigned_Truck": vehicle_types[v],
            "Truck_Capacity": cap,
            "Total_Load": route_load,
            "Utilization_%": round(utilization, 1),
            "Total_Distance_km": round(route_dist, 2),
            "Route": " -> ".join(route_nodes)
        })
        
        # Milk Run Pairs logic (if route has > 1 DH)
        dhs_only = route_nodes[1:-1]
        if len(dhs_only) > 1:
            for i, d in enumerate(dhs_only):
                milk_run_pairs.append({
                    "Vehicle_ID": v,
                    "Assigned_Truck": vehicle_types[v],
                    "Stop_Sequence": i + 1,
                    "DH_Name": d
                })
                
    pd.DataFrame(routes_output).to_csv("optimized_routes.csv", index=False)
    pd.DataFrame(milk_run_pairs).to_csv("milk_run_pairs.csv", index=False)
    
    print("Done! Check optimized_routes.csv and milk_run_pairs.csv")

if __name__ == "__main__":
    build_solver()
