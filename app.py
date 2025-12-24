# /app.py - MASTER PLATFORM CONTROLLER

import os
import glob
import uuid
import json
import shutil
import csv
import logging
import importlib
from datetime import datetime
from flask import Flask, render_template, request, url_for, send_from_directory, jsonify, redirect
from werkzeug.utils import secure_filename
from jinja2 import ChoiceLoader, FileSystemLoader

# --- SERVICE & MODULE IMPORTS ---
from services.logger_service import LoggerService
from modules.audit_slide.qa_tool import run_audit_slide
from modules.audit_slide.ai_engine import AIEngine
from modules.audit_slide.fix_engine import FixEngine
# NEW: Import Analyzer class directly for re-runs
from modules.audit_slide.analyzer import PptxAnalyzer 
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

# --- HELPER: SMART STALE CHECK ---
def is_analysis_stale(report_dir):
    """
    Checks if the generated JSON report is older than the code or configuration.
    If code/settings have changed, we return True to trigger a re-analysis.
    """
    json_path = os.path.join(report_dir, 'audit_report.json')
    if not os.path.exists(json_path):
        return True # Missing report = Stale

    json_mtime = os.path.getmtime(json_path)

    # List of critical files that affect the analysis logic
    dependencies = [
        'modules/audit_slide/analyzer.py',      # Core Logic
        'modules/audit_slide/config.py',        # Hardcoded Settings
        'modules/audit_slide/utils.py',         # Core Utils
        os.path.join(CONFIG_DIR, 'llm_config.json'),   # User Settings
        os.path.join(CONFIG_DIR, 'brand_config.json')  # Brand Settings
    ]

    for dep in dependencies:
        if os.path.exists(dep):
            dep_mtime = os.path.getmtime(dep)
            # If code is NEWER than report -> Report is Stale
            if dep_mtime > json_mtime:
                print(f"🔄 Auto-Update Triggered: {os.path.basename(dep)} is newer than report.")
                return True
    
    return False

# --- HELPER: CACHE MANAGER ---
def get_or_create_cached_report(report_id, template_name, output_filename, force_rebuild=False):
    """
    Ensures static HTML reports are generated and up-to-date.
    Checks if the template file has changed since the last build.
    """
    report_dir = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
    json_path = os.path.join(report_dir, 'audit_report.json')
    cached_path = os.path.join(report_dir, output_filename)
    template_path = os.path.join(module_template_dir, template_name)

    if not (os.path.exists(json_path) and os.path.exists(template_path)):
        return None, 404

    needs_rebuild = force_rebuild
    
    if not needs_rebuild:
        if not os.path.exists(cached_path):
            needs_rebuild = True
        else:
            try:
                # Check if template or data is newer than the HTML file
                cache_mtime = os.path.getmtime(cached_path)
                data_mtime = os.path.getmtime(json_path)
                template_mtime = os.path.getmtime(template_path)
                
                # Rebuild if data changed OR if the HTML template changed
                if data_mtime > cache_mtime or template_mtime > cache_mtime:
                    needs_rebuild = True
            except Exception as e:
                logger_service.log_system('warning', f"Cache timestamp check failed: {e}")
                needs_rebuild = True

    if needs_rebuild:
        try:
            with open(template_path, 'r', encoding='utf-8') as f: 
                html_template = f.read()
            with open(json_path, 'r', encoding='utf-8') as f: 
                full_data = json.load(f)
            
            # Serialize data for JS injection
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
    total_audits = 0
    all_scores = []
    
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
                except: pass

    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0
    
    total_tokens = 0
    token_ledger_path = os.path.join(LOG_FOLDER, 'token_ledger.csv')
    if os.path.exists(token_ledger_path):
        try:
            with open(token_ledger_path, 'r') as f:
                reader = csv.reader(f)
                next(reader, None) 
                for row in reader:
                    if len(row) >= 6:
                        total_tokens += int(row[4]) + int(row[5])
        except Exception: pass

    kpi_data = {
        'total_audits': total_audits,
        'avg_compliance_score': avg_score,
        'tokens_consumed_monthly': total_tokens,
        'active_users': 1 
    }

    system_logs = logger_service.get_recent_logs(limit=5)
    return render_template('dashboard.html', active_page='dashboard', kpis=kpi_data, system_logs=system_logs)

