# Deploying Samruddhi on Render (Free) + Neon Postgres

## 1. Database (Neon)
1. Create a free project at neon.tech. Copy the **connection string** (looks like `postgresql://user:pass@ep-xxxx.region.aws.neon.tech/neondb?sslmode=require`).
2. Keep `sslmode=require` in it. The app accepts `postgres://` or `postgresql://` and adds the driver itself.
3. Tables are created automatically on first start. No manual SQL.

## 2. Web service (Render)
1. Render dashboard → **New → Web Service** → connect `digitalrockstar/samruddhi-fin`, branch `main`.
   (Or **New → Blueprint** and Render reads `render.yaml`.)
2. Settings:

| Field | Value |
|---|---|
| Runtime | Python 3 |
| Instance type | Free |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn wsgi:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60` |
| Health check path | `/healthz` |

3. Environment variables:

| Key | Value |
|---|---|
| `DATABASE_URL` | the Neon connection string |
| `SECRET` | long random string: `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `PYTHON_VERSION` | `3.12.9` |

4. Deploy. Open the URL: the first screen is **/setup**. Whoever signs up first becomes the household admin.
5. Without `DATABASE_URL` the app uses local SQLite (`instance/samruddhi.db`). Never rely on that on Render Free: the disk is wiped on every deploy and restart.

## Free-tier behaviour
- The service sleeps after ~15 min idle; the first request then takes ~30-60 s. Neon also auto-suspends and wakes on connect.
- Due subscriptions, goal assignments and bill reminders are generated on the first request each day, not by a background job, so nothing is missed while it sleeps.
- Take a CSV backup before big changes: **More → Data reset → Backup as CSV**.

## Android APK
Actions → **Build Android APK** → Run workflow, enter your Render URL (or set a repository variable `APP_URL`). Download `app-debug.apk` from the run's artifacts. The APK is a thin WebView shell around the web app.

## Local development
```
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # optional
flask --app wsgi run --debug
python -m pytest tests -q
```

## Schema changes
V1 creates tables with `create_all`. Adding tables is automatic; changing existing columns needs a manual migration (Alembic can be added in V3).
