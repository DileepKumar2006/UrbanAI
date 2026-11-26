import os
import unittest
from unittest.mock import patch

import app as translation_app
import werkzeug


class TranslationServiceTests(unittest.TestCase):
    def setUp(self):
        # Flask 2.3 test client still reads this attribute, removed in newer
        # Werkzeug; production service dependencies use Flask 3.1+.
        if not hasattr(werkzeug, '__version__'):
            werkzeug.__version__ = 'test'
        self.client = translation_app.app.test_client()
        self.env = patch.dict(os.environ, {'TRANSLATION_API_KEY': 'test-secret'})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def test_requires_private_api_key(self):
        response = self.client.post('/translate', json={
            'q': 'குப்பை', 'source': 'ta', 'target': 'en',
        })
        self.assertEqual(response.status_code, 401)

    def test_validates_language_and_body(self):
        headers = {'Authorization': 'Bearer test-secret'}
        unsupported = self.client.post('/translate', headers=headers, json={
            'q': 'hello', 'source': 'en', 'target': 'ta',
        })
        malformed = self.client.post('/translate', headers=headers, json=[])
        too_long = self.client.post('/translate', headers=headers, json={
            'q': 'x' * 5001, 'source': 'hi', 'target': 'en',
        })
        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(malformed.status_code, 400)
        self.assertEqual(too_long.status_code, 400)

    def test_returns_translated_text_from_model(self):
        with patch.object(translation_app, 'translate_text', return_value='Garbage is overflowing.') as translate:
            response = self.client.post('/translate', headers={
                'Authorization': 'Bearer test-secret',
            }, json={'q': 'குப்பை நிரம்பி வழிகிறது', 'source': 'ta', 'target': 'en'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'translatedText': 'Garbage is overflowing.'})
        translate.assert_called_once()

    def test_returns_unavailable_if_model_fails(self):
        with patch.object(translation_app, 'translate_text', side_effect=RuntimeError('offline')):
            response = self.client.post('/translate', headers={
                'Authorization': 'Bearer test-secret',
            }, json={'q': 'ಕಸ ತುಂಬಿದೆ', 'source': 'kn', 'target': 'en'})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json(), {'error': 'Translation unavailable.'})


if __name__ == '__main__':
    unittest.main()
