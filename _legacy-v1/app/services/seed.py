"""Idempotent seeding of persons and the category tree."""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_maker
from app.models import Category, Person

PERSONS = [
    {"name": "Akshay Patel", "prefix": "AP-", "display_order": 1},
    {"name": "Ashka Shah", "prefix": "AS-", "display_order": 2},
]

ROOTS = [
    {"name": "EXPENSE", "type": "expense", "icon": "\U0001F4B8", "display_order": 1},
    {"name": "INCOME", "type": "income", "icon": "\U0001F4B0", "display_order": 2},
    {"name": "TRANSFER", "type": "transfer", "icon": "\U0001F504", "display_order": 3},
    {"name": "IGNORE", "type": "ignore", "icon": "\U0001F6AB", "display_order": 4},
]

SUBCATEGORIES = {
    "EXPENSE": [
        ("Food & Dining", "\U0001F354", 1),
        ("Transport", "\U0001F68C", 2),
        ("Shopping", "\U0001F6CD\uFE0F", 3),
        ("Bills & Utilities", "\U0001F4C4", 4),
        ("Health & Wellness", "\U0001F3E5", 5),
        ("Entertainment", "\U0001F3AE", 6),
        ("Education", "\U0001F4DA", 7),
        ("Personal Care", "\U0001F487", 8),
        ("Home & Maintenance", "\U0001F3E0", 9),
        ("Cash & ATM", "\U0001F4B8", 14),
        ("Travel", "\u2708\uFE0F", 10),
        ("Gifts & Donations", "\U0001F381", 11),
        ("Investments", "\U0001F4C8", 12),
        ("Other Expense", "\U0001F4E6", 13),
    ],
    "INCOME": [
        ("Salary", "\U0001F4BC", 1),
        ("Freelance / Business", "\U0001F4BB", 2),
        ("Interest / Dividends", "\U0001F4CA", 3),
        ("Refunds", "\u21A9\uFE0F", 4),
        ("Cashback Received", "\U0001F4B5", 5),
        ("Gifts Received", "\U0001F381", 6),
        ("Other Income", "\U0001F4E6", 7),
    ],
    "TRANSFER": [
        ("Credit Card Payment", "\U0001F4B3", 1),
        ("Account Transfer", "\U0001F3E6", 2),
        ("Wallet Top-up", "\U0001F4AB", 3),
        ("Loan Repayment", "\U0001F4DD", 4),
        ("Investment Funding", "\U0001F4C8", 5),
    ],
    "IGNORE": [
        ("Spam", "\U0001F5D1\uFE0F", 1),
        ("OTP / Verification", "\U0001F510", 2),
        ("Promotional", "\U0001F4E2", 3),
        ("System Alerts", "\u26A0\uFE0F", 4),
    ],
}


async def seed_persons(db: AsyncSession) -> None:
    for p in PERSONS:
        existing = (
            await db.execute(select(Person).where(Person.prefix == p["prefix"]))
        ).scalar_one_or_none()
        if not existing:
            db.add(Person(**p))


async def seed_categories(db: AsyncSession) -> None:
    root_ids: dict[str, int] = {}

    for r in ROOTS:
        found = (
            await db.execute(
                select(Category).where(
                    Category.name == r["name"], Category.parent_id.is_(None)
                )
            )
        ).scalar_one_or_none()
        if not found:
            found = Category(**r, is_system=True)
            db.add(found)
            await db.flush()
        root_ids[r["name"]] = found.id

    for root_name, children in SUBCATEGORIES.items():
        parent_id = root_ids.get(root_name)
        root_type = next(r["type"] for r in ROOTS if r["name"] == root_name)
        if not parent_id:
            continue
        for name, icon, order in children:
            found = (
                await db.execute(
                    select(Category).where(
                        Category.name == name, Category.parent_id == parent_id
                    )
                )
            ).scalar_one_or_none()
            if not found:
                db.add(Category(
                    name=name,
                    type=root_type,
                    parent_id=parent_id,
                    icon=icon,
                    display_order=order,
                    is_system=True,
                ))


async def seed_all() -> None:
    async with async_session_maker() as db:
        try:
            await seed_persons(db)
            await seed_categories(db)
            await db.commit()
        except Exception:
            await db.rollback()
            raise