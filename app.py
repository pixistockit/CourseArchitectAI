# /app.py - MASTER PLATFORM CONTROLLER

import os
import uuid
import json
import shutil
import csv
import logging
from datetime import datetime
from flask import Flask, render_template, request, url_for, send_from_directory, jsonify, redirect
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
UPLOAD_FOLDER = os.path.join('data', 'uploads')
OUTPUT_FOLDER = os.path.join('data', 'reports')
LOG_FOLDER = os.path.join('data', 'logs')
CONFIG_DIR = os.path.join('data', 'config')

for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER, LOG_FOLDER, CONFIG_DIR]: 
    os.makedirs(folder, exist_ok=True)

app = Flask(__name__)

# --- ADVANCED TEMPLATE LOADER ---
# Allows extending 'layout.html' from platform_shell while using module templates
platform_template_dir = os.path.join(BASE_DIR, 'platform_shell', 'templates')
module_template_dir = os.path.join(BASE_DIR, 'modules', 'audit_slide', 'templates')
app.jinja_loader = ChoiceLoader([
    FileSystemLoader(platform_template_dir), 
    FileSystemLoader(module_template_dir)
])

app.config.update(
    UPLOAD_FOLDER=UPLOAD_FOLDER, 
    OUTPUT_FOLDER=OUTPUT_FOLDER, 
    MAX_CONTENT_LENGTH=500 * 1024 * 1024  # 500MB Limit
)

# Initialize Logger
logger_service = LoggerService(base_data_path='data')

# --- HELPER: CACHE MANAGER ---
def get_or_create_cached_report(report_id, template_name, output_filename):
    """
    Ensures static HTML reports are generated and up-to-date.
    """
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
            # If cache is newer than both data and template, use it
            if cache_mtime > data_mtime and cache_mtime > template_mtime:
                needs_rebuild = False
        except Exception as e:
            logger_service.log_system('warning', f"Cache timestamp check failed: {e}")

    if needs_rebuild:
        try:
            with open(template_path, 'r', encoding='utf-8') as f: 
                html_template = f.read()
            with open(json_path, 'r', encoding='utf-8') as f: 
                full_data = json.load(f)
            
            # Inject Data into JS const
            json_str = json.dumps(full_data)
            final_html = html_template.replace('/* INSERT_JSON_HERE */', f"const auditData = {json_str};")
            
            with open(cached_path, 'w', encoding='utf-8') as f: 
                f.write(final_html)
        except Exception as e:
            logger_service.log_system('error', f"Cache rebuild failed for {report_id}: {e}")
            return None, 500

    return cached_path, 200

# --- CORE PLATFORM ROUTES ---

@app.route('/')
def index():
    """
    Executive Dashboard (KPIs & High-level Stats)
    """
    total_audits = 0
    all_scores = []
    
    # 1. Calculate Stats from Files
    if os.path.exists(OUTPUT_FOLDER):
        for item in os.listdir(OUTPUT_FOLDER):
            json_path = os.path.join(OUTPUT_FOLDER, item, 'audit_report.json')
            if os.path.exists(json_path):
                total_audits += 1
                try:
                    with open(json_path, 'r') as f: 
                        data = json.load(f)
                        score = data.get('summary', {}).get('executive_metrics', {}).get('wcag_compliance_rate', 0)
                        all_scores.append(score)
                except: 
                    pass

    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0
    
    # 2. Calculate Tokens
    total_tokens = 0
    token_ledger_path = os.path.join(LOG_FOLDER, 'token_ledger.csv')
    if os.path.exists(token_ledger_path):
        try:
            with open(token_ledger_path, 'r') as f:
                reader = csv.reader(f)
                next(reader, None) # Skip header
                for row in reader:
                    if len(row) >= 6:
                        total_tokens += int(row[4]) + int(row[5])
        except Exception: pass

    kpi_data = {
        'total_audits': total_audits,
        'avg_compliance_score': avg_score,
        'tokens_consumed_monthly': total_tokens,
        'active_users': 1  # Hardcoded for single-tenant version
    }

    # 3. Get Recent Logs
    system_logs = logger_service.get_recent_logs(limit=5)

    return render_template('dashboard.html', active_page='dashboard', kpis=kpi_data, system_logs=system_logs)

