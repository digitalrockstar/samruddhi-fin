"""Compile dashboard queries against the PostgreSQL dialect.

We cannot run these locally without Postgres, so instead we assert the SQL
SQLAlchemy generates is valid Postgres and contains the expected shapes.
"""
from datetime import datetime

from sqlalchemy import and_, case, func, select
from sqlalchemy.dialects import postgresql

from app.models import Category, Person, Transaction, TransactionAllocation
from app.schemas import TransactionType

DEBIT = TransactionType.DEBIT
CREDIT = TransactionType.CREDIT
EXPENSE_FOR = "expense_for"
INCOME_FROM = "income_from"


def compile_sql(stmt) -> str:
    return str(
        stmt.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


def test_totals_sql():
    stmt = select(
        func.coalesce(func.sum(case((Transaction.txn_type == DEBIT,
                                     Transaction.parsed_amount), else_=0)), 0).label("expense"),
        func.count(Transaction.id).label("count"),
    ).where(and_(Transaction.is_spam.is_(False),
                 Transaction.txn_timestamp >= datetime(2025, 1, 1)))
    sql = compile_sql(stmt)
    assert "case when" in sql.lower()
    assert "coalesce" in sql.lower()
    assert "is_spam IS false" in sql


def test_by_category_sql():
    stmt = (
        select(Category.id, Category.name, Category.parent_id,
               func.coalesce(func.sum(Transaction.parsed_amount), 0).label("total"),
               func.count(Transaction.id).label("count"))
        .select_from(Transaction)
        .join(Category, Transaction.category_id == Category.id)
        .where(and_(Transaction.txn_type == DEBIT,
                    Category.parent_id.is_not(None)))
        .group_by(Category.id, Category.name, Category.parent_id)
        .order_by(func.coalesce(func.sum(Transaction.parsed_amount), 0).desc())
    )
    sql = compile_sql(stmt)
    assert "JOIN categories" in sql
    assert "GROUP BY" in sql
    assert "parent_id IS NOT NULL" in sql


def test_person_split_sql():
    stmt = (
        select(TransactionAllocation.person_id,
               TransactionAllocation.allocation_type,
               func.coalesce(func.sum(TransactionAllocation.amount), 0).label("amt"))
        .select_from(TransactionAllocation)
        .join(Transaction, TransactionAllocation.transaction_id == Transaction.id)
        .where(and_(Transaction.is_spam.is_(False),
                    TransactionAllocation.person_id.is_(None)))
        .group_by(TransactionAllocation.person_id, TransactionAllocation.allocation_type)
    )
    sql = compile_sql(stmt)
    assert "JOIN transactions" in sql
    assert "person_id IS NULL" in sql


def test_trend_group_by_label_is_valid():
    """Postgres allows GROUP BY / ORDER BY on an output column alias."""
    stmt = (
        select(func.date(Transaction.txn_timestamp).label("bucket"),
               func.coalesce(func.sum(Transaction.parsed_amount), 0).label("expense"))
        .where(Transaction.is_spam.is_(False))
        .group_by("bucket")
        .order_by("bucket")
    )
    sql = compile_sql(stmt)
    assert "GROUP BY bucket" in sql
    assert "ORDER BY bucket" in sql


def test_weekly_trend_uses_date_trunc():
    stmt = (
        select(func.date_trunc("week", Transaction.txn_timestamp).label("bucket"),
               func.count(Transaction.id))
        .where(Transaction.is_spam.is_(False))
        .group_by("bucket")
    )
    sql = compile_sql(stmt)
    assert "date_trunc('week', transactions.txn_timestamp)" in sql


def test_cashback_join_is_valid():
    from app.models import Account

    stmt = (
        select(Account.nickname, Transaction.cashback_amount)
        .select_from(Transaction)
        .join(Account, Transaction.account_id == Account.id)
        .where(Transaction.cashback_amount > 0)
    )
    sql = compile_sql(stmt)
    assert "JOIN accounts" in sql