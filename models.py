# /models.py

from extensions import db
from datetime import datetime
from sqlalchemy.dialects.postgresql import JSONB
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# --- 1. USER MANAGEMENT (Updated for Phase 5) ---
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    
    # --- NEW: Subscription & Levels ---
    # default='free' ensures all new signups get the free tier automatically
    subscription_tier = db.Column(db.String(20), default='free') 
    stripe_customer_id = db.Column(db.String(100), nullable=True)
    
    # User Profile (Company Name, API Keys, Preferences)
    meta_data = db.Column(JSONB, default={})
    
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    projects = db.relationship('Project', backref='owner', lazy=True)
    token_usage = db.relationship('TokenUsage', backref='user', lazy=True)

    # --- SECURITY METHODS ---
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    # --- NEW: Permission Helper ---
    def has_access(self, required_tier):
        """
        Simple hierarchy: free < pro < enterprise
        """
        tiers = ['free', 'pro', 'enterprise']
        try:
            user_level = tiers.index(self.subscription_tier)
            required_level = tiers.index(required_tier)
            return user_level >= required_level
        except ValueError:
            return False

    def __repr__(self):
        return f'<User {self.email} ({self.subscription_tier})>'

# ... (Keep Project and TokenUsage classes exactly as they were) ...
# Copy/Paste the Project and TokenUsage classes from your previous file here
# so the file remains complete.
class Project(db.Model):
    __tablename__ = 'projects'
    id = db.Column(db.String(36), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    project_name = db.Column(db.String(200), nullable=False, default="Untitled Project")
    module_type = db.Column(db.String(50), default='audit_slide')
    filename = db.Column(db.String(255))
    file_path = db.Column(db.String(500)) 
    report_data = db.Column(JSONB, nullable=True)
    compliance_score = db.Column(db.Float, default=0.0)
    total_issues = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TokenUsage(db.Model):
    __tablename__ = 'token_usage'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    project_id = db.Column(db.String(36), db.ForeignKey('projects.id'), nullable=True)
    agent_role = db.Column(db.String(50)) 
    provider = db.Column(db.String(50))   
    model_name = db.Column(db.String(100))
    input_tokens = db.Column(db.Integer, default=0)
    output_tokens = db.Column(db.Integer, default=0)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
