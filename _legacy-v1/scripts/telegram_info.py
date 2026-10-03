"""Look up Telegram chat/user IDs using the bot token from the environment.

Never accepts a token as an argument, so it cannot end up in shell history or
a committed file.

Usage:
    python -m scripts.telegram_info
    python -m scripts.telegram_info --latest 20
"""
from __future__ import annotations

import argparse
import asyncio
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SECRET_KEY", "info")

import httpx  # noqa: E402

from app.config import settings  # noqa: E402


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--latest", type=int, default=10, help="show the last N messages")
    args = ap.parse_args()

    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN is not set (put it in .env)")
        return 1

    token = settings.telegram_bot_token
    base = f"https://api.telegram.org/bot{token}"

    async with httpx.AsyncClient(timeout=30) as client:
        print("bot      :", (await client.get(f"{base}/getMe")).json().get("result", {}).get("username"))

        # A webhook and long polling cannot both consume updates.
        info = (await client.get(f"{base}/getWebhookInfo")).json().get("result", {})
        print("webhook  :", info.get("url") or "(not set)")
        print("pending  :", info.get("pending_update_count"))

        if info.get("url"):
            print("\nA webhook is active, so getUpdates will return nothing.")
            print("Message something in the group, then run again.")
            print("To use getUpdates instead: python -m scripts.setup_webhook --delete-first")
            return 0

        res = (await client.get(f"{base}/getUpdates",
                                params={"limit": args.limit})).json()
        if not res.get("ok"):
            print("getUpdates failed:", res)
            return 1

        updates = res.get("result", [])
        if not updates:
            print("\nNo updates yet. Send a message in the group and run again.")
            return 0

        print()
        print("=" * 70)
        print("CHATS / GROUPS")
        print("=" * 70)
        seen = {}
        for u in updates:
            for key in ("message", "edited_message", "channel_post"):
                m = u.get(key)
                if not m:
                    continue
                chat = m.get("chat") or {}
                cid = chat.get("id")
                if cid is None or cid in seen:
                    continue
                seen[cid] = True
                print(f"  TELEGRAM_CHAT_ID={cid}")
                print(f"    type  : {chat.get('type')}")
                print(f"    title : {chat.get('title') or chat.get('first_name')}")

        print()
        print("=" * 70)
        print(f"LAST {args.latest} MESSAGES")
        print("=" * 70)
        for u in updates[-args.latest:]:
            m = u.get("message") or u.get("edited_message")
            if not m:
                continue
            frm = m.get("from") or {}
            text = (m.get("text") or m.get("caption") or "")[:110]
            print(f"  {m.get('message_id'):>6}  {frm.get('first_name','?'):<10} {text!r}")

        print()
        print("Copy the TELEGRAM_CHAT_ID line above into your .env.")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))