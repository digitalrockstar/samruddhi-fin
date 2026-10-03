"""Run once on a trusted local machine to create TELEGRAM_SESSION_STRING."""
import asyncio
from telethon import TelegramClient
from telethon.sessions import StringSession
from app.config import settings

async def main():
    if not settings.telegram_api_id or not settings.telegram_api_hash or not settings.telegram_phone:
        raise RuntimeError("Set TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_PHONE first")
    client=TelegramClient(StringSession(), settings.telegram_api_id, settings.telegram_api_hash)
    await client.start(phone=settings.telegram_phone)
    print("TELEGRAM_SESSION_STRING=")
    print(client.session.save())
    print("Store this as a deployment secret. Do not commit it or paste it into chat.")
    await client.disconnect()

if __name__=="__main__":
    asyncio.run(main())
