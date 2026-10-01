import csv
import io
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import (
    Transaction, TransactionAllocation, MerchantMapping, Category
)
from app.schemas import (
    PaginatedResponse,
    TransactionAllocationResponse,
    TransactionResponse,
    TransactionType,
    TransactionUpdate,
)

router = APIRouter(prefix="/api/transactions", tags=["transactions"])


TXN_LOADS = (
    selectinload(Transaction.person),
    selectinload(Transaction.account),
    selectinload(Transaction.category),
    selectinload(Transaction.allocations).selectinload(TransactionAllocation.person),
)


def serialize(t: Transaction) -> TransactionResponse:
    """Build a TransactionResponse from an ORM row with relations loaded."""
    return TransactionResponse(
        id=t.id,
        message_id=t.message_id,
        person_id=t.person_id,
        person_name=t.person.name if t.person else "Unknown",
        account_id=t.account_id,
        account_nickname=t.account.nickname if t.account else None,
        category_id=t.category_id,
        category_name=t.category.name if t.category else None,
        category_type=t.category.type if t.category else None,
        raw_text=t.raw_text,
        parsed_amount=t.parsed_amount,
        currency=t.currency or "INR",
        txn_type=t.txn_type,
        txn_mode=t.txn_mode,
        merchant=t.merchant,
        upi_ref=t.upi_ref,
        txn_timestamp=t.txn_timestamp,
        received_at=t.received_at,
        cashback_amount=t.cashback_amount or Decimal("0"),
        cashback_type=t.cashback_type,
        cashback_wallet=t.cashback_wallet,
        cashback_status=t.cashback_status,
        is_spam=bool(t.is_spam),
        needs_review=bool(t.needs_review),
        is_split_parent=bool(t.is_split_parent),
        parent_txn_id=t.parent_txn_id,
        notes=t.notes,
        created_at=t.created_at,
        allocations=[
            TransactionAllocationResponse(
                id=a.id,
                transaction_id=a.transaction_id,
                person_id=a.person_id,
                person_name=a.person.name if a.person else None,
                amount=a.amount,
                allocation_type=a.allocation_type,
                notes=a.notes,
                created_at=a.created_at,
            )
            for a in t.allocations
        ],
    )


def build_filters(
    person_id=None,
    account_id=None,
    category_id=None,
    txn_type=None,
    txn_mode=None,
    start_date=None,
    end_date=None,
    min_amount=None,
    max_amount=None,
    merchant=None,
    needs_review=None,
    is_spam=None,
):
    filters = []
    if person_id:
        filters.append(Transaction.person_id == person_id)
    if account_id:
        filters.append(Transaction.account_id == account_id)
    if category_id:
        filters.append(Transaction.category_id == category_id)
    if txn_type:
        filters.append(Transaction.txn_type == txn_type)
    if txn_mode:
        filters.append(Transaction.txn_mode == txn_mode)
    if start_date:
        filters.append(Transaction.txn_timestamp >= start_date)
    if end_date:
        filters.append(Transaction.txn_timestamp <= end_date)
    if min_amount:
        filters.append(Transaction.parsed_amount >= min_amount)
    if max_amount:
        filters.append(Transaction.parsed_amount <= max_amount)
    if merchant:
        filters.append(Transaction.merchant.ilike(f"%{merchant}%"))
    if needs_review is not None:
        filters.append(Transaction.needs_review == needs_review)
    if is_spam is not None:
        filters.append(Transaction.is_spam == is_spam)
    return filters


# ---------------------------------------------------------------- list + export
# NOTE: /export and /uncategorized must be declared before /{transaction_id}
# so the literal paths win over the path param.


