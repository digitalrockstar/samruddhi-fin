"""Alembic environment, wired to the app's settings and async engine.

The DB URL is never stored here; it is read from app.config so migrations and
the running app can never drift apart.
"""
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import inspect, text, create_engine, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.config import settings
from app.database import Base

# Import models so every table is registered on Base.metadata
from app import models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The URL normally comes from app settings, but allow an explicit override
# (tests, one-off maintenance) so the migration target can be redirected
# without touching the environment.
URL = config.get_main_option("sqlalchemy.url") or settings.async_database_url
config.set_main_option("sqlalchemy.url", URL)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    context.configure(
        url=URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def heal_reset_database(connection) -> None:
    """Make `alembic upgrade head` work after someone emptied the database by hand.

    Dropping tables in Neon leaves Postgres enum types behind (CREATE TYPE then fails with
    "already exists") and may leave a stale alembic_version row (migrations are skipped and the
    app starts without tables). If the app's core table is missing there is no data to protect,
    so clear that leftover state first.
    """
    if connection.dialect.name != "postgresql":
        return
    if inspect(connection).has_table("persons"):
        return
    connection.execute(text("DROP TABLE IF EXISTS alembic_version"))
    names = connection.execute(text(
        "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
        "WHERE t.typtype = 'e' AND n.nspname = current_schema()")).scalars().all()
    for name in names:
        connection.execute(text(f'DROP TYPE IF EXISTS "{name}" CASCADE'))
    connection.commit()


def do_run_migrations(connection: Connection) -> None:
    heal_reset_database(connection)
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_sync() -> None:
    """For sync URLs (e.g. plain sqlite:// used by tooling and offline SQL)."""
    connectable = create_engine(URL, poolclass=pool.NullPool, future=True)
    try:
        with connectable.connect() as connection:
            do_run_migrations(connection)
    finally:
        connectable.dispose()


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def _is_async_url(url: str) -> bool:
    return "+asyncpg" in url or "+aiosqlite" in url or "+asyncmy" in url


if context.is_offline_mode():
    run_migrations_offline()
elif _is_async_url(URL):
    asyncio.run(run_migrations_online())
else:
    run_migrations_sync()