@app.route('/settings')
def settings():
    llm_config_path = os.path.join(CONFIG_DIR, 'llm_config.json')
    brand_config_path = os.path.join(CONFIG_DIR, 'brand_config.json')
    
    llm_config = {}
    if os.path.exists(llm_config_path):
        with open(llm_config_path, 'r') as f: llm_config = json.load(f)
            
    brand_config = {}
    if os.path.exists(brand_config_path):
        with open(brand_config_path, 'r') as f: brand_config = json.load(f)

    llm_config.setdefault('default_buffer', getattr(CFG, 'BUFFER_ACTIVITY_SLIDE', 5.0))
    
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
    
    raw_text = form_data.get('blacklist', '')
    blacklist_dict = {}
    for line in raw_text.splitlines():
        if line.strip():
            parts = line.split(':', 1)
            key = parts[0].strip().lower()
            val = parts[1].strip() if len(parts) > 1 else ""
            blacklist_dict[key] = val

    llm_keys = [
        'agent_1_provider', 'agent_2_provider', 'agent_3_provider',
        'gemini_api_key', 'openai_api_key', 'anthropic_api_key', 'groq_api_key', 'mistral_api_key',
        'aws_access_key', 'aws_secret_key', 'aws_region',
        'default_grade', 'max_words_per_slide', 'contrast_ratio', 'min_font_size',
        'wcag_strictness', 'default_buffer', 'check_spelling', 'check_grammar'
    ]
    llm_config = {k: form_data.get(k, '') for k in llm_keys}
    llm_config['blacklist'] = blacklist_dict

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
            
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(save_path)
            
            audit_output_dir = os.path.join(app.config['OUTPUT_FOLDER'], unique_id)
            os.makedirs(audit_output_dir, exist_ok=True)
            
            logger_service.log_system('info', f"Starting audit for {filename} ({unique_id})")
            
            # --- RUN AUDIT ---
            run_audit_slide(save_path, audit_output_dir)
            
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

    return render_template('new_audit.html', active_page='projects', existing_projects=sorted(list(existing_projects)), defaults=defaults)

@app.route('/view-report/<report_id>')
def view_report(report_id):
    report_dir = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
    json_path = os.path.join(report_dir, 'audit_report.json')
    
    # --- AUTO-UPDATE CHECK ---
    # Checks if JSON is older than Config or Code
    # Also checks if the HTML template is newer than the generated report
    
    force_rebuild = False
    
    # 1. Logic Stale Check (Re-run Analyzer)
    if os.path.exists(json_path) and is_analysis_stale(report_dir):
        logger_service.log_system('info', f"Report {report_id} is stale. Re-running analysis logic...")
        try:
            # Load old data to find original filename
            with open(json_path, 'r') as f: 
                old_data = json.load(f)
                filename = old_data.get('summary', {}).get('presentation_name')
            
            # Find the PPTX file
            if filename:
                pptx_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                # Fallback search if exact path fails
                if not os.path.exists(pptx_path):
                    matches = glob.glob(os.path.join(app.config['UPLOAD_FOLDER'], f"*{filename}"))
                    if matches: pptx_path = matches[0]

                if os.path.exists(pptx_path):
                    # --- CRITICAL: RELOAD MODULES TO GET NEW CONFIG ---
                    import modules.audit_slide.analyzer
                    importlib.reload(modules.audit_slide.analyzer)
                    from modules.audit_slide.analyzer import PptxAnalyzer
                    
                    # Re-Run Analysis with new settings
                    analyzer = PptxAnalyzer(pptx_path)
                    
                    # Use the hybrid report object directly (it acts as a dict)
                    hybrid_result = analyzer.run_analysis()
                    
                    # Convert to standard dict for saving if needed, though hybrid works as dict
                    # We ensure we have the 'summary' key which hybrid report has
                    new_data = dict(hybrid_result)
                    
                    # Preserve Project Name & ID
                    new_data['summary']['project_name'] = old_data.get('summary', {}).get('project_name')
                    new_data['summary']['master_slide_count'] = old_data.get('summary', {}).get('master_slide_count', 1)

                    # Overwrite JSON
                    with open(json_path, 'w') as f: 
                        json.dump(new_data, f, indent=4)
                    
                    force_rebuild = True # Data changed, must rebuild HTML
        except Exception as e:
            logger_service.log_system('error', f"Failed to auto-update stale report: {e}")

    # 2. Template Stale Check (Re-gen HTML only) handled inside get_or_create...
    path, status = get_or_create_cached_report(report_id, 'report.html', 'Printable Executive Summary.html', force_rebuild=force_rebuild)
    
    if status != 200: return f"Error generating report: {status}", status
    return send_from_directory(os.path.dirname(path), os.path.basename(path))

@app.route('/view-workstation/<report_id>')
def view_workstation(report_id):
    # Similar stale check logic could be applied here if Workstation views depend on generated HTML
    # Currently it loads JSON directly via JS, so we just ensure the JSON is fresh via view_report logic or similar.
    # For now, we assume view-report is the entry point that triggers updates.
    
    # Trigger the update check by calling the logic directly (optional but safer)
    report_dir = os.path.join(app.
