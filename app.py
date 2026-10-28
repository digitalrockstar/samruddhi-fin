from __future__ import annotations

import csv
import calendar
import io
import json
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import wraps
from typing import Any

from flask import Flask, Response, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint, func, inspect as sqlalchemy_inspect, or_, text
from werkzeug.security import check_password_hash, generate_password_hash

db = SQLAlchemy()

DEFAULT_CATEGORIES = [
    ("Groceries", "expense", "basket", "#80b895"),
    ("Dining", "expense", "utensils", "#e5a071"),
    ("Transport", "expense", "car", "#7297d2"),
    ("Home", "expense", "house", "#a28ac7"),
    ("Bills", "expense", "receipt", "#d5b45f"),
    ("Health", "expense", "heart", "#dc91a6"),
    ("Shopping", "expense", "bag", "#70afb0"),
    ("Other expense", "expense", "more", "#9da8a0"),
    ("Salary", "income", "wallet", "#80b895"),
    ("Other income", "income", "trend", "#7297d2"),
]

TRANSFER_CATEGORIES = [
    ("Investment", "transfer", "trending-up", "#a28ac7"),
    ("CC Payment", "transfer", "credit-card", "#e5a071"),
    ("Self-Transfer", "transfer", "repeat", "#7297d2"),
]
ACCOUNT_TYPES = {"cash", "bank", "savings", "credit", "investment", "other"}
KINDS = {"income", "expense", "transfer"}
COLOUR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def utcnow_naive() -> datetime:
    """Return UTC time as a naive value for consistent SQLite DateTime round-trips."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Account(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    account_type = db.Column(db.String(20), nullable=False, default="bank")
    opening_balance_paise = db.Column(db.Integer, nullable=False, default=0)
    colour = db.Column(db.String(7), nullable=False, default="#7297d2")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)


class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    kind = db.Column(db.String(12), nullable=False)
    icon = db.Column(db.String(24), nullable=False, default="circle")
    colour = db.Column(db.String(7), nullable=False, default="#7297d2")


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_date = db.Column(db.Date, nullable=False, default=date.today, index=True)
    kind = db.Column(db.String(12), nullable=False, index=True)
    amount_paise = db.Column(db.Integer, nullable=False)
    description = db.Column(db.String(180), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    transfer_to_account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=True, index=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False, index=True)
    notes = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)

    account = db.relationship("Account", foreign_keys=[account_id])
    transfer_to = db.relationship("Account", foreign_keys=[transfer_to_account_id])
    category = db.relationship("Category")


class ImportBatch(db.Model):
    id = db.Column(db.String(40), primary_key=True)
    rows_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    consumed = db.Column(db.Boolean, nullable=False, default=False)


class CreditLimitGroup(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    limit_paise = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)


class CreditCardProfile(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), unique=True, nullable=False, index=True)
    limit_mode = db.Column(db.String(12), nullable=False, default="individual")
    individual_limit_paise = db.Column(db.Integer, nullable=True)
    shared_group_id = db.Column(db.Integer, db.ForeignKey("credit_limit_group.id"), nullable=True, index=True)
    statement_day = db.Column(db.Integer, nullable=False, default=1)
    due_day = db.Column(db.Integer, nullable=False, default=20)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    account = db.relationship("Account")
    shared_group = db.relationship("CreditLimitGroup")


class CreditCardStatement(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    statement_month = db.Column(db.String(7), nullable=False, index=True)
    statement_date = db.Column(db.Date, nullable=False)
    due_date = db.Column(db.Date, nullable=False, index=True)
    total_due_paise = db.Column(db.Integer, nullable=False)
    minimum_due_paise = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    account = db.relationship("Account")
    __table_args__ = (UniqueConstraint("account_id", "statement_month", name="uq_card_statement_month"),)


class StatementPayment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    statement_id = db.Column(db.Integer, db.ForeignKey("credit_card_statement.id"), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), unique=True, nullable=False, index=True)
    amount_paise = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    statement = db.relationship("CreditCardStatement")
    transaction = db.relationship("Transaction")


class FamilyMember(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    # Nullable for safe additive migration of existing local databases; new accounts always set it.
    username = db.Column(db.String(64), nullable=True)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="member")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)


class AccountOwner(db.Model):
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), primary_key=True)
    # NULL means a shared family account; a member id means individually owned.
    member_id = db.Column(db.Integer, db.ForeignKey("family_member.id"), nullable=True, index=True)
    account = db.relationship("Account")
    member = db.relationship("FamilyMember")


class AccountPurposeTag(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    tag = db.Column(db.String(32), nullable=False)
    __table_args__ = (UniqueConstraint("account_id", "tag", name="uq_account_purpose_tag"),)


class CategoryParent(db.Model):
    subcategory_id = db.Column(db.Integer, db.ForeignKey("category.id"), primary_key=True)
    parent_category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=False, index=True)
    parent = db.relationship("Category", foreign_keys=[parent_category_id])
    subcategory = db.relationship("Category", foreign_keys=[subcategory_id])


class Budget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    owner_type = db.Column(db.String(12), nullable=False, default="family")
    member_id = db.Column(db.Integer, db.ForeignKey("family_member.id"), nullable=True, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True, index=True)
    period_type = db.Column(db.String(12), nullable=False, default="monthly")
    amount_paise = db.Column(db.Integer, nullable=False)
    spread_monthly = db.Column(db.Boolean, nullable=False, default=False)
    start_date = db.Column(db.Date, nullable=False, default=date.today)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    member = db.relationship("FamilyMember")
    category = db.relationship("Category")


class Goal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    owner_type = db.Column(db.String(12), nullable=False, default="family")
    member_id = db.Column(db.Integer, db.ForeignKey("family_member.id"), nullable=True, index=True)
    target_paise = db.Column(db.Integer, nullable=False)
    target_date = db.Column(db.Date, nullable=True)
    monthly_contribution_paise = db.Column(db.Integer, nullable=False, default=0)
    contribution_source_account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=True)
    contribution_destination_account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=True)
    next_contribution_date = db.Column(db.Date, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    member = db.relationship("FamilyMember")
    contribution_source = db.relationship("Account", foreign_keys=[contribution_source_account_id])
    contribution_destination = db.relationship("Account", foreign_keys=[contribution_destination_account_id])


class GoalEarmark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    goal_id = db.Column(db.Integer, db.ForeignKey("goal.id"), nullable=False, index=True)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    amount_paise = db.Column(db.Integer, nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive, onupdate=utcnow_naive)
    goal = db.relationship("Goal")
    account = db.relationship("Account")
    __table_args__ = (UniqueConstraint("goal_id", "account_id", name="uq_goal_account_earmark"),)


class TransactionMetadata(db.Model):
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), primary_key=True)
    budget_id = db.Column(db.Integer, db.ForeignKey("budget.id"), nullable=True, index=True)
    budget_scope = db.Column(db.String(12), nullable=False, default="family")
    source = db.Column(db.String(20), nullable=False, default="manual")
    # Snapshots payer ownership at transaction time. A deliberate NULL snapshot means shared family account.
    payer_member_id = db.Column(db.Integer, db.ForeignKey("family_member.id"), nullable=True, index=True)
    payer_snapshot_set = db.Column(db.Boolean, nullable=False, default=False)
    budget = db.relationship("Budget")
    transaction = db.relationship("Transaction")
    payer = db.relationship("FamilyMember")


class TransactionBeneficiary(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), nullable=False, index=True)
    member_id = db.Column(db.Integer, db.ForeignKey("family_member.id"), nullable=False, index=True)
    amount_paise = db.Column(db.Integer, nullable=False)
    split_method = db.Column(db.String(12), nullable=False, default="equal")
    member = db.relationship("FamilyMember")
    transaction = db.relationship("Transaction")
    __table_args__ = (UniqueConstraint("transaction_id", "member_id", name="uq_transaction_beneficiary"),)


class CashbackScheme(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    merchant_keyword = db.Column(db.String(100), nullable=False)
    rate_bps = db.Column(db.Integer, nullable=False)
    destination_account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False)
    delay_days = db.Column(db.Integer, nullable=False, default=0)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    notes = db.Column(db.String(300), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    account = db.relationship("Account", foreign_keys=[account_id])
    destination_account = db.relationship("Account", foreign_keys=[destination_account_id])


class Subscription(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False, index=True)
    category_id = db.Column(db.Integer, db.ForeignKey("category.id"), nullable=True)
    amount_paise = db.Column(db.Integer, nullable=False)
    frequency = db.Column(db.String(16), nullable=False, default="monthly")
    interval_weeks = db.Column(db.Integer, nullable=False, default=6)
    next_date = db.Column(db.Date, nullable=False, index=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_archived = db.Column(db.Boolean, nullable=False, default=False)
    notes = db.Column(db.String(500), nullable=False, default="")
    budget_id = db.Column(db.Integer, db.ForeignKey("budget.id"), nullable=True)
    budget_scope = db.Column(db.String(12), nullable=False, default="family")
    beneficiary_ids_json = db.Column(db.Text, nullable=False, default="[]")
    cashback_scheme_id = db.Column(db.Integer, db.ForeignKey("cashback_scheme.id"), nullable=True)
    cashback_rate_bps = db.Column(db.Integer, nullable=True)
    cashback_destination_account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=True)
    cashback_delay_days = db.Column(db.Integer, nullable=False, default=0)
    created_from_transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), unique=True, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    account = db.relationship("Account", foreign_keys=[account_id])
    category = db.relationship("Category")
    budget = db.relationship("Budget")
    cashback_scheme = db.relationship("CashbackScheme")
    cashback_destination_account = db.relationship("Account", foreign_keys=[cashback_destination_account_id])
    origin_transaction = db.relationship("Transaction", foreign_keys=[created_from_transaction_id])


class SubscriptionOccurrence(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    subscription_id = db.Column(db.Integer, db.ForeignKey("subscription.id"), nullable=False, index=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), unique=True, nullable=False, index=True)
    occurrence_date = db.Column(db.Date, nullable=False)
    subscription = db.relationship("Subscription")
    transaction = db.relationship("Transaction")
    __table_args__ = (UniqueConstraint("subscription_id", "occurrence_date", name="uq_subscription_occurrence_date"),)


class CashbackEvent(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), unique=True, nullable=False, index=True)
    scheme_id = db.Column(db.Integer, db.ForeignKey("cashback_scheme.id"), nullable=True)
    expected_paise = db.Column(db.Integer, nullable=False)
    actual_paise = db.Column(db.Integer, nullable=True)
    destination_account_id = db.Column(db.Integer, db.ForeignKey("account.id"), nullable=False)
    expected_date = db.Column(db.Date, nullable=False, index=True)
    status = db.Column(db.String(24), nullable=False, default="scheduled", index=True)
    posted_transaction_id = db.Column(db.Integer, db.ForeignKey("transaction.id"), unique=True, nullable=True)
    notes = db.Column(db.String(300), nullable=False, default="")
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow_naive)
    transaction = db.relationship("Transaction", foreign_keys=[transaction_id])
    scheme = db.relationship("CashbackScheme")
    destination_account = db.relationship("Account")
    posted_transaction = db.relationship("Transaction", foreign_keys=[posted_transaction_id])


def money_to_paise(value: Any, *, allow_zero: bool = False) -> int:
    """Parse currency without binary floats and store the result as integer paise."""
    try:
        amount = Decimal(str(value).replace(",", "").replace("₹", "").strip())
    except (InvalidOperation, ValueError):
        raise ValueError("Enter a valid amount.") from None
    if not amount.is_finite() or amount < 0 or (amount == 0 and not allow_zero):
        raise ValueError("Amount must be greater than zero." if not allow_zero else "Amount cannot be negative.")
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def format_money(paise: int | None) -> str:
    value = int(paise or 0)
    sign = "−" if value < 0 else ""
    value = abs(value)
    return f"{sign}₹{value // 100:,}.{value % 100:02d}"


def month_bounds(month_text: str | None) -> tuple[date, date, str]:
    try:
        start = datetime.strptime(month_text or date.today().strftime("%Y-%m"), "%Y-%m").date().replace(day=1)
    except ValueError:
        start = date.today().replace(day=1)
    end = date(start.year + (start.month == 12), 1 if start.month == 12 else start.month + 1, 1)
    return start, end, start.strftime("%Y-%m")


def normalized_colour(value: str | None) -> str:
    colour = (value or "").strip()
    return colour if COLOUR_RE.fullmatch(colour) else "#7297d2"


def seed_defaults() -> None:
    if not Category.query.first():
        db.session.add_all([
            Category(name=name, kind=kind, icon=icon, colour=colour)
            for name, kind, icon, colour in DEFAULT_CATEGORIES
        ])
    if not Account.query.first():
        db.session.add_all([
            Account(name="Cash", account_type="cash", colour="#80b895"),
            Account(name="Main bank", account_type="bank", colour="#7297d2"),
            Account(name="Savings", account_type="savings", colour="#a28ac7"),
            Account(name="Credit card", account_type="credit", colour="#e5a071"),
        ])
    db.session.commit()


def make_login_required():
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if current_app.config.get("TESTING") and not current_app.config.get("TEST_AUTH_REQUIRED"):
                return view(*args, **kwargs)
            member_id = session.get("member_id")
            if member_id and db.session.get(FamilyMember, int(member_id)):
                member = db.session.get(FamilyMember, int(member_id))
                if member and member.is_active:
                    return view(*args, **kwargs)
            if FamilyMember.query.filter_by(is_active=True).count() == 0:
                return redirect(url_for("setup"))
            return redirect(url_for("login", next=request.path))
        return wrapped
    return decorator


def current_password() -> str:
    from flask import current_app
    return str(current_app.config.get("APP_PASSWORD") or os.environ.get("APP_PASSWORD") or "")


def ensure_additive_schema() -> None:
    """Apply safe additive changes for databases created by earlier development builds."""
    inspector = sqlalchemy_inspect(db.engine)
    table_names = set(inspector.get_table_names())
    changes = [
        ("family_member", "username", "VARCHAR(64)"),
        ("account", "is_active", "BOOLEAN NOT NULL DEFAULT TRUE"),
        ("goal", "monthly_contribution_paise", "INTEGER NOT NULL DEFAULT 0"),
        ("goal", "contribution_source_account_id", "INTEGER REFERENCES account(id)"),
        ("goal", "contribution_destination_account_id", "INTEGER REFERENCES account(id)"),
        ("goal", "next_contribution_date", "DATE"),
        ("subscription", "is_archived", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("transaction_metadata", "payer_member_id", "INTEGER REFERENCES family_member(id)"),
        ("transaction_metadata", "payer_snapshot_set", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ("subscription", "cashback_scheme_id", "INTEGER REFERENCES cashback_scheme(id)"),
        ("subscription", "cashback_rate_bps", "INTEGER"),
        ("subscription", "cashback_destination_account_id", "INTEGER REFERENCES account(id)"),
        ("subscription", "cashback_delay_days", "INTEGER NOT NULL DEFAULT 0"),
    ]
    columns_by_table = {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in table_names if table in {"family_member", "transaction_metadata", "subscription", "account", "goal"}
    }
    for table, column, definition in changes:
        if table in table_names and column not in columns_by_table.get(table, set()):
            db.session.execute(text(f'ALTER TABLE "{table}" ADD COLUMN "{column}" {definition}'))

    # Existing accounts predate usernames. Derive a stable login handle from the email
    # local part, adding a member-id suffix only when two handles would collide.
    if "family_member" in table_names:
        members = FamilyMember.query.order_by(FamilyMember.id).all()
        used = {member.username.casefold() for member in members if member.username}
        for member in members:
            if member.username and member.username.strip():
                continue
            base = re.sub(r"[^a-z0-9._-]", "", (member.email.split("@", 1)[0] or "").lower())[:64]
            base = base or f"member{member.id}"
            candidate = base
            if candidate.casefold() in used:
                suffix = f"_{member.id}"
                candidate = f"{base[:64 - len(suffix)]}{suffix}"
                counter = 2
                while candidate.casefold() in used:
                    suffix = f"_{member.id}_{counter}"
                    candidate = f"{base[:64 - len(suffix)]}{suffix}"
                    counter += 1
            member.username = candidate
            used.add(candidate.casefold())
        db.session.flush()
        db.session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_family_member_username ON family_member(username)"
        ))

    if "subscription_occurrence" in table_names:
        db.session.execute(text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_subscription_occurrence_date "
            "ON subscription_occurrence(subscription_id, occurrence_date)"
        ))
    db.session.commit()


def ensure_primary_admin() -> None:
    """Keep one recoverable household administrator, always identified as akshayp."""
    members = FamilyMember.query.order_by(FamilyMember.id).all()
    active_members = [member for member in members if member.is_active]
    if not active_members:
        return

    primary = next(
        (member for member in active_members if (member.username or "").casefold() == "akshayp"),
        None,
    )
    if primary is None:
        # If a prior local database already has a household, keep its oldest active admin
        # where possible rather than discarding access; otherwise use the oldest active member.
        inactive_primary = next(
            (member for member in members if (member.username or "").casefold() == "akshayp"),
            None,
        )
        if inactive_primary:
            inactive_primary.username = f"inactive-akshayp-{inactive_primary.id}"
            db.session.flush()
        primary = next((member for member in active_members if member.role == "admin"), None)
        if primary is None:
            primary = active_members[0]
        primary.username = "akshayp"

    for member in members:
        member.role = "admin" if member.id == primary.id else "member"
    db.session.commit()


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "local-development-only-change-before-hosting"),
        SQLALCHEMY_DATABASE_URI=os.environ.get("DATABASE_URL", "sqlite:///samruddhi.db"),
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
        APP_PASSWORD=os.environ.get("APP_PASSWORD", ""),
    )
    if test_config:
        app.config.update(test_config)

    uri = app.config["SQLALCHEMY_DATABASE_URI"]
    if uri.startswith("postgres://"):
        app.config["SQLALCHEMY_DATABASE_URI"] = uri.replace("postgres://", "postgresql+psycopg://", 1)
    elif uri.startswith("postgresql://"):
        app.config["SQLALCHEMY_DATABASE_URI"] = uri.replace("postgresql://", "postgresql+psycopg://", 1)

    db.init_app(app)
    login_required = make_login_required()
    from family_features import register_family_routes
    register_family_routes(app, db, {
        "FamilyMember": FamilyMember, "AccountOwner": AccountOwner, "AccountPurposeTag": AccountPurposeTag,
        "Category": Category, "CategoryParent": CategoryParent, "Budget": Budget, "Goal": Goal,
        "GoalEarmark": GoalEarmark, "Transaction": Transaction, "TransactionMetadata": TransactionMetadata,
        "TransactionBeneficiary": TransactionBeneficiary, "Subscription": Subscription,
        "SubscriptionOccurrence": SubscriptionOccurrence, "CashbackScheme": CashbackScheme,
        "CashbackEvent": CashbackEvent, "Account": Account, "CreditCardStatement": CreditCardStatement,
        "StatementPayment": StatementPayment, "CreditCardProfile": CreditCardProfile,
    }, {
        "money_to_paise": money_to_paise, "format_money": format_money,
        "normalized_colour": normalized_colour, "utcnow_naive": utcnow_naive,
        "login_required": login_required,
    })
    with app.app_context():
        db.create_all()
        ensure_additive_schema()
        ensure_primary_admin()
        seed_defaults()
        for category_name, category_kind, category_icon, category_colour in TRANSFER_CATEGORIES:
            existing_category = Category.query.filter(func.lower(Category.name) == category_name.lower()).first()
            if existing_category is None:
                db.session.add(Category(
                    name=category_name, kind=category_kind, icon=category_icon, colour=category_colour))
        db.session.flush()
        transfer_categories = {row.name: row for row in Category.query.filter(
            Category.kind == "transfer", Category.name.in_([item[0] for item in TRANSFER_CATEGORIES])).all()}
        for legacy_transfer in Transaction.query.filter(
            Transaction.kind == "transfer", Transaction.category_id.is_(None)).all():
            source_account = db.session.get(Account, legacy_transfer.account_id)
            destination_account = db.session.get(Account, legacy_transfer.transfer_to_account_id) if legacy_transfer.transfer_to_account_id else None
            if destination_account and destination_account.account_type == "credit":
                category_name = "CC Payment"
            elif (source_account and source_account.account_type == "investment") or (
                destination_account and destination_account.account_type == "investment"):
                category_name = "Investment"
            else:
                category_name = "Self-Transfer"
            inferred_category = transfer_categories.get(category_name)
            if inferred_category:
                legacy_transfer.category_id = inferred_category.id
        # Preserve pre-family transactions without guessing beneficiaries or a personal payer.
        # They remain in the shared family context until an existing transaction is edited.
        known_metadata_ids = {row[0] for row in db.session.query(TransactionMetadata.transaction_id).all()}
        for legacy_txn in Transaction.query.order_by(Transaction.id).all():
            if legacy_txn.id not in known_metadata_ids:
                db.session.add(TransactionMetadata(
                    transaction_id=legacy_txn.id, budget_scope="family", source="legacy",
                    payer_member_id=None, payer_snapshot_set=True))
        db.session.commit()

    @app.context_processor
    def inject_template_helpers():
        def csrf_token() -> str:
            token = session.get("_csrf_token")
            if not token:
                token = secrets.token_urlsafe(32)
                session["_csrf_token"] = token
            return token
        current_member = db.session.get(FamilyMember, int(session["member_id"])) if session.get("member_id") else None
        return {"money": format_money, "today": date.today().isoformat(),
                "auth_enabled": bool(current_member), "current_member": current_member, "csrf_token": csrf_token}

    @app.before_request
    def protect_post_requests():
        if request.method == "POST":
            expected = session.get("_csrf_token", "")
            provided = request.form.get("_csrf_token", "")
            if not expected or not provided or not secrets.compare_digest(expected, provided):
                abort(400, description="The form has expired. Refresh the page and try again.")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if FamilyMember.query.filter_by(is_active=True).count() == 0:
            return redirect(url_for("setup"))
        if request.method == "POST":
            identifier = (request.form.get("identifier") or request.form.get("email", "")).strip().lower()[:254]
            password = request.form.get("password", "")
            member = FamilyMember.query.filter(
                or_(
                    func.lower(FamilyMember.email) == identifier,
                    func.lower(FamilyMember.username) == identifier,
                ),
                FamilyMember.is_active.is_(True),
            ).first()
            if member and check_password_hash(member.password_hash, password):
                session.clear()
                session["member_id"] = member.id
                session["_csrf_token"] = secrets.token_urlsafe(32)
                target = request.args.get("next", "")
                if not target.startswith("/") or target.startswith("//"):
                    target = url_for("dashboard")
                return redirect(target)
            flash("Email or password did not match.", "error")
        return render_template("login.html")

    @app.post("/logout")
    @login_required
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.get("/")
    @login_required
    def dashboard():
        start, end, month = month_bounds(request.args.get("month"))
        cb_source_ids = db.session.query(TransactionMetadata.transaction_id).filter(TransactionMetadata.source == "cashback")
        income = db.session.query(func.coalesce(func.sum(Transaction.amount_paise), 0)).filter(
            Transaction.kind == "income", Transaction.is_archived.is_(False), Transaction.transaction_date >= start,
            Transaction.transaction_date < end, ~Transaction.id.in_(cb_source_ids)).scalar()
        cashback_earned = db.session.query(func.coalesce(func.sum(Transaction.amount_paise), 0)).join(
            TransactionMetadata, TransactionMetadata.transaction_id == Transaction.id).filter(
            TransactionMetadata.source == "cashback", Transaction.kind == "income",
            Transaction.is_archived.is_(False), Transaction.transaction_date >= start, Transaction.transaction_date < end).scalar()
        expenses = db.session.query(func.coalesce(func.sum(Transaction.amount_paise), 0)).filter(
            Transaction.kind == "expense", Transaction.is_archived.is_(False), Transaction.transaction_date >= start,
            Transaction.transaction_date < end).scalar()
        recent = Transaction.query.filter(Transaction.is_archived.is_(False), Transaction.transaction_date >= start,
            Transaction.transaction_date < end).order_by(
            Transaction.transaction_date.desc(), Transaction.id.desc()).limit(7).all()
        recent = current_app.extensions["family_finance"]["decorate_transactions"](recent)
        category_rows = db.session.query(Category.name, Category.colour,
            func.sum(Transaction.amount_paise)).join(Transaction, Transaction.category_id == Category.id).filter(
            Transaction.kind == "expense", Transaction.is_archived.is_(False), Transaction.transaction_date >= start,
            Transaction.transaction_date < end).group_by(Category.id).order_by(
            func.sum(Transaction.amount_paise).desc()).limit(6).all()
        expense_total = int(expenses or 0)
        breakdown = [{"name": name, "colour": colour, "amount": int(value or 0),
            "share": round(int(value or 0) * 100 / expense_total) if expense_total else 0}
            for name, colour, value in category_rows]
        accounts = Account.query.filter_by(is_active=True).order_by(Account.name).all()
        return render_template("dashboard.html", month=month, income=int(income or 0),
            expenses=expense_total, cashback_earned=int(cashback_earned or 0),
            net=int(income or 0) + int(cashback_earned or 0) - expense_total,
            recent=recent, breakdown=breakdown, accounts=accounts)

    @app.get("/transactions")
    @login_required
    def transactions():
        start, end, month = month_bounds(request.args.get("month"))
        show_archived = request.args.get("archived") == "1"
        query = Transaction.query.filter(Transaction.is_archived.is_(show_archived), Transaction.transaction_date >= start, Transaction.transaction_date < end)
        kind = request.args.get("kind", "")
        search = request.args.get("q", "").strip()[:100]
        if kind in KINDS:
            query = query.filter(Transaction.kind == kind)
        if search:
            pattern = f"%{search}%"
            query = query.filter(or_(Transaction.description.ilike(pattern), Transaction.notes.ilike(pattern)))
        items = query.order_by(Transaction.transaction_date.desc(), Transaction.id.desc()).all()
        items = current_app.extensions["family_finance"]["decorate_transactions"](items)
        return render_template("transactions.html", items=items, month=month, kind=kind, q=search, archived=show_archived)

    def save_transaction(item: Transaction | None):
        accounts = Account.query.order_by(Account.name).all()
        categories = Category.query.order_by(Category.kind, Category.name).all()
        if not accounts:
            flash("Create an account before adding a transaction.", "error")
            return redirect(url_for("accounts"))
        if request.method == "POST":
            try:
                kind = request.form.get("kind", "expense")
                if kind not in KINDS:
                    raise ValueError("Choose income, expense or transfer.")
                amount = money_to_paise(request.form.get("amount", ""))
                description = request.form.get("description", "").strip()
                if not description:
                    raise ValueError("Add a description.")
                txn_date = date.fromisoformat(request.form.get("transaction_date", ""))
                account_id = int(request.form.get("account_id", ""))
                if not db.session.get(Account, account_id):
                    raise ValueError("Choose a valid account.")
                category_id = None
                target_id = None
                if kind == "transfer":
                    target_id = int(request.form.get("transfer_to_account_id", ""))
                    if target_id == account_id or not db.session.get(Account, target_id):
                        raise ValueError("Choose a different destination account.")
                raw_category = request.form.get("category_id", "").strip()
                category_id = int(raw_category) if raw_category else None
                if category_id:
                    category = db.session.get(Category, category_id)
                    if not category or category.kind != kind:
                        raise ValueError("Choose a category matching the transaction type.")
                record = item or Transaction()
                record.kind = kind
                record.amount_paise = amount
                record.description = description[:180]
                record.transaction_date = txn_date
                record.account_id = account_id
                record.category_id = category_id
                record.transfer_to_account_id = target_id
                record.notes = request.form.get("notes", "").strip()[:500]
                db.session.add(record)
                db.session.flush()
                current_app.extensions["family_finance"]["save_transaction_extras"](record, item is None)
                db.session.commit()
                flash("Transaction saved.", "success")
                return redirect(url_for("transactions"))
            except (ValueError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc) or "Check the transaction details.", "error")
        form_context = current_app.extensions["family_finance"]["transaction_form_context"](item, accounts, categories)
        return render_template("transaction_form.html", item=item, accounts=accounts, categories=categories, **form_context)

    @app.route("/transactions/new", methods=["GET", "POST"])
    @login_required
    def transaction_new():
        return save_transaction(None)

    @app.route("/transactions/<int:transaction_id>/edit", methods=["GET", "POST"])
    @login_required
    def transaction_edit(transaction_id: int):
        edit_problem = current_app.extensions["family_finance"]["transaction_edit_guard"](transaction_id)
        if edit_problem:
            flash(edit_problem, "error")
            return redirect(url_for("transactions"))
        return save_transaction(db.get_or_404(Transaction, transaction_id))

    @app.post("/transactions/<int:transaction_id>/archive")
    @login_required
    def transaction_archive(transaction_id: int):
        item = db.get_or_404(Transaction, transaction_id)
        guard_message = current_app.extensions["family_finance"]["transaction_archive_guard"](transaction_id)
        if guard_message:
            flash(guard_message, "error")
            return redirect(url_for("transactions", month=item.transaction_date.strftime("%Y-%m")))
        item.is_archived = True
        current_app.extensions["family_finance"]["on_transaction_archive"](item, True)
        db.session.commit()
        flash("Transaction archived. You can restore it from Archived transactions.", "success")
        return redirect(url_for("transactions", month=item.transaction_date.strftime("%Y-%m")))

    @app.post("/transactions/<int:transaction_id>/restore")
    @login_required
    def transaction_restore(transaction_id: int):
        item = db.get_or_404(Transaction, transaction_id)
        guard_message = current_app.extensions["family_finance"]["transaction_archive_guard"](transaction_id)
        if guard_message:
            flash(guard_message, "error")
            return redirect(url_for("transactions", month=item.transaction_date.strftime("%Y-%m"), archived="1"))
        item.is_archived = False
        current_app.extensions["family_finance"]["on_transaction_archive"](item, False)
        db.session.commit()
        flash("Transaction restored.", "success")
        return redirect(url_for("transactions", month=item.transaction_date.strftime("%Y-%m")))

    def balances_for_accounts(accounts: list[Account]) -> list[dict[str, Any]]:
        output = []
        for account in accounts:
            txns = Transaction.query.filter(Transaction.is_archived.is_(False), or_(Transaction.account_id == account.id,
                Transaction.transfer_to_account_id == account.id)).all()
            balance = account.opening_balance_paise
            for txn in txns:
                if txn.kind == "transfer":
                    if account.account_type == "credit":
                        balance += -txn.amount_paise if txn.transfer_to_account_id == account.id else txn.amount_paise
                    else:
                        balance += txn.amount_paise if txn.transfer_to_account_id == account.id else -txn.amount_paise
                elif txn.account_id == account.id:
                    if account.account_type == "credit":
                        balance += txn.amount_paise if txn.kind == "expense" else -txn.amount_paise
                    else:
                        balance += txn.amount_paise if txn.kind == "income" else -txn.amount_paise
            output.append({"account": account, "balance": balance, "count": len(txns)})
        return output

    def credit_card_overview():
        card_accounts = Account.query.filter_by(account_type="credit", is_active=True).order_by(Account.name).all()
        balance_rows = balances_for_accounts(card_accounts)
        balance_by_id = {row["account"].id: row["balance"] for row in balance_rows}
        profiles = {profile.account_id: profile for profile in CreditCardProfile.query.all()}
        groups = {group.id: group for group in CreditLimitGroup.query.order_by(CreditLimitGroup.name).all()}
        group_used = {}
        for card in card_accounts:
            profile = profiles.get(card.id)
            if profile and profile.limit_mode == "shared" and profile.shared_group_id in groups:
                group_used[profile.shared_group_id] = group_used.get(profile.shared_group_id, 0) + max(int(balance_by_id.get(card.id, 0)), 0)

        card_rows = []
        total_owed = total_limit = total_available = 0
        counted_groups = set()
        for card in card_accounts:
            profile = profiles.get(card.id)
            owed = max(int(balance_by_id.get(card.id, 0)), 0)
            total_owed += owed
            group = groups.get(profile.shared_group_id) if profile and profile.limit_mode == "shared" else None
            if profile and profile.limit_mode == "individual":
                limit, used = int(profile.individual_limit_paise or 0), owed
                available = max(limit - used, 0)
                total_limit += limit
                total_available += available
                mode_label = "Individual limit"
            elif profile and group:
                limit, used = int(group.limit_paise), group_used.get(group.id, 0)
                available = max(limit - used, 0)
                mode_label = "Shared limit · " + group.name
                if group.id not in counted_groups:
                    total_limit += limit
                    total_available += available
                    counted_groups.add(group.id)
            else:
                limit = used = available = 0
                mode_label = "Limit not configured"
            utilization = round(used * 100 / limit) if limit else 0
            card_rows.append({
                "account": card, "profile": profile, "balance": int(balance_by_id.get(card.id, 0)),
                "owed": owed, "group": group, "limit_paise": limit, "used_paise": used,
                "available_paise": available, "utilization": utilization,
                "progress_pct": min(max(utilization, 0), 100), "mode_label": mode_label,
            })

        metrics = {
            "total_owed_paise": total_owed, "total_limit_paise": total_limit,
            "available_paise": total_available, "card_count": len(card_accounts),
            "configured_count": sum(1 for row in card_rows if row["limit_paise"] > 0),
        }
        return card_rows, metrics

    @app.route("/credit-cards", methods=["GET", "POST"])
    @login_required
    def credit_cards():
        if request.method == "POST":
            action = request.form.get("action", "")
            try:
                if action == "create_group":
                    name = request.form.get("group_name", "").strip()[:80]
                    limit = money_to_paise(request.form.get("group_limit", ""))
                    if not name:
                        raise ValueError("Enter a name for the shared limit group.")
                    if CreditLimitGroup.query.filter(func.lower(CreditLimitGroup.name) == name.lower()).first():
                        raise ValueError("A shared limit group with that name already exists.")
                    db.session.add(CreditLimitGroup(name=name, limit_paise=limit))
                    db.session.commit()
                    flash("Shared credit limit group created.", "success")
                elif action == "update_group":
                    group = db.session.get(CreditLimitGroup, int(request.form.get("group_id", "")))
                    if not group:
                        raise ValueError("Choose a valid shared limit group.")
                    name = request.form.get("group_name", "").strip()[:80]
                    limit = money_to_paise(request.form.get("group_limit", ""))
                    if not name:
                        raise ValueError("Enter a name for the shared limit group.")
                    duplicate = CreditLimitGroup.query.filter(
                        func.lower(CreditLimitGroup.name) == name.lower(),
                        CreditLimitGroup.id != group.id,
                    ).first()
                    if duplicate:
                        raise ValueError("A shared limit group with that name already exists.")
                    group.name, group.limit_paise = name, limit
                    db.session.commit()
                    flash("Shared credit limit group updated.", "success")
                elif action == "save_profile":
                    card = db.session.get(Account, int(request.form.get("account_id", "")))
                    if not card or card.account_type != "credit":
                        raise ValueError("Choose a valid credit card account.")
                    mode = request.form.get("limit_mode", "individual")
                    if mode not in {"individual", "shared"}:
                        raise ValueError("Choose an individual or shared credit limit.")
                    profile = CreditCardProfile.query.filter_by(account_id=card.id).first() or CreditCardProfile(account_id=card.id)
                    profile.limit_mode = mode
                    if mode == "individual":
                        profile.individual_limit_paise = money_to_paise(request.form.get("individual_limit", ""))
                        profile.shared_group_id = None
                    else:
                        group = db.session.get(CreditLimitGroup, int(request.form.get("shared_group_id", "")))
                        if not group:
                            raise ValueError("Choose a valid shared credit limit group.")
                        profile.individual_limit_paise, profile.shared_group_id = None, group.id
                    db.session.add(profile)
                    db.session.commit()
                    flash(f"Credit limit saved for {card.name}. Card usage always reflects the transaction ledger.", "success")
                else:
                    raise ValueError("Credit-card payments are recorded under Transactions as categorized transfers.")
                return redirect(url_for("credit_cards"))
            except (ValueError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc) or "Check the credit-card settings.", "error")

        card_rows, metrics = credit_card_overview()
        groups = CreditLimitGroup.query.order_by(CreditLimitGroup.name).all()
        return render_template("credit_cards.html", cards=card_rows, metrics=metrics, groups=groups)

    @app.route("/accounts", methods=["GET", "POST"])
    @login_required
    def accounts():
        if request.method == "POST":
            if request.form.get("action") == "update_account":
                actor = db.session.get(FamilyMember, int(session["member_id"])) if session.get("member_id") else None
                if not current_app.config.get("TESTING") and (not actor or actor.role != "admin" or actor.username != "akshayp"):
                    abort(403)
                try:
                    account_id = int(request.form.get("account_id", ""))
                    account = db.session.get(Account, account_id) or abort(404)
                    name = request.form.get("name", "").strip()[:80]
                    kind = request.form.get("account_type", "")
                    opening = money_to_paise(request.form.get("opening_balance", "0"), allow_zero=True)
                    colour = normalized_colour(request.form.get("colour"))
                    duplicate = Account.query.filter(func.lower(Account.name) == name.lower(), Account.id != account.id).first()
                    if not name or duplicate or kind not in ACCOUNT_TYPES:
                        raise ValueError("Enter a unique account name and valid account type.")
                    account.name, account.account_type = name, kind
                    account.opening_balance_paise, account.colour = opening, colour
                    db.session.commit()
                    flash("Account updated.", "success")
                    return redirect(url_for("accounts"))
                except (ValueError, TypeError) as exc:
                    db.session.rollback()
                    flash(str(exc) or "Check the account details.", "error")
                    return redirect(url_for("accounts"))
            if request.form.get("action") == "save_account_meta":
                actor = db.session.get(FamilyMember, int(session["member_id"])) if session.get("member_id") else None
                if not current_app.config.get("TESTING") and (
                    not actor or actor.role != "admin" or actor.username != "akshayp"
                ):
                    abort(403)
                try:
                    current_app.extensions["family_finance"]["save_account_owner"](
                        int(request.form.get("account_id", "")), request.form.get("owner_member_id", ""),
                        request.form.getlist("purpose_tags"))
                    db.session.commit()
                    flash("Account settings saved.", "success")
                    return redirect(url_for("accounts"))
                except (ValueError, TypeError) as exc:
                    db.session.rollback()
                    flash(str(exc) or "Check account settings.", "error")
                    return redirect(url_for("accounts"))
            try:
                name = request.form.get("name", "").strip()
                kind = request.form.get("account_type", "bank")
                opening_raw = request.form.get("opening_balance", "0").strip() or "0"
                opening = money_to_paise(opening_raw, allow_zero=True)
                if not name:
                    raise ValueError("Enter an account name.")
                if kind not in ACCOUNT_TYPES:
                    raise ValueError("Choose a valid account type.")
                if Account.query.filter(func.lower(Account.name) == name.lower()).first():
                    raise ValueError("An account with that name already exists.")
                actor = db.session.get(FamilyMember, int(session["member_id"])) if session.get("member_id") else None
                requested_owner = request.form.get("owner_member_id", "").strip()
                if not current_app.config.get("TESTING") and actor and (
                    actor.role != "admin" or actor.username != "akshayp"
                ):
                    if requested_owner != str(actor.id):
                        raise ValueError("Family members can add accounts for themselves. Ask an administrator to create a shared account or assign another owner.")
                new_account = Account(name=name[:80], account_type=kind,
                    opening_balance_paise=opening, colour=normalized_colour(request.form.get("colour")))
                db.session.add(new_account)
                db.session.flush()
                current_app.extensions["family_finance"]["save_account_owner"](
                    new_account.id, requested_owner,
                    request.form.getlist("purpose_tags"))
                db.session.commit()
                flash("Account added.", "success")
                return redirect(url_for("accounts"))
            except (ValueError, TypeError) as exc:
                db.session.rollback()
                flash(str(exc) or "Check the account details.", "error")
        show_archived = request.args.get("archived") == "1"
        account_list = Account.query.filter_by(is_active=not show_archived).order_by(Account.name).all()
        rows = current_app.extensions["family_finance"]["enrich_account_rows"](balances_for_accounts(account_list))
        return render_template("accounts.html", rows=rows,
            people=current_app.extensions["family_finance"]["active_members"](), show_archived=show_archived)

    @app.post("/accounts/<int:account_id>/toggle")
    @login_required
    def toggle_account(account_id):
        actor = db.session.get(FamilyMember, int(session["member_id"])) if session.get("member_id") else None
        if not current_app.config.get("TESTING") and (not actor or actor.role != "admin" or actor.username != "akshayp"):
            abort(403)
        account = db.session.get(Account, account_id) or abort(404)
        account.is_active = not account.is_active
        db.session.commit()
        flash("Account archived." if not account.is_active else "Account restored.", "success")
        return redirect(url_for("accounts"))

    @app.route("/categories", methods=["GET", "POST"])
    @login_required
    def categories():
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            kind = request.form.get("kind", "expense")
            if not name:
                flash("Enter a category name.", "error")
            elif kind not in ("income", "expense", "transfer"):
                flash("Choose income, expense or transfer.", "error")
            elif Category.query.filter(func.lower(Category.name) == name.lower()).first():
                flash("That category already exists.", "error")
            else:
                parent_raw = request.form.get("parent_category_id", "").strip()
                parent_id = int(parent_raw) if parent_raw else None
                if parent_id:
                    parent = db.session.get(Category, parent_id)
                    if not parent or parent.kind != kind:
                        flash("Choose a parent category of the same type.", "error")
                        return redirect(url_for("categories"))
                    if CategoryParent.query.filter_by(subcategory_id=parent_id).first():
                        flash("Subcategories can have only one level. Choose an L1 parent category.", "error")
                        return redirect(url_for("categories"))
                new_category = Category(name=name[:80], kind=kind, icon="circle",
                    colour=normalized_colour(request.form.get("colour")))
                db.session.add(new_category)
                db.session.flush()
                if parent_id:
                    db.session.add(CategoryParent(subcategory_id=new_category.id, parent_category_id=parent_id))
                db.session.commit()
                flash("Subcategory added." if parent_id else "Category added.", "success")
                return redirect(url_for("categories"))
        rows = current_app.extensions["family_finance"]["enrich_categories"](
            Category.query.order_by(Category.kind, Category.name).all())
        subcategory_ids = {row.subcategory_id for row in CategoryParent.query.all()}
        parents = [row for row in Category.query.order_by(Category.kind, Category.name).all()
                   if row.id not in subcategory_ids]
        return render_template("categories.html", categories=rows, parents=parents)


    def require_primary_admin():
        member = db.session.get(FamilyMember, int(session["member_id"])) if session.get("member_id") else None
        if app.config.get("TESTING") and not app.config.get("TEST_AUTH_REQUIRED"):
            return member
        if not member:
            abort(403)
        if member.username != "akshayp" or member.role != "admin":
            abort(403)
        return member

    def reset_cutoff(raw):
        if not raw:
            raise ValueError("Choose a date and time.")
        try:
            local_value = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ValueError("Enter a valid date and time.") from exc
        if local_value.tzinfo is None:
            local_value = local_value.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
        return local_value.astimezone(timezone.utc).replace(tzinfo=None)

    def backup_payload():
        backup_models = [
            Account, Category, Transaction, ImportBatch, CreditLimitGroup, CreditCardProfile,
            CreditCardStatement, StatementPayment, FamilyMember, AccountOwner, AccountPurposeTag,
            CategoryParent, Budget, Goal, GoalEarmark, TransactionMetadata, TransactionBeneficiary,
            CashbackScheme, Subscription, SubscriptionOccurrence, CashbackEvent,
        ]
        payload = {"app": "Samruddhi Finance", "format_version": 1,
                   "created_at_utc": utcnow_naive().isoformat() + "Z", "tables": {}}
        for model in backup_models:
            rows = []
            for row in model.query.order_by(model.__table__.primary_key.columns.values()[0]).all():
                item = {}
                for column in model.__table__.columns:
                    if column.name == "password_hash":
                        continue
                    value = getattr(row, column.name)
                    item[column.name] = value.isoformat() if isinstance(value, (date, datetime)) else value
                rows.append(item)
            payload["tables"][model.__tablename__] = rows
        return payload

    def reset_preview_counts(mode, cutoff=None, preserve_setup=False):
        counts = {}
        if mode == "full":
            models_to_clear = [StatementPayment, SubscriptionOccurrence, TransactionBeneficiary,
                TransactionMetadata, GoalEarmark, CashbackEvent, Transaction, CreditCardStatement, ImportBatch]
            if not preserve_setup:
                models_to_clear += [Subscription, Goal, Budget, CashbackScheme, CreditCardProfile,
                    CreditLimitGroup, AccountOwner, AccountPurposeTag, Account]
            for model in models_to_clear:
                counts[model.__tablename__] = model.query.count()
        else:
            txn_ids = [value for (value,) in db.session.query(Transaction.id).filter(Transaction.created_at >= cutoff).all()]
            counts["transactions"] = len(txn_ids)
            counts["transaction details"] = TransactionMetadata.query.filter(TransactionMetadata.transaction_id.in_(txn_ids)).count() if txn_ids else 0
            counts["beneficiary splits"] = TransactionBeneficiary.query.filter(TransactionBeneficiary.transaction_id.in_(txn_ids)).count() if txn_ids else 0
            counts["subscription occurrences"] = SubscriptionOccurrence.query.filter(
                or_(SubscriptionOccurrence.transaction_id.in_(txn_ids), SubscriptionOccurrence.subscription_id.in_(
                    db.session.query(Subscription.id).filter(Subscription.created_from_transaction_id.in_(txn_ids))))
            ).count() if txn_ids else 0
            counts["cashback events"] = CashbackEvent.query.filter(or_(
                CashbackEvent.created_at >= cutoff,
                CashbackEvent.transaction_id.in_(txn_ids),
                CashbackEvent.posted_transaction_id.in_(txn_ids))).count() if txn_ids else CashbackEvent.query.filter(CashbackEvent.created_at >= cutoff).count()
            counts["credit-card statements"] = CreditCardStatement.query.filter(CreditCardStatement.created_at >= cutoff).count()
            statement_ids = db.session.query(CreditCardStatement.id).filter(CreditCardStatement.created_at >= cutoff)
            counts["statement payments"] = StatementPayment.query.filter(or_(
                StatementPayment.created_at >= cutoff,
                StatementPayment.statement_id.in_(statement_ids),
                StatementPayment.transaction_id.in_(txn_ids) if txn_ids else text("1=0"))).count()
            counts["goal earmarks"] = GoalEarmark.query.filter(GoalEarmark.updated_at >= cutoff).count()
            counts["CSV import batches"] = ImportBatch.query.filter(ImportBatch.created_at >= cutoff).count()
        return {key: value for key, value in counts.items() if value}

    @app.get("/settings")
    @login_required
    def settings():
        require_primary_admin()
        mode = request.args.get("mode", "full")
        if mode not in {"full", "since"}:
            mode = "full"
        cutoff_raw = request.args.get("cutoff", "")
        cutoff = None
        error = None
        counts = {}
        if mode == "since" and cutoff_raw:
            try:
                cutoff = reset_cutoff(cutoff_raw)
                counts = reset_preview_counts(mode, cutoff=cutoff)
            except ValueError as exc:
                error = str(exc)
        elif mode == "full":
            counts = reset_preview_counts("full", preserve_setup=request.args.get("preserve_setup") == "1")
        return render_template("settings.html", mode=mode, cutoff_raw=cutoff_raw,
            cutoff=cutoff, counts=counts, error=error,
            preserve_setup=request.args.get("preserve_setup") == "1")

    @app.get("/settings/data-reset/backup")
    @login_required
    def download_reset_backup():
        require_primary_admin()
        payload = backup_payload()
        content = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        filename = "samruddhi-backup-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".json"
        return Response(content, mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.post("/settings/data-reset")
    @login_required
    def perform_data_reset():
        require_primary_admin()
        mode = request.form.get("mode", "full")
        preserve_setup = request.form.get("preserve_setup") == "1"
        cutoff_raw = request.form.get("cutoff", "").strip()
        if mode not in {"full", "since"}:
            abort(400)
        if request.form.get("backup_confirmed") != "1":
            flash("Download a backup and confirm before resetting data.", "error")
            return redirect(url_for("settings", mode=mode, cutoff=cutoff_raw, preserve_setup="1" if preserve_setup else "0"))
        if request.form.get("confirmation", "").strip() != ("RESET ALL" if mode == "full" else "RESET SINCE"):
            flash("The confirmation phrase did not match. No data was changed.", "error")
            return redirect(url_for("settings", mode=mode, cutoff=cutoff_raw, preserve_setup="1" if preserve_setup else "0"))
        try:
            cutoff = reset_cutoff(cutoff_raw) if mode == "since" else None
            if mode == "since" and not reset_preview_counts("since", cutoff=cutoff):
                flash("No records match that cutoff. Nothing was changed.", "info")
                return redirect(url_for("settings", mode="since", cutoff=cutoff_raw))
            if mode == "full":
                # Delete dependent activity before parent records to preserve FK integrity.
                StatementPayment.query.delete(synchronize_session=False)
                SubscriptionOccurrence.query.delete(synchronize_session=False)
                TransactionBeneficiary.query.delete(synchronize_session=False)
                TransactionMetadata.query.delete(synchronize_session=False)
                GoalEarmark.query.delete(synchronize_session=False)
                CashbackEvent.query.delete(synchronize_session=False)
                if preserve_setup:
                    Subscription.query.filter(Subscription.created_from_transaction_id.is_not(None)).update(
                        {Subscription.created_from_transaction_id: None}, synchronize_session=False)
                Transaction.query.delete(synchronize_session=False)
                CreditCardStatement.query.delete(synchronize_session=False)
                ImportBatch.query.delete(synchronize_session=False)
                if not preserve_setup:
                    Subscription.query.delete(synchronize_session=False)
                    Goal.query.delete(synchronize_session=False)
                    Budget.query.delete(synchronize_session=False)
                    CashbackScheme.query.delete(synchronize_session=False)
                    CreditCardProfile.query.delete(synchronize_session=False)
                    CreditLimitGroup.query.delete(synchronize_session=False)
                    AccountOwner.query.delete(synchronize_session=False)
                    AccountPurposeTag.query.delete(synchronize_session=False)
                    Account.query.delete(synchronize_session=False)
                db.session.commit()
                flash("Full financial reset completed. Member logins and categories were preserved.", "success")
            else:
                txn_ids = [value for (value,) in db.session.query(Transaction.id).filter(Transaction.created_at >= cutoff).all()]
                statement_ids = [value for (value,) in db.session.query(CreditCardStatement.id).filter(CreditCardStatement.created_at >= cutoff).all()]
                related_event_query = CashbackEvent.query.filter(or_(
                    CashbackEvent.created_at >= cutoff,
                    CashbackEvent.transaction_id.in_(txn_ids) if txn_ids else text("1=0"),
                    CashbackEvent.posted_transaction_id.in_(txn_ids) if txn_ids else text("1=0")))
                related_event_ids = [value for (value,) in related_event_query.with_entities(CashbackEvent.id).all()]
                if txn_ids:
                    Subscription.query.filter(Subscription.created_from_transaction_id.in_(txn_ids)).update(
                        {Subscription.created_from_transaction_id: None}, synchronize_session=False)
                    SubscriptionOccurrence.query.filter(SubscriptionOccurrence.transaction_id.in_(txn_ids)).delete(synchronize_session=False)
                    TransactionMetadata.query.filter(TransactionMetadata.transaction_id.in_(txn_ids)).delete(synchronize_session=False)
                    TransactionBeneficiary.query.filter(TransactionBeneficiary.transaction_id.in_(txn_ids)).delete(synchronize_session=False)
                    StatementPayment.query.filter(StatementPayment.transaction_id.in_(txn_ids)).delete(synchronize_session=False)
                if related_event_ids:
                    CashbackEvent.query.filter(CashbackEvent.id.in_(related_event_ids)).delete(synchronize_session=False)
                if statement_ids:
                    StatementPayment.query.filter(StatementPayment.statement_id.in_(statement_ids)).delete(synchronize_session=False)
                    CreditCardStatement.query.filter(CreditCardStatement.id.in_(statement_ids)).delete(synchronize_session=False)
                GoalEarmark.query.filter(GoalEarmark.updated_at >= cutoff).delete(synchronize_session=False)
                ImportBatch.query.filter(ImportBatch.created_at >= cutoff).delete(synchronize_session=False)
                if txn_ids:
                    # If an older cashback event referenced a deleted posted transaction, it was removed above.
                    Transaction.query.filter(Transaction.id.in_(txn_ids)).delete(synchronize_session=False)
                db.session.commit()
                flash("Financial activity created since the selected IST time was reset. Accounts and setup definitions were preserved.", "success")
        except Exception:
            db.session.rollback()
            current_app.logger.exception("Data reset failed")
            flash("Reset failed and the database changes were rolled back. No reset was completed.", "error")
        return redirect(url_for("settings"))

    @app.get("/export.csv")
    @login_required
    def export_csv():
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["date", "type", "amount", "description", "account", "category", "transfer_to", "notes"])
        for txn in Transaction.query.filter(Transaction.is_archived.is_(False)).order_by(Transaction.transaction_date, Transaction.id).all():
            writer.writerow([txn.transaction_date.isoformat(), txn.kind,
                f"{txn.amount_paise // 100}.{txn.amount_paise % 100:02d}", txn.description,
                txn.account.name, txn.category.name if txn.category else "",
                txn.transfer_to.name if txn.transfer_to else "", txn.notes])
        return Response("\ufeff" + output.getvalue(), mimetype="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="samruddhi-transactions.csv"'})

    def duplicate_exists(row: dict[str, Any]) -> bool:
        return db.session.query(Transaction.id).join(Account, Transaction.account_id == Account.id).filter(
            Transaction.transaction_date == date.fromisoformat(row["date"]),
            Transaction.kind == row["type"],
            Transaction.amount_paise == row["amount_paise"],
            func.lower(Transaction.description) == row["description"].lower(),
            func.lower(Account.name) == row["account"].lower()).first() is not None

    @app.route("/import", methods=["GET", "POST"])
    @login_required
    def import_csv():
        if request.method == "POST" and request.form.get("action") == "confirm":
            batch_id = session.pop("import_batch_id", None)
            batch = db.session.get(ImportBatch, batch_id) if batch_id else None
            if not batch or batch.consumed or utcnow_naive() - batch.created_at > timedelta(hours=1):
                flash("That preview expired. Please upload the CSV again.", "error")
                return redirect(url_for("import_csv"))
            rows = json.loads(batch.rows_json)
            saved = 0
            for row in rows:
                if row.get("error") or duplicate_exists(row):
                    continue
                txn = Transaction(transaction_date=date.fromisoformat(row["date"]),
                    kind=row["type"], amount_paise=int(row["amount_paise"]), description=row["description"],
                    account_id=int(row["account_id"]), category_id=row.get("category_id"),
                    transfer_to_account_id=row.get("transfer_to_account_id"), notes=row.get("notes", ""))
                db.session.add(txn)
                db.session.flush()
                account_owner = AccountOwner.query.filter_by(account_id=txn.account_id).first()
                db.session.add(TransactionMetadata(
                    transaction_id=txn.id, budget_scope="family", source="import",
                    payer_member_id=account_owner.member_id if account_owner else None,
                    payer_snapshot_set=True))
                saved += 1
            batch.consumed = True
            db.session.commit()
            flash(f"Imported {saved} transaction(s). Invalid rows and duplicates were skipped.", "success")
            return redirect(url_for("transactions"))

        if request.method == "POST":
            upload = request.files.get("file")
            if not upload or not upload.filename or not upload.filename.lower().endswith(".csv"):
                flash("Choose a .csv file.", "error")
                return redirect(url_for("import_csv"))
            try:
                content = upload.stream.read().decode("utf-8-sig")
                if len(content.splitlines()) > 501:
                    raise ValueError("Import is limited to 500 rows at a time.")
                reader = csv.DictReader(io.StringIO(content))
                required = {"date", "type", "amount", "description", "account"}
                headers = {header.strip().lower() for header in (reader.fieldnames or [])}
                if not required.issubset(headers):
                    raise ValueError("CSV headers must include date,type,amount,description,account. Category is optional.")
                accounts_by_name = {a.name.lower(): a for a in Account.query.all()}
                categories_by_name = {c.name.lower(): c for c in Category.query.all()}
                rows = []
                for source in reader:
                    raw = {(key or "").strip().lower(): (value or "").strip() for key, value in source.items()}
                    row: dict[str, Any] = {
                        "date": raw.get("date", ""), "type": raw.get("type", "").lower(),
                        "amount": raw.get("amount", ""), "description": raw.get("description", "")[:180],
                        "account": raw.get("account", ""), "category": raw.get("category", ""),
                        "transfer_to": raw.get("transfer_to", ""), "notes": raw.get("notes", "")[:500],
                        "error": "",
                    }
                    try:
                        parsed_date = date.fromisoformat(row["date"])
                        if row["type"] not in KINDS:
                            raise ValueError("Type must be income, expense or transfer.")
                        row["amount_paise"] = money_to_paise(row["amount"])
                        if not row["description"]:
                            raise ValueError("Description is required.")
                        account = accounts_by_name.get(row["account"].lower())
                        if not account:
                            raise ValueError("Account does not match an existing account.")
                        row["account_id"] = account.id
                        row["date"] = parsed_date.isoformat()
                        row["category_id"] = None
                        row["transfer_to_account_id"] = None
                        if row["type"] == "transfer":
                            target = accounts_by_name.get(row["transfer_to"].lower())
                            if not target or target.id == account.id:
                                raise ValueError("Transfer needs a different existing transfer_to account.")
                            row["transfer_to_account_id"] = target.id
                        if row["category"]:
                            category = categories_by_name.get(row["category"].lower())
                            if not category or category.kind != row["type"]:
                                raise ValueError("Category does not match an existing category of that type.")
                            row["category_id"] = category.id
                        row["duplicate"] = duplicate_exists(row)
                    except (ValueError, TypeError) as exc:
                        row["error"] = str(exc) or "Invalid row."
                        row["duplicate"] = False
                    rows.append(row)
                batch_id = secrets.token_urlsafe(18)
                db.session.add(ImportBatch(id=batch_id, rows_json=json.dumps(rows), created_at=utcnow_naive()))
                db.session.commit()
                session["import_batch_id"] = batch_id
                return render_template("import.html", rows=rows, preview=True)
            except (UnicodeDecodeError, csv.Error, ValueError) as exc:
                flash(str(exc) or "Could not read this CSV.", "error")
                return redirect(url_for("import_csv"))
        return render_template("import.html", rows=[], preview=False)

    @app.errorhandler(413)
    def file_too_large(_error):
        flash("That file is too large. CSV imports are limited to 2 MB.", "error")
        return redirect(url_for("import_csv"))

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("HOST", "127.0.0.1"),
            port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG") == "1")
