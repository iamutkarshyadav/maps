import os
import subprocess
import pandas as pd
from flask import Flask, render_template, request, jsonify, send_file, send_from_directory

app = Flask(__name__)

# Paths
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
INPUTS_DIR = os.path.join(DATA_DIR, 'inputs')
OUTPUTS_DIR = os.path.join(DATA_DIR, 'outputs')
VISUALS_DIR = os.path.join(DATA_DIR, 'visuals')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/download_template')
def download_template():
    # Create the template format dynamically
    template_path = os.path.join(INPUTS_DIR, 'S&OP_Template.xlsx')
    
    # Generate template only if it doesn't exist to save time, or always overwrite
    df = pd.DataFrame(columns=['CITY', 'MH ', 'DH', 'w1', 'Vehicle restrictions'])
    # Add a dummy row for guidance
    df.loc[0] = ['Bengaluru', 'BLR-DRY-MH-SUMADHURA', 'BLR-Sanjaynagar', 1892, '14FT_TRUCK']
    
    # Save as Excel using openpyxl
    with pd.ExcelWriter(template_path, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='S&OP', index=False)
        
    return send_file(template_path, as_attachment=True, download_name='Zepto_S&OP_Template.xlsx')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
        
    if file:
        # Save the uploaded file as details.xlsx
        upload_path = os.path.join(INPUTS_DIR, 'details.xlsx')
        try:
            file.save(upload_path)
        except PermissionError:
            return jsonify({'error': 'File is locked by another program. Please close details.xlsx in Excel and try again.'}), 400
        
        # Execute the pipeline
        try:
            # We run the batch script. Since it uses relative paths (cd scripts), we run it from root.
            process = subprocess.Popen(['run_pipeline.bat'], stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
            stdout, stderr = process.communicate()
            
            if process.returncode != 0:
                print(f"Error executing pipeline: {stderr.decode('utf-8')}")
                return jsonify({'error': 'Pipeline execution failed.', 'details': stderr.decode('utf-8')}), 500
                
            return jsonify({'success': True, 'message': 'Optimization Complete!'})
            
        except Exception as e:
            return jsonify({'error': str(e)}), 500

@app.route('/download_results')
def download_results():
    results_path = os.path.join(OUTPUTS_DIR, 'Zepto_Final_Master_Plan.xlsx')
    if os.path.exists(results_path):
        return send_file(results_path, as_attachment=True, download_name='Zepto_Final_Master_Plan.xlsx')
    return "Results not found", 404

@app.route('/view_map')
def view_map():
    map_path = os.path.join(VISUALS_DIR, 'optimized_routes_map.html')
    if os.path.exists(map_path):
        return send_file(map_path)
    return "Map not found", 404

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
