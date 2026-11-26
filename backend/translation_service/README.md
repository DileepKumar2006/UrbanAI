# Private IndicTrans2 translation adapter

This optional service translates Hindi, Kannada, and Tamil complaint text into
English using a public multilingual IndicTrans2 fine-tune. It implements
the request/response shape already used by `backend/app.py`; the original
citizen text remains unchanged. Translation runs only when a citizen opts in
and the main app is configured with `ADMIN_LANGUAGE=en`.

## Setup

1. Create a separate Python 3.10+ environment (or use the Dockerfile), then run commands from this folder.
2. Copy `.env.example` to `.env`, generate `TRANSLATION_API_KEY`, and keep this service
   on localhost/private networking. It is not intended for public exposure.
3. Install the CPU PyTorch wheel, then the rest of `requirements.txt`, and run
   `gunicorn --workers 1 --threads 2 --timeout 180 --bind 127.0.0.1:5001 app:app`:

   ```bash
   python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
   python -m pip install -r requirements.txt gunicorn
   ```

   The first translation downloads and initializes about 624 MB of model
   weights; allow network access and sufficient disk/RAM. CPU translation may
   take several seconds.
4. In the main backend `.env`, set:

   ```text
   ADMIN_LANGUAGE=en
   TRANSLATION_SERVICE_URL=http://127.0.0.1:5001/translate
   TRANSLATION_API_KEY=<same random value>
   TRANSLATION_TIMEOUT_SECONDS=45
   ```

For containers, use a private network and set `TRANSLATION_BIND=0.0.0.0`; do
not publish port 5001 to the public internet. Use a persistent `HF_HOME` volume
so model weights are not downloaded after each restart.

## Contract and limits

`POST /translate` accepts `{ "q": "...", "source": "hi|kn|ta", "target": "en" }`
with `Authorization: Bearer <TRANSLATION_API_KEY>`. Success returns
`{ "translatedText": "..." }`. `/health` reports whether the model is loaded.
The API key is mandatory; text is limited to 5,000 characters and model input
to 256 tokens. Machine translation can be inaccurate and should be reviewed by
staff. If the adapter is unavailable, complaint submission continues with the
original text preserved.

The model is pinned to revision `c90f7f5a41cfb38c425d0a7443e2b9a41305b38a`.
Its public model card lists MIT licensing, Hindi/Kannada/Tamil support, and
evaluation results on FLORES-200. This is a third-party fine-tune; its published
results are not validation for Nagrik complaint text. The model and toolkit
downloaded successfully in WSL, but initial local inference failed with a
CPU/meta tensor mismatch. The loader now disables low-memory meta initialization
for CPU inference, following the model card's CPU-compatible load pattern. This
fix could not be rerun after the environment approval limit was reached. The
service is not configured in production. Test real outputs for all three
languages and have fluent reviewers validate civic terminology before enabling
it.
