from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.database import get_db
from app.models import MerchantMapping
from app.schemas import MerchantMappingCreate, MerchantMappingResponse

router = APIRouter(prefix="/api/mappings", tags=["mappings"])


@router.get("", response_model=List[MerchantMappingResponse])
async def list_mappings(db: AsyncSession = Depends(get_db)):
    stmt = select(MerchantMapping).where(
        MerchantMapping.is_active == True
    ).order_by(MerchantMapping.last_used_at.desc().nullslast(), MerchantMapping.pattern)
    result = await db.execute(stmt)
    return [
        MerchantMappingResponse(
            id=m.id,
            pattern=m.pattern,
            category_id=m.category_id,
            confidence=m.confidence,
            is_active=m.is_active,
            created_at=m.created_at,
            updated_at=m.updated_at,
            last_used_at=m.last_used_at
        )
        for m in result.scalars().all()
    ]


@router.post("", response_model=MerchantMappingResponse)
async def create_mapping(mapping: MerchantMappingCreate, db: AsyncSession = Depends(get_db)):
    stmt = select(MerchantMapping).where(MerchantMapping.pattern == mapping.pattern)
    existing = (await db.execute(stmt)).scalar_one_or_none()

    if existing:
        existing.category_id = mapping.category_id
        existing.confidence = mapping.confidence
        existing.is_active = True
        obj = existing
    else:
        obj = MerchantMapping(**mapping.model_dump())
        db.add(obj)

    await db.commit()
    await db.refresh(obj)

    return MerchantMappingResponse(
        id=obj.id,
        pattern=obj.pattern,
        category_id=obj.category_id,
        confidence=obj.confidence,
        is_active=obj.is_active,
        created_at=obj.created_at,
        updated_at=obj.updated_at,
        last_used_at=obj.last_used_at
    )


@router.delete("/{mapping_id}")
async def delete_mapping(mapping_id: int, db: AsyncSession = Depends(get_db)):
    stmt = select(MerchantMapping).where(MerchantMapping.id == mapping_id)
    m = (await db.execute(stmt)).scalar_one_or_none()
    if not m:
        raise HTTPException(404, "Mapping not found")
    m.is_active = False
    await db.commit()
    return {"deleted": True}