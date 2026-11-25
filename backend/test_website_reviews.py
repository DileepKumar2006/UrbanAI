import os
import unittest

import werkzeug
if not hasattr(werkzeug, '__version__'):
    werkzeug.__version__ = '3-compat'

os.environ.setdefault('DATABASE_URL', 'sqlite://')
os.environ.setdefault('URBANPULSE_ADMIN_KEY', 'test-admin-key')
os.environ.setdefault('RESEND_API_KEY', '')

from app import AuditLog, User, WebsiteReview, app, create_access_token, db, limiter
from werkzeug.security import generate_password_hash


class WebsiteReviewWorkflowTest(unittest.TestCase):
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
        self.citizen = User(username='resident', email='resident@example.test',
                            password_hash=generate_password_hash('a-secure-test-password'), role='citizen')
        self.second_citizen = User(username='neighbor', email='neighbor@example.test',
                                   password_hash=generate_password_hash('another-secure-password'), role='citizen')
        self.admin = User(username='moderator', email='moderator@example.test',
                          password_hash=generate_password_hash('moderator-secure-password'), role='admin')
        self.worker = User(username='worker', email='worker@example.test',
                           password_hash=generate_password_hash('worker-secure-password'), role='worker',
                           department='roads')
        db.session.add_all([self.citizen, self.second_citizen, self.admin, self.worker])
        db.session.commit()
        self.citizen_headers = self.headers_for(self.citizen)
        self.other_headers = self.headers_for(self.second_citizen)
        self.admin_headers = self.headers_for(self.admin)
        self.worker_headers = self.headers_for(self.worker)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.context.pop()

    @staticmethod
    def headers_for(user):
        with app.app_context():
            token = create_access_token(identity=str(user.id), additional_claims={'role': user.role})
        return {'Authorization': f'Bearer {token}'}

    @staticmethod
    def payload(rating=5, text='A clear and helpful civic reporting platform.'):
        return {'rating': rating, 'review_text': text}

    def test_public_listing_has_real_empty_summary_and_no_private_data(self):
        response = self.client.get('/api/reviews')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json['average_rating'])
        self.assertEqual(response.json['rating_distribution'], {str(i): 0 for i in range(1, 6)})
        self.assertEqual(response.json['items'], [])
        self.assertEqual(response.json['total_reviews'], 0)

    def test_authentication_role_validation_and_duplicate_prevention(self):
        self.assertEqual(self.client.post('/api/reviews/mine', json=self.payload()).status_code, 401)
        self.assertEqual(self.client.post('/api/reviews/mine', headers=self.worker_headers,
                                          json=self.payload()).status_code, 403)
        for invalid in (0, 6, True, '5'):
            response = self.client.post('/api/reviews/mine', headers=self.citizen_headers,
                                        json=self.payload(rating=invalid))
            self.assertEqual(response.status_code, 400)
        response = self.client.post('/api/reviews/mine', headers=self.citizen_headers,
                                    json=self.payload())
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json['review']['moderation_status'], 'pending')
        self.assertNotIn('email', response.json['review'])
        duplicate = self.client.post('/api/reviews/mine', headers=self.citizen_headers,
                                     json=self.payload(4, 'Trying to create a second review.'))
        self.assertEqual(duplicate.status_code, 409)
        self.assertEqual(WebsiteReview.query.count(), 1)

    def test_edit_keeps_single_review_and_returns_it_to_moderation(self):
        created = self.client.post('/api/reviews/mine', headers=self.citizen_headers,
                                   json=self.payload())
        review_id = created.json['review']['id']
        self.client.patch(f'/api/admin/reviews/{review_id}/moderation', headers=self.admin_headers,
                          json={'moderation_status': 'published'})
        edited = self.client.put('/api/reviews/mine', headers=self.citizen_headers,
                                 json=self.payload(3, 'The updated experience is improving.'))
        self.assertEqual(edited.status_code, 200)
        self.assertEqual(edited.json['review']['id'], review_id)
        self.assertEqual(edited.json['review']['rating'], 3)
        self.assertEqual(edited.json['review']['moderation_status'], 'pending')
        self.assertEqual(WebsiteReview.query.count(), 1)
        self.assertGreaterEqual(AuditLog.query.filter_by(action='website_review_edited').count(), 1)

    def test_only_published_reviews_affect_public_statistics_and_filters(self):
        first = self.client.post('/api/reviews/mine', headers=self.citizen_headers,
                                 json=self.payload(5, 'The city updates are clear.'))
        second = self.client.post('/api/reviews/mine', headers=self.other_headers,
                                  json=self.payload(3, 'The map needs a little more detail.'))
        for response in (first, second):
            self.client.patch(f"/api/admin/reviews/{response.json['review']['id']}/moderation",
                              headers=self.admin_headers, json={'moderation_status': 'published'})
        listing = self.client.get('/api/reviews?rating=5&sort=highest&search=updates')
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json['total_reviews'], 2)
        self.assertEqual(listing.json['average_rating'], 4.0)
        self.assertEqual(listing.json['rating_distribution']['5'], 1)
        self.assertEqual(listing.json['pagination']['total'], 1)
        self.assertNotIn('email', listing.json['items'][0])
        self.assertEqual(self.client.get('/api/reviews?sort=unknown').status_code, 400)

    def test_moderation_is_admin_only_and_audited(self):
        created = self.client.post('/api/reviews/mine', headers=self.citizen_headers,
                                   json=self.payload())
        review_id = created.json['review']['id']
        self.assertEqual(self.client.patch(f'/api/admin/reviews/{review_id}/moderation',
                                           headers=self.worker_headers,
                                           json={'moderation_status': 'published'}).status_code, 403)
        response = self.client.patch(f'/api/admin/reviews/{review_id}/moderation',
                                     headers=self.admin_headers,
                                     json={'moderation_status': 'published'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['review']['moderation_status'], 'published')
        self.assertEqual(self.client.get('/api/admin/reviews', headers=self.admin_headers).status_code, 200)
        self.assertEqual(self.client.get('/api/admin/reviews').status_code, 403)
        self.assertEqual(AuditLog.query.filter_by(action='website_review_moderated').count(), 1)

    def test_citizen_can_delete_only_their_review(self):
        created = self.client.post('/api/reviews/mine', headers=self.citizen_headers,
                                   json=self.payload())
        self.assertEqual(self.client.delete('/api/reviews/mine', headers=self.other_headers).status_code, 404)
        self.assertEqual(self.client.delete('/api/reviews/mine', headers=self.citizen_headers).status_code, 200)
        self.assertEqual(WebsiteReview.query.count(), 0)
        self.assertEqual(self.client.get('/api/reviews/mine', headers=self.citizen_headers).json['review'], None)


if __name__ == '__main__':
    unittest.main()
