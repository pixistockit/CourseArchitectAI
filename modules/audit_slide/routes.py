# modules/audit_slide/routes.py

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

from flask import Blueprint, render_template, request, url_for, send_from_directory, jsonify, redirect, current_app, flash
from werkzeug.utils import secure_filename
from flask_login import login_required, current_user

# --- MODULE IMPORTS ---
from .qa_tool import run_audit_slide
from services.ai_engine import AIEngine
from .fix_engine import FixEngine
from .analyzer import PptxAnalyzer 
from . import config as CFG 

# --- DATABASE EXTENSIONS ---
from extensions import db
import models

# --- LOGGING ---
logger = logging.getLogger('platform_system')

# --- BLUEPRINT DEFINITION ---
audit_bp = Blueprint('audit_slide', __name__, template_folder='templates')

# ==========================================
# --- HELPER FUNCTIONS ---
# ==========================================

def get_paths():
    """
    Safely retrieves upload/output paths from config, defaulting if missing.
    Prevents KeyError crashes.
    """
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
    
    upload = current_app.config.get('UPLOAD_FOLDER', os.path.join(base_dir, 'data', 'uploads'))
    output = current_app.config.get('OUTPUT_FOLDER', os.path.join(base_dir, 'data', 'reports'))
    
    # Ensure they exist
    os.makedirs(upload, exist_ok=True)
    os.makedirs(output, exist_ok=True)
    
    return upload, output

def is_analysis_stale(report_dir):
    """Checks if the generated JSON report is older than the code/config."""
    json_path = os.path.join(report_dir, 'audit_report.json')
    if not os.path.exists(json_path): return True

    json_mtime = os.path.getmtime(json_path)
    base_dir = current_app.root_path
    config_dir = os.path.join(base_dir, 'data', 'config')
    module_path = os.path.dirname(os.path.abspath(__file__))
    
    dependencies = [
        os.path.join(module_path, 'analyzer.py'), 
        os.path.join(module_path, 'config.py'), 
        os.path.join(module_path, 'utils.py'),
        os.path.join(config_dir, 'llm_config.json'), 
        os.path.join(config_dir, 'brand_config.json')
    ]

    for dep in dependencies:
        if os.path.exists(dep):
            if os.path.getmtime(dep) > json_mtime:
                return True
    return False

def generate_cadence_log(audit_output_dir, slide_data):
    """Generates the 'AUDITSLIDE CADENCE & PACING LOG'."""
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
        
        duration_display = str(round(float(duration), 1))
        running_total_time += float(duration)
        
        notes = data.get('notes', '').strip()
        snippet = (notes[:45] + '...') if len(notes) > 45 else "(No Notes)"
        snippet = snippet.replace('\n', ' ').replace('\r', '')

        output_lines.append(row_fmt.format(str(slide_num), event_str[:40], duration_display, logic_type, snippet))

    output_lines.append("-" * 120)
    output_lines.append(f"CALCULATED TOTAL DURATION: {round(running_total_time, 1)} Minutes")
    output_lines.append("=" * 120)

    try:
        with open(log_file_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(output_lines))
        return True
    except Exception as e:
        logger.error(f"Error writing Cadence Log: {e}")
        return False

def get_or_create_cached_report(report_id, template_name, output_filename, force_rebuild=False):
    """Ensures static HTML reports are generated and up-to-date."""
    _, output_folder = get_paths()
    report_dir = os.path.join(output_folder, report_id)
    json_path = os.path.join(report_dir, 'audit_report.json')
    cached_path = os.path.join(report_dir, output_filename)
    template_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates', template_name)

    if not (os.path.exists(json_path) and os.path.exists(template_path)):
        return None, 404

    needs_rebuild = force_rebuild
    if not needs_rebuild:
        if not os.path.exists(cached_path):
            needs_rebuild = True
        else:
            try:
                cache_mtime = os.path.getmtime(cached_path)
                data_mtime = os.path.getmtime(json_path)
                template_mtime = os.path.getmtime(template_path)
                if data_mtime > cache_mtime or template_mtime > cache_mtime:
                    needs_rebuild = True
            except: needs_rebuild = True

    if needs_rebuild:
        try:
            with open(template_path, 'r', encoding='utf-8') as f: html_template = f.read()
            with open(json_path, 'r', encoding='utf-8') as f: full_data = json.load(f)
            
            json_str = json.dumps(full_data)
            final_html = html_template.replace('/* INSERT_JSON_HERE */', f"const auditData = {json_str};")
            
            with open(cached_path, 'w', encoding='utf-8') as f: f.write(final_html)
        except Exception as e:
            return None, 500

    return cached_path, 200

