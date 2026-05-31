"""
FastAPI router for SCD (Strategic Conflict Detection) operations.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
from flight_blender.config import get_settings
from flight_blender.database import get_db
from flight_blender.schemas.scd import (
    ClearAreaRequest,
    ClearAreaResponse,
    FlightPlanResponse,
    FlightPlanUpsertRequest,
    SCDCapabilitiesResponse,
    SCDStatusResponse,
)

router = APIRouter()


# ── SCD v1 ─────────────────────────────────────────────────────────────────────


@router.get("/v1/status", response_model=SCDStatusResponse, dependencies=[ReadDep])
async def scd_status():
    return SCDStatusResponse(status="operational")


@router.get("/v1/capabilities", response_model=SCDCapabilitiesResponse, dependencies=[ReadDep])
async def scd_capabilities():
    return SCDCapabilitiesResponse(capabilities=["BasicStrategicConflictDetection", "HighPriorityFlights"])


# ── Flight planning ─────────────────────────────────────────────────────────────


@router.put("/flight_planning/flight_plans/{flight_plan_id}", response_model=FlightPlanResponse, dependencies=[WriteDep])
async def upsert_flight_plan(
    payload: FlightPlanUpsertRequest,
    flight_plan_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    if settings.ussp_network_enabled:
        raise HTTPException(status_code=503, detail="DSS integration not yet implemented in FastAPI port")
    return FlightPlanResponse(flight_plan_id=flight_plan_id, planning_result="NotPlanned", notes="USSP network not enabled — DSS integration required for flight planning")


@router.delete("/flight_planning/flight_plans/{flight_plan_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[WriteDep])
async def delete_flight_plan(flight_plan_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    pass


@router.post("/flight_planning/clear_area_requests", response_model=ClearAreaResponse, dependencies=[WriteDep])
async def clear_area(payload: ClearAreaRequest):
    settings = get_settings()
    if settings.ussp_network_enabled:
        raise HTTPException(status_code=503, detail="DSS integration not yet implemented in FastAPI port")
    return ClearAreaResponse(outcome={"success": False, "message": "USSP network not enabled"})


@router.get("/flight_planning/status", response_model=SCDStatusResponse, dependencies=[ReadDep])
async def flight_planning_status():
    return SCDStatusResponse(status="operational")


# ── U-Space variants ────────────────────────────────────────────────────────────


@router.put("/flight_planning/u_space/flight_plans/{flight_plan_id}", response_model=FlightPlanResponse, dependencies=[WriteDep])
async def upsert_uspace_flight_plan(
    payload: FlightPlanUpsertRequest,
    flight_plan_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    if settings.ussp_network_enabled:
        raise HTTPException(status_code=503, detail="DSS integration not yet implemented in FastAPI port")
    return FlightPlanResponse(flight_plan_id=flight_plan_id, planning_result="NotPlanned", notes="USSP network not enabled — DSS integration required for flight planning")


@router.delete("/flight_planning/u_space/flight_plans/{flight_plan_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[WriteDep])
async def delete_uspace_flight_plan(flight_plan_id: uuid.UUID = Path(...)):
    pass


@router.post("/flight_planning/u_space/clear_area_requests", response_model=ClearAreaResponse, dependencies=[WriteDep])
async def clear_uspace_area(payload: ClearAreaRequest):
    settings = get_settings()
    if settings.ussp_network_enabled:
        raise HTTPException(status_code=503, detail="DSS integration not yet implemented in FastAPI port")
    return ClearAreaResponse(outcome={"success": False, "message": "USSP network not enabled"})


@router.get("/flight_planning/u_space/status", response_model=SCDStatusResponse, dependencies=[ReadDep])
async def uspace_status():
    return SCDStatusResponse(status="operational")
