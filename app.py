import os
import sys
import subprocess
import json
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file

# Add scripts/ to sys.path so we can import constants
SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'scripts')
sys.path.insert(0, SCRIPTS_DIR)

app = Flask(__name__)

# Paths
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
INPUTS_DIR = os.path.join(DATA_DIR, 'inputs')
OUTPUTS_DIR = os.path.join(DATA_DIR, 'outputs')
VISUALS_DIR = os.path.join(DATA_DIR, 'visuals')
CACHE_DIR = os.path.join(DATA_DIR, 'cache')

# ─── Column Mapping (for documentation on the Settings page) ─────────
COLUMN_MAPPING = {
    'CITY': {
        'required': False,
        'description': 'City name. Used for grouping/display only.',
        'example': 'Bengaluru',
    },
    'MH ': {
        'required': True,
        'description': 'Material Hub (depot) name. Must match the origin_store_name in data.tsv.',
        'example': 'BLR-DRY-MH-SUMADHURA',
    },
    'DH': {
        'required': True,
        'description': 'Destination Hub (store) name. Must match destination_store_name in data.tsv.',
        'example': 'BLR-Sanjaynagar',
    },
    'w1': {
        'required': True,
        'description': 'Demand weight in kg for this DH. Used to calculate truck load and split deliveries.',
        'example': '1892',
    },
    'Vehicle restrictions': {
        'required': True,
        'description': 'Maximum allowed truck type for this DH. Restricts which trucks can serve this stop.',
        'example': '14FT_TRUCK',
    },
}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/settings')
def settings_page():
    # Import fresh each time to pick up latest JSON overrides
    import importlib
    import constants
    importlib.reload(constants)
    config = constants.get_current_config()
    return render_template('settings.html',
                           config=config,
                           column_mapping=COLUMN_MAPPING)


@app.route('/api/settings', methods=['GET'])
def get_settings():
    import importlib
    import constants
    importlib.reload(constants)
    return jsonify(constants.get_current_config())


@app.route('/api/settings', methods=['POST'])
def save_settings():
    """Save settings from the web UI to settings.json."""
    import importlib
    import constants

    data = request.get_json()
    if not data:
        return jsonify({'error': 'No data provided'}), 400

    try:
        # Validate and build the config dict
        cfg = {}

        # Truck capacities — expect dict of str->int
        if 'truck_capacities' in data:
            cfg['truck_capacities'] = {
                k: int(v) for k, v in data['truck_capacities'].items()
            }

        # Loading times
        if 'loading_times' in data:
            cfg['loading_times'] = {
                k: int(v) for k, v in data['loading_times'].items()
            }

        # Unloading times
        if 'unloading_times' in data:
            cfg['unloading_times'] = {
                k: int(v) for k, v in data['unloading_times'].items()
            }

        # Dock overheads
        if 'mh_dock_overhead' in data:
            cfg['mh_dock_overhead'] = int(data['mh_dock_overhead'])
        if 'dh_dock_overhead' in data:
            cfg['dh_dock_overhead'] = int(data['dh_dock_overhead'])

        # Contract config
        if 'contract_config' in data:
            cc = {}
            for key, val in data['contract_config'].items():
                cc[key] = {
                    'total_hours': int(val['total_hours']),
                    'effective_hours': int(val['effective_hours']),
                    'effective_minutes': int(val['effective_minutes']),
                    'num_trips': int(val['num_trips']),
                    'minutes_per_trip': int(val['minutes_per_trip']),
                }
            cfg['contract_config'] = cc

        # Flags
        if 'mixed_contract_mode' in data:
            cfg['mixed_contract_mode'] = bool(data['mixed_contract_mode'])
        if 'solver_time_limit' in data:
            cfg['solver_time_limit'] = int(data['solver_time_limit'])

        constants.save_config(cfg)
        importlib.reload(constants)

        return jsonify({'success': True, 'message': 'Settings saved!'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/clear_cache', methods=['POST'])
def clear_cache():
    """Delete the distance matrix cache."""
    import shutil
    try:
        if os.path.exists(CACHE_DIR):
            shutil.rmtree(CACHE_DIR)
            os.makedirs(CACHE_DIR, exist_ok=True)
        return jsonify({'success': True, 'message': 'Cache cleared!'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/cache_info')
def cache_info():
    """Return info about the current cache state."""
    meta_file = os.path.join(CACHE_DIR, 'matrix_cache_meta.json')
    csv_file = os.path.join(CACHE_DIR, 'cached_distance_matrix.csv')
    info = {'exists': False, 'rows': 0, 'hash': None}

    if os.path.exists(meta_file):
        try:
            with open(meta_file, 'r') as f:
                meta = json.load(f)
            info['hash'] = meta.get('pairs_hash', '')[:12] + '...'
            info['exists'] = True
        except Exception:
            pass

    if os.path.exists(csv_file):
        try:
            df = pd.read_csv(csv_file)
            info['rows'] = len(df)
        except Exception:
            pass

    return jsonify(info)


@app.route('/download_template')
def download_template():
    template_path = os.path.join(INPUTS_DIR, 'S&OP_Template.xlsx')
    df = pd.DataFrame(columns=['CITY', 'MH ', 'DH', 'w1', 'Vehicle restrictions'])
    df.loc[0] = ['Bengaluru', 'BLR-DRY-MH-SUMADHURA', 'BLR-Sanjaynagar', 1892, '14FT_TRUCK']

    with pd.ExcelWriter(template_path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='S&OP', index=False)

    return send_file(template_path, as_attachment=True,
                     download_name='Zepto_S&OP_Template.xlsx')


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    if file:
        upload_path = os.path.join(INPUTS_DIR, 'details.xlsx')
        try:
            file.save(upload_path)
        except PermissionError:
            return jsonify({
                'error': 'File is locked by another program. '
                         'Please close details.xlsx in Excel and try again.'
            }), 400

        try:
            process = subprocess.Popen(
                ['run_pipeline.bat'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True
            )
            stdout, stderr = process.communicate()

            if process.returncode != 0:
                err_text = stderr.decode('utf-8', errors='replace')
                print(f"Error executing pipeline: {err_text}")
                return jsonify({
                    'error': 'Pipeline execution failed.',
                    'details': err_text
                }), 500

            return jsonify({'success': True, 'message': 'Optimization Complete!'})

        except Exception as e:
            return jsonify({'error': str(e)}), 500


@app.route('/download_results')
def download_results():
    results_path = os.path.join(OUTPUTS_DIR, 'Zepto_Final_Master_Plan.xlsx')
    if os.path.exists(results_path):
        return send_file(results_path, as_attachment=True,
                         download_name='Zepto_Final_Master_Plan.xlsx')
    return "Results not found", 404


@app.route('/view_map')
def view_map():
    map_path = os.path.join(VISUALS_DIR, 'optimized_routes_map.html')
    if os.path.exists(map_path):
        return send_file(map_path)
    return "Map not found", 404


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
