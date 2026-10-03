from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Person
from app.schemas import PersonCreate, PersonResponse, PersonUpdate

router = APIRouter(prefix="/api/persons", tags=["persons"])


@router.get("", response_model=List[PersonResponse])
async def list_persons(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.execute(select(Person).order_by(Person.display_order, Person.name))
    ).scalars().all()
    return [PersonResponse.model_validate(p) for p in rows]


@router.post("", response_model=PersonResponse, status_code=201)
async def create_person(body: PersonCreate, db: AsyncSession = Depends(get_db)):
    if (await db.execute(select(Person).where(Person.prefix == body.prefix))).scalar_one_or_none():
        raise HTTPException(400, "Prefix already in use")
    person = Person(**body.model_dump())
    db.add(person)
    await db.commit()
    await db.refresh(person)
    return PersonResponse.model_validate(person)


@router.patch("/{person_id}", response_model=PersonResponse)
async def update_person(
    person_id: int, body: PersonUpdate, db: AsyncSession = Depends(get_db)
):
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(person, field, value)
    await db.commit()
    await db.refresh(person)
    return PersonResponse.model_validate(person)


@router.delete("/{person_id}")
async def delete_person(person_id: int, db: AsyncSession = Depends(get_db)):
    person = await db.get(Person, person_id)
    if not person:
        raise HTTPException(404, "Person not found")
    await db.delete(person)
    await db.commit()
    return {"deleted": True}