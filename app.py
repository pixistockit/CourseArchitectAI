# /app.py

import os
import uuid
import json
import shutil
from flask import Flask, render_template, request, redirect, url_for, send_from_directory, jsonify
from werkzeug.utils import secure_filename

# --- SERVICE INITIALIZATION ---
from services.logger_service import LoggerService

# --- MODIFIED: Import from the new 'modules' directory ---
from modules.audit_slide.qa_tool import run_audit_slide
from modules.audit_slide.ai_engine import AIEngine
from modules.audit_slide.fix_engine import FixEngine
import modules.audit_slide.config as CFG 

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# --- MODIFIED: Update the template path to the new module location ---
TEMPLATE_DIR = os.path.join(BASE_DIR, 'modules', 'audit_slide', 'templates')
UPLOAD_FOLDER = 'data/uploads'
OUTPUT_FOLDER = 'data/reports'
LOG_FOLDER = 'data/logs'
CONFIG_DIR = 'data/config'

for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER, LOG_FOLDER, CONFIG_DIR]:
    os.makedirs(folder, exist_ok=True)

app = Flask(__name__, template_folder=TEMPLATE_DIR)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['OUTPUT_FOLDER'] = OUTPUT_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 500 * 1024 * 1024

logger_service = LoggerService(base_data_path='data')

# --- (The rest of the file remains exactly the same as the previous version) ---

# --- SMART CACHING HELPER ---
def get_or_create_cached_report(report_id, template_name, output_filename):
    report_dir = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
    json_path = os.path.join(report_dir, 'audit_report.json')
    cached_path = os.path.join(report_dir, output_filename)
    template_path = os.path.join(TEMPLATE_DIR, template_name)

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
            with open(json_path, 'r') as f: full_data = json.load(f)
            with open(template_path, 'r') as f: html_template = f.read()
            json_str = json.dumps(full_data)
            final_html = html_template.replace('/* INSERT_JSON_HERE */', f"const auditData = {json_str};")
            with open(cached_path, 'w') as f: f.write(final_html)
        except Exception as e:
            logger_service.log_audit(report_id, 'error', f"Cache rebuild failed: {e}", agent='CACHE_MANAGER')
            return None, 500

    return cached_path, 200

# --- ROUTES ---

