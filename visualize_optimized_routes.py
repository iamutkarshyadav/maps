import pandas as pd
import folium
import requests
import random

def create_map():
    print("Loading data...")
    routes = pd.read_csv('optimized_routes.csv')
    data = pd.read_csv('data.tsv', sep='\t')
    if 'DH Lat Long' not in data.columns:
        data = pd.read_csv('data.tsv', sep=',')

    coords_map = {}
    for _, row in data.iterrows():
        coords_map[str(row['destination_store_name']).strip()] = row['DH Lat Long']
        
    coords_map['BLR-DRY-MH-SUMADHURA'] = "13.14193,77.86832"

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
    m = folium.Map(location=[13.14193, 77.86832], zoom_start=11, tiles="cartodbpositron")
    
    fg_direct = folium.FeatureGroup(name="Direct Trips", show=True)
    fg_milk = folium.FeatureGroup(name="Milk Runs", show=True)
    
    colors = ['#FF5733', '#33FF57', '#3357FF', '#F333FF', '#33FFF3', '#FFB833', '#FF338A', '#8AFF33', '#338AFF']
    
    # Add depot pin to base map (always visible)
    folium.Marker(
        location=[13.14193, 77.86832],
        popup="DEPOT: BLR-DRY-MH-SUMADHURA",
        icon=folium.Icon(color="red", icon="home"),
        z_index_offset=1000
    ).add_to(m)
    
    for i, row in routes.iterrows():
        route_str = str(row['Route'])
        if route_str.strip() == "": continue
        
        stops = [s.strip() for s in route_str.split('->')]
        if len(stops) < 2: continue
        
        dhs = [s for s in stops if s != 'BLR-DRY-MH-SUMADHURA' and s != '']
        target_layer = fg_milk if len(dhs) > 1 else fg_direct
        
        points = []
        for s in stops:
            lat, lon = get_coords(s)
            points.append((lat, lon))
            
            # Add pin markers for DHs to specific layer
            if s != 'BLR-DRY-MH-SUMADHURA':
                clean_s = s.split('_part')[0]
                folium.Marker(
                    location=[lat, lon],
                    tooltip=f"<b>Store:</b> {clean_s}",
                    icon=folium.Icon(color="blue", icon="map-pin", prefix='fa')
                ).add_to(target_layer)
                
        geom = get_route_geometry(points)
        if geom:
            color = random.choice(colors)
            tooltip_html = f"""
            <div style="font-family: sans-serif; font-size: 14px;">
                <b style="color:{color}; font-size: 16px;">{row['Assigned_Truck']}</b><br>
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
            
    fg_direct.add_to(m)
    fg_milk.add_to(m)
    
    # Add LayerControl to allow toggling of Direct vs Milk Runs
    folium.LayerControl(position='topright').add_to(m)
            
    m.save('optimized_routes_map.html')
    print("Map saved to optimized_routes_map.html with LayerControl filters!")

if __name__ == "__main__":
    create_map()