# ==========================================
# --- ROUTES ---
# ==========================================

@audit_bp.route('/projects')
@login_required
def projects_page():
    """
    Lists all audit projects for the current user.
    Loads primarily from Database for speed and security.
    """
    logger.info(f"Projects page accessed by {current_user.email}")
    
    # DB Load
    user_projects = models.Project.query.filter_by(user_id=current_user.id).order_by(models.Project.created_at.desc()).all()
    
    return render_template('projects.html', active_page='projects', projects=user_projects)

@audit_bp.route('/upload', methods=['POST'])
@login_required
def upload_file():
    """
    Handles file upload, runs analysis, and saves to DB.
    Includes GATEKEEPER logic to restrict Free users.
    """
    # 1. GATEKEEPER CHECK (SaaS Security)
    allowed_tiers = ['pro', 'enterprise']
    if current_user.subscription_tier not in allowed_tiers:
        logger.warning(f"Access Denied: User {current_user.email} (Tier: {current_user.subscription_tier}) tried to upload.")
        return jsonify({"status": "error", "message": "Access Restricted. Please upgrade to Pro or Enterprise."}), 403

    # 2. File Validation
    if 'file' not in request.files: return jsonify({"status": "error", "message": "No file uploaded"}), 400
    file = request.files['file']
    if file.filename == '': return jsonify({"status": "error", "message": "No file selected"}), 400

    if file and file.filename.lower().endswith(('.pptx', '.ppt')):
        try:
            filename = secure_filename(file.filename)
            unique_id = str(uuid.uuid4()) # Generate Project ID
            
            upload_folder, output_folder = get_paths()
            
            # Save Path
            save_path = os.path.join(upload_folder, filename)
            file.save(save_path)
            
            # Output Directory
            audit_output_dir = os.path.join(output_folder, unique_id)
            os.makedirs(audit_output_dir, exist_ok=True)
            
            logger.info(f"Starting audit for {filename} ({unique_id})")
            
            # 3. RUN ANALYSIS
            run_audit_slide(save_path, audit_output_dir)
            
            # 4. POST-PROCESSING
            project_name = request.form.get('project_name')
            json_path = os.path.join(audit_output_dir, 'audit_report.json')
            
            if os.path.exists(json_path):
                with open(json_path, 'r') as f: data = json.load(f)
                
                # Update JSON Metadata
                if project_name: data['summary']['project_name'] = project_name
                generate_cadence_log(audit_output_dir, data.get('slide_content', {}))
                with open(json_path, 'w') as f: json.dump(data, f, indent=4)

                # 5. SAVE TO DATABASE (Hybrid Persistence)
                try:
                    new_project = models.Project(
                        id=unique_id,
                        user_id=current_user.id,
                        project_name=project_name or data['summary']['presentation_name'],
                        module_type='audit_slide',
                        filename=filename,
                        file_path=save_path,
                        report_data=data, 
                        compliance_score=data.get('summary', {}).get('executive_metrics', {}).get('wcag_compliance_rate', 0),
                        total_issues=data.get('summary', {}).get('total_errors', 0)
                    )
                    db.session.add(new_project)
                    db.session.commit()
                    logger.info(f"Project {unique_id} synced to DB.")
                except Exception as db_e:
                    db.session.rollback()
                    logger.error(f"DB Write Failed (File saved OK): {db_e}")

            return jsonify({"status": "success", "session_id": unique_id})
        except Exception as e:
            logger.error(f"Audit failed: {e}")
            return jsonify({"status": "error", "message": str(e)}), 500
            
    return jsonify({"status": "error", "message": "Invalid file type. Only .pptx allowed."}), 400

