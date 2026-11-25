# Nagrik

Nagrik is a React and Flask civic issue reporting app. Your City. Your Voice. Your Nagrik. It supports civic image reports, persistent report history, department status updates, before and after completion photos, email notifications through Resend, and optional AI category suggestions. AI suggestions do not override citizen confirmation or manually assigned departments.



## Nagrik platform reviews

Citizens can maintain one platform review each; new and edited reviews enter moderation. Public statistics and listings include published reviews only and expose no citizen email or account identifier. Administrators can publish or hide reviews from the Reviews page. Complaint-resolution feedback remains separate.

Apply the additive review schema with `flask --app app db upgrade` from `backend`; migration `0004_website_reviews` follows the road-sections migration and does not remove complaint data. Review endpoints: `GET /api/reviews`, `GET|POST|PUT|DELETE /api/reviews/mine`, `GET /api/admin/reviews`, `PATCH /api/admin/reviews/<id>/moderation`, and `GET /api/public/stats`.

### Civic intelligence, language preferences, themes, and complaint tracking

The current local branch adds an additive migration, `0005_civic_intelligence`, for complaint description language, a separately stored staff translation, related-report groups, citizen resolution verification, timestamped complaint history, and configurable category-to-department overrides. Apply it with `flask --app app db upgrade` from `backend` after backing up the target database and testing a restored copy. The migration preserves existing issue rows; its downgrade is intentionally a no-op to avoid deleting citizen data.

New API operations are:

- `GET /api/ai/model-info`: reports whether a compatible trained model is available and only returns evaluation metadata when it exists.
- `POST /api/issues/duplicate-candidates`: returns candidate reports for the citizen to review; it does not reject or automatically merge a report.
- `POST /api/issues/<id>/verification`: lets the report owner record whether completed work resolved the issue.
- `PATCH /api/issues/<id>/translation`: lets authorized staff save an administrative translation without replacing the citizen's original description.
- `PATCH /api/admin/issues/<id>/group`: lets an administrator link or separate reports while retaining each report and its history.
- `GET /api/admin/duplicates/analytics`: returns duplicate/group counts from stored reports.
- `GET|PUT /api/admin/department-routes`: reads or changes category routing overrides.

Duplicate candidates currently use category, a simple description token-overlap score, photo perceptual hash when available, proximity, and a 45-day unresolved-report window. The score is a heuristic for human review, not a calibrated probability or semantic AI model. A citizen must explicitly choose whether a candidate is the same issue, and each submitted report remains independently stored. Public match results use generalized location and do not reveal citizen identity.

The complaint timeline is based on recorded history events and the original report. It includes submitted, assignment, status, completion, translation, and verification events when those are present; it does not invent missing workflow stages. Department staff can enter an administrative translation manually. No external translation provider is configured, so automatic translation is not provided. The four UI language catalogs and persisted selector currently cover shared navigation, key landing/report/status/preferences and duplicate-match strings; untranslated interface copy falls back to English and this is not yet a complete translation of every screen.

The appearance selector supports Light, Dark, and System modes, persists the choice locally, and follows OS color changes in System mode. The shared CSS includes dark theme tokens and key dashboard components, but this update has not received full manual visual review across every screen and third-party map/chart surface.

AI classification remains unavailable unless compatible trained UrbanAI model weights and verified evaluation metadata are installed. This work does not create model weights, dataset claims, confidence calibration, or performance results. The existing training pipeline now records provenance metadata in newly generated checkpoints; it does not train automatically.

## Run locally

