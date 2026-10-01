from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

_URL = settings.async_database_url

# Pool sizing only applies to real server pools; SQLite (local dev, tests)
# uses StaticPool and rejects these kwargs.
_is_sqlite = _URL.startswith("sqlite")

engine = create_async_engine(
    _URL,
    echo=False,
    pool_pre_ping=True,
    # Neon/pgBouncer work best with a modest, recycled pool
    **(
        {}
        if _is_sqlite
        else {"pool_size": 5, "max_overflow": 5, "pool_recycle": 300}
    ),
)

async_session_maker = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def _schema_is_current() -> bool:
    """Check that every table the app needs actually exists."""
    from app import models  # noqa: F401  (populate metadata)

    def has_tables(conn):
        existing = set(inspect(conn).get_table_names())
        expected = set(Base.metadata.tables)
        return expected.issubset(existing)

    async with engine.connect() as conn:
        return await conn.run_sync(has_tables)


async def init_db() -> None:
    """Verify the schema is migrated, then seed reference data.

    Schema changes are owned by Alembic (``alembic upgrade head``), never by
    create_all. This keeps local, staging and production databases consistent
    and makes every change reviewable and reversible.
    """
    if not await _schema_is_current():
        raise RuntimeError(
            "Database schema is missing or out of date.\n"
            "Run:  alembic upgrade head\n"
            f"URL:   {settings.async_database_url.split('@')[-1]}"
        )

    from app.services.seed import seed_all
    await seed_all()