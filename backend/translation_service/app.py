"""Private IndicTrans2 English-translation adapter for Nagrik complaint text."""

import hmac
import os
import threading
from functools import lru_cache

from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()
app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 24 * 1024
LANGUAGES = {'hi', 'kn', 'ta'}
MODEL_ID = os.getenv(
    'TRANSLATION_MODEL_ID', 'vijaysarathy/indictrans2-indic-en-multilingual-ft'
)
MODEL_REVISION = os.getenv(
    'TRANSLATION_MODEL_REVISION', 'c90f7f5a41cfb38c425d0a7443e2b9a41305b38a'
)
LANGUAGE_CODES = {'hi': 'hin_Deva', 'kn': 'kan_Knda', 'ta': 'tam_Taml'}
_model_lock = threading.Lock()


@lru_cache(maxsize=1)
def load_translator():
    """Load the pinned, public multilingual checkpoint once."""
    import torch
    from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    from IndicTransToolkit.processor import IndicProcessor

    tokenizer = AutoTokenizer.from_pretrained(
        MODEL_ID, revision=MODEL_REVISION, trust_remote_code=True
    )
    model = AutoModelForSeq2SeqLM.from_pretrained(
        MODEL_ID, revision=MODEL_REVISION, trust_remote_code=True,
        dtype=torch.float32, low_cpu_mem_usage=False,
    )
    model.to('cpu')
    model.eval()
    return torch, tokenizer, model, IndicProcessor(inference=True)


def translate_text(text, source):
    if source not in LANGUAGES:
        raise ValueError('Unsupported source language.')
    torch, tokenizer, model, processor = load_translator()
    prepared = processor.preprocess_batch(
        [text.strip()], src_lang=LANGUAGE_CODES[source], tgt_lang='eng_Latn'
    )
    encoded = tokenizer(
        prepared, truncation=True, max_length=256, padding=True,
        return_tensors='pt', return_attention_mask=True,
    )
    with torch.inference_mode():
        output = model.generate(
            **encoded, use_cache=False, num_beams=5, max_length=256,
            num_return_sequences=1,
        )
    decoded = tokenizer.batch_decode(
        output, skip_special_tokens=True, clean_up_tokenization_spaces=True
    )
    processed = processor.postprocess_batch(decoded, lang='eng_Latn')
    return processed[0].strip() if processed and processed[0].strip() else None


@app.get('/health')
def health():
    return jsonify({
        'status': 'ok', 'model': MODEL_ID, 'revision': MODEL_REVISION,
        'loaded': load_translator.cache_info().currsize > 0,
    })


@app.post('/translate')
def translate():
    expected = os.getenv('TRANSLATION_API_KEY', '').strip()
    supplied = request.headers.get('Authorization', '')
    if not expected or not hmac.compare_digest(supplied, f'Bearer {expected}'):
        return jsonify({'error': 'Unauthorized'}), 401
    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({'error': 'A JSON object is required.'}), 400
    text = body.get('q')
    source = str(body.get('source', '')).lower()
    target = str(body.get('target', '')).lower()
    if source not in LANGUAGES or target != 'en':
        return jsonify({'error': 'Only Hindi, Kannada, or Tamil to English is supported.'}), 400
    if not isinstance(text, str) or not text.strip() or len(text) > 5000:
        return jsonify({'error': 'Text must contain between 1 and 5000 characters.'}), 400
    try:
        with _model_lock:
            translated = translate_text(text, source)
    except Exception:
        app.logger.exception('IndicTrans2 translation failed')
        return jsonify({'error': 'Translation unavailable.'}), 503
    if not translated:
        return jsonify({'error': 'Translation returned no text.'}), 503
    return jsonify({'translatedText': translated})


if __name__ == '__main__':
    # Local-only by default. For containers, expose through a private network.
    app.run(host=os.getenv('TRANSLATION_BIND', '127.0.0.1'),
            port=int(os.getenv('TRANSLATION_PORT', '5001')))