@router.get("", response_model=PaginatedResponse)
async def list_transactions(
    person_id: Optional[int] = None,
    account_id: Optional[int] = None,
    category_id: Optional[int] = None,
    txn_type: Optional[TransactionType] = None,
    txn_mode: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    min_amount: Optional[Decimal] = None,
    max_amount: Optional[Decimal] = None,
    merchant: Optional[str] = None,
    needs_review: Optional[bool] = None,
    is_spam: Optional[bool] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    filters = build_filters(
        person_id, account_id, category_id, txn_type, txn_mode,
        start_date, end_date, min_amount, max_amount, merchant,
        needs_review, is_spam,
    )

    total = await db.scalar(select(func.count(Transaction.id)).where(*filters)) or 0

    stmt = (
        select(Transaction)
        .options(*TXN_LOADS)
        .where(*filters)
        .order_by(Transaction.txn_timestamp.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = (await db.execute(stmt)).scalars().all()

    return PaginatedResponse(
        items=[serialize(t) for t in rows],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
    )


@router.get("/uncategorized", response_model=List[TransactionResponse])
async def get_uncategorized(db: AsyncSession = Depends(get_db)):
    stmt = (
        select(Transaction)
        .options(*TXN_LOADS)
        .where(Transaction.needs_review.is_(True), Transaction.is_spam.is_(False))
        .order_by(Transaction.txn_timestamp.desc())
        .limit(100)
    )
    rows = (await db.execute(stmt)).scalars().all()
    return [serialize(t) for t in rows]


@router.get("/export/csv")
async def export_transactions(
    person_id: Optional[int] = None,
    account_id: Optional[int] = None,
    category_id: Optional[int] = None,
    txn_type: Optional[TransactionType] = None,
    txn_mode: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    merchant: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    filters = build_filters(
        person_id, account_id, category_id, txn_type, txn_mode,
        start_date, end_date, None, None, merchant, None, None,
    )
    filters.append(Transaction.is_spam.is_(False))

    stmt = (
        select(Transaction)
        .options(*TXN_LOADS)
        .where(*filters)
        .order_by(Transaction.txn_timestamp.desc())
    )
    rows = (await db.execute(stmt)).scalars().all()

    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow([
        "Date", "Paid By", "Account", "Bank", "Category", "Category Type",
        "Txn Type", "Mode", "Merchant", "Amount", "Currency",
        "Cashback", "Cashback Type", "Cashback Wallet", "Cashback Status",
        "Expense For", "Income From", "Notes", "Raw Text",
    ])

    for t in rows:
        expense_for = "; ".join(
            f"{a.person.name if a.person else 'Home'}: {a.amount}"
            for a in t.allocations if a.allocation_type.value == "expense_for"
        )
        income_from = "; ".join(
            f"{a.person.name if a.person else 'Home'}: {a.amount}"
            for a in t.allocations if a.allocation_type.value == "income_from"
        )
        w.writerow([
            t.txn_timestamp.strftime("%Y-%m-%d %H:%M"),
            t.person.name if t.person else "",
            t.account.nickname if t.account else "",
            t.account.bank_name if t.account else "",
            t.category.name if t.category else "",
            t.category.type.value if t.category else "",
            t.txn_type.value,
            t.txn_mode.value if t.txn_mode else "",
            t.merchant or "",
            t.parsed_amount,
            t.currency or "INR",
            t.cashback_amount or 0,
            t.cashback_type.value if t.cashback_type else "",
            t.cashback_wallet or "",
            t.cashback_status.value if t.cashback_status else "",
            expense_for,
            income_from,
            t.notes or "",
            (t.raw_text or "")[:500],
        ])

    buf.seek(0)
    filename = f"samruddhi_transactions_{datetime.now():%Y%m%d_%H%M}.csv"
    return StreamingResponse(
        io.BytesIO(buf.getvalue().encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class BulkCategorizeRequest(BaseModel):
    transaction_ids: List[int]
    category_id: int


@router.post("/bulk-categorize")
async def bulk_categorize(
    body: BulkCategorizeRequest,
    db: AsyncSession = Depends(get_db),
):
    if not body.transaction_ids:
        return {"updated": 0}

    rows = (
        await db.execute(
            select(Transaction).where(Transaction.id.in_(body.transaction_ids))
        )
    ).scalars().all()

    category = await db.get(Category, body.category_id)
    if not category:
        raise HTTPException(404, "Category not found")

    for t in rows:
        t.category_id = body.category_id
        t.needs_review = False
        if t.merchant:
            existing = (
                await db.execute(
                    select(MerchantMapping).where(MerchantMapping.pattern == t.merchant)
                )
            ).scalar_one_or_none()
            if existing:
                existing.category_id = body.category_id
                existing.is_active = True
                existing.confidence = 100
            else:
                db.add(MerchantMapping(
                    pattern=t.merchant,
                    category_id=body.category_id,
                    confidence=100,
                    transaction_id=t.id,
                ))

    await db.commit()
    return {"updated": len(rows)}


# ---------------------------------------------------------------- single row


@router.get("/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(transaction_id: int, db: AsyncSession = Depends(get_db)):
    t = (
        await db.execute(
            select(Transaction).options(*TXN_LOADS).where(Transaction.id == transaction_id)
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Transaction not found")
    return serialize(t)


@router.patch("/{transaction_id}", response_model=TransactionResponse)
async def update_transaction(
    transaction_id: int,
    update: TransactionUpdate,
    db: AsyncSession = Depends(get_db),
):
    t = (
        await db.execute(
            select(Transaction).options(*TXN_LOADS).where(Transaction.id == transaction_id)
        )
    ).scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Transaction not found")

    data = update.model_dump(exclude_unset=True)
    allocations_in = data.pop("allocations", None)

    for field, value in data.items():
        setattr(t, field, value)

    if allocations_in is not None:
        await db.execute(
            delete(TransactionAllocation).where(TransactionAllocation.transaction_id == t.id)
        )
        for a in allocations_in:
            db.add(TransactionAllocation(
                transaction_id=t.id,
                person_id=a.get("person_id"),
                amount=a["amount"],
                allocation_type=a["allocation_type"],
                notes=a.get("notes"),
            ))
        t.is_split_parent = len(allocations_in) > 1

    # Learning: a manual category decision teaches the merchant mapping
    if "category_id" in data and t.category_id and t.merchant:
        existing = (
            await db.execute(
                select(MerchantMapping).where(MerchantMapping.pattern == t.merchant)
            )
        ).scalar_one_or_none()
        if existing:
            existing.category_id = t.category_id
            existing.confidence = 100
            existing.is_active = True
        else:
            db.add(MerchantMapping(
                pattern=t.merchant,
                category_id=t.category_id,
                confidence=100,
                transaction_id=t.id,
            ))
        t.needs_review = False

    await db.commit()

    # Reload so relationships reflect the new allocations
    t = (
        await db.execute(
            select(Transaction).options(*TXN_LOADS).where(Transaction.id == transaction_id)
        )
    ).scalar_one()
    return serialize(t)


@router.delete("/{transaction_id}")
async def delete_transaction(transaction_id: int, db: AsyncSession = Depends(get_db)):
    t = await db.get(Transaction, transaction_id)
    if not t:
        raise HTTPException(404, "Transaction not found")
    await db.delete(t)
    await db.commit()
    return {"deleted": True}