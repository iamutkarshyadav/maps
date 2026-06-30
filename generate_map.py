import pandas as pd
import folium
import requests

GRAPHHOPPER_URL = "http://127.0.0.1:8989/route"

def get_route_geometry(origin_str, dest_str, profile="truck"):
    try:
        start_lat, start_lon = [float(x.strip()) for x in origin_str.split(',')]
        end_lat, end_lon = [float(x.strip()) for x in dest_str.split(',')]
    except Exception:
        return None, None, None
        
    params = {
        "point": [f"{start_lat},{start_lon}", f"{end_lat},{end_lon}"],
        "profile": profile,
        "locale": "en",
        "instructions": False,
        "points_encoded": False
    }

    try:
        response = requests.get(GRAPHHOPPER_URL, params=params)
        if response.status_code == 200:
            data = response.json()
            # points.coordinates is a list of [lon, lat]
            coords = data['paths'][0]['points']['coordinates']
            return coords, [start_lat, start_lon], [end_lat, end_lon]
    except Exception:
        pass
    return None, None, None

def create_map(input_file="routed_data_filtered.csv", output_file="od_map.html"):
    print("Reading data...")
    try:
        df = pd.read_csv(input_file)
    except:
        print(f"Could not read {input_file}")
        return
        
    try:
        first_origin = [float(x.strip()) for x in df['Origin Lat Long'].iloc[0].split(',')]
        m = folium.Map(location=first_origin, zoom_start=11)
    except:
        m = folium.Map(location=[13.13, 77.40], zoom_start=11)
        
    print(f"Fetching geometry and building GeoJSON for {len(df)} routes...")
    
    features = []
    
    for idx, row in df.iterrows():
        try:
            o_lat, o_lon = [float(x.strip()) for x in str(row['Origin Lat Long']).split(',')]
            d_lat, d_lon = [float(x.strip()) for x in str(row['DH Lat Long']).split(',')]
            
            coords, _, _ = get_route_geometry(str(row['Origin Lat Long']), str(row['DH Lat Long']))
            
            if not coords:
                coords = [[o_lon, o_lat], [d_lon, d_lat]] # fallback to straight line
                
            features.append({
                "type": "Feature",
                "geometry": {
                    "type": "LineString",
                    "coordinates": coords
                },
                "properties": {
                    "Origin": str(row['origin_store_name']),
                    "Destination": str(row['destination_store_name']),
                    "Distance": f"{row['GH_Distance_km']:.1f} km",
                    "Time": f"{row['GH_Travel_Time_mins']:.1f} mins"
                }
            })
            
        except Exception as e:
            continue
            
        if (idx + 1) % 50 == 0:
            print(f"Processed {idx + 1} / {len(df)} routes...")
            
    geojson_data = {
        "type": "FeatureCollection",
        "features": features
    }
    
    # 1. Very faint base style so it looks clean (others out of focus)
    style_function = lambda x: {
        'color': '#3388ff',
        'weight': 3,
        'opacity': 0.15
    }
    
    # 2. Bright red and fully opaque when hovered! (focus)
    highlight_function = lambda x: {
        'color': '#ff0000',
        'weight': 6,
        'opacity': 1.0
    }
    
    # 3. Dynamic Tooltip Card
    tooltip = folium.GeoJsonTooltip(
        fields=['Origin', 'Destination', 'Distance', 'Time'],
        aliases=['From:', 'To:', 'Dist:', 'Est. Time:'],
        localize=True,
        sticky=True,
        labels=True,
        style="""
            background-color: #ffffff;
            border: 2px solid #333333;
            border-radius: 8px;
            box-shadow: 0px 4px 6px rgba(0,0,0,0.3);
            font-size: 14px;
            font-family: Arial, sans-serif;
            padding: 10px;
        """,
        max_width=400,
    )
    
    # Add GeoJSON to map
    folium.GeoJson(
        geojson_data,
        name="Dynamic Routes",
        style_function=style_function,
        highlight_function=highlight_function,
        tooltip=tooltip
    ).add_to(m)
    
    # Note: Circles were removed to prevent blocking the hover interaction on dense networks.
    
    folium.LayerControl().add_to(m)
    
    m.save(output_file)
    print(f"Dynamic Map successfully saved to {output_file}!")

if __name__ == "__main__":
    create_map()
