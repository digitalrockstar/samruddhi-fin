"""Review queue for SMS formats no rule recognises, plus the decisions made on them."""
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import RawMessage, SmsTemplate
from app.services.telegram import TelegramProcessor
from app.services.templates import DECISIONS, mask, skeleton

router = APIRouter(prefix="/api/formats", tags=["formats"])


async def _rows_for(db: AsyncSession, key: str):
    """Raw rows that went through the unknown-format path and share this format."""
    rows = (await db.execute(select(RawMessage).where(RawMessage.rule == "generic_fallback"))).scalars().all()
    return [r for r in rows if skeleton(r.body) == key]


async def _reprocess(db: AsyncSession, key: str) -> dict:
    proc = TelegramProcessor(db)
    created = kept = 0
    for row in await _rows_for(db, key):
        txn, note = await proc.derive(row, replace=True)
        if note.startswith("kept:"):
            kept += 1
        elif txn is not None:
            created += 1
    return {"created": created, "kept_manual": kept}


@router.get("/queue")
async def queue(limit: int = 100, db: AsyncSession = Depends(get_db)):
    """Undecided formats, biggest first: one tap clears every message of that format."""
    rows = (await db.execute(
        select(RawMessage).where(RawMessage.verdict == "review").order_by(RawMessage.id.desc())
    )).scalars().all()
    groups = defaultdict(list)
    for r in rows:
        groups[skeleton(r.body)].append(r)
    items = [{"skeleton": k, "count": len(v), "example": mask(v[0].body)[:400],
              "amount": v[0].amount, "senders": sorted({r.prefix_detected or "?" for r in v})}
             for k, v in groups.items()]
    items.sort(key=lambda i: -i["count"])
    return {"pending_formats": len(items), "pending_messages": len(rows), "items": items[:limit]}


class Decision(BaseModel):
    skeleton: str
    decision: str
    mode: Optional[str] = None


@router.post("/decide")
async def decide(body: Decision, db: AsyncSession = Depends(get_db)):
    if body.decision not in DECISIONS:
        raise HTTPException(400, f"decision must be one of {sorted(DECISIONS)}")
    t = (await db.execute(select(SmsTemplate).where(SmsTemplate.skeleton == body.skeleton))).scalars().first()
    if t:
        t.decision, t.mode = body.decision, body.mode
    else:
        db.add(SmsTemplate(skeleton=body.skeleton, decision=body.decision, mode=body.mode))
    await db.commit()
    return {"saved": body.skeleton, **(await _reprocess(db, body.skeleton))}


@router.get("/decided")
async def decided(db: AsyncSession = Depends(get_db)):
    ts = (await db.execute(select(SmsTemplate).order_by(SmsTemplate.id.desc()))).scalars().all()
    return [{"id": t.id, "skeleton": t.skeleton, "decision": t.decision, "mode": t.mode} for t in ts]


@router.delete("/{template_id}")
async def undo(template_id: int, db: AsyncSession = Depends(get_db)):
    t = await db.get(SmsTemplate, template_id)
    if not t:
        raise HTTPException(404, "Not found")
    key = t.skeleton
    await db.delete(t)
    await db.commit()
    return {"removed": key, **(await _reprocess(db, key))}
