import pandas as pd
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
sop = pd.read_excel('details.xlsx', sheet_name='S&OP')
for i, row in sop.iterrows():
    dem = int(row['w1']) if pd.notnull(row['w1']) else 0
    res = str(row['Vehicle restrictions']).strip()
    cap = TRUCK_CAPACITIES.get(res, 16800)
    if dem > cap:
        print(f"Row {i} DH {row['DH']} has demand {dem} but restriction {res} (cap {cap})")
