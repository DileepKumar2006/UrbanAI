import csv
import os
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from PIL import Image
import werkzeug

if not hasattr(werkzeug, '__version__'):
    werkzeug.__version__ = '3-compat'

os.environ.setdefault('DATABASE_URL', 'sqlite://')
os.environ.setdefault('URBANPULSE_ADMIN_KEY', 'test-admin-key')
os.environ.setdefault('RESEND_API_KEY', '')

from app import AIAnalysis, AuditLog, Issue, User, app, db, limiter, create_access_token
from werkzeug.security import generate_password_hash
from ai.prepare_dataset import prepare_dataset
from ai.inference import ModelUnavailable


def jpeg_bytes(color=(20, 50, 80)):
    image = BytesIO()
    Image.new('RGB', (24, 18), color=color).save(image, 'JPEG')
    return image.getvalue()


class AIWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite://', RATELIMIT_ENABLED=False)
        limiter.enabled = False

    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.admin = {'X-Admin-Key': 'test-admin-key'}

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def image_upload(self, data=None, name='photo.jpg'):
        return {'image': (BytesIO(data or jpeg_bytes()), name, 'image/jpeg')}

    def classify(self, payload=None, result=None):
        prediction = result or {
            'predicted_category': 'Water Leakage', 'confidence_score': 0.51,
            'model_version': 'test-model-v1', 'processing_status': 'requires_review',
            'review_required': True,
        }
        with patch('app.classify_image', return_value=prediction):
            return self.client.post('/api/ai/classify', data=self.image_upload(payload), content_type='multipart/form-data')

    def submit(self, payload=None, token=None, category='Pothole / Road Damage'):
        data = self.image_upload(payload)
        data.update({'issue_type': 'pothole', 'category': category,
                     'description': 'Observed civic issue', 'geotag_address': 'Central Road',
                     'latitude': '12.3', 'longitude': '77.6'})
        if token:
            data['prediction_token'] = token
        return self.client.post('/api/issues', data=data, content_type='multipart/form-data')

    def test_missing_model_is_nonblocking_and_persisted_as_unavailable(self):
        with patch('app.classify_image', side_effect=ModelUnavailable('No trained model installed.')):
            unavailable = self.client.post('/api/ai/classify', data=self.image_upload(), content_type='multipart/form-data')
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(unavailable.get_json()['processing_status'], 'unavailable')
        submitted = self.submit()
        self.assertEqual(submitted.status_code, 201)
        self.assertEqual(AIAnalysis.query.one().processing_status, 'unavailable')

    def test_signed_prediction_is_bound_to_image_and_kept_separate_from_confirmation(self):
        content = jpeg_bytes()
        prediction = self.classify(content)
        self.assertEqual(prediction.status_code, 200)
        result = prediction.get_json()
        submitted = self.submit(content, result['prediction_token'], category='Pothole / Road Damage')
        issue = Issue.query.one()
        analysis = AIAnalysis.query.one()
        self.assertEqual(submitted.status_code, 201)
        self.assertEqual(analysis.predicted_category, 'Water Leakage')
        self.assertEqual(issue.confirmed_category, 'Pothole / Road Damage')
        self.assertEqual(submitted.get_json()['issue']['recommended_department'], 'Public Works Department')
        self.assertIsNone(issue.assigned_department)
        self.assertTrue(analysis.review_required)

    def test_mismatched_prediction_does_not_reject_report_and_queues_review(self):
        predicted = self.classify(jpeg_bytes()).get_json()
        response = self.submit(jpeg_bytes((250, 10, 20)), predicted['prediction_token'])
        analysis = AIAnalysis.query.one()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(analysis.processing_status, 'failed')
        self.assertTrue(analysis.review_required)

    def test_invalid_image_rejected_by_classifier(self):
        response = self.client.post('/api/ai/classify', data=self.image_upload(b'not-an-image'), content_type='multipart/form-data')
        self.assertEqual(response.status_code, 400)

    def test_admin_review_records_correction_and_retains_original_prediction(self):
        content = jpeg_bytes()
        prediction = self.classify(content).get_json()
        submitted = self.submit(content, prediction['prediction_token'])
        issue_id = submitted.get_json()['issue']['id']
        denied = self.client.patch(f'/api/issues/{issue_id}/ai-review', json={'confirmed_category': 'Road Obstruction'})
        self.assertEqual(denied.status_code, 403)
        reviewed = self.client.patch(f'/api/issues/{issue_id}/ai-review', json={'confirmed_category': 'Road Obstruction'}, headers=self.admin)
        self.assertEqual(reviewed.status_code, 200)
        analysis = AIAnalysis.query.one()
        self.assertEqual(analysis.predicted_category, 'Water Leakage')
        self.assertEqual(analysis.reviewed_category, 'Road Obstruction')
        self.assertEqual(analysis.processing_status, 'reviewed')
        self.assertFalse(analysis.review_required)
        self.assertEqual(AuditLog.query.filter_by(action='ai_classification_reviewed').count(), 1)

    def test_report_owner_can_correct_category_and_admin_queue_lists_uncertain_report(self):
        citizen = User(username='resident', email='resident@example.test', password_hash=generate_password_hash('test-password'), role='citizen')
        db.session.add(citizen); db.session.commit()
        token = create_access_token(identity=str(citizen.id))
        content = jpeg_bytes()
        prediction = self.classify(content).get_json()
        response = self.submit(content, prediction['prediction_token'], category='Pothole / Road Damage')
        issue_id = response.get_json()['issue']['id']
        db.session.get(Issue, issue_id).created_by = citizen.id
        db.session.commit()
        corrected = self.client.patch(f'/api/issues/{issue_id}/category', json={'category': 'Water Leakage'}, headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(corrected.status_code, 200)
        self.assertEqual(Issue.query.get(issue_id).confirmed_category, 'Water Leakage')
        self.assertEqual(AIAnalysis.query.one().predicted_category, 'Water Leakage')
        queue = self.client.get('/api/admin/ai-review', headers=self.admin)
        self.assertEqual(queue.status_code, 200)
        self.assertEqual([item['id'] for item in queue.get_json()['items']], [issue_id])

    def test_dataset_pipeline_excludes_unverified_and_groups_exact_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            raw = root / 'raw'
            folder = raw / 'pothole_road_damage'
            folder.mkdir(parents=True)
            data = jpeg_bytes()
            (folder / 'one.jpg').write_bytes(data)
            (folder / 'duplicate.jpg').write_bytes(data)
            (folder / 'unverified.jpg').write_bytes(jpeg_bytes((20, 180, 40)))
            manifest = root / 'sources.csv'
            with manifest.open('w', newline='', encoding='utf-8') as stream:
                writer = csv.DictWriter(stream, fieldnames=('relative_path', 'source', 'license', 'license_url', 'attribution', 'permission_verified'))
                writer.writeheader()
                for name, verified in [('one.jpg', 'yes'), ('duplicate.jpg', 'yes'), ('unverified.jpg', 'no')]:
                    writer.writerow({'relative_path': f'pothole_road_damage/{name}', 'source': 'reviewed', 'license': 'CC0', 'license_url': 'https://example.test', 'attribution': '', 'permission_verified': verified})
            report = prepare_dataset(raw, manifest, root / 'split')
            self.assertEqual(report['total_images'], 1)
            self.assertEqual(len(report['duplicates']), 1)
            self.assertTrue(any('not verified' in item['reason'] for item in report['excluded']))
            with (root / 'split' / 'dataset_manifest.csv').open(encoding='utf-8') as stream:
                split_paths = [Path(row['relative_path']) for row in csv.DictReader(stream)]
            self.assertEqual(len(split_paths), 1)


if __name__ == '__main__':
    unittest.main()
