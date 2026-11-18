"""
API Routes for Urban AI Management Platform
Handles issue creation, updates, manager operations, and escalations
"""

from flask import Blueprint, request, jsonify, current_app
from flask_jwt_extended import jwt_required, get_jwt_identity
from datetime import datetime, timedelta
from sqlalchemy import desc
import logging

logger = logging.getLogger(__name__)

# Create blueprints
pothole_bp = Blueprint('potholes', __name__, url_prefix='/api/potholes')
waste_bp = Blueprint('waste', __name__, url_prefix='/api/waste')
issues_bp = Blueprint('issues', __name__, url_prefix='/api/issues')
manager_bp = Blueprint('managers', __name__, url_prefix='/api/managers')
escalation_bp = Blueprint('escalation', __name__, url_prefix='/api/escalation')
feed_bp = Blueprint('feed', __name__, url_prefix='/api/feed')

# ============ POTHOLE ROUTES ============

@pothole_bp.route('', methods=['POST'])
@jwt_required()
def report_pothole():
    """Report a new pothole"""
    from app import db, Issue, NotificationQueue, User
    from utils.geotag import GeotagService
    from services.notification_service import NotificationService
    
    try:
        current_user = get_jwt_identity()
        data = request.get_json()
        
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        # Validate required fields
        required_fields = ['latitude', 'longitude', 'description']
        if not all(field in data for field in required_fields):
            return jsonify({'error': 'Missing required fields'}), 400
        
        # Get address from coordinates
        geo_service = GeotagService()
        address = geo_service.get_address_from_coordinates(
            data['latitude'],
            data['longitude']
        )
        
        # Create issue
        new_issue = Issue(
            issue_type='pothole',
            title=data.get('title', f"Pothole at {address}"),
            description=data['description'],
            latitude=data['latitude'],
            longitude=data['longitude'],
            geotag_address=address,
            severity=data.get('severity', 5),
            image_url=data.get('image_url', ''),
            created_by=current_user
        )
        
        db.session.add(new_issue)
        db.session.flush()  # Get the ID
        
        # Find appropriate manager (pothole_repair department)
        manager = User.query.filter_by(
            role='manager',
            department='pothole_repair'
        ).first()
        
        if manager:
            new_issue.assigned_to = manager.id
            new_issue.assigned_at = datetime.utcnow()
            new_issue.status = 'assigned'
            
            # Create notification
            notif_service = NotificationService(current_app)
            notif_service.create_notification(
                db,
                issue_id=new_issue.id,
                manager_id=manager.id,
                notification_type='new_pothole',
                time_limit_hours=24
            )
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': 'Pothole reported successfully',
            'issue_id': new_issue.id,
            'issue': new_issue.to_dict()
        }), 201
        
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error reporting pothole: {str(e)}")
        return jsonify({'error': str(e)}), 500


@pothole_bp.route('', methods=['GET'])
def get_all_potholes():
    """Get all reported potholes"""
    from app import Issue
    
    try:
        page = request.args.get('page', 1, type=int)
        status = request.args.get('status', None)
        
        query = Issue.query.filter_by(issue_type='pothole')
        
        if status:
            query = query.filter_by(status=status)
        
        potholes = query.order_by(desc(Issue.created_at)).paginate(
            page=page,
            per_page=20
        )
        
        return jsonify({
            'success': True,
            'total': potholes.total,
            'pages': potholes.pages,
            'current_page': page,
            'items': [issue.to_dict() for issue in potholes.items]
        }), 200
        
    except Exception as e:
        logger.error(f"Error fetching potholes: {str(e)}")
        return jsonify({'error': str(e)}), 500


@pothole_bp.route('/<int:issue_id>', methods=['GET'])
def get_pothole(issue_id):
    """Get specific pothole details"""
    from app import Issue
    
    try:
        issue = Issue.query.filter_by(
            id=issue_id,
            issue_type='pothole'
        ).first()
        
        if not issue:
            return jsonify({'error': 'Pothole not found'}), 404
        
        return jsonify({
            'success': True,
            'issue': issue.to_dict()
        }), 200
        
    except Exception as e:
        logger.error(f"Error fetching pothole: {str(e)}")
        return jsonify({'error': str(e)}), 500