1. In `backend`, create/activate a Python environment and install `requirements.txt`.
2. Copy `backend/.env.example` to `backend/.env`. For local SQLite set `DATABASE_URL=sqlite:///urban_ai.db`. Set private `URBANPULSE_ADMIN_KEY` and a stable random `JWT_SECRET_KEY` (generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`). Email is recorded as `not_configured` until valid Resend settings are provided; the complaint itself is still saved.
3. From `backend`, initialize or update the schema with `flask --app app db upgrade` before starting the backend. This is an explicit migration command; the web process no longer creates or changes tables at request time.
4. Start the backend from `backend`: `python app.py` (port 5000).
5. In another terminal, run `npm install` and `npm start` from `frontend` (port 3000).
6. Create or sign in to an account in Settings. Citizens can view their own reports; administrators and department officers use their account roles for authorized actions. The legacy `URBANPULSE_ADMIN_KEY` remains available for existing administrators.

The frontend can point to another backend with `REACT_APP_API_URL` before starting or building it. Photo capture uses the browser camera picker where supported; otherwise users can choose a local image. Photo uploads are citizen initiated and do not perform automatic monitoring.

### AI image classification (optional)

The complaint form requests an advisory prediction after a photo is selected. It shows confidence, model version, and review state; the user still selects the confirmed category, and AI downtime never blocks reporting. The backend signs predictions against the image hash, stores predictions separately from the confirmed complaint category, and exposes an administrator review queue. Department routing uses only the citizen-confirmed category, never an unconfirmed AI prediction. The category map in `backend/ai/categories.py` assigns a department on report creation and re-routes it when the confirmed category changes, provided the current assignment still matches the previous category's automatic route. Administrators can manually override the route. This is a static category mapping, not the Phase 2 department registry/workload engine: department CRUD, editable mappings, workload balancing, assignment history, department-specific email routing, and automatic overdue escalation are not implemented.

The current repository contains a MobileNetV3-Small transfer-learning pipeline, but no trained UrbanAI weights or evaluation metrics. Install `backend/requirements-ai.txt` for optional local ML dependencies. Follow [the dataset preparation guide](backend/ai/dataset/README.md), gather reviewed/licensed labeled examples for each of the seven categories, prepare deterministic splits, then train with `python -m ai.train --data artifacts/dataset-splits --output ai/models` from `backend`. Actual evaluation metrics are saved to `backend/ai/models/evaluation.json`; model weights are git-ignored. Do not use supplied reference photos as a training dataset without explicit permission and proper labels.

The classification endpoint is `POST /api/ai/classify` with multipart field `image`. Reports are created with `POST /api/issues` and may include the signed `prediction_token`; `PATCH /api/issues/<id>/category` records a report-owner/admin correction; administrators list uncertain analyses at `GET /api/admin/ai-review` and review at `PATCH /api/issues/<id>/ai-review` with `confirmed_category`.

Apply the additive Phase 3 schema with `flask --app app db upgrade` from `backend` after backing up and testing a restored copy of an existing production database. It adds `issues.confirmed_category` and `ai_analyses`; it does not delete complaint records. Do not deploy this migration until a database backup and restore have been verified.

### Interactive complaint map

The dashboard's **City Map** view uses React Leaflet, Leaflet, OpenStreetMap tiles, and clustered actual results from `GET /api/map/issues`. The map API supports `category`, `status`, `priority`, `department`, `search`, `date_from`, `date_to`, `bounds=south,west,north,east`, `limit`, and `offset`. Results cap at 500 per page; the UI loads 300 at a time and offers Load more. `GET /api/map/issues` has no database migration requirements.

Anonymous map coordinates and viewport filtering use the same 2-decimal precision (roughly 1 km in the UrbanAI deployment area) to avoid leaking exact points through repeated bounding-box requests. Administrators, report owners, and assigned department staff receive precise points only for reports they can access. Public responses omit description, address, department and AI suggestion. Reports with missing, zero, or invalid coordinates stay in the dashboard but are not placed on the map. Density cells are computed from filtered database rows and indicate complaint concentration, not severity.

OpenStreetMap tiles are requested only for the active viewport, with visible contributor attribution and a map-issue link. OSM's standard tile servers are best-effort and have a usage policy; choose a suitable OSM-derived provider and set `REACT_APP_OSM_TILE_URL` and `REACT_APP_OSM_TILE_ATTRIBUTION` if expected traffic exceeds a modest preview. The URL defaults to `https://tile.openstreetmap.org/{z}/{x}/{y}.png`; the application does not bulk-download or prefetch tiles. See the [React Leaflet installation guide](https://react-leaflet.js.org/docs/v4/start-installation/) and [OpenStreetMap tile usage policy](https://operations.osmfoundation.org/policies/tiles/).

### Database migrations and safe upgrades

Schema changes are managed through Flask-Migrate/Alembic. The first migration is additive: it creates only missing model tables, adds `issues.assigned_department` if absent, and creates missing query indexes. It does not delete or rewrite existing rows. To create a later migration, run `flask --app app db migrate -m "describe change"`, review its generated file, then apply it with `flask --app app db upgrade`. `flask --app app db current` shows the applied revision. Downgrading this baseline is intentionally a no-op because dropping baseline tables could erase complaints; use a reviewed later migration or database restore for rollback. Before applying a migration to production, take a provider snapshot/dump and verify restore into a separate database. No production backup has been taken or restore-tested in this session, so do not migrate the live Render database yet. Keep future changes as versioned migrations; do not use request-time `create_all()` to upgrade production.

The Docker container runs `flask --app app db upgrade` before starting Gunicorn. This keeps the API from serving requests against an older schema during a deployment. The migrations in this release are additive and preserve existing complaint rows. For local development, run the same command from `backend` before starting `python app.py`. To roll back an application release, redeploy the previous code; use a database restore for destructive migration rollback rather than dropping baseline tables. Render's current free Postgres instance does not provide exports or point-in-time restore, so this additive migration was locally rehearsed but not restore-tested against a production backup.

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
- `APP_BASE_URL`: public Nagrik URL used for complaint and photograph links.
- `RESEND_WEBHOOK_SECRET`: signing secret for the Resend webhook (recommended for delivery tracking).

The backend sends new complaint emails to the admin and configured department, assignment email to the department, and Work in Progress/completion emails to both. Registered citizens who own a report are also included for creation, assignment, progress, and completion events; anonymous reports have no owner recipient. Complaint photos and after-repair photos are attached when present, and each message includes the original complaint link. Email failures do not roll back or hide complaints. Each recipient/event is logged in `email_deliveries`; Resend acceptance is recorded as `accepted`, and only a verified `email.delivered` webhook sets `delivered`. Bounces, provider failures, delayed delivery and complaints update that log as well. Administrators can inspect deliveries and retry determinate failures in Settings; retries use a stable Resend idempotency key and are limited to the provider's 24-hour idempotency window.

In Resend, verify the sender domain, create a webhook for `https://YOUR-APP-URL/api/email/webhook`, select `email.sent`, `email.delivered`, `email.delivery_delayed`, `email.failed`, `email.bounced`, and `email.complained`, then save its signing secret as `RESEND_WEBHOOK_SECRET`. Resend documents idempotency keys for duplicate-safe sends and webhooks for delivery events: [Idempotency keys](https://resend.com/changelog/idempotency-keys), [webhooks](https://resend.com/features/webhooks).

After configuring Render environment variables, wait for deployment. Submit a fresh pothole and waste report, assign each to a department, mark one Work in Progress, and complete one with an after photo. Confirm the emails in the recipient inboxes and check for `delivered` rows in Nagrik Settings. Without valid Resend credentials, a verified sender and department address, and a webhook secret, live email delivery cannot be claimed.

## Data and deployment notes

Reports, status, completion updates, uploaded image bytes, email delivery records, and webhook receipts use the SQLAlchemy database. Uploaded photos therefore survive application redeploys. The Render Blueprint deploys the React frontend and Flask API together and provisions a free Postgres database for preview use. Render free Postgres databases expire after 30 days; upgrade the database before relying on the deployment for ongoing reporting. The admin key is generated by Render and can be viewed in the service environment settings. Database changes are now explicit migrations rather than request-time table creation or `ALTER TABLE` calls.

The three supplied road photographs are in `frontend/public/reference-photos` and appear only as reference examples; they are not fabricated reports. The database is no longer seeded with sample issue records.

## Architecture and API map

The deployed service is a single Docker image: React is built in the Node stage and served by Flask/Gunicorn alongside the JSON API. PostgreSQL is the production database; SQLAlchemy models, route handlers, email delivery, authorization, and most business logic currently live together in `backend/app.py`. `backend/ai/` contains the optional training and inference pipeline, `backend/migrations/` contains additive Alembic revisions, and `backend/test_*.py` contains API and workflow tests. `frontend/src/components/Dashboard.jsx` is the main citizen/administrator interface; `IssueMap.jsx` is the map view and `App.css` contains shared styles. Older page, route, service, and model modules remain in the repository but are not the active API implementation.

Main API routes:

- `POST /api/auth/register`, `POST /api/auth/login`, `POST /api/auth/logout`: citizen account and session lifecycle.
- `POST /api/issues`: validates and stores a report/photo, category, geotag, optional AI record, confirmed-category routing, audit event, and email delivery records.
- `GET /api/issues`: report list scoped to public, citizen, department, or administrator access.
- `PATCH /api/issues/<id>/category`, `PATCH /api/issues/<id>/ai-review`, `GET /api/admin/ai-review`: citizen correction and administrator AI review.
- `POST /api/issues/<id>/assign`, `PATCH /api/issues/<id>/status`, `POST /api/issues/<id>/complete`: department assignment, progress, and completion on the original report.
- `GET /api/map/issues`: paged/filterable map records with public coordinate masking.
- `GET /api/stats`: database-side aggregate counts, administrator-only.
- `GET /api/email/status`, `POST /api/email/retry/<id>`, `POST /api/email/webhook`: delivery tracking, controlled retry, and signed provider events.
- `GET /api/health`: database health and optional email/webhook configuration flags.

## Phase 5 integration and verification

These checks used isolated test databases and mocked Resend requests; no production complaint records or live email credentials were used:

- Backend: 25 tests passed, including registration, citizen ownership, image validation, confirmed-category assignment, administrator reassignment, citizen progress/completion email records, same-report after-photo history, public map masking, delivery webhook, duplicate prevention, failed delivery persistence, escalation authorization, and database health.
- Frontend: 5 tests passed across dashboard/account controls, citizen registration, map rendering, filters, and privacy behavior.
- Frontend production build: `npm run build` succeeded.
- Restore/migration rehearsal: a temporary copy of the local Phase 1 backup passed SQLite integrity checks, upgraded through `0002_ai_classification`, and retained the existing issue/user row counts. This is not a restore test of the Render production database.
- Email tests mocked Resend acceptance and signed webhook callbacks; they did not send or verify real email delivery.
- `npm audit --omit=dev` could not reach the npm registry because DNS resolution failed, so current frontend advisory status was not verified. Do not infer that the dependencies are clear.
- Docker is not installed in this workspace. The local Windows host also cannot launch Gunicorn because Gunicorn requires Unix `fcntl`; the Flask test client, Python compilation, and frontend build passed, but a Linux container build/startup remains unverified here.

### Civic intelligence verification (local, current branch)

- Backend: 43 tests passed, including the new grouping, ownership, translation, routing override, duplicate candidate, privacy, and missing-model checks.
- Frontend: 11 tests passed, including preference persistence and existing dashboard, review, map, and road intelligence checks.
- Frontend production build succeeded.
- Migration rehearsal: all revisions through `0005_civic_intelligence` applied to a disposable SQLite database; expected tables and issue columns were verified and the temporary database was removed.
- No production migration, live email delivery, deployment, or manual cross-screen visual inspection was performed.

### Phase 1–4 readiness checklist

| Area | Status | Evidence / remaining work |
| --- | --- | --- |
| Phase 1 security and persistence | Implemented and tested; production checks remain | Password hashing, JWT revocation, role/ownership scopes, image validation, audit events, explicit migrations, Postgres configuration, rate limits and email logs exist. Production secrets, backup/restore and provider settings require deployment-owner verification. |
| Phase 2 department management | Incomplete | Static confirmed-category routing and manual override are integrated. Department registry/CRUD, editable mappings, workload balancing, assignment history, department-specific email recipients and overdue auto-escalation are absent. |
| Phase 3 image classification | Implemented but unavailable without model assets | Inference and review paths handle missing models safely; no trained UrbanAI weights, reviewed dataset, or measured evaluation results are present. |
| Phase 4 interactive map | Implemented and tested | API-backed markers, filters, clustering/density, paging, missing-coordinate handling and public coordinate masking are tested. Tile availability depends on the external provider. |
| Email notifications | Implemented; live delivery unverified | Resend calls, provider IDs, idempotency, retries and signed webhook events are implemented. Real sender verification, recipients, webhook configuration and inbox verification remain external setup tasks. |
| Citizen/admin/department workflows | Implemented and tested for covered paths | Citizen registration/ownership, administrator management, department-scoped work, report history and completion are covered. The dashboard's report feed is polled for updates; `NotificationQueue` is not an active notification service. |
| Escalation | Partially implemented | Authorized manual escalation and audit history exist; no scheduled SLA/overdue escalation job or escalation email event exists. |

The `/api/stats` route rejects unauthenticated requests and uses SQL count queries instead of loading every complaint into Python memory. Department assignment is triggered by confirmed categories and can be manually overridden; an AI prediction alone never assigns a department.

## Phase 6A — Road-section management and observations

Phase 6A adds a staff-only **Predictive Road Intelligence** workspace for registering road sections and collecting sourced evidence. It stores road-section metadata separately from citizen complaints, plus condition observations, inspection notes, weather readings, traffic measurements, maintenance history, and road images. Existing complaint and image records are not transformed or used as road measurements.

The active database migration is `0003_road_sections`, following the Phase 5 head `0002_ai_classification`. It creates the new tables additively and has a forward-only downgrade so road records are not dropped automatically. Apply it in a backed-up target environment with `flask --app app db upgrade`; the Docker service does not run migrations at startup. The legacy baseline migration uses the current ORM metadata for fresh database setup, so the Phase 6A migration uses `checkfirst` to support both fresh installs and upgrades.

Road data is available to administrators and department officers/managers. Staff can create and edit road-section metadata, import a CSV, record condition and inspection observations, save weather and traffic measurements, enter actual past maintenance records, and upload images with source and optional license/attribution metadata. Worker and citizen roles cannot access the road-data API. Road coordinates are returned only through the authorized staff API. Road image links are signed and expire after 15 minutes.

Phase 6A API routes are `GET/POST /api/road-sections`, `GET/PATCH /api/road-sections/<id>`, `POST /api/road-sections/import`, and record endpoints under `/api/road-sections/<id>/conditions`, `/inspections`, `/weather`, `/traffic`, `/maintenance`, and `/images`. Road-photo reads use `/api/road-sections/<id>/images/<image_id>` with staff authorization or a short-lived signed link. There is no delete route for road sections or observations in this phase, which avoids accidental loss of source records.

Road-section CSV imports require `section_code,road_name`; optional columns are `ward_zone,road_type,length_meters,latitude,longitude,notes,source`. IDs must be unique within the file and database. Imports are limited to 2 MB and 5,000 sections and are all-or-nothing. Latitude and longitude must be supplied together. Length is stored in meters. Weather values use mm, °C, and percent; traffic counts require an interval when a sample vehicle count is provided. Missing values remain null. Every observation requires a timestamp and a source description; section source is optional when it is unknown.

The UI currently reports **Prediction unavailable**. Existing image classification concerns complaint categories; there are no validated future-condition labels or trained deterioration model. This release does not calculate risk scores, estimate deterioration, create sample measurements, or authorize maintenance. Data-source provenance and any dataset licenses must be checked before using uploaded material for future model training. Dataset preparation, model training/evaluation, prediction, prioritization, and road-risk map overlays remain future phases.

No performance benchmark or production PostgreSQL load test was run. The reports API currently returns all authorized reports in one response; use pagination before scaling to large datasets. Uploaded photographs are stored as PostgreSQL BLOBs, so storage, backup size, and transfer costs should be monitored. Rate limiting currently uses process-local memory; use shared Redis before scaling to multiple web instances.

## Deploy on Render

In Render, create a Blueprint from `DileepKumar2006/UrbanAI` and select the `master` branch. Render reads the root `render.yaml`, builds the Docker image, and creates the web service and Postgres database. The blueprint uses free plans for a no-cost preview; its database is temporary and expires after 30 days. Choose a paid Postgres plan in Render before using it for durable citizen reports. After the service is live, open the generated `onrender.com` URL and use the generated `URBANPULSE_ADMIN_KEY` from the service environment in the app's Settings page.

The free Render tier is preview-only for this application: free web services can sleep after 15 minutes idle and lose local files on restart/redeploy; free Postgres is 1 GB and expires after 30 days. Nagrik stores uploaded images in the database, so plan durable PostgreSQL capacity and backups before collecting real reports. Render currently lists paid web compute starting at $7/month and Postgres starting at $6/month; actual totals depend on service size, database storage, bandwidth, and plan changes. Verify current pricing before choosing a production plan: [Render pricing](https://render.com/pricing) and [free-tier limits](https://render.com/docs/free). No production environment variable, database, Render service, or deployment was changed during Phase 5.

### Deployment readiness gate

Before approving a production rollout: use a paid durable PostgreSQL plan; take and restore-test a fresh production backup; set a strong `JWT_SECRET_KEY` and admin key; configure the verified Resend sender, recipient addresses and webhook; decide whether untrained AI should remain disabled; configure shared rate-limit storage if scaling; build and launch the Docker image on Linux; run smoke tests against a staging database; verify logs, health checks, image access, and mail delivery; then explicitly approve deployment. These checks have not been performed against the live Render service.
