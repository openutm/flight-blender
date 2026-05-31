"""
FastAPI router for UTM adapter (consolidated endpoints).
"""

import uuid

from fastapi import APIRouter, Depends, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
from flight_blender.database import get_db
from flight_blender.models.flight_declaration import FlightDeclaration
from flight_blender.schemas.flight_declaration import (
    FlightDeclarationCreate,
    FlightDeclarationListResponse,
    FlightDeclarationResponse,
    FlightDeclarationStateUpdate,
)

router = APIRouter()


@router.get("/ping", dependencies=[ReadDep])
async def utm_ping():
    return {"message": "pong"}


@router.get("/network_remote_id/capabilities", dependencies=[ReadDep])
async def network_rid_capabilities():
    return {"capabilities": ["ASTM_F3411_22a"]}


@router.post("/network_remote_id/set_telemetry", dependencies=[WriteDep])
async def network_rid_set_telemetry(payload: dict):
    from flight_blender.tasks.flight_feed import write_incoming_air_traffic_data

    write_incoming_air_traffic_data.delay(payload)
    return {"message": "Telemetry queued"}


@router.get("/network_remote_id/uss/flights/{flight_id}/details", dependencies=[ReadDep])
async def network_rid_flight_details(flight_id: str = Path(...)):
    return {"id": flight_id, "details": {}}


@router.get("/network_remote_id/uss/flights", dependencies=[ReadDep])
async def network_rid_flights(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(FlightDeclaration).where(FlightDeclaration.state.in_([1, 2])).limit(100))
    return {"flights": [str(f.id) for f in result.scalars().all()]}


@router.get("/flight_declaration", response_model=FlightDeclarationListResponse, dependencies=[ReadDep])
async def utm_list_flight_declarations(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func

    count_result = await db.execute(select(func.count()).select_from(FlightDeclaration))
    total = count_result.scalar_one()
    result = await db.execute(select(FlightDeclaration).order_by(FlightDeclaration.created_at.desc()).limit(100))
    return FlightDeclarationListResponse(count=total, results=result.scalars().all())


@router.post("/flight_declaration", response_model=FlightDeclarationResponse, dependencies=[WriteDep])
async def utm_create_flight_declaration(payload: FlightDeclarationCreate, db: AsyncSession = Depends(get_db)):
    decl = FlightDeclaration(**payload.model_dump())
    db.add(decl)
    await db.flush()
    await db.refresh(decl)
    return decl


@router.get("/flight_declaration/capabilities", dependencies=[ReadDep])
async def utm_flight_declaration_capabilities():
    return {"capabilities": ["FlightAuthorisationData", "BasicStrategicConflictDetection"]}


@router.put("/flight_declaration_state/{declaration_id}", dependencies=[WriteDep])
async def utm_update_declaration_state(
    payload: FlightDeclarationStateUpdate,
    declaration_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    from fastapi import HTTPException, status

    decl = await db.get(FlightDeclaration, declaration_id)
    if not decl:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Declaration not found")
    decl.state = payload.state
    await db.flush()
    return {"id": str(declaration_id), "state": decl.state}


@router.get("/traffic_information", dependencies=[ReadDep])
async def traffic_information_discovery():
    return {
        "message": "Flight Blender traffic information service",
        "url": "/flight_stream",
        "description": "Real-time air traffic information",
    }
