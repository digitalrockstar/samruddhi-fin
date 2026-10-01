"""Environment check: what is set, what is missing, what to do about it.

Usage:
    python -m scripts.doctor
    python -m scripts.doctor --public-url https://samruddhi-fin.onrender.com
"""
from __future__ import annotations

import argparse
import os
import sys

# Set sane defaults so importing app.config never explodes during a check.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db")

OK, WARN, BAD, INFO = "OK  ", "WARN", "BAD ", "INFO"


def mask(value: str, keep: int = 4) -> str:
    """Show enough to identify a value without printing the whole secret."""
    if not value:
        return ""
    if len(value) <= keep * 2:
        return "*" * len(value)
    return f"{value[:keep]}...{value[-keep:]}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--public-url", help="your deployed base URL, e.g. https://app.onrender.com")
    args = ap.parse_args()

    from app.config import settings

    rows = []

    def add(status, name, detail):
        rows.append((status, name, detail))

    # ---------------------------------------------------------- required
    url = settings.database_url
    if not url:
        add(BAD, "DATABASE_URL", "REQUIRED. Set it to your Neon pooled connection string.")
    elif "neon.tech" not in url and "postgres" not in url:
        add(BAD, "DATABASE_URL", "Does not look like a Postgres URL.")
    elif "+asyncpg" not in url:
        add(WARN, "DATABASE_URL",
            f"{mask(url)}  (auto-upgraded to postgresql+asyncpg://, this is fine)")
    else:
        add(OK, "DATABASE_URL", f"{mask(url)}  (async driver)")

    if settings.is_production and settings.secret_key == "dev-only-insecure-key":
        add(WARN, "SECRET_KEY", "Using the insecure default in production. Set your own.")
    else:
        add(OK, "SECRET_KEY", f"{mask(settings.secret_key)}")

    # ---------------------------------------------------------- telegram
    if settings.telegram_bot_token:
        add(OK, "TELEGRAM_BOT_TOKEN", f"{mask(settings.telegram_bot_token, 6)}")
    else:
        add(WARN, "TELEGRAM_BOT_TOKEN", "Not set. Telegram ingestion is disabled.")

    if settings.telegram_chat_id:
        add(OK, "TELEGRAM_CHAT_ID", f"set (ends {str(settings.telegram_chat_id)[-3:]})")
    else:
        add(WARN, "TELEGRAM_CHAT_ID", "Not set. Any chat id will be accepted by the webhook.")

    if settings.telegram_webhook_secret:
        add(OK, "TELEGRAM_WEBHOOK_SECRET", "set (optional extra guard)")
    else:
        add(WARN, "TELEGRAM_WEBHOOK_SECRET",
            "Not set (optional). The webhook is gated by chat id only - "
            "anyone who learns the URL and chat id could inject fake SMS.")

    if settings.webhook_url:
        add(OK, "WEBHOOK_URL", settings.webhook_url)
    else:
        add(WARN, "WEBHOOK_URL", "Not set. Needed by scripts/setup_webhook.py to register the webhook.")

    add(INFO, "APP_ENV", settings.app_env)

    # ------------------------------------------------------------ output
    width = max(len(n) for _, n, _ in rows)
    print("=" * (width + 60))
    print("ENVIRONMENT")
    print("=" * (width + 60))
    for status, name, detail in rows:
        print(f"[{status}] {name.ljust(width)}  {detail}")

    # ------------------------------------------------------------- actions
    print()
    print("=" * (width + 60))
    print("NEXT STEPS")
    print("=" * (width + 60))

    steps = []
    if "+asyncpg" in url or url:
        steps.append("alembic upgrade head            # create/refresh schema")

    if settings.telegram_enabled and settings.webhook_url:
        steps.append("python -m scripts.setup_webhook  # point the bot at this deployment")
    elif settings.telegram_enabled and args.public_url:
        base = args.public_url.rstrip("/")
        os.environ["WEBHOOK_URL"] = f"{base}/webhook/telegram"
        import importlib
        from app import config as cfg
        importlib.reload(cfg)
        steps.append(
            f'export WEBHOOK_URL={base}/webhook/telegram\n'
            f'  python -m scripts.setup_webhook   # register the webhook'
        )
    elif settings.telegram_enabled:
        steps.append("set WEBHOOK_URL, then: python -m scripts.setup_webhook")

    steps.append("uvicorn app.main:app --reload    # or deploy to Render")

    for s in steps:
        print(f"  {s}")

    worst = any(s == BAD for s, _, _ in rows)
    if worst:
        print()
        print("Fix the BAD entries above before continuing.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())