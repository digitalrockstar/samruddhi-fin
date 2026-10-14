from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_session
from app.models import RawMessage, Review, Tag, LearnedFormat, Transaction
from app.services.learning import build_pattern, group_key
from app.services.processing import process_unreviewed
from app.services.telegram import ingest_once

router = APIRouter()

@router.get("/")
async def home(request: Request, session: AsyncSession = Depends(get_session)):
    pending = await session.scalar(select(Review.id).where(Review.status == "pending").limit(1))
    txns = await session.scalar(select(Transaction.id).limit(1))
    return request.app.state.templates.TemplateResponse("index.html", {"request": request, "pending": bool(pending), "has_txns": bool(txns)})

async def do_ingest(session, backfill):
    max_id = await ingest_once(session, backfill=backfill)
    raws = (await session.scalars(select(RawMessage).where(RawMessage.group_key == ""))).all()
    for raw in raws:
        raw.group_key = group_key(raw.text)
    await session.commit()
    await process_unreviewed(session)

@router.post("/ingest")
async def ingest(session: AsyncSession = Depends(get_session)):
    await do_ingest(session, False)
    return RedirectResponse("/review", 303)

@router.post("/backfill")
async def backfill(session: AsyncSession = Depends(get_session)):
    await do_ingest(session, True)
    return RedirectResponse("/review", 303)

@router.get("/review")
async def review(request: Request, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Review, RawMessage).join(RawMessage, RawMessage.id == Review.raw_message_id).where(Review.status == "pending").order_by(desc(RawMessage.message_date)).limit(500))).all()
    tags = (await session.scalars(select(Tag).where(Tag.enabled.is_(True)).order_by(Tag.name))).all()
    groups = {}
    for rev, raw in rows:
        groups.setdefault(raw.group_key or group_key(raw.text), []).append((rev, raw))
    grouped = sorted(groups.values(), key=len, reverse=True)
    return request.app.state.templates.TemplateResponse("review.html", {"request": request, "groups": grouped, "tags": tags})

@router.post("/review/{review_id}/tag")
async def tag_review(review_id: int, tag_id: int = Form(...), session: AsyncSession = Depends(get_session)):
    review = await session.get(Review, review_id)
    tag = await session.get(Tag, tag_id)
    if not review or not tag:
        raise HTTPException(404)
    review.tag_id = tag.id
    review.decision_source = "manual"
    review.reviewed_at = datetime.now(timezone.utc)
    review.status = "pending" if tag.counts_as_transaction else "confirmed"
    await session.commit()
    return RedirectResponse("/review", 303)

@router.post("/review/group/tag")
async def tag_group(group_key_value: str = Form(...), tag_id: int = Form(...), session: AsyncSession = Depends(get_session)):
    tag = await session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(404)
    rows = (await session.scalars(select(Review).join(RawMessage).where(RawMessage.group_key == group_key_value, Review.status == "pending"))).all()
    for review in rows:
        review.tag_id = tag.id
        review.decision_source = "manual"
        review.reviewed_at = datetime.now(timezone.utc)
        review.status = "pending" if tag.counts_as_transaction else "confirmed"
    await session.commit()
    return RedirectResponse("/review", 303)

@router.get("/review/{review_id}/txn")
async def txn_form(request: Request, review_id: int, session: AsyncSession = Depends(get_session)):
    review = await session.get(Review, review_id)
    raw = await session.get(RawMessage, review.raw_message_id) if review else None
    if not review or not raw:
        raise HTTPException(404)
    return request.app.state.templates.TemplateResponse("txn.html", {"request": request, "review": review, "raw": raw})

