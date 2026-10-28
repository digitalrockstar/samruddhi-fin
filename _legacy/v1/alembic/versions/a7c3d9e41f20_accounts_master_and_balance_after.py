"""accounts master (balances, investments) and per-transaction balance_after

Revision ID: a7c3d9e41f20
Revises: 1965608f8cb7
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7c3d9e41f20"
down_revision: Union[str, None] = "1965608f8cb7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TYPES = ("LOAN", "MUTUAL_FUND", "FIXED_DEPOSIT", "STOCKS", "OTHER_INVESTMENT")
ACCOUNT_COLS = [
    ("last_six", sa.String(6)),
    ("notes", sa.String(500)),
    ("credit_limit", sa.Numeric(15, 2)),
    ("balance", sa.Numeric(15, 2)),
    ("balance_as_of", sa.DateTime(timezone=True)),
    ("invested_amount", sa.Numeric(15, 2)),
    ("current_value", sa.Numeric(15, 2)),
    ("valuation_as_of", sa.DateTime(timezone=True)),
]


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ADD VALUE cannot run inside a transaction block on older Postgres.
        with op.get_context().autocommit_block():
            for v in NEW_TYPES:
                op.execute(f"ALTER TYPE accounttype ADD VALUE IF NOT EXISTS '{v}'")
    else:
        # SQLite stores the enum as VARCHAR(longest name); widen it so the schema
        # matches the model (the drift test compares them).
        with op.batch_alter_table("accounts") as batch:
            batch.alter_column(
                "account_type", existing_nullable=False,
                type_=sa.Enum("CREDIT_CARD", "DEBIT_CARD", "SAVINGS", "CURRENT", "UPI", "WALLET",
                              *NEW_TYPES, name="accounttype"))
    for name, typ in ACCOUNT_COLS:
        op.add_column("accounts", sa.Column(name, typ, nullable=True))
    op.add_column("transactions", sa.Column("balance_after", sa.Numeric(15, 2), nullable=True))


def downgrade() -> None:
    op.drop_column("transactions", "balance_after")
    for name, _ in reversed(ACCOUNT_COLS):
        op.drop_column("accounts", name)
    # Postgres cannot drop enum values; the extra accounttype members are left in place.
