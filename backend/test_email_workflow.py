import base64
import hashlib
import hmac
import json
import os
import unittest
from datetime import datetime
from io import BytesIO
from unittest.mock import Mock, patch
from PIL import Image
import werkzeug

# Flask 2.3's test client still reads this legacy attribute; Werkzeug 3 removed it.
if not hasattr(werkzeug, '__version__'):
    werkzeug.__version__ = '3-compat'

os.environ.update({
    'DATABASE_URL': 'sqlite://',
    'URBANPULSE_ADMIN_KEY': 'test-admin-key',
    'RESEND_API_KEY': 're_test_key',
    'RESEND_FROM_EMAIL': 'UrbanPulse <alerts@example.com>',
    'ADMIN_EMAIL': 'dk142252@gmail.com',
    'DEPARTMENT_EMAIL': 'roads@example.com',
    'APP_BASE_URL': 'https://urbanpulse.example',
})

from app import app, db, EmailDelivery, Issue, UploadedImage, User, limiter
from werkzeug.security import generate_password_hash


class ComplaintEmailWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite://', RATELIMIT_ENABLED=False)
        limiter.enabled = False
        cls._secret = b'resend-webhook-test-secret'
        os.environ['RESEND_WEBHOOK_SECRET'] = 'whsec_' + base64.b64encode(cls._secret).decode()

    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.resend = Mock()
        self.resend.return_value.ok = True
        self.resend.return_value.json.return_value = {'id': 're_email_123'}
        self.post_patch = patch('app.requests.post', self.resend)
        self.post_patch.start()

    def tearDown(self):
        self.post_patch.stop()
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def submit_report(self, issue_type, headers=None):
        photo = BytesIO()
        Image.new('RGB', (4, 4), color='red').save(photo, format='JPEG')
        photo.seek(0)
        return self.client.post('/api/issues', data={
            'issue_type': issue_type,
            'category': 'Mixed Waste',
            'description': f'Test {issue_type} report details',
            'geotag_address': 'Test Main Road',
            'latitude': '12.3',
            'longitude': '77.6',
            'image': (photo, 'before.jpg', 'image/jpeg'),
        }, headers=headers, content_type='multipart/form-data')

    def test_new_pothole_and_waste_send_email_with_photo_and_dashboard_link(self):
        pothole = self.submit_report('pothole')
        waste = self.submit_report('waste')
        self.assertEqual(pothole.status_code, 201)
        self.assertEqual(waste.status_code, 201)
        self.assertEqual(Issue.query.count(), 2)
        deliveries = EmailDelivery.query.filter_by(email_type='created').all()
        self.assertEqual(len(deliveries), 4)
        self.assertTrue(all(row.status == 'accepted' for row in deliveries))
        self.assertTrue(all('Idempotency-Key' in call.kwargs['headers'] for call in self.resend.call_args_list))
        payload = self.resend.call_args_list[0].kwargs['json']
        self.assertIn('UP-0001', payload['html'])
        self.assertIn('View complaint in UrbanPulse', payload['html'])
        self.assertTrue(payload['attachments'][0]['filename'].endswith('-before.jpg'))

    def test_assignment_progress_and_completion_share_complaint_and_attach_before_after(self):
        submitted = self.submit_report('pothole')
        issue_id = submitted.get_json()['issue']['id']
        admin_headers = {'X-Admin-Key': 'test-admin-key'}
        assigned = self.client.post(f'/api/issues/{issue_id}/assign', json={'department': 'Roads Department'}, headers=admin_headers)
        self.assertEqual(assigned.status_code, 200)
        self.assertEqual(assigned.get_json()['issue']['assigned_department'], 'Roads Department')
        progress = self.client.patch(f'/api/issues/{issue_id}/status', json={'status': 'in_progress'}, headers=admin_headers)
        self.assertEqual(progress.status_code, 200)
        completed = self.client.post(f'/api/issues/{issue_id}/complete', data={
            'update_text': 'Road repair completed successfully.',
            'image': (self.jpeg(), 'after.jpg', 'image/jpeg'),
        }, headers=admin_headers, content_type='multipart/form-data')
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(db.session.get(Issue, issue_id).status, 'completed')
        self.assertEqual(EmailDelivery.query.filter_by(email_type='assigned').count(), 1)
        self.assertEqual(EmailDelivery.query.filter_by(email_type='in_progress').count(), 2)
        self.assertEqual(EmailDelivery.query.filter_by(email_type='completed').count(), 2)
        completion_call = next(call for call in self.resend.call_args_list if call.kwargs['json']['subject'].startswith('UrbanPulse Work completed'))
        self.assertTrue(any(item['filename'].endswith('-before.jpg') for item in completion_call.kwargs['json']['attachments']))
        self.assertTrue(any(item['filename'].endswith('-after.jpg') for item in completion_call.kwargs['json']['attachments']))
        self.assertIn('Road repair completed successfully.', completion_call.kwargs['json']['html'])

    def test_signed_delivery_webhook_marks_delivered_not_provider_acceptance(self):
        submitted = self.submit_report('waste')
        self.assertTrue(all(row.status == 'accepted' for row in EmailDelivery.query.filter_by(email_type='created').all()))
        delivery = EmailDelivery.query.filter_by(email_type='created').first()
        payload = json.dumps({'type': 'email.delivered', 'data': {'email_id': delivery.provider_email_id, 'to': [delivery.recipient_email]}}).encode()
        event_id = 'evt_test_001'
        timestamp = str(int(datetime.utcnow().timestamp()))
        signature = base64.b64encode(hmac.new(self._secret, f'{event_id}.{timestamp}.'.encode() + payload, hashlib.sha256).digest()).decode()
        response = self.client.post('/api/email/webhook', data=payload, content_type='application/json', headers={
            'svix-id': event_id, 'svix-timestamp': timestamp, 'svix-signature': f'v1,{signature}',
        })
        self.assertEqual(response.status_code, 200)
        db.session.refresh(delivery)
        self.assertEqual(delivery.status, 'delivered')
        self.assertIsNotNone(delivery.delivered_at)
        self.client.post('/api/email/webhook', data=payload, content_type='application/json', headers={
            'svix-id': event_id, 'svix-timestamp': timestamp, 'svix-signature': f'v1,{signature}',
        })
        db.session.refresh(delivery)
        self.assertEqual(delivery.status, 'delivered')

    def test_failed_email_does_not_rollback_complaint_and_retry_reuses_idempotency_key(self):
        self.resend.return_value.ok = False
        self.resend.return_value.status_code = 422
        self.resend.return_value.text = 'sender domain is not verified'
        response = self.submit_report('pothole')
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Issue.query.count(), 1)
        failed = EmailDelivery.query.filter_by(email_type='created').first()
        self.assertEqual(failed.status, 'failed')
        first_key = failed.idempotency_key
        retry = self.client.post(f'/api/email/retry/{failed.id}', headers={'X-Admin-Key': 'test-admin-key'})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(self.resend.call_args_list[-1].kwargs['headers']['Idempotency-Key'], first_key)
        self.assertEqual(Issue.query.count(), 1)

    @staticmethod
    def jpeg():
        photo = BytesIO()
        Image.new('RGB', (4, 4), color='blue').save(photo, format='JPEG')
        photo.seek(0)
        return photo

    def test_public_issue_list_hides_exact_location_and_description(self):
        self.submit_report('pothole')
        public = self.client.get('/api/issues').get_json()['items'][0]
        self.assertNotIn('latitude', public)
        self.assertNotIn('longitude', public)
        self.assertNotIn('geotag_address', public)
        self.assertNotIn('description', public)
        self.assertNotIn('image_url', public)
        private = self.client.get('/api/issues', headers={'X-Admin-Key': 'test-admin-key'}).get_json()['items'][0]
        self.assertEqual(private['latitude'], 12.3)
        self.assertEqual(private['description'], 'Test pothole report details')

    def test_bad_image_is_rejected_and_admin_jwt_is_authorized(self):
        bad = self.client.post('/api/issues', data={
            'issue_type': 'pothole', 'description': 'Bad file',
            'geotag_address': 'Test Road', 'latitude': '1', 'longitude': '2',
            'image': (BytesIO(b'not an image'), 'bad.jpg', 'image/jpeg'),
        }, content_type='multipart/form-data')
        self.assertEqual(bad.status_code, 400)
        self.assertEqual(Issue.query.count(), 0)
        user = User(username='reviewer', email='reviewer@example.com',
                    password_hash=generate_password_hash('long-test-password'), role='admin')
        db.session.add(user)
        db.session.commit()
        response = self.client.post('/api/auth/login', json={
            'username': 'reviewer', 'password': 'long-test-password',
        })
        self.assertEqual(response.status_code, 200)
        token = response.get_json()['access_token']
        submitted = self.submit_report('pothole')
        issue_id = submitted.get_json()['issue']['id']
        admin_headers = {'Authorization': f'Bearer {token}'}
        self.assertEqual(self.client.patch(f'/api/issues/{issue_id}/status', json={
            'status': 'in_progress',
        }, headers=admin_headers).status_code, 200)
        self.assertEqual(self.client.post(f'/api/issues/{issue_id}/escalate', headers=admin_headers).status_code, 200)
        self.assertEqual(self.client.post(f'/api/issues/{issue_id}/escalate').status_code, 403)
        self.assertEqual(self.client.post(f'/api/issues/{issue_id}/escalate', headers={
            'Authorization': f'Bearer {token}',
        }).status_code, 200)

    def test_citizen_owns_private_report_and_signed_photo_access(self):
        signup = self.client.post('/api/auth/register', json={
            'username': 'citizen-one', 'email': 'citizen@example.com',
            'password': 'citizen-long-password',
        })
        self.assertEqual(signup.status_code, 201)
        token = signup.get_json()['access_token']
        headers = {'Authorization': f'Bearer {token}'}
        submitted = self.submit_report('pothole', headers=headers)
        issue_id = submitted.get_json()['issue']['id']
        self.assertEqual(db.session.get(Issue, issue_id).created_by, signup.get_json()['user']['id'])
        own = self.client.get('/api/issues', headers=headers).get_json()['items']
        self.assertEqual(len(own), 1)
        self.assertEqual(own[0]['description'], 'Test pothole report details')
        image_url = own[0]['image_url']
        self.assertIn('?access=', image_url)
        image_response = self.client.get(image_url)
        self.assertEqual(image_response.status_code, 200)
        direct_path = image_url.split('?')[0]
        self.assertEqual(self.client.get(direct_path).status_code, 403)
        self.assertEqual(self.client.patch(f'/api/issues/{issue_id}/status',
                                           json={'status': 'in_progress'}, headers=headers).status_code, 403)
        logout = self.client.post('/api/auth/logout', headers=headers)
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(self.client.get('/api/issues', headers=headers).get_json()['items'][0].get('description'), None)

    def test_invalid_login_and_database_health(self):
        denied = self.client.post('/api/auth/login', json={
            'username': 'missing-user', 'password': 'invalid-password',
        })
        self.assertEqual(denied.status_code, 401)
        health = self.client.get('/api/health')
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.get_json()['database'], 'connected')
        self.assertEqual(health.headers.get('X-Content-Type-Options'), 'nosniff')
        with patch.object(db.session, 'execute', side_effect=RuntimeError('database unavailable')):
            unhealthy = self.client.get('/api/health')
        self.assertEqual(unhealthy.status_code, 503)
        self.assertEqual(unhealthy.get_json()['database'], 'unavailable')

    def test_assigned_department_officer_can_update_only_assigned_work(self):
        response = self.submit_report('pothole')
        issue_id = response.get_json()['issue']['id']
        admin_headers = {'X-Admin-Key': 'test-admin-key'}
        self.client.post(f'/api/issues/{issue_id}/assign', json={'department': 'Roads Department'}, headers=admin_headers)
        created = self.client.post('/api/auth/register-department', json={
            'username': 'roads-officer', 'email': 'roads@example.com',
            'department': 'Roads Department', 'password': 'roads-long-password',
        }, headers=admin_headers)
        self.assertEqual(created.status_code, 201)
        login = self.client.post('/api/auth/login', json={
            'username': 'roads-officer', 'password': 'roads-long-password',
        })
        officer_headers = {'Authorization': f"Bearer {login.get_json()['access_token']}"}
        self.assertEqual(self.client.patch(f'/api/issues/{issue_id}/status', json={
            'status': 'in_progress',
        }, headers=officer_headers).status_code, 200)
        private = self.client.get('/api/issues', headers=officer_headers).get_json()['items'][0]
        self.assertEqual(private['geotag_address'], 'Test Main Road')
        self.assertEqual(self.client.post(f'/api/issues/{issue_id}/assign', json={
            'department': 'Other Department',
        }, headers=officer_headers).status_code, 403)


if __name__ == '__main__':
    unittest.main()
