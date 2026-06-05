import uuid
from typing import Any

import arrow
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.api.dependencies import require_scopes
from flight_blender.api.schemas.surveillance import SensorHealthUpdate, SurveillanceSessionAction
from flight_blender.core.entities.common import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from flight_blender.core.operations.surveillance import SurveillanceOperations
from flight_blender.infrastructure.celery.task_scheduler import TaskSchedulerService
from flight_blender.infrastructure.database.repositories.sa_flight_feed import SQLAlchemyFlightFeedRepository
from flight_blender.infrastructure.database.repositories.sa_surveillance import SQLAlchemySurveillanceRepository
from flight_blender.infrastructure.database.session import async_get_db

router = APIRouter(prefix="/surveillance_monitoring_ops")


async def _ops(db: AsyncSession = Depends(async_get_db)) -> SurveillanceOperations:
    surveillance_repo = SQLAlchemySurveillanceRepository(db)
    flight_feed_repo = SQLAlchemyFlightFeedRepository(db)
    return SurveillanceOperations(repo=surveillance_repo, scheduler=TaskSchedulerService, flight_feed_repo=flight_feed_repo)


@router.get("/health/")
async def surveillance_health(
    ops: SurveillanceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    return await ops.get_health()


@router.put("/start_stop_surveillance_heartbeat_track/{surveillance_session_id}")
async def start_stop_surveillance_heartbeat_track(
    surveillance_session_id: uuid.UUID,
    body: SurveillanceSessionAction,
    ops: SurveillanceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    result, status_code = await ops.start_stop_surveillance_session(session_id=surveillance_session_id, action=body.action)
    return JSONResponse(result, status_code=status_code)


@router.get("/list_surveillance_sensors")
async def list_surveillance_sensors(
    ops: SurveillanceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    sensors = await ops.list_surveillance_sensors()
    return {"active_sensors": sensors}


@router.get("/service_metrics")
async def service_metrics(
    start_date: str | None = None,
    end_date: str | None = None,
    session_id: str | None = None,
    ops: SurveillanceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    try:
        result = await ops.get_service_metrics(start_date=start_date, end_date=end_date, session_id=session_id)
    except arrow.parser.ParserError:
        return JSONResponse({"error": "Invalid date format. Use ISO8601 format."}, status_code=400)
    return result


@router.put("/update_sensor_health/{sensor_id}")
async def update_sensor_health(
    sensor_id: uuid.UUID,
    body: SensorHealthUpdate,
    ops: SurveillanceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    result, status_code = await ops.update_sensor_health(sensor_id=sensor_id, new_status=body.status, recovery_type=body.recovery_type)
    return JSONResponse(result, status_code=status_code)


@router.get("/list_sensor_health_notifications")
async def list_sensor_health_notifications(
    sensor_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    ops: SurveillanceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    try:
        notifications = await ops.list_sensor_health_notifications(
            sensor_id=sensor_id,
            start_date=start_date,
            end_date=end_date,
        )
    except arrow.parser.ParserError:
        return JSONResponse({"error": "Invalid date format. Use ISO8601 format."}, status_code=400)
    return {"notifications": notifications}