@app.route('/settings')
def settings():
    # Load Configurations
    llm_config_path = os.path.join(CONFIG_DIR, 'llm_config.json')
    brand_config_path = os.path.join(CONFIG_DIR, 'brand_config.json')
    
    llm_config = {}
    if os.path.exists(llm_config_path):
        with open(llm_config_path, 'r') as f: llm_config = json.load(f)
            
    brand_config = {}
    if os.path.exists(brand_config_path):
        with open(brand_config_path, 'r') as f: brand_config = json.load(f)

    # Defaults
    llm_config.setdefault('default_buffer', getattr(CFG, 'BUFFER_ACTIVITY_SLIDE', 5.0))
    
    # Format Blacklist for Textarea
    if 'blacklist' in llm_config:
        val = llm_config['blacklist']
        display_str = ""
        if isinstance(val, dict):
            for k, v in val.items():
                display_str += f"{k}: {v}\n" if v else f"{k}\n"
        else:
            display_str = str(val)
        llm_config['blacklist_display'] = display_str.strip()

    return render_template('settings.html', active_page='settings', config=llm_config, brand_config=brand_config)

@app.route('/save-settings', methods=['POST'])
def save_settings():
    form_data = request.form.to_dict()
    
    # Process Blacklist (Text -> Dict)
    raw_text = form_data.get('blacklist', '')
    blacklist_dict = {}
    for line in raw_text.splitlines():
        if line.strip():
            parts = line.split(':', 1)
            key = parts[0].strip().lower()
            val = parts[1].strip() if len(parts) > 1 else ""
            blacklist_dict[key] = val

    # LLM Config Keys
    llm_keys = [
        'agent_1_provider', 'agent_2_provider', 'agent_3_provider',
        'gemini_api_key', 'openai_api_key', 'anthropic_api_key', 'groq_api_key', 'mistral_api_key',
        'aws_access_key', 'aws_secret_key', 'aws_region',
        'default_grade', 'max_words_per_slide', 'contrast_ratio', 'min_font_size',
        'wcag_strictness', 'default_buffer', 'check_spelling', 'check_grammar'
    ]
    llm_config = {k: form_data.get(k, '') for k in llm_keys}
    llm_config['blacklist'] = blacklist_dict

    # Brand Config Keys
    raw_headers = form_data.get('required_headers', '')
    headers_list = [h.strip() for h in raw_headers.splitlines() if h.strip()]
    
    raw_allowed = form_data.get('allowed_fonts', '')
    allowed_list = [x.strip() for x in raw_allowed.split(',') if x.strip()]

    brand_config = {
        'title_font': form_data.get('title_font'),
        'body_font': form_data.get('body_font'),
        'body_font_size': form_data.get('body_font_size'),
        'notes_font': form_data.get('notes_font'),
        'allowed_fonts': allowed_list,
        'required_headers': headers_list,
        'notes_scripting_level': form_data.get('notes_scripting_level'),
        'exempt_first_slide': form_data.get('exempt_first_slide') == 'on',
        'exempt_last_slide': form_data.get('exempt_last_slide') == 'on',
        'exempt_specific_slides': form_data.get('exempt_specific_slides', '')
    }

    try:
        with open(os.path.join(CONFIG_DIR, 'llm_config.json'), 'w') as f: 
            json.dump(llm_config, f, indent=4)
        with open(os.path.join(CONFIG_DIR, 'brand_config.json'), 'w') as f: 
            json.dump(brand_config, f, indent=4)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

# --- AUDITSLIDE MODULE ROUTES ---

