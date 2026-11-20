"""
AI Urban Management Platform - Main Flask Application
Handles citizen pothole and waste reports and their completion history
"""

from flask import Flask, jsonify, request, send_from_directory, send_file, abort
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_jwt_extended import JWTManager
from datetime import datetime, timedelta
import os
import requests
import base64
import hashlib
import hmac
import html
import json
from werkzeug.utils import secure_filename
from io import BytesIO
from pathlib import Path
from dotenv import load_dotenv
from sqlalchemy import inspect, text

dotenv_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=dotenv_path)

# Initialize Flask app
# The frontend build is served by the catch-all route below. Disable Flask's
# default /static route so it cannot shadow React's bundled /static assets.
app = Flask(__name__, static_folder=None)

# ============ CONFIGURATION ============
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    'DATABASE_URL',
    f"sqlite:///{Path(__file__).resolve().parent / 'urban_ai.db'}"
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['MAX_CONTENT_LENGTH'] = 12 * 1024 * 1024
app.config['JWT_SECRET_KEY'] = os.getenv('JWT_SECRET_KEY', 'your-secret-key-change-in-production')
app.config['JWT_ACCESS_TOKEN_EXPIRES'] = timedelta(days=30)

# Initialize extensions
db = SQLAlchemy(app)
jwt = JWTManager(app)
CORS(app)

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
    db.session.add(UploadedImage(filename=filename, content=content, mimetype=file.mimetype))
    return f'/api/uploads/{filename}'

def require_admin():
    expected = os.getenv('URBANPULSE_ADMIN_KEY', '')
    return bool(expected) and request.headers.get('X-Admin-Key', '') == expected

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
        absolute_image_url = html.escape(absolute_app_url(image_url), quote=True)
        images.append(f'<p><b>{html.escape(label_text)}:</b><br><a href="{absolute_image_url}">{absolute_image_url}</a></p>')

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


# ============ HEALTH CHECK ============

@app.route('/api/health', methods=['GET'])
def health_check():
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.utcnow().isoformat(),
        'database': 'connected'
    })


# ============ DEMO API ============

def seed_demo_data():
    """Remove only the exact sample issues from the previous demo seed."""
    demo_titles = [
        'Overflowing pothole cluster near Market Road',
        'Smart signal overload at Tech Park junction',
        'AI waste segregation alert',
    ]
    samples = Issue.query.filter(Issue.title.in_(demo_titles)).all()
    changed = bool(samples)
    if samples:
        for sample in samples:
            db.session.delete(sample)
    traffic_manager = User.query.filter_by(username='traffic_manager', email='traffic@urbanai.local').first()
    if traffic_manager and not Issue.query.filter_by(assigned_to=traffic_manager.id).first():
        NotificationQueue.query.filter_by(recipient_id=traffic_manager.id).delete(synchronize_session=False)
        db.session.delete(traffic_manager)
        changed = True
    if changed:
        db.session.commit()


DATABASE_READY = False

@app.before_request
def ensure_database_ready():
    global DATABASE_READY
    if not DATABASE_READY:
        db.create_all()
        issue_columns = {column['name'] for column in inspect(db.engine).get_columns('issues')}
        if 'assigned_department' not in issue_columns:
            db.session.execute(text('ALTER TABLE issues ADD COLUMN assigned_department VARCHAR(120)'))
            db.session.commit()
        seed_demo_data()
        DATABASE_READY = True


@app.route('/api/issues', methods=['GET'])
def get_issues():
    issue_type = request.args.get('type')
    status = request.args.get('status')

    query = Issue.query
    if issue_type and issue_type != 'all':
        query = query.filter_by(issue_type=issue_type)
    if status and status != 'all':
        query = query.filter_by(status=status)

    issues = query.order_by(Issue.created_at.desc()).all()
    return jsonify({'success': True, 'items': [issue.to_dict() for issue in issues]})

@app.route('/api/uploads/<path:filename>')
def uploaded_image(filename):
    image = db.session.get(UploadedImage, filename)
    if image:
        return send_file(BytesIO(image.content), mimetype=image.mimetype, download_name=filename)
    legacy_dir = Path(__file__).resolve().parent / 'uploads'
    if legacy_dir.exists() and (legacy_dir / filename).is_file():
        return send_from_directory(legacy_dir, filename)
    return jsonify({'error': 'Image not found'}), 404


@app.route('/api/issues', methods=['POST'])
def create_issue():
    data = request.form if request.content_type and request.content_type.startswith('multipart/') else (request.get_json() or {})
    required = ['issue_type', 'description', 'geotag_address']
    missing = [field for field in required if field not in data or data[field] in ('', None)]
    if missing:
        return jsonify({'success': False, 'error': f'Missing fields: {", ".join(missing)}'}), 400

    issue_type = data['issue_type']
    if issue_type not in {'pothole', 'waste'}:
        return jsonify({'success': False, 'error': 'Only pothole and waste reports are supported.'}), 400
    try:
        image_url = save_uploaded_image(request.files.get('image')) if request.files else (data.get('image_url') or '')
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 400
    if not image_url:
        return jsonify({'success': False, 'error': 'A report photograph is required.'}), 400
    address = data['geotag_address'].strip()
    try:
        latitude = float(data.get('latitude') or 0)
        longitude = float(data.get('longitude') or 0)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'GPS coordinates must be valid numbers.'}), 400
    if not -90 <= latitude <= 90 or not -180 <= longitude <= 180:
        return jsonify({'success': False, 'error': 'GPS coordinates are outside the valid range.'}), 400

    issue = Issue(
        issue_type=issue_type,
        title=(data.get('category') or 'Pothole report') if issue_type == 'waste' else 'Pothole report',
        description=data['description'],
        latitude=latitude,
        longitude=longitude,
        geotag_address=address,
        area_name=data.get('area_name', ''),
        severity=5,
        status='new_report',
        image_url=image_url,
    )
    db.session.add(issue)
    db.session.commit()
    notify_issue_by_email(issue, 'created')
    db.session.commit()
    email_statuses = [row.status for row in EmailDelivery.query.filter_by(issue_id=issue.id, email_type='created').all()]
    return jsonify({'success': True, 'issue': issue.to_dict(), 'email_statuses': email_statuses}), 201


