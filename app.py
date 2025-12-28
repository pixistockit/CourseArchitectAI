# /app.py - MASTER PLATFORM CONTROLLER (DB ENABLED)

import os
import glob
import uuid
import json
import shutil
import csv
import logging
import importlib
import re
from datetime import datetime
from flask import Flask, render_template, request, url_for, send_from_directory, jsonify, redirect
from werkzeug.utils import secure_filename
from jinja2 import ChoiceLoader, FileSystemLoader

from flask_login import login_required, current_user
from extensions import db, migrate, login_manager # Updated import
from modules.auth import auth_bp

# --- DATABASE & EXTENSIONS (NEW) ---
from extensions import db, migrate
import models # Registers the tables

# --- SERVICE & MODULE IMPORTS ---
from services.logger_service import LoggerService
from modules.audit_slide.qa_tool import run_audit_slide
from modules.audit_slide.ai_engine import AIEngine
from modules.audit_slide.fix_engine import FixEngine
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

# --- DATABASE CONFIGURATION (NEW) ---
# Connects to the local Docker DB or RDS based on env var
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://audit_user:secure_pass_123@db:5432/audit_db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- INITIALIZE SERVICES ---
db.init_app(app)
migrate.init_app(app, db)
login_manager.init_app(app)
login_manager.login_view = 'auth.login' # Redirect here if not logged in
app.register_blueprint(auth_bp)

@login_manager.user_loader
def load_user(user_id):
    return models.User.query.get(int(user_id))
logger_service = LoggerService(base_data_path='data')


# --- HELPER: SMART STALE CHECK ---
def is_analysis_stale(report_dir):
    json_path = os.path.join(report_dir, 'audit_report.json')
    if not os.path.exists(json_path): return True

    json_mtime = os.path.getmtime(json_path)
    dependencies = [
        'modules/audit_slide/analyzer.py', 'modules/audit_slide/config.py', 
        'modules/audit_slide/utils.py',
        os.path.join(CONFIG_DIR, 'llm_config.json'), os.path.join(CONFIG_DIR, 'brand_config.json')
    ]

    for dep in dependencies:
        if os.path.exists(dep):
            if os.path.getmtime(dep) > json_mtime:
                print(f"🔄 Auto-Update Triggered: {os.path.basename(dep)} is newer.")
                return True
    return False

