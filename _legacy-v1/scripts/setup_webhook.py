"""Point the Telegram bot at this deployment, using env vars only.

Usage:
    python -m scripts.setup_webhook              # register the webhook
    python -m scripts.setup_webhook --delete-first
        # remove any existing webhook first (needed before getUpdates will
        # return the update history, e.g. to discover your chat id)

Never accepts the bot token as an argument, so it cannot end up in shell
history or a committed file.
"""
import asyncio
import sys

import httpx

from app.config import settings


async def _call(client, method: str, path: str, **kw):
    url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"
    res = await client.post(url, **kw)
    try:
        return res.json()
    except ValueError:
        return {"ok": False, "error": f"HTTP {res.status_code}"}


async def main() -> int:
    delete_first = "--delete-first" in sys.argv

    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not set (put it in .env)")
        return 1
    if not settings.webhook_url:
        print("WEBHOOK_URL is not set.")
        print("Set it to https://<your-service>/webhook/telegram, then re-run.")
        return 1

    base = settings.webhook_url.rstrip("/")
    if not base.endswith("/webhook/telegram"):
        print(f"WARNING: WEBHOOK_URL should end in /webhook/telegram (got {base})")

    async with httpx.AsyncClient(timeout=30) as client:
        me = await _call(client, "getMe")
        if not me.get("ok"):
            print("Bot token rejected by Telegram:", me)
            return 1
        print("bot:", me["result"].get("username"))

        if delete_first:
            out = await _call(client, "deleteWebhook", params={"drop_pending_updates": "false"})
            print("deleteWebhook:", "ok" if out.get("ok") else out)
            print("-> getUpdates will now return history. Run scripts/telegram_info")
            print("   then re-run this script WITHOUT --delete-first.")
            return 0

        payload = {
            "url": base,
            "allowed_updates": ["message", "edited_message"],
            "drop_pending_updates": True,
        }
        # Optional: only register a secret token if one was configured.
        if settings.telegram_webhook_secret:
            payload["secret_token"] = settings.telegram_webhook_secret
            print("registering with a secret token")
        else:
            print("no secret token (chat id is the only gate)")

        out = await _call(client, "setWebhook", json=payload)
        print("setWebhook:", "ok" if out.get("ok") else out)
        if not out.get("ok"):
            return 1

        info = (await _call(client, "getWebhookInfo"))["result"]
        print()
        print("webhook url      :", info.get("url"))
        print("has_custom_cert  :", info.get("has_custom_certificate"))
        print("pending updates  :", info.get("pending_update_count"))
        if info.get("last_error_message"):
            print("last error       :", info["last_error_message"])
        if not info.get("url"):
            print("Webhook was not registered.")
            return 1

        # Round-trip check through the public URL
        origin = base.rsplit("/webhook/", 1)[0]
        health = await client.get(f"{origin}/health")
        print("app reachable    :", health.status_code, f"{origin}/health")

        status = await client.get(f"{base}")
        print("webhook status   :", status.json() if status.status_code == 200 else status.status_code)

    print()
    print("Done. Forward a bank SMS to the group to test.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))