@app.route('/')
def index():
    logger_service.log_system('info', 'Admin dashboard accessed', ip=request.remote_addr)
    return render_template('dashboard.html', active_page='dashboard')

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
                        with open(json_path, 'r') as f:
                            data = json.load(f)
                            summary = data.get('summary', {})
                            p_name = summary.get('project_name') or summary.get('presentation_name', 'Unsorted Projects')
                            file_data = {
                                'id': item,
                                'filename': summary.get('presentation_name', 'Unknown'),
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
    llm_config_path = os.path.join(CONFIG_DIR, 'llm_config.json')
    llm_config = {}
    if os.path.exists(llm_config_path):
        with open(llm_config_path, 'r') as f: llm_config = json.load(f)

    brand_config_path = os.path.join(CONFIG_DIR, 'brand_config.json')
    brand_config = {}
    if os.path.exists(brand_config_path):
        with open(brand_config_path, 'r') as f: brand_config = json.load(f)

    if llm_config.get('default_buffer') is None:
        llm_config['default_buffer'] = getattr(CFG, 'BUFFER_ACTIVITY_SLIDE', 5.0)
    if llm_config.get('contrast_ratio') is None: llm_config['contrast_ratio'] = getattr(CFG, 'WCAG_RATIO_NORMAL', 4.5)
    if llm_config.get('min_font_size') is None: llm_config['min_font_size'] = getattr(CFG, 'WCAG_MIN_LARGE_FONT_SIZE', 18)
    if llm_config.get('wcag_strictness') is None: llm_config['wcag_strictness'] = 'aa'
    if llm_config.get('default_grade') is None: llm_config['default_grade'] = getattr(CFG, 'TARGET_READING_GRADE_LEVEL', 9)
    if llm_config.get('max_words_per_slide') is None: llm_config['max_words_per_slide'] = 60
    
    if not llm_config.get('blacklist'):
        bl = getattr(CFG, 'CLIENT_BLACKLIST', {})
        llm_config['blacklist_json'] = bl 
        display_str = ""
        for k, v in bl.items():
            if v: display_str += f"{k}: {v}\n"
            else: display_str += f"{k}\n"
        llm_config['blacklist_display'] = display_str.strip()
    else:
        val = llm_config['blacklist']
        if isinstance(val, dict):
            display_str = ""
            for k, v in val.items():
                if v: display_str += f"{k}: {v}\n"
                else: display_str += f"{k}\n"
            llm_config['blacklist_display'] = display_str.strip()
        elif isinstance(val, str):
            try:
                js = json.loads(val)
                if isinstance(js, dict):
                     display_str = ""
                     for k, v in js.items():
                         if v: display_str += f"{k}: {v}\n"
                         else: display_str += f"{k}\n"
                     llm_config['blacklist_display'] = display_str.strip()
                else:
                    llm_config['blacklist_display'] = val
            except:
                llm_config['blacklist_display'] = val

    if not brand_config.get('title_font'): brand_config['title_font'] = getattr(CFG, 'TITLE_FONT_NAME', 'Rockwell')
    if not brand_config.get('body_font'): brand_config['body_font'] = getattr(CFG, 'BODY_FONT_NAME', 'Calibri')
    if not brand_config.get('notes_font'): brand_config['notes_font'] = getattr(CFG, 'NOTES_FONT_NAME', 'Calibri')
    if not brand_config.get('allowed_fonts'): brand_config['allowed_fonts'] = getattr(CFG, 'ALLOWED_BODY_FONTS', ['Calibri', 'Arial'])
            
    logger_service.log_system('info', "Settings page accessed", ip=request.remote_addr)
    return render_template('settings.html', config=llm_config, brand_config=brand_config, active_page='settings')

@app.route('/save-settings', methods=['POST'])
def save_settings():
    ip_addr = request.remote_addr
    logger_service.log_system('info', "Attempting to save settings", ip=ip_addr)
    form_data = request.form.to_dict()
    
    raw_text = form_data.get('blacklist', '')
    blacklist_dict = {}
    if raw_text:
        lines = raw_text.splitlines()
        for line in lines:
            if not line.strip(): continue
            parts = line.split(':')
            key = parts[0].strip().lower() 
            val = parts[1].strip() if len(parts) > 1 else "" 
            if key: blacklist_dict[key] = val

    llm_keys = [
        'agent_1_provider', 'agent_2_provider', 'agent_3_provider',
        'gemini_api_key', 'openai_api_key', 'anthropic_api_key', 'groq_api_key', 'mistral_api_key',
        'aws_access_key', 'aws_secret_key', 'aws_region',
        'default_grade', 'default_buffer', 'max_words_per_slide',
        'contrast_ratio', 'min_font_size', 'wcag_strictness',
        'check_spelling', 'check_grammar'
    ]
    
    llm_config = {k: form_data.get(k, '') for k in llm_keys}
    llm_config['blacklist'] = blacklist_dict 
    
    raw_allowed = form_data.get('allowed_fonts', '')
    allowed_list = [x.strip() for x in raw_allowed.split(',') if x.strip()]
    raw_headers = form_data.get('required_headers', '')
    headers_list = [h.strip() for h in raw_headers.splitlines() if h.strip()]
    exempt_first = 'exempt_first_slide' in form_data
    exempt_last = 'exempt_last_slide' in form_data
    specific_slides = form_data.get('exempt_specific_slides', '')

    brand_config = {
        'title_font': form_data.get('title_font', 'Rockwell'),
        'body_font': form_data.get('body_font', 'Calibri'),
        'body_font_size': form_data.get('body_font_size', '24'),
        'notes_font': form_data.get('notes_font', 'Calibri'),
        'allowed_fonts': allowed_list,
        'required_headers': headers_list,
        'notes_scripting_level': form_data.get('notes_scripting_level', 'Light'),
        'exempt_first_slide': exempt_first,
        'exempt_last_slide': exempt_last,
        'exempt_specific_slides': specific_slides
    }
    
    try:
        with open(os.path.join(CONFIG_DIR, 'llm_config.json'), 'w') as f: 
            json.dump(llm_config, f, indent=4)
        with open(os.path.join(CONFIG_DIR, 'brand_config.json'), 'w') as f: 
            json.dump(brand_config, f, indent=4)
        logger_service.log_system('info', "Successfully saved all settings.", ip=ip_addr)
        return jsonify({"status": "success"})
    except Exception as e:
        logger_service.log_system('error', f"Failed to save settings: {e}", ip=ip_addr)
        return jsonify({"status": "error", "message": str(e)}), 500

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
    slide_data = {
        "slide_number": data.get('slide_number'), "title": data.get('title', ''), 
        "full_text": data.get('full_text', data.get('content', '')), "notes": data.get('notes', ''),
        "visual_context": data.get('visual_context', {})
    }
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
    if not slides_list:
        return jsonify({"status": "error", "message": "No slides provided"}), 400
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

@app.route('/new-audit')
def new_audit():
    existing_projects = set()
    if os.path.exists(OUTPUT_FOLDER):
        for item in os.listdir(OUTPUT_FOLDER):
            json_path = os.path.join(OUTPUT_FOLDER, item, 'audit_report.json')
            if os.path.exists(json_path):
                try:
                    with open(json_path, 'r') as f:
                        data = json.load(f)
                        p = data.get('summary', {}).get('project_name')
                        if p: existing_projects.add(p)
                except: pass
    
    llm_config_path = os.path.join(CONFIG_DIR, 'llm_config.json')
    defaults = {}
    if os.path.exists(llm_config_path):
        with open(llm_config_path, 'r') as f: defaults = json.load(f)
    if defaults.get('default_buffer') is None:
        defaults['default_buffer'] = getattr(CFG, 'BUFFER_ACTIVITY_SLIDE', 5.0)

    return render_template('index.html', existing_projects=sorted(list(existing_projects)), defaults=defaults)

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
