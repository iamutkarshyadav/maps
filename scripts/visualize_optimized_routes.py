import pandas as pd
import folium
import requests
import hashlib


def create_map():
    """
    Generate an interactive Folium map from optimized_routes.csv.

    Features:
      • Same color per Physical_Vehicle_ID across Trip 1 & Trip 2
      • Solid lines for Trip 1, dashed lines for Trip 2
      • Tooltip with time utilization bar and full breakdown
      • Layer control per MH
    """
    print("Loading data...")
    try:
        routes = pd.read_csv('../data/outputs/optimized_routes.csv')
    except Exception as e:
        print("Could not read optimized_routes.csv:", e)
        return

    try:
        data = pd.read_csv('../data/inputs/data.tsv', sep='\t')
        if 'DH Lat Long' not in data.columns:
            data = pd.read_csv('../data/inputs/data.tsv', sep=',')
    except Exception as e:
        print("Could not read data.tsv:", e)
        return

    # ─── Coordinate Maps ──────────────────────────────────────────────
    coords_map = {}
    for _, row in data.iterrows():
        coords_map[str(row['destination_store_name']).strip()] = \
            row['DH Lat Long']

    mh_coords_map = {}
    mh_data = data[
        ['origin_store_name', 'Origin Lat Long']
    ].drop_duplicates().dropna()
    for _, row in mh_data.iterrows():
        mh_name = str(row['origin_store_name']).strip()
        coords = str(row['Origin Lat Long']).strip()
        coords_map[mh_name] = coords
        mh_coords_map[mh_name] = coords

    def get_coords(name):
        clean_name = name.split('_part')[0].strip()
        lat_lon = coords_map.get(clean_name, "0,0")
        try:
            lat, lon = [float(x.strip()) for x in str(lat_lon).split(',')]
            return lat, lon
        except (ValueError, AttributeError):
            return 0, 0

    GRAPHHOPPER_URL = "http://127.0.0.1:8989/route"
    
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=50, pool_maxsize=50)
    session.mount('http://', adapter)

    def get_route_geometry(points, profile="truck"):
        try:
            url = GRAPHHOPPER_URL + "?"
            for lat, lon in points:
                url += f"point={lat},{lon}&"
            url += f"profile={profile}&points_encoded=false"
            resp = session.get(url, timeout=10)
            return resp.json()['paths'][0]['points']['coordinates']
        except Exception:
            return None

    # ─── Color Assignment ─────────────────────────────────────────────
    # Deterministic color per Physical_Vehicle_ID using hash
    COLOR_PALETTE = [
        '#FF5733', '#33FF57', '#3357FF', '#F333FF', '#33FFF3',
        '#FFB833', '#FF338A', '#8AFF33', '#338AFF', '#FF6F61',
        '#6B5B95', '#88B04B', '#F7CAC9', '#92A8D1', '#955251',
        '#B565A7', '#009B77', '#DD4124', '#D65076', '#45B8AC',
        '#EFC050', '#5B5EA6', '#9B2335', '#DFCFBE', '#BC243C',
    ]

    vehicle_color_map = {}

    def get_vehicle_color(phys_id):
        if phys_id not in vehicle_color_map:
            h = int(hashlib.md5(str(phys_id).encode()).hexdigest(), 16)
            vehicle_color_map[phys_id] = COLOR_PALETTE[
                h % len(COLOR_PALETTE)
            ]
        return vehicle_color_map[phys_id]

    # ─── Build Map ────────────────────────────────────────────────────
    print("Generating optimized routes map with trip time info...")

    center_loc = [13.14193, 77.86832]
    if mh_coords_map:
        first_mh_coords = list(mh_coords_map.values())[0]
        try:
            center_loc = [
                float(x.strip()) for x in first_mh_coords.split(',')
            ]
        except (ValueError, AttributeError):
            pass

    m = folium.Map(
        location=center_loc, zoom_start=11, tiles="cartodbpositron"
    )

    # ── MH Depot Markers (always visible) ──
    mhs_in_routes = (
        routes['MH_Name'].unique() if 'MH_Name' in routes.columns else []
    )
    for mh in mhs_in_routes:
        lat, lon = get_coords(mh)
        if lat != 0 and lon != 0:
            folium.Marker(
                location=[lat, lon],
                popup=f"DEPOT: {mh}",
                icon=folium.Icon(color="red", icon="home"),
                z_index_offset=1000,
            ).add_to(m)

    # ── Feature Groups per MH ──
    mh_feature_groups = {}
    for mh in mhs_in_routes:
        mh_feature_groups[mh] = folium.FeatureGroup(
            name=f"Routes: {mh}", show=True
        )
        mh_feature_groups[mh].add_to(m)

    # ─── Pre-fetch Geometries ─────────────────────────────────────────
    from concurrent.futures import ThreadPoolExecutor
    print(f"Pre-fetching route geometries for {len(routes)} routes...")
    route_geometries = {}

    def fetch_for_index(idx, pts):
        return idx, get_route_geometry(pts)

    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = []
        for idx, row in routes.iterrows():
            route_str = str(row.get('Route', ''))
            if route_str.strip() in ('', '—'):
                continue
            stops = [s.strip() for s in route_str.split('->')]
            if len(stops) < 2:
                continue
            pts = []
            for s in stops:
                lat, lon = get_coords(s)
                pts.append((lat, lon))
            futures.append(executor.submit(fetch_for_index, idx, pts))
            
        for future in futures:
            idx, geom = future.result()
            route_geometries[idx] = geom

    # ─── Plot Routes ──────────────────────────────────────────────────
    for idx, row in routes.iterrows():
        route_str = str(row.get('Route', ''))
        if route_str.strip() in ('', '—'):
            continue

        stops = [s.strip() for s in route_str.split('->')]
        if len(stops) < 2:
            continue

        mh_name = row.get('MH_Name', '')
        if mh_name not in mh_feature_groups:
            mh_feature_groups[mh_name] = folium.FeatureGroup(
                name=f"Routes: {mh_name}", show=True
            )
            mh_feature_groups[mh_name].add_to(m)

        target_layer = mh_feature_groups[mh_name]

        # Read new columns (with fallbacks for backward compatibility)
        phys_id = row.get('Physical_Vehicle_ID', f"{mh_name}_V???")
        contract = row.get('Contract_Type', '24H')
        trip_num = int(row.get('Trip_Number', 1))
        truck_type = row.get('Assigned_Truck', '?')
        capacity = row.get('Truck_Capacity', 0)
        total_load = row.get('Total_Load', 0)
        weight_util = row.get('Weight_Utilization_%', 0)
        distance = row.get('Total_Distance_km', 0)
        trip_time = row.get('Total_Trip_Time_min', 0)
        time_budget = row.get('Time_Budget_min', 600)
        time_util = row.get('Time_Utilization_%', 0)
        mh_overhead = row.get('MH_Overhead_min', 0)
        loading = row.get('Loading_Time_min', 0)
        travel = row.get('Travel_Time_min', 0)
        dh_overhead = row.get('DH_Overhead_min', 0)
        unloading = row.get('Unloading_Time_min', 0)

        color = get_vehicle_color(phys_id)
        is_trip2 = (trip_num == 2)

        # Build coordinate list and add DH pins
        points = []
        for s in stops:
            lat, lon = get_coords(s)
            points.append((lat, lon))

            if s != mh_name:
                clean_s = s.split('_part')[0]
                pin_color = "blue" if not is_trip2 else "purple"
                folium.Marker(
                    location=[lat, lon],
                    tooltip=(
                        f"<b>Store:</b> {clean_s}<br>"
                        f"<b>MH:</b> {mh_name}<br>"
                        f"<b>Vehicle:</b> {phys_id} (Trip {trip_num})"
                    ),
                    icon=folium.Icon(
                        color=pin_color, icon="map-pin", prefix='fa'
                    ),
                ).add_to(target_layer)

        # ── Time utilization bar colors ──
        if time_util <= 50:
            bar_color = "#00c853"   # Green
        elif time_util <= 80:
            bar_color = "#ffb300"   # Yellow/amber
        elif time_util <= 95:
            bar_color = "#ff6d00"   # Orange
        else:
            bar_color = "#d50000"   # Red

        if weight_util <= 50:
            wbar_color = "#00c853"
        elif weight_util <= 80:
            wbar_color = "#ffb300"
        elif weight_util <= 95:
            wbar_color = "#ff6d00"
        else:
            wbar_color = "#d50000"

        trip_label = f"Trip {trip_num}"
        if contract == '24H':
            trip_label += " of 2"

        # Build rich tooltip HTML
        tooltip_html = f"""
        <div style="font-family: 'Segoe UI', sans-serif; font-size: 13px;
                    min-width: 280px; padding: 4px;">
            <div style="font-size: 16px; font-weight: 700;
                        color: {color}; margin-bottom: 6px;">
                🚛 {truck_type}
            </div>
            <div style="font-size: 11px; color: #888; margin-bottom: 8px;">
                {phys_id} &nbsp;·&nbsp; {contract} Contract
                &nbsp;·&nbsp; {trip_label}
            </div>
            <hr style="margin: 4px 0; border-color: #ddd;">

            <b>Capacity</b>
            <span style="float:right; font-size: 12px;">
                {total_load} / {capacity} kg ({weight_util}%)
            </span>
            <div style="background: #e0e0e0; border-radius: 4px;
                        height: 10px; margin: 4px 0 8px 0;">
                <div style="background: {wbar_color}; border-radius: 4px;
                            height: 10px;
                            width: {min(weight_util, 100)}%;"></div>
            </div>

            <b>Trip Time</b>
            <span style="float:right; font-size: 12px;">
                {trip_time} / {time_budget} min ({time_util}%)
            </span>
            <div style="background: #e0e0e0; border-radius: 4px;
                        height: 10px; margin: 4px 0 8px 0;">
                <div style="background: {bar_color}; border-radius: 4px;
                            height: 10px;
                            width: {min(time_util, 100)}%;"></div>
            </div>

            <hr style="margin: 6px 0; border-color: #ddd;">
            <div style="font-size: 11px; color: #666;">
                <b>Time Breakdown:</b><br>
                MH overhead + loading: {mh_overhead + loading} min<br>
                Travel: {travel} min<br>
                DH overhead + unloading: {dh_overhead + unloading} min<br>
                Distance: {distance} km
            </div>
            <hr style="margin: 6px 0; border-color: #ddd;">
            <div style="font-size: 11px;">
                <b>Route:</b><br>
                {route_str.replace(' -> ', '<br>&darr;<br>')}
            </div>
        </div>
        """

        # ── Draw route on map ──
        geom = route_geometries.get(idx)
        if geom:
            # Line weight proportional to load
            base_weight = 3
            if total_load > 5000:
                base_weight = 5
            elif total_load > 2000:
                base_weight = 4

            dash_array = '10 6' if is_trip2 else None

            def make_style_fn(c, w, da):
                def style_fn(x):
                    result = {
                        'color': c,
                        'weight': w,
                        'opacity': 0.4,
                    }
                    if da:
                        result['dashArray'] = da
                    return result
                return style_fn

            geo = folium.GeoJson(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "LineString",
                        "coordinates": geom,
                    },
                    "properties": {},
                },
                style_function=make_style_fn(color, base_weight, dash_array),
                highlight_function=lambda x: {
                    'weight': 8,
                    'opacity': 1.0,
                },
            )
            folium.Tooltip(tooltip_html).add_to(geo)
            geo.add_to(target_layer)

    # ── Layer Control ──
    folium.LayerControl(position='topright').add_to(m)

    m.save('../data/visuals/optimized_routes_map.html')
    print("Map saved to optimized_routes_map.html with trip time info!")


if __name__ == "__main__":
    create_map()
