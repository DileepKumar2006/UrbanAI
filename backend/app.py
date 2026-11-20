"""
AI Urban Management Platform - Main Flask Application
Handles citizen pothole and waste reports and their completion history
"""

from flask import Flask, jsonify, request, send_from_directory, send_file, abort
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, get_jwt, get_jwt_identity, jwt_required, verify_jwt_in_request
from datetime import datetime, timedelta, timezone
import csv
import os
import requests
import base64
import hashlib
import hmac
import html
import json
from getpass import getpass
from werkzeug.utils import secure_filename
from io import BytesIO
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import Index, or_, and_, text, func
from sqlalchemy.orm import selectinload, deferred
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from PIL import Image, UnidentifiedImageError
from ai.categories import COMPLAINT_CATEGORIES, LABELS, LEGACY_WASTE_CATEGORIES, issue_type_for_category, recommended_department
from ai.inference import ModelUnavailable, classify_image

dotenv_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=dotenv_path)

# Initialize Flask app
# The frontend build is served by the catch-all route below. Disable Flask's
# default /static route so it cannot shadow React's bundled /static assets.
app = Flask(__name__, static_folder=None)

# ============ CONFIGURATION ============
is_production = os.getenv('FLASK_ENV', '').lower() == 'production'
configured_database_url = os.getenv('DATABASE_URL')
if is_production and not configured_database_url:
    raise RuntimeError('DATABASE_URL must be configured in production.')
if is_production and not configured_database_url.startswith(('postgres://', 'postgresql://')):
    raise RuntimeError('Production DATABASE_URL must point to PostgreSQL.')
if configured_database_url and configured_database_url.startswith('postgres://'):
    configured_database_url = configured_database_url.replace('postgres://', 'postgresql://', 1)
app.config['SQLALCHEMY_DATABASE_URI'] = configured_database_url or f"sqlite:///{Path(__file__).resolve().parent / 'urban_ai.db'}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
configured_jwt_secret = os.getenv('JWT_SECRET_KEY')
if is_production and (not configured_jwt_secret or configured_jwt_secret.lower() in {'change-me', 'change-me-to-a-long-random-secret', 'your-secret-key-change-in-production'}):
    raise RuntimeError('JWT_SECRET_KEY must be configured in production.')
app.config['JWT_SECRET_KEY'] = configured_jwt_secret or os.urandom(32)
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(hours=8)
app.config['MAX_CONTENT_LENGTH'] = 12 * 1024 * 1024

# Initialize extensions
db = SQLAlchemy(app)
jwt = JWTManager(app)
from flask_migrate import Migrate
migrate = Migrate(app, db, compare_type=True)
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'),
)
configured_origins = [origin.strip() for origin in os.getenv('CORS_ORIGINS', 'http://localhost:3000,http://127.0.0.1:3000').split(',') if origin.strip()]
CORS(app, resources={r"/api/*": {"origins": configured_origins}})

def validate_image_upload(file):
    if not file or not file.filename:
        raise ValueError('An image file is required.')
    if file.mimetype not in {'image/jpeg', 'image/png', 'image/webp'}:
        raise ValueError('Please upload a JPEG, PNG, or WebP image.')
    filename = secure_filename(file.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.jpg', '.jpeg', '.png', '.webp'}:
        raise ValueError('Unsupported image format.')
    content = file.read()
    try:
        with Image.open(BytesIO(content)) as image:
            actual_format = image.format
            if image.width * image.height > 40_000_000:
                raise ValueError('Image dimensions are too large.')
            image.verify()
    except ValueError:
        raise
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        raise ValueError('The uploaded file is not a valid image.')
    actual_types = {'JPEG': ('image/jpeg', {'.jpg', '.jpeg'}), 'PNG': ('image/png', {'.png'}), 'WEBP': ('image/webp', {'.webp'})}
    actual_type = actual_types.get(actual_format)
    if not actual_type or actual_type[0] != file.mimetype or suffix not in actual_type[1]:
        raise ValueError('Image contents do not match the selected file type.')
    if not content:
        raise ValueError('The uploaded image is empty.')
    return content, file.mimetype, filename


def save_uploaded_image(file):
    if not file or not file.filename:
        return ''
    content, mimetype, original_name = validate_image_upload(file)
    filename = f'{datetime.utcnow().strftime("%Y%m%d%H%M%S%f")}-{original_name}'
    db.session.add(UploadedImage(filename=filename, content=content, mimetype=mimetype))
    return f'/api/uploads/{filename}'


def accept_existing_image_url(value):
    if not isinstance(value, str) or not value.startswith('/api/uploads/'):
        return ''
    filename = value.removeprefix('/api/uploads/')
    if not filename or secure_filename(filename) != filename or '/' in filename or '\\' in filename:
        return ''
    return value if db.session.get(UploadedImage, filename) else ''

def require_admin():
    expected = os.getenv('URBANPULSE_ADMIN_KEY', '')
    supplied = request.headers.get('X-Admin-Key', '')
    if expected and hmac.compare_digest(supplied, expected):
        return True
    try:
        verify_jwt_in_request(optional=True)
        user = current_user()
        return bool(user and user.role == 'admin')
    except Exception:
        return False


def current_user():
    try:
        verify_jwt_in_request(optional=True)
        identity = get_jwt_identity()
        return db.session.get(User, int(identity)) if identity else None
    except Exception:
        return None


def can_access_issue(issue, user=None):
    user = user or current_user()
    if not user:
        return False
    if user.role == 'admin':
        return True
    if user.role in {'manager', 'department_officer', 'worker'}:
        return bool(user.department and issue.assigned_department and user.department.casefold() == issue.assigned_department.casefold())
    return user.role == 'citizen' and issue.created_by == user.id


def can_manage_issue(issue):
    if require_admin():
        return True
    user = current_user()
    return bool(user and user.role in {'manager', 'department_officer', 'worker'}
                and user.department and issue.assigned_department
                and user.department.casefold() == issue.assigned_department.casefold())


def can_manage_any_issue():
    if require_admin():
        return True
    user = current_user()
    return bool(user and user.role in {'manager', 'department_officer', 'worker'})


def audit_action(issue_id, action, details=''):
    user = current_user()
    db.session.add(AuditLog(actor_id=user.id if user else None, issue_id=issue_id,
                            action=action, details=details[:500]))


def image_token(issue, image_url):
    if not image_url or not image_url.startswith('/api/uploads/'):
        return image_url
    filename = image_url.removeprefix('/api/uploads/')
    token = URLSafeTimedSerializer(app.config['JWT_SECRET_KEY'], salt='urbanai-image-access').dumps({
        'issue_id': issue.id, 'filename': filename,
    })
    return f'{image_url}?access={token}'


def analysis_dict(analysis):
    if not analysis:
        return None
    return {
        'predicted_category': analysis.predicted_category,
        'confidence_score': analysis.confidence_score,
        'confirmed_category': analysis.confirmed_category,
        'reviewed_category': analysis.reviewed_category,
        'model_version': analysis.model_version,
        'processing_status': analysis.processing_status,
        'review_required': analysis.review_required,
        'error_message': analysis.error_message,
        'reviewed_at': analysis.reviewed_at.isoformat() if analysis.reviewed_at else None,
    }


def issue_dict_for_request(issue):
    """Return private details only to an admin, owner, or assigned officer."""
    payload = issue.to_dict()
    payload['recommended_department'] = recommended_department(issue.confirmed_category)
    latest_analysis = issue.ai_analyses[-1] if issue.ai_analyses else None
    payload['ai_analysis'] = analysis_dict(latest_analysis)
    user = current_user()
    authorized = require_admin() or can_access_issue(issue, user)
    if authorized:
        payload['image_url'] = image_token(issue, issue.image_url)
        payload['completion_image_url'] = image_token(issue, issue.completion_image_url)
    else:
        payload.pop('latitude', None)
        payload.pop('longitude', None)
        payload.pop('geotag_address', None)
        payload.pop('description', None)
        payload.pop('assigned_to', None)
        payload.pop('assigned_manager', None)
        payload.pop('assigned_department', None)
        payload.pop('created_by', None)
        payload.pop('updates', None)
        payload.pop('escalation_level', None)
        payload.pop('escalation_status', None)
        payload.pop('social_media_posted', None)
        payload.pop('social_media_url', None)
        payload.pop('ai_analysis', None)
        payload.pop('image_url', None)
        payload.pop('completion_image_url', None)
        payload['area_name'] = issue.area_name or 'Location withheld'
    return payload

# ============ DATABASE MODELS ============

class User(db.Model):
    """User model for authentication"""
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(20), default='citizen')  # admin, manager, citizen, worker
    department = db.Column(db.String(50))  # pothole_repair, waste_collection
    phone = db.Column(db.String(20))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<User {self.username}>'


class Issue(db.Model):
    """Main issue model for all problem reports"""
    __tablename__ = 'issues'
    
    id = db.Column(db.Integer, primary_key=True)
    issue_type = db.Column(db.String(50), nullable=False)  # pothole, waste
    title = db.Column(db.String(200), nullable=False)
    confirmed_category = db.Column(db.String(100))
    description = db.Column(db.Text)
    
    # Location data
    latitude = db.Column(db.Float, nullable=False)
    longitude = db.Column(db.Float, nullable=False)
    geotag_address = db.Column(db.String(255))
    area_name = db.Column(db.String(100))
    
    # Severity and status
    severity = db.Column(db.Integer, default=5)  # 1-10 scale
    status = db.Column(db.String(50), default='pending')  # pending, assigned, in_progress, completed
    
    # Images
    image_url = db.Column(db.String(255))
    completion_image_url = db.Column(db.String(255))
    
    # Assignment
    assigned_to = db.Column(db.Integer, db.ForeignKey('users.id'))
    assigned_at = db.Column(db.DateTime)
    assigned_department = db.Column(db.String(120))
    
    # Escalation
    escalation_level = db.Column(db.Integer, default=0)  # 0-5
    escalation_status = db.Column(db.String(50), default='none')  # none, notified, escalated, social_media
    escalation_date = db.Column(db.DateTime)
    social_media_posted = db.Column(db.Boolean, default=False)
    social_media_url = db.Column(db.String(255))
    
    # Timestamps
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    completed_at = db.Column(db.DateTime)
    
    # Relationships
    assigned_manager = db.relationship('User', foreign_keys=[assigned_to], backref='assigned_issues')
    creator = db.relationship('User', foreign_keys=[created_by], backref='created_issues')
    escalations = db.relationship('EscalationLog', backref='issue', lazy=True, cascade='all, delete-orphan')
    updates = db.relationship('IssueUpdate', backref='issue', lazy=True, cascade='all, delete-orphan')
    ai_analyses = db.relationship('AIAnalysis', back_populates='issue', lazy=True,
                                  order_by='AIAnalysis.created_at', cascade='all, delete-orphan')
    __table_args__ = (
        Index('ix_issues_status_created_at', 'status', 'created_at'),
        Index('ix_issues_type_status', 'issue_type', 'status'),
        Index('ix_issues_department_status', 'assigned_department', 'status'),
        Index('ix_issues_creator_created_at', 'created_by', 'created_at'),
    )
    
    def to_dict(self):
        return {
            'id': self.id,
            'issue_type': self.issue_type,
            'title': self.title,
            'description': self.description,
            'category': self.confirmed_category or (self.title if self.issue_type in {'waste', 'civic'} else 'Pothole / Road Damage'),
            'confirmed_category': self.confirmed_category,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'geotag_address': self.geotag_address,
            'severity': self.severity,
            'status': self.status,
            'image_url': self.image_url,
            'completion_image_url': self.completion_image_url,
            'assigned_to': self.assigned_to,
            'assigned_department': self.assigned_department,
            'escalation_level': self.escalation_level,
            'escalation_status': self.escalation_status,
            'social_media_posted': self.social_media_posted,
            'created_at': self.created_at.isoformat(),
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'created_by': self.created_by,
            'assigned_manager': self.assigned_manager.username if self.assigned_manager else None
            ,'updates': [{'text': u.update_text, 'image_url': u.image_url, 'timestamp': u.timestamp.isoformat()} for u in sorted(self.updates, key=lambda item: item.timestamp)]
        }
    
    def __repr__(self):
        return f'<Issue {self.id} - {self.issue_type}>'


