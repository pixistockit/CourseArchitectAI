# /models.py

from extensions import db
from datetime import datetime
from sqlalchemy.dialects.postgresql import JSONB
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash

# --- 1. USER MANAGEMENT ---
class User(UserMixin, db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    
    # --- SYSTEM ROLES (Platform Management) ---
    # 'super_admin': Full access to everything (You)
    # 'admin': Virtual Assistants / Support staff
    # 'subscriber': Regular customers (No admin access)
    role = db.Column(db.String(20), default='subscriber', nullable=False)

    # --- SUBSCRIPTION (Product Access) ---
    # We store the ID of the plan they are on
    plan_id = db.Column(db.Integer, db.ForeignKey('subscription_plans.id'), nullable=True)
    
    # Legacy field (Kept for backward compatibility/safety)
    subscription_tier = db.Column(db.String(20), default='free') 
    stripe_customer_id = db.Column(db.String(100), nullable=True)
    
    # User Profile (Company Name, API Keys, Preferences)
    meta_data = db.Column(JSONB, default={})
    
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relationships
    projects = db.relationship('Project', backref='owner', lazy=True)
    token_usage = db.relationship('TokenUsage', backref='user', lazy=True)
    
    # Relationship to the Plan (New in Phase 6)
    plan = db.relationship('SubscriptionPlan', backref='users')

    # --- SECURITY METHODS ---
    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)
    
    # --- HELPER: Check Role ---
    @property
    def is_super_admin(self):
        return self.role == 'super_admin'
    
    @property
    def is_admin(self):
        return self.role in ['admin', 'super_admin']

    def __repr__(self):
        return f'<User {self.email} (Role: {self.role}, Tier: {self.subscription_tier})>'

# --- 2. DYNAMIC SUBSCRIPTION PLANS (NEW) ---
class SubscriptionPlan(db.Model):
    __tablename__ = 'subscription_plans'
    
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), unique=True, nullable=False) # e.g. "Essentials", "Pro"
    slug = db.Column(db.String(50), unique=True, nullable=False) # e.g. "essentials", "pro"
    
    # Pricing (Stored in cents to avoid float math errors, or basic float for display)
    price_monthly = db.Column(db.Float, default=0.0)
    price_yearly = db.Column(db.Float, default=0.0)
    
    # Feature Flags (The "Switchboard" for tools)
    # Example: { "audit_slide": { "ai_enabled": true, "max_uploads": 50 } }
    features = db.Column(JSONB, default={})
    
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f'<Plan {self.name}>'

# --- 3. PROJECTS ---
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
    status = db.Column(db.String(20), default='completed') 
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# --- 4. TOKEN USAGE ---
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
