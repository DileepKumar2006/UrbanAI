import logging
from datetime import datetime, timedelta
from typing import List
from flask_mail import Mail, Message

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class NotificationService:
    """Handle notifications and escalation logic"""
    
    def __init__(self, app=None):
        self.app = app
        self.mail = Mail(app) if app else None
        self.escalation_config = {
            'level_1': {'hours': 2, 'action': 'manager_email'},
            'level_2': {'hours': 6, 'action': 'manager_sms_reminder'},
            'level_3': {'hours': 12, 'action': 'senior_manager_notification'},
            'level_4': {'hours': 20, 'action': 'admin_alert'},
            'level_5': {'hours': 24, 'action': 'social_media_post'}
        }
    
    def create_notification(self, db, issue_id: int, manager_id: int, 
                           notification_type: str, time_limit_hours: int = 24) -> bool:
        """Create notification for manager with escalation tracking"""
        from app import NotificationQueue
        
        try:
            deadline = datetime.utcnow() + timedelta(hours=time_limit_hours)
            
            notification = NotificationQueue(
                issue_id=issue_id,
                recipient_id=manager_id,
                notification_type=notification_type,
                message=f"New {notification_type} issue assigned. Action required by {deadline}",
                time_limit_hours=time_limit_hours,
                deadline=deadline
            )
            
            db.session.add(notification)
            db.session.commit()
            
            logger.info(f"Notification created for issue {issue_id}")
            return True
            
        except Exception as e:
            logger.error(f"Error creating notification: {str(e)}")
            return False
    
    def check_escalations(self, db) -> List[int]:
        """Check for issues that need escalation"""
        from app import NotificationQueue, Issue
        
        now = datetime.utcnow()
        escalated_issues = []
        
        try:
            # Find notifications past deadline
            overdue_notifications = NotificationQueue.query.filter(
                NotificationQueue.deadline < now,
                NotificationQueue.escalation_triggered == False,
                NotificationQueue.is_sent == True
            ).all()
            
            for notif in overdue_notifications:
                issue = Issue.query.get(notif.issue_id)
                if issue and issue.status != 'completed':
                    self.escalate_issue(db, issue_id=issue.id)
                    escalated_issues.append(issue.id)
            
            return escalated_issues
            
        except Exception as e:
            logger.error(f"Error checking escalations: {str(e)}")
            return []
    
    def escalate_issue(self, db, issue_id: int):
        """Escalate an issue to next level"""
        from app import Issue, EscalationLog, NotificationQueue
        
        try:
            issue = Issue.query.get(issue_id)
            if not issue:
                return False
            
            issue.escalation_level = min(issue.escalation_level + 1, 5)
            
            escalation_action = list(self.escalation_config.values())[issue.escalation_level - 1]['action']
            
            escalation_log = EscalationLog(
                issue_id=issue_id,
                escalation_level=issue.escalation_level,
                action_taken=escalation_action,
                escalation_type=escalation_action
            )
            
            db.session.add(escalation_log)
            
            # Update notification
            notif = NotificationQueue.query.filter_by(issue_id=issue_id).first()
            if notif:
                notif.escalation_triggered = True
            
            db.session.commit()
            
            logger.info(f"Issue {issue_id} escalated to level {issue.escalation_level}")
            return True
            
        except Exception as e:
            logger.error(f"Error escalating issue: {str(e)}")
            return False
    
    def send_email_notification(self, recipient_email: str, subject: str, body: str) -> bool:
        """Send email notification"""
        if not self.mail:
            logger.warning("Mail service not configured")
            return False
        
        try:
            msg = Message(subject=subject, recipients=[recipient_email], body=body)
            self.mail.send(msg)
            logger.info(f"Email sent to {recipient_email}")
            return True
        except Exception as e:
            logger.error(f"Error sending email: {str(e)}")
            return False