# --- HELPER: CADENCE LOG GENERATOR ---
def generate_cadence_log(audit_output_dir, slide_data):
    log_dir = os.path.join(audit_output_dir, 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_file_path = os.path.join(log_dir, 'cadence_pacing.log')

    header_fmt = "{:<5} | {:<40} | {:<5} | {:<30} | {}"
    row_fmt    = "{:<5} | {:<40} | {:<5} | {:<30} | {}"
    
    output_lines = ["AUDITSLIDE CADENCE & PACING LOG", "=" * 120, header_fmt.format("SLIDE", "GAGNE EVENTS", "TIME", "LOGIC TYPE", "SNIPPET"), "-" * 120]
    running_total_time = 0

    items = slide_data.items() if isinstance(slide_data, dict) else enumerate(slide_data, 1)

    for slide_num, data in items:
        events = data.get('gagne_events', [])
        event_str = ", ".join(events) if events else "UNTAGGED"
        duration = data.get('calculated_duration', 0.5)
        logic_type = data.get('pacing_logic_type', "ESTIMATED (Fallback)")
        running_total_time += float(duration)
        notes = data.get('notes', '').strip()
        snippet = (notes[:45] + '...') if len(notes) > 45 else "(No Notes)"
        snippet = snippet.replace('\n', ' ').replace('\r', '')
        output_lines.append(row_fmt.format(str(slide_num), event_str[:40], str(round(float(duration), 1)), logic_type, snippet))

    output_lines.extend(["-" * 120, f"CALCULATED TOTAL DURATION: {round(running_total_time, 1)} Minutes", "=" * 120])

    try:
        with open(log_file_path, 'w', encoding='utf-8') as f: f.write('\n'.join(output_lines))
        return True
    except Exception as e:
        print(f"Error writing Cadence Log: {e}")
        return False

# --- HELPER: CACHE MANAGER ---
def get_or_create_cached_report(report_id, template_name, output_filename, force_rebuild=False):
    report_dir = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
    json_path = os.path.join(report_dir, 'audit_report.json')
    cached_path = os.path.join(report_dir, output_filename)
    template_path = os.path.join(module_template_dir, template_name)

    if not (os.path.exists(json_path) and os.path.exists(template_path)): return None, 404

    needs_rebuild = force_rebuild
    if not needs_rebuild:
        if not os.path.exists(cached_path): needs_rebuild = True
        else:
            try:
                cache_mtime = os.path.getmtime(cached_path)
                if os.path.getmtime(json_path) > cache_mtime or os.path.getmtime(template_path) > cache_mtime:
                    needs_rebuild = True
            except Exception as e:
                logger_service.log_system('warning', f"Cache timestamp check failed: {e}")
                needs_rebuild = True

    if needs_rebuild:
        try:
            with open(template_path, 'r', encoding='utf-8') as f: html_template = f.read()
            with open(json_path, 'r', encoding='utf-8') as f: full_data = json.load(f)
            json_str = json.dumps(full_data)
            final_html = html_template.replace('/* INSERT_JSON_HERE */', f"const auditData = {json_str};")
            with open(cached_path, 'w', encoding='utf-8') as f: f.write(final_html)
        except Exception as e:
            logger_service.log_system('error', f"Cache rebuild failed for {report_id}: {e}")
            return None, 500
    return cached_path, 200



# --- CORE PLATFORM ROUTES ---

@app.route('/')
def index():
    logger_service.log_system('info', 'Admin dashboard accessed', ip=request.remote_addr)
    total_audits, all_scores = 0, []
    
    # 1. Try DB First (Future Optimization)
    # count = models.Project.query.count()
    
    # 2. Fallback to File Scan (Current)
    if os.path.exists(OUTPUT_FOLDER):
        for item in os.listdir(OUTPUT_FOLDER):
            json_path = os.path.join(OUTPUT_FOLDER, item, 'audit_report.json')
            if os.path.exists(json_path):
                total_audits += 1
                try:
                    with open(json_path, 'r') as f: data = json.load(f)
                    all_scores.append(data.get('summary', {}).get('executive_metrics', {}).get('wcag_compliance_rate', 0))
                except: pass

    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0
    total_tokens = 0
    token_ledger_path = os.path.join(LOG_FOLDER, 'token_ledger.csv')
    if os.path.exists(token_ledger_path):
        try:
            with open(token_ledger_path, 'r', newline='') as f:
                reader = csv.reader(f); next(reader, None)
                for row in reader: 
                    if len(row) >= 6: total_tokens += int(row[4]) + int(row[5])
        except Exception: pass

    kpi_data = {'total_audits': total_audits, 'avg_compliance_score': avg_score, 'tokens_consumed_monthly': total_tokens, 'active_users': 12}
    
    system_logs = []
    log_path = os.path.join(LOG_FOLDER, 'platform_system.log')
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r') as f: lines = f.readlines()[-10:] 
            for line in reversed(lines):
                match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*? - (\w+) - (.*)", line)
                if match:
                    system_logs.append({'timestamp': match.group(1).split(' ')[1], 'level': match.group(2), 'message': match.group(3).strip()})
        except: pass

    return render_template('dashboard.html', active_page='dashboard', kpis=kpi_data, system_logs=system_logs)

@app.route('/projects')
def projects_page():
    logger_service.log_system('info', 'Projects page accessed', ip=request.remote_addr)
    projects = {} 
    
    # File Scan for Projects (Will replace with DB query later)
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
    if 'file' not in request.files: return jsonify({"status": "error", "message": "No file uploaded"}), 400
    file = request.files['file']
    if file.filename == '': return jsonify({"status": "error", "message": "No file selected"}), 400

    if file and file.filename.lower().endswith(('.pptx', '.ppt')):
        try:
            filename = secure_filename(file.filename)
            unique_id = str(uuid.uuid4())
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(save_path)
            
            audit_output_dir = os.path.join(app.config['OUTPUT_FOLDER'], unique_id)
            os.makedirs(audit_output_dir, exist_ok=True)
            
            logger_service.log_system('info', f"Starting audit for {filename} ({unique_id})")
            
            run_audit_slide(save_path, audit_output_dir)
            
            project_name = request.form.get('project_name')
            json_path = os.path.join(audit_output_dir, 'audit_report.json')
            
            # --- POST-PROCESSING & HYBRID SAVE ---
            if os.path.exists(json_path):
                with open(json_path, 'r') as f: data = json.load(f)
                
                # 1. Update JSON File
                if project_name: data['summary']['project_name'] = project_name
                generate_cadence_log(audit_output_dir, data.get('slide_content', {}))
                with open(json_path, 'w') as f: json.dump(data, f, indent=4)

                # 2. SAVE TO DATABASE (Hybrid Redundancy)
                try:
                    new_project = models.Project(
                        id=unique_id,
                        project_name=project_name or data['summary']['presentation_name'],
                        module_type='audit_slide',
                        filename=filename,
                        file_path=save_path,
                        report_data=data, # Storing the full analysis in DB
                        compliance_score=data.get('summary', {}).get('executive_metrics', {}).get('wcag_compliance_rate', 0),
                        total_issues=data.get('summary', {}).get('total_errors', 0)
                    )
                    db.session.add(new_project)
                    db.session.commit()
                    logger_service.log_system('info', f"Project {unique_id} synced to DB.")
                except Exception as db_e:
                    db.session.rollback()
                    logger_service.log_system('error', f"DB Write Failed (File saved OK): {db_e}")

            return jsonify({"status": "success", "session_id": unique_id})
        except Exception as e:
            logger_service.log_system('error', f"Audit failed: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Invalid file type. Only .pptx allowed."}), 400

