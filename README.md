# UrbanPulse

UrbanPulse is a React and Flask civic issue reporting app. It supports pothole and waste photo reports, persistent report history, department status updates, before and after completion photos, and optional Telegram delivery.

## Run locally

1. In `backend`, create/activate a Python environment and install `requirements.txt`.
2. Copy `backend/.env.example` to `backend/.env`. For a local SQLite database set `DATABASE_URL=sqlite:///urban_ai.db`; the database file is stored under `backend` when Flask is started from that directory. Set a private `URBANPULSE_ADMIN_KEY`. Telegram variables can remain empty until a bot and group are configured.
3. Start the backend from `backend`: `python app.py` (port 5000).
4. In another terminal, run `npm install` and `npm start` from `frontend` (port 3000).
5. Open Settings in UrbanPulse and enter the same `URBANPULSE_ADMIN_KEY` to enable status and completion actions in that browser session.

The frontend can point to another backend with `REACT_APP_API_URL` before starting or building it. Photo capture uses the browser camera picker where supported; otherwise users can choose a local image. Photo uploads are citizen initiated and do not perform automatic monitoring or AI detection.

## Telegram setup

Create a bot with Telegram's BotFather, add it to the target administrator group, and set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` only in the backend environment. Restart the backend. The Settings screen reports whether the values are configured and displays per-event delivery state. A delivery is considered successful only when Telegram's API accepts the message; the app records `sent`, `failed`, or `not_configured`. New report and completion events are deduplicated per report/event. Do not put the bot token in frontend variables.

## Data and deployment notes

Reports, status, completion updates, and Telegram delivery attempts use the existing SQLAlchemy database. Uploaded images are stored in `backend/uploads` and referenced by URL in the report. For a hosted deployment, attach durable storage for that directory or move uploads to a persistent object store; ephemeral application filesystems can lose uploaded bytes on redeploy. Configure a reachable database and the admin key in the deployment environment. The current Vercel rewrite still contains a placeholder Render service URL and must be set to the deployed backend before publishing.

The three supplied road photographs are in `frontend/public/reference-photos` and appear only as reference examples; they are not fabricated reports. The database is no longer seeded with sample issue records.
