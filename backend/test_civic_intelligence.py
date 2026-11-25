import os
import unittest

import werkzeug
if not hasattr(werkzeug, '__version__'):
    werkzeug.__version__ = '3-compat'

os.environ.update({'DATABASE_URL': 'sqlite://', 'URBANPULSE_ADMIN_KEY': 'test-admin-key'})
from app import (DepartmentRoute, Issue, IssueGroup, IssueHistory, User, app,
                 create_access_token, db, find_duplicate_candidates, limiter,
                 recommended_department)


class CivicIntelligenceWorkflowTest(unittest.TestCase):
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
        self.citizen = User(username='resident', email='resident-civic@example.test', password_hash='hash', role='citizen')
        self.other = User(username='neighbor', email='neighbor-civic@example.test', password_hash='hash', role='citizen')
        self.admin = User(username='admin-civic', email='admin-civic@example.test', password_hash='hash', role='admin')
        self.officer = User(username='roads-officer', email='roads@example.test', password_hash='hash',
                            role='department_officer', department='Public Works Department')
        db.session.add_all([self.citizen, self.other, self.admin, self.officer])
        db.session.commit()
        self.citizen_headers = self.headers(self.citizen)
        self.other_headers = self.headers(self.other)
        self.officer_headers = self.headers(self.officer)
        self.admin_headers = {'X-Admin-Key': 'test-admin-key'}

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    @staticmethod
    def headers(user):
        token = create_access_token(identity=str(user.id), additional_claims={'role': user.role})
        return {'Authorization': f'Bearer {token}'}

    def issue(self, **values):
        defaults = dict(issue_type='pothole', title='Pothole / Road Damage',
            confirmed_category='Pothole / Road Damage', description='Large pothole beside the east bus stop',
            latitude=12.9716, longitude=77.5946, geotag_address='Exact address is private',
            area_name='Ward 12', status='in_progress', image_url=None,
            created_by=self.citizen.id, assigned_department='Public Works Department')
        defaults.update(values)
        row = Issue(**defaults)
        db.session.add(row); db.session.commit()
        return row

    def test_duplicate_candidates_use_category_text_and_distance_and_hide_private_address(self):
        issue = self.issue()
        response = self.client.post('/api/issues/duplicate-candidates', json={
            'category': 'Pothole / Road Damage',
            'description': 'Large pothole beside east bus stop has grown wider',
            'latitude': 12.9717, 'longitude': 77.5945,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['items'][0]['id'], issue.id)
        match = response.json['items'][0]
        self.assertIn('similarity_score', match)
        self.assertNotIn('geotag_address', match)
        self.assertNotIn('created_by', match)
        self.assertEqual(match['location'], 'Ward 12')
        far = find_duplicate_candidates('Pothole / Road Damage', 'Large pothole beside east bus stop', 13.5, 77.5)
        self.assertEqual(far, [])

    def test_admin_can_group_and_separate_without_deleting_reports(self):
        first = self.issue()
        second = self.issue(description='Another rider reports the pothole by the east bus stop', created_by=self.other.id)
        linked = self.client.patch(f'/api/admin/issues/{second.id}/group', headers=self.admin_headers,
                                   json={'related_issue_id': first.id})
        self.assertEqual(linked.status_code, 200)
        self.assertEqual(Issue.query.count(), 2)
        self.assertEqual(db.session.get(Issue, first.id).group_id, db.session.get(Issue, second.id).group_id)
        separate = self.client.patch(f'/api/admin/issues/{second.id}/group', headers=self.admin_headers, json={})
        self.assertEqual(separate.status_code, 200)
        self.assertEqual(Issue.query.count(), 2)
        self.assertIsNone(db.session.get(Issue, second.id).group_id)

    def test_citizen_verification_is_owner_only_and_appended_to_history(self):
        issue = self.issue(status='completed', verification_status='pending')
        denied = self.client.post(f'/api/issues/{issue.id}/verification', headers=self.other_headers,
                                  json={'verification': 'resolved'})
        self.assertEqual(denied.status_code, 403)
        accepted = self.client.post(f'/api/issues/{issue.id}/verification', headers=self.citizen_headers,
                                    json={'verification': 'still_unresolved', 'note': 'Water is still pooling here.'})
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json['issue']['verification_status'], 'unresolved')
        self.assertEqual(accepted.json['issue']['status'], 'completed')
        self.assertEqual(accepted.json['issue']['history'][-1]['event_type'], 'citizen_disputed')

    def test_assigned_staff_translation_preserves_original_description(self):
        original = 'குழாய் அருகே நீர் கசிகிறது'
        issue = self.issue(description=original, description_language='ta')
        response = self.client.patch(f'/api/issues/{issue.id}/translation', headers=self.officer_headers,
                                     json={'translation': 'Water is leaking near the pipe.'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['original_description'], original)
        self.assertEqual(db.session.get(Issue, issue.id).description, original)
        self.assertEqual(db.session.get(Issue, issue.id).admin_translation, 'Water is leaking near the pipe.')

    def test_admin_mapping_overrides_existing_department_suggestion(self):
        self.assertEqual(recommended_department('Pothole / Road Damage'), 'Public Works Department')
        response = self.client.put('/api/admin/department-routes', headers=self.admin_headers,
                                   json={'category': 'Pothole / Road Damage', 'department': 'Roads and Infrastructure'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(recommended_department('Pothole / Road Damage'), 'Roads and Infrastructure')

    def test_ai_model_status_does_not_claim_missing_weights(self):
        response = self.client.get('/api/ai/model-info')
        self.assertEqual(response.status_code, 200)
        self.assertIsInstance(response.json['model']['available'], bool)
        if not response.json['model']['available']:
            self.assertIsNone(response.json['model']['evaluation'])
            self.assertIn('No trained', response.json['model']['message'])


if __name__ == '__main__':
    unittest.main()
