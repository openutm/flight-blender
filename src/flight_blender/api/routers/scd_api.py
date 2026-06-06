from typing import Any
from uuid import UUID

import arrow
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.api.dependencies import require_scopes
from flight_blender.db.session import async_get_db
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository
from flight_blender.repositories.notifications_repo import SQLAlchemyNotificationsRepository
from flight_blender.services import scd_svc
from flight_blender.services.scd_svc import SCDService

router = APIRouter(prefix="/scd")


async def _ops(db: AsyncSession = Depends(async_get_db)) -> SCDService:
    return SCDService(fd_repo=SQLAlchemyFlightDeclarationRepository(db))


@router.get("/v1/status")
async def scd_test_status(_auth: Any = Depends(require_scopes(["utm.inject_test_data"]))):
    data = scd_svc.get_scd_test_status()
    return JSONResponse(data, status_code=200)


@router.get("/v1/capabilities")
async def scd_test_capabilities(_auth: Any = Depends(require_scopes(["utm.inject_test_data"]))):
    data = scd_svc.get_scd_test_capabilities()
    return JSONResponse(data, status_code=200)


@router.get("/flight_planning/status")
@router.get("/flight_planning/u_space/status")
async def flight_planning_status(_auth: Any = Depends(require_scopes(["interuss.flight_planning.direct_automated_test"]))):
    data = scd_svc.get_flight_planning_status()
    return JSONResponse(data, status_code=200)


@router.post("/flight_planning/clear_area_requests")
@router.post("/flight_planning/u_space/clear_area_requests")
async def flight_planning_clear_area_request(
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.direct_automated_test"])),
):
    data, status_code = await scd_svc.clear_area(body)
    return JSONResponse(data, status_code=status_code)


@router.put("/flight_planning/flight_plans/{flight_plan_id}")
@router.put("/flight_planning/u_space/flight_plans/{flight_plan_id}")
async def upsert_flight_plan(
    flight_plan_id: UUID,
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.plan"])),
    ops: SCDService = Depends(_ops),
):
    data, status_code = await ops.upsert_flight_plan(str(flight_plan_id), body)
    return JSONResponse(data, status_code=status_code)


@router.delete("/flight_planning/flight_plans/{flight_plan_id}")
@router.delete("/flight_planning/u_space/flight_plans/{flight_plan_id}")
async def delete_flight_plan(
    flight_plan_id: UUID,
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.plan"])),
    ops: SCDService = Depends(_ops),
):
    data, status_code = await ops.delete_flight_plan(str(flight_plan_id))
    return JSONResponse(data, status_code=status_code)


@router.get("/flight_planning/user_notifications")
@router.get("/flight_planning/u_space/user_notifications")
async def query_user_notifications(
    after: str,
    before: str | None = None,
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.plan"])),
    db: AsyncSession = Depends(async_get_db),
):
    """Return user notifications observed by the USS between `after` and `before`.

    Implements the InterUSS Flight Planning automated testing interface
    (https://github.com/interuss/automated_testing_interfaces/blob/main/flight_planning/v1/flight_planning.yaml).
    """
    try:
        after_dt = arrow.get(after).datetime
        if before:
            before_dt = arrow.get(before).datetime
        else:
            before_dt = arrow.utcnow().datetime
    except Exception:
        return JSONResponse({"message": "Invalid date format. Use ISO 8601 format."}, status_code=400)

    repo = SQLAlchemyNotificationsRepository(db)
    notifications = await repo.get_active_notifications_between(after_dt, before_dt)
    return JSONResponse(
        {
            "user_notifications": [
                {
                    "observed_at": {
                        "value": n.created_at.isoformat() if n.created_at else arrow.utcnow().isoformat(),
                        "format": "RFC3339",
                    },
                    "message": n.message,
                }
                for n in notifications
            ]
        },
        status_code=200,
    )
