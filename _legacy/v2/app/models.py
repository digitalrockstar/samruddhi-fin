from datetime import datetime
from decimal import Decimal
from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase): pass

class RawMessage(Base):
    __tablename__ = "raw_messages"
    __table_args__ = (UniqueConstraint("chat_id", "message_id", name="uq_raw_chat_message"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    message_id: Mapped[int] = mapped_column(Integer)
    message_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sender_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    text: Mapped[str] = mapped_column(Text, default="")
    group_key: Mapped[str] = mapped_column(String(64), index=True, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    review: Mapped["Review"] = relationship(back_populates="raw", uselist=False)

class Tag(Base):
    __tablename__ = "tags"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    counts_as_transaction: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

class LearnedFormat(Base):
    __tablename__ = "learned_formats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    pattern: Mapped[str] = mapped_column(Text)
    specificity: Mapped[int] = mapped_column(Integer, default=0)
    tag_id: Mapped[int] = mapped_column(ForeignKey("tags.id"))
    direction: Mapped[str | None] = mapped_column(String(30), nullable=True)
    direction_keywords: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    account: Mapped[str | None] = mapped_column(String(120), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    match_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class Review(Base):
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_message_id: Mapped[int] = mapped_column(ForeignKey("raw_messages.id"), unique=True)
    tag_id: Mapped[int | None] = mapped_column(ForeignKey("tags.id"), nullable=True)
    format_id: Mapped[int | None] = mapped_column(ForeignKey("learned_formats.id"), nullable=True)
    decision_source: Mapped[str] = mapped_column(String(20), default="manual")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    raw: Mapped[RawMessage] = relationship(back_populates="review")

class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    raw_message_id: Mapped[int] = mapped_column(ForeignKey("raw_messages.id"), unique=True)
    format_id: Mapped[int] = mapped_column(ForeignKey("learned_formats.id"))
    source: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    merchant: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reference_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    balance: Mapped[Decimal | None] = mapped_column(Numeric(18, 2), nullable=True)
    direction: Mapped[str] = mapped_column(String(30))
    mode: Mapped[str | None] = mapped_column(String(30), nullable=True)
    account: Mapped[str | None] = mapped_column(String(120), nullable=True)
    owner: Mapped[str | None] = mapped_column(String(120), nullable=True)
    amount_span: Mapped[str | None] = mapped_column(String(80), nullable=True)
    merchant_span: Mapped[str | None] = mapped_column(String(80), nullable=True)
    reference_span: Mapped[str | None] = mapped_column(String(80), nullable=True)
    balance_span: Mapped[str | None] = mapped_column(String(80), nullable=True)
    direction_span: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class IngestState(Base):
    __tablename__ = "ingest_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    last_message_id: Mapped[int] = mapped_column(Integer, default=0)
