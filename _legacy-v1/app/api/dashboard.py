from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.dashboard import DashboardService

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

PERIOD = Query("month", pattern="^(day|week|month|year|all)$")


@router.get("/summary")
async def summary(
    period: str = PERIOD,
    person_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    return await DashboardService(db).get_summary(period, person_id)


@router.get("/averages")
async def averages(
    period: str = PERIOD,
    person_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    return await DashboardService(db).get_averages(period, person_id)


@router.get("/trend")
async def trend(
    grain: str = Query("day", pattern="^(day|week|month)$"),
    buckets: int = Query(30, ge=1, le=120),
    person_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    data = await DashboardService(db).get_trend(grain, buckets, person_id)
    return {"grain": grain, "data": data}


@router.get("/cashback")
async def cashback(
    months: int = Query(6, ge=1, le=36),
    db: AsyncSession = Depends(get_db),
):
    svc = DashboardService(db)
    return {
        "totals": await svc.get_cashback_totals(months),
        "rows": await svc.get_cashback(months),
    }


@router.get("/recurring")
async def recurring(
    days_ahead: int = Query(30, ge=1, le=180),
    db: AsyncSession = Depends(get_db),
):
    return {"recurring": await DashboardService(db).get_recurring(days_ahead)}