@app.route('/api/issues/<int:issue_id>/complete', methods=['POST'])
def complete_issue(issue_id):
    issue = Issue.query.get_or_404(issue_id)
    if not require_admin():
        return jsonify({'success': False, 'error': 'Administrator key required.'}), 403
    data = request.form if request.content_type and request.content_type.startswith('multipart/') else (request.get_json() or {})
    if issue.status == 'completed':
        return jsonify({'success': False, 'error': 'This report is already completed.'}), 409
    try:
        after_image = save_uploaded_image(request.files.get('image')) if request.files else (data.get('completion_image_url') or '')
    except ValueError as error:
        return jsonify({'success': False, 'error': str(error)}), 400
    if not after_image or not data.get('update_text', '').strip():
        return jsonify({'success': False, 'error': 'After photo and completion details are required.'}), 400

    issue.status = 'completed'
    issue.completed_at = datetime.utcnow()
    issue.completion_image_url = after_image

    db.session.add(IssueUpdate(
        issue_id=issue.id,
        update_text=data['update_text'].strip(),
        image_url=issue.completion_image_url,
    ))
    db.session.commit()
    notify_issue_by_email(issue, 'completed')
    db.session.commit()
    return jsonify({'success': True, 'issue': issue.to_dict()})

@app.route('/api/issues/<int:issue_id>/status', methods=['PATCH'])
def update_issue_status(issue_id):
    if not require_admin():
        return jsonify({'success': False, 'error': 'Administrator key required.'}), 403
    issue = Issue.query.get_or_404(issue_id)
    status = (request.get_json() or {}).get('status')
    if status not in {'pending', 'in_progress'}:
        return jsonify({'success': False, 'error': 'Choose pending or in_progress; completion requires an after photo.'}), 400
    previous_status = issue.status
    issue.status = status
    db.session.commit()
    if status == 'in_progress' and previous_status != 'in_progress':
        notify_issue_by_email(issue, 'in_progress')
    return jsonify({'success': True, 'issue': issue.to_dict()})

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
    db.session.commit()
    department_email = os.getenv('DEPARTMENT_EMAIL', '').strip()
    notify_issue_by_email(issue, 'assigned', [department_email] if department_email else [])
    if not department_email:
        # Persist the assignment; email configuration is tracked separately and
        # cannot make the complaint itself fail.
        return jsonify({'success': True, 'issue': issue.to_dict(), 'email_status': 'not_configured'}), 200
    return jsonify({'success': True, 'issue': issue.to_dict()}), 200

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
    issue = Issue.query.get_or_404(issue_id)
    issue.escalation_level = min(issue.escalation_level + 1, 5)
    issue.escalation_status = 'social_media' if issue.escalation_level >= 5 else 'escalated'
    issue.escalation_date = datetime.utcnow()

    if issue.escalation_level >= 5:
        issue.social_media_posted = True
        issue.social_media_url = f'https://social.example.com/urbanai/issues/{issue.id}'

    db.session.add(EscalationLog(
        issue_id=issue.id,
        escalation_level=issue.escalation_level,
        action_taken='Auto escalation triggered from public dashboard',
        escalation_type=issue.escalation_status,
    ))
    db.session.commit()
    return jsonify({'success': True, 'issue': issue.to_dict()})


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

@app.cli.command()
def init_db():
    """Initialize the database"""
    db.create_all()
    print("Database initialized successfully!")


@app.cli.command()
def seed_db():
    """Seed database with sample data"""
    # Create sample users
    admin = User(
        username='admin',
        email='admin@urban-ai.com',
        password_hash='hashed_password',
        role='admin'
    )
    
    pothole_manager = User(
        username='pothole_manager',
        email='pothole@urban-ai.com',
        password_hash='hashed_password',
        role='manager',
        department='pothole_repair',
        phone='555-0001'
    )
    
    waste_manager = User(
        username='waste_manager',
        email='waste@urban-ai.com',
        password_hash='hashed_password',
        role='manager',
        department='waste_collection',
        phone='555-0003'
    )
    
    citizen = User(
        username='citizen_001',
        email='citizen@example.com',
        password_hash='hashed_password',
        role='citizen'
    )
    
    db.session.add_all([admin, pothole_manager, waste_manager, citizen])
    db.session.commit()
    print("Database seeded with sample users!")

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
    app.run(debug=True, host='0.0.0.0', port=5000)
