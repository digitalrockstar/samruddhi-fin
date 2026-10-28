from sqlalchemy import false as sa_false
from sqlalchemy import Column, Integer, BigInteger, String, DateTime, Boolean, ForeignKey, UniqueConstraint, Index, Text, Numeric, Enum as SQLEnum, text
from sqlalchemy.orm import relationship, declared_attr
from sqlalchemy.sql import func
from app.database import Base
import enum


class Person(Base):
    __tablename__ = "persons"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    prefix = Column(String(10), unique=True, nullable=False)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    accounts = relationship("Account", back_populates="person")
    transactions = relationship("Transaction", back_populates="person")
    allocations = relationship("TransactionAllocation", back_populates="person")


class AccountType(str, enum.Enum):
    CREDIT_CARD = "credit_card"
    DEBIT_CARD = "debit_card"
    SAVINGS = "savings"
    CURRENT = "current"
    UPI = "upi"
    WALLET = "wallet"
    LOAN = "loan"
    MUTUAL_FUND = "mutual_fund"
    FIXED_DEPOSIT = "fixed_deposit"
    STOCKS = "stocks"
    OTHER_INVESTMENT = "other_investment"


class CashbackType(str, enum.Enum):
    CASH = "cash"
    POINTS = "points"
    WALLET = "wallet"


class Account(Base):
    __tablename__ = "accounts"

    id = Column(Integer, primary_key=True)
    person_id = Column(Integer, ForeignKey("persons.id"), nullable=False)
    bank_name = Column(String(100), nullable=False)
    account_type = Column(SQLEnum(AccountType), nullable=False)
    last_four = Column(String(4))
    last_six = Column(String(6))
    upi_id = Column(String(100))
    nickname = Column(String(50))
    notes = Column(String(500))

    # Money position. For savings/current/wallet: `balance` is cash held.
    # For credit cards and loans: `balance` is the OUTSTANDING amount owed.
    # Cards also carry `credit_limit`. `balance` is a manual checkpoint; SMS-reported
    # balances newer than `balance_as_of` take over (see /api/accounts/summary).
    credit_limit = Column(Numeric(15, 2))
    balance = Column(Numeric(15, 2))
    balance_as_of = Column(DateTime(timezone=True))

    # Investments (MF / FD / stocks / other): updated by hand, infrequently.
    invested_amount = Column(Numeric(15, 2))
    current_value = Column(Numeric(15, 2))
    valuation_as_of = Column(DateTime(timezone=True))
    
    # Cashback defaults at card level
    default_cashback_pct = Column(Numeric(5, 2), default=0)
    default_cashback_type = Column(SQLEnum(CashbackType))
    default_cashback_wallet = Column(String(50))
    
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    person = relationship("Person", back_populates="accounts")
    transactions = relationship("Transaction", back_populates="account")


class CategoryType(str, enum.Enum):
    EXPENSE = "expense"
    INCOME = "income"
    TRANSFER = "transfer"
    IGNORE = "ignore"


class Category(Base):
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True)
    name = Column(String(50), nullable=False)
    type = Column(SQLEnum(CategoryType), nullable=False)
    parent_id = Column(Integer, ForeignKey("categories.id"))
    icon = Column(String(30))
    is_system = Column(Boolean, default=False)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    parent = relationship("Category", remote_side=[id], backref="children")
    transactions = relationship("Transaction", back_populates="category")

    __table_args__ = (
        Index("idx_category_type", "type"),
        # Postgres treats NULLs as distinct in UNIQUE constraints, so a plain
        # UNIQUE(name, parent_id) would allow unlimited duplicate ROOT rows.
        # Two partial indexes give the real guarantee on both roots and children.
        Index(
            "uq_category_root_name",
            "name",
            unique=True,
            sqlite_where=text("parent_id IS NULL"),
            postgresql_where=text("parent_id IS NULL"),
        ),
        Index(
            "uq_category_child_name",
            "name",
            "parent_id",
            unique=True,
            sqlite_where=text("parent_id IS NOT NULL"),
            postgresql_where=text("parent_id IS NOT NULL"),
        ),
    )