@audit_bp.route('/new-audit')
@login_required
def new_audit():
    # Load defaults for the settings dropdown
    config_dir = os.path.join(current_app.root_path, 'data', 'config')
    llm_config_path = os.path.join(config_dir, 'llm_config.json')
    defaults = {}
    if os.path.exists(llm_config_path):
        with open(llm_config_path, 'r') as f: defaults = json.load(f)
        
    return render_template('new_audit.html', active_page='projects', defaults=defaults)

@audit_bp.route('/view-report/<report_id>')
@login_required
def view_report(report_id):
    # Verify ownership via DB to prevent Unauthorized Access
    project = models.Project.query.filter_by(id=report_id, user_id=current_user.id).first()
    if not project:
        flash('Project not found or access denied.', 'error')
        return redirect(url_for('audit_slide.projects_page'))

    upload_folder, output_folder = get_paths()
    report_dir = os.path.join(output_folder, report_id)
    json_path = os.path.join(report_dir, 'audit_report.json')
    
    # 1. Stale Logic Check & Auto-Update
    force_rebuild = False
    if os.path.exists(json_path) and is_analysis_stale(report_dir):
        logger.info(f"Report {report_id} is stale. Re-running logic...")
        try:
            with open(json_path, 'r') as f: old_data = json.load(f)
            filename = old_data.get('summary', {}).get('presentation_name')
            if filename:
                pptx_path = os.path.join(upload_folder, filename)
                # Fallback search if exact path missing
                if not os.path.exists(pptx_path):
                     matches = glob.glob(os.path.join(upload_folder, f"*{filename}"))
                     if matches: pptx_path = matches[0]
                
                if os.path.exists(pptx_path):
                    # Reload Analyzer Module to catch code changes
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
                    
                    # Update DB Record
                    project.report_data = new_data
                    db.session.commit()
        except Exception as e:
             logger.error(f"Auto-update failed: {e}")

    # 2. Render Cached HTML
    path, status = get_or_create_cached_report(report_id, 'report.html', 'Printable Executive Summary.html', force_rebuild=force_rebuild)
    if status != 200: return f"Error: {status}", status
    return send_from_directory(os.path.dirname(path), os.path.basename(path))

@audit_bp.route('/view-workstation/<report_id>')
@login_required
def view_workstation(report_id):
    project = models.Project.query.filter_by(id=report_id, user_id=current_user.id).first()
    if not project or not project.report_data:
        return "Error: Audit report not found or access denied.", 404
        
    return render_template('workstation.html', active_page='projects', audit_data=project.report_data)

@audit_bp.route('/delete/<report_id>', methods=['POST'])
@login_required
def delete_report(report_id):
    # Security: Ensure user owns this project
    project = models.Project.query.filter_by(id=report_id, user_id=current_user.id).first()
    if not project:
        return jsonify({"status": "error", "message": "Project not found or access denied"}), 404

    _, output_folder = get_paths()
    path = os.path.join(output_folder, report_id)
    
    # 1. Delete from File System
    if os.path.exists(path):
        shutil.rmtree(path)
        
    # 2. Delete from Database
    try:
        db.session.delete(project)
        db.session.commit()
        logger.info(f"Deleted report {report_id} from DB and Disk")
        return jsonify({"status": "deleted"})
    except Exception as e:
        db.session.rollback()
        logger.error(f"DB Deletion failed for {report_id}: {e}")
        return jsonify({"status": "error", "message": "DB Error"}), 500

@audit_bp.route('/delete-project-group', methods=['POST'])
@login_required
def delete_project_group():
    data = request.json
    target_project = data.get('project_name')
    if not target_project: 
        return jsonify({"status": "error", "message": "Missing project name"}), 400
    
    deleted_count = 0
    _, output_folder = get_paths()
    
    try:
        # Delete only projects owned by current user
        projects_to_delete = models.Project.query.filter_by(project_name=target_project, user_id=current_user.id).all()
        for proj in projects_to_delete:
            path = os.path.join(output_folder, proj.id)
            if os.path.exists(path):
                shutil.rmtree(path)
            
            db.session.delete(proj)
            deleted_count += 1
            
        db.session.commit()
        logger.info(f"Deleted project group '{target_project}' ({deleted_count} items)")
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Group deletion failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "success", "deleted_count": deleted_count})

