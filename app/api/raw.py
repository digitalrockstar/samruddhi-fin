"""Raw message archive and re-derivation.

Every inbound message is stored before parsing. These endpoints make that
archive visible and let you rebuild transactions after the parser improves.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import RawMessage, Transaction
from app.services import telegram as tg
from app.services.telegram import TelegramProcessor


def parser_version() -> str:
    """Read the version through the module, not a copied binding.

    Importing the constant directly would freeze the value at import time and
    silently stop detecting stale rows after an upgrade.
    """
    return tg.PARSER_VERSION

router = APIRouter(prefix="/api/raw", tags=["raw-messages"])


class RawMessageResponse(BaseModel):
    id: int
    source: str
    external_id: Optional[str] = None
    chat_id: Optional[str] = None
    sender_user_id: Optional[str] = None
    sender_name: Optional[str] = None
    prefix_detected: Optional[str] = None
    body: str
    raw_text: Optional[str] = None
    received_at: datetime
    verdict: Optional[str] = None
    reason: Optional[str] = None
    rule: Optional[str] = None
    amount: Optional[Any] = None
    txn_type: Optional[str] = None
    account_last_four: Optional[str] = None
    merchant: Optional[str] = None
    upi_handle: Optional[str] = None
    transfer_kind: Optional[str] = None
    parser_version: Optional[str] = None
    parsed_at: Optional[datetime] = None
    transaction_id: Optional[int] = None
    note: Optional[str] = None

    model_config = {"from_attributes": True}


def serialize(r: RawMessage) -> RawMessageResponse:
    return RawMessageResponse(
        id=r.id,
        source=r.source,
        external_id=r.external_id,
        chat_id=r.chat_id,
        sender_user_id=r.sender_user_id,
        sender_name=r.sender_name,
        prefix_detected=r.prefix_detected,
        body=r.body or "",
        raw_text=r.raw_text,
        received_at=r.received_at,
        verdict=r.verdict,
        reason=r.reason,
        rule=r.rule,
        amount=r.amount,
        txn_type=r.txn_type.value if r.txn_type else None,
        account_last_four=r.account_last_four,
        merchant=r.merchant,
        upi_handle=r.upi_handle,
        transfer_kind=r.transfer_kind,
        parser_version=r.parser_version,
        parsed_at=r.parsed_at,
        transaction_id=r.transaction_id,
        note=r.note,
    )


@router.get("", response_model=List[RawMessageResponse])
async def list_raw(
    verdict: Optional[str] = Query(None, pattern="^(posted|not_posted)$"),
    reason: Optional[str] = None,
    rule: Optional[str] = None,
    prefix: Optional[str] = None,
    stale_only: bool = Query(False, description="parsed by an older parser version"),
    unlinked: bool = Query(False, description="stored but has no transaction"),
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
):
    filters = []
    if verdict:
        filters.append(RawMessage.verdict == verdict)
    if reason:
        filters.append(RawMessage.reason == reason)
    if rule:
        filters.append(RawMessage.rule == rule)
    if prefix:
        filters.append(RawMessage.prefix_detected == prefix)
    if stale_only:
        filters.append(RawMessage.parser_version != parser_version())
    if unlinked:
        filters.append(RawMessage.transaction_id.is_(None))

    total = await db.scalar(select(func.count(RawMessage.id)).where(*filters)) or 0

    rows = (
        await db.execute(
            select(RawMessage)
            .where(*filters)
            .order_by(RawMessage.received_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return [serialize(r) for r in rows]


@router.get("/stats")
async def raw_stats(db: AsyncSession = Depends(get_db)):
    """What the archive currently contains, and what is stale."""
    by_verdict = dict(
        (
            await db.execute(
                select(RawMessage.verdict, func.count(RawMessage.id))
                .group_by(RawMessage.verdict)
            )
        ).all()
    )
    by_reason = {
        (r or "?"): c
        for r, c in (
            await db.execute(
                select(RawMessage.reason, func.count(RawMessage.id))
                .where(RawMessage.verdict == "not_posted")
                .group_by(RawMessage.reason)
            )
        ).all()
    }
    stale = await db.scalar(
        select(func.count(RawMessage.id)).where(
            RawMessage.parser_version != parser_version()
        )
    ) or 0
    unlinked = await db.scalar(
        select(func.count(RawMessage.id)).where(RawMessage.transaction_id.is_(None))
    ) or 0
    total_txn = await db.scalar(select(func.count(Transaction.id))) or 0

    return {
        "parser_version": parser_version(),
        "total_raw": sum(by_verdict.values()),
        "by_verdict": by_verdict,
        "by_rejection_reason": by_reason,
        "parsed_by_older_version": stale,
        "stored_without_transaction": unlinked,
        "total_transactions": total_txn,
    }


@router.get("/{raw_id}", response_model=RawMessageResponse)
async def get_raw(raw_id: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(RawMessage, raw_id)
    if not row:
        raise HTTPException(404, "Raw message not found")
    return serialize(row)


class ReprocessRequest(BaseModel):
    ids: Optional[List[int]] = None            # explicit rows
    stale_only: bool = True                     # only rows from an older parser
    verdict: Optional[str] = None
    prefix: Optional[str] = None
    replace: bool = True                        # rebuild the transaction
    dry_run: bool = False
    limit: int = 5000


@router.post("/reprocess")
async def reprocess(body: ReprocessRequest, db: AsyncSession = Depends(get_db)):
    """Re-parse stored messages and rebuild their transactions.

    This is what makes the raw archive worth having: improve the parser, then
    re-derive everything without re-forwarding a single SMS.
    """
    filters = []
    if body.ids:
        filters.append(RawMessage.id.in_(body.ids))
    else:
        if body.stale_only:
            filters.append(RawMessage.parser_version != parser_version())
        if body.verdict:
            filters.append(RawMessage.verdict == body.verdict)
        if body.prefix:
            filters.append(RawMessage.prefix_detected == body.prefix)

    stmt = select(RawMessage).where(*filters).order_by(RawMessage.received_at)
    stmt = stmt.limit(body.limit)
    rows = (await db.execute(stmt)).scalars().all()

    processor = TelegramProcessor(db)
    result = {
        "parser_version": parser_version(),
        "examined": len(rows),
        "rebuilt": 0,
        "no_longer_transactions": 0,
        "failed": 0,
        "dry_run": body.dry_run,
        "changes": [],
    }

    for row in rows:
        before = _snapshot(row)
        had_txn = row.transaction_id is not None

        if body.dry_run:
            # Genuinely read-only: preview writes nothing, so there is nothing
            # to roll back and no way for this to mutate state.
            parsed, would_create, note = await processor.preview(row)
            if would_create:
                result["rebuilt"] += 1
            elif had_txn:
                result["no_longer_transactions"] += 1
            else:
                result["failed"] += 1
            after = {
                "raw_id": row.id,
                "rule": parsed.rule,
                "verdict": parsed.verdict,
                "amount": parsed.amount,
                "merchant": parsed.merchant,
            }
        else:
            txn, note = await processor.derive(row, replace=body.replace)
            if txn is not None:
                result["rebuilt"] += 1
            elif had_txn:
                result["no_longer_transactions"] += 1
            else:
                result["failed"] += 1
            after = _snapshot(row)

        if before != after and len(result["changes"]) < 50:
            result["changes"].append({"before": before, "after": after, "note": note})

    return result


def _snapshot(row: RawMessage) -> Dict[str, Any]:
    return {
        "raw_id": row.id,
        "rule": row.rule,
        "verdict": row.verdict,
        "amount": row.amount,
        "merchant": row.merchant,
    }


class IngestRequest(BaseModel):
    """Insert a message by hand (e.g. an SMS you never forwarded)."""
    text: str
    prefix: Optional[str] = None
    source: str = "manual"
    derive: bool = True


@router.post("", response_model=RawMessageResponse, status_code=201)
async def ingest_manual(body: IngestRequest, db: AsyncSession = Depends(get_db)):
    """Store a message directly, bypassing Telegram.

    Useful for the occasional SMS you forget to forward. The prefix is
    prepended for you if you give one.
    """
    text = f"{body.prefix} {body.text}" if body.prefix else body.text
    processor = TelegramProcessor(db)
    row = await processor.ingest_raw(text, external_id=None, source=body.source)
    if row is None:
        raise HTTPException(409, "Already ingested")
    if body.derive:
        await processor.derive(row)
        await db.refresh(row)
    return serialize(row)


@router.delete("/{raw_id}")
async def delete_raw(raw_id: int, delete_txn: bool = False, db: AsyncSession = Depends(get_db)):
    row = await db.get(RawMessage, raw_id)
    if not row:
        raise HTTPException(404, "Raw message not found")

    if delete_txn and row.transaction_id:
        txn = await db.get(Transaction, row.transaction_id)
        if txn:
            await db.delete(txn)

    await db.delete(row)
    await db.commit()
    return {"deleted": True, "transaction_deleted": bool(delete_txn and row.transaction_id)}