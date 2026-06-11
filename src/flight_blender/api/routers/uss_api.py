import asyncio
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.api.dependencies import require_scopes
from flight_blender.db.session import async_get_db
from flight_blender.repositories.constraint_repo import SQLAlchemyConstraintRepository
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository
from flight_blender.repositories.flight_feed_repo import SQLAlchemyFlightFeedRepository
from flight_blender.repositories.rid_repo import SQLAlchemyRIDRepository
from flight_blender.services import uss_svc
from flight_blender.services.uss_svc import USSService

router = APIRouter(prefix="/uss")


async def _ops(db: AsyncSession = Depends(async_get_db)) -> USSService:
    return USSService(
        fd_repo=SQLAlchemyFlightDeclarationRepository(db),
        constraint_repo=SQLAlchemyConstraintRepository(db),
        rid_repo=SQLAlchemyRIDRepository(db),
    )


async def _feed_ops(db: AsyncSession = Depends(async_get_db)) -> SQLAlchemyFlightFeedRepository:
    return SQLAlchemyFlightFeedRepository(db)


@router.post("/v1/reports")
async def peer_uss_report_notification(
    body: dict = Body(...),
    _auth: Any = Depends(
        require_scopes(
            [
                "utm.strategic_coordination",
                "utm.constraint_processing",
                "utm.constraint_management",
                "utm.conformance_monitoring_sa",
                "utm.availability_arbitration",
            ],
            allow_any=True,
        )
    ),
):
    data, status_code = await asyncio.to_thread(uss_svc.peer_uss_report_notification, body)
    return JSONResponse(data, status_code=status_code)


@router.get("/v1/operational_intents/{opint_id}")
async def uss_operational_intent_details(
    opint_id: uuid.UUID,
    ops: USSService = Depends(_ops),
    _auth: Any = Depends(require_scopes(["utm.strategic_coordination"])),
):
    data, status_code = await ops.uss_operational_intent_details(str(opint_id))
    return JSONResponse(data, status_code=status_code)


@router.get("/v1/operational_intents/{opint_id}/telemetry")
async def uss_opint_detail_telemetry(
    opint_id: uuid.UUID,
    _auth: Any = Depends(require_scopes(["utm.conformance_monitoring_sa"])),
):
    data = await asyncio.to_thread(uss_svc.uss_telemetry, str(opint_id))
    return JSONResponse(data, status_code=200)


@router.post("/v1/operational_intents")
async def uss_update_opint_details(
    body: dict = Body(...),
    ops: USSService = Depends(_ops),
    _auth: Any = Depends(require_scopes(["utm.strategic_coordination"])),
):
    data, status_code = await ops.uss_update_opint_details(body)
    return Response(status_code=status_code)


@router.get("/v1/constraints/{constraint_id}")
async def uss_constraint_details(
    constraint_id: uuid.UUID,
    ops: USSService = Depends(_ops),
    _auth: Any = Depends(require_scopes(["utm.constraint_processing"])),
):
    data, status_code = await ops.uss_constraint_details(str(constraint_id))
    return JSONResponse(data, status_code=status_code)


@router.post("/v1/constraints")
async def uss_update_constraint_details(
    body: dict = Body(...),
    ops: USSService = Depends(_ops),
    _auth: Any = Depends(require_scopes(["utm.constraint_processing"])),
):
    status_code = await ops.uss_update_constraint_details(body)
    return Response(status_code=status_code)


@router.get("/flights")
async def get_uss_flights(
    view: str | None = None,
    feed_repo: SQLAlchemyFlightFeedRepository = Depends(_feed_ops),
    _auth: Any = Depends(require_scopes(["rid.display_provider"])),
):
    if not view:
        return JSONResponse({"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}, status_code=400)
    data, status_code = await uss_svc.get_uss_flights(view, repo=feed_repo)
    return JSONResponse(data, status_code=status_code)


@router.get("/flights/{flight_id}/details")
async def get_uss_flight_details(
    flight_id: uuid.UUID,
    ops: USSService = Depends(_ops),
    _auth: Any = Depends(require_scopes(["rid.display_provider"])),
):
    data, status_code = await ops.get_uss_flight_details(flight_id)
    return JSONResponse(data, status_code=status_code)
