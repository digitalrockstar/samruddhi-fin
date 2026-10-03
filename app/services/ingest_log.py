"""Small in-memory log of recent Telegram updates (no message text), for diagnosing delivery."""
from collections import deque
from datetime import datetime, timezone

_LOG: deque = deque(maxlen=50)


def record(update: dict, outcome: str, chat_id=None) -> None:
    _LOG.appendleft({
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "update_keys": sorted(k for k in update.keys() if k != "update_id"),
        "chat_id": chat_id,
        "outcome": outcome,
    })


def recent() -> list:
    return list(_LOG)