class EscalationLog(db.Model):
    """Track escalation history for issues"""
    __tablename__ = 'escalation_logs'
    
    id = db.Column(db.Integer, primary_key=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=False)
    escalation_level = db.Column(db.Integer)
    action_taken = db.Column(db.Text)
    escalation_type = db.Column(db.String(50))  # manager_notification, social_media, admin_alert
    notification_sent = db.Column(db.Boolean, default=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    def __repr__(self):
        return f'<EscalationLog {self.issue_id}>'


class IssueUpdate(db.Model):
    """Track updates/progress on issues"""
    __tablename__ = 'issue_updates'
    
    id = db.Column(db.Integer, primary_key=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    update_text = db.Column(db.Text)
    image_url = db.Column(db.String(255))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    
    updater = db.relationship('User', backref='issue_updates')


class NotificationQueue(db.Model):
    """Queue for manager notifications"""
    __tablename__ = 'notification_queue'
    
    id = db.Column(db.Integer, primary_key=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('issues.id'))
    recipient_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    notification_type = db.Column(db.String(50))  # new_issue, escalation, deadline_warning
    message = db.Column(db.Text)
    is_sent = db.Column(db.Boolean, default=False)
    sent_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Escalation tracking
    time_limit_hours = db.Column(db.Integer, default=24)
    deadline = db.Column(db.DateTime)
    escalation_triggered = db.Column(db.Boolean, default=False)
    
    recipient = db.relationship('User', backref='notifications')

class UploadedImage(db.Model):
    __tablename__ = 'uploaded_images'
    filename = db.Column(db.String(255), primary_key=True)
    content = db.Column(db.LargeBinary, nullable=False)
    mimetype = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class EmailDelivery(db.Model):
    __tablename__ = 'email_deliveries'
    id = db.Column(db.Integer, primary_key=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=False)
    recipient_email = db.Column(db.String(320), nullable=False)
    email_type = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(30), nullable=False, default='pending')
    provider_email_id = db.Column(db.String(120))
    idempotency_key = db.Column(db.String(64), nullable=False)
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    last_attempt_at = db.Column(db.DateTime)
    accepted_at = db.Column(db.DateTime)
    delivered_at = db.Column(db.DateTime)
    error = db.Column(db.String(1000))
    __table_args__ = (
        db.UniqueConstraint('issue_id', 'recipient_email', 'email_type', name='uq_email_issue_recipient_type'),
    )

class EmailWebhookReceipt(db.Model):
    __tablename__ = 'email_webhook_receipts'
    id = db.Column(db.Integer, primary_key=True)
    provider_event_id = db.Column(db.String(160), nullable=False, unique=True)
    delivery_id = db.Column(db.Integer, db.ForeignKey('email_deliveries.id'), nullable=False)
    event_type = db.Column(db.String(60), nullable=False)
    received_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class RevokedToken(db.Model):
    __tablename__ = 'revoked_tokens'
    jti = db.Column(db.String(36), primary_key=True)
    expires_at = db.Column(db.DateTime, nullable=False)
    revoked_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)


class AIAnalysis(db.Model):
    __tablename__ = 'ai_analyses'
    id = db.Column(db.Integer, primary_key=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=False, index=True)
    image_url = db.Column(db.String(255), nullable=False)
    image_sha256 = db.Column(db.String(64), nullable=False)
    predicted_category = db.Column(db.String(100))
    confidence_score = db.Column(db.Float)
    confirmed_category = db.Column(db.String(100))
    reviewed_category = db.Column(db.String(100))
    model_version = db.Column(db.String(120))
    processing_status = db.Column(db.String(30), nullable=False, default='pending')
    review_required = db.Column(db.Boolean, nullable=False, default=False)
    error_message = db.Column(db.String(500))
    reviewed_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    reviewed_at = db.Column(db.DateTime)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)
    issue = db.relationship('Issue', back_populates='ai_analyses')
    reviewer = db.relationship('User', foreign_keys=[reviewed_by])


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=True, index=True)
    action = db.Column(db.String(80), nullable=False)
    details = db.Column(db.Text, nullable=False, default='')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)


class RoadSection(db.Model):
    """A maintained road segment; separate from citizen complaint locations."""
    __tablename__ = 'road_sections'
    id = db.Column(db.Integer, primary_key=True)
    section_code = db.Column(db.String(64), nullable=False, unique=True, index=True)
    road_name = db.Column(db.String(200), nullable=False)
    ward_zone = db.Column(db.String(120))
    road_type = db.Column(db.String(80))
    length_meters = db.Column(db.Float)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    notes = db.Column(db.String(2000))
    source = db.Column(db.String(255))
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    conditions = db.relationship('RoadConditionObservation', backref='road_section', lazy=True, cascade='all, delete-orphan')
    inspections = db.relationship('RoadInspection', backref='road_section', lazy=True, cascade='all, delete-orphan')
    weather_observations = db.relationship('RoadWeatherObservation', backref='road_section', lazy=True, cascade='all, delete-orphan')
    traffic_observations = db.relationship('RoadTrafficObservation', backref='road_section', lazy=True, cascade='all, delete-orphan')
    maintenance_records = db.relationship('RoadMaintenanceRecord', backref='road_section', lazy=True, cascade='all, delete-orphan')
    images = db.relationship('RoadImage', backref='road_section', lazy=True, cascade='all, delete-orphan')
    __table_args__ = (
        db.CheckConstraint('length_meters IS NULL OR length_meters >= 0', name='ck_road_sections_length_nonnegative'),
        db.CheckConstraint('(latitude IS NULL AND longitude IS NULL) OR (latitude IS NOT NULL AND longitude IS NOT NULL)',
                           name='ck_road_sections_coordinate_pair'),
    )


class RoadConditionObservation(db.Model):
    __tablename__ = 'road_condition_observations'
    id = db.Column(db.Integer, primary_key=True)
    road_section_id = db.Column(db.Integer, db.ForeignKey('road_sections.id'), nullable=False, index=True)
    observed_at = db.Column(db.DateTime, nullable=False, index=True)
    condition = db.Column(db.String(48), nullable=False)
    severity = db.Column(db.Integer)
    details = db.Column(db.String(2000))
    source = db.Column(db.String(255), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        db.CheckConstraint("condition IN ('healthy','minor_cracking','alligator_cracking','surface_wear','edge_deterioration','potholes','other')",
                           name='ck_road_condition_category'),
        db.CheckConstraint('severity IS NULL OR severity BETWEEN 1 AND 5', name='ck_road_condition_severity'),
    )


