import os
import unittest

import werkzeug

if not hasattr(werkzeug, '__version__'):
    werkzeug.__version__ = '3-compat'

os.environ.setdefault('DATABASE_URL', 'sqlite://')
os.environ.setdefault('URBANPULSE_ADMIN_KEY', 'test-map-admin')

from app import Issue, User, app, db, limiter, create_access_token


class ComplaintMapApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite://', RATELIMIT_ENABLED=False)
        limiter.enabled = False

    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        db.drop_all(); db.create_all()
        self.client = app.test_client()
        os.environ['URBANPULSE_ADMIN_KEY'] = 'test-map-admin'

    def tearDown(self):
        db.session.remove(); db.drop_all(); self.context.pop()

    def create_issue(self, *, latitude=12.34567, longitude=77.65432,
                     category='Pothole / Road Damage', status='pending', severity=5,
                     department='Roads Department', created_at=None):
        issue = Issue(
            issue_type='pothole' if category == 'Pothole / Road Damage' else 'civic',
            title=category, confirmed_category=category, description='Detailed private location description',
            latitude=latitude, longitude=longitude, geotag_address='Private home address',
            area_name='Ward 8', severity=severity, status=status,
            assigned_department=department, created_at=created_at,
        )
        db.session.add(issue); db.session.commit()
        return issue

    def test_public_markers_are_coarse_and_omit_private_details(self):
        self.create_issue()
        response = self.client.get('/api/map/issues')
        self.assertEqual(response.status_code, 200)
        item = response.get_json()['items'][0]
        self.assertEqual((item['latitude'], item['longitude']), (12.35, 77.65))
        self.assertTrue(item['location_approximate'])
        self.assertEqual(item['short_description'], '')
        self.assertIsNone(item['assigned_department'])
        self.assertFalse(item['can_open_details'])

    def test_public_bounds_query_uses_masked_coordinates_not_exact_location(self):
        self.create_issue()
        probe = self.client.get('/api/map/issues?bounds=12.3456,77.6540,12.3458,77.6545')
        self.assertEqual(probe.status_code, 200)
        self.assertEqual(probe.get_json()['total'], 0)
        approximate_view = self.client.get('/api/map/issues?bounds=12.349,77.649,12.351,77.651')
        self.assertEqual(approximate_view.get_json()['total'], 1)

    def test_admin_receives_exact_location_and_can_filter_category_status_priority_department_dates(self):
        self.create_issue(category='Road Obstruction', status='in_progress', severity=9,
                          department='Roads Department')
        response = self.client.get(
            '/api/map/issues?category=Road%20Obstruction&status=in_progress&priority=high&department=roads%20department&date_from=2020-01-01',
            headers={'X-Admin-Key': 'test-map-admin'},
        )
        payload = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload['total'], 1)
        item = payload['items'][0]
        self.assertEqual((item['latitude'], item['longitude']), (12.34567, 77.65432))
        self.assertFalse(item['location_approximate'])
        self.assertEqual(item['assigned_department'], 'Roads Department')
        self.assertEqual(item['priority'], 'high')
        self.assertTrue(item['can_open_details'])
        self.assertEqual(payload['departments'], ['Roads Department'])
        self.assertEqual(payload['hotspots'][0]['count'], 1)

    def test_assigned_department_user_gets_precise_locations_only_for_their_queue(self):
        self.create_issue(department='Roads Department')
        self.create_issue(department='Water Department', category='Water Leakage')
        officer = User(username='road_officer', email='road@example.test', password_hash='hash',
                       role='department_officer', department='Roads Department')
        db.session.add(officer); db.session.commit()
        token = create_access_token(identity=str(officer.id))
        payload = self.client.get('/api/map/issues', headers={'Authorization': f'Bearer {token}'}).get_json()
        self.assertTrue(payload['precise_coordinates'])
        self.assertEqual(payload['total'], 1)
        self.assertEqual(payload['items'][0]['latitude'], 12.34567)
        self.assertTrue(payload['items'][0]['can_open_details'])

    def test_missing_and_invalid_coordinates_are_not_returned_as_markers(self):
        self.create_issue(latitude=0, longitude=0)
        self.create_issue(latitude=200, longitude=77)
        payload = self.client.get('/api/map/issues').get_json()
        self.assertEqual(payload['items'], [])
        self.assertEqual(payload['total'], 0)
        self.assertEqual(payload['missing_coordinates'], 2)

    def test_pagination_and_empty_filter_results(self):
        for _ in range(3):
            self.create_issue()
        first = self.client.get('/api/map/issues?limit=1&offset=0').get_json()
        second = self.client.get('/api/map/issues?limit=1&offset=1').get_json()
        self.assertEqual(len(first['items']), 1)
        self.assertTrue(first['has_more'])
        self.assertNotEqual(first['items'][0]['id'], second['items'][0]['id'])
        empty = self.client.get('/api/map/issues?category=Broken%20Streetlight').get_json()
        self.assertEqual(empty['items'], [])
        self.assertEqual(empty['total'], 0)

    def test_public_regular_issue_api_no_longer_exposes_department_assignment(self):
        self.create_issue()
        item = self.client.get('/api/issues').get_json()['items'][0]
        self.assertNotIn('assigned_department', item)
        self.assertNotIn('latitude', item)
        self.assertNotIn('geotag_address', item)


if __name__ == '__main__':
    unittest.main()
