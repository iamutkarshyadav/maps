import pandas as pd
import requests

# Matching the Google Maps buffers from our batch_routing.py
DISTANCE_MULTIPLIER = 1.05
TIME_MULTIPLIER = 1.25

GRAPHHOPPER_URL = "http://127.0.0.1:8989/route"

def build_matrix(data_file="../data/inputs/data.tsv", sop_file="../data/inputs/details.xlsx", output_file="../data/outputs/dh_distance_matrix.csv"):
    print("Reading data...")
    try:
        df = pd.read_csv(data_file, sep='\t')
        if 'DH Lat Long' not in df.columns:
            df = pd.read_csv(data_file, sep=',')
    except Exception as e:
        print("Error reading input file:", e)
        return
        
    try:
        sop = pd.read_excel(sop_file, sheet_name='S&OP')
    except Exception as e:
        print("Error reading details.xlsx:", e)
        return

    # Clean strings
    sop['MH '] = sop['MH '].astype(str).str.strip()
    sop['DH'] = sop['DH'].astype(str).str.strip()
    df['destination_store_name'] = df['destination_store_name'].astype(str).str.strip()
    df['origin_store_name'] = df['origin_store_name'].astype(str).str.strip()

    # Extract DH Lat Long mapping
    dh_coords = df[['destination_store_name', 'destination_loccode', 'DH Lat Long']].drop_duplicates().dropna()
    dh_dict = {}
    for _, row in dh_coords.iterrows():
        dh_dict[row['destination_store_name']] = {
            'code': row['destination_loccode'],
            'lat_lon': str(row['DH Lat Long'])
        }

    # Extract MH Lat Long mapping
    mh_coords = df[['origin_store_name', 'Origin Lat Long']].drop_duplicates().dropna()
    mh_dict = {}
    for _, row in mh_coords.iterrows():
        mh_dict[row['origin_store_name']] = str(row['Origin Lat Long'])

    # Fallback for missing DHs in data.tsv
    fallback_dhs = {
        'BLR-Garudachar Palya': '12.9866, 77.7121',
        'BLR-Doddanekundi': '12.9713, 77.6965',
        'BLR-Nagdevanahalli': '12.9360, 77.4981'
    }
    for fd_name, fd_coords in fallback_dhs.items():
        if fd_name not in dh_dict:
            dh_dict[fd_name] = {'code': 'FB_CODE', 'lat_lon': fd_coords}

    # Group DHs by MH based on S&OP
    mh_to_dhs = {}
    for _, row in sop.iterrows():
        mh = row['MH ']
        dh = row['DH']
        if mh not in mh_to_dhs:
            mh_to_dhs[mh] = []
        # Only add if we have coords for DH
        if dh in dh_dict and dh not in [x['name'] for x in mh_to_dhs[mh]]:
            mh_to_dhs[mh].append({
                'name': dh,
                'code': dh_dict[dh]['code'],
                'lat_lon': dh_dict[dh]['lat_lon']
            })

    print(f"Found {len(mh_to_dhs)} unique Material Hubs.")
    
    # Use requests.Session() to keep the connection open and make requests incredibly fast
    session = requests.Session()
    results = []
    
    for mh, dhs in mh_to_dhs.items():
        if mh not in mh_dict:
            print(f"Warning: MH {mh} not found in {data_file}. Skipping.")
            continue
            
        mh_lat_lon = mh_dict[mh]
        try:
            mh_lat, mh_lon = [x.strip() for x in mh_lat_lon.split(',')]
        except:
            print(f"Warning: Invalid coordinates for MH {mh}: {mh_lat_lon}. Skipping.")
            continue
            
        print(f"Processing MH: {mh} with {len(dhs)} DHs...")
        
        # Build node list for this MH: MH + all its DHs
        nodes = [{
            'name': mh,
            'code': 'MH_CODE',
            'lat': mh_lat,
            'lon': mh_lon
        }]
        
        for dh in dhs:
            try:
                parts = [x.strip() for x in dh['lat_lon'].split(',')]
                lat, lon = parts[0], parts[1]
                nodes.append({
                    'name': dh['name'],
                    'code': dh['code'],
                    'lat': lat,
                    'lon': lon
                })
            except:
                pass
                
        n_nodes = len(nodes)
        total_routes = n_nodes * n_nodes
        count = 0
        
        for origin in nodes:
            o_name = origin['name']
            o_code = origin['code']
            o_lat, o_lon = origin['lat'], origin['lon']
            
            for dest in nodes:
                d_name = dest['name']
                d_code = dest['code']
                d_lat, d_lon = dest['lat'], dest['lon']
                
                count += 1
                if count % 1000 == 0:
                    print(f"  Processed {count}/{total_routes} routes for {mh}...")
                    
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
                
                import time
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        resp = session.get(GRAPHHOPPER_URL, params=params, timeout=5)
                        if resp.status_code == 200:
                            path = resp.json()['paths'][0]
                            dist = (path['distance'] / 1000.0) * DISTANCE_MULTIPLIER
                            t = (path['time'] / (1000 * 60)) * TIME_MULTIPLIER
                            break
                        else:
                            dist, t = -1.0, -1.0
                    except:
                        dist, t = -1.0, -1.0
                        time.sleep(2)
                    
                # We save all nodes in this CSV so vrp_solver doesn't need to do live calls
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
