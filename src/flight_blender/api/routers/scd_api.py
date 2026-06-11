from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.api.dependencies import require_scopes
from flight_blender.db.session import async_get_db
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository
from flight_blender.repositories.notifications_repo import SQLAlchemyNotificationsRepository
from flight_blender.schemas.scd import (
    ClearAreaRequestSchema,
    ClearAreaResponseSchema,
    CloseFlightPlanResponseSchema,
    FlightPlanningStatusSchema,
    SCDCapabilitiesSchema,
    SCDTestStatusSchema,
    UpsertFlightPlanRequestSchema,
    UpsertFlightPlanResponseSchema,
    UserNotificationsResponseSchema,
)
from flight_blender.services import scd_svc
from flight_blender.services.scd_svc import SCDService

router = APIRouter(prefix="/scd")


async def _flight_declaration_repo(db: AsyncSession = Depends(async_get_db)) -> SQLAlchemyFlightDeclarationRepository:
    return SQLAlchemyFlightDeclarationRepository(db)


async def _notifications_repo(db: AsyncSession = Depends(async_get_db)) -> SQLAlchemyNotificationsRepository:
    return SQLAlchemyNotificationsRepository(db)


async def _ops(
    fd_repo: SQLAlchemyFlightDeclarationRepository = Depends(_flight_declaration_repo),
    notifications_repo: SQLAlchemyNotificationsRepository = Depends(_notifications_repo),
) -> SCDService:
    return SCDService(fd_repo=fd_repo, notifications_repo=notifications_repo)


@router.get("/v1/status", response_model=SCDTestStatusSchema)
async def scd_test_status(_auth: Any = Depends(require_scopes(["utm.inject_test_data"]))):
    return scd_svc.get_scd_test_status()


@router.get("/v1/capabilities", response_model=SCDCapabilitiesSchema)
async def scd_test_capabilities(_auth: Any = Depends(require_scopes(["utm.inject_test_data"]))):
    return scd_svc.get_scd_test_capabilities()


@router.get("/flight_planning/status", response_model=FlightPlanningStatusSchema)
@router.get("/flight_planning/u_space/status", response_model=FlightPlanningStatusSchema)
async def flight_planning_status(_auth: Any = Depends(require_scopes(["interuss.flight_planning.direct_automated_test"]))):
    return scd_svc.get_flight_planning_status()


@router.post("/flight_planning/clear_area_requests", response_model=ClearAreaResponseSchema)
@router.post("/flight_planning/u_space/clear_area_requests", response_model=ClearAreaResponseSchema)
async def flight_planning_clear_area_request(
    body: ClearAreaRequestSchema,
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.direct_automated_test"])),
):
    return await scd_svc.clear_area(body)


@router.put("/flight_planning/flight_plans/{flight_plan_id}", response_model=UpsertFlightPlanResponseSchema)
@router.put("/flight_planning/u_space/flight_plans/{flight_plan_id}", response_model=UpsertFlightPlanResponseSchema)
async def upsert_flight_plan(
    flight_plan_id: UUID,
    body: UpsertFlightPlanRequestSchema,
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.plan"])),
    ops: SCDService = Depends(_ops),
):
    return await ops.upsert_flight_plan(flight_plan_id, body)


@router.delete("/flight_planning/flight_plans/{flight_plan_id}", response_model=CloseFlightPlanResponseSchema)
@router.delete("/flight_planning/u_space/flight_plans/{flight_plan_id}", response_model=CloseFlightPlanResponseSchema)
async def delete_flight_plan(
    flight_plan_id: UUID,
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.plan"])),
    ops: SCDService = Depends(_ops),
):
    return await ops.delete_flight_plan(flight_plan_id)


@router.get("/flight_planning/user_notifications", response_model=UserNotificationsResponseSchema)
@router.get("/flight_planning/u_space/user_notifications", response_model=UserNotificationsResponseSchema)
async def query_user_notifications(
    after: datetime = Query(...),
    before: datetime | None = Query(default=None),
    _auth: Any = Depends(require_scopes(["interuss.flight_planning.plan"])),
    ops: SCDService = Depends(_ops),
):
    """Return user notifications observed by the USS between `after` and `before`.

    Implements the InterUSS Flight Planning automated testing interface
    (https://github.com/interuss/automated_testing_interfaces/blob/main/flight_planning/v1/flight_planning.yaml).
    """
    return await ops.query_user_notifications(after, before)
