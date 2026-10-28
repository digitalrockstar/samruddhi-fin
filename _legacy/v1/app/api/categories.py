from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Category, Transaction
from app.schemas import (
    CategoryCreate,
    CategoryResponse,
    CategoryType,
    CategoryUpdate,
)

router = APIRouter(prefix="/api/categories", tags=["categories"])


def serialize(c: Category, children: Optional[List[Category]] = None) -> CategoryResponse:
    return CategoryResponse(
        id=c.id,
        name=c.name,
        type=c.type,
        parent_id=c.parent_id,
        icon=c.icon,
        is_system=bool(c.is_system),
        display_order=c.display_order or 0,
        created_at=c.created_at,
        children=[serialize(ch) for ch in (children or [])],
    )


def build_tree(categories: List[Category]) -> List[CategoryResponse]:
    by_parent: dict[Optional[int], List[Category]] = {}
    for c in categories:
        by_parent.setdefault(c.parent_id, []).append(c)

    def walk(parent_id):
        nodes = sorted(by_parent.get(parent_id, []),
                       key=lambda x: (x.display_order or 0, x.name))
        return [serialize(c, walk(c.id)) for c in nodes]

    return walk(None)


@router.get("/tree", response_model=List[CategoryResponse])
async def get_tree(db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(select(Category))).scalars().all()
    return build_tree(rows)


@router.get("", response_model=List[CategoryResponse])
async def list_roots(
    type: Optional[CategoryType] = None,
    parent_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Category)
    if type:
        stmt = stmt.where(Category.type == type)
    if parent_id is None:
        stmt = stmt.where(Category.parent_id.is_(None))
    else:
        stmt = stmt.where(Category.parent_id == parent_id)
    stmt = stmt.order_by(Category.display_order, Category.name)
    rows = (await db.execute(stmt)).scalars().all()

    out = []
    for c in rows:
        kids = (
            await db.execute(
                select(Category)
                .where(Category.parent_id == c.id)
                .order_by(Category.display_order, Category.name)
            )
        ).scalars().all()
        out.append(serialize(c, kids))
    return out


@router.post("", response_model=CategoryResponse, status_code=201)
async def create_category(body: CategoryCreate, db: AsyncSession = Depends(get_db)):
    parent = None
    if body.parent_id:
        parent = await db.get(Category, body.parent_id)
        if not parent:
            raise HTTPException(400, "Parent category not found")

    # A child always inherits its parent's type
    category_type = parent.type if parent else body.type

    dup = (
        await db.execute(
            select(Category).where(
                Category.name == body.name, Category.parent_id == body.parent_id
            )
        )
    ).scalar_one_or_none()
    if dup:
        raise HTTPException(400, f"'{body.name}' already exists under this parent")

    cat = Category(
        name=body.name,
        type=category_type,
        parent_id=body.parent_id,
        icon=body.icon,
        is_system=False,
        display_order=body.display_order,
    )
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return serialize(cat)


@router.patch("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: int, body: CategoryUpdate, db: AsyncSession = Depends(get_db)
):
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(404, "Category not found")
    if cat.is_system and body.type:
        raise HTTPException(400, "Cannot change type of a system category")

    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(cat, field, value)

    await db.commit()
    await db.refresh(cat)
    return serialize(cat)


@router.delete("/{category_id}")
async def delete_category(category_id: int, db: AsyncSession = Depends(get_db)):
    cat = await db.get(Category, category_id)
    if not cat:
        raise HTTPException(404, "Category not found")
    if cat.is_system:
        raise HTTPException(400, "System categories cannot be deleted")

    kids = (
        await db.execute(select(func.count(Category.id)).where(Category.parent_id == cat.id))
    ).scalar_one()
    if kids:
        raise HTTPException(400, "Delete or move its subcategories first")

    used = (
        await db.execute(
            select(func.count(Transaction.id)).where(Transaction.category_id == cat.id)
        )
    ).scalar_one()
    if used:
        raise HTTPException(400, f"Category is used by {used} transaction(s)")

    await db.delete(cat)
    await db.commit()
    return {"deleted": True}


@router.post("/reorder")
async def reorder(payload: dict, db: AsyncSession = Depends(get_db)):
    """payload: {"parent_id": null|int, "order": [category_id, ...]}"""
    parent_id = payload.get("parent_id")
    order = payload.get("order") or []
    for idx, cid in enumerate(order):
        cat = await db.get(Category, int(cid))
        if cat:
            cat.display_order = idx
            cat.parent_id = parent_id
    await db.commit()
    return {"ok": True}