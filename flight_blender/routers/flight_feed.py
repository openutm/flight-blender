"""
FastAPI router for flight feed / air traffic operations.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
from flight_blender.common.redis_stream_operations import read_all_observations
from flight_blender.database import get_db
from flight_blender.models.flight_feed import FlightObservation, SignedTelemetryPublicKey
from flight_blender.schemas.flight_feed import (
    BulkObservationRequest,
    FlightObservationResponse,
    SignedTelemetryPublicKeyCreate,
    SignedTelemetryPublicKeyResponse,
    SignedTelemetryPublicKeyUpdate,
    SingleObservation,
    TelemetryObservation,
)
from flight_blender.tasks.flight_feed import bulk_write_incoming_air_traffic_data, write_incoming_air_traffic_data

router = APIRouter()


# ── Public Keys ────────────────────────────────────────────────────────────────


@router.get("/public_keys/", response_model=list[SignedTelemetryPublicKeyResponse], dependencies=[ReadDep])
async def list_public_keys(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SignedTelemetryPublicKey))
    return result.scalars().all()


@router.post("/public_keys/", response_model=SignedTelemetryPublicKeyResponse, status_code=status.HTTP_201_CREATED, dependencies=[WriteDep])
async def create_public_key(payload: SignedTelemetryPublicKeyCreate, db: AsyncSession = Depends(get_db)):
    key = SignedTelemetryPublicKey(**payload.model_dump())
    db.add(key)
    await db.flush()
    await db.refresh(key)
    return key


@router.get("/public_keys/{key_id}", response_model=SignedTelemetryPublicKeyResponse, dependencies=[ReadDep])
async def get_public_key(key_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    obj = await db.get(SignedTelemetryPublicKey, key_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Public key not found")
    return obj


@router.put("/public_keys/{key_id}", response_model=SignedTelemetryPublicKeyResponse, dependencies=[WriteDep])
async def update_public_key(payload: SignedTelemetryPublicKeyUpdate, key_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    obj = await db.get(SignedTelemetryPublicKey, key_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Public key not found")
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(obj, field, value)
    await db.flush()
    await db.refresh(obj)
    return obj


@router.delete("/public_keys/{key_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[WriteDep])
async def delete_public_key(key_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    obj = await db.get(SignedTelemetryPublicKey, key_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Public key not found")
    await db.delete(obj)


# ── Air Traffic Observations ───────────────────────────────────────────────────


@router.post("/set_air_traffic/{session_id}", dependencies=[WriteDep])
async def set_air_traffic(observation: SingleObservation, session_id: uuid.UUID = Path(...)):
    """Ingest a single air traffic observation via Celery task."""
    task_payload = {**observation.model_dump(), "session_id": str(session_id)}
    write_incoming_air_traffic_data.delay(task_payload)
    return {"message": "Observation queued for processing"}


@router.post("/bulk_set_air_traffic/{session_id}", dependencies=[WriteDep])
async def bulk_set_air_traffic(payload: BulkObservationRequest, session_id: uuid.UUID = Path(...)):
    """Ingest multiple air traffic observations via Celery task."""
    observations = [{**obs.model_dump(), "session_id": str(session_id)} for obs in payload.observations]
    bulk_write_incoming_air_traffic_data.delay(observations)
    return {"message": f"{len(observations)} observations queued for processing"}


@router.get("/get_air_traffic/{session_id}", response_model=list[FlightObservationResponse], dependencies=[ReadDep])
async def get_air_traffic(session_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    """Return observations for a given session from Redis stream."""
    read_all_observations(session_id=str(session_id))
    # Also query DB for persisted records
    result = await db.execute(
        select(FlightObservation).where(FlightObservation.session_id == session_id).order_by(FlightObservation.created_at.desc()).limit(500)
    )
    return result.scalars().all()


# ── Telemetry ──────────────────────────────────────────────────────────────────


@router.post("/set_telemetry", dependencies=[WriteDep])
async def set_telemetry(observation: SingleObservation):
    """Accept a raw telemetry observation and queue it."""
    write_incoming_air_traffic_data.delay(observation.model_dump())
    return {"message": "Telemetry queued"}


@router.post("/set_signed_telemetry", dependencies=[WriteDep])
async def set_signed_telemetry(payload: TelemetryObservation):
    """Accept a signed ASTM RID telemetry observation."""
    # Signature verification would happen here in production
    logger.info("Received signed telemetry observation")
    write_incoming_air_traffic_data.delay(payload.model_dump())
    return {"message": "Signed telemetry queued"}


@router.post("/start_opensky_feed", dependencies=[WriteDep])
async def start_opensky_feed():
    """Trigger the OpenSky Network polling task."""
    from flight_blender.tasks.flight_feed import start_opensky_network_stream

    start_opensky_network_stream.delay()
    return {"message": "OpenSky feed started"}
