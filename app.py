# /app.py - DEFINITIVE VERSION

import os
import uuid
import json
import shutil
import re
import csv
from flask import Flask, render_template, request, url_for, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from jinja2 import ChoiceLoader, FileSystemLoader

# --- SERVICE & MODULE IMPORTS ---
from services.logger_service import LoggerService
from modules.audit_slide.qa_tool import run_audit_slide
from modules.audit_slide.ai_engine import AIEngine
from modules.audit_slide.fix_engine import FixEngine
import modules.audit_slide.config as CFG 

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER, OUTPUT_FOLDER, LOG_FOLDER, CONFIG_DIR = 'data/uploads', 'data/reports', 'data/logs', 'data/config'
for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER, LOG_FOLDER, CONFIG_DIR]: os.makedirs(folder, exist_ok=True)

app = Flask(__name__)

# --- ADVANCED TEMPLATE LOADER CONFIGURATION ---
platform_template_dir = os.path.join(BASE_DIR, 'platform_shell', 'templates')
module_template_dir = os.path.join(BASE_DIR, 'modules', 'audit_slide', 'templates')
app.jinja_loader = ChoiceLoader([FileSystemLoader(platform_template_dir), FileSystemLoader(module_template_dir)])
app.config.update(UPLOAD_FOLDER=UPLOAD_FOLDER, OUTPUT_FOLDER=OUTPUT_FOLDER, MAX_CONTENT_LENGTH=500*1024*1024)
logger_service = LoggerService(base_data_path='data')

# --- SMART CACHING HELPER ---
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
            cache_mtime = os.path.getmtime(cached_path); data_mtime = os.path.getmtime(json_path); template_mtime = os.path.getmtime(template_path)
            if cache_mtime > data_mtime and cache_mtime > template_mtime: needs_rebuild = False
        except Exception as e:
            logger_service.log_audit(report_id, 'warning', f"Cache timestamp check failed: {e}", agent='CACHE_MANAGER')
    if needs_rebuild:
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
    logger_service.log_system('info', 'Admin dashboard accessed', ip=request.remote_addr)
    total_audits, all_scores = 0, []
    if os.path.exists(OUTPUT_FOLDER):
        for item in os.listdir(OUTPUT_FOLDER):
            if os.path.isdir(os.path.join(OUTPUT_FOLDER, item)):
                json_path = os.path.join(OUTPUT_FOLDER, item, 'audit_report.json')
                if os.path.exists(json_path):
                    total_audits += 1
                    try:
                        with open(json_path, 'r') as f: data = json.load(f)
                        all_scores.append(data.get('summary', {}).get('executive_metrics', {}).get('wcag_compliance_rate', 0))
                    except: pass
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0
    total_tokens = 0
    token_ledger_path = os.path.join('data', 'logs', 'token_ledger.csv')
    if os.path.exists(token_ledger_path):
        try:
            with open(token_ledger_path, 'r', newline='') as f:
                reader = csv.reader(f); next(reader, None)
                for row in reader: total_tokens += int(row[4]) + int(row[5])
        except Exception as e: logger_service.log_system('error', f"Could not calculate tokens: {e}")
    kpi_data = {'total_audits': total_audits, 'avg_compliance_score': avg_score, 'tokens_consumed_monthly': total_tokens, 'active_users': 12 }
    system_logs = []
    # Logic to read logs for dashboard display
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
            logger_service.log_system('error', f"CRITICAL FAILURE during audit for {filename}. Error: {e}", ip=ip_addr)
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

@app.route('/view-report/<report_id>')
def view_report(report_id):
    path, status = get_or_create_cached_report(report_id, 'report.html', 'Printable Executive Summary.html')
    if status != 200: return f"Error: {status}", status
    return send_from_directory(os.path.dirname(path), os.path.basename(path))

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
    logger_service.log_system('info', "Settings page accessed", ip=request.remote_addr)
    llm_config_path = os.path.join(CONFIG_DIR, 'llm_config.json')
    llm_config = {}
    if os.path.exists(llm_config_path):
        with open(llm_config_path, 'r') as f: llm_config = json.load(f)

    brand_config_path = os.path.join(CONFIG_DIR, 'brand_config.json')
    brand_config = {}
    if os.path.exists(brand_config_path):
        with open(brand_config_path, 'r') as f: brand_config = json.load(f)

    if llm_config.get('blacklist'):
        val = llm_config['blacklist']
        display_str = ""
        if isinstance(val, dict):
            for k, v in val.items(): display_str += f"{k}:{v}\n" if v else f"{k}\n"
        else: display_str = str(val)
        llm_config['blacklist_display'] = display_str.strip()
    
    return render_template('settings.html', config=llm_config, brand_config=brand_config, active_page='settings')