@audit_bp.route('/reanalyze/<report_id>', methods=['POST'])
@login_required
def reanalyze_deck(report_id):
    # Security Check
    project = models.Project.query.filter_by(id=report_id, user_id=current_user.id).first()
    if not project: return jsonify({"status": "error", "message": "Access Denied"}), 403

    if 'file' not in request.files: return jsonify({"status": "error", "message": "No file uploaded"}), 400
    file = request.files['file']
    
    if file and file.filename.lower().endswith(('.pptx', '.ppt')):
        try:
            upload_folder, output_folder = get_paths()
            
            audit_output_dir = os.path.join(output_folder, report_id)
            filename = secure_filename(file.filename)
            save_path = os.path.join(upload_folder, f"{report_id}_{filename}")
            file.save(save_path)
            
            run_audit_slide(save_path, audit_output_dir)
            
            # Refresh Logs and DB
            json_path = os.path.join(audit_output_dir, 'audit_report.json')
            if os.path.exists(json_path):
                with open(json_path, 'r') as f: data = json.load(f)
                generate_cadence_log(audit_output_dir, data.get('slide_content', {}))
                
                # Update DB
                project.report_data = data
                db.session.commit()
            
            return jsonify({"status": "success", "message": "Re-analysis complete"})
        except Exception as e:
            return jsonify({"status": "error", "message": str(e)}), 500
    return jsonify({"status": "error", "message": "Invalid file type"}), 400

