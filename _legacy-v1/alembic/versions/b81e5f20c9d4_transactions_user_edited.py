"""transactions.user_edited so reprocessing keeps manual edits

Revision ID: b81e5f20c9d4
Revises: a7c3d9e41f20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b81e5f20c9d4"
down_revision: Union[str, None] = "a7c3d9e41f20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("transactions", sa.Column("user_edited", sa.Boolean(), nullable=False,
                                            server_default=sa.false()))


def downgrade() -> None:
    op.drop_column("transactions", "user_edited")
