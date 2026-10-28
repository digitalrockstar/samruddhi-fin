"""Telegram webhook: receives forwarded SMS messages.

Expected text format: "AP- <sms>" or "AS- <sms>" (prefix = who paid).
Message ID is used for idempotency, so a retried Telegram update is a no-op.
"""
import hashlib
import hmac
import re
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Transaction
from app.services import ingest_log
from app.services.telegram import TelegramProcessor

router = APIRouter(prefix="/webhook", tags=["telegram"])


@router.post("/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    # In production the secret is mandatory (startup refuses to boot without it).
    # Outside production it is enforced whenever it is configured.
    if settings.telegram_webhook_secret:
        got = x_telegram_bot_api_secret_token or ""
        if not hmac.compare_digest(got.encode(), settings.telegram_webhook_secret.encode()):
            raise HTTPException(status_code=403, detail="forbidden")

    try:
        update: Dict[str, Any] = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid json"}

    # channel_post covers SMS forwarded into a Telegram channel rather than a group.
    message = (update.get("message") or update.get("edited_message")
               or update.get("channel_post") or update.get("edited_channel_post") or {})
    if not message:
        ingest_log.record(update, "skipped: no message in update")
        return {"ok": True, "skipped": "no message"}

    chat_id = (message.get("chat") or {}).get("id")
    if settings.telegram_chat_id and chat_id != settings.telegram_chat_id:
        ingest_log.record(update, "skipped: chat id not allowed", chat_id)
        return {"ok": True, "skipped": "chat id not allowed"}

    text = message.get("text") or message.get("caption") or ""
    if not text.strip():
        ingest_log.record(update, "skipped: empty text (photo, file or sticker?)", chat_id)
        return {"ok": True, "skipped": "empty text"}

    message_id = message.get("message_id")
    sender = message.get("from") or {}
    received_at = datetime.fromtimestamp(message["date"]) if message.get("date") else None

    # Store the message verbatim first, then derive a transaction from it. A
    # failed parse still leaves an auditable row.
    processor = TelegramProcessor(db)
    row = await processor.ingest_raw(
        text,
        external_id=message_id,
        source="telegram",
        meta={
            "chat_id": chat_id,
            "sender_id": sender.get("id"),
            "sender_name": (sender.get("first_name") or "")[:80] or None,
            "received_at": received_at,
        },
    )

    ingest_log.record(update, "stored" if row is not None else "duplicate", chat_id)
    if row is None:
        # Already archived (Telegram retries on timeout).
        return {"ok": True, "duplicate": True, "raw_id": None}

    txn, note = await processor.derive(row)

    if txn is None:
        return {
            "ok": True,
            "raw_id": row.id,
            "skipped": note,
            "verdict": row.verdict,
            "rule": row.rule,
        }

    return {
        "ok": True,
        "raw_id": row.id,
        "transaction_id": txn.id,
        "needs_review": bool(txn.needs_review),
        "amount": float(txn.parsed_amount),
        "merchant": txn.merchant,
        "note": note,
    }


@router.post("/sms")
async def sms_direct(
    request: Request,
    x_webhook_secret: Optional[str] = Header(default=None),
    db: AsyncSession = Depends(get_db),
):
    """Direct SMS ingest for forwarders that post as a BOT.

    Telegram never delivers a bot's messages to another bot (or to itself), so a webhook
    cannot see SMS that a forwarder app sends to the group via the Bot API. Have the
    forwarder also POST here: header `X-Webhook-Secret: <TELEGRAM_WEBHOOK_SECRET>` and either
    a plain-text body (the same text it sends to Telegram) or JSON
    {"text": "...", "prefix": "AS", "time": "2026-10-03T12:36:17+05:30"}.
    """
    secret = settings.telegram_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="direct ingest is not configured")
    if not hmac.compare_digest((x_webhook_secret or "").encode(), secret.encode()):
        raise HTTPException(status_code=403, detail="forbidden")

    raw = await request.body()
    text, prefix, when = "", None, None
    if "json" in request.headers.get("content-type", ""):
        try:
            data = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid JSON")
        text = str(data.get("text") or "")
        prefix = (data.get("prefix") or "").strip().upper().rstrip("-: ") or None
        if data.get("time"):
            try:
                when = datetime.fromisoformat(str(data["time"]))
            except ValueError:
                pass
    else:
        text = raw.decode("utf-8", errors="replace")
    if not text.strip():
        raise HTTPException(status_code=400, detail="empty text")
    if prefix and not re.match(r"^\s*(AP|AS)\s*[-:]", text):
        text = f"{prefix}- {text}"

    received_at = when or datetime.now(timezone.utc)
    # Identical text inside the same minute counts as a forwarder retry.
    minute = received_at.strftime("%Y%m%d%H%M")
    ext_id = int(hashlib.sha1(f"{text}|{minute}".encode()).hexdigest()[:15], 16)

    processor = TelegramProcessor(db)
    row = await processor.ingest_raw(
        text, external_id=ext_id, source="sms_direct",
        meta={"chat_id": None, "sender_id": None, "sender_name": None, "received_at": received_at},
    )
    ingest_log.record({"sms_direct": 1}, "stored" if row is not None else "duplicate")
    if row is None:
        return {"ok": True, "duplicate": True}
    txn, note = await processor.derive(row)
    return {"ok": True, "raw_id": row.id, "verdict": row.verdict, "transaction_id": txn.id if txn else None, "note": note}


@router.get("/telegram")
async def webhook_info():
    """Unauthenticated status endpoint. Deliberately reveals nothing sensitive:
    it never echoes the chat id or the bot token."""
    return {
        "ok": True,
        "service": "samruddhi-fin",
        "telegram_enabled": settings.telegram_enabled,
        "chat_id_configured": bool(settings.telegram_chat_id),
        "secret_token_enabled": bool(settings.telegram_webhook_secret),
        "webhook_url_configured": bool(settings.webhook_url),
    }