"""Run migrations safely when a database was manually emptied."""
import asyncio
import subprocess
import sys
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from app.config import settings

EXPECTED={"raw_messages","tags","learned_formats","reviews","transactions","ingest_state"}

async def main():
    engine=create_async_engine(settings.db_url())
    async with engine.begin() as conn:
        rows=await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        tables={r[0] for r in rows}
        if not (EXPECTED & tables):
            await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
    await engine.dispose()
    raise SystemExit(subprocess.call([sys.executable,"-m","alembic","upgrade","head"]))

if __name__=="__main__":
    asyncio.run(main())
