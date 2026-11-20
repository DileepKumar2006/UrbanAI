import os
import unittest
from datetime import datetime
from io import BytesIO

import werkzeug
from PIL import Image

if not hasattr(werkzeug, '__version__'):
    werkzeug.__version__ = '3-compat'

os.environ.update({'DATABASE_URL': 'sqlite://', 'URBANPULSE_ADMIN_KEY': 'test-admin-key'})

from app import (app, db, AIAnalysis, Issue, RoadConditionObservation, RoadImage, RoadInspection,
                 RoadMaintenanceRecord, RoadSection, RoadTrafficObservation, RoadWeatherObservation,
                 User, create_access_token, limiter)


class RoadIntelligenceWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        app.config.update(TESTING=True, SQLALCHEMY_DATABASE_URI='sqlite://', RATELIMIT_ENABLED=False)
        limiter.enabled = False
        os.environ['URBANPULSE_ADMIN_KEY'] = 'test-admin-key'

    def setUp(self):
        self.context = app.app_context()
        self.context.push()
        db.drop_all()
        db.create_all()
        self.client = app.test_client()
        self.admin = {'X-Admin-Key': 'test-admin-key'}
        self.section = self.client.post('/api/road-sections', headers=self.admin, json={
            'section_code': 'RS-TEST-01', 'road_name': 'Survey Road', 'ward_zone': 'Ward 1', 'source': 'Road registry',
        }).get_json()['item']

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    def test_road_management_is_staff_only_and_workers_cannot_write(self):
        self.assertEqual(self.client.get('/api/road-sections').status_code, 403)
        self.assertEqual(self.client.post('/api/road-sections', json={'section_code': 'X', 'road_name': 'Road'}).status_code, 403)
        worker = User(username='road-worker', email='road-worker@example.test', password_hash='hash', role='worker', department='Roads')
        db.session.add(worker)
        db.session.commit()
        token = create_access_token(identity=str(worker.id))
        response = self.client.get('/api/road-sections', headers={'Authorization': f'Bearer {token}'})
        self.assertEqual(response.status_code, 403)
        officer = User(username='road-officer', email='road-officer@example.test', password_hash='hash', role='department_officer', department='Roads')
        db.session.add(officer)
        db.session.commit()
        officer_token = create_access_token(identity=str(officer.id))
        allowed = self.client.get('/api/road-sections', headers={'Authorization': f'Bearer {officer_token}'})
        self.assertEqual(allowed.status_code, 200)

    def test_section_validation_coordinates_and_duplicate_code(self):
        invalid = self.client.post('/api/road-sections', headers=self.admin, json={
            'section_code': 'RS-INVALID', 'road_name': 'Incomplete coordinates', 'latitude': 12.5,
        })
        self.assertEqual(invalid.status_code, 400)
        duplicate = self.client.post('/api/road-sections', headers=self.admin, json={
            'section_code': 'RS-TEST-01', 'road_name': 'Duplicate',
        })
        self.assertEqual(duplicate.status_code, 409)
        updated = self.client.patch(f"/api/road-sections/{self.section['id']}", headers=self.admin, json={
            'road_name': 'Survey Road North', 'latitude': 12.5, 'longitude': 77.6,
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()['item']['road_name'], 'Survey Road North')
        self.assertEqual(updated.get_json()['item']['latitude'], 12.5)

    def test_csv_import_is_validated_and_all_or_nothing(self):
        invalid = self.client.post('/api/road-sections/import', headers=self.admin, data={
            'file': (BytesIO(b'section_code,road_name\nRS-NEW,New Road\nRS-NEW,Duplicate Road\n'), 'roads.csv'),
        }, content_type='multipart/form-data')
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(RoadSection.query.count(), 1)
        imported = self.client.post('/api/road-sections/import', headers=self.admin, data={
            'file': (BytesIO(b'section_code,road_name,ward_zone,length_meters,source\nRS-NEW,New Road,Ward 2,450,City road register\n'), 'roads.csv'),
        }, content_type='multipart/form-data')
        self.assertEqual(imported.status_code, 201)
        self.assertEqual(imported.get_json()['created'], 1)
        self.assertEqual(imported.get_json()['items'][0]['length_meters'], 450)
        self.assertEqual(imported.get_json()['items'][0]['source'], 'City road register')

    def test_condition_weather_traffic_inspection_and_maintenance_keep_sources(self):
        section_id = self.section['id']
        self.assertEqual(self.client.post(f'/api/road-sections/{section_id}/conditions', headers=self.admin, json={
            'observed_at': '2026-11-20T12:00:00Z', 'condition': 'minor_cracking', 'severity': 2, 'source': 'Field survey A',
        }).status_code, 201)
        self.assertEqual(self.client.post(f'/api/road-sections/{section_id}/weather', headers=self.admin, json={
            'observed_at': '2026-11-19T10:00:00Z', 'rainfall_mm': 12.4, 'source': 'Station 42',
        }).status_code, 201)
        self.assertEqual(self.client.post(f'/api/road-sections/{section_id}/traffic', headers=self.admin, json={
            'observed_at': '2026-11-19T10:00:00Z', 'interval_minutes': 30, 'vehicle_count': 88, 'source': 'Manual vehicle count',
        }).status_code, 201)
        self.assertEqual(self.client.post(f'/api/road-sections/{section_id}/inspections', headers=self.admin, json={
            'inspected_at': '2026-11-18T10:00:00Z', 'findings': 'Surface inspected.', 'source': 'Work order 123',
        }).status_code, 201)
        self.assertEqual(self.client.post(f'/api/road-sections/{section_id}/maintenance', headers=self.admin, json={
            'maintenance_date': '2026-10-18T10:00:00Z', 'repair_type': 'Patch repair', 'source': 'Historical work order 99',
        }).status_code, 201)
        detail = self.client.get(f'/api/road-sections/{section_id}', headers=self.admin).get_json()['item']
        self.assertEqual(detail['record_counts'], {'conditions': 1, 'inspections': 1, 'weather': 1, 'traffic': 1, 'maintenance': 1, 'images': 0})
        self.assertEqual(detail['weather_observations'][0]['source'], 'Station 42')
        self.assertIsNone(detail['weather_observations'][0]['temperature_c'])
        empty_weather = self.client.post(f'/api/road-sections/{section_id}/weather', headers=self.admin, json={
            'observed_at': '2026-11-19T10:00:00Z', 'source': 'No values',
        })
        self.assertEqual(empty_weather.status_code, 400)

    def test_road_image_is_validated_private_and_accessible_by_signed_url(self):
        photo = BytesIO()
        Image.new('RGB', (8, 8), color='gray').save(photo, format='JPEG')
        photo.seek(0)
        response = self.client.post(f"/api/road-sections/{self.section['id']}/images", headers=self.admin, data={
            'source': 'Field inspection', 'image': (photo, 'surface.jpg', 'image/jpeg'),
        }, content_type='multipart/form-data')
        self.assertEqual(response.status_code, 201)
        image_url = response.get_json()['item']['image_url']
        self.assertEqual(self.client.get(image_url).status_code, 200)
        self.assertEqual(self.client.get(f"/api/road-sections/{self.section['id']}/images/1").status_code, 403)

    def test_road_tables_are_separate_from_existing_complaint_tables(self):
        issue = Issue(issue_type='pothole', title='Existing report', latitude=1.0, longitude=2.0)
        db.session.add(issue)
        db.session.flush()
        db.session.add(RoadConditionObservation(road_section_id=self.section['id'], observed_at=datetime.utcnow(),
                                                condition='healthy', source='Recorded inspection'))
        db.session.add(RoadInspection(road_section_id=self.section['id'], inspected_at=datetime.utcnow(),
                                      findings='Condition recorded.', source='Recorded inspection'))
        db.session.add(RoadWeatherObservation(road_section_id=self.section['id'], observed_at=datetime.utcnow(), source='Provider'))
        db.session.add(RoadTrafficObservation(road_section_id=self.section['id'], observed_at=datetime.utcnow(), source='Counter'))
        db.session.add(RoadMaintenanceRecord(road_section_id=self.section['id'], maintenance_date=datetime.utcnow(),
                                             repair_type='Recorded repair', source='Work order'))
        db.session.add(RoadImage(road_section_id=self.section['id'], filename='x.jpg', original_name='x.jpg',
                                 mimetype='image/jpeg', content=b'x', source='Inspection'))
        db.session.add(AIAnalysis(issue_id=issue.id, image_url='/api/uploads/x.jpg', image_sha256='x' * 64))
        db.session.commit()
        self.assertEqual(Issue.query.count(), 1)
        self.assertEqual(AIAnalysis.query.count(), 1)
        self.assertEqual(RoadSection.query.count(), 1)


if __name__ == '__main__':
    unittest.main()