class RoadInspection(db.Model):
    __tablename__ = 'road_inspections'
    id = db.Column(db.Integer, primary_key=True)
    road_section_id = db.Column(db.Integer, db.ForeignKey('road_sections.id'), nullable=False, index=True)
    inspected_at = db.Column(db.DateTime, nullable=False, index=True)
    inspector = db.Column(db.String(160))
    findings = db.Column(db.String(2000), nullable=False)
    recommended_action = db.Column(db.String(1000))
    source = db.Column(db.String(255), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class RoadWeatherObservation(db.Model):
    __tablename__ = 'road_weather_observations'
    id = db.Column(db.Integer, primary_key=True)
    road_section_id = db.Column(db.Integer, db.ForeignKey('road_sections.id'), nullable=False, index=True)
    observed_at = db.Column(db.DateTime, nullable=False, index=True)
    rainfall_mm = db.Column(db.Float)
    temperature_c = db.Column(db.Float)
    humidity_percent = db.Column(db.Float)
    source = db.Column(db.String(255), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        db.CheckConstraint('rainfall_mm IS NULL OR rainfall_mm >= 0', name='ck_road_weather_rainfall_nonnegative'),
        db.CheckConstraint('humidity_percent IS NULL OR humidity_percent BETWEEN 0 AND 100', name='ck_road_weather_humidity_range'),
    )


class RoadTrafficObservation(db.Model):
    __tablename__ = 'road_traffic_observations'
    id = db.Column(db.Integer, primary_key=True)
    road_section_id = db.Column(db.Integer, db.ForeignKey('road_sections.id'), nullable=False, index=True)
    observed_at = db.Column(db.DateTime, nullable=False, index=True)
    interval_minutes = db.Column(db.Integer)
    vehicle_count = db.Column(db.Integer)
    average_daily_traffic = db.Column(db.Integer)
    heavy_vehicle_proportion = db.Column(db.Float)
    peak_hour_vehicles = db.Column(db.Integer)
    intensity_category = db.Column(db.String(24))
    source = db.Column(db.String(255), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        db.CheckConstraint('interval_minutes IS NULL OR interval_minutes BETWEEN 1 AND 1440', name='ck_road_traffic_interval'),
        db.CheckConstraint('vehicle_count IS NULL OR vehicle_count >= 0', name='ck_road_traffic_count_nonnegative'),
        db.CheckConstraint('average_daily_traffic IS NULL OR average_daily_traffic >= 0', name='ck_road_traffic_adt_nonnegative'),
        db.CheckConstraint('peak_hour_vehicles IS NULL OR peak_hour_vehicles >= 0', name='ck_road_traffic_peak_nonnegative'),
        db.CheckConstraint('heavy_vehicle_proportion IS NULL OR heavy_vehicle_proportion BETWEEN 0 AND 1', name='ck_road_traffic_heavy_ratio'),
        db.CheckConstraint("intensity_category IS NULL OR intensity_category IN ('low','moderate','heavy')", name='ck_road_traffic_intensity'),
    )


class RoadMaintenanceRecord(db.Model):
    __tablename__ = 'road_maintenance_records'
    id = db.Column(db.Integer, primary_key=True)
    road_section_id = db.Column(db.Integer, db.ForeignKey('road_sections.id'), nullable=False, index=True)
    maintenance_date = db.Column(db.DateTime, nullable=False, index=True)
    repair_type = db.Column(db.String(160), nullable=False)
    cost = db.Column(db.Float)
    notes = db.Column(db.String(2000))
    source = db.Column(db.String(255), nullable=False)
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    __table_args__ = (
        db.CheckConstraint('cost IS NULL OR cost >= 0', name='ck_road_maintenance_cost_nonnegative'),
    )


class RoadImage(db.Model):
    __tablename__ = 'road_images'
    id = db.Column(db.Integer, primary_key=True)
    road_section_id = db.Column(db.Integer, db.ForeignKey('road_sections.id'), nullable=False, index=True)
    filename = db.Column(db.String(255), nullable=False)
    original_name = db.Column(db.String(255), nullable=False)
    mimetype = db.Column(db.String(80), nullable=False)
    content = deferred(db.Column(db.LargeBinary, nullable=False))
    captured_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    source = db.Column(db.String(255), nullable=False)
    license_name = db.Column(db.String(160))
    license_url = db.Column(db.String(500))
    attribution = db.Column(db.String(500))
    created_by = db.Column(db.Integer, db.ForeignKey('users.id'))
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


ROAD_CONDITIONS = {'healthy', 'minor_cracking', 'alligator_cracking', 'surface_wear', 'edge_deterioration', 'potholes', 'other'}
TRAFFIC_INTENSITIES = {'low', 'moderate', 'heavy'}


def road_data_authorized():
    user = current_user()
    return require_admin() or bool(user and user.role in {'manager', 'department_officer'} and user.department)


def parse_road_datetime(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'{field} is required.')
    try:
        result = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
    except ValueError:
        raise ValueError(f'{field} must be an ISO date and time.')
    if result.tzinfo:
        result = result.astimezone(timezone.utc).replace(tzinfo=None)
    return result


def optional_road_number(value, field, minimum=None, maximum=None, integer=False):
    if value in (None, ''):
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        parsed = float(value)
        if integer and not parsed.is_integer():
            raise ValueError
        number = int(parsed) if integer else parsed
    except (TypeError, ValueError):
        raise ValueError(f'{field} must be a valid number.')
    if isinstance(number, float) and (number != number or number in (float('inf'), float('-inf'))):
        raise ValueError(f'{field} must be a finite number.')
    if minimum is not None and number < minimum or maximum is not None and number > maximum:
        raise ValueError(f'{field} must be between {minimum} and {maximum}.')
    return number


def road_section_dict(section, detail=False):
    result = {
        'id': section.id, 'section_code': section.section_code, 'road_name': section.road_name,
        'ward_zone': section.ward_zone, 'road_type': section.road_type,
        'length_meters': section.length_meters, 'latitude': section.latitude,
        'longitude': section.longitude, 'notes': section.notes, 'source': section.source,
        'created_at': section.created_at.isoformat(), 'updated_at': section.updated_at.isoformat(),
        'record_counts': {
            'conditions': len(section.conditions), 'inspections': len(section.inspections),
            'weather': len(section.weather_observations), 'traffic': len(section.traffic_observations),
            'maintenance': len(section.maintenance_records), 'images': len(section.images),
        },
    }
    if detail:
        result.update({
            'conditions': [{'id': row.id, 'observed_at': row.observed_at.isoformat(), 'condition': row.condition,
                            'severity': row.severity, 'details': row.details, 'source': row.source}
                           for row in sorted(section.conditions, key=lambda item: item.observed_at, reverse=True)],
            'inspections': [{'id': row.id, 'inspected_at': row.inspected_at.isoformat(), 'inspector': row.inspector,
                             'findings': row.findings, 'recommended_action': row.recommended_action, 'source': row.source}
                            for row in sorted(section.inspections, key=lambda item: item.inspected_at, reverse=True)],
            'weather_observations': [{'id': row.id, 'observed_at': row.observed_at.isoformat(), 'rainfall_mm': row.rainfall_mm,
                                      'temperature_c': row.temperature_c, 'humidity_percent': row.humidity_percent, 'source': row.source}
                                     for row in sorted(section.weather_observations, key=lambda item: item.observed_at, reverse=True)],
            'traffic_observations': [{'id': row.id, 'observed_at': row.observed_at.isoformat(), 'interval_minutes': row.interval_minutes,
                                      'vehicle_count': row.vehicle_count, 'average_daily_traffic': row.average_daily_traffic,
                                      'heavy_vehicle_proportion': row.heavy_vehicle_proportion, 'peak_hour_vehicles': row.peak_hour_vehicles,
                                      'intensity_category': row.intensity_category, 'source': row.source}
                                     for row in sorted(section.traffic_observations, key=lambda item: item.observed_at, reverse=True)],
            'maintenance_records': [{'id': row.id, 'maintenance_date': row.maintenance_date.isoformat(), 'repair_type': row.repair_type,
                                     'cost': row.cost, 'notes': row.notes, 'source': row.source}
                                    for row in sorted(section.maintenance_records, key=lambda item: item.maintenance_date, reverse=True)],
            'images': [{'id': row.id, 'original_name': row.original_name, 'mimetype': row.mimetype,
                        'captured_at': row.captured_at.isoformat(), 'source': row.source, 'license_name': row.license_name,
                        'license_url': row.license_url, 'attribution': row.attribution,
                        'image_url': road_image_url(row)}
                       for row in sorted(section.images, key=lambda item: item.captured_at, reverse=True)],
        })
    return result


def road_image_url(image):
    access = URLSafeTimedSerializer(app.config['JWT_SECRET_KEY'], salt='urbanai-road-image-access').dumps({
        'section_id': image.road_section_id, 'image_id': image.id,
    })
    return f'/api/road-sections/{image.road_section_id}/images/{image.id}?access={access}'


def get_road_section_or_404(section_id):
    return db.session.get(RoadSection, section_id)


@app.route('/api/road-sections', methods=['GET'])
@limiter.limit('120 per minute')
def list_road_sections():
    if not road_data_authorized():
        return jsonify({'success': False, 'error': 'Administrator or department officer access is required.'}), 403
    try:
        limit = min(max(int(request.args.get('limit', 200)), 1), 500)
        offset = max(int(request.args.get('offset', 0)), 0)
    except ValueError:
        return jsonify({'success': False, 'error': 'Limit and offset must be whole numbers.'}), 400
    query = RoadSection.query.order_by(RoadSection.section_code.asc())
    total = query.count()
    sections = query.offset(offset).limit(limit).all()
    return jsonify({'success': True, 'items': [road_section_dict(row) for row in sections], 'total': total})


@app.route('/api/road-sections', methods=['POST'])
@limiter.limit('60 per hour')
def create_road_section():
    if not road_data_authorized():
        return jsonify({'success': False, 'error': 'Administrator or department officer access is required.'}), 403
    data = request.get_json(silent=True) or {}
    code = str(data.get('section_code') or '').strip()
    name = str(data.get('road_name') or '').strip()
    if not code or len(code) > 64 or not name or len(name) > 200:
        return jsonify({'success': False, 'error': 'Section ID (1–64 characters) and road name (1–200 characters) are required.'}), 400
    if RoadSection.query.filter_by(section_code=code).first():
        return jsonify({'success': False, 'error': 'A road section with this ID already exists.'}), 409
    try:
        length = optional_road_number(data.get('length_meters'), 'Length in meters', 0)
        latitude = optional_road_number(data.get('latitude'), 'Latitude', -90, 90)
        longitude = optional_road_number(data.get('longitude'), 'Longitude', -180, 180)
        if (latitude is None) != (longitude is None):
            raise ValueError('Latitude and longitude must be supplied together.')
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 400
    user = current_user()
    section = RoadSection(section_code=code, road_name=name, ward_zone=(data.get('ward_zone') or '').strip()[:120] or None,
                          road_type=(data.get('road_type') or '').strip()[:80] or None, length_meters=length,
                          latitude=latitude, longitude=longitude, notes=(data.get('notes') or '').strip()[:2000] or None,
                          source=(data.get('source') or '').strip()[:255] or None,
                          created_by=user.id if user else None)
    db.session.add(section)
    try:
        db.session.flush()
        audit_action(None, 'road_section_created', f'{section.section_code}: {section.road_name}')
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return jsonify({'success': True, 'item': road_section_dict(section)}), 201


@app.route('/api/road-sections/import', methods=['POST'])
@limiter.limit('10 per hour')
def import_road_sections_csv():
    if not road_data_authorized():
        return jsonify({'success': False, 'error': 'Administrator or department officer access is required.'}), 403
    uploaded = request.files.get('file')
    if not uploaded or not uploaded.filename.lower().endswith('.csv'):
        return jsonify({'success': False, 'error': 'Choose a CSV file.'}), 400
    raw = uploaded.read(2_000_001)
    if len(raw) > 2_000_000:
        return jsonify({'success': False, 'error': 'CSV files are limited to 2 MB.'}), 413
    try:
        text_data = raw.decode('utf-8-sig')
        reader = csv.DictReader(text_data.splitlines())
        required = {'section_code', 'road_name'}
        if not reader.fieldnames or not required.issubset({name.strip() for name in reader.fieldnames}):
            raise ValueError('CSV must include section_code and road_name columns.')
        allowed = {'section_code', 'road_name', 'ward_zone', 'road_type', 'length_meters', 'latitude', 'longitude', 'notes', 'source'}
        if not set(name.strip() for name in reader.fieldnames).issubset(allowed):
            raise ValueError('CSV contains unsupported columns.')
        rows = []
        seen = set()
        for row_number, row in enumerate(reader, start=2):
            if row_number > 5001:
                raise ValueError('A CSV import can contain at most 5,000 road sections.')
            normalized = {str(key or '').strip(): (value or '').strip() for key, value in row.items()}
            code, name = normalized.get('section_code', ''), normalized.get('road_name', '')
            if not code or len(code) > 64 or not name or len(name) > 200:
                raise ValueError(f'Row {row_number}: section_code and road_name are required and must fit the documented limits.')
            if code in seen or RoadSection.query.filter_by(section_code=code).first():
                raise ValueError(f'Row {row_number}: duplicate section_code {code}.')
            seen.add(code)
            length = optional_road_number(normalized.get('length_meters'), f'Row {row_number} length_meters', 0)
            latitude = optional_road_number(normalized.get('latitude'), f'Row {row_number} latitude', -90, 90)
            longitude = optional_road_number(normalized.get('longitude'), f'Row {row_number} longitude', -180, 180)
            if (latitude is None) != (longitude is None):
                raise ValueError(f'Row {row_number}: latitude and longitude must be supplied together.')
            rows.append(RoadSection(section_code=code, road_name=name,
                                    ward_zone=normalized.get('ward_zone', '')[:120] or None,
                                    road_type=normalized.get('road_type', '')[:80] or None,
                                    length_meters=length, latitude=latitude, longitude=longitude,
                                    notes=normalized.get('notes', '')[:2000] or None,
                                    source=normalized.get('source', '')[:255] or None,
                                    created_by=current_user().id if current_user() else None))
        if not rows:
            raise ValueError('CSV contains no road sections.')
    except (UnicodeDecodeError, csv.Error, ValueError) as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 400
    try:
        db.session.add_all(rows)
        db.session.flush()
        audit_action(None, 'road_sections_csv_imported', f'{len(rows)} sections imported')
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return jsonify({'success': True, 'created': len(rows), 'items': [road_section_dict(row) for row in rows]}), 201


@app.route('/api/road-sections/<int:section_id>', methods=['GET', 'PATCH'])
def road_section_detail(section_id):
    if not road_data_authorized():
        return jsonify({'success': False, 'error': 'Administrator or department officer access is required.'}), 403
    section = get_road_section_or_404(section_id)
    if not section:
        return jsonify({'success': False, 'error': 'Road section not found.'}), 404
    if request.method == 'GET':
        return jsonify({'success': True, 'item': road_section_dict(section, detail=True)})
    data = request.get_json(silent=True) or {}
    allowed = {'road_name', 'ward_zone', 'road_type', 'length_meters', 'latitude', 'longitude', 'notes', 'source'}
    if set(data) - allowed:
        return jsonify({'success': False, 'error': 'Unsupported road-section field.'}), 400
    try:
        for field, maximum in [('road_name', 200), ('ward_zone', 120), ('road_type', 80), ('notes', 2000), ('source', 255)]:
            if field in data:
                value = str(data[field] or '').strip()
                if field == 'road_name' and not value:
                    raise ValueError('Road name cannot be empty.')
                if len(value) > maximum:
                    raise ValueError(f'{field} exceeds {maximum} characters.')
                setattr(section, field, value or None)
        if 'length_meters' in data:
            section.length_meters = optional_road_number(data['length_meters'], 'Length in meters', 0)
        if 'latitude' in data:
            section.latitude = optional_road_number(data['latitude'], 'Latitude', -90, 90)
        if 'longitude' in data:
            section.longitude = optional_road_number(data['longitude'], 'Longitude', -180, 180)
        if (section.latitude is None) != (section.longitude is None):
            raise ValueError('Latitude and longitude must be supplied together.')
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 400
    audit_action(None, 'road_section_updated', section.section_code)
    db.session.commit()
    return jsonify({'success': True, 'item': road_section_dict(section)})


def road_record_context(section_id):
    if not road_data_authorized():
        return None, (jsonify({'success': False, 'error': 'Administrator or department officer access is required.'}), 403)
    section = get_road_section_or_404(section_id)
    if not section:
        return None, (jsonify({'success': False, 'error': 'Road section not found.'}), 404)
    return section, None


def road_record_source(data):
    source = str(data.get('source') or '').strip()
    if not source or len(source) > 255:
        raise ValueError('Data source is required and must be at most 255 characters.')
    return source


def road_record_author():
    user = current_user()
    return user.id if user else None


def commit_road_record(section, action, details):
    audit_action(None, action, f'{section.section_code}: {details}')
    db.session.commit()


@app.route('/api/road-sections/<int:section_id>/conditions', methods=['POST'])
def create_road_condition(section_id):
    section, error = road_record_context(section_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    try:
        condition = str(data.get('condition') or '').strip()
        if condition not in ROAD_CONDITIONS:
            raise ValueError('Choose a supported observed road condition.')
        severity = optional_road_number(data.get('severity'), 'Severity', 1, 5, integer=True)
        record = RoadConditionObservation(road_section=section, observed_at=parse_road_datetime(data.get('observed_at'), 'Observation date'),
                                          condition=condition, severity=severity,
                                          details=(data.get('details') or '').strip()[:2000] or None,
                                          source=road_record_source(data), created_by=road_record_author())
        db.session.add(record)
        commit_road_record(section, 'road_condition_recorded', condition)
    except ValueError as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 400
    return jsonify({'success': True, 'item': road_section_dict(section, detail=True)['conditions'][0]}), 201


@app.route('/api/road-sections/<int:section_id>/inspections', methods=['POST'])
def create_road_inspection(section_id):
    section, error = road_record_context(section_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    findings = str(data.get('findings') or '').strip()
    if not findings or len(findings) > 2000:
        return jsonify({'success': False, 'error': 'Inspection findings are required and must fit 2,000 characters.'}), 400
    try:
        record = RoadInspection(road_section=section, inspected_at=parse_road_datetime(data.get('inspected_at'), 'Inspection date'),
                                inspector=(data.get('inspector') or '').strip()[:160] or None,
                                findings=findings, recommended_action=(data.get('recommended_action') or '').strip()[:1000] or None,
                                source=road_record_source(data), created_by=road_record_author())
        db.session.add(record)
        commit_road_record(section, 'road_inspection_recorded', 'inspection recorded')
    except ValueError as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 400
    return jsonify({'success': True, 'item': road_section_dict(section, detail=True)['inspections'][0]}), 201


@app.route('/api/road-sections/<int:section_id>/weather', methods=['POST'])
def create_road_weather_observation(section_id):
    section, error = road_record_context(section_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    try:
        rainfall = optional_road_number(data.get('rainfall_mm'), 'Rainfall (mm)', 0)
        temperature = optional_road_number(data.get('temperature_c'), 'Temperature (°C)', -100, 100)
        humidity = optional_road_number(data.get('humidity_percent'), 'Humidity (%)', 0, 100)
        if all(value is None for value in (rainfall, temperature, humidity)):
            raise ValueError('Enter at least one measured weather value.')
        record = RoadWeatherObservation(road_section=section, observed_at=parse_road_datetime(data.get('observed_at'), 'Observation date'),
                                        rainfall_mm=rainfall, temperature_c=temperature, humidity_percent=humidity,
                                        source=road_record_source(data), created_by=road_record_author())
        db.session.add(record)
        commit_road_record(section, 'road_weather_recorded', 'weather observation recorded')
    except ValueError as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 400
    return jsonify({'success': True, 'item': road_section_dict(section, detail=True)['weather_observations'][0]}), 201


@app.route('/api/road-sections/<int:section_id>/traffic', methods=['POST'])
def create_road_traffic_observation(section_id):
    section, error = road_record_context(section_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    try:
        interval = optional_road_number(data.get('interval_minutes'), 'Count interval (minutes)', 1, 1440, integer=True)
        count = optional_road_number(data.get('vehicle_count'), 'Vehicle count', 0, integer=True)
        daily = optional_road_number(data.get('average_daily_traffic'), 'Average daily traffic', 0, integer=True)
        heavy = optional_road_number(data.get('heavy_vehicle_proportion'), 'Heavy-vehicle proportion', 0, 1)
        peak = optional_road_number(data.get('peak_hour_vehicles'), 'Peak-hour vehicles', 0, integer=True)
        intensity = str(data.get('intensity_category') or '').strip().lower() or None
        if intensity and intensity not in TRAFFIC_INTENSITIES:
            raise ValueError('Traffic intensity must be low, moderate, or heavy.')
        if all(value is None for value in (count, daily, heavy, peak, intensity)):
            raise ValueError('Enter at least one measured traffic value.')
        if count is not None and interval is None:
            raise ValueError('A count interval is required when entering a vehicle count.')
        record = RoadTrafficObservation(road_section=section, observed_at=parse_road_datetime(data.get('observed_at'), 'Observation date'),
                                       interval_minutes=interval, vehicle_count=count, average_daily_traffic=daily,
                                       heavy_vehicle_proportion=heavy, peak_hour_vehicles=peak, intensity_category=intensity,
                                       source=road_record_source(data), created_by=road_record_author())
        db.session.add(record)
        commit_road_record(section, 'road_traffic_recorded', 'traffic observation recorded')
    except ValueError as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 400
    return jsonify({'success': True, 'item': road_section_dict(section, detail=True)['traffic_observations'][0]}), 201


@app.route('/api/road-sections/<int:section_id>/maintenance', methods=['POST'])
def create_road_maintenance_record(section_id):
    section, error = road_record_context(section_id)
    if error:
        return error
    data = request.get_json(silent=True) or {}
    repair_type = str(data.get('repair_type') or '').strip()
    if not repair_type or len(repair_type) > 160:
        return jsonify({'success': False, 'error': 'Repair type is required and must fit 160 characters.'}), 400
    try:
        cost = optional_road_number(data.get('cost'), 'Maintenance cost', 0)
        record = RoadMaintenanceRecord(road_section=section, maintenance_date=parse_road_datetime(data.get('maintenance_date'), 'Maintenance date'),
                                       repair_type=repair_type, cost=cost, notes=(data.get('notes') or '').strip()[:2000] or None,
                                       source=road_record_source(data), created_by=road_record_author())
        db.session.add(record)
        commit_road_record(section, 'road_maintenance_recorded', repair_type)
    except ValueError as error:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(error)}), 400
    return jsonify({'success': True, 'item': road_section_dict(section, detail=True)['maintenance_records'][0]}), 201


@app.route('/api/road-sections/<int:section_id>/images', methods=['POST'])
@limiter.limit('20 per hour')
def create_road_image(section_id):
    section, error = road_record_context(section_id)
    if error:
        return error
    uploaded = request.files.get('image')
    try:
        content, mimetype, original_name = validate_image_upload(uploaded)
        source = road_record_source(request.form)
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 400
    try:
        captured_at = parse_road_datetime(request.form.get('captured_at') or datetime.utcnow().isoformat(), 'Capture date')
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 400
    image = RoadImage(road_section=section, filename=f'{datetime.utcnow().strftime("%Y%m%d%H%M%S%f")}-{original_name}',
                      original_name=original_name, mimetype=mimetype, content=content, captured_at=captured_at,
                      source=source, license_name=(request.form.get('license_name') or '').strip()[:160] or None,
                      license_url=(request.form.get('license_url') or '').strip()[:500] or None,
                      attribution=(request.form.get('attribution') or '').strip()[:500] or None,
                      created_by=road_record_author())
    db.session.add(image)
    try:
        db.session.flush()
        audit_action(None, 'road_image_uploaded', f'{section.section_code}: {source}')
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    return jsonify({'success': True, 'item': {'id': image.id, 'original_name': image.original_name,
                   'captured_at': image.captured_at.isoformat(), 'source': image.source,
                   'image_url': road_image_url(image)}}), 201


@app.route('/api/road-sections/<int:section_id>/images/<int:image_id>', methods=['GET'])
def get_road_image(section_id, image_id):
    image = RoadImage.query.filter_by(id=image_id, road_section_id=section_id).first()
    if not image:
        return jsonify({'success': False, 'error': 'Road image not found.'}), 404
    authorized = road_data_authorized()
    signed_access = request.args.get('access')
    if signed_access and not authorized:
        try:
            claims = URLSafeTimedSerializer(app.config['JWT_SECRET_KEY'], salt='urbanai-road-image-access').loads(signed_access, max_age=900)
            authorized = claims.get('section_id') == section_id and claims.get('image_id') == image_id
        except (BadSignature, SignatureExpired, TypeError, ValueError):
            authorized = False
    if not authorized:
        return jsonify({'success': False, 'error': 'Road image access is not authorized.'}), 403
    return send_file(BytesIO(image.content), mimetype=image.mimetype, download_name=image.original_name)


@jwt.token_in_blocklist_loader
def token_is_revoked(_jwt_header, jwt_payload):
    return db.session.get(RevokedToken, jwt_payload['jti']) is not None

EMAIL_EVENT_LABELS = {
    'created': 'New complaint',
    'assigned': 'Department assignment',
    'in_progress': 'Work in progress',
    'completed': 'Work completed',
}

def absolute_app_url(path=''):
    base = os.getenv('APP_BASE_URL', request.url_root).rstrip('/')
    return f'{base}/{path.lstrip("/")}' if path else base

def issue_dashboard_url(issue):
    return f'{absolute_app_url()}?report=UP-{issue.id:04d}'

def build_email_payload(issue, event, recipient):
    label = EMAIL_EVENT_LABELS[event]
    report_id = f'UP-{issue.id:04d}'
    location = issue.geotag_address or 'Not provided'
    category = issue.confirmed_category or issue.title
    status_label = issue.status.replace('_', ' ').title()
    submitted = issue.created_at.isoformat() if issue.created_at else 'Unknown'
    completed = issue.completed_at.isoformat() if issue.completed_at else ''
    description = html.escape(issue.description or '')
    url = html.escape(issue_dashboard_url(issue), quote=True)
    details = [
        f'<p><b>Complaint ID:</b> {report_id}</p>',
        f'<p><b>Category:</b> {html.escape(category)}</p>',
        f'<p><b>Location:</b> {html.escape(location)}</p>',
        f'<p><b>Description:</b> {description}</p>',
        f'<p><b>Submitted:</b> {html.escape(submitted)}</p>',
        f'<p><b>Status:</b> {html.escape(status_label)}</p>',
    ]
    if issue.assigned_department:
        details.append(f'<p><b>Department:</b> {html.escape(issue.assigned_department)}</p>')
    if event == 'completed':
        details.extend([
            f'<p><b>Completed:</b> {html.escape(completed)}</p>',
            f'<p><b>Completion details:</b> {html.escape(issue.updates[-1].update_text if issue.updates else "")}</p>',
        ])

    images = []
    attachments = []
    urls = [('Before photograph', issue.image_url)]
    if issue.completion_image_url:
        urls.append(('After photograph', issue.completion_image_url))
    for label_text, image_url in urls:
        if not image_url:
            continue
        filename = Path(image_url).name
        uploaded = db.session.get(UploadedImage, filename)
        if uploaded:
            attachments.append({
                'filename': filename,
                'content': base64.b64encode(uploaded.content).decode('ascii'),
                'content_type': uploaded.mimetype,
            })
        images.append(f'<p><b>{html.escape(label_text)}:</b> {html.escape(filename)} is attached to this email.</p>')

    subject = f'UrbanPulse {label}: {report_id} — {location}'
    body = (
        f'<h2>UrbanPulse — {html.escape(label)}</h2>'
        + ''.join(details)
        + ''.join(images)
        + f'<p><a href="{url}">View complaint in UrbanPulse</a></p>'
    )
    text_body = '\n'.join([
        f'UrbanPulse — {label}', f'Complaint ID: {report_id}', f'Category: {category}',
        f'Location: {location}', f'Description: {issue.description or ""}',
        f'Submitted: {submitted}', f'Status: {status_label}',
        *( [f'Completed: {completed}'] if completed else [] ),
        f'View complaint: {issue_dashboard_url(issue)}',
    ])
    return {
        'from': os.getenv('RESEND_FROM_EMAIL', 'UrbanPulse <onboarding@resend.dev>'),
        'to': [recipient], 'subject': subject, 'html': body, 'text': text_body,
        'attachments': attachments,
    }

def send_email_delivery(delivery, issue):
    api_key = os.getenv('RESEND_API_KEY', '').strip()
    sender = os.getenv('RESEND_FROM_EMAIL', '').strip()
    if not api_key or not sender:
        delivery.status = 'not_configured'
        missing = [name for name, value in [('RESEND_API_KEY', api_key), ('RESEND_FROM_EMAIL', sender)] if not value]
        delivery.error = f'{", ".join(missing)} is not configured.'
        db.session.commit()
        return

    payload = build_email_payload(issue, delivery.email_type, delivery.recipient_email)
    delivery.status = 'pending'
    delivery.error = None
    delivery.last_attempt_at = datetime.utcnow()
    delivery.attempt_count = (delivery.attempt_count or 0) + 1
    db.session.commit()
    try:
        response = requests.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
                'Idempotency-Key': delivery.idempotency_key,
            },
            json=payload,
            timeout=15,
        )
        if not response.ok:
            delivery.status = 'failed'
            delivery.error = f'Resend API returned HTTP {response.status_code}: {response.text[:700]}'
        else:
            result = response.json()
            provider_id = result.get('id')
            if not provider_id:
                delivery.status = 'unknown'
                delivery.error = 'Resend accepted an unexpected response without an email ID.'
            else:
                # Provider acceptance is not final recipient delivery.
                delivery.status = 'accepted'
                delivery.provider_email_id = provider_id
                delivery.accepted_at = datetime.utcnow()
    except requests.Timeout:
        delivery.status = 'unknown'
        delivery.error = 'Resend request timed out; delivery is unconfirmed. Check provider events before retrying.'
    except (requests.RequestException, ValueError) as error:
        delivery.status = 'unknown'
        delivery.error = f'Resend request outcome is unconfirmed: {str(error)[:700]}'
    db.session.commit()

def notify_issue_by_email(issue, event, recipient_list=None):
    if recipient_list is None:
        recipients = [os.getenv('ADMIN_EMAIL', 'dk142252@gmail.com').strip(), os.getenv('DEPARTMENT_EMAIL', '').strip()]
    else:
        recipients = recipient_list
    # Citizens who own a report receive the same persisted status events. Email
    # delivery remains best-effort and cannot affect the complaint transaction.
    if issue.creator and issue.creator.email:
        recipients = [*recipients, issue.creator.email]
    recipients = list(dict.fromkeys(address.lower() for address in recipients if address and address.strip()))
    for recipient in recipients:
        delivery = EmailDelivery.query.filter_by(issue_id=issue.id, recipient_email=recipient, email_type=event).first()
        if delivery:
            continue
        digest = hashlib.sha256(f'urbanpulse:{issue.id}:{event}:{recipient}'.encode()).hexdigest()
        delivery = EmailDelivery(
            issue_id=issue.id,
            recipient_email=recipient,
            email_type=event,
            status='pending',
            idempotency_key=digest,
        )
        db.session.add(delivery)
        db.session.commit()
        send_email_delivery(delivery, issue)

def verify_resend_webhook(raw_body):
    secret = os.getenv('RESEND_WEBHOOK_SECRET', '').strip()
    if not secret:
        return False
    msg_id = request.headers.get('svix-id', '')
    timestamp = request.headers.get('svix-timestamp', '')
    signatures = request.headers.get('svix-signature', '')
    try:
        if abs(datetime.utcnow().timestamp() - int(timestamp)) > 300:
            return False
        key = base64.b64decode(secret.removeprefix('whsec_'))
        signed = f'{msg_id}.{timestamp}.'.encode() + raw_body
        expected = base64.b64encode(hmac.new(key, signed, hashlib.sha256).digest()).decode()
        return any(
            hmac.compare_digest(signature, expected)
            for part in signatures.split()
            for version, separator, signature in [part.partition(',')]
            if separator and version == 'v1'
        )
    except (ValueError, TypeError):
        return False


# ============ ERROR HANDLERS ============

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Resource not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500


@app.errorhandler(413)
def request_too_large(_error):
    return jsonify({'success': False, 'error': 'Request exceeds the 12 MB limit.'}), 413


@app.errorhandler(429)
def rate_limit_exceeded(_error):
    return jsonify({'success': False, 'error': 'Too many requests. Please try again later.'}), 429


@app.after_request
def add_security_headers(response):
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-Frame-Options', 'DENY')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'camera=(self), geolocation=(self)')
    return response


@app.route('/api/auth/register', methods=['POST'])
@limiter.limit('5 per hour')
def register_citizen():
    data = request.get_json(silent=True) or {}
    username = str(data.get('username') or '').strip()
    email = str(data.get('email') or '').strip().lower()
    password = data.get('password')
    if len(username) < 3 or len(username) > 80 or len(email) > 120 or '@' not in email:
        return jsonify({'success': False, 'error': 'Provide a valid username and email address.'}), 400
    if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
        return jsonify({'success': False, 'error': 'Password must contain 12 to 256 characters.'}), 400
    if User.query.filter(or_(User.username == username, User.email == email)).first():
        return jsonify({'success': False, 'error': 'Username or email is already registered.'}), 409
    user = User(username=username, email=email, password_hash=generate_password_hash(password), role='citizen')
    db.session.add(user)
    db.session.commit()
    token = create_access_token(identity=str(user.id), additional_claims={'role': user.role})
    return jsonify({'success': True, 'access_token': token, 'user': {
        'id': user.id, 'username': user.username, 'role': user.role,
    }}), 201


@app.route('/api/auth/register-department', methods=['POST'])
@limiter.limit('5 per hour')
def register_department_user():
    if not require_admin():
        return jsonify({'success': False, 'error': 'Administrator authorization required.'}), 403
    data = request.get_json(silent=True) or {}
    username = str(data.get('username') or '').strip()
    email = str(data.get('email') or '').strip().lower()
    department = str(data.get('department') or '').strip()
    password = data.get('password')
    if not username or len(username) > 80 or '@' not in email or len(email) > 120 or not department or len(department) > 50:
        return jsonify({'success': False, 'error': 'Valid username, email, and department are required.'}), 400
    if not isinstance(password, str) or len(password) < 12 or len(password) > 256:
        return jsonify({'success': False, 'error': 'Password must contain 12 to 256 characters.'}), 400
    if User.query.filter(or_(User.username == username, User.email == email)).first():
        return jsonify({'success': False, 'error': 'Username or email is already registered.'}), 409
    user = User(username=username, email=email, password_hash=generate_password_hash(password),
                role='department_officer', department=department)
    db.session.add(user)
    db.session.commit()
    audit_action(None, 'department_user_created', f'{department}: {username}')
    db.session.commit()
    return jsonify({'success': True, 'user': {'id': user.id, 'username': user.username,
                                               'role': user.role, 'department': user.department}}), 201


@app.route('/api/auth/login', methods=['POST'])
@limiter.limit('10 per minute')
def login():
    data = request.get_json(silent=True) or {}
    identifier = str(data.get('username') or data.get('email') or '').strip()
    password = data.get('password')
    user = User.query.filter(or_(User.username == identifier, User.email == identifier)).first() if identifier else None
    try:
        password_matches = bool(user and isinstance(password, str) and check_password_hash(user.password_hash, password))
    except (ValueError, TypeError):
        password_matches = False
    if not password_matches:
        return jsonify({'success': False, 'error': 'Invalid username/email or password.'}), 401
    token = create_access_token(identity=str(user.id), additional_claims={'role': user.role})
    return jsonify({'success': True, 'access_token': token, 'user': {
        'id': user.id, 'username': user.username, 'role': user.role, 'department': user.department,
    }})


@app.route('/api/auth/logout', methods=['POST'])
@jwt_required()
def logout():
    claims = get_jwt()
    db.session.add(RevokedToken(
        jti=claims['jti'],
        expires_at=datetime.utcfromtimestamp(claims['exp']),
    ))
    db.session.commit()
    return jsonify({'success': True, 'message': 'Signed out.'})


# ============ HEALTH CHECK ============

@app.route('/api/health', methods=['GET'])
def health_check():
    optional_services = {
        'resend': 'configured' if all(os.getenv(name) for name in ('RESEND_API_KEY', 'RESEND_FROM_EMAIL', 'APP_BASE_URL')) else 'not_configured',
        'resend_webhook': 'configured' if os.getenv('RESEND_WEBHOOK_SECRET') else 'not_configured',
    }
    try:
        db.session.execute(text('SELECT 1'))
    except Exception:
        db.session.rollback()
        return jsonify({'status': 'unhealthy', 'timestamp': datetime.utcnow().isoformat(),
                        'database': 'unavailable', 'optional_services': optional_services}), 503
    return jsonify({'status': 'healthy', 'timestamp': datetime.utcnow().isoformat(),
                    'database': 'connected', 'optional_services': optional_services})


@app.route('/api/issues', methods=['GET'])
def get_issues():
    issue_type = request.args.get('type')
    status = request.args.get('status')

    query = Issue.query
    user = current_user()
    if not require_admin() and user:
        if user.role in {'manager', 'department_officer', 'worker'}:
            query = query.filter(Issue.assigned_department == user.department)
        elif user.role == 'citizen':
            query = query.filter(Issue.created_by == user.id)
    if issue_type and issue_type != 'all':
        query = query.filter_by(issue_type=issue_type)
    if status and status != 'all':
        query = query.filter_by(status=status)

    issues = query.order_by(Issue.created_at.desc()).all()
    return jsonify({'success': True, 'items': [issue_dict_for_request(issue) for issue in issues]})


@app.route('/api/map/issues', methods=['GET'])
@limiter.limit('120 per minute')
def get_map_issues():
    """Map-only report feed with public coordinate masking and bounded paging."""
    user = current_user()
    is_admin = require_admin()
    query = Issue.query
    if not is_admin and user:
        if user.role in {'manager', 'department_officer', 'worker'}:
            query = query.filter(Issue.assigned_department == user.department)
        elif user.role == 'citizen':
            query = query.filter(Issue.created_by == user.id)
        else:
            query = query.filter(Issue.id == -1)
    department_scope = query

    category = request.args.get('category', '').strip()
    status = request.args.get('status', '').strip()
    department = request.args.get('department', '').strip()
    priority = request.args.get('priority', '').strip().lower()
    search = request.args.get('search', '').strip()[:120]
    if category and category != 'all':
        query = query.filter(or_(Issue.confirmed_category == category, Issue.title == category))
    if status and status != 'all':
        if status not in {'new_report', 'pending', 'assigned', 'in_progress', 'completed'}:
            return jsonify({'success': False, 'error': 'Invalid complaint status filter.'}), 400
        query = query.filter(Issue.status == status)
    if department and department != 'all':
        query = query.filter(Issue.assigned_department.ilike(department))
    if priority and priority != 'all':
        if priority == 'high':
            query = query.filter(Issue.severity >= 8)
        elif priority == 'medium':
            query = query.filter(Issue.severity.between(4, 7))
        elif priority == 'low':
            query = query.filter(Issue.severity <= 3)
        else:
            return jsonify({'success': False, 'error': 'Priority must be low, medium, or high.'}), 400
    if search:
        pattern = f'%{search}%'
        search_fields = [Issue.title.ilike(pattern), Issue.confirmed_category.ilike(pattern), Issue.area_name.ilike(pattern)]
        # Public requests may use private fields to narrow the result only for
        # staff/owner contexts, never to reveal their values.
        if is_admin or (user and user.role in {'manager', 'department_officer', 'worker', 'citizen'}):
            search_fields.extend([Issue.description.ilike(pattern), Issue.geotag_address.ilike(pattern)])
        query = query.filter(or_(*search_fields))

    try:
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        if date_from:
            query = query.filter(Issue.created_at >= datetime.combine(datetime.fromisoformat(date_from).date(), datetime.min.time()))
        if date_to:
            end_date = datetime.fromisoformat(date_to).date() + timedelta(days=1)
            query = query.filter(Issue.created_at < datetime.combine(end_date, datetime.min.time()))
    except ValueError:
        return jsonify({'success': False, 'error': 'Dates must use YYYY-MM-DD format.'}), 400

    missing_coordinate_query = query
    bounds = request.args.get('bounds')
    if bounds:
        try:
            south, west, north, east = [float(value) for value in bounds.split(',')]
            if not (-90 <= south < north <= 90 and -180 <= west < east <= 180):
                raise ValueError
            if is_admin or user:
                query = query.filter(Issue.latitude.between(south, north), Issue.longitude.between(west, east))
            else:
                # Public viewport queries must use the same coarse location
                # precision as public marker output, avoiding a bbox probing
                # side channel for recovering exact report coordinates.
                query = query.filter(func.round(Issue.latitude, 2).between(south, north),
                                     func.round(Issue.longitude, 2).between(west, east))
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'Map bounds must be south,west,north,east decimal coordinates.'}), 400

    missing_coordinates = missing_coordinate_query.filter(or_(
        and_(Issue.latitude == 0, Issue.longitude == 0),
        Issue.latitude < -90, Issue.latitude > 90,
        Issue.longitude < -180, Issue.longitude > 180,
    )).count()
    query = query.filter(Issue.latitude.between(-90, 90), Issue.longitude.between(-180, 180),
                         or_(Issue.latitude != 0, Issue.longitude != 0))
    total = query.count()
    hotspot_rows = query.with_entities(
        (func.round(Issue.latitude * 50) / 50.0).label('latitude_cell'),
        (func.round(Issue.longitude * 50) / 50.0).label('longitude_cell'),
        func.count(Issue.id).label('report_count'),
    ).group_by('latitude_cell', 'longitude_cell').order_by(func.count(Issue.id).desc()).limit(2000).all()
    try:
        limit = max(1, min(int(request.args.get('limit', 300)), 500))
        offset = max(0, int(request.args.get('offset', 0)))
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'Pagination values must be integers.'}), 400
    page_query = query.order_by(Issue.created_at.desc(), Issue.id.desc())
    if is_admin or user:
        page_query = page_query.options(selectinload(Issue.ai_analyses))
    issues = page_query.offset(offset).limit(limit + 1).all()
    has_more = len(issues) > limit
    issues = issues[:limit]
    items = []
    for issue in issues:
        authorized = is_admin or can_access_issue(issue, user)
        latitude, longitude = issue.latitude, issue.longitude
        if not authorized:
            latitude, longitude = round(latitude, 2), round(longitude, 2)
        analysis = issue.ai_analyses[-1] if authorized and issue.ai_analyses else None
        category_value = issue.confirmed_category or (issue.title if issue.issue_type in {'waste', 'civic'} else 'Pothole / Road Damage')
        items.append({
            'id': issue.id,
            'reference': f'UP-{issue.id:04d}',
            'category': category_value,
            'issue_type': issue.issue_type,
            'status': issue.status,
            'priority': 'high' if issue.severity >= 8 else 'low' if issue.severity <= 3 else 'medium',
            'severity': issue.severity,
            'created_at': issue.created_at.isoformat() if issue.created_at else None,
            'latitude': latitude,
            'longitude': longitude,
            'location_approximate': not authorized,
            'short_description': (issue.description[:180] if issue.description else '') if authorized else '',
            'assigned_department': issue.assigned_department if authorized else None,
            'ai_suggested_category': analysis.predicted_category if analysis else None,
            'can_open_details': authorized,
            'is_escalated': bool(issue.escalation_level or issue.escalation_status in {'escalated', 'social_media'}),
        })
    departments = []
    if is_admin or (user and user.role in {'manager', 'department_officer', 'worker'}):
        departments = [name for (name,) in department_scope.with_entities(Issue.assigned_department).filter(
            Issue.assigned_department.isnot(None), Issue.assigned_department != ''
        ).distinct().order_by(Issue.assigned_department).all()]
    return jsonify({'success': True, 'items': items, 'total': total, 'limit': limit,
                    'offset': offset, 'has_more': has_more,
                    'missing_coordinates': missing_coordinates,
                    'precise_coordinates': bool(is_admin or (user and user.role in {'citizen', 'manager', 'department_officer', 'worker'})),
                    'departments': departments,
                    'hotspots': [{'latitude': float(row.latitude_cell), 'longitude': float(row.longitude_cell),
                                  'count': row.report_count} for row in hotspot_rows]})


