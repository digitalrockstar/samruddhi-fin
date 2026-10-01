from fastapi import APIRouter

from app.api import (
    accounts,
    categories,
    dashboard,
    mappings,
    persons,
    raw,
    transactions,
    webhook,
)

api_router = APIRouter()

# Each router already declares its own prefix (/api/... or /webhook/...)
api_router.include_router(transactions.router)
api_router.include_router(categories.router)
api_router.include_router(accounts.router)
api_router.include_router(persons.router)
api_router.include_router(mappings.router)
api_router.include_router(raw.router)
api_router.include_router(dashboard.router)
api_router.include_router(webhook.router)

__all__ = ["api_router"]