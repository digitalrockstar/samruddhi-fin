from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Account, Person, Transaction
from app.schemas import AccountCreate, AccountResponse, AccountUpdate

router = APIRouter(prefix="/api/accounts", tags=["accounts"])


@router.get("", response_model=List[AccountResponse])
async def list_accounts(
    person_id: Optional[int] = None,
    is_active: Optional[bool] = True,
    bank_name: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Account).options(selectinload(Account.person))
    if person_id:
        stmt = stmt.where(Account.person_id == person_id)
    if is_active is not None:
        stmt = stmt.where(Account.is_active.is_(is_active))
    if bank_name:
        stmt = stmt.where(func.lower(Account.bank_name) == bank_name.lower())
    stmt = stmt.order_by(Account.bank_name, Account.nickname)

    rows = (await db.execute(stmt)).scalars().all()
    return [AccountResponse.model_validate(a) for a in rows]


@router.post("", response_model=AccountResponse, status_code=201)
async def create_account(body: AccountCreate, db: AsyncSession = Depends(get_db)):
    if not await db.get(Person, body.person_id):
        raise HTTPException(400, "Person not found")

    if body.last_four:
        clash = (
            await db.execute(
                select(Account).where(
                    Account.person_id == body.person_id,
                    Account.last_four == body.last_four,
                    Account.is_active.is_(True),
                )
            )
        ).scalar_one_or_none()
        if clash:
            raise HTTPException(400, "An active account with these last 4 digits already exists")

    account = Account(**body.model_dump())
    db.add(account)
    await db.commit()
    await db.refresh(account)
    return AccountResponse.model_validate(account)


@router.patch("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int, body: AccountUpdate, db: AsyncSession = Depends(get_db)
):
    account = await db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    await db.commit()
    await db.refresh(account)
    return AccountResponse.model_validate(account)


@router.delete("/{account_id}")
async def delete_account(account_id: int, db: AsyncSession = Depends(get_db)):
    """Soft delete so historical transactions keep their link."""
    account = await db.get(Account, account_id)
    if not account:
        raise HTTPException(404, "Account not found")

    used = (
        await db.execute(
            select(func.count(Transaction.id)).where(Transaction.account_id == account_id)
        )
    ).scalar_one()
    if used:
        account.is_active = False
        await db.commit()
        return {"deleted": True, "deactivated": True, "transactions": used}

    await db.delete(account)
    await db.commit()
    return {"deleted": True, "deactivated": False}