@audit_bp.route('/apply-fix-batch', methods=['POST'])
@login_required
def apply_fix_batch():
    data = request.json
    filename = data.get('filename')
    fixes = data.get('fixes')
    
    if not filename or not fixes: 
        return jsonify({"status": "error", "message": "Missing filename or fixes"}), 400

    upload_folder, output_folder = get_paths()
    
    input_path = os.path.join(upload_folder, filename)
    # Search fallback if not found directly
    if not os.path.exists(input_path):
        for root, _, files in os.walk(output_folder):
            if filename in files:
                input_path = os.path.join(root, filename)
                break
    
    if not os.path.exists(input_path):
        return jsonify({"status": "error", "message": "Original file not found"}), 404

    try:
        engine = FixEngine()
        remediated_dir = os.path.join(output_folder, 'remediated_decks')
        os.makedirs(remediated_dir, exist_ok=True)
        
        new_file_path = engine.apply_fixes(input_path, fixes, remediated_dir)
        
        if new_file_path:
            rel_name = os.path.basename(new_file_path)
            return jsonify({"status": "success", "download_url": f"/modules/audit_slide/download-fixed/{rel_name}"})
        else:
            return jsonify({"status": "error", "message": "No changes applied"}), 400
            
    except Exception as e:
        logger.error(f"Fix Engine failed: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@audit_bp.route('/download-fixed/<filename>')
@login_required
def download_fixed(filename):
    _, output_folder = get_paths()
    directory = os.path.join(output_folder, 'remediated_decks')
    return send_from_directory(directory, filename, as_attachment=True)

# --- AI ENDPOINTS ---

@audit_bp.route('/run-ai-batch', methods=['POST'])
@login_required
def run_ai_batch():
    """Endpoint for Batch AI Analysis (All Slides)."""
    try:
        data = request.json
        slides = data.get('slides', [])
        total_count = data.get('total_slides', 0)
        
        engine = AIEngine()
        results = engine.analyze_batch(slides, total_slide_count=total_count)
        
        return jsonify({"status": "success", "data": results})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@audit_bp.route('/run-ai-agent', methods=['POST'])
@login_required
def run_ai_agent():
    """Endpoint for Single Slide Analysis."""
    try:
        slide_data = request.json
        engine = AIEngine()
        results = engine.analyze_batch([slide_data], total_slide_count=0)
        
        if results:
            return jsonify({"status": "success", "data": results[0]})
        else:
            return jsonify({"status": "error", "message": "No data returned"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500
    
@audit_bp.route('/run-executive-summary/<report_id>', methods=['POST'])
@login_required
def run_executive_summary(report_id):
    # Verify Owner
    project = models.Project.query.filter_by(id=report_id, user_id=current_user.id).first()
    if not project: return jsonify({"status": "error", "message": "Access Denied"}), 403

    _, output_folder = get_paths()
    json_path = os.path.join(output_folder, report_id, 'audit_report.json')
    
    try:
        with open(json_path, 'r') as f: full_data = json.load(f)
        ai_engine = AIEngine()
        summary_text = ai_engine.generate_executive_summary(full_data['summary'], report_id)
        
        # Save back to JSON and DB
        full_data['executive_summary'] = summary_text
        with open(json_path, 'w') as f: json.dump(full_data, f, indent=4)
        
        project.report_data = full_data
        db.session.commit()
            
        return jsonify({"status": "success", "summary": summary_text})
        
    except Exception as e:
        logger.error(f"Failed to run exec summary: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500  

# --- SETTINGS ROUTES ---

@audit_bp.route('/settings')
@login_required
def settings():
    logger.info("Settings page accessed")
    config_dir = os.path.join(current_app.root_path, 'data', 'config')
    
    llm_config_path = os.path.join(config_dir, 'llm_config.json')
    brand_config_path = os.path.join(config_dir, 'brand_config.json')
    
    llm_config = {}
    brand_config = {}
    
    if os.path.exists(llm_config_path):
        with open(llm_config_path, 'r') as f: llm_config = json.load(f)
    if os.path.exists(brand_config_path):
        with open(brand_config_path, 'r') as f: brand_config = json.load(f)
    
    # Format Blacklist for Display
    llm_config.setdefault('default_buffer', getattr(CFG, 'BUFFER_ACTIVITY_SLIDE', 5.0))
    if 'blacklist' in llm_config:
        val = llm_config['blacklist']; display_str = ""
        if isinstance(val, dict):
            for k, v in val.items(): display_str += f"{k}:{v}\n" if v else f"{k}\n"
        else: display_str = str(val)
        llm_config['blacklist_display'] = display_str.strip()

    return render_template('settings.html', active_page='settings', config=llm_config, brand_config=brand_config)

@audit_bp.route('/save-settings', methods=['POST'])
@login_required
def save_settings():
    logger.info("Attempting to save settings")
    form_data = request.form.to_dict()
    
    # Process Blacklist
    raw_text = form_data.get('blacklist', '')
    blacklist_dict = {}
    for line in raw_text.splitlines():
        if line.strip():
            parts = line.split(':', 1)
            key = parts[0].strip().lower()
            val = parts[1].strip() if len(parts) > 1 else ""
            blacklist_dict[key] = val

    # LLM Settings
    llm_keys = [
        'agent_1_provider', 'agent_2_provider', 'agent_3_provider',
        'gemini_api_key', 'openai_api_key', 'anthropic_api_key', 'groq_api_key', 'mistral_api_key',
        'aws_access_key', 'aws_secret_key', 'aws_region',
        'default_grade', 'max_words_per_slide', 'contrast_ratio', 'min_font_size',
        'wcag_strictness', 'default_buffer', 'check_spelling', 'check_grammar'
    ]
    llm_config = {k: form_data.get(k, '') for k in llm_keys}
    llm_config['blacklist'] = blacklist_dict

    # Brand Settings
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
    
    config_dir = os.path.join(current_app.root_path, 'data', 'config')
    try:
        with open(os.path.join(config_dir, 'llm_config.json'), 'w') as f: 
            json.dump(llm_config, f, indent=4)
        with open(os.path.join(config_dir, 'brand_config.json'), 'w') as f: 
            json.dump(brand_config, f, indent=4)
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@audit_bp.route('/api/update-settings', methods=['POST'])
@login_required
def update_llm_settings():
    new_settings = request.get_json()
    config_dir = os.path.join(current_app.root_path, 'data', 'config')
    config_path = os.path.join(config_dir, 'llm_config.json')
    try:
        with open(config_path, 'r') as f: current_config = json.load(f)
        current_config.update(new_settings)
        with open(config_path, 'w') as f: json.dump(current_config, f, indent=4)
        return jsonify({"status": "success", "message": "Settings updated"})
    except Exception as e: return jsonify({"status": "error", "message": str(e)}), 500
