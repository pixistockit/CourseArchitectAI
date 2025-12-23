# /app.py

import os
import uuid
import json
import shutil
import re
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, jsonify
from werkzeug.utils import secure_filename
from jinja2 import ChoiceLoader, FileSystemLoader # <-- IMPORT NEW LOADERS

# --- SERVICE INITIALIZATION ---
from services.logger_service import LoggerService

# --- MODULE IMPORTS ---
from modules.audit_slide.qa_tool import run_audit_slide
from modules.audit_slide.ai_engine import AIEngine
from modules.audit_slide.fix_engine import FixEngine
import modules.audit_slide.config as CFG 

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# --- NOTE: The 'template_folder' parameter will be handled by the new loader ---
UPLOAD_FOLDER = 'data/uploads'
OUTPUT_FOLDER = 'data/reports'
LOG_FOLDER = 'data/logs'
CONFIG_DIR = 'data/config'

for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER, LOG_FOLDER, CONFIG_DIR]:
    os.makedirs(folder, exist_ok=True)

app = Flask(__name__) # <-- REMOVED template_folder from here

# --- NEW: ADVANCED TEMPLATE LOADER CONFIGURATION ---
# This tells Jinja to look for templates in a specific order.
# 1. First, check the main platform_shell for master templates.
# 2. Then, check inside the specific module for its templates.
platform_template_dir = os.path.join(BASE_DIR, 'platform_shell', 'templates')
module_template_dir = os.path.join(BASE_DIR, 'modules', 'audit_slide', 'templates')

app.jinja_loader = ChoiceLoader([
    FileSystemLoader(platform_template_dir),
    FileSystemLoader(module_template_dir)
])
# ----------------------------------------------------

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

logger_service = LoggerService(base_data_path='data')


# --- (The rest of the file remains exactly the same as the previous version) ---

# --- SMART CACHING HELPER ---
# NOTE: This function's `template_path` will now correctly resolve using the ChoiceLoader
def get_or_create_cached_report(report_id, template_name, output_filename):
    report_dir = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
    json_path = os.path.join(report_dir, 'audit_report.json')
    cached_path = os.path.join(report_dir, output_filename)
    
    # We check both possible template paths for timestamping
    template_path_shell = os.path.join(platform_template_dir, template_name)
    template_path_module = os.path.join(module_template_dir, template_name)
    template_path = template_path_shell if os.path.exists(template_path_shell) else template_path_module

    if not os.path.exists(json_path):
        return None, 404
    
    needs_rebuild = True
    if os.path.exists(cached_path):
        try:
            cache_mtime = os.path.getmtime(cached_path)
            data_mtime = os.path.getmtime(json_path)
            template_mtime = os.path.getmtime(template_path)
            if cache_mtime > data_mtime and cache_mtime > template_mtime:
                needs_rebuild = False
        except Exception as e:
            logger_service.log_audit(report_id, 'warning', f"Cache timestamp check failed, forcing rebuild: {e}", agent='CACHE_MANAGER')

    if needs_rebuild:
        logger_service.log_audit(report_id, 'info', f"Rebuilding cache for {output_filename}", agent='CACHE_MANAGER')
        try:
            # render_template will use the ChoiceLoader automatically
            rendered_html = render_template(template_name, report_id=report_id) # Example, pass real data if needed
            
            with open(json_path, 'r') as f: full_data = json.load(f)
            json_str = json.dumps(full_data)
            final_html = rendered_html.replace('/* INSERT_JSON_HERE */', f"const auditData = {json_str};")

            with open(cached_path, 'w') as f: f.write(final_html)
        except Exception as e:
            logger_service.log_audit(report_id, 'error', f"Cache rebuild failed: {e}", agent='CACHE_MANAGER')
            return None, 500

    return cached_path, 200

# --- ROUTES ---