@app.route('/api/ai/classify', methods=['POST'])
@limiter.limit('30 per hour')
def classify_complaint_image():
    uploaded = request.files.get('image')
    if not uploaded or not uploaded.filename:
        return jsonify({'success': False, 'available': False, 'processing_status': 'failed',
                        'error': 'Choose a complaint image to classify.'}), 400
    try:
        image_bytes, _mimetype, _filename = validate_image_upload(uploaded)
    except ValueError as error:
        return jsonify({'success': False, 'available': False, 'processing_status': 'failed',
                        'error': str(error)}), 400
    try:
        result = classify_image(image_bytes)
    except ModelUnavailable as error:
        return jsonify({'success': False, 'available': False, 'processing_status': 'unavailable',
                        'error': str(error)}), 503
    except (ValueError, RuntimeError) as error:
        return jsonify({'success': False, 'available': False, 'processing_status': 'failed',
                        'error': str(error)}), 503
    if result.get('predicted_category') not in LABELS:
        return jsonify({'success': False, 'available': False, 'processing_status': 'failed',
                        'error': 'The model returned a category outside the configured label set.'}), 503
    confidence = result.get('confidence_score')
    if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        return jsonify({'success': False, 'available': False, 'processing_status': 'failed',
                        'error': 'The model returned an invalid confidence score.'}), 503
    signed_claims = {
        'image_sha256': hashlib.sha256(image_bytes).hexdigest(),
        'predicted_category': result['predicted_category'],
        'confidence_score': float(confidence),
        'model_version': str(result.get('model_version') or 'unknown')[:120],
        'processing_status': result.get('processing_status') if result.get('processing_status') in {'completed', 'requires_review'} else 'requires_review',
        'review_required': bool(result.get('review_required', True)),
    }
    token = URLSafeTimedSerializer(app.config['JWT_SECRET_KEY'], salt='urbanai-ai-prediction').dumps(signed_claims)
    return jsonify({'success': True, 'available': True, **signed_claims, 'prediction_token': token}), 200

