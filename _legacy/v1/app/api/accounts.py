from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models import Account, AccountType, Person, Transaction
from app.schemas import AccountCreate, AccountResponse, AccountUpdate

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

INVESTMENT_TYPES = {AccountType.MUTUAL_FUND, AccountType.FIXED_DEPOSIT,
                    AccountType.STOCKS, AccountType.OTHER_INVESTMENT}
BANK_TYPES = {AccountType.SAVINGS, AccountType.CURRENT, AccountType.WALLET, AccountType.UPI}


def _clean_digits(values: dict) -> dict:
    """last_six is kept as typed digits; last_four (used for SMS matching) follows it."""
    six = values.get("last_six")
    if six:
        digits = "".join(ch for ch in six if ch.isdigit())
        if len(digits) != 6:
            raise HTTPException(400, "last_six must be exactly 6 digits")
        values["last_six"] = digits
        if not values.get("last_four"):
            values["last_four"] = digits[-4:]
    return values


def _changed(values: dict, key: str, existing) -> bool:
    if key not in values:
        return False
    if existing is None:
        return values[key] is not None
    return values[key] != getattr(existing, key)


def _stamp(values: dict, existing: Optional[Account] = None) -> dict:
    """Stamp 'as of' only when a manual balance or valuation really changed."""
    now = datetime.now(timezone.utc)
    if _changed(values, "balance", existing):
        values["balance_as_of"] = now
    elif "balance_as_of" in values:
        values.pop("balance_as_of")           # never let a stale echo from the UI overwrite it
    if _changed(values, "current_value", existing) or _changed(values, "invested_amount", existing):
        values["valuation_as_of"] = now
    elif "valuation_as_of" in values:
        values.pop("valuation_as_of")
    return values


def _aware(dt):
    return dt if dt is None or dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@router.get("/summary")
async def accounts_summary(db: AsyncSession = Depends(get_db)):
    """Current position per account plus household totals.

    Balance rule: a manual `balance` checkpoint is used until an SMS reports a newer
    balance for that account; then the SMS figure wins. Cards report the AVAILABLE
    limit in SMS, so outstanding = credit_limit - available (needs credit_limit).
    """
    accounts = (await db.execute(
        select(Account).where(Account.is_active.is_(True)).options(selectinload(Account.person))
    )).scalars().all()

    latest = {}
    rows = (await db.execute(
        select(Transaction.account_id, Transaction.balance_after, Transaction.txn_timestamp)
        .where(Transaction.account_id.is_not(None), Transaction.balance_after.is_not(None))
        .order_by(Transaction.txn_timestamp.asc(), Transaction.id.asc())
    )).all()
    for acc_id, bal, ts in rows:
        latest[acc_id] = (bal, ts)

    out, assets, liabilities, invested, current = [], Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0")
    for a in accounts:
        item = {"id": a.id, "nickname": a.nickname, "bank_name": a.bank_name,
                "account_type": a.account_type.value, "holder": a.person.name if a.person else None,
                "last_four": a.last_four, "last_six": a.last_six,
                "balance": None, "balance_source": None, "balance_as_of": None,
                "available_limit": None, "credit_limit": a.credit_limit,
                "invested_amount": a.invested_amount, "current_value": a.current_value,
                "valuation_as_of": a.valuation_as_of}
        sms = latest.get(a.id)
        sms_newer = sms and (a.balance is None or a.balance_as_of is None
                             or _aware(sms[1]) > _aware(a.balance_as_of))
        if a.account_type == AccountType.CREDIT_CARD:
            if sms_newer and a.credit_limit is not None:
                item.update(balance=a.credit_limit - sms[0], balance_source="sms",
                            balance_as_of=sms[1], available_limit=sms[0])
            elif a.balance is not None:
                item.update(balance=a.balance, balance_source="manual", balance_as_of=a.balance_as_of)
                if a.credit_limit is not None:
                    item["available_limit"] = a.credit_limit - a.balance
            elif sms_newer:
                item.update(available_limit=sms[0], balance_source="sms_limit_only", balance_as_of=sms[1])
            if item["balance"] is not None:
                liabilities += item["balance"]
        elif a.account_type in BANK_TYPES | {AccountType.DEBIT_CARD}:
            if sms_newer:
                item.update(balance=sms[0], balance_source="sms", balance_as_of=sms[1])
            elif a.balance is not None:
                item.update(balance=a.balance, balance_source="manual", balance_as_of=a.balance_as_of)
            # A debit card reports its linked savings balance; counting it would double count.
            if item["balance"] is not None and a.account_type in BANK_TYPES:
                assets += item["balance"]
        elif a.account_type == AccountType.LOAN:
            if a.balance is not None:
                item.update(balance=a.balance, balance_source="manual", balance_as_of=a.balance_as_of)
                liabilities += a.balance
        elif a.account_type in INVESTMENT_TYPES:
            if a.current_value is not None:
                assets += a.current_value
                current += a.current_value
            if a.invested_amount is not None:
                invested += a.invested_amount
        out.append(item)

    gain = current - invested if (invested or current) else None
    return {
        "accounts": out,
        "totals": {"assets": assets, "liabilities": liabilities, "net_worth": assets - liabilities,
                   "invested": invested, "investment_value": current, "investment_gain": gain},
    }



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

    values = _clean_digits(body.model_dump(exclude_unset=True))
    if values.get("last_four"):
        clash = (
            await db.execute(
                select(Account).where(
                    Account.person_id == body.person_id,
                    Account.last_four == values["last_four"],
                    Account.is_active.is_(True),
                )
            )
        ).scalars().first()
        if clash:
            raise HTTPException(400, "An active account with these last 4 digits already exists")

    account = Account(**_stamp(values))
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
    changes = _stamp(_clean_digits(body.model_dump(exclude_unset=True)), account)
    for field, value in changes.items():
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