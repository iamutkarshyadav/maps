import pandas as pd
import requests

# Google Maps usually reports slightly longer distances and much longer times due to traffic.
# Adjust these multipliers to match Google Maps more closely! (1.05 = +5% buffer)
DISTANCE_MULTIPLIER = 1.05
TIME_MULTIPLIER = 1.25

GRAPHHOPPER_URL = "http://127.0.0.1:8989/route"

def get_route(origin_str, dest_str, profile="truck"):
    # Parse "lat, lon" strings
    try:
        start_lat, start_lon = [x.strip() for x in str(origin_str).split(',')]
        end_lat, end_lon = [x.strip() for x in str(dest_str).split(',')]
    except Exception:
        return None, None
        
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
            path = data['paths'][0]
            
            # Apply our buffers
            distance_km = (path['distance'] / 1000.0) * DISTANCE_MULTIPLIER
            time_mins = (path['time'] / (1000 * 60)) * TIME_MULTIPLIER
            
            return distance_km, time_mins
        else:
            return None, None
    except requests.exceptions.ConnectionError:
        return None, None

def process_file(input_file, output_file):
    print(f"Reading {input_file}...")
    
    # Try reading as tab-separated first, fallback to comma-separated
    try:
        df = pd.read_csv(input_file, sep='\t')
        if 'Origin Lat Long' not in df.columns:
            df = pd.read_csv(input_file, sep=',')
    except Exception as e:
        print(f"Error reading file: {e}")
        return
        
    if 'Origin Lat Long' not in df.columns or 'DH Lat Long' not in df.columns:
        print("Error: Could not find 'Origin Lat Long' or 'DH Lat Long' columns in the dataset.")
        return
        
    print(f"Found {len(df)} rows. Calculating routes using local GraphHopper (this might take a moment)...")
    
    distances = []
    times = []
    
    for index, row in df.iterrows():
        origin = row['Origin Lat Long']
        dest = row['DH Lat Long']
        
        dist, travel_time = get_route(origin, dest)
        distances.append(dist)
        times.append(travel_time)
            
        if (index + 1) % 50 == 0:
            print(f"Processed {index + 1} / {len(df)} rows...")
            
    df['GH_Distance_km'] = distances
    df['GH_Travel_Time_mins'] = times
    
    df.to_csv(output_file, index=False)
    print(f"Done! Results saved to {output_file}")

if __name__ == "__main__":
    # Ensure you save your data to a file named data.tsv or data.csv in the same folder
    INPUT_FILE = "data.tsv" 
    OUTPUT_FILE = "routed_data.csv"
    process_file(INPUT_FILE, OUTPUT_FILE)
