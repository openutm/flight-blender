"""
FastAPI router for surveillance monitoring operations.
"""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
from flight_blender.common.redis_stream_operations import read_all_observations
from flight_blender.database import get_db
from flight_blender.models.surveillance import (
    SurveillanceHeartbeatEvent,
    SurveillanceSensor,
    SurveillanceSensorFailureNotification,
    SurveillanceSensorHealth,
    SurveillanceSensorHealthTracking,
    SurveillanceSession,
    SurveillanceTrackEvent,
)
from flight_blender.schemas.surveillance import (
    SensorFailureNotificationResponse,
    StartStopHeartbeatRequest,
    SurveillanceHealthResponse,
    SurveillanceSensorHealthResponse,
    SurveillanceSensorHealthUpdate,
    SurveillanceSensorResponse,
)
from flight_blender.tasks.surveillance import send_and_generate_track_to_consumer, send_heartbeat_to_consumer


from flight_blender.common.datetime_utils import parse_iso_utc as _parse_iso_dt


def _apply_time_window(query: Select, model: Any, start_dt: datetime | None, end_dt: datetime | None) -> Select:
    """Constrain a count query to an optional [start_dt, end_dt] time window."""
    if start_dt:
        query = query.where(model.created_at >= start_dt)
    if end_dt:
        query = query.where(model.created_at <= end_dt)
    return query


router = APIRouter()


@router.get("/health/", response_model=SurveillanceHealthResponse, dependencies=[ReadDep])
async def surveillance_health(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SurveillanceSensor).where(SurveillanceSensor.is_active == True))  # noqa: E712
    sensors = result.scalars().all()

    statuses = []
    for sensor in sensors:
        health = await db.execute(select(SurveillanceSensorHealth).where(SurveillanceSensorHealth.sensor_id == sensor.id))
        h = health.scalar_one_or_none()
        if h:
            statuses.append(h.status)

    if not statuses or all(s == "outage" for s in statuses):
        current_status = "outage"
    elif any(s in ("degraded", "outage") for s in statuses):
        current_status = "degraded"
    else:
        current_status = "operational"

    return SurveillanceHealthResponse(status=current_status, active_sessions=len(sensors), sensors=sensors)


@router.put("/start_stop_surveillance_heartbeat_track/{session_id}", dependencies=[WriteDep])
async def start_stop_heartbeat(
    payload: StartStopHeartbeatRequest,
    session_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    existing = await db.get(SurveillanceSession, session_id)

    if payload.action == "start":
        if existing:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Session already exists")
        session = SurveillanceSession(id=session_id)
        db.add(session)
        await db.flush()
        send_heartbeat_to_consumer.apply_async(kwargs={"session_id": str(session_id)}, countdown=1)
        send_and_generate_track_to_consumer.apply_async(kwargs={"session_id": str(session_id)}, countdown=1)
        return {"message": "Heartbeat started", "session_id": str(session_id)}
    else:
        if not existing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        await db.delete(existing)
        return {"message": "Heartbeat stopped", "session_id": str(session_id)}


@router.get("/get_air_traffic", dependencies=[ReadDep])
async def get_air_traffic(session_id: str | None = None):
    """Return the latest air-traffic observations for display.

    Mirrors the Django ``get_air_traffic`` GET view: it reads the most-recent
    observations from the Redis stream (optionally filtered by ``session_id``)
    and returns them under an ``observations`` key. Guarded by the READ scope.
    """
    observations = read_all_observations(session_id=session_id, count=500)
    return {"observations": observations}


@router.get("/list_surveillance_sensors", response_model=list[SurveillanceSensorResponse], dependencies=[ReadDep])
async def list_surveillance_sensors(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SurveillanceSensor))
    return result.scalars().all()


