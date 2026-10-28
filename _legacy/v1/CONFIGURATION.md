# Configuration reference

Every environment variable, every file that reads one, and exactly what to set
for local development and for Render.

Check your current setup at any time:

```bash
python -m scripts.doctor
python -m scripts.doctor --public-url https://your-app.onrender.com
```

---

## 1. Environment variables

### `DATABASE_URL` — **required**

Your Neon **pooled** connection string.

Neon dashboard → your project → **Connection Details** → copy the
**"Pooled connection"** URI (the one containing `-pooler`).

```
DATABASE_URL=postgresql://USER:PASSWORD@ep-xxxx-pooler.REGION.aws.neon.tech/samruddhi?sslmode=require
```

Accepted forms — all normalised to the async driver at startup:

| You paste | app uses |
|---|---|
| `postgresql://...` | `postgresql+asyncpg://...` |
| `postgres://...` | `postgresql+asyncpg://...` |
| `postgresql+asyncpg://...` | unchanged |
| `sqlite+aiosqlite:///./local.db` | unchanged (local dev) |

Use the **pooled** string, not the direct one: Render egress goes through
PgBouncer, which does not support prepared statements.

### `SECRET_KEY` — **required in production**

Any long random string.

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Currently unused at runtime (no sessions yet) but validated at boot so you
cannot ship the default.

### `TELEGRAM_BOT_TOKEN` — optional