@app.route('/api/uploads/<path:filename>')
def uploaded_image(filename):
    safe_filename = secure_filename(filename)
    if not filename or safe_filename != filename or '/' in filename or '\\' in filename:
        return jsonify({'error': 'Image not found'}), 404
    report_path = f'/api/uploads/{filename}'
    authorized = False
    signed_access = request.args.get('access')
    if signed_access:
        try:
            claims = URLSafeTimedSerializer(app.config['JWT_SECRET_KEY'], salt='urbanai-image-access').loads(signed_access, max_age=900)
            issue = db.session.get(Issue, int(claims.get('issue_id', 0)))
            authorized = bool(issue and claims.get('filename') == filename and report_path in {
                issue.image_url, issue.completion_image_url,
            })
        except (BadSignature, SignatureExpired, TypeError, ValueError):
            authorized = False
    if not authorized:
        user = current_user()
        linked_issues = Issue.query.filter(or_(Issue.image_url == report_path, Issue.completion_image_url == report_path)).all()
        authorized = require_admin() or any(can_access_issue(issue, user) for issue in linked_issues)
    if not authorized:
        return jsonify({'error': 'Image access is not authorized.'}), 403
    image = db.session.get(UploadedImage, filename)
    if image:
        return send_file(BytesIO(image.content), mimetype=image.mimetype, download_name=filename)
    legacy_dir = Path(__file__).resolve().parent / 'uploads'
    if legacy_dir.exists() and (legacy_dir / filename).is_file():
        return send_from_directory(legacy_dir, filename)
    return jsonify({'error': 'Image not found'}), 404


@app.route('/api/issues', methods=['POST'])
@limiter.limit('10 per hour')
def create_issue():
    data = request.form if request.content_type and request.content_type.startswith('multipart/') else (request.get_json() or {})
    required = ['issue_type', 'description', 'geotag_address']
    missing = [field for field in required if field not in data or data[field] in ('', None)]
    if missing:
        return jsonify({'success': False, 'error': f'Missing fields: {", ".join(missing)}'}), 400

    issue_type = data['issue_type']
    if issue_type not in {'pothole', 'waste', 'civic'}:
        return jsonify({'success': False, 'error': 'Choose a supported civic complaint type.'}), 400
    description = data.get('description')
    address = data.get('geotag_address')
    if not isinstance(description, str) or not description.strip() or len(description) > 5000:
        return jsonify({'success': False, 'error': 'Description must contain 1 to 5,000 characters.'}), 400
    if not isinstance(address, str) or not address.strip() or len(address.strip()) > 255:
        return jsonify({'success': False, 'error': 'Location must contain 1 to 255 characters.'}), 400
    category = str(data.get('category') or '').strip()
    # Keep old clients compatible: the legacy pothole form submitted the waste
    # selector's first value. Do not reinterpret that as a waste complaint.
    if issue_type == 'pothole' and (category not in COMPLAINT_CATEGORIES or category in LEGACY_WASTE_CATEGORIES):
        category = 'Pothole / Road Damage'
    elif not category:
        category = 'Pothole / Road Damage' if issue_type == 'pothole' else ''
    if category not in COMPLAINT_CATEGORIES:
        return jsonify({'success': False, 'error': 'Choose a valid complaint category.'}), 400
    issue_type = issue_type_for_category(category)
    try:
        image_url = save_uploaded_image(request.files.get('image')) if request.files else accept_existing_image_url(data.get('image_url'))
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 400
    if not image_url:
        return jsonify({'success': False, 'error': 'A report photograph is required.'}), 400
    address = address.strip()
    try:
        latitude = float(data.get('latitude') or 0)
        longitude = float(data.get('longitude') or 0)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'GPS coordinates must be valid numbers.'}), 400
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return jsonify({'success': False, 'error': 'GPS coordinates are outside the valid range.'}), 400

    user = current_user()
    confirmed_category = category
    issue = Issue(
        issue_type=issue_type,
        title=category,
        confirmed_category=confirmed_category,
        description=description.strip(),
        latitude=latitude,
        longitude=longitude,
        geotag_address=address,
        area_name=data.get('area_name', ''),
        severity=5,
        status='assigned' if recommended_department(confirmed_category) else 'new_report',
        image_url=image_url,
        created_by=user.id if user else None,
        assigned_department=recommended_department(confirmed_category),
        assigned_at=datetime.utcnow() if recommended_department(confirmed_category) else None,
    )
    db.session.add(issue)
    db.session.commit()
    if issue.assigned_department:
        audit_action(issue.id, 'department_auto_assigned', f'{confirmed_category} -> {issue.assigned_department} (citizen-confirmed category)')
        db.session.commit()
    image_filename = image_url.removeprefix('/api/uploads/') if image_url else ''
    stored_image = db.session.get(UploadedImage, image_filename) if image_filename else None
    image_bytes = stored_image.content if stored_image else b''
    prediction_token = data.get('prediction_token')
    claims = None
    if prediction_token:
        try:
            claims = URLSafeTimedSerializer(app.config['JWT_SECRET_KEY'], salt='urbanai-ai-prediction').loads(prediction_token, max_age=900)
            if (not image_bytes or claims.get('image_sha256') != hashlib.sha256(image_bytes).hexdigest()
                    or claims.get('predicted_category') not in LABELS
                    or not isinstance(claims.get('confidence_score'), (int, float))
                    or not 0 <= claims['confidence_score'] <= 1):
                claims = None
        except (BadSignature, SignatureExpired, TypeError, ValueError):
            claims = None
    if claims:
        analysis = AIAnalysis(
            issue_id=issue.id, image_url=image_url,
            image_sha256=hashlib.sha256(image_bytes).hexdigest(),
            predicted_category=claims['predicted_category'],
            confidence_score=float(claims['confidence_score']),
            confirmed_category=confirmed_category,
            model_version=claims.get('model_version'),
            processing_status=claims.get('processing_status', 'requires_review'),
            review_required=bool(claims.get('review_required')),
        )
    else:
        analysis = AIAnalysis(
            issue_id=issue.id, image_url=image_url,
            image_sha256=hashlib.sha256(image_bytes).hexdigest() if image_bytes else '0' * 64,
            confirmed_category=confirmed_category,
            processing_status='unavailable' if not prediction_token else 'failed',
            review_required=bool(prediction_token),
            error_message=('No valid signed prediction was attached.' if prediction_token else 'AI classification was unavailable; category was selected manually.'),
        )
    db.session.add(analysis)
    db.session.commit()
    audit_action(issue.id, 'complaint_submitted', issue.issue_type)
    db.session.commit()
    notify_issue_by_email(issue, 'created')
    db.session.commit()
    email_statuses = [row.status for row in EmailDelivery.query.filter_by(issue_id=issue.id, email_type='created').all()]
    return jsonify({'success': True, 'issue': issue_dict_for_request(issue), 'email_statuses': email_statuses}), 201