@app.route('/new-audit')
def new_audit():
    existing_projects = set()
    # Hybrid: Read from DB or Files. For now, files is safer/easier.
    if os.path.exists(OUTPUT_FOLDER):
        for item in os.listdir(OUTPUT_FOLDER):
            json_path = os.path.join(OUTPUT_FOLDER, item, 'audit_report.json')
            if os.path.exists(json_path):
                try:
                    with open(json_path, 'r') as f: data = json.load(f)
                    p = data.get('summary', {}).get('project_name')
                    if p: existing_projects.add(p)
                except: pass
    
    llm_config_path = os.path.join(CONFIG_DIR, 'llm_config.json'); defaults = {}
    if os.path.exists(llm_config_path):
        with open(llm_config_path, 'r') as f: defaults = json.load(f)
    return render_template('new_audit.html', active_page='projects', existing_projects=sorted(list(existing_projects)), defaults=defaults)

@app.route('/view-report/<report_id>')
def view_report(report_id):
    report_dir = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
    json_path = os.path.join(report_dir, 'audit_report.json')
    
    # 1. Logic Stale Check
    force_rebuild = False
    if os.path.exists(json_path) and is_analysis_stale(report_dir):
        logger_service.log_system('info', f"Report {report_id} is stale. Re-running logic...")
        try:
            with open(json_path, 'r') as f: old_data = json.load(f)
            filename = old_data.get('summary', {}).get('presentation_name')
            if filename:
                pptx_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                if not os.path.exists(pptx_path):
                     matches = glob.glob(os.path.join(app.config['UPLOAD_FOLDER'], f"*{filename}"))
                     if matches: pptx_path = matches[0]
                if os.path.exists(pptx_path):
                    import modules.audit_slide.analyzer
                    importlib.reload(modules.audit_slide.analyzer)
                    from modules.audit_slide.analyzer import PptxAnalyzer
                    
                    analyzer = PptxAnalyzer(pptx_path)
                    hybrid_result = analyzer.run_analysis()
                    new_data = dict(hybrid_result)
                    
                    # Restore ID info
                    new_data['summary']['project_name'] = old_data.get('summary', {}).get('project_name')
                    with open(json_path, 'w') as f: json.dump(new_data, f, indent=4)
                    force_rebuild = True
        except Exception as e:
             logger_service.log_system('error', f"Auto-update failed: {e}")

    path, status = get_or_create_cached_report(report_id, 'report.html', 'Printable Executive Summary.html', force_rebuild=force_rebuild)
    if status != 200: return f"Error: {status}", status
    return send_from_directory(os.path.dirname(path), os.path.basename(path))

@app.route('/view-workstation/<report_id>')
def view_workstation(report_id):
    # Trigger stale check via logic in view_report if needed, or implement here.
    # For now, load JSON.
    json_path = os.path.join(app.config['OUTPUT_FOLDER'], report_id, 'audit_report.json')
    if not os.path.exists(json_path): return "Error: Audit report not found.", 404
    try:
        with open(json_path, 'r', encoding='utf-8') as f: audit_data = json.load(f)
    except Exception as e:
        return "Error: Could not load audit data.", 500
    return render_template('workstation.html', active_page='projects', audit_data=audit_data)