From [@BotFather](https://t.me/BotFather). Without it, Telegram ingestion is
simply off and the rest of the app works.

### `TELEGRAM_CHAT_ID` — optional

The group's **numeric** id (`-100…` for a supergroup).

Do it with the helper, which reads the token from the environment so the token
never reaches a command line or a file:

```bash
python -m scripts.telegram_info
```

It prints a ready-to-paste `TELEGRAM_CHAT_ID=…` line for every chat the bot has
seen, plus the last messages. Send something in the group first.

Alternatives: forward a message to
[@userinfobot](https://t.me/userinfobot), or read `chat.id` from a
`getUpdates` response.

> `getUpdates` returns nothing while a webhook is registered. If you need the
> update history first, run `python -m scripts/setup_webhook.py --delete-first`,
> then `scripts/telegram_info`, then re-register with
> `scripts/setup_webhook.py`.

**Security note:** this is the only gate on the webhook. If it is unset, the
endpoint accepts updates from *any* chat. Anyone who learns both the webhook URL
and the chat id can inject fake transactions. Both are needed, and the chat id
is not guessable, but treat the pair as a secret.

> **Revoke any bot token that has been pasted into a chat, log or screenshot.**
> `@BotFather` → `/revoke` issues a new one. Then update `.env`.

### `TELEGRAM_WEBHOOK_SECRET` — optional, you can skip it

You do not need this. Left unset, the webhook relies on the chat-id check
alone, which is what you asked for.

If you ever want a second layer, set it and it is sent to Telegram as
`secret_token`; Telegram then returns it in the
`X-Telegram-Bot-Api-Secret-Token` header, which the app verifies.

### `WEBHOOK_URL` — optional

Your public webhook URL. Only used by `scripts/setup_webhook.py`.

Set it *after* the first deploy, because the URL is not known until then:

```
WEBHOOK_URL=https://samruddhi-fin.onrender.com/webhook/telegram
```

### `APP_ENV` — optional

`development` (default) or `production`. Used for warnings only.

---

## 2. Where each variable is read

| Variable | File | Line context |
|---|---|---|
| `DATABASE_URL` | `app/config.py` | `Settings.database_url`, `.async_database_url` |
| | `alembic/env.py` | `URL = config.get_main_option(...) or settings.async_database_url` |
| `SECRET_KEY` | `app/config.py` | validated by `scripts/doctor.py` |
| `TELEGRAM_BOT_TOKEN` | `app/config.py` | `.telegram_enabled` |
| | `scripts/setup_webhook.py` | builds the `api.telegram.org` URL |
| | `scripts/demo.py`, `tests/*` | defaults |
| `TELEGRAM_CHAT_ID` | `app/config.py` | `.telegram_enabled` |
| | `app/api/webhook.py` | rejects updates from other chats |
| | `scripts/doctor.py` | reports whether it is set |
| `TELEGRAM_WEBHOOK_SECRET` | `app/config.py` | `Optional[str] = None` |
| | `app/api/webhook.py` | header check, only when set |
| | `scripts/setup_webhook.py` | sent as `secret_token`, only when set |
| `WEBHOOK_URL` | `app/config.py` | `webhook_url` |
| | `scripts/setup_webhook.py` | `payload["url"]` |
| `APP_ENV` | `app/config.py` | `.is_production` |
| `SECRET_KEY` (dev default) | `app/config.py` | `"dev-only-insecure-key"` |

No other file reads `os.environ` directly — `pydantic-settings` handles it all
from `app/config.py`. If you add a variable, add it to `Settings` and to
`scripts/doctor.py`.

---

## 3. Files to change per environment

| Purpose | File | What |
|---|---|---|
| Local dev | `.env` | copy from `.env.example`, set `DATABASE_URL` + `SECRET_KEY` |
| Local dev | nothing else | `DATABASE_URL=sqlite+aiosqlite:///./local.db` also works |
| Local dev schema | — | `alembic upgrade head` |
| Render | Render dashboard | the 4 sync:false vars below |
| Render schema | `render.yaml` | `preDeployCommand: alembic upgrade head` |
| Telegram wiring | — | `python -m scripts.setup_webhook` (once) |

### Render variables to set

| Key | Value |
|---|---|
| `DATABASE_URL` | Neon pooled URL |
| `TELEGRAM_BOT_TOKEN` | from @BotFather |
| `TELEGRAM_CHAT_ID` | group numeric id |
| `SECRET_KEY` | auto-generated by Render |
| `WEBHOOK_URL` | set after first deploy, then redeploy |
| `TELEGRAM_WEBHOOK_SECRET` | leave unset |
| `APP_ENV` | `production` (already in `render.yaml`) |

---

## 4. Files that need a code change (not just an env var)

These are the only places the app needs manual intervention. Nothing else is
environment-specific.

| Change | File |
|---|---|
| Add an environment variable | `app/config.py` + `scripts/doctor.py` |
| Add a person / change prefixes | `app/services/telegram.py` → `PREFIX_MAP`, `ALIASES`, `DEFAULT_PREFIX` |
| Add a new forwarding wrapper (e.g. `WhatsApp: `) | `app/services/telegram.py` → `ENVELOPE_MARKERS` |
| Add a bank/account | `app/services/parser.py` → `BANKS`, plus a row in **Settings → Accounts** |
| Add a bank SMS template | `app/services/parser.py` → `RULES` |
| Change a category | UI, or `app/services/seed.py` (then reseed) |
| Add a categorisation keyword | `app/services/categorizer.py` → `KEYWORD_RULES` |
| Treat a UPI handle as an internal transfer | `app/services/parser.py` → `CC_PAY_HANDLE` / `WALLET_HANDLE` |
| Change the dashboard period options | `app/templates/index.html` → `periods` |
| Add a page | `app/main.py` route + template + `app/templates/base.html` nav |
| Change the schema | models → `alembic revision --autogenerate` |
| Change Render build/start | `render.yaml` |

## 4a. Forwarding formats currently understood

Defined in `app/services/telegram.py`:

| Constant | Purpose | Current entries |
|---|---|---|
| `PREFIX_MAP` | canonical person keys → name | `AP-`, `AS-` |
| `ALIASES` | accepted spellings → canonical | `AP-` `AP:` `AP`, `AS-` `AS:` `AS` |
| `DEFAULT_PREFIX` | who an un-prefixed `From:/Time:` forward belongs to | `AP-` |
| `ENVELOPE_MARKERS` | share-sheet wrappers to strip | `New SMS:`, `New Message:`, `New text message:`, `SMS:`, `Message:`, `Forwarded message:` |
| `SENDER_HEADER` | the `From:` / `Time:` header block | `From`/`Sent from`/`Contact`, then optional `Time`/`Date`/`Date/Time`/`Timestamp` |

---

## 5. Deploy order (first time)

```bash
# 1. Neon: create the project, copy the POOLED URL
# 2. Local
pip install -r requirements.txt
copy .env.example .env          # fill DATABASE_URL + SECRET_KEY
python -m scripts.doctor        # confirms both are present
alembic upgrade head
uvicorn app.main:app --reload

# 3. Push to Git, create the Render Web Service, set the 4 variables
# 4. After the first deploy succeeds:
#    set WEBHOOK_URL=https://<your-service>/webhook/telegram  (redeploy)
python -m scripts.setup_webhook
# 5. Open the app, go to Settings, add your cards/accounts
# 6. Forward a bank SMS to the group with the AP- / AS- prefix
```

---

## 6. Security posture today

There is **no authentication**. The person selector in the header is stored in
`localStorage` and is a UI convenience, not a security boundary. Anyone with
the URL can read and edit everything.

What is protected:

- the webhook, by chat id (and optionally by secret token)

What is not:

- every page and API endpoint
- the Neon database itself (keep `sslmode=require`, restrict connections if
  you want a second layer)

Before exposing this beyond your household, add auth — e.g. a
`X-Api-Key` header checked in middleware, or put it behind Cloudflare Access.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Database schema is missing or out of date` | migrations not run | `alembic upgrade head` |
| `DATABASE_URL is not set` | no `.env` | copy `.env.example` |
| webhook returns `chat id not allowed` | wrong chat id | re-read it via @userinfobot |
| webhook never fires | webhook not registered | `python -m scripts.setup_webhook` |
| Telegram 409 conflict | another `getUpdates` poller running | stop it, then re-run setup |
| `relation does not exist` | wrong Neon database | check you used the pooled URL for the right project |
| everything stuck / timeouts | direct (non-pooled) URL | switch to the pooled connection string |
| transactions arrive but no category | parser did not recognise the merchant | expected — categorise once and merchant learning handles the rest |