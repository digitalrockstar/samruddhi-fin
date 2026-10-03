"""Small in-memory log of recent Telegram updates (no message text), for diagnosing delivery."""
import logging
from collections import deque
from datetime import datetime, timezone

_LOG: deque = deque(maxlen=50)


def record(update: dict, outcome: str, chat_id=None) -> None:
    # Also goes to the Render log, which survives the free plan sleeping (this deque does not).
    logging.getLogger("uvicorn.error").warning(
        "telegram update: %s | chat_id=%s | keys=%s", outcome, chat_id,
        sorted(k for k in update.keys() if k != "update_id"))
    _LOG.appendleft({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "update_keys": sorted(k for k in update.keys() if k != "update_id"),
        "chat_id": chat_id,
        "outcome": outcome,
    })


def recent() -> list:
    return list(_LOG)
