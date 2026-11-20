"""
AI Urban Management Platform - Main Flask Application
Handles citizen pothole and waste reports and their completion history
"""

from flask import Flask, jsonify, request, send_from_directory, send_file, abort
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager, create_access_token, get_jwt, get_jwt_identity, jwt_required, verify_jwt_in_request
from datetime import datetime, timedelta
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
from sqlalchemy import Index, or_, text
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.security import check_password_hash, generate_password_hash
from PIL import Image, UnidentifiedImageError

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

def save_uploaded_image(file):
    if not file or not file.filename:
        return ''
    if file.mimetype not in {'image/jpeg', 'image/png', 'image/webp'}:
        raise ValueError('Please upload a JPEG, PNG, or WebP image.')
    filename = secure_filename(file.filename)
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.jpg', '.jpeg', '.png', '.webp'}:
        raise ValueError('Unsupported image format.')
    filename = f'{datetime.utcnow().strftime("%Y%m%d%H%M%S%f")}-{filename}'
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
    db.session.add(UploadedImage(filename=filename, content=content, mimetype=file.mimetype))
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


def issue_dict_for_request(issue):
    """Return private details only to an admin, owner, or assigned officer."""
    payload = issue.to_dict()
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
        payload.pop('created_by', None)
        payload.pop('updates', None)
        payload.pop('escalation_level', None)
        payload.pop('escalation_status', None)
        payload.pop('social_media_posted', None)
        payload.pop('social_media_url', None)
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
            'category': self.title if self.issue_type == 'waste' else None,
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


class AuditLog(db.Model):
    __tablename__ = 'audit_logs'
    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True, index=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=True, index=True)
    action = db.Column(db.String(80), nullable=False)
    details = db.Column(db.Text, nullable=False, default='')
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, index=True)


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
    category = issue.title if issue.issue_type == 'waste' else 'Pothole'
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
    if issue_type not in {'pothole', 'waste'}:
        return jsonify({'success': False, 'error': 'Only pothole and waste reports are supported.'}), 400
    description = data.get('description')
    address = data.get('geotag_address')
    if not isinstance(description, str) or not description.strip() or len(description) > 5000:
        return jsonify({'success': False, 'error': 'Description must contain 1 to 5,000 characters.'}), 400
    if not isinstance(address, str) or not address.strip() or len(address.strip()) > 255:
        return jsonify({'success': False, 'error': 'Location must contain 1 to 255 characters.'}), 400
    category = str(data.get('category') or 'Pothole report').strip()
    if issue_type == 'waste' and (not category or len(category) > 200):
        return jsonify({'success': False, 'error': 'Waste category must contain 1 to 200 characters.'}), 400
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
    issue = Issue(
        issue_type=issue_type,
        title=category if issue_type == 'waste' else 'Pothole report',
        description=description.strip(),
        latitude=latitude,
        longitude=longitude,
        geotag_address=address,
        area_name=data.get('area_name', ''),
        severity=5,
        status='new_report',
        image_url=image_url,
        created_by=user.id if user else None,
    )
    db.session.add(issue)
    db.session.commit()
    audit_action(issue.id, 'complaint_submitted', issue.issue_type)
    db.session.commit()
    notify_issue_by_email(issue, 'created')
    db.session.commit()
    email_statuses = [row.status for row in EmailDelivery.query.filter_by(issue_id=issue.id, email_type='created').all()]
    return jsonify({'success': True, 'issue': issue_dict_for_request(issue), 'email_statuses': email_statuses}), 201


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
    notify_issue_by_email(issue, 'assigned', [department_email] if department_email else [])
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
    issues = Issue.query.all()
    return jsonify({
        'success': True,
        'stats': {
            'total': len(issues),
            'pending': len([issue for issue in issues if issue.status == 'pending']),
            'in_progress': len([issue for issue in issues if issue.status == 'in_progress']),
            'completed': len([issue for issue in issues if issue.status == 'completed']),
            'escalated': len([issue for issue in issues if issue.escalation_level > 0]),
            'social_media': len([issue for issue in issues if issue.social_media_posted]),
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