@app.route('/save-settings', methods=['POST'])
def save_settings():
    logger_service.log_system('info', "Attempting to save settings", ip=request.remote_addr)
    form_data = request.form.to_dict()
    
    raw_text = form_data.get('blacklist', '')
    blacklist_dict = {}
    if raw_text:
        for line in raw_text.splitlines():
            if not line.strip(): continue
            parts = line.split(':'); key = parts[0].strip().lower(); val = parts[1].strip() if len(parts) > 1 else ""
            if key: blacklist_dict[key] = val

    llm_keys = [
        'agent_1_provider', 'agent_2_provider', 'agent_3_provider',
        'gemini_api_key', 'openai_api_key', 'anthropic_api_key', 'groq_api_key', 'mistral_api_key',
        'aws_access_key', 'aws_secret_key', 'aws_region',
        'default_grade', 'max_words_per_slide', 'contrast_ratio', 'min_font_size',
        'check_spelling', 'check_grammar'
    ]
    llm_config = {k: form_data.get(k, '') for k in llm_keys}; llm_config['blacklist'] = blacklist_dict 
    
    raw_allowed = form_data.get('allowed_fonts', ''); allowed_list = [x.strip() for x in raw_allowed.split(',') if x.strip()]
    raw_headers = form_data.get('required_headers', ''); headers_list = [h.strip() for h in raw_headers.splitlines() if h.strip()]
    
    brand_config = {
        'title_font': form_data.get('title_font'), 'body_font': form_data.get('body_font'),
        'allowed_fonts': allowed_list, 'required_headers': headers_list,
        'exempt_first_slide': 'exempt_first_slide' in form_data,
        'exempt_last_slide': 'exempt_last_slide' in form_data,
        'exempt_specific_slides': form_data.get('exempt_specific_slides', '')
    }
    
    try:
        with open(os.path.join(CONFIG_DIR, 'llm_config.json'), 'w') as f: json.dump(llm_config, f, indent=4)
        with open(os.path.join(CONFIG_DIR, 'brand_config.json'), 'w') as f: json.dump(brand_config, f, indent=4)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/update-settings', methods=['POST'])
def update_llm_settings():
    new_settings = request.get_json()
    config_path = os.path.join(app.root_path, 'data/config/llm_config.json')
    try:
        with open(config_path, 'r') as f: current_config = json.load(f)
        current_config.update(new_settings)
        with open(config_path, 'w') as f: json.dump(current_config, f, indent=4)
        return jsonify({"status": "success", "message": "Settings updated successfully"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/delete/<report_id>', methods=['POST'])
def delete_report(report_id):
    path = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
    if os.path.exists(path): shutil.rmtree(path); return jsonify({"status": "deleted"})
    return jsonify({"status": "error"}), 404

@app.route('/delete-project-group', methods=['POST'])
def delete_project_group():
    data = request.json
    target_project = data.get('project_name')
    if not target_project: return jsonify({"status": "error", "message": "Missing project name"}), 400
    deleted_count = 0
    if os.path.exists(OUTPUT_FOLDER):
        for item in os.listdir(OUTPUT_FOLDER):
            item_path = os.path.join(OUTPUT_FOLDER, item)
            if os.path.isdir(item_path):
                json_path = os.path.join(item_path, 'audit_report.json')
                if os.path.exists(json_path):
                    try:
                        with open(json_path, 'r') as f:
                            summary = json.load(f).get('summary', {})
                            p_name = summary.get('project_name') or summary.get('presentation_name')
                            if p_name == target_project:
                                shutil.rmtree(item_path); deleted_count += 1
                    except: pass
    return jsonify({"status": "success", "deleted_count": deleted_count})

@app.route('/reanalyze/<report_id>', methods=['POST'])
def reanalyze_deck(report_id):
    if 'file' not in request.files: return jsonify({"status": "error", "message": "No file uploaded"}), 400
    file = request.files['file']
    if file.filename == '': return jsonify({"status": "error", "message": "No file selected"}), 400
    if file and file.filename.endswith(('.pptx', '.ppt')):
        try:
            audit_output_dir = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{report_id}_{filename}")
            file.save(save_path)
            run_audit_slide(save_path, audit_output_dir)
            return jsonify({"status": "success", "message": "Re-analysis complete"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Invalid file type"}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
