"""
FastAPI router for conformance monitoring operations.
"""

from fastapi import APIRouter, Body, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
from flight_blender.database import get_db
from flight_blender.models.conformance import ConformanceRecord
from flight_blender.models.flight_declaration import FlightDeclaration
from flight_blender.schemas.conformance import (
    ConformanceRecordResponse,
    ConformanceStatusResponse,
    ConformanceSummaryResponse,
)
from flight_blender.services.state_machine import get_valid_transitions, is_valid_transition


class StateTransitionRequest(BaseModel):
    flight_declaration_id: str
    event: str

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


@router.post("/manage_operation_state_transition", dependencies=[WriteDep])
async def manage_operation_state_transition(
    body: StateTransitionRequest = Body(...),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(FlightDeclaration).where(FlightDeclaration.id == body.flight_declaration_id))
    flight_declaration = result.scalar_one_or_none()
    if flight_declaration is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flight declaration not found")

    current_state = flight_declaration.state
    valid, new_state = is_valid_transition(current_state, body.event)
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid state transition", "current_state": current_state, "event": body.event},
        )

    flight_declaration.state = new_state
    await db.flush()
    return {
        "id": body.flight_declaration_id,
        "event": body.event,
        "previous_state": current_state,
        "new_state": new_state,
        "message": "State transition applied",
    }


@router.get("/operation_state_transitions", dependencies=[ReadDep])
async def operation_state_transitions():
    return get_valid_transitions()
