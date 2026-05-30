"""
FastAPI router for conformance monitoring operations.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep
from flight_blender.database import get_db
from flight_blender.models.conformance import ConformanceRecord
from flight_blender.schemas.conformance import (
    ConformanceRecordResponse,
    ConformanceStatusResponse,
    ConformanceSummaryResponse,
)

router = APIRouter()


@router.get("/conformance_record_summary", response_model=ConformanceSummaryResponse, dependencies=[ReadDep])
async def get_conformance_summary(db: AsyncSession = Depends(get_db)):
    total_result = await db.execute(select(func.count()).select_from(ConformanceRecord))
    total = total_result.scalar_one()

    conforming_result = await db.execute(select(func.count()).select_from(ConformanceRecord).where(ConformanceRecord.conformance_state == 1))
    conforming = conforming_result.scalar_one()

    non_conforming = total - conforming
    rate = (conforming / total * 100) if total > 0 else 100.0

    min_date_result = await db.execute(select(func.min(ConformanceRecord.created_at)))
    max_date_result = await db.execute(select(func.max(ConformanceRecord.created_at)))

    return ConformanceSummaryResponse(
        total_records=total,
        conforming_records=conforming,
        non_conforming_records=non_conforming,
        conformance_rate_percent=rate,
        start_date=min_date_result.scalar_one(),
        end_date=max_date_result.scalar_one(),
    )


@router.get("/conformance_status", response_model=ConformanceStatusResponse, dependencies=[ReadDep])
async def get_conformance_status(db: AsyncSession = Depends(get_db)):
    active_nc_result = await db.execute(
        select(func.count()).select_from(ConformanceRecord).where(ConformanceRecord.conformance_state == 0, ConformanceRecord.resolved == False)  # noqa: E712
    )
    active_nc = active_nc_result.scalar_one()

    last_result = await db.execute(select(func.max(ConformanceRecord.created_at)))
    last_checked = last_result.scalar_one()

    return ConformanceStatusResponse(
        is_conforming=active_nc == 0,
        active_nonconforming_count=active_nc,
        last_checked=last_checked,
    )


@router.get("/get_conformance_records", response_model=list[ConformanceRecordResponse], dependencies=[ReadDep])
async def get_conformance_records(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ConformanceRecord).order_by(ConformanceRecord.created_at.desc()).limit(100))
    return result.scalars().all()
