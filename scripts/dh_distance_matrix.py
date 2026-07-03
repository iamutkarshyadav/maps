import pandas as pd
import requests
import hashlib
import json
import os

# Matching the Google Maps buffers from our batch_routing.py
DISTANCE_MULTIPLIER = 1.05
TIME_MULTIPLIER = 1.25

GRAPHHOPPER_URL = "http://127.0.0.1:8989/route"

# Cache paths
CACHE_DIR = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'cache'
))
CACHE_META_FILE = os.path.join(CACHE_DIR, 'matrix_cache_meta.json')
CACHE_CSV_FILE = os.path.join(CACHE_DIR, 'cached_distance_matrix.csv')


def _compute_pairs_hash(all_pairs):
    """
    Compute a deterministic hash of all (origin, dest) location pairs.
    If the set of pairs hasn't changed, the cache is valid.
    """
    sorted_pairs = sorted(set(all_pairs))
    raw = json.dumps(sorted_pairs, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def _load_cache(pairs_hash):
    """
    Check if we have a valid cached distance matrix.
    Returns (cached_df, cached_pairs_set) if valid, else (None, set()).
    """
    if not os.path.exists(CACHE_META_FILE) or not os.path.exists(CACHE_CSV_FILE):
        return None, set()

    try:
        with open(CACHE_META_FILE, 'r') as f:
            meta = json.load(f)
    except (json.JSONDecodeError, IOError):
        return None, set()

    if meta.get('pairs_hash') == pairs_hash:
        # Full cache hit — exact same set of pairs
        try:
            df = pd.read_csv(CACHE_CSV_FILE)
            cached_pairs = set(
                zip(df['Origin_DH'].astype(str), df['Dest_DH'].astype(str))
            )
            print(f"  Full cache hit! {len(df)} rows loaded from cache.")
            return df, cached_pairs
        except Exception:
            return None, set()

    # Partial cache — load what we have so individual pairs can be reused
    try:
        df = pd.read_csv(CACHE_CSV_FILE)
        cached_pairs = set(
            zip(df['Origin_DH'].astype(str), df['Dest_DH'].astype(str))
        )
        print(f"  Partial cache found. {len(cached_pairs)} pairs available.")
        return df, cached_pairs
    except Exception:
        return None, set()


def _save_cache(df, pairs_hash):
    """Save the distance matrix and its hash to disk."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    df.to_csv(CACHE_CSV_FILE, index=False)
    meta = {'pairs_hash': pairs_hash}
    with open(CACHE_META_FILE, 'w') as f:
        json.dump(meta, f)
    print(f"  Cache saved ({len(df)} rows).")


def build_matrix(data_file="../data/inputs/data.tsv",
                 sop_file="../data/inputs/details.xlsx",
                 output_file="../data/outputs/dh_distance_matrix.csv"):
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
        if dh in dh_dict and dh not in [x['name'] for x in mh_to_dhs[mh]]:
            mh_to_dhs[mh].append({
                'name': dh,
                'code': dh_dict[dh]['code'],
                'lat_lon': dh_dict[dh]['lat_lon']
            })

    print(f"Found {len(mh_to_dhs)} unique Material Hubs.")

    # ─── Build Required Pairs & Check Cache ───────────────────────────
    all_required_pairs = []
    mh_nodes_map = {}  # mh -> list of node dicts

    for mh, dhs in mh_to_dhs.items():
        if mh not in mh_dict:
            continue

        mh_lat_lon = mh_dict[mh]
        try:
            mh_lat, mh_lon = [x.strip() for x in mh_lat_lon.split(',')]
        except Exception:
            continue

        nodes = [{'name': mh, 'code': 'MH_CODE', 'lat': mh_lat, 'lon': mh_lon}]
        for dh in dhs:
            try:
                parts = [x.strip() for x in dh['lat_lon'].split(',')]
                nodes.append({
                    'name': dh['name'], 'code': dh['code'],
                    'lat': parts[0], 'lon': parts[1]
                })
            except Exception:
                pass

        mh_nodes_map[mh] = nodes
        for origin in nodes:
            for dest in nodes:
                all_required_pairs.append((origin['name'], dest['name']))

    pairs_hash = _compute_pairs_hash(all_required_pairs)
    cached_df, cached_pairs = _load_cache(pairs_hash)

    # If full cache hit (hash matches), just copy to output and return
    if cached_df is not None and len(cached_pairs) >= len(set(all_required_pairs)):
        required_set = set(all_required_pairs)
        if required_set.issubset(cached_pairs):
            # Filter cached_df to only required pairs
            cached_df_filtered = cached_df[
                cached_df.apply(
                    lambda r: (str(r['Origin_DH']), str(r['Dest_DH'])) in required_set,
                    axis=1
                )
            ]
            cached_df_filtered.to_csv(output_file, index=False)
            print(f"Full cache hit! Copied {len(cached_df_filtered)} rows to {output_file}")
            return

    # ─── Fetch Missing Pairs ──────────────────────────────────────────
    session = requests.Session()
    adapter = requests.adapters.HTTPAdapter(pool_connections=200, pool_maxsize=200)
    session.mount('http://', adapter)

    # Build a lookup from cached data for quick pair checking
    cached_lookup = {}
    if cached_df is not None:
        for _, row in cached_df.iterrows():
            key = (str(row['Origin_DH']), str(row['Dest_DH']))
            cached_lookup[key] = row.to_dict()

    results = []
    tasks_to_fetch = []

    for mh, nodes in mh_nodes_map.items():
        print(f"Processing MH: {mh} with {len(nodes) - 1} DHs...")
        for origin in nodes:
            for dest in nodes:
                pair_key = (origin['name'], dest['name'])

                # Check cache first
                if pair_key in cached_lookup:
                    results.append(cached_lookup[pair_key])
                    continue

                # Self-pair
                if origin['name'] == dest['name']:
                    result = {
                        "Origin_DH": origin['name'],
                        "Origin_Code": origin['code'],
                        "Dest_DH": dest['name'],
                        "Dest_Code": dest['code'],
                        "Distance_km": 0.0,
                        "Time_mins": 0.0
                    }
                    results.append(result)
                    cached_lookup[pair_key] = result
                    continue

                tasks_to_fetch.append((
                    origin['name'], origin['code'], origin['lat'], origin['lon'],
                    dest['name'], dest['code'], dest['lat'], dest['lon']
                ))

    if tasks_to_fetch:
        print(f"  Need to fetch {len(tasks_to_fetch)} new routes "
              f"({len(results)} from cache)...")

        def fetch_route(o_name, o_code, o_lat, o_lon,
                        d_name, d_code, d_lat, d_lon):
            import time as _time
            params = {
                "point": [f"{o_lat},{o_lon}", f"{d_lat},{d_lon}"],
                "profile": "truck",
                "locale": "en",
                "instructions": False,
                "points_encoded": False
            }
            max_retries = 3
            dist, t = -1.0, -1.0
            for attempt in range(max_retries):
                try:
                    resp = session.get(GRAPHHOPPER_URL, params=params, timeout=5)
                    if resp.status_code == 200:
                        path = resp.json()['paths'][0]
                        dist = (path['distance'] / 1000.0) * DISTANCE_MULTIPLIER
                        t = (path['time'] / (1000 * 60)) * TIME_MULTIPLIER
                        break
                except Exception:
                    _time.sleep(1)

            return {
                "Origin_DH": o_name,
                "Origin_Code": o_code,
                "Dest_DH": d_name,
                "Dest_Code": d_code,
                "Distance_km": dist,
                "Time_mins": t
            }

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=200) as executor:
            futures = {executor.submit(fetch_route, *t): t for t in tasks_to_fetch}
            count = 0
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                count += 1
                if count % 500 == 0:
                    print(f"  Fetched {count}/{len(tasks_to_fetch)} new routes...")

        print(f"  Fetched all {len(tasks_to_fetch)} new routes.")
    else:
        print("  All routes served from cache!")

    # Save to output and update cache
    out_df = pd.DataFrame(results)
    out_df.to_csv(output_file, index=False)
    _save_cache(out_df, pairs_hash)
    print(f"Matrix saved to {output_file} ({len(out_df)} rows)")


if __name__ == "__main__":
    build_matrix()
