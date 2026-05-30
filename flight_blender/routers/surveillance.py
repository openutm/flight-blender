"""
FastAPI router for surveillance monitoring operations.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
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
    SurveillanceMetricsResponse,
    SurveillanceSensorHealthResponse,
    SurveillanceSensorHealthUpdate,
    SurveillanceSensorResponse,
)
from flight_blender.tasks.surveillance import send_heartbeat_to_consumer

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


@router.post("/start_stop_surveillance_heartbeat_track/{session_id}", dependencies=[WriteDep])
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
        return {"message": "Heartbeat started", "session_id": str(session_id)}
    else:
        if not existing:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        await db.delete(existing)
        return {"message": "Heartbeat stopped", "session_id": str(session_id)}


@router.get("/list_surveillance_sensors", response_model=list[SurveillanceSensorResponse], dependencies=[ReadDep])
async def list_surveillance_sensors(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(SurveillanceSensor))
    return result.scalars().all()


@router.get("/service_metrics", response_model=SurveillanceMetricsResponse, dependencies=[ReadDep])
async def get_service_metrics(db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func

    heartbeat_total = await db.execute(select(func.count()).select_from(SurveillanceHeartbeatEvent))
    heartbeat_on_time = await db.execute(
        select(func.count()).select_from(SurveillanceHeartbeatEvent).where(SurveillanceHeartbeatEvent.delivered_on_time == True)  # noqa: E712
    )
    track_total = await db.execute(select(func.count()).select_from(SurveillanceTrackEvent))
    track_with_data = await db.execute(
        select(func.count()).select_from(SurveillanceTrackEvent).where(SurveillanceTrackEvent.had_active_tracks == True)  # noqa: E712
    )

    hb_total = heartbeat_total.scalar_one() or 1
    hb_on_time = heartbeat_on_time.scalar_one()
    t_total = track_total.scalar_one() or 1
    t_with_data = track_with_data.scalar_one()

    return SurveillanceMetricsResponse(
        heartbeat_delivery_probability=hb_on_time / hb_total,
        track_update_probability=t_with_data / t_total,
        per_sensor_health=[],
        aggregate_health="operational",
        active_sessions=0,
    )


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
