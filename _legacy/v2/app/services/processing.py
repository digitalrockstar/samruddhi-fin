from datetime import datetime, timezone
from sqlalchemy import select
from app.models import RawMessage, Review, LearnedFormat, Transaction, Tag
from app.services.matcher import match_formats, parse_amount

async def process_raw(session, raw: RawMessage):
    formats = list((await session.scalars(select(LearnedFormat).where(LearnedFormat.enabled.is_(True)))).all())
    matches = match_formats(raw.text, formats)
    if not matches:
        review = await session.scalar(select(Review).where(Review.raw_message_id == raw.id))
        if review is None:
            session.add(Review(raw_message_id=raw.id, status="pending", decision_source="system"))
            await session.commit()
        return None
    winner = matches[0]
    fmt = winner.fmt
    tag = await session.get(Tag, fmt.tag_id)
    review = await session.scalar(select(Review).where(Review.raw_message_id == raw.id))
    if review is None:
        review = Review(raw_message_id=raw.id)
        session.add(review)
    review.tag_id, review.format_id, review.decision_source, review.status, review.reviewed_at = fmt.tag_id, fmt.id, "auto", "auto", datetime.now(timezone.utc)
    fmt.match_count += 1
    if tag and tag.counts_as_transaction:
        amount = parse_amount(winner.groups.get("amount"))
        if amount is None:
            review.status = "pending"
            await session.commit()
            return review
        exists = await session.scalar(select(Transaction.id).where(Transaction.raw_message_id == raw.id))
        if not exists:
            session.add(Transaction(
                raw_message_id=raw.id, format_id=fmt.id, source="auto", amount=amount,
                merchant=winner.groups.get("merchant"),
                reference_number=winner.groups.get("reference"),
                balance=parse_amount(winner.groups.get("balance")),
                direction=fmt.direction or winner.groups.get("direction") or "debit",
                mode=fmt.mode, account=fmt.account, owner=fmt.owner
            ))
    await session.commit()
    return review

async def process_unreviewed(session):
    rows = (await session.scalars(select(RawMessage).where(~RawMessage.id.in_(select(Review.raw_message_id))))).all()
    for raw in rows:
        await process_raw(session, raw)
