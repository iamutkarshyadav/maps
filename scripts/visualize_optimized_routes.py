import pandas as pd
import folium
import requests
import random

def create_map():
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

    coords_map = {}
    for _, row in data.iterrows():
        coords_map[str(row['destination_store_name']).strip()] = row['DH Lat Long']
        
    # Dynamically extract MH coords from data.tsv
    mh_coords_map = {}
    mh_data = data[['origin_store_name', 'Origin Lat Long']].drop_duplicates().dropna()
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
        except:
            return 0, 0

    GRAPHHOPPER_URL = "http://127.0.0.1:8989/route"

    def get_route_geometry(points, profile="truck"):
        try:
            url = GRAPHHOPPER_URL + "?"
            for lat, lon in points:
                url += f"point={lat},{lon}&"
            url += f"profile={profile}&points_encoded=false"
            resp = requests.get(url)
            return resp.json()['paths'][0]['points']['coordinates']
        except:
            return None

    print("Generating optimized routes map... (Getting actual road geometries)")
    
    # Get center by taking the first MH's coordinates, or default to Bangalore
    center_loc = [13.14193, 77.86832]
    if mh_coords_map:
        first_mh_coords = list(mh_coords_map.values())[0]
        try:
            center_loc = [float(x.strip()) for x in first_mh_coords.split(',')]
        except:
            pass
            
    m = folium.Map(location=center_loc, zoom_start=11, tiles="cartodbpositron")
    
    # Add depot pins to base map (always visible) for all unique MHs in the routes
    mhs_in_routes = routes['MH_Name'].unique() if 'MH_Name' in routes.columns else []
    
    colors = ['#FF5733', '#33FF57', '#3357FF', '#F333FF', '#33FFF3', '#FFB833', '#FF338A', '#8AFF33', '#338AFF']
    
    mh_feature_groups = {}
    for mh in mhs_in_routes:
        mh_feature_groups[mh] = folium.FeatureGroup(name=f"Routes: {mh}", show=True)
        mh_feature_groups[mh].add_to(m)
        
    for mh in mhs_in_routes:
        lat, lon = get_coords(mh)
        if lat != 0 and lon != 0:
            folium.Marker(
                location=[lat, lon],
                popup=f"DEPOT: {mh}",
                icon=folium.Icon(color="red", icon="home"),
                z_index_offset=1000
            ).add_to(m)
    
    for i, row in routes.iterrows():
        route_str = str(row['Route'])
        if route_str.strip() == "": continue
        
        stops = [s.strip() for s in route_str.split('->')]
        if len(stops) < 2: continue
        
        mh_name = row.get('MH_Name', 'BLR-DRY-MH-SUMADHURA')
        if mh_name not in mh_feature_groups:
            mh_feature_groups[mh_name] = folium.FeatureGroup(name=f"Routes: {mh_name}", show=True)
            mh_feature_groups[mh_name].add_to(m)
            
        target_layer = mh_feature_groups[mh_name]
        
        points = []
        for s in stops:
            lat, lon = get_coords(s)
            points.append((lat, lon))
            
            # Add pin markers for DHs to specific layer
            if s != mh_name:
                clean_s = s.split('_part')[0]
                folium.Marker(
                    location=[lat, lon],
                    tooltip=f"<b>Store:</b> {clean_s}<br><b>MH:</b> {mh_name}",
                    icon=folium.Icon(color="blue", icon="map-pin", prefix='fa')
                ).add_to(target_layer)
                
        geom = get_route_geometry(points)
        if geom:
            color = random.choice(colors)
            tooltip_html = f"""
            <div style="font-family: sans-serif; font-size: 14px;">
                <b style="color:{color}; font-size: 16px;">{row['Assigned_Truck']}</b><br>
                <b>MH:</b> {mh_name}<br>
                <b>Capacity:</b> {row['Truck_Capacity']} kg<br>
                <b>Load Sent:</b> {row['Total_Load']} kg<br>
                <b>Utilization:</b> {row['Utilization_%']}%<br>
                <b>Distance:</b> {row['Total_Distance_km']} km<br>
                <hr style="margin: 4px 0;">
                <b>Route Sequence:</b><br>
                {route_str.replace('->', '<br>&darr;<br>')}
            </div>
            """
            
            feature = {
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": geom
                },
                "properties": {}
            }
            
            geo = folium.GeoJson(
                feature,
                style_function=lambda x, c=color: {
                    'color': c,
                    'weight': 4,
                    'opacity': 0.3
                },
                highlight_function=lambda x: {
                    'weight': 8,
                    'opacity': 1.0
                }
            )
            folium.Tooltip(tooltip_html).add_to(geo)
            geo.add_to(target_layer)
    
    # Add LayerControl to allow toggling of Direct vs Milk Runs
    folium.LayerControl(position='topright').add_to(m)
            
    m.save('../data/visuals/optimized_routes_map.html')
    print("Map saved to optimized_routes_map.html with LayerControl filters!")

if __name__ == "__main__":
    create_map()
