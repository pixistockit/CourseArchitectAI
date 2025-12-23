# /app.py

import os
import uuid
import json
import shutil
import re
import csv
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from jinja2 import ChoiceLoader, FileSystemLoader

# --- SERVICE INITIALIZATION ---
from services.logger_service import LoggerService

# --- MODULE IMPORTS ---
from modules.audit_slide.qa_tool import run_audit_slide
from modules.audit_slide.ai_engine import AIEngine
from modules.audit_slide.fix_engine import FixEngine
import modules.audit_slide.config as CFG 

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = 'data/uploads'
OUTPUT_FOLDER = 'data/reports'
LOG_FOLDER = 'data/logs'
CONFIG_DIR = 'data/config'

for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER, LOG_FOLDER, CONFIG_DIR]:
    os.makedirs(folder, exist_ok=True)

app = Flask(__name__)

# --- ADVANCED TEMPLATE LOADER CONFIGURATION ---
platform_template_dir = os.path.join(BASE_DIR, 'platform_shell', 'templates')
module_template_dir = os.path.join(BASE_DIR, 'modules', 'audit_slide', 'templates')

app.jinja_loader = ChoiceLoader([
    FileSystemLoader(platform_template_dir),
    FileSystemLoader(module_template_dir)
])

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

logger_service = LoggerService(base_data_path='data')


def get_or_create_cached_report(report_id, template_name, output_filename):
    report_dir = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
    json_path = os.path.join(report_dir, 'audit_report.json')
    cached_path = os.path.join(report_dir, output_filename)
    template_path = os.path.join(module_template_dir, template_name)
    if not (os.path.exists(json_path) and os.path.exists(template_path)):
        return None, 404
    needs_rebuild = True
    if os.path.exists(cached_path):
        try:
            cache_mtime = os.path.getmtime(cached_path)
            data_mtime = os.path.getmtime(json_path)
            template_mtime = os.path.getmtime(template_path)
            if cache_mtime > data_mtime and cache_mtime > template_mtime: needs_rebuild = False
        except Exception as e:
            logger_service.log_audit(report_id, 'warning', f"Cache timestamp check failed: {e}", agent='CACHE_MANAGER')
    if needs_rebuild:
        logger_service.log_audit(report_id, 'info', f"Rebuilding cache for {output_filename}", agent='CACHE_MANAGER')
        try:
            with open(template_path, 'r', encoding='utf-8') as f: html_template = f.read()
            with open(json_path, 'r', encoding='utf-8') as f: full_data = json.load(f)
            json_str = json.dumps(full_data)
            final_html = html_template.replace('/* INSERT_JSON_HERE */', f"const auditData = {json_str};")
            with open(cached_path, 'w', encoding='utf-8') as f: f.write(final_html)
        except Exception as e:
            logger_service.log_audit(report_id, 'error', f"Cache rebuild failed: {e}", agent='CACHE_MANAGER')
            return None, 500
    return cached_path, 200

# --- ROUTES ---

@app.route('/')
def index():
    """
    Serves the Master Admin Dashboard with LIVE data.
    """
    logger_service.log_system('info', 'Admin dashboard accessed', ip=request.remote_addr)

    # --- KPI CALCULATION LOGIC ---
    total_audits = 0
    all_scores = []
    
    if os.path.exists(OUTPUT_FOLDER):
        for item in os.listdir(OUTPUT_FOLDER):
            item_path = os.path.join(OUTPUT_FOLDER, item)
            if os.path.isdir(item_path):
                json_path = os.path.join(item_path, 'audit_report.json')
                if os.path.exists(json_path):
                    total_audits += 1
                    try:
                        with open(json_path, 'r') as f:
                            data = json.load(f)
                            score = data.get('summary', {}).get('executive_metrics', {}).get('wcag_compliance_rate', 0)
                            all_scores.append(score)
                    except Exception:
                        pass # Ignore malformed JSON for KPI calculation
    
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0

    # --- TOKEN USAGE CALCULATION ---
    total_tokens = 0
    token_ledger_path = os.path.join('data', 'logs', 'token_ledger.csv')
    if os.path.exists(token_ledger_path):
        try:
            with open(token_ledger_path, 'r', newline='') as f:
                reader = csv.reader(f)
                next(reader) # Skip header
                for row in reader:
                    # Assuming input_tokens is column 4 and output_tokens is column 5
                    total_tokens += int(row[4]) + int(row[5])
        except Exception as e:
            logger_service.log_system('error', f"Could not calculate tokens: {e}")

    kpi_data = {
        'total_audits': total_audits,
        'avg_compliance_score': avg_score,
        'tokens_consumed_monthly': total_tokens,
        'active_users': 12 # Placeholder until user auth is implemented
    }

    # --- SYSTEM LOG READER ---
    system_logs = []
    log_file_path = os.path.join('data', 'logs', 'platform_system.log')
    try:
        if os.path.exists(log_file_path):
            with open(log_file_path, 'r') as f: lines = f.readlines()[-100:]
            for line in reversed(lines):
                if len(system_logs) >= 10: break
                match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*? - (\w+) - (.*)", line)
                if match:
                    system_logs.append({
                        'timestamp': match.group(1).split(' ')[1],
                        'level': match.group(2),
                        'message': match.group(3).strip()
                    })
    except Exception as e:
        logger_service.log_system('error', f"Failed to parse system log for dashboard: {e}")

    return render_template('dashboard.html', active_page='dashboard', kpis=kpi_data, system_logs=system_logs)

