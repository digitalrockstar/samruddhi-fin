import asyncio
from app.db import SessionLocal
from app.api import do_ingest

async def main():
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is required")
    async with SessionLocal() as session:
        await do_ingest(session, False)

if __name__ == "__main__":
    asyncio.run(main())
