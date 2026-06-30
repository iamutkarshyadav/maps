import requests
import folium

# ==========================================
# CONFIGURATION
# ==========================================
# Your local, offline GraphHopper routing endpoint
GRAPHHOPPER_URL = "http://localhost:8989/route"

# Sample Hubs (Latitude, Longitude)
# Start: MH Sumadhura (HSR Layout)
START_COORD = (12.9116, 77.6394) 
# End: DH Mahadevapura
END_COORD = (12.9922, 77.6945)

def get_route(start, end):
    """
    Pings the local GraphHopper server to calculate distance and time.
    """
    # GraphHopper expects the query parameters formatted in a specific way
    params = {
        "point": [
            f"{start[0]},{start[1]}",
            f"{end[0]},{end[1]}"
        ],
        "profile": "truck",  # The routing profile we built the graph for
        "locale": "en",
        "instructions": False, # Set to True if you want turn-by-turn text
        "points_encoded": False
    }

    try:
        response = requests.get(GRAPHHOPPER_URL, params=params)
        
        # Check if the local server responded successfully
        if response.status_code == 200:
            data = response.json()
            path = data['paths'][0]
            
            # GraphHopper returns distance in meters and time in milliseconds
            distance_km = path['distance'] / 1000.0
            time_mins = path['time'] / (1000 * 60)
            
            # The coordinates are returned as [longitude, latitude]
            coordinates = path['points']['coordinates']
            
            # Folium expects [latitude, longitude]
            route_coords = [[coord[1], coord[0]] for coord in coordinates]
            
            return distance_km, time_mins, route_coords
        else:
            print(f"Server Error: {response.status_code} - {response.text}")
            return None, None, None

    except requests.exceptions.ConnectionError:
        print("Error: Could not connect to GraphHopper.")
        print("Ensure the Java server is running and says 'ServerStarted' in the other terminal.")
        return None, None, None

def main():
    print(f"Connecting to local GraphHopper engine...")
    print(f"Calculating route from {START_COORD} to {END_COORD}...\n")
    
    dist_km, time_mins, route_coords = get_route(START_COORD, END_COORD)
    
    if dist_km is not None:
        print("--- ROUTE CALCULATED SUCCESSFULLY ---")
        print(f"True Road Distance: {dist_km:.2f} km")
        print(f"Estimated Travel Time: {time_mins:.2f} minutes")
        
        # Create a map centered at the start coordinate
        m = folium.Map(location=START_COORD, zoom_start=13)
        
        # Add markers for the start and end points
        folium.Marker(START_COORD, tooltip="Start", icon=folium.Icon(color='green')).add_to(m)
        folium.Marker(END_COORD, tooltip="End", icon=folium.Icon(color='red')).add_to(m)
        
        # Add the route line to the map
        folium.PolyLine(route_coords, weight=5, color='blue', opacity=0.8).add_to(m)
        
        # Save the map to an HTML file
        map_filename = "route_map.html"
        m.save(map_filename)
        print(f"\nMap saved to {map_filename}. Open this file in your browser to view the route!")

if __name__ == "__main__":
    main()