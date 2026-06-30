import pandas as pd

milk = pd.read_csv('milk_run_pairs.csv')
routes = pd.read_csv('optimized_routes.csv')
sop = pd.read_excel('details.xlsx', sheet_name='S&OP')

def get_original_dh(name):
    if "_part" in name:
        return name.split("_part")[0]
    return name

milk['Original_DH'] = milk['DH_Name'].apply(get_original_dh)

sop_demands = sop[['DH', 'w1']].dropna().rename(columns={'DH': 'Original_DH', 'w1': 'Respective_Demand'})
milk = milk.merge(sop_demands, on='Original_DH', how='left')

vid_col = routes.columns[0]
routes_info = routes[[vid_col, 'Total_Load']].rename(columns={vid_col: 'Vehicle_ID', 'Total_Load': 'Total_Volume_Sent'})
milk = milk.merge(routes_info, on='Vehicle_ID', how='left')

milk = milk.drop(columns=['Original_DH'])
milk.to_csv('milk_run_pairs.csv', index=False)
print("Enriched successfully!")
