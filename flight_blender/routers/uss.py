"""
FastAPI router for USS interoperability operations.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
from flight_blender.database import get_db
from flight_blender.schemas.uss import (
    ConstraintDetailsResponse,
    OperationalIntentDetailsResponse,
    OperationalIntentDetailsUpdate,
    TelemetryUpdate,
    USSFlightDetailResponse,
    USSFlightResponse,
    USSReportCreate,
)

router = APIRouter()


# ── Reports ────────────────────────────────────────────────────────────────────


@router.post("/v1/reports", status_code=status.HTTP_201_CREATED, dependencies=[WriteDep])
async def submit_uss_report(payload: USSReportCreate):
    return {"message": "Report received"}


# ── Operational Intents ────────────────────────────────────────────────────────


@router.get("/v1/operational_intents/{intent_id}", response_model=OperationalIntentDetailsResponse, dependencies=[ReadDep])
async def get_operational_intent(intent_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    from flight_blender.models.flight_declaration import FlightDeclaration

    obj = await db.get(FlightDeclaration, intent_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operational intent not found")
    return OperationalIntentDetailsResponse(operational_intent_id=intent_id, details={"state": obj.state})


@router.put("/v1/operational_intents/{intent_id}", response_model=OperationalIntentDetailsResponse, dependencies=[WriteDep])
async def update_operational_intent(
    payload: OperationalIntentDetailsUpdate,
    intent_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    from flight_blender.models.flight_declaration import FlightDeclaration

    obj = await db.get(FlightDeclaration, intent_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operational intent not found")
    return OperationalIntentDetailsResponse(operational_intent_id=intent_id, details=payload.operational_intent)


@router.post("/v1/operational_intents/{intent_id}/telemetry", status_code=status.HTTP_204_NO_CONTENT, dependencies=[WriteDep])
async def submit_operational_intent_telemetry(payload: TelemetryUpdate, intent_id: uuid.UUID = Path(...)):
    pass


@router.post("/v1/operational_intents", status_code=status.HTTP_200_OK, dependencies=[WriteDep])
async def notify_operational_intent_change(payload: OperationalIntentDetailsUpdate):
    return {"message": "Operational intent change acknowledged"}


# ── Constraints ────────────────────────────────────────────────────────────────


@router.get("/v1/constraints/{constraint_id}", response_model=ConstraintDetailsResponse, dependencies=[ReadDep])
async def get_constraint(constraint_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    from flight_blender.models.constraint import ConstraintReference

    obj = await db.get(ConstraintReference, constraint_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Constraint not found")
    return ConstraintDetailsResponse(constraint_id=constraint_id, details={"uss_availability": obj.uss_availability})


@router.put("/v1/constraints/{constraint_id}", response_model=ConstraintDetailsResponse, dependencies=[WriteDep])
async def update_constraint(constraint_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    return ConstraintDetailsResponse(constraint_id=constraint_id, details={})


@router.post("/v1/constraints", status_code=status.HTTP_200_OK, dependencies=[WriteDep])
async def notify_constraint_change(payload: dict):
    return {"message": "Constraint change acknowledged"}


# ── Flights ────────────────────────────────────────────────────────────────────


@router.get("/flights", response_model=USSFlightResponse, dependencies=[ReadDep])
async def get_all_flights(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import select
    from flight_blender.models.flight_declaration import FlightDeclaration

    result = await db.execute(select(FlightDeclaration).where(FlightDeclaration.state.in_([1, 2])).limit(100))
    flights = [{"id": str(f.id), "state": f.state} for f in result.scalars().all()]
    return USSFlightResponse(flights=flights)


@router.get("/flights/{flight_id}/details", response_model=USSFlightDetailResponse, dependencies=[ReadDep])
async def get_flight_details(flight_id: str = Path(...), db: AsyncSession = Depends(get_db)):
    return USSFlightDetailResponse(id=flight_id, details={})
