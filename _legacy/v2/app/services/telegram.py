from datetime import datetime, timezone
from telethon import TelegramClient
from telethon.sessions import StringSession
from sqlalchemy import select
from app.config import settings
from app.models import IngestState, RawMessage

async def client() -> TelegramClient:
    if not all([settings.telegram_api_id, settings.telegram_api_hash, settings.telegram_phone, settings.telegram_session_string]):
        raise RuntimeError("Telegram user-session configuration is incomplete")
    return TelegramClient(StringSession(settings.telegram_session_string), settings.telegram_api_id, settings.telegram_api_hash)

async def ingest_once(session, backfill=False) -> int:
    if not settings.telegram_chat_id:
        raise RuntimeError("TELEGRAM_CHAT_ID is missing")
    state = await session.scalar(select(IngestState).where(IngestState.chat_id == settings.telegram_chat_id))
    if state is None:
        state = IngestState(chat_id=settings.telegram_chat_id, last_message_id=0)
        session.add(state)
        await session.flush()
    max_id = state.last_message_id
    async with await client() as tg:
        if backfill:
            offset = 0
            while True:
                found = 0
                async for msg in tg.iter_messages(settings.telegram_chat_id, limit=settings.ingest_batch_size, offset_id=offset, reverse=False):
                    if not msg.message_id:
                        continue
                    found += 1
                    exists = await session.scalar(select(RawMessage.id).where(RawMessage.chat_id == settings.telegram_chat_id, RawMessage.message_id == msg.id))
                    if not exists:
                        session.add(RawMessage(chat_id=settings.telegram_chat_id, message_id=msg.id, message_date=msg.date or datetime.now(timezone.utc), sender_id=getattr(msg, "sender_id", None), text=msg.message or ""))
                    max_id=max(max_id,msg.id)
                await session.commit()
                if found < settings.ingest_batch_size:
                    break
                offset = max_id
        else:
            async for msg in tg.iter_messages(settings.telegram_chat_id, limit=settings.ingest_batch_size, min_id=state.last_message_id, reverse=True):
                if not msg.message_id:
                    continue
                exists = await session.scalar(select(RawMessage.id).where(RawMessage.chat_id == settings.telegram_chat_id, RawMessage.message_id == msg.id))
                if not exists:
                    session.add(RawMessage(chat_id=settings.telegram_chat_id, message_id=msg.id, message_date=msg.date or datetime.now(timezone.utc), sender_id=getattr(msg, "sender_id", None), text=msg.message or ""))
                max_id=max(max_id,msg.id)
            await session.commit()
    state.last_message_id=max_id
    await session.commit()
    return max_id