@app.route('/projects')
def projects_page():
    """
    Lists all audits grouped by Project Name.
    """
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
                            p_name = summary.get('project_name') or summary.get('presentation_name', 'Unsorted')
                            
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
                        logger_service.log_system('warning', f"Error reading report {item}: {e}")

    # Sort files by date descending
    for p in projects: 
        projects[p].sort(key=lambda x: x['date'], reverse=True)

    return render_template('projects.html', projects=projects, active_page='projects')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No file selected"}), 400

    if file and file.filename.lower().endswith(('.pptx', '.ppt')):
        try:
            filename = secure_filename(file.filename)
            unique_id = str(uuid.uuid4())
            
            # Save Input
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(save_path)
            
            # Prepare Output
            audit_output_dir = os.path.join(app.config['OUTPUT_FOLDER'], unique_id)
            os.makedirs(audit_output_dir, exist_ok=True)
            
            # --- RUN THE CORE AUDIT TOOL ---
            logger_service.log_system('info', f"Starting audit for {filename} ({unique_id})")
            run_audit_slide(save_path, audit_output_dir)
            
            # Update Project Name in JSON if provided
            project_name = request.form.get('project_name')
            json_path = os.path.join(audit_output_dir, 'audit_report.json')
            
            if project_name and os.path.exists(json_path):
                with open(json_path, 'r') as f: data = json.load(f)
                data['summary']['project_name'] = project_name
                with open(json_path, 'w') as f: json.dump(data, f, indent=4)

            return jsonify({"status": "success", "session_id": unique_id})
            
        except Exception as e:
            logger_service.log_system('error', f"Audit failed: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500
            
    return jsonify({"status": "error", "message": "Invalid file type. Only .pptx allowed."}), 400

@app.route('/new-audit')
def new_audit():
    # Scan for existing projects to populate dropdown
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
    
    # Load defaults for form
    llm_config_path = os.path.join(CONFIG_DIR, 'llm_config.json')
    defaults = {}
    if os.path.exists(llm_config_path):
        with open(llm_config_path, 'r') as f: defaults = json.load(f)

    return render_template('index.html', active_page='projects', existing_projects=sorted(list(existing_projects)), defaults=defaults)

@app.route('/view-report/<report_id>')
def view_report(report_id):
    path, status = get_or_create_cached_report(report_id, 'report.html', 'Printable Executive Summary.html')
    if status != 200: return f"Error generating report: {status}", status
    return send_from_directory(os.path.dirname(path), os.path.basename(path))

@app.route('/view-workstation/<report_id>')
def view_workstation(report_id):
    """
    Renders the dynamic ID Workstation using the new template.
    """
    # 1. Locate the source JSON data
    json_path = os.path.join(app.config['OUTPUT_FOLDER'], report_id, 'audit_report.json')
    
    if not os.path.exists(json_path):
        return f"Error: Report data not found for ID {report_id}", 404

    # 2. Load data to pass to the template
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            audit_data = json.load(f)
    except Exception as e:
        return f"Error reading report JSON: {e}", 500

    # 3. Render the dynamic template
    return render_template('workstation.html', active_page='projects', audit_data=audit_data)

@app.route('/delete/<report_id>', methods=['POST'])
def delete_report(report_id):
    path = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
    if os.path.exists(path):
        shutil.rmtree(path)
        logger_service.log_system('info', f"Deleted report {report_id}")
        return jsonify({"status": "deleted"})
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
                            data = json.load(f)
                            p_name = data.get('summary', {}).get('project_name')
                            if p_name == target_project:
                                shutil.rmtree(item_path)
                                deleted_count += 1
                    except: pass
                    
    logger_service.log_system('info', f"Deleted project group '{target_project}' ({deleted_count} files)")
    return jsonify({"status": "success", "deleted_count": deleted_count})

@app.route('/reanalyze/<report_id>', methods=['POST'])
def reanalyze_deck(report_id):
    if 'file' not in request.files: return jsonify({"status": "error", "message": "No file uploaded"}), 400
    file = request.files['file']
    
    if file and file.filename.lower().endswith(('.pptx', '.ppt')):
        try:
            # Overwrite existing folder
            audit_output_dir = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{report_id}_{filename}")
            file.save(save_path)
            
            run_audit_slide(save_path, audit_output_dir)
            return jsonify({"status": "success", "message": "Re-analysis complete"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Invalid file type"}), 400

# --- FIX ENGINE & DOWNLOADS ---

@app.route('/apply-fix-batch', methods=['POST'])
def apply_fix_batch():
    data = request.json
    filename = data.get('filename')
    fixes = data.get('fixes')
    
    if not filename or not fixes: 
        return jsonify({"status": "error", "message": "Missing filename or fixes"}), 400

    # Locate file (Uploads or Reports folder)
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(input_path):
        # Fallback search in reports
        for root, _, files in os.walk(app.config['OUTPUT_FOLDER']):
            if filename in files:
                input_path = os.path.join(root, filename)
                break
    
    if not os.path.exists(input_path):
        return jsonify({"status": "error", "message": "Original file not found"}), 404

    try:
        engine = FixEngine()
        remediated_dir = os.path.join(app.config['OUTPUT_FOLDER'], 'remediated_decks')
        os.makedirs(remediated_dir, exist_ok=True)
        
        new_file_path = engine.apply_fixes(input_path, fixes, remediated_dir)
        
        if new_file_path:
            rel_name = os.path.basename(new_file_path)
            return jsonify({"status": "success", "download_url": f"/download-fixed/{rel_name}"})
        else:
            return jsonify({"status": "error", "message": "No changes applied"}), 400
            
    except Exception as e:
        logger_service.log_system('error', f"Fix Engine failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/download-fixed/<filename>')
def download_fixed(filename):
    directory = os.path.join(app.config['OUTPUT_FOLDER'], 'remediated_decks')
    return send_from_directory(directory, filename, as_attachment=True)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