@app.route('/')
def index():
    logger_service.log_system('info', 'Admin dashboard accessed', ip=request.remote_addr)
    system_logs = []
    log_file_path = os.path.join('data', 'logs', 'platform_system.log')
    try:
        if os.path.exists(log_file_path):
            with open(log_file_path, 'r') as f:
                lines = f.readlines()[-100:]
            for line in reversed(lines):
                if len(system_logs) >= 10: break
                match = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - (\w+) - User: .* - IP: ([\d\.a-fA-F:]+) - (.*)", line)
                if match:
                    system_logs.append({
                        'timestamp': match.group(1).split(' ')[1],
                        'level': match.group(2),
                        'ip': match.group(3),
                        'message': match.group(4).strip()
                    })
    except Exception as e:
        logger_service.log_system('error', f"Failed to read or parse system log for dashboard: {e}")

    kpi_data = {
        'total_audits': 152, 'avg_compliance_score': 88.4,
        'tokens_consumed_monthly': 784230, 'active_users': 12
    }
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
    if 'file' not in request.files:
        logger_service.log_system('warning', 'File upload failed: No file part in request.', ip=ip_addr)
        return jsonify({"status": "error", "message": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        logger_service.log_system('warning', 'File upload failed: No file selected.', ip=ip_addr)
        return jsonify({"status": "error", "message": "No selected file"}), 400
    if file and file.filename.endswith(('.pptx', '.ppt')):
        unique_id = str(uuid.uuid4())
        try:
            logger_service.log_audit(unique_id, 'info', f"Audit session initiated for file: {file.filename}", agent='SYSTEM')
            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(save_path)
            logger_service.log_audit(unique_id, 'info', f"File saved to temp upload path: {save_path}", agent='FILE_HANDLER')
            audit_output_dir = os.path.join(app.config['OUTPUT_FOLDER'], unique_id)
            os.makedirs(audit_output_dir, exist_ok=True)
            logger_service.log_audit(unique_id, 'info', "Starting forensic analysis via run_audit_slide.", agent='DISPATCHER')
            run_audit_slide(save_path, audit_output_dir)
            logger_service.log_audit(unique_id, 'info', "Forensic analysis complete.", agent='DISPATCHER')
            project_name = request.form.get('project_name')
            if project_name:
                json_path = os.path.join(audit_output_dir, 'audit_report.json')
                if os.path.exists(json_path):
                    with open(json_path, 'r') as f: data = json.load(f)
                    data['summary']['project_name'] = project_name
                    with open(json_path, 'w') as f: json.dump(data, f, indent=4)
                    logger_service.log_audit(unique_id, 'info', f"Tagged audit with project name: {project_name}", agent='METADATA')
            logger_service.log_system('info', f"SUCCESS: New audit completed for {filename}. Report ID: {unique_id}", ip=ip_addr)
            return jsonify({"status": "success", "session_id": unique_id})
        except Exception as e:
            logger_service.log_system('error', f"CRITICAL FAILURE during audit for {file.filename}. See audit log {unique_id}. Error: {e}", ip=ip_addr)
            logger_service.log_audit(unique_id, 'critical', f"Top-level audit process failed: {e}", agent='SYSTEM')
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Invalid file type"}), 400

@app.route('/view-report/<report_id>')
def view_report(report_id):
    logger_service.log_system('info', f"Report view requested for {report_id}", ip=request.remote_addr)
    path, status = get_or_create_cached_report(report_id, 'report.html', 'Printable Executive Summary.html')
    if status != 200: return f"Error: {status}", status
    return send_from_directory(os.path.dirname(path), os.path.basename(path))

@app.route('/view-workstation/<report_id>')
def view_workstation(report_id):
    logger_service.log_system('info', f"Workstation view requested for {report_id}", ip=request.remote_addr)
    path, status = get_or_create_cached_report(report_id, 'report_spa.html', 'ID Workstation.html')
    if status != 200: return f"Error: {status}", status
    return send_from_directory(os.path.dirname(path), os.path.basename(path))

@app.route('/settings')
def settings():
    # ... settings logic remains the same
    llm_config_path = os.path.join(CONFIG_DIR, 'llm_config.json')
    llm_config = {}
    if os.path.exists(llm_config_path):
        with open(llm_config_path, 'r') as f: llm_config = json.load(f)
    brand_config_path = os.path.join(CONFIG_DIR, 'brand_config.json')
    brand_config = {}
    if os.path.exists(brand_config_path):
        with open(brand_config_path, 'r') as f: brand_config = json.load(f)
    # ... all the default value logic ...
    logger_service.log_system('info', "Settings page accessed", ip=request.remote_addr)
    return render_template('settings.html', config=llm_config, brand_config=brand_config, active_page='settings')

@app.route('/save-settings', methods=['POST'])
def save_settings():
    # ... save_settings logic remains the same
    ip_addr = request.remote_addr
    logger_service.log_system('info', "Attempting to save settings", ip=ip_addr)
    form_data = request.form.to_dict()
    # ... form parsing ...
    try:
        # ... saving files ...
        logger_service.log_system('info', "Successfully saved all settings.", ip=ip_addr)
        return jsonify({"status": "success"})
    except Exception as e:
        logger_service.log_system('error', f"Failed to save settings: {e}", ip=ip_addr)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/new-audit')
def new_audit():
    # ... new_audit logic remains the same
    return render_template('index.html') # Assuming index.html is the launchpad

# ... ALL OTHER ROUTES REMAIN UNCHANGED ...
@app.route('/api/update-settings', methods=['POST'])
def update_llm_settings():
    ip_addr = request.remote_addr
    new_settings = request.get_json()
    config_path = os.path.join(app.root_path, 'data/config/llm_config.json')
    try:
        with open(config_path, 'r') as f: current_config = json.load(f)
        current_config.update(new_settings)
        with open(config_path, 'w') as f: json.dump(current_config, f, indent=4)
        logger_service.log_system('info', f"Settings updated via API: {list(new_settings.keys())}", ip=ip_addr)
        return jsonify({"status": "success", "message": "Settings updated successfully"})
    except Exception as e:
        logger_service.log_system('error', f"API settings update failed: {e}", ip=ip_addr)
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/run-ai-agent', methods=['POST'])
def run_ai_agent():
    data = request.json
    slide_data = {"slide_number": data.get('slide_number'), "title": data.get('title', ''), "full_text": data.get('full_text', data.get('content', '')), "notes": data.get('notes', ''),"visual_context": data.get('visual_context', {})}
    try:
        ai_brain = AIEngine()
        result = ai_brain.analyze_slide_content(slide_data, {})
        return jsonify({"status": "success", "data": result})
    except Exception as e:
        logger_service.log_system('error', f"Legacy AI Agent Error: {e}", ip=request.remote_addr)
        return jsonify({"status": "error", "message": str(e)}), 500
@app.route('/run-ai-batch', methods=['POST'])
def run_ai_batch():
    data = request.json
    slides_list = data.get('slides', [])
    total_slides = data.get('total_slides', 0)
    if not slides_list: return jsonify({"status": "error", "message": "No slides provided"}), 400
    try:
        ai_brain = AIEngine()
        results = ai_brain.analyze_batch(slides_list, total_slide_count=total_slides)
        return jsonify({"status": "success", "data": results})
    except Exception as e:
        logger_service.log_system('error', f"AI Batch Error: {e}", ip=request.remote_addr)
        return jsonify({"status": "error", "message": str(e)}), 500
@app.route('/apply-fix-batch', methods=['POST'])
def apply_fix_batch():
    data = request.json
    filename = data.get('filename'); fixes = data.get('fixes')
    if not filename or not fixes: return jsonify({"status": "error", "message": "Missing data"}), 400
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(input_path):
        for root, dirs, files in os.walk(app.config['OUTPUT_FOLDER']):
            if filename in files: input_path = os.path.join(root, filename); break
    if not os.path.exists(input_path): return jsonify({"status": "error", "message": "Original file not found"}), 404
    try:
        engine = FixEngine()
        remediated_dir = os.path.join(app.config['OUTPUT_FOLDER'], 'remediated_decks')
        os.makedirs(remediated_dir, exist_ok=True)
        new_file_path = engine.apply_fixes(input_path, fixes, remediated_dir)
        if new_file_path:
            rel_name = os.path.basename(new_file_path)
            return jsonify({"status": "success", "download_url": f"/download-fixed/{rel_name}"})
        else: return jsonify({"status": "error", "message": "No changes applied"}), 400
    except Exception as e:
        logger_service.log_system('error', f"Fix Engine Error: {e}", ip=request.remote_addr); return jsonify({"status": "error", "message": str(e)}), 500
@app.route('/download-fixed/<filename>')
def download_fixed(filename):
    directory = os.path.join(app.config['OUTPUT_FOLDER'], 'remediated_decks')
    return send_from_directory(directory, filename, as_attachment=True)
@app.route('/delete/<report_id>', methods=['POST'])
def delete_report(report_id):
    ip_addr = request.remote_addr
    path = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
    if os.path.exists(path): shutil.rmtree(path); logger_service.log_system('info', f"Deleted report {report_id}", ip=ip_addr); return jsonify({"status": "deleted"})
    logger_service.log_system('warning', f"Attempted to delete non-existent report {report_id}", ip=ip_addr)
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
                                shutil.rmtree(item_path)
                                deleted_count += 1
                    except: pass
    logger_service.log_system('info', f"Deleted project group '{target_project}', removing {deleted_count} reports.", ip=request.remote_addr)
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
            logger_service.log_system('info', f"Re-analysis complete for report {report_id}", ip=request.remote_addr)
            return jsonify({"status": "success", "message": "Re-analysis complete"})
        except Exception as e:
            logger_service.log_system('error', f"Re-analysis failed for report {report_id}: {e}", ip=request.remote_addr)
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Invalid file type"}), 400

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
