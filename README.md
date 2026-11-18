# KeystrokeID

Keystroke-dynamics data collection and identification. This part: **backend** (FastAPI + SQLite) in Docker.

```
browser ──HTTPS──► Caddy ──► FastAPI (api/) ──► data/ (SQLite) ──► ml/ (KNN, RF, LightGBM)
```

* `api/main.py` - HTTP API (thin layer over `data/`)
* `data/` - SQLite, validation, H/DD/UD features, CSV export (contract: `docs/data_contract.md`)
* `scripts/` - `create_user.py` (invite codes), `backup.py`
* `frontend/` - static participant app served at `/` (plain HTML/JS, no build step; see below)

## Run with Docker (production-style, HTTPS)

```bash
cp .env.example .env        # set DOMAIN and a long random KEYSTROKEID_ADMIN_KEY
docker compose up -d --build
docker compose ps
```

With `DOMAIN=localhost` Caddy uses a self-signed certificate (browsers warn; `curl -k` works).
With a real domain pointing at the server (ports 80/443 open) it gets a Let's Encrypt certificate.

Local run without HTTPS: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build app`
(API on http://localhost:8000, Swagger on http://localhost:8000/docs).

## Participants and invite codes

```bash
docker compose exec app python -m scripts.create_user U01 U02 U03   # prints "U01<TAB>code"
```
Codes are shown once and stored only as hashes. Keep the code -> person mapping **outside** the server.
Admin can also create users via `POST /api/admin/users` (header `X-Admin-Key`).

## Frontend

Plain ES modules, served by the API itself (same origin, no CORS, no build). Flow: invite code ->
consent -> sessions -> typing task (20 valid entries per session).

| File | Role |
|---|---|
| `recorder.js` | **all measurement logic** (pure, unit-tested): rules for valid/invalid attempts |
| `api.js` | API client + outbox that retries uploads with the same `client_id` |
| `app.js` | screens and flow |
| `strings.js` | all user-facing text (Polish) - edit consent wording here |

Personal link for a participant (the code is in the URL fragment, which is never sent to the server and is
removed from the address bar immediately): `https://<DOMAIN>/#code=<invite code>`.

An attempt is marked invalid (and still stored with `valid=false`) on: wrong key, Backspace/Delete,
autorepeat, Shift/arrows/other keys, Ctrl/Alt shortcuts, paste/drop, text inserted without key events
(autofill, voice, on-screen keyboard), focus loss. Timestamps are `event.timeStamp` relative to the
first key of the attempt. **The consent text in `strings.js` is a placeholder** and must be approved
by the supervisor before collecting data.

## API

Participant endpoints need `X-Invite-Code`; admin endpoints need `X-Admin-Key`.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/health`, `/api/prompts` | public |
| GET | `/api/me` | user id, consent, per-session counts |
| POST | `/api/consent` | records consent timestamp |
| POST | `/api/sessions` | next session label `S01`, `S02`... (needs consent, limit per user) |
| POST | `/api/samples` | sample JSON (see contract); `client_id` makes retries idempotent (200 + `duplicate:true`) |
| POST | `/api/predict` | `501` until `ml/predict.py` exists |
| POST | `/api/admin/users` | create participant, returns invite code once |
| GET | `/api/admin/stats`, `/api/admin/export.csv` | collection progress, CSV for ML |

Time values are measured in the **browser** (`performance.now()`), the server never measures time.
User identity always comes from the invite code, never from the request body.

## Data, backups, updates

Data lives in the Docker volume `storage` (database, backups) - recreating containers does not remove it.

```bash
docker compose exec app python -m scripts.backup          # consistent online backup
docker compose cp app:/app/storage/backups ./backups      # copy OFF the server
docker compose up -d --build                              # deploy a new version
```
`docker compose down -v` **deletes the volume (all samples)** - do not run it on the server.

## Development without Docker

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements-dev.txt     # Linux/macOS: .venv/bin/python
.venv/Scripts/python -m pytest                                  # 43 backend tests
node --test tests/js/frontend.test.mjs                          # 19 frontend tests (Node 20+)
.venv/Scripts/python -m uvicorn api.main:create_app --factory --reload
```

## Privacy

Samples are biometric data. Do not commit `*.db`, `storage/`, backups, CSV exports or `.env`
(all in `.gitignore`). Agree hosting, retention and consent wording with the supervisor before collecting.
