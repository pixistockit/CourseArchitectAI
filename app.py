# /app.py - MASTER PLATFORM CONTROLLER
# Architecture: Modular Blueprint System with Hybrid Database/File Storage

import os
import re
import csv
import logging
from flask import Flask, render_template, request
from jinja2 import ChoiceLoader, FileSystemLoader
from flask_login import login_required, current_user

# --- CORE EXTENSIONS ---
from extensions import db, migrate, login_manager

# --- BLUEPRINTS ---
from modules.auth import auth_bp
from modules.audit_slide.routes import audit_bp 

# --- SERVICES ---
from services.logger_service import LoggerService
import models

# --- CONFIGURATION ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join('data', 'uploads')
OUTPUT_FOLDER = os.path.join('data', 'reports')
LOG_FOLDER = os.path.join('data', 'logs')
CONFIG_DIR = os.path.join('data', 'config')

# Ensure critical directories exist
for folder in [UPLOAD_FOLDER, OUTPUT_FOLDER, LOG_FOLDER, CONFIG_DIR]: 
    os.makedirs(folder, exist_ok=True)

app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-change-in-prod')

# --- TEMPLATE CONFIGURATION ---
# Allows templates to be loaded from both the platform shell and specific modules
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

# --- DATABASE CONFIGURATION ---
# Connects to PostgreSQL (Docker or Prod)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'postgresql://audit_user:secure_pass_123@db:5432/audit_db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# --- INITIALIZE SERVICES ---
db.init_app(app)
migrate.init_app(app, db)
login_manager.init_app(app)
login_manager.login_view = 'auth.login' # Automatic redirect for protected routes

# Initialize System Logger
logger_service = LoggerService(base_data_path='data')

# --- REGISTER BLUEPRINTS ---
# 1. Authentication (Login/Register)
app.register_blueprint(auth_bp)

# 2. Tools (AuditSlide AI)
app.register_blueprint(audit_bp)

# --- USER SESSION LOADER ---
@login_manager.user_loader
def load_user(user_id):
    return models.User.query.get(int(user_id))

# --- MASTER DASHBOARD ROUTE ---
@app.route('/')
@login_required
def index():
    """
    The main landing page for logged-in users.
    Aggregates high-level metrics from all tools.
    """
    logger_service.log_system('info', 'Admin dashboard accessed', ip=request.remote_addr)
    
    # KPI Logic
    total_audits = 0
    all_scores = []
    
    # Note: In future, replace file scan with: total_audits = models.Project.query.count()
    if os.path.exists(OUTPUT_FOLDER):
        for item in os.listdir(OUTPUT_FOLDER):
            if os.path.isdir(os.path.join(OUTPUT_FOLDER, item)):
                json_path = os.path.join(OUTPUT_FOLDER, item, 'audit_report.json')
                if os.path.exists(json_path):
                    total_audits += 1
                    try:
                        # Extract score for average calculation
                        import json
                        with open(json_path, 'r') as f: 
                            data = json.load(f)
                            score = data.get('summary', {}).get('executive_metrics', {}).get('wcag_compliance_rate', 0)
                            all_scores.append(score)
                    except: pass

    avg_score = round(sum(all_scores) / len(all_scores), 1) if all_scores else 0
    
    # Token Usage Calculation
    total_tokens = 0
    token_ledger_path = os.path.join(LOG_FOLDER, 'token_ledger.csv')
    if os.path.exists(token_ledger_path):
        try:
            with open(token_ledger_path, 'r') as f:
                reader = csv.reader(f); next(reader, None)
                for row in reader: 
                    if len(row) >= 6: total_tokens += int(row[4]) + int(row[5])
        except: pass

    kpi_data = {
        'total_audits': total_audits, 
        'avg_compliance_score': avg_score, 
        'tokens_consumed_monthly': total_tokens, 
        'active_users': 1 # Placeholder for SaaS scaling
    }
    
    # System Log Tail
    system_logs = []
    log_path = os.path.join(LOG_FOLDER, 'platform_system.log')
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r') as f: lines = f.readlines()[-10:] 
            for line in reversed(lines):
                match = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*? - (\w+) - (.*)", line)
                if match:
                    system_logs.append({
                        'timestamp': match.group(1).split(' ')[1], 
                        'level': match.group(2), 
                        'message': match.group(3).strip()
                    })
        except: pass

    return render_template('dashboard.html', active_page='dashboard', kpis=kpi_data, system_logs=system_logs)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