@app.route('/delete/<report_id>', methods=['POST'])
def delete_report(report_id):
    # 1. Delete from File System
    path = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
    if os.path.exists(path):
        shutil.rmtree(path)
        
    # 2. Delete from Database (NEW)
    try:
        project = models.Project.query.get(report_id)
        if project:
            db.session.delete(project)
            db.session.commit()
            logger_service.log_system('info', f"Deleted report {report_id} from DB and Disk")
            return jsonify({"status": "deleted"})
    except Exception as e:
        db.session.rollback()
        logger_service.log_system('error', f"DB Deletion failed for {report_id}: {e}")
        
    return jsonify({"status": "error", "message": "Report not found in DB or Disk"}), 404

@app.route('/delete-project-group', methods=['POST'])
def delete_project_group():
    data = request.json
    target_project = data.get('project_name')
    if not target_project: 
        return jsonify({"status": "error", "message": "Missing project name"}), 400
    
    deleted_count = 0
    
    # 1. Database Bulk Delete (Faster & Cleaner)
    try:
        projects_to_delete = models.Project.query.filter_by(project_name=target_project).all()
        for proj in projects_to_delete:
            # Delete the folder for this specific project ID
            path = os.path.join(app.config['OUTPUT_FOLDER'], proj.id)
            if os.path.exists(path):
                shutil.rmtree(path)
            
            # Delete the DB Record
            db.session.delete(proj)
            deleted_count += 1
            
        db.session.commit()
        logger_service.log_system('info', f"Deleted project group '{target_project}' ({deleted_count} items)")
        
    except Exception as e:
        db.session.rollback()
        logger_service.log_system('error', f"Group deletion failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "success", "deleted_count": deleted_count})

@app.route('/reanalyze/<report_id>', methods=['POST'])
def reanalyze_deck(report_id):
    if 'file' not in request.files: return jsonify({"status": "error", "message": "No file uploaded"}), 400
    file = request.files['file']
    if file and file.filename.lower().endswith(('.pptx', '.ppt')):
        try:
            audit_output_dir = os.path.join(app.config['OUTPUT_FOLDER'], report_id)
            filename = secure_filename(file.filename)
            save_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{report_id}_{filename}")
            file.save(save_path)
            run_audit_slide(save_path, audit_output_dir)
            
            # Post-Process
            json_path = os.path.join(audit_output_dir, 'audit_report.json')
            if os.path.exists(json_path):
                with open(json_path, 'r') as f: data = json.load(f)
                generate_cadence_log(audit_output_dir, data.get('slide_content', {}))
                
                # Update DB (Optional for re-analysis, but good practice)
                # project = models.Project.query.get(report_id)
                # if project: project.report_data = data; db.session.commit()
            
            return jsonify({"status": "success", "message": "Re-analysis complete"})
        except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Invalid file type"}), 400

@app.route('/settings')
def settings():
    llm_config_path = os.path.join(CONFIG_DIR, 'llm_config.json')
    brand_config_path = os.path.join(CONFIG_DIR, 'brand_config.json')
    llm_config = {}; brand_config = {}
    if os.path.exists(llm_config_path):
        with open(llm_config_path, 'r') as f: llm_config = json.load(f)
    if os.path.exists(brand_config_path):
        with open(brand_config_path, 'r') as f: brand_config = json.load(f)
    
    # Defaults
    llm_config.setdefault('default_buffer', getattr(CFG, 'BUFFER_ACTIVITY_SLIDE', 5.0))
    if 'blacklist' in llm_config:
        val = llm_config['blacklist']; display_str = ""
        if isinstance(val, dict):
            for k, v in val.items(): display_str += f"{k}:{v}\n" if v else f"{k}\n"
        else: display_str = str(val)
        llm_config['blacklist_display'] = display_str.strip()

    return render_template('settings.html', active_page='settings', config=llm_config, brand_config=brand_config)

@app.route('/save-settings', methods=['POST'])
def save_settings():
    form_data = request.form.to_dict()
    raw_text = form_data.get('blacklist', '')
    blacklist_dict = {parts[0].strip().lower(): (parts[1].strip() if len(parts) > 1 else "") for line in raw_text.splitlines() if line.strip() and (parts := line.split(':', 1))}

    llm_keys = ['agent_1_provider', 'agent_2_provider', 'agent_3_provider', 'gemini_api_key', 'openai_api_key', 'anthropic_api_key', 'groq_api_key', 'mistral_api_key', 'aws_access_key', 'aws_secret_key', 'aws_region', 'default_grade', 'max_words_per_slide', 'contrast_ratio', 'min_font_size', 'wcag_strictness', 'default_buffer', 'check_spelling', 'check_grammar']
    llm_config = {k: form_data.get(k, '') for k in llm_keys}; llm_config['blacklist'] = blacklist_dict 

    raw_headers = form_data.get('required_headers', ''); headers_list = [h.strip() for h in raw_headers.splitlines() if h.strip()]
    raw_allowed = form_data.get('allowed_fonts', ''); allowed_list = [x.strip() for x in raw_allowed.split(',') if x.strip()]
    
    brand_config = {
        'title_font': form_data.get('title_font'), 'body_font': form_data.get('body_font'), 'body_font_size': form_data.get('body_font_size'), 'notes_font': form_data.get('notes_font'),
        'allowed_fonts': allowed_list, 'required_headers': headers_list, 'notes_scripting_level': form_data.get('notes_scripting_level'),
        'exempt_first_slide': form_data.get('exempt_first_slide') == 'on', 'exempt_last_slide': form_data.get('exempt_last_slide') == 'on', 'exempt_specific_slides': form_data.get('exempt_specific_slides', '')
    }
    
    try:
        with open(os.path.join(CONFIG_DIR, 'llm_config.json'), 'w') as f: json.dump(llm_config, f, indent=4)
        with open(os.path.join(CONFIG_DIR, 'brand_config.json'), 'w') as f: json.dump(brand_config, f, indent=4)
        return jsonify({"status": "success"})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

# --- AI & FIX ROUTES ---
@app.route('/apply-fix-batch', methods=['POST'])
def apply_fix_batch():
    data = request.json; filename = data.get('filename'); fixes = data.get('fixes')
    if not filename or not fixes: return jsonify({"status": "error", "message": "Missing filename or fixes"}), 400
    input_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    if not os.path.exists(input_path):
        for root, _, files in os.walk(app.config['OUTPUT_FOLDER']):
            if filename in files: input_path = os.path.join(root, filename); break
    if not os.path.exists(input_path): return jsonify({"status": "error", "message": "Original file not found"}), 404
    try:
        engine = FixEngine(); remediated_dir = os.path.join(app.config['OUTPUT_FOLDER'], 'remediated_decks'); os.makedirs(remediated_dir, exist_ok=True)
        new_file_path = engine.apply_fixes(input_path, fixes, remediated_dir)
        if new_file_path: return jsonify({"status": "success", "download_url": f"/download-fixed/{os.path.basename(new_file_path)}"})
        return jsonify({"status": "error", "message": "No changes applied"}), 400
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/download-fixed/<filename>')
def download_fixed(filename):
    return send_from_directory(os.path.join(app.config['OUTPUT_FOLDER'], 'remediated_decks'), filename, as_attachment=True)

@app.route('/run-ai-batch', methods=['POST'])
def run_ai_batch():
    try:
        data = request.json; slides = data.get('slides', []); total_count = data.get('total_slides', 0)
        engine = AIEngine(); results = engine.analyze_batch(slides, total_slide_count=total_count)
        return jsonify({"status": "success", "data": results})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/run-ai-agent', methods=['POST'])
def run_ai_agent():
    try:
        slide_data = request.json; engine = AIEngine()
        results = engine.analyze_batch([slide_data], total_slide_count=0)
        return jsonify({"status": "success", "data": results[0]}) if results else jsonify({"status": "error", "message": "No data returned"}), 500
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/run-executive-summary/<report_id>', methods=['POST'])
def run_executive_summary(report_id):
    json_path = os.path.join(app.config['OUTPUT_FOLDER'], report_id, 'audit_report.json')
    if not os.path.exists(json_path): return jsonify({"status": "error", "message": "Report not found"}), 404
    try:
        with open(json_path, 'r') as f: full_data = json.load(f)
        ai_engine = AIEngine()
        summary_text = ai_engine.generate_executive_summary(full_data['summary'], report_id)
        full_data['executive_summary'] = summary_text
        with open(json_path, 'w') as f: json.dump(full_data, f, indent=4)
        return jsonify({"status": "success", "summary": summary_text})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/update-settings', methods=['POST'])
def update_llm_settings():
    new_settings = request.get_json()
    config_path = os.path.join(app.root_path, 'data/config/llm_config.json')
    try:
        with open(config_path, 'r') as f: current_config = json.load(f)
        current_config.update(new_settings)
        with open(config_path, 'w') as f: json.dump(current_config, f, indent=4)
        return jsonify({"status": "success", "message": "Settings updated"})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