@app.route('/api/admin/ai-review', methods=['GET'])
def get_ai_review_queue():
    if not require_admin():
        return jsonify({'success': False, 'error': 'Administrator authorization required.'}), 403
    analyses = AIAnalysis.query.filter(
        or_(AIAnalysis.review_required.is_(True), AIAnalysis.processing_status.in_(['failed', 'unavailable']))
    ).order_by(AIAnalysis.created_at.desc()).limit(200).all()
    return jsonify({'success': True, 'items': [issue_dict_for_request(row.issue) for row in analyses if row.issue]})


@app.route('/api/issues/<int:issue_id>/category', methods=['PATCH'])
def correct_issue_category(issue_id):
    issue = Issue.query.get_or_404(issue_id)
    user = current_user()
    if not (require_admin() or (user and can_access_issue(issue, user) and user.role == 'citizen' and issue.created_by == user.id)):
        return jsonify({'success': False, 'error': 'Only the report owner or an administrator can confirm its category.'}), 403
    category = (request.get_json() or {}).get('category')
    if category not in COMPLAINT_CATEGORIES:
        return jsonify({'success': False, 'error': 'Choose a valid complaint category.'}), 400
    previous = issue.confirmed_category
    previous_recommendation = recommended_department(previous)
    issue.confirmed_category = category
    issue.title = category
    issue.issue_type = issue_type_for_category(category)
    if issue.assigned_department == previous_recommendation:
        next_department = recommended_department(category)
        issue.assigned_department = next_department
        issue.assigned_at = datetime.utcnow() if next_department else None
        if issue.status == 'assigned' and not next_department:
            issue.status = 'new_report'
        elif issue.status == 'new_report' and next_department:
            issue.status = 'assigned'
    analysis = AIAnalysis.query.filter_by(issue_id=issue.id).order_by(AIAnalysis.created_at.desc()).first()
    if analysis:
        analysis.confirmed_category = category
    audit_action(issue.id, 'category_confirmed', f'{previous or "unset"} -> {category}')
    db.session.commit()
    return jsonify({'success': True, 'issue': issue_dict_for_request(issue)})


@app.route('/api/issues/<int:issue_id>/ai-review', methods=['PATCH'])
def review_issue_classification(issue_id):
    if not require_admin():
        return jsonify({'success': False, 'error': 'Administrator authorization required.'}), 403
    issue = Issue.query.get_or_404(issue_id)
    category = (request.get_json() or {}).get('confirmed_category')
    if category not in COMPLAINT_CATEGORIES:
        return jsonify({'success': False, 'error': 'Choose a valid complaint category.'}), 400
    analysis = AIAnalysis.query.filter_by(issue_id=issue.id).order_by(AIAnalysis.created_at.desc()).first()
    if not analysis:
        return jsonify({'success': False, 'error': 'No AI analysis is recorded for this complaint.'}), 404
    previous = issue.confirmed_category
    previous_recommendation = recommended_department(previous)
    issue.confirmed_category = category
    issue.title = category
    issue.issue_type = issue_type_for_category(category)
    if issue.assigned_department == previous_recommendation:
        next_department = recommended_department(category)
        issue.assigned_department = next_department
        issue.assigned_at = datetime.utcnow() if next_department else None
        if issue.status == 'assigned' and not next_department:
            issue.status = 'new_report'
        elif issue.status == 'new_report' and next_department:
            issue.status = 'assigned'
    analysis.confirmed_category = category
    analysis.reviewed_category = category
    analysis.processing_status = 'reviewed'
    analysis.review_required = False
    analysis.reviewed_by = current_user().id if current_user() else None
    analysis.reviewed_at = datetime.utcnow()
    audit_action(issue.id, 'ai_classification_reviewed', f'{previous or "unset"} -> {category}; prediction retained: {analysis.predicted_category or "none"}')
    db.session.commit()
    return jsonify({'success': True, 'issue': issue_dict_for_request(issue)})


@app.route('/api/issues/<int:issue_id>/complete', methods=['POST'])
def complete_issue(issue_id):
    if not can_manage_any_issue():
        return jsonify({'success': False, 'error': 'Administrator or department officer authorization required.'}), 403
    issue = Issue.query.get_or_404(issue_id)
    if not can_manage_issue(issue):
        return jsonify({'success': False, 'error': 'Administrator or assigned department authorization required.'}), 403
    data = request.form if request.content_type and request.content_type.startswith('multipart/') else (request.get_json() or {})
    if issue.status == 'completed':
        return jsonify({'success': False, 'error': 'This report is already completed.'}), 409
    try:
        after_image = save_uploaded_image(request.files.get('image')) if request.files else accept_existing_image_url(data.get('completion_image_url'))
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 400
    update_text = data.get('update_text')
    if not after_image or not isinstance(update_text, str) or not update_text.strip() or len(update_text) > 2000:
        return jsonify({'success': False, 'error': 'After photo and completion details are required.'}), 400

    issue.status = 'completed'
    issue.completed_at = datetime.utcnow()
    issue.completion_image_url = after_image

    db.session.add(IssueUpdate(
        issue_id=issue.id,
        updated_by=(current_user().id if current_user() else None),
        update_text=update_text.strip(),
        image_url=issue.completion_image_url,
    ))
    audit_action(issue.id, 'work_completed', update_text.strip())
    db.session.commit()
    notify_issue_by_email(issue, 'completed')
    db.session.commit()
    return jsonify({'success': True, 'issue': issue_dict_for_request(issue)})