class TransactionType(str, enum.Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class TransactionMode(str, enum.Enum):
    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"
    ATM = "atm"
    WALLET = "wallet"
    CASH = "cash"
    IMPS = "imps"
    NEFT = "neft"
    RTGS = "rtgs"


class CashbackStatus(str, enum.Enum):
    PENDING = "pending"
    RECEIVED = "received"
    EXPIRED = "expired"


class Transaction(Base):
    __tablename__ = "transactions"

    id = Column(Integer, primary_key=True)
    message_id = Column(BigInteger, unique=True, index=True)
    person_id = Column(Integer, ForeignKey("persons.id"), nullable=False)
    account_id = Column(Integer, ForeignKey("accounts.id"))
    category_id = Column(Integer, ForeignKey("categories.id"))
    raw_text = Column(Text, nullable=False)
    parsed_amount = Column(Numeric(15, 2), nullable=False)
    currency = Column(String(3), default="INR")
    txn_type = Column(SQLEnum(TransactionType), nullable=False)
    txn_mode = Column(SQLEnum(TransactionMode))
    merchant = Column(String(200))
    upi_ref = Column(String(100))
    # Balance (or card available limit) printed in the SMS after this transaction.
    balance_after = Column(Numeric(15, 2))
    # True once a person edits the row; reprocessing then leaves it alone.
    user_edited = Column(Boolean, nullable=False, default=False, server_default=sa_false())
    txn_timestamp = Column(DateTime(timezone=True), nullable=False, index=True)
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # Cashback tracking
    cashback_amount = Column(Numeric(15, 2), default=0)
    cashback_type = Column(SQLEnum(CashbackType))
    cashback_wallet = Column(String(50))
    cashback_status = Column(SQLEnum(CashbackStatus), default=CashbackStatus.PENDING)
    
    is_spam = Column(Boolean, default=False, index=True)
    needs_review = Column(Boolean, default=False, index=True)
    is_split_parent = Column(Boolean, default=False)
    parent_txn_id = Column(Integer, ForeignKey("transactions.id"))
    notes = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    person = relationship("Person", back_populates="transactions")
    account = relationship("Account", back_populates="transactions")
    category = relationship("Category", back_populates="transactions")
    allocations = relationship(
        "TransactionAllocation",
        back_populates="transaction",
        cascade="all, delete-orphan",
    )
    # Splits are modelled with transaction_allocations (one row per person),
    # so no self-referential relationship is needed.
    merchant_mappings = relationship("MerchantMapping", back_populates="transaction")


class AllocationType(str, enum.Enum):
    EXPENSE_FOR = "expense_for"
    INCOME_FROM = "income_from"
    PAID_BY = "paid_by"


class TransactionAllocation(Base):
    __tablename__ = "transaction_allocations"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id", ondelete="CASCADE"), nullable=False)
    person_id = Column(Integer, ForeignKey("persons.id"))
    amount = Column(Numeric(15, 2), nullable=False)
    allocation_type = Column(SQLEnum(AllocationType), nullable=False)
    notes = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    transaction = relationship("Transaction", back_populates="allocations")
    person = relationship("Person", back_populates="allocations")

    __table_args__ = (
        Index("idx_alloc_transaction", "transaction_id"),
        Index("idx_alloc_person", "person_id"),
    )


class RawMessage(Base):
    """Every inbound message, kept verbatim before any parsing.

    This is the system's source of truth. Transactions are a *derived* view:
    if the parser improves, `POST /api/reprocess` rebuilds them from here.
    Messages that turn out not to be transactions are still stored, so the
    parser's rejections stay auditable and explainable.
    """
    __tablename__ = "raw_messages"

    id = Column(Integer, primary_key=True)
    source = Column(String(20), nullable=False, default="telegram")

    # Identity, so re-delivery is a no-op
    external_id = Column(String(64), index=True)
    chat_id = Column(String(32))
    sender_user_id = Column(String(32))
    sender_name = Column(String(80))

    prefix_detected = Column(String(10))     # AP- / AS- / None
    body = Column(Text, nullable=False)      # SMS text, headers stripped
    raw_text = Column(Text)                  # exactly what arrived
    received_at = Column(DateTime(timezone=True), server_default=func.now())
    txn_date_hint = Column(DateTime(timezone=True))  # from a "Time:" header

    # Parser outcome, cached so reprocessing can find what changed
    verdict = Column(String(20))
    reason = Column(String(20))
    rule = Column(String(60))
    amount = Column(Numeric(15, 2))
    txn_type = Column(SQLEnum(TransactionType))
    txn_mode = Column(SQLEnum(TransactionMode))
    account_last_four = Column(String(4))
    merchant = Column(String(200))
    upi_ref = Column(String(100))
    upi_handle = Column(String(120))
    transfer_kind = Column(String(20))
    confidence = Column(Numeric(3, 2))
    parsed_at = Column(DateTime(timezone=True))
    parser_version = Column(String(40))

    transaction_id = Column(Integer, ForeignKey("transactions.id"), index=True)
    note = Column(String(120))
    updated_at = Column(DateTime(timezone=True), server_default=func.now(),
                        onupdate=func.now())

    __table_args__ = (
        Index("ux_raw_source_ext", "source", "external_id", unique=True),
        Index("idx_raw_verdict", "verdict"),
        Index("idx_raw_received", "received_at"),
        Index("idx_raw_parsed_at", "parsed_at"),
    )


class MerchantMapping(Base):
    __tablename__ = "merchant_mappings"

    id = Column(Integer, primary_key=True)
    pattern = Column(String(200), nullable=False, unique=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False)
    confidence = Column(Integer, default=100)  # 0-100
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_used_at = Column(DateTime(timezone=True))
    transaction_id = Column(Integer, ForeignKey("transactions.id"))

    category = relationship("Category")
    transaction = relationship("Transaction", back_populates="merchant_mappings")

    __table_args__ = (
        Index("idx_merchant_mapping_pattern", "pattern"),
        Index("idx_merchant_mapping_active", "is_active"),
    )


class RecurringTransaction(Base):
    __tablename__ = "recurring_transactions"

    id = Column(Integer, primary_key=True)
    transaction_id = Column(Integer, ForeignKey("transactions.id"), nullable=False)
    merchant = Column(String(200), nullable=False)
    amount = Column(Numeric(15, 2), nullable=False)
    frequency = Column(String(20), nullable=False)  # monthly, quarterly, yearly
    next_expected_date = Column(DateTime(timezone=True))
    is_active = Column(Boolean, default=True)
    confidence = Column(Integer, default=50)  # 0-100
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_detected_at = Column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_recurring_next_date", "next_expected_date"),
    )

class SmsTemplate(Base):
    """A message format and what a person decided it is (ignore / debit / credit / transfer)."""
    __tablename__ = "sms_templates"

    id = Column(Integer, primary_key=True)
    skeleton = Column(String(120), nullable=False, unique=True, index=True)
    decision = Column(String(12), nullable=False)
    mode = Column(String(20))
    decided_at = Column(DateTime(timezone=True), server_default=func.now())
