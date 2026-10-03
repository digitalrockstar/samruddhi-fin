from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.config import settings

engine = create_async_engine(settings.db_url(), pool_pre_ping=True) if settings.db_url() else None
SessionLocal = async_sessionmaker(engine, expire_on_commit=False) if engine else None

async def get_session():
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    async with SessionLocal() as session:
        yield session