@app.route('/api/issues/<int:issue_id>/status', methods=['PATCH'])
def update_issue_status(issue_id):
    if not can_manage_any_issue():
        return jsonify({'success': False, 'error': 'Administrator or department officer authorization required.'}), 403
    issue = Issue.query.get_or_404(issue_id)
    if not can_manage_issue(issue):
        return jsonify({'success': False, 'error': 'Administrator or assigned department authorization required.'}), 403
    status = (request.get_json() or {}).get('status')
    if status not in {'pending', 'in_progress'}:
        return jsonify({'success': False, 'error': 'Choose pending or in_progress; completion requires an after photo.'}), 400
    previous_status = issue.status
    issue.status = status
    audit_action(issue.id, 'status_changed', f'{previous_status} -> {status}')
    db.session.commit()
    if status == 'in_progress' and previous_status != 'in_progress':
        notify_issue_by_email(issue, 'in_progress')
    return jsonify({'success': True, 'issue': issue_dict_for_request(issue)})

@app.route('/api/issues/<int:issue_id>/assign', methods=['POST'])
def assign_issue(issue_id):
    if not require_admin():
        return jsonify({'success': False, 'error': 'Administrator key required.'}), 403
    issue = Issue.query.get_or_404(issue_id)
    department = ((request.get_json() or {}).get('department') or '').strip()
    if not department:
        return jsonify({'success': False, 'error': 'A municipal department is required.'}), 400
    if len(department) > 120:
        return jsonify({'success': False, 'error': 'Department name must be 120 characters or fewer.'}), 400
    issue.assigned_department = department
    issue.assigned_at = datetime.utcnow()
    if issue.status in {'new_report', 'pending'}:
        issue.status = 'assigned'
    audit_action(issue.id, 'department_assigned', department)
    db.session.commit()
    department_email = os.getenv('DEPARTMENT_EMAIL', '').strip()
    recipients = [department_email] if department_email else []
    if issue.creator and issue.creator.email:
        recipients.append(issue.creator.email)
    notify_issue_by_email(issue, 'assigned', recipients)
    if not department_email:
        # Persist the assignment; email configuration is tracked separately and
        # cannot make the complaint itself fail.
        return jsonify({'success': True, 'issue': issue_dict_for_request(issue), 'email_status': 'not_configured'}), 200
    return jsonify({'success': True, 'issue': issue_dict_for_request(issue)}), 200

@app.route('/api/email/status', methods=['GET'])
def email_status():
    if not require_admin():
        return jsonify({'success': False, 'error': 'Administrator key required.'}), 403
    rows = EmailDelivery.query.order_by(EmailDelivery.created_at.desc()).limit(100).all()
    return jsonify({
        'configured': bool(os.getenv('RESEND_API_KEY') and os.getenv('RESEND_FROM_EMAIL') and os.getenv('ADMIN_EMAIL', 'dk142252@gmail.com') and os.getenv('APP_BASE_URL')),
        'department_configured': bool(os.getenv('DEPARTMENT_EMAIL')),
        'webhook_configured': bool(os.getenv('RESEND_WEBHOOK_SECRET')),
        'deliveries': [{
            'id': row.id, 'issue_id': row.issue_id, 'recipient_email': row.recipient_email,
            'email_type': row.email_type, 'status': row.status,
            'provider_email_id': row.provider_email_id,
            'created_at': row.created_at.isoformat() if row.created_at else None,
            'last_attempt_at': row.last_attempt_at.isoformat() if row.last_attempt_at else None,
            'accepted_at': row.accepted_at.isoformat() if row.accepted_at else None,
            'delivered_at': row.delivered_at.isoformat() if row.delivered_at else None,
            'attempt_count': row.attempt_count, 'error': row.error,
        } for row in rows],
    })


@app.route('/api/admin/audit', methods=['GET'])
def audit_log():
    if not require_admin():
        return jsonify({'success': False, 'error': 'Administrator authorization required.'}), 403
    rows = AuditLog.query.order_by(AuditLog.created_at.desc()).limit(200).all()
    return jsonify({'success': True, 'items': [{
        'id': row.id, 'actor_id': row.actor_id, 'issue_id': row.issue_id,
        'action': row.action, 'details': row.details,
        'created_at': row.created_at.isoformat() if row.created_at else None,
    } for row in rows]})

@app.route('/api/email/retry/<int:delivery_id>', methods=['POST'])
def retry_email(delivery_id):
    if not require_admin():
        return jsonify({'success': False, 'error': 'Administrator key required.'}), 403
    delivery = db.session.get(EmailDelivery, delivery_id)
    if not delivery:
        return jsonify({'success': False, 'error': 'Email delivery record not found.'}), 404
    if delivery.status not in {'failed', 'not_configured'}:
        return jsonify({'success': False, 'error': 'Only failed or not-configured deliveries can be retried.'}), 409
    if delivery.last_attempt_at and datetime.utcnow() - delivery.last_attempt_at > timedelta(hours=23):
        return jsonify({'success': False, 'error': 'Safe retry window expired. Resend idempotency keys are retained for 24 hours; verify provider history before any manual resend.'}), 409
    issue = db.session.get(Issue, delivery.issue_id)
    if not issue:
        return jsonify({'success': False, 'error': 'Complaint not found.'}), 404
    send_email_delivery(delivery, issue)
    return jsonify({'success': True, 'delivery': {
        'id': delivery.id, 'status': delivery.status, 'error': delivery.error,
        'attempt_count': delivery.attempt_count,
    }})

@app.route('/api/email/webhook', methods=['POST'])
def resend_webhook():
    raw_body = request.get_data(cache=True)
    if not verify_resend_webhook(raw_body):
        return jsonify({'success': False, 'error': 'Invalid or unconfigured webhook signature.'}), 401
    try:
        event = json.loads(raw_body)
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid JSON.'}), 400
    provider_event_id = request.headers.get('svix-id', '')
    if provider_event_id and EmailWebhookReceipt.query.filter_by(provider_event_id=provider_event_id).first():
        return jsonify({'success': True, 'duplicate': True}), 200
    data = event.get('data') or {}
    provider_email_id = data.get('email_id') or data.get('id')
    delivery = EmailDelivery.query.filter_by(provider_email_id=provider_email_id).first() if provider_email_id else None
    if not delivery:
        return jsonify({'success': True, 'ignored': True}), 200
    type_status = {
        'email.sent': 'accepted',
        'email.delivered': 'delivered',
        'email.delivery_delayed': 'delayed',
        'email.failed': 'failed',
        'email.bounced': 'bounced',
        'email.complained': 'complained',
        'email.suppressed': 'suppressed',
    }
    next_status = type_status.get(event.get('type'))
    if not next_status:
        return jsonify({'success': True, 'ignored': True}), 200
    if provider_event_id:
        db.session.add(EmailWebhookReceipt(
            provider_event_id=provider_event_id,
            delivery_id=delivery.id,
            event_type=event.get('type', ''),
        ))
    terminal = {'delivered', 'failed', 'bounced', 'complained', 'suppressed'}
    terminal = {'delivered', 'failed', 'bounced', 'complained', 'suppressed'}
    was_terminal = delivery.status in terminal
    if not was_terminal:
        delivery.status = next_status
    if next_status == 'delivered' and not was_terminal:
        delivery.delivered_at = datetime.utcnow()
        delivery.error = None
    elif next_status in terminal | {'delayed'} and not was_terminal:
        delivery.error = str(data.get('reason') or data.get('bounce', {}).get('message') or event.get('type'))[:1000]
    db.session.commit()
    return jsonify({'success': True}), 200

@app.route('/api/issues/<int:issue_id>/escalate', methods=['POST'])
def escalate_issue(issue_id):
    if not can_manage_any_issue():
        return jsonify({'success': False, 'error': 'Administrator or department officer authorization required.'}), 403
    issue = Issue.query.get_or_404(issue_id)
    if not can_manage_issue(issue):
        return jsonify({'success': False, 'error': 'Administrator or assigned department authorization required.'}), 403
    issue.escalation_level = min(issue.escalation_level + 1, 5)
    issue.escalation_status = 'escalated'
    issue.escalation_date = datetime.utcnow()

    db.session.add(EscalationLog(
        issue_id=issue.id,
        escalation_level=issue.escalation_level,
        action_taken='Escalation requested by an authorized administrator',
        escalation_type=issue.escalation_status,
    ))
    audit_action(issue.id, 'complaint_escalated', f'Level {issue.escalation_level}')
    db.session.commit()
    return jsonify({'success': True, 'issue': issue_dict_for_request(issue)})


@app.route('/api/stats', methods=['GET'])
def get_stats():
    if not require_admin():
        return jsonify({'success': False, 'error': 'Administrator authorization required.'}), 403
    total = Issue.query.count()
    pending = Issue.query.filter(Issue.status.in_(['pending', 'new_report'])).count()
    in_progress = Issue.query.filter_by(status='in_progress').count()
    completed = Issue.query.filter_by(status='completed').count()
    escalated = Issue.query.filter(Issue.escalation_level > 0).count()
    social_media = Issue.query.filter_by(social_media_posted=True).count()
    return jsonify({
        'success': True,
        'stats': {
            'total': total,
            'pending': pending,
            'in_progress': in_progress,
            'completed': completed,
            'escalated': escalated,
            'social_media': social_media,
        }
    })


# ============ DATABASE INITIALIZATION ============

@app.cli.command('create-admin')
def create_admin():
    """Create a real admin account with an interactively entered password."""
    username = input('Admin username: ').strip()
    email = input('Admin email: ').strip().lower()
    password = getpass('Admin password (minimum 12 characters): ')
    if len(password) < 12:
        raise SystemExit('Password must contain at least 12 characters.')
    if not username or not email:
        raise SystemExit('Username and email are required.')
    if User.query.filter(or_(User.username == username, User.email == email)).first():
        raise SystemExit('That username or email already exists.')
    user = User(username=username, email=email, password_hash=generate_password_hash(password), role='admin')
    db.session.add(user)
    db.session.commit()
    print(f'Administrator {username} created.')

FRONTEND_BUILD = Path(__file__).resolve().parent.parent / 'frontend' / 'build'

@app.route('/', defaults={'path': ''})
@app.route('/<path:path>')
def frontend_app(path):
    if path.startswith('api/'):
        abort(404)
    if not FRONTEND_BUILD.exists():
        return jsonify({'message': 'UrbanPulse backend is running', 'api_root': '/api/health'})
    if path and (FRONTEND_BUILD / path).is_file():
        return send_from_directory(FRONTEND_BUILD, path)
    return send_from_directory(FRONTEND_BUILD, 'index.html')


if __name__ == '__main__':
    app.run(debug=os.getenv('FLASK_DEBUG') == '1' and os.getenv('FLASK_ENV', '').lower() != 'production', host='0.0.0.0', port=5000)
