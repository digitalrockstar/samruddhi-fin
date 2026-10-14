# Samruddhi Finance

Household expense tracker for bank SMS forwarded into a Telegram group.

## Ingestion decision

The forwarder posts through Telegram bots. A bot cannot read messages sent by another bot. This app therefore uses a Telegram **user session with Telethon**, not Bot API webhooks or getUpdates.

### Telethon login

1. Create a Telegram application and obtain `api_id` and `api_hash`.
2. Put those values plus your phone number in a local `.env`.
3. Run `python scripts_telegram_login.py` on a trusted machine.
4. Telegram sends an OTP. Telethon may also request your Telegram 2FA password.
5. The script prints a session string. Put it only in `TELEGRAM_SESSION_STRING` in your secret store.
6. Never commit the session string or paste it into chat.

User automation is subject to Telegram Terms and anti-abuse controls. Large history pulls, rapid repeated calls and unusual automation can trigger flood waits or account restrictions. This design uses scheduled pulls and bounded batches.

## Environment

Create these locally or as deployment secrets:

- APP_ENV
- APP_USERNAME
- APP_PASSWORD
- DATABASE_URL
- PYTHON_VERSION
- SECRET_KEY
- TELEGRAM_API_ID
- TELEGRAM_API_HASH
- TELEGRAM_PHONE
- TELEGRAM_SESSION_STRING
- TELEGRAM_CHAT_ID
- INGEST_BATCH_SIZE

Do not put real values in git.

Neon URLs using `sslmode=require` and `channel_binding=require` are accepted. The app removes those libpq parameters before constructing the SQLAlchemy asyncpg URL.

## Local setup

1. Install Python 3.12.9.
2. Create a virtual environment.
3. Install `pip install -e '.[test]'`.
4. Create `.env` from `.env.example`.
5. Run `python scripts_migrate.py`.
6. Run `uvicorn app.main:app --reload`.
7. Open `/`. HTTP Basic protects every route except `/health`.
8. Run the Telegram login script and store the resulting session string as a secret.
9. Run the first backfill.
10. Schedule `python scripts_ingest.py` every 15 to 60 minutes.

## Ingestion

Backfill paginates through the full group history. Later pulls use the stored highest message id as `min_id`. Raw messages are immutable and unique on `(chat_id, message_id)`, so retries do not duplicate them.

Deployment options:

1. Local machine. Cheapest, but sleep stops pulls.
2. Small VPS. Low cost and reliable for scheduled jobs.
3. GitHub Actions. Cheap for periodic execution, but a long-lived Telegram user session in CI adds secret-management and account-risk complexity. It is not my preferred production option.

## Learning

A reviewed example becomes an editable regex with named groups for amount, merchant, reference, balance and direction. Direction, mode, account and owner are stored as metadata.

Specificity is the amount of fixed literal text in the learned pattern. If multiple formats match, the highest-specificity format wins.

Failure modes are changed bank wording, over-broad regexes, ambiguous formats and a matching format with no valid amount. A Txn match without a valid amount returns to review. Unknown formats never create transactions.

The review queue masks URLs, dates and numeric values before grouping. Transaction records are stored separately from raw messages and link back to both the raw message and learned format.

## Database recovery

Use `python scripts_migrate.py` for deployment. If the public schema was manually emptied but a stale `alembic_version` remains, the script removes that stale marker before running migrations. This project deliberately avoids database enum types. Do not manually drop production tables without a backup.

## Deploy to Render

1. Create a Render Web Service from the repository.
2. Use Python 3.12.9.
3. Build command: `pip install -e .`.
4. Start command: `python scripts_migrate.py && uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
5. Add all environment variables above as Render secrets.
6. Run Telegram login locally and store only the resulting session string in Render.
7. Run ingestion from a separate scheduled job rather than an always-on Telegram listener.

Vercel is a poor fit for the Telethon ingestion component. If Vercel is used, keep ingestion on a VPS or scheduled worker.

## Safety

Only confirmed transactions feed transaction reporting. Manual tags for non-transaction categories immediately leave the review queue. Manual Txn tagging remains pending until transaction details are saved. Auto-created transactions require a learned Txn format and a valid amount.

If you shared real database credentials or Telegram tokens in chat, rotate them. Do not reuse exposed secrets.

## Versioning

Keep main stable. Release with tags such as `v1.0.0` and update CHANGELOG.md.
