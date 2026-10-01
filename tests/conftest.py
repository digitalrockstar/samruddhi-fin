import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Shared test database
#
# HTTP-level test modules share ONE database file. They must NOT each set
# DATABASE_URL themselves: app.config caches `settings` on first import, so the
# second module to import would silently reuse the first module's URL and write
# into the wrong database.
# ---------------------------------------------------------------------------
_TMP = Path(r"C:\Users\aksha\AppData\Local\Temp\opencode")
_TMP.mkdir(parents=True, exist_ok=True)
TEST_DB_PATH = _TMP / "samruddhi_http_tests.db"
if TEST_DB_PATH.exists():
    TEST_DB_PATH.unlink()

os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB_PATH}"
os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
os.environ["SECRET_KEY"] = "test-secret"

# Build the schema with real migrations before any test module imports the app.
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402

_ROOT = Path(__file__).resolve().parent.parent
_cfg = Config(str(_ROOT / "alembic.ini"))
_cfg.set_main_option("script_location", str(_ROOT / "alembic"))
_cfg.set_main_option("sqlalchemy.url", f"sqlite:///{TEST_DB_PATH}")
command.upgrade(_cfg, "head")

import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

TEST_DB = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture
async def session():
    """Private in-memory DB built straight from the models.

    Used by service-level tests that do not need migrations. HTTP-level tests
    use the shared migrated database instead, so both paths stay covered.
    """
    # Imported lazily so this module never caches app settings at import time.
    from app.database import Base
    from app import models  # noqa: F401

    engine = create_async_engine(TEST_DB)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    maker = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    async with maker() as s:
        yield s

    await engine.dispose()