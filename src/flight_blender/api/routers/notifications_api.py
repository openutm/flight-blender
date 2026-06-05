from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.api.dependencies import require_scopes
from flight_blender.db.session import async_get_db
from flight_blender.domain_types.common import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from flight_blender.repositories.notifications_repo import SQLAlchemyNotificationsRepository
from flight_blender.schemas.notifications import CreateNotificationRequest
from flight_blender.services.notifications_svc import NotificationsOperations

router = APIRouter(prefix="/notifications_ops")


async def _ops(db: AsyncSession = Depends(async_get_db)) -> NotificationsOperations:
    return NotificationsOperations(repo=SQLAlchemyNotificationsRepository(db))


@router.get("/notifications")
async def list_notifications(
    start_date: str | None = None,
    end_date: str | None = None,
    ops: NotificationsOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    dates, error = ops.parse_date_range_with_lookback(start_date, end_date)
    if error:
        return JSONResponse({"error": error}, status_code=400)
    start, end = dates
    return {"notifications": await ops.get_active_notifications(start_time=start, end_time=end)}


@router.post("/notifications", status_code=201)
async def create_notification(
    body: CreateNotificationRequest,
    ops: NotificationsOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    return await ops.create_notification(message=body.message, session_id=body.session_id)