@router.post("/review/{review_id}/txn")
async def save_txn(review_id: int, amount: str = Form(...), merchant: str = Form(""), reference_number: str = Form(""), balance: str = Form(""), direction: str = Form(...), mode: str = Form(""), account: str = Form(""), owner: str = Form(""), amount_start: int = Form(0), amount_end: int = Form(0), merchant_start: int = Form(0), merchant_end: int = Form(0), reference_start: int = Form(0), reference_end: int = Form(0), balance_start: int = Form(0), balance_end: int = Form(0), direction_start: int = Form(0), direction_end: int = Form(0), learn: bool = Form(False), session: AsyncSession = Depends(get_session)):
    review = await session.get(Review, review_id)
    raw = await session.get(RawMessage, review.raw_message_id) if review else None
    if not review or not raw:
        raise HTTPException(404)
    tag = await session.get(Tag, review.tag_id) if review.tag_id else None
    if not tag or not tag.counts_as_transaction:
        raise HTTPException(400, "Select a transaction tag first")
    spans = {"amount": (amount_start, amount_end), "merchant": (merchant_start, merchant_end), "reference": (reference_start, reference_end), "balance": (balance_start, balance_end), "direction": (direction_start, direction_end)}
    fmt_id = review.format_id
    if learn:
        pattern, specificity = build_pattern(raw.text, spans)
        fmt = LearnedFormat(name=f"Learned {raw.message_id}", pattern=pattern, specificity=specificity, tag_id=tag.id, direction=direction, direction_keywords=direction, mode=mode or None, account=account or None, owner=owner or None)
        session.add(fmt)
        await session.flush()
        fmt_id = fmt.id
        review.format_id = fmt.id
    if not fmt_id:
        raise HTTPException(400, "Learning the format is required for a new transaction")
    try:
        amt = Decimal(amount.replace(",", ""))
        bal = Decimal(balance.replace(",", "")) if balance else None
    except Exception:
        raise HTTPException(400, "Invalid amount")
    exists = await session.scalar(select(Transaction.id).where(Transaction.raw_message_id == raw.id))
    if not exists:
        session.add(Transaction(raw_message_id=raw.id, format_id=fmt_id, source="manual", amount=amt, merchant=merchant or None, reference_number=reference_number or None, balance=bal, direction=direction, mode=mode or None, account=account or None, owner=owner or None))
    review.decision_source, review.status, review.reviewed_at = "manual", "confirmed", datetime.now(timezone.utc)
    await session.commit()
    return RedirectResponse("/review", 303)

@router.post("/review/{review_id}/undo")
async def undo(review_id: int, session: AsyncSession = Depends(get_session)):
    review = await session.get(Review, review_id)
    if not review:
        raise HTTPException(404)
    review.status = "pending"
    review.decision_source = "undo"
    review.reviewed_at = None
    txn = await session.scalar(select(Transaction).where(Transaction.raw_message_id == review.raw_message_id))
    if txn:
        await session.delete(txn)
    await session.commit()
    return RedirectResponse("/review", 303)

@router.get("/spot-check")
async def spot_check(request: Request, session: AsyncSession = Depends(get_session)):
    from sqlalchemy import func
    rows = (await session.execute(select(Review, RawMessage).join(RawMessage, RawMessage.id == Review.raw_message_id).where(Review.decision_source == "auto").order_by(func.random()).limit(10))).all()
    return request.app.state.templates.TemplateResponse("spot_check.html", {"request": request, "rows": rows})

@router.get("/settings")
async def settings_page(request: Request, session: AsyncSession = Depends(get_session)):
    tags = (await session.scalars(select(Tag).order_by(Tag.name))).all()
    formats = (await session.scalars(select(LearnedFormat).order_by(desc(LearnedFormat.match_count)))).all()
    return request.app.state.templates.TemplateResponse("settings.html", {"request": request, "tags": tags, "formats": formats})

@router.post("/settings/tags")
async def create_tag(name: str = Form(...), counts_as_transaction: bool = Form(False), session: AsyncSession = Depends(get_session)):
    session.add(Tag(name=name.strip(), counts_as_transaction=counts_as_transaction))
    await session.commit()
    return RedirectResponse("/settings", 303)

@router.post("/settings/formats/{format_id}/disable")
async def disable_format(format_id: int, session: AsyncSession = Depends(get_session)):
    fmt = await session.get(LearnedFormat, format_id)
    if not fmt:
        raise HTTPException(404)
    fmt.enabled = False
    await session.commit()
    return RedirectResponse("/settings", 303)

@router.post("/settings/tags/{tag_id}/disable")
async def disable_tag(tag_id: int, session: AsyncSession = Depends(get_session)):
    tag = await session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(404)
    tag.enabled = False
    await session.commit()
    return RedirectResponse("/settings", 303)

@router.post("/settings/formats/{format_id}/delete")
async def delete_format(format_id: int, session: AsyncSession = Depends(get_session)):
    fmt = await session.get(LearnedFormat, format_id)
    if not fmt:
        raise HTTPException(404)
    await session.delete(fmt)
    await session.commit()
    return RedirectResponse("/settings", 303)