@router.get("/service_metrics", dependencies=[ReadDep])
async def get_service_metrics(
    session_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    start_dt = _parse_iso_dt(start_date)
    end_dt = _parse_iso_dt(end_date)

    # Heartbeat stats
    hb_base = select(func.count()).select_from(SurveillanceHeartbeatEvent)
    hb_on_time_base = hb_base.where(SurveillanceHeartbeatEvent.delivered_on_time == True)  # noqa: E712
    hb_total = (await db.execute(_apply_time_window(hb_base, SurveillanceHeartbeatEvent, start_dt, end_dt))).scalar_one() or 1
    hb_on_time = (await db.execute(_apply_time_window(hb_on_time_base, SurveillanceHeartbeatEvent, start_dt, end_dt))).scalar_one()

    # Track stats
    track_base = select(func.count()).select_from(SurveillanceTrackEvent)
    track_with_data_base = track_base.where(SurveillanceTrackEvent.had_active_tracks == True)  # noqa: E712
    t_total = (await db.execute(_apply_time_window(track_base, SurveillanceTrackEvent, start_dt, end_dt))).scalar_one() or 1
    t_with_data = (await db.execute(_apply_time_window(track_with_data_base, SurveillanceTrackEvent, start_dt, end_dt))).scalar_one()

    session_count = (await db.execute(select(func.count()).select_from(SurveillanceSession))).scalar_one()

    window_start = start_dt.isoformat() if start_dt else ""
    window_end = end_dt.isoformat() if end_dt else ""
    sid = session_id or ""

    return {
        "heartbeat_rates": [
            {
                "measured_rate_hz": 1.0,
                "target_rate_hz": 1.0,
                "session_id": sid,
                "window_start": window_start,
                "window_end": window_end,
                "total_heartbeats_in_window": hb_total,
            }
        ],
        "heartbeat_delivery_probabilities": [
            {
                "probability": hb_on_time / hb_total,
                "delivered_on_time": hb_on_time,
                "total_expected": hb_total,
                "session_id": sid,
                "window_start": window_start,
                "window_end": window_end,
            }
        ],
        "track_update_probabilities": [
            {
                "probability": t_with_data / t_total,
                "ticks_with_active_tracks": t_with_data,
                "total_ticks": t_total,
                "session_id": sid,
                "window_start": window_start,
                "window_end": window_end,
            }
        ],
        "per_sensor_health": [],
        "aggregate_health": {
            "avg_mttr_seconds": None,
            "avg_auto_recovery_time_seconds": None,
            "avg_mtbf_with_auto_recovery_seconds": None,
            "avg_mtbf_without_auto_recovery_seconds": None,
            "total_sensors": 0,
            "window_start": window_start,
            "window_end": window_end,
        },
        "active_sessions": session_count,
        "window_start": window_start,
        "window_end": window_end,
    }


@router.put("/update_sensor_health/{sensor_id}", response_model=SurveillanceSensorHealthResponse, dependencies=[WriteDep])
async def update_sensor_health(
    payload: SurveillanceSensorHealthUpdate,
    sensor_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    sensor = await db.get(SurveillanceSensor, sensor_id)
    if not sensor:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sensor not found")

    health = await db.execute(select(SurveillanceSensorHealth).where(SurveillanceSensorHealth.sensor_id == sensor_id))
    health_obj = health.scalar_one_or_none()

    if health_obj:
        previous_status = health_obj.status
        health_obj.status = payload.status
    else:
        health_obj = SurveillanceSensorHealth(sensor_id=sensor_id, status=payload.status)
        db.add(health_obj)
        previous_status = None

    # Record health tracking event
    tracking = SurveillanceSensorHealthTracking(
        sensor_id=sensor_id,
        status=payload.status,
        recovery_type=payload.recovery_type,
    )
    db.add(tracking)

    # Create failure notification if transitioning to non-operational
    if previous_status and previous_status != payload.status:
        notification = SurveillanceSensorFailureNotification(
            sensor_id=sensor_id,
            previous_status=previous_status,
            new_status=payload.status,
            recovery_type=payload.recovery_type,
            message=f"Sensor status changed from {previous_status} to {payload.status}",
        )
        db.add(notification)

    await db.flush()
    await db.refresh(health_obj)
    return health_obj


@router.get("/list_sensor_health_notifications", response_model=list[SensorFailureNotificationResponse], dependencies=[ReadDep])
async def list_sensor_health_notifications(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(SurveillanceSensorFailureNotification).order_by(SurveillanceSensorFailureNotification.created_at.desc()).limit(100)
    )
    return result.scalars().all()
