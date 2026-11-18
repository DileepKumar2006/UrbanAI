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
from werkzeug.utils import secure_filename
from io import BytesIO
from pathlib import Path
from dotenv import load_dotenv

dotenv_path = Path(__file__).resolve().parent / '.env'
load_dotenv(dotenv_path=dotenv_path)

# Initialize Flask app
app = Flask(__name__)

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

def send_telegram(issue, event):
    token, chat_id = os.getenv('TELEGRAM_BOT_TOKEN'), os.getenv('TELEGRAM_CHAT_ID')
    entry = TelegramDelivery.query.filter_by(issue_id=issue.id, event=event).first()
    if entry:
        return
    entry = TelegramDelivery(issue_id=issue.id, event=event, status='not_configured' if not token or not chat_id else 'pending')
    db.session.add(entry)
    db.session.flush()
    if token and chat_id:
        label = 'WORK COMPLETED' if event == 'completed' else ('NEW WASTE REPORT' if issue.issue_type == 'waste' else 'NEW POTHOLE REPORT')
        text = f"🚧 URBANPULSE — {label}\n\n{issue.title}\nLocation: {issue.geotag_address or 'Not provided'}\nReport ID: UP-{issue.id:04d}\nDate: {issue.created_at.isoformat()}\nStatus: {issue.status.replace('_', ' ').title()}"
        try:
            response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage', json={'chat_id': chat_id, 'text': text}, timeout=8)
            response.raise_for_status()
            if not response.json().get('ok'):
                raise requests.RequestException('Telegram API rejected the message.')
            entry.status = 'sent'
            entry.sent_at = datetime.utcnow()
        except requests.RequestException as error:
            entry.status = 'failed'
            entry.error = str(error)[:500]

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

class TelegramDelivery(db.Model):
    __tablename__ = 'telegram_deliveries'
    id = db.Column(db.Integer, primary_key=True)
    issue_id = db.Column(db.Integer, db.ForeignKey('issues.id'), nullable=False)
    event = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(30), nullable=False)
    error = db.Column(db.String(500))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    sent_at = db.Column(db.DateTime)
    __table_args__ = (db.UniqueConstraint('issue_id', 'event', name='uq_telegram_issue_event'),)

class UploadedImage(db.Model):
    __tablename__ = 'uploaded_images'
    filename = db.Column(db.String(255), primary_key=True)
    content = db.Column(db.LargeBinary, nullable=False)
    mimetype = db.Column(db.String(80), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


# ============ ERROR HANDLERS ============

@app.errorhandler(404)
def not_found(error):
    return jsonify({'error': 'Resource not found'}), 404


@app.errorhandler(500)
def internal_error(error):
    db.session.rollback()
    return jsonify({'error': 'Internal server error'}), 500


# ============ HEALTH CHECK ============

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'message': 'UrbanAI backend is running',
        'api_root': '/api/health'
    })


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

    issue = Issue(
        issue_type=issue_type,
        title=(data.get('category') or 'Pothole report') if issue_type == 'waste' else 'Pothole report',
        description=data['description'],
        latitude=float(data.get('latitude') or 0),
        longitude=float(data.get('longitude') or 0),
        geotag_address=address,
        area_name=data.get('area_name', ''),
        severity=5,
        status='new_report',
        image_url=image_url,
    )
    db.session.add(issue)
    db.session.commit()
    send_telegram(issue, 'created')
    db.session.commit()
    return jsonify({'success': True, 'issue': issue.to_dict()}), 201


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
    send_telegram(issue, 'completed')
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
    issue.status = status
    db.session.commit()
    return jsonify({'success': True, 'issue': issue.to_dict()})

@app.route('/api/telegram/status')
def telegram_status():
    configured = bool(os.getenv('TELEGRAM_BOT_TOKEN') and os.getenv('TELEGRAM_CHAT_ID'))
    rows = TelegramDelivery.query.order_by(TelegramDelivery.created_at.desc()).limit(30).all()
    return jsonify({'configured': configured, 'deliveries': [{'issue_id': row.issue_id, 'event': row.event, 'status': row.status, 'created_at': row.created_at.isoformat(), 'sent_at': row.sent_at.isoformat() if row.sent_at else None, 'error': row.error} for row in rows]})


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