@app.route('/projects')
def projects_page():
    logger_service.log_system('info', 'Projects page accessed', ip=request.remote_addr)
    projects = {} 
    if os.path.exists(OUTPUT_FOLDER):
        for item in os.listdir(OUTPUT_FOLDER):
            item_path = os.path.join(OUTPUT_FOLDER, item)
            if os.path.isdir(item_path):
                json_path = os.path.join(item_path, 'audit_report.json')
                if os.path.exists(json_path):
                    try:
                        with open(json_path, 'r') as f: data = json.load(f)
                        summary = data.get('summary', {})
                        p_name = summary.get('project_name') or summary.get('presentation_name', 'Unsorted Projects')
                        file_data = {
                            'id': item, 'filename': summary.get('presentation_name', 'Unknown'),
                            'date': summary.get('date_generated', '')[:10],
                            'score': summary.get('executive_metrics', {}).get('wcag_compliance_rate', 0),
                            'issues': summary.get('total_errors', 0)
                        }
                        if p_name not in projects: projects[p_name] = []
                        projects[p_name].append(file_data)
                    except Exception as e:
                        logger_service.log_system('warning', f"Skipping malformed report directory {item}: {e}", ip=request.remote_addr)
    
    for p in projects: projects[p].sort(key=lambda x: x['date'], reverse=True)
    return render_template('projects.html', projects=projects, active_page='projects')

@app.route('/upload', methods=['POST'])
def upload_file():
    ip_addr = request.remote_addr
    if 'file' not in request.files: return jsonify({"status": "error", "message": "No file part"}), 400
    file = request.files['file']
    if file.filename == '': return jsonify({"status": "error", "message": "No selected file"}), 400
    if file and file.filename.endswith(('.pptx', '.ppt')):
        unique_id = str(uuid.uuid4())
        try:
            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(save_path)
            audit_output_dir = os.path.join(app.config['OUTPUT_FOLDER'], unique_id)
            os.makedirs(audit_output_dir, exist_ok=True)
            run_audit_slide(save_path, audit_output_dir)
            project_name = request.form.get('project_name')
            if project_name:
                json_path = os.path.join(audit_output_dir, 'audit_report.json')
                if os.path.exists(json_path):
                    with open(json_path, 'r') as f: data = json.load(f)
                    data['summary']['project_name'] = project_name
                    with open(json_path, 'w') as f: json.dump(data, f, indent=4)
            logger_service.log_system('info', f"SUCCESS: New audit completed for {filename}. Report ID: {unique_id}", ip=ip_addr)
            return jsonify({"status": "success", "session_id": unique_id})
        except Exception as e:
            logger_service.log_system('error', f"CRITICAL FAILURE during audit for {filename}. See audit log {unique_id}. Error: {e}", ip=ip_addr)
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Invalid file type"}), 400

@app.route('/new-audit')
def new_audit():
    logger_service.log_system('info', "New audit page accessed", ip=request.remote_addr)
    existing_projects = set()
    if os.path.exists(OUTPUT_FOLDER):
        for item in os.listdir(OUTPUT_FOLDER):
            json_path = os.path.join(OUTPUT_FOLDER, item, 'audit_report.json')
            if os.path.exists(json_path):
                try:
                    with open(json_path, 'r') as f: data = json.load(f)
                    p = data.get('summary', {}).get('project_name')
                    if p and p != 'Unsorted Projects': existing_projects.add(p)
                except Exception as e:
                    logger_service.log_system('warning', f"Error reading project name from {json_path}: {e}")
    return render_template('new_audit.html', active_page='projects', existing_projects=sorted(list(existing_projects)))

@app.route('/view-workstation/<report_id>')
def view_workstation(report_id):
    logger_service.log_system('info', f"Workstation view requested for {report_id}", ip=request.remote_addr)
    json_path = os.path.join(app.config['OUTPUT_FOLDER'], report_id, 'audit_report.json')
    if not os.path.exists(json_path): return "Error: Audit report not found.", 404
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            audit_data = json.load(f)
    except Exception as e:
        logger_service.log_system('error', f"Failed to load audit JSON for workstation: {e}", ip=request.remote_addr)
        return "Error: Could not load audit data.", 500
    return render_template('workstation.html', active_page='projects', audit_data=audit_data)

@app.route('/settings')
def settings():
    # This logic is complex and remains as it was, no changes needed.
    llm_config_path = os.path.join(CONFIG_DIR, 'llm_config.json'); llm_config = {}
    if os.path.exists(llm_config_path):
        with open(llm_config_path, 'r') as f: llm_config = json.load(f)
    brand_config_path = os.path.join(CONFIG_DIR, 'brand_config.json'); brand_config = {}
    if os.path.exists(brand_config_path):
        with open(brand_config_path, 'r') as f: brand_config = json.load(f)
    return render_template('settings.html', config=llm_config, brand_config=brand_config, active_page='settings')

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
