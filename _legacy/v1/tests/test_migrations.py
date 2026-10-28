"""Guards against schema drift.

1. Migration/model parity - upgrades a scratch DB with Alembic, then asserts
   autogenerate sees no differences from the models.
2. Enum storage consistency - SQLAlchemy persists enum NAMES; the app mixes
   enum members and raw strings, so both must land on the same value.
"""
import asyncio
import os
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine as sync_engine
from sqlalchemy import inspect

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def scratch_db(tmp_path):
    return tmp_path / "parity.db"


def _alembic_config(db_path: Path) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def test_migrations_exist_and_form_single_head(scratch_db):
    script = ScriptDirectory.from_config(_alembic_config(scratch_db))
    heads = script.get_heads()
    assert len(heads) == 1, f"expected exactly one head, got {heads}"
    assert script.get_base() is not None


def test_upgrade_head_creates_every_model_table(scratch_db):
    cfg = _alembic_config(scratch_db)
    command.upgrade(cfg, "head")

    from app.database import Base
    from app import models  # noqa: F401

    eng = sync_engine(f"sqlite:///{scratch_db}")
    try:
        actual = set(inspect(eng).get_table_names())
    finally:
        eng.dispose()

    expected = set(Base.metadata.tables)
    assert expected.issubset(actual), f"missing tables: {expected - actual}"


def test_no_model_drift_after_migration(scratch_db):
    """Autogenerate must find nothing - this is the drift alarm."""
    cfg = _alembic_config(scratch_db)
    command.upgrade(cfg, "head")

    from app.database import Base
    from app import models  # noqa: F401

    eng = sync_engine(f"sqlite:///{scratch_db}")
    try:
        with eng.connect() as conn:
            ctx = MigrationContext.configure(
                conn, opts={"compare_type": True, "compare_server_default": True}
            )
            diff = compare_metadata(ctx, Base.metadata)
    finally:
        eng.dispose()

    assert diff == [], f"models drifted from migrations:\n{diff}"


def test_downgrade_then_upgrade_roundtrip(scratch_db):
    cfg = _alembic_config(scratch_db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")  # must not raise


def test_enum_names_are_what_lands_in_db(scratch_db):
    """Enum columns store NAMES; string inputs must coerce to the same value."""
    import enum

    from sqlalchemy import Column, Integer, String
    from sqlalchemy import Enum as SQLEnum
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.orm import DeclarativeBase

    class Kind(str, enum.Enum):
        EXPENSE = "expense"
        IGNORE = "ignore"

    class Base2(DeclarativeBase):
        pass

    class Row(Base2):
        __tablename__ = "_enum_probe"
        id = Column(Integer, primary_key=True)
        kind = Column(SQLEnum(Kind), nullable=False)

    async def run():
        eng = create_async_engine(f"sqlite+aiosqlite:///{scratch_db}")
        async with eng.begin() as conn:
            await conn.run_sync(Base2.metadata.create_all, tables=[Row.__table__])
        S = async_sessionmaker(eng, class_=AsyncSession, expire_on_commit=False)
        async with S() as s:
            s.add_all([Row(id=1, kind=Kind.EXPENSE), Row(id=2, kind="expense")])
            await s.commit()
            rows = (await s.execute(text("SELECT id, kind FROM _enum_probe ORDER BY id"))).all()
        await eng.dispose()
        return rows

    rows = asyncio.run(run())
    # Both the enum member and the lowercase string must persist identically
    assert rows[0][1] == rows[1][1], f"enum coercion mismatch: {rows}"
    assert rows[0][1] == "EXPENSE", f"expected enum NAME to be stored, got {rows[0][1]}"


def test_seed_only_uses_enum_compatible_values():
    """seed.py passes plain strings for `type`; they must be valid enum values."""
    from app.models import CategoryType
    from app.services.seed import ROOTS, SUBCATEGORIES

    for root in ROOTS:
        # seed.py builds child types via `root["type"]`
        CategoryType(root["type"])

    for root_name in SUBCATEGORIES:
        assert root_name in {r["name"] for r in ROOTS}


def test_duplicate_root_category_is_rejected_by_db(scratch_db):
    """UNIQUE(name, parent_id) does NOT protect roots in Postgres (NULLs differ).

    Two partial unique indexes are what actually prevent duplicate roots.
    """
    cfg = _alembic_config(scratch_db)
    command.upgrade(cfg, "head")

    from sqlalchemy import text as sql_text
    from sqlalchemy.exc import IntegrityError

    eng = sync_engine(f"sqlite:///{scratch_db}")
    try:
        with eng.begin() as conn:
            conn.execute(sql_text(
                "INSERT INTO categories (name, type) VALUES ('EXPENSE', 'EXPENSE')"
            ))
        # Second root with the same name must fail
        with pytest.raises(IntegrityError):
            with eng.begin() as conn:
                conn.execute(sql_text(
                    "INSERT INTO categories (name, type) VALUES ('EXPENSE', 'EXPENSE')"
                ))

        # Siblings under the same parent are still unique
        with eng.begin() as conn:
            for name in ("Shopping", "Food"):
                conn.execute(
                    sql_text(
                        "INSERT INTO categories (name, type, parent_id) "
                        "VALUES (:n, 'EXPENSE', 1)"
                    ),
                    {"n": name},
                )
        with pytest.raises(IntegrityError):
            with eng.begin() as conn:
                conn.execute(sql_text(
                    "INSERT INTO categories (name, type, parent_id) "
                    "VALUES ('Shopping', 'EXPENSE', 1)"
                ))

        # Same child name under a DIFFERENT parent is allowed
        with eng.begin() as conn:
            conn.execute(sql_text(
                "INSERT INTO categories (name, type) VALUES ('INCOME', 'INCOME')"
            ))
            second_root = conn.execute(sql_text(
                "SELECT id FROM categories WHERE name = 'INCOME'"
            )).scalar_one()
            conn.execute(sql_text(
                "INSERT INTO categories (name, type, parent_id) VALUES ('Gift', 'EXPENSE', 1)"
            ))
            conn.execute(
                sql_text(
                    "INSERT INTO categories (name, type, parent_id) "
                    "VALUES ('Gift', 'INCOME', :pid)"
                ),
                {"pid": second_root},
            )
    finally:
        eng.dispose()


def test_app_refuses_to_boot_without_migrations(tmp_path, monkeypatch):
    """init_db() must fail loudly rather than silently running on a stale schema."""
    import asyncio

    from sqlalchemy import create_engine as sync_engine
    from sqlalchemy.ext.asyncio import create_async_engine

    empty = tmp_path / "empty.db"
    sync_engine(f"sqlite:///{empty}").dispose()  # create the file, no tables

    eng = create_async_engine(f"sqlite+aiosqlite:///{empty}")

    import app.database as db_module

    monkeypatch.setattr(db_module, "engine", eng)
    with pytest.raises(RuntimeError, match="alembic upgrade head"):
        asyncio.run(db_module.init_db())
    asyncio.run(eng.dispose())


def test_schema_check_passes_after_migration(scratch_db):
    cfg = _alembic_config(scratch_db)
    command.upgrade(cfg, "head")

    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine

    import app.database as db_module

    real_engine = db_module.engine
    eng = create_async_engine(f"sqlite+aiosqlite:///{scratch_db}")
    try:
        db_module.engine = eng
        assert asyncio.run(db_module._schema_is_current()) is True
    finally:
        db_module.engine = real_engine
        asyncio.run(eng.dispose())