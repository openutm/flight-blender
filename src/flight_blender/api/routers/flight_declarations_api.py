import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.api.dependencies import require_scopes
from flight_blender.clients.dss_scd_client import OperationalIntentReferenceHelper, SCDOperations
from flight_blender.db.session import async_get_db
from flight_blender.domain_types.common import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository
from flight_blender.services.flight_declarations_svc import FlightDeclarationOperations, do_network_declarations_by_view
from flight_blender.tasks.flight_declarations_task import CelerySCDNotifier

router = APIRouter(prefix="/flight_declaration_ops")


def _scd_query_client() -> SCDOperations:
    return SCDOperations()


def _operational_intent_parser() -> OperationalIntentReferenceHelper:
    return OperationalIntentReferenceHelper()


def _notifier() -> CelerySCDNotifier:
    return CelerySCDNotifier()


async def _ops(db: AsyncSession = Depends(async_get_db)) -> FlightDeclarationOperations:
    return FlightDeclarationOperations(
        repo=SQLAlchemyFlightDeclarationRepository(db),
        scd_client=_scd_query_client(),
        parser=_operational_intent_parser(),
        notifier=_notifier(),
    )


@router.post("/set_flight_declaration")
async def set_flight_declaration(
    body: dict = Body(...),
    ops: FlightDeclarationOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    data, status_code = await ops.create_flight_declaration(body, response_message="Submitted Flight Declaration")
    return JSONResponse(data, status_code=status_code)


@router.post("/set_operational_intent")
async def set_operational_intent(
    body: dict = Body(...),
    ops: FlightDeclarationOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    data, status_code = await ops.set_operational_intent(body)
    return JSONResponse(data, status_code=status_code)


@router.post("/set_flight_declarations_bulk")
async def set_flight_declarations_bulk(
    body: Any = Body(...),
    ops: FlightDeclarationOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    error = await ops.validate_bulk_declarations_payload(body)
    if error is not None:
        data, status_code = error
        return JSONResponse(data, status_code=status_code)
    data, status_code = await ops.bulk_create_flight_declarations(body)
    return JSONResponse(data, status_code=status_code)


@router.post("/set_operational_intents_bulk")
async def set_operational_intents_bulk(
    body: Any = Body(...),
    ops: FlightDeclarationOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    error = await ops.validate_bulk_operational_intents_payload(body)
    if error is not None:
        data, status_code = error
        return JSONResponse(data, status_code=status_code)
    data, status_code = await ops.set_operational_intents_bulk(body)
    return JSONResponse(data, status_code=status_code)


@router.get("/flight_declaration")
async def list_flight_declarations(
    start_date: str | None = None,
    end_date: str | None = None,
    view: str | None = None,
    state: str | None = None,
    page: int = 1,
    page_size: int = 10,
    ops: FlightDeclarationOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    data, status_code = await ops.list_flight_declarations(start_date, end_date, state, page, page_size)
    return JSONResponse(data, status_code=status_code)


@router.post("/flight_declaration")
async def create_flight_declaration(
    body: dict = Body(...),
    ops: FlightDeclarationOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE])),
):
    data, status_code = await ops.create_flight_declaration(body, response_message="Submitted Flight Declaration")
    return JSONResponse(data, status_code=status_code)


@router.get("/flight_declaration/{pk}")
async def get_flight_declaration(
    pk: uuid.UUID,
    ops: FlightDeclarationOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    data, status_code = await ops.get_flight_declaration(pk)
    if data is None:
        return Response(status_code=404)
    return JSONResponse(data, status_code=status_code)


@router.get("/flight_declaration/{flight_declaration_id}/network_flight_declarations")
async def network_flight_declaration_details(
    flight_declaration_id: uuid.UUID,
    ops: FlightDeclarationOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    data, status_code = await ops.get_network_declarations_by_id(str(flight_declaration_id))
    return JSONResponse(data, status_code=status_code)


@router.get("/network_flight_declarations_by_view")
async def network_flight_declarations_by_view(
    view: str | None = None,
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    data, status_code = await do_network_declarations_by_view(view, _scd_query_client())
    return JSONResponse(data, status_code=status_code)


@router.put("/flight_declaration_review/{pk}")
async def update_flight_declaration_approval(
    pk: uuid.UUID,
    body: dict = Body(...),
    ops: FlightDeclarationOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    data, status_code = await ops.update_approval_from_request(pk, body)
    return JSONResponse(data, status_code=status_code)


@router.put("/flight_declaration_state/{pk}")
async def update_flight_declaration_state(
    pk: uuid.UUID,
    body: dict = Body(...),
    ops: FlightDeclarationOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    data, status_code = await ops.update_state_from_request(pk, body)
    return JSONResponse(data, status_code=status_code)


@router.delete("/flight_declaration/{declaration_id}/delete")
async def delete_flight_declaration(
    declaration_id: uuid.UUID,
    ops: FlightDeclarationOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    status_code = await ops.delete_flight_declaration(declaration_id)
    return Response(status_code=status_code)


@router.post("/flight_declaration/{pk}/submit_to_dss")
async def submit_flight_declaration_to_dss(
    pk: uuid.UUID,
    ops: FlightDeclarationOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    data, status_code = await ops.submit_flight_declaration_to_dss(pk)
    return JSONResponse(data, status_code=status_code)
