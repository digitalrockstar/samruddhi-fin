from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Dict, List, Optional

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Account,
    Category,
    Person,
    RecurringTransaction,
    Transaction,
    TransactionAllocation,
)
from app.schemas import AllocationType, CategoryType, TransactionType

DEBIT = TransactionType.DEBIT
CREDIT = TransactionType.CREDIT
PAID_BY = AllocationType.PAID_BY
EXPENSE_FOR = AllocationType.EXPENSE_FOR
INCOME_FROM = AllocationType.INCOME_FROM


def _period_start(period: str) -> datetime:
    now = datetime.now()
    if period == "day":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        start = now - timedelta(days=now.weekday())
        return start.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "year":
        return now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    if period == "all":
        return datetime(2000, 1, 1)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


# strftime formats used when the dialect is not PostgreSQL
_SQLITE_TRUNC = {
    "day": "%Y-%m-%d",
    "week": "%Y-%W",       # Monday-based week index
    "month": "%Y-%m-01",
}


def _truncate(col, grain: str, dialect_name: str):
    """Portable date truncation.

    Postgres gets date_trunc(); anything else (e.g. SQLite, used for local
    dev and tests) falls back to strftime. Both return an orderable value.
    """
    if dialect_name == "postgresql":
        return func.date_trunc(grain, col)
    return func.strftime(_SQLITE_TRUNC[grain], col)


