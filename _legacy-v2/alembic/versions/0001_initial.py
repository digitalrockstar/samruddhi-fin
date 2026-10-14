from alembic import op
import sqlalchemy as sa

revision="0001_initial"
down_revision=None
branch_labels=None
depends_on=None

def upgrade():
    op.create_table("raw_messages",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("chat_id", sa.BigInteger, nullable=False),
        sa.Column("message_id", sa.Integer, nullable=False),
        sa.Column("message_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("sender_id", sa.BigInteger),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("group_key", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("chat_id", "message_id", name="uq_raw_chat_message"))
    op.create_index("ix_raw_messages_chat_id", "raw_messages", ["chat_id"])
    op.create_index("ix_raw_messages_message_date", "raw_messages", ["message_date"])
    op.create_table("tags",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(80), unique=True),
        sa.Column("counts_as_transaction", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()))
    op.create_table("learned_formats",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("pattern", sa.Text, nullable=False),
        sa.Column("specificity", sa.Integer, nullable=False, server_default="0"),
        sa.Column("tag_id", sa.Integer, sa.ForeignKey("tags.id"), nullable=False),
        sa.Column("direction", sa.String(30)),
        sa.Column("direction_keywords", sa.Text, nullable=False, server_default=""),
        sa.Column("mode", sa.String(30)),
        sa.Column("account", sa.String(120)),
        sa.Column("owner", sa.String(120)),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("match_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("reviews",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("raw_message_id", sa.Integer, sa.ForeignKey("raw_messages.id"), unique=True),
        sa.Column("tag_id", sa.Integer, sa.ForeignKey("tags.id")),
        sa.Column("format_id", sa.Integer, sa.ForeignKey("learned_formats.id")),
        sa.Column("decision_source", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True)))
    op.create_table("transactions",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("raw_message_id", sa.Integer, sa.ForeignKey("raw_messages.id"), unique=True),
        sa.Column("format_id", sa.Integer, sa.ForeignKey("learned_formats.id"), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("amount", sa.Numeric(18,2), nullable=False),
        sa.Column("merchant", sa.String(255)),
        sa.Column("reference_number", sa.String(255)),
        sa.Column("balance", sa.Numeric(18,2)),
        sa.Column("direction", sa.String(30), nullable=False),
        sa.Column("mode", sa.String(30)),
        sa.Column("account", sa.String(120)),
        sa.Column("owner", sa.String(120)),
        sa.Column("amount_span", sa.String(80)),
        sa.Column("merchant_span", sa.String(80)),
        sa.Column("reference_span", sa.String(80)),
        sa.Column("balance_span", sa.String(80)),
        sa.Column("direction_span", sa.String(80)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()))
    op.create_table("ingest_state",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("chat_id", sa.BigInteger, unique=True, nullable=False),
        sa.Column("last_message_id", sa.Integer, nullable=False, server_default="0"))
    op.bulk_insert(sa.table("tags",
        sa.column("name", sa.String), sa.column("counts_as_transaction", sa.Boolean), sa.column("enabled", sa.Boolean)),
        [{"name":"OTP","counts_as_transaction":False,"enabled":True},
         {"name":"Txn","counts_as_transaction":True,"enabled":True},
         {"name":"Marketing","counts_as_transaction":False,"enabled":True},
         {"name":"Spam","counts_as_transaction":False,"enabled":True}])

def downgrade():
    for table in ["ingest_state","transactions","reviews","learned_formats","tags","raw_messages"]:
        op.drop_table(table)
