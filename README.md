# UrbanPulse

UrbanPulse is a React and Flask civic issue reporting app. It supports civic image reports, persistent report history, department status updates, before and after completion photos, email notifications through Resend, and optional AI category suggestions. AI suggestions do not override citizen confirmation or manually assigned departments.

## Run locally

1. In `backend`, create/activate a Python environment and install `requirements.txt`.
2. Copy `backend/.env.example` to `backend/.env`. For local SQLite set `DATABASE_URL=sqlite:///urban_ai.db`. Set private `URBANPULSE_ADMIN_KEY` and a stable random `JWT_SECRET_KEY` (generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`). Email is recorded as `not_configured` until valid Resend settings are provided; the complaint itself is still saved.
3. From `backend`, initialize or update the schema with `flask --app app db upgrade` before starting the backend. This is an explicit migration command; the web process no longer creates or changes tables at request time.
4. Start the backend from `backend`: `python app.py` (port 5000).
5. In another terminal, run `npm install` and `npm start` from `frontend` (port 3000).
6. Create or sign in to an account in Settings. Citizens can view their own reports; administrators and department officers use their account roles for authorized actions. The legacy `URBANPULSE_ADMIN_KEY` remains available for existing administrators.

The frontend can point to another backend with `REACT_APP_API_URL` before starting or building it. Photo capture uses the browser camera picker where supported; otherwise users can choose a local image. Photo uploads are citizen initiated and do not perform automatic monitoring.

### AI image classification (optional)

The complaint form requests an advisory prediction after a photo is selected. It shows confidence, model version, and review state; the user still selects the confirmed category, and AI downtime never blocks reporting. The backend signs predictions against the image hash, stores predictions separately from the confirmed complaint category, and exposes an administrator review queue. The confirmed category produces a recommended department hint; it does not automatically assign the complaint. Existing administrator/department assignment behavior remains manual because this branch does not contain the Phase 2 workload-routing engine.

The current repository contains a MobileNetV3-Small transfer-learning pipeline, but no trained UrbanAI weights or evaluation metrics. Install `backend/requirements-ai.txt` for optional local ML dependencies. Follow [the dataset preparation guide](backend/ai/dataset/README.md), gather reviewed/licensed labeled examples for each of the seven categories, prepare deterministic splits, then train with `python -m ai.train --data artifacts/dataset-splits --output ai/models` from `backend`. Actual evaluation metrics are saved to `backend/ai/models/evaluation.json`; model weights are git-ignored. Do not use supplied reference photos as a training dataset without explicit permission and proper labels.

The classification endpoint is `POST /api/ai/classify` with multipart field `image`. Reports are created with `POST /api/issues` and may include the signed `prediction_token`; `PATCH /api/issues/<id>/category` records a report-owner/admin correction; administrators list uncertain analyses at `GET /api/admin/ai-review` and review at `PATCH /api/issues/<id>/ai-review` with `confirmed_category`.

Apply the additive Phase 3 schema with `flask --app app db upgrade` from `backend` after backing up and testing a restored copy of an existing production database. It adds `issues.confirmed_category` and `ai_analyses`; it does not delete complaint records. Do not deploy this migration until a database backup and restore have been verified.

### Interactive complaint map

The dashboard's **City Map** view uses React Leaflet, Leaflet, OpenStreetMap tiles, and clustered actual results from `GET /api/map/issues`. The map API supports `category`, `status`, `priority`, `department`, `search`, `date_from`, `date_to`, `bounds=south,west,north,east`, `limit`, and `offset`. Results cap at 500 per page; the UI loads 300 at a time and offers Load more. `GET /api/map/issues` has no database migration requirements.

Anonymous map coordinates and viewport filtering use the same 2-decimal precision (roughly 1 km in the UrbanAI deployment area) to avoid leaking exact points through repeated bounding-box requests. Administrators, report owners, and assigned department staff receive precise points only for reports they can access. Public responses omit description, address, department and AI suggestion. Reports with missing, zero, or invalid coordinates stay in the dashboard but are not placed on the map. Density cells are computed from filtered database rows and indicate complaint concentration, not severity.

OpenStreetMap tiles are requested only for the active viewport, with visible contributor attribution and a map-issue link. OSM's standard tile servers are best-effort and have a usage policy; choose a suitable OSM-derived provider and set `REACT_APP_OSM_TILE_URL` and `REACT_APP_OSM_TILE_ATTRIBUTION` if expected traffic exceeds a modest preview. The URL defaults to `https://tile.openstreetmap.org/{z}/{x}/{y}.png`; the application does not bulk-download or prefetch tiles. See the [React Leaflet installation guide](https://react-leaflet.js.org/docs/v4/start-installation/) and [OpenStreetMap tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

### Database migrations and safe upgrades

Schema changes are managed through Flask-Migrate/Alembic. The first migration is additive: it creates only missing model tables, adds `issues.assigned_department` if absent, and creates missing query indexes. It does not delete or rewrite existing rows. To create a later migration, run `flask --app app db migrate -m "describe change"`, review its generated file, then apply it with `flask --app app db upgrade`. `flask --app app db current` shows the applied revision. Downgrading this baseline is intentionally a no-op because dropping baseline tables could erase complaints; use a reviewed later migration or database restore for rollback. Before applying a migration to production, take a provider snapshot/dump and verify restore into a separate database. No production backup has been taken or restore-tested in this session, so do not migrate the live Render database yet. Keep future changes as versioned migrations; do not use request-time `create_all()` to upgrade production.

The web container does not run migrations automatically. Initialize a new database with `flask --app app db upgrade` before serving traffic. For an existing database, snapshot and restore-test it first, then run the same command during a controlled maintenance window. To roll back an application release, redeploy the previous code; use a database restore for destructive migration rollback rather than dropping baseline tables.

`CORS_ORIGINS` is a comma-separated backend setting. Defaults permit local React development origins; add the exact production UI origin only if frontend and API are hosted separately. `RATELIMIT_STORAGE_URI` defaults to process-local memory for a single instance; configure Redis before running multiple web instances so submission limits are shared.

After migrating, create an administrator interactively with `flask --app app create-admin`. This creates a hashed-password account; authenticate with `POST /api/auth/login` to receive a role-bearing JWT. Citizen registration is available through Settings and `POST /api/auth/register`; an administrator can provision department officers through `POST /api/auth/register-department`. JWTs expire after eight hours and logout revokes the token in the database. Citizens see their own reports, department officers see reports assigned to their department, and administrators can review all reports. The dashboard retains `URBANPULSE_ADMIN_KEY` for compatibility; prefer account tokens for regular use. The placeholder demo seeding command has been removed.

Public complaint lists omit exact coordinates, street addresses, descriptions, internal assignment details, and report images. Private report images are served through short-lived signed links for the report owner, assigned department, or administrator. Important submissions and management changes are written to `audit_logs`; administrators can review them through `GET /api/admin/audit`.

### Backup and restore checklist

For local SQLite, run these from `backend` before schema work:

```powershell
python -c "import sqlite3; src=sqlite3.connect('urban_ai.db'); dst=sqlite3.connect('urban_ai_backup.db'); src.backup(dst); dst.close(); src.close()"
python -c "import sqlite3; db=sqlite3.connect('urban_ai_backup.db'); print(db.execute('PRAGMA integrity_check').fetchone()[0]); db.close()"
```

To verify a restore without touching the working database, restore the backup into a separate file and check its integrity and row counts before approving a migration. For PostgreSQL, create a provider snapshot or run `pg_dump --format=custom --file=urbanai-backup.dump "$PRODUCTION_DATABASE_URL"`; verify restoration into a separate database with `pg_restore --dbname="$RESTORE_CHECK_DATABASE_URL" urbanai-backup.dump`. Keep backup files out of Git and restrict access because they contain complaint data and photographs.

### Tests

From `backend`, run `python -m unittest discover -s . -p "test_*.py"`. From `frontend`, run `npm test -- --watchAll=false` and `npm run build`. The migration test should use a restored copy of the database, never the production database. Check that the original and migrated copy retain the same existing complaint, user, image, and notification row counts.

### Production configuration and rollout

Set `DATABASE_URL`, a long random `JWT_SECRET_KEY`, `URBANPULSE_ADMIN_KEY` for legacy clients, exact `CORS_ORIGINS`, and the Resend variables listed above in the hosting provider’s secret settings. Do not use the example values in production. Make a verified backup, apply `flask --app app db upgrade` as a controlled pre-release step, check `/api/health`, then deploy the application. The container intentionally does not auto-migrate. Keep the previous app release available for code rollback; use a database restore if a future migration cannot be safely reversed. Resend testing senders may be restricted to verified recipients; configure a verified sender domain before expecting external municipal delivery.

### GPS location on reports

On the HTTPS deployment (or `localhost`), a reporter can choose **Use current location** in the report form. The browser then asks for location permission and captures one high-accuracy GPS fix; the coordinates and the reporter-entered street or landmark are saved with the report. Location is optional, and no continuous tracking occurs. If permission is denied or the device cannot get a fix, the reporter can still submit with a manually entered location. The browser must support Geolocation and the user must grant permission for GPS capture.

## Email notifications with Resend

Configure these **backend** environment variables (never put the API key in React variables):

- `RESEND_API_KEY`: Resend API key.
- `RESEND_FROM_EMAIL`: sender using a verified domain in Resend. Resend's `onboarding@resend.dev` sender is for testing only.
- `ADMIN_EMAIL`: administrator recipient; defaults to `dk142252@gmail.com`.
- `DEPARTMENT_EMAIL`: municipal department inbox for assignment and complaint updates.
- `APP_BASE_URL`: public UrbanPulse URL used for complaint and photograph links.
- `RESEND_WEBHOOK_SECRET`: signing secret for the Resend webhook (recommended for delivery tracking).

The backend sends new complaint emails to the admin and configured department, assignment email to the department, and Work in Progress/completion emails to both. Complaint photos and after-repair photos are attached when present, and each message includes the original complaint link. Email failures do not roll back or hide complaints. Each recipient/event is logged in `email_deliveries`; Resend acceptance is recorded as `accepted`, and only a verified `email.delivered` webhook sets `delivered`. Bounces, provider failures, delayed delivery and complaints update that log as well. Administrators can inspect deliveries and retry determinate failures in Settings; retries use a stable Resend idempotency key and are limited to the provider's 24-hour idempotency window.

In Resend, verify the sender domain, create a webhook for `https://YOUR-APP-URL/api/email/webhook`, select `email.sent`, `email.delivered`, `email.delivery_delayed`, `email.failed`, `email.bounced`, and `email.complained`, then save its signing secret as `RESEND_WEBHOOK_SECRET`. Resend documents idempotency keys for duplicate-safe sends and webhooks for delivery events: [Idempotency keys](https://resend.com/changelog/idempotency-keys), [webhooks](https://resend.com/features/webhooks).

After configuring Render environment variables, wait for deployment. Submit a fresh pothole and waste report, assign each to a department, mark one Work in Progress, and complete one with an after photo. Confirm the emails in the recipient inboxes and check for `delivered` rows in UrbanPulse Settings. Without valid Resend credentials, a verified sender and department address, and a webhook secret, live email delivery cannot be claimed.

## Data and deployment notes

Reports, status, completion updates, uploaded image bytes, email delivery records, and webhook receipts use the SQLAlchemy database. Uploaded photos therefore survive application redeploys. The Render Blueprint deploys the React frontend and Flask API together and provisions a free Postgres database for preview use. Render free Postgres databases expire after 30 days; upgrade the database before relying on the deployment for ongoing reporting. The admin key is generated by Render and can be viewed in the service environment settings. Database changes are now explicit migrations rather than request-time table creation or `ALTER TABLE` calls.

The three supplied road photographs are in `frontend/public/reference-photos` and appear only as reference examples; they are not fabricated reports. The database is no longer seeded with sample issue records.

## Deploy on Render

In Render, create a Blueprint from `DileepKumar2006/UrbanAI` and select the `master` branch. Render reads the root `render.yaml`, builds the Docker image, and creates the web service and Postgres database. The blueprint uses free plans for a no-cost preview; its database is temporary and expires after 30 days. Choose a paid Postgres plan in Render before using it for durable citizen reports. After the service is live, open the generated `onrender.com` URL and use the generated `URBANPULSE_ADMIN_KEY` from the service environment in the app's Settings page.