class DashboardService:
    def __init__(self, db: AsyncSession):
        self.db = db

    # -------------------------------------------------------------- totals

    async def _totals(self, start: datetime, end: datetime, person_id: int) -> Dict[str, Any]:
        conds = [
            Transaction.txn_timestamp >= start,
            Transaction.txn_timestamp <= end,
            Transaction.is_spam.is_(False),
        ]
        if person_id:
            conds.append(Transaction.person_id == person_id)

        stmt = select(
            func.coalesce(func.sum(case((Transaction.txn_type == DEBIT,
                                         Transaction.parsed_amount), else_=0)), 0).label("expense"),
            func.coalesce(func.sum(case((Transaction.txn_type == CREDIT,
                                         Transaction.parsed_amount), else_=0)), 0).label("income"),
            func.count(Transaction.id).label("count"),
        ).where(and_(*conds))

        row = (await self.db.execute(stmt)).one()
        expense = row.expense or Decimal("0")
        income = row.income or Decimal("0")
        return {
            "total_expense": expense,
            "total_income": income,
            "net": income - expense,
            "transaction_count": row.count or 0,
        }

    # ----------------------------------------------------------- by category

    async def _by_category(self, start: datetime, end: datetime, person_id: int) -> List[Dict]:
        conds = [
            Transaction.txn_timestamp >= start,
            Transaction.txn_timestamp <= end,
            Transaction.is_spam.is_(False),
            Transaction.txn_type == DEBIT,
            Category.type == CategoryType.EXPENSE,
            Category.parent_id.is_not(None),
        ]
        if person_id:
            conds.append(Transaction.person_id == person_id)

        stmt = (
            select(
                Category.id,
                Category.name,
                Category.parent_id,
                func.coalesce(func.sum(Transaction.parsed_amount), 0).label("total"),
                func.count(Transaction.id).label("count"),
            )
            .select_from(Transaction)
            .join(Category, Transaction.category_id == Category.id)
            .where(and_(*conds))
            .group_by(Category.id, Category.name, Category.parent_id)
            .order_by(func.coalesce(func.sum(Transaction.parsed_amount), 0).desc())
        )
        rows = (await self.db.execute(stmt)).all()

        parent_names = await self._parent_names({r.parent_id for r in rows})
        grand = sum((r.total for r in rows), Decimal("0")) or Decimal("0")

        return [
            {
                "category_id": r.id,
                "category_name": r.name,
                "parent_category": parent_names.get(r.parent_id),
                "total_amount": float(r.total),
                "transaction_count": r.count,
                "percentage": round(float(r.total) / float(grand) * 100, 2) if grand else 0.0,
            }
            for r in rows
        ]

    async def _parent_names(self, ids: set) -> Dict[int, str]:
        ids = {i for i in ids if i}
        if not ids:
            return {}
        rows = (
            await self.db.execute(select(Category.id, Category.name).where(Category.id.in_(ids)))
        ).all()
        return {r.id: r.name for r in rows}

    # --------------------------------------------------------------- by mode

    async def _by_mode(self, start: datetime, end: datetime, person_id: int) -> List[Dict]:
        conds = [
            Transaction.txn_timestamp >= start,
            Transaction.txn_timestamp <= end,
            Transaction.is_spam.is_(False),
            Transaction.txn_type == DEBIT,
            Transaction.txn_mode.is_not(None),
        ]
        if person_id:
            conds.append(Transaction.person_id == person_id)

        stmt = (
            select(
                Transaction.txn_mode,
                func.coalesce(func.sum(Transaction.parsed_amount), 0).label("total"),
                func.count(Transaction.id).label("count"),
            )
            .where(and_(*conds))
            .group_by(Transaction.txn_mode)
            .order_by(func.coalesce(func.sum(Transaction.parsed_amount), 0).desc())
        )
        rows = (await self.db.execute(stmt)).all()
        grand = sum((r.total for r in rows), Decimal("0")) or Decimal("0")

        return [
            {
                "mode": r.txn_mode.value if r.txn_mode else "unknown",
                "total_amount": float(r.total),
                "transaction_count": r.count,
                "percentage": round(float(r.total) / float(grand) * 100, 2) if grand else 0.0,
            }
            for r in rows
        ]

    # ------------------------------------------------------------- by person

    async def _by_person(self, start: datetime, end: datetime, person_id: int) -> List[Dict]:
        conds = [
            Transaction.txn_timestamp >= start,
            Transaction.txn_timestamp <= end,
            Transaction.is_spam.is_(False),
        ]
        if person_id:
            conds.append(Transaction.person_id == person_id)

        # Paid by (transaction owner)
        paid_stmt = (
            select(
                Person.id,
                Person.name,
                func.coalesce(func.sum(Transaction.parsed_amount), 0).label("paid"),
            )
            .select_from(Transaction)
            .join(Person, Transaction.person_id == Person.id)
            .where(and_(*conds))
            .group_by(Person.id, Person.name)
        )
        people = {
            r.id: {"id": r.id, "name": r.name, "paid": float(r.paid),
                   "expense_for": 0.0, "income_from": 0.0}
            for r in (await self.db.execute(paid_stmt)).all()
        }

        # Expense for / Income from (allocations, excluding paid_by rows)
        alloc_stmt = (
            select(
                TransactionAllocation.person_id,
                TransactionAllocation.allocation_type,
                func.coalesce(func.sum(TransactionAllocation.amount), 0).label("amt"),
            )
            .select_from(TransactionAllocation)
            .join(Transaction, TransactionAllocation.transaction_id == Transaction.id)
            .where(and_(*conds))
            .group_by(TransactionAllocation.person_id, TransactionAllocation.allocation_type)
        )
        for row in (await self.db.execute(alloc_stmt)).all():
            if row.person_id is None:
                continue  # "Home (Both)" tracked separately below
            entry = people.setdefault(row.person_id, {
                "id": row.person_id, "name": None, "paid": 0.0,
                "expense_for": 0.0, "income_from": 0.0,
            })
            if row.allocation_type == EXPENSE_FOR:
                entry["expense_for"] = float(row.amt)
            elif row.allocation_type == INCOME_FROM:
                entry["income_from"] = float(row.amt)

        # Shared ("Home") bucket
        home_stmt = (
            select(
                TransactionAllocation.allocation_type,
                func.coalesce(func.sum(TransactionAllocation.amount), 0).label("amt"),
            )
            .select_from(TransactionAllocation)
            .join(Transaction, TransactionAllocation.transaction_id == Transaction.id)
            .where(and_(*conds, TransactionAllocation.person_id.is_(None)))
            .group_by(TransactionAllocation.allocation_type)
        )
        home = {"expense_for": 0.0, "income_from": 0.0}
        for row in (await self.db.execute(home_stmt)).all():
            if row.allocation_type == EXPENSE_FOR:
                home["expense_for"] = float(row.amt)
            elif row.allocation_type == INCOME_FROM:
                home["income_from"] = float(row.amt)

        # Fill names for people seen only via allocations
        missing = [pid for pid, e in people.items() if not e["name"]]
        if missing:
            rows = (
                await self.db.execute(
                    select(Person.id, Person.name).where(Person.id.in_(missing))
                )
            ).all()
            for r in rows:
                people[r.id]["name"] = r.name

        if any(home.values()):
            people[-1] = {
                "id": -1,
                "name": "Home (Shared)",
                "paid": 0.0,
                "expense_for": home["expense_for"],
                "income_from": home["income_from"],
            }

        out = list(people.values())
        for p in out:
            p["net"] = round(p["income_from"] - p["expense_for"], 2)
        return out

    # ---------------------------------------------------------------- trends

    async def _trend(self, grain: str, buckets: int, person_id: Optional[int]) -> List[Dict]:
        now = datetime.now()
        if grain == "day":
            start = now - timedelta(days=buckets)
        elif grain == "week":
            start = now - timedelta(weeks=buckets)
        else:
            start = now - timedelta(days=buckets * 31)

        dialect = self.db.get_bind().dialect.name
        trunc = _truncate(Transaction.txn_timestamp, grain, dialect)

        conds = [
            Transaction.txn_timestamp >= start,
            Transaction.is_spam.is_(False),
        ]
        if person_id:
            conds.append(Transaction.person_id == person_id)

        stmt = (
            select(
                trunc.label("bucket"),
                func.coalesce(func.sum(case((Transaction.txn_type == DEBIT,
                                             Transaction.parsed_amount), else_=0)), 0).label("expense"),
                func.coalesce(func.sum(case((Transaction.txn_type == CREDIT,
                                             Transaction.parsed_amount), else_=0)), 0).label("income"),
            )
            .where(and_(*conds))
            .group_by("bucket")
            .order_by("bucket")
        )
        rows = (await self.db.execute(stmt)).all()

        out = []
        for r in rows:
            b = r.bucket
            expense = float(r.expense or 0)
            income = float(r.income or 0)
            out.append({
                "bucket": b.isoformat() if hasattr(b, "isoformat") else str(b),
                "expense": expense,
                "income": income,
                "net": round(income - expense, 2),
            })
        return out

    async def get_trend(self, grain: str, buckets: int, person_id: Optional[int] = None) -> List[Dict]:
        """Public entry point for time-series data."""
        return await self._trend(grain, buckets, person_id)

    async def get_summary(self, period: str, person_id: Optional[int] = None) -> Dict[str, Any]:
        start = _period_start(period)
        end = datetime.now()

        totals = await self._totals(start, end, person_id)
        by_category = await self._by_category(start, end, person_id)
        by_mode = await self._by_mode(start, end, person_id)
        by_person = await self._by_person(start, end, person_id)

        return {
            **totals,
            "period": period,
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
            "by_category": by_category,
            "by_mode": by_mode,
            "by_person": by_person,
            "daily_trend": await self._trend("day", 30, person_id),
            "weekly_trend": await self._trend("week", 12, person_id),
            "monthly_trend": await self._trend("month", 12, person_id),
        }

    async def get_averages(self, period: str, person_id: Optional[int] = None) -> Dict[str, Any]:
        start = _period_start(period)
        end = datetime.now()
        totals = await self._totals(start, end, person_id)
        days = max((end - start).days, 1)

        expense = float(totals["total_expense"])
        n = totals["transaction_count"]

        # Expense only (transfers/credits excluded from spend averages)
        conds = [
            Transaction.txn_timestamp >= start,
            Transaction.txn_timestamp <= end,
            Transaction.is_spam.is_(False),
            Transaction.txn_type == DEBIT,
        ]
        if person_id:
            conds.append(Transaction.person_id == person_id)
        txn_count = (
            await self.db.execute(select(func.count(Transaction.id)).where(and_(*conds)))
        ).scalar_one() or 0

        return {
            "days_in_period": days,
            "transaction_count": n,
            "expense_transaction_count": txn_count,
            "avg_per_day": round(expense / days, 2),
            "avg_per_week": round(expense / (days / 7), 2),
            "avg_per_month": round(expense / (days / 30.44), 2),
            "avg_per_expense": round(expense / txn_count, 2) if txn_count else 0.0,
            "avg_income_per_day": round(float(totals["total_income"]) / days, 2),
        }

    # ------------------------------------------------------------ cashback

    async def get_cashback(self, months: int = 6) -> List[Dict]:
        start = datetime.now() - timedelta(days=months * 31)
        stmt = (
            select(
                Account.nickname,
                Account.bank_name,
                Account.last_four,
                Account.default_cashback_pct,
                Transaction.merchant,
                Transaction.parsed_amount,
                Transaction.cashback_amount,
                Transaction.cashback_type,
                Transaction.cashback_wallet,
                Transaction.cashback_status,
                Transaction.txn_timestamp,
            )
            .select_from(Transaction)
            .join(Account, Transaction.account_id == Account.id)
            .where(and_(Transaction.cashback_amount > 0,
                        Transaction.txn_timestamp >= start))
            .order_by(Transaction.txn_timestamp.desc())
        )
        out = []
        for r in (await self.db.execute(stmt)).all():
            spend = float(r.parsed_amount or 0)
            cb = float(r.cashback_amount or 0)
            out.append({
                "card": r.nickname or f"{r.bank_name} {r.last_four or ''}".strip(),
                "bank_name": r.bank_name,
                "merchant": r.merchant,
                "amount": spend,
                "cashback_amount": cb,
                "effective_pct": round(cb / spend * 100, 2) if spend else 0.0,
                "card_default_pct": float(r.default_cashback_pct or 0),
                "cashback_type": r.cashback_type.value if r.cashback_type else None,
                "cashback_wallet": r.cashback_wallet,
                "cashback_status": r.cashback_status.value if r.cashback_status else None,
                "date": r.txn_timestamp.isoformat(),
            })
        return out

    async def get_cashback_totals(self, months: int = 6) -> Dict[str, Any]:
        start = datetime.now() - timedelta(days=months * 31)
        conds = [Transaction.cashback_amount > 0, Transaction.txn_timestamp >= start]

        stmt = (
            select(
                func.coalesce(func.sum(Transaction.cashback_amount), 0).label("total"),
                func.coalesce(func.sum(Transaction.parsed_amount), 0).label("spend"),
                func.count(Transaction.id).label("count"),
            )
            .where(and_(*conds))
        )
        row = (await self.db.execute(stmt)).one()

        status_stmt = (
            select(Transaction.cashback_status, func.coalesce(func.sum(Transaction.cashback_amount), 0))
            .where(and_(*conds))
            .group_by(Transaction.cashback_status)
        )
        by_status = {s.value if s else "pending": float(v) for s, v in (await self.db.execute(status_stmt)).all()}

        total = float(row.total or 0)
        spend = float(row.spend or 0)
        return {
            "total_expected": total,
            "total_spend": spend,
            "transaction_count": row.count or 0,
            "effective_rate": round(total / spend * 100, 2) if spend else 0.0,
            "pending": by_status.get("pending", 0.0),
            "received": by_status.get("received", 0.0),
            "expired": by_status.get("expired", 0.0),
            "by_status": by_status,
        }

    # ----------------------------------------------------------- recurring

    async def get_recurring(self, days_ahead: int = 30) -> List[Dict]:
        rows = (
            await self.db.execute(
                select(RecurringTransaction)
                .where(RecurringTransaction.is_active.is_(True))
                .order_by(RecurringTransaction.next_expected_date)
            )
        ).scalars().all()

        cutoff = datetime.now() + timedelta(days=days_ahead)
        out = []
        for r in rows:
            due = r.next_expected_date
            out.append({
                "id": r.id,
                "merchant": r.merchant,
                "amount": float(r.amount or 0),
                "frequency": r.frequency,
                "next_expected_date": due.isoformat() if due else None,
                "is_due_soon": bool(due and due <= cutoff),
                "confidence": r.confidence,
                "last_detected_at": r.last_detected_at.isoformat() if r.last_detected_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            })
        return out