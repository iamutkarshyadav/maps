import pandas as pd
import requests

# Matching the Google Maps buffers from our batch_routing.py
DISTANCE_MULTIPLIER = 1.05
TIME_MULTIPLIER = 1.25

GRAPHHOPPER_URL = "http://127.0.0.1:8989/route"

def build_matrix(input_file="data.tsv", output_file="dh_distance_matrix.csv"):
    print("Reading data...")
    try:
        df = pd.read_csv(input_file, sep='\t')
        if 'DH Lat Long' not in df.columns:
            df = pd.read_csv(input_file, sep=',')
    except Exception as e:
        print("Error reading input file:", e)
        return
        
    # Extract all unique Destination Hubs
    dhs = df[['destination_store_name', 'destination_loccode', 'DH Lat Long']].drop_duplicates().dropna()
    dh_list = dhs.to_dict('records')
    n = len(dh_list)
    print(f"Found {n} unique Destination Hubs.")
    print(f"Calculating {n}x{n} = {n*n} routes. This will take about 1-2 minutes...")
    
    # Use requests.Session() to keep the connection open and make requests incredibly fast
    session = requests.Session()
    results = []
    
    count = 0
    total = n * n
    
    for origin in dh_list:
        o_name = origin['destination_store_name']
        o_code = origin['destination_loccode']
        o_lat_lon = str(origin['DH Lat Long'])
        try:
            o_lat, o_lon = [x.strip() for x in o_lat_lon.split(',')]
        except:
            total -= n
            continue
            
        for dest in dh_list:
            d_name = dest['destination_store_name']
            d_code = dest['destination_loccode']
            d_lat_lon = str(dest['DH Lat Long'])
            try:
                d_lat, d_lon = [x.strip() for x in d_lat_lon.split(',')]
            except:
                count += 1
                continue
                
            count += 1
            if count % 2500 == 0:
                print(f"Processed {count}/{total} routes...")
                
            if o_name == d_name:
                results.append({
                    "Origin_DH": o_name,
                    "Origin_Code": o_code,
                    "Dest_DH": d_name,
                    "Dest_Code": d_code,
                    "Distance_km": 0.0,
                    "Time_mins": 0.0
                })
                continue
                
            params = {
                "point": [f"{o_lat},{o_lon}", f"{d_lat},{d_lon}"],
                "profile": "truck",
                "locale": "en",
                "instructions": False,
                "points_encoded": False
            }
            
            try:
                resp = session.get(GRAPHHOPPER_URL, params=params)
                if resp.status_code == 200:
                    path = resp.json()['paths'][0]
                    dist = (path['distance'] / 1000.0) * DISTANCE_MULTIPLIER
                    t = (path['time'] / (1000 * 60)) * TIME_MULTIPLIER
                else:
                    dist, t = None, None
            except:
                dist, t = None, None
                
            results.append({
                "Origin_DH": o_name,
                "Origin_Code": o_code,
                "Dest_DH": d_name,
                "Dest_Code": d_code,
                "Distance_km": dist,
                "Time_mins": t
            })
            
    out_df = pd.DataFrame(results)
    out_df.to_csv(output_file, index=False)
    print(f"Matrix successfully saved to {output_file}!")

if __name__ == "__main__":
    build_matrix()
