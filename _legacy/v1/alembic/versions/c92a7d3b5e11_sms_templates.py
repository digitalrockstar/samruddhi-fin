"""sms_templates: person-decided message formats

Revision ID: c92a7d3b5e11
Revises: b81e5f20c9d4
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c92a7d3b5e11"
down_revision: Union[str, None] = "b81e5f20c9d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sms_templates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("skeleton", sa.String(120), nullable=False),
        sa.Column("decision", sa.String(12), nullable=False),
        sa.Column("mode", sa.String(20), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_sms_templates_skeleton", "sms_templates", ["skeleton"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_sms_templates_skeleton", table_name="sms_templates")
    op.drop_table("sms_templates")
