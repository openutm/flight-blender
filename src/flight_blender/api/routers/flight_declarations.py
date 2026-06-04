import json
import uuid
from dataclasses import asdict
from os import environ as env
from typing import Any

import arrow
from asgiref.sync import sync_to_async
from fastapi import APIRouter, Body, Depends
from fastapi.responses import JSONResponse, Response
from loguru import logger

from flight_blender.api.dependencies import require_scopes
from flight_blender.common.data_definitions import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from flight_blender.flight_declarations.data_definitions import BulkFlightDeclarationCreateResponse, HTTP400Response, HTTP404Response

router = APIRouter()


# ── sync helpers (run inside sync_to_async) ──────────────────────────────────


def _do_set_flight_declaration(request_data: dict) -> tuple[dict, int]:
    from flight_blender.flight_declarations.views import (
        _process_intersection_result,
        _run_deconfliction,
        _validate_and_save_flight_declaration,
    )

    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))
    default_state = 0 if ussp_network_enabled else 1
    flight_declaration, error = _validate_and_save_flight_declaration(request_data, default_state)
    if error or flight_declaration is None:
        return error or {"message": "Unknown error"}, 400
    intersection_results = _run_deconfliction([flight_declaration], ussp_network_enabled)
    creation_response = _process_intersection_result(flight_declaration, intersection_results[str(flight_declaration.id)], ussp_network_enabled)
    return asdict(creation_response), 200


def _do_set_operational_intent(request_data: dict) -> tuple[dict, int]:
    from flight_blender.flight_declarations.views import (
        _process_intersection_result,
        _run_deconfliction,
        _validate_and_save_operational_intent,
    )

    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))
    default_state = 0 if ussp_network_enabled else 1
    flight_declaration, error = _validate_and_save_operational_intent(request_data, default_state)
    if error or flight_declaration is None:
        return error or {"message": "Unknown error"}, 400
    intersection_results = _run_deconfliction([flight_declaration], ussp_network_enabled)
    creation_response = _process_intersection_result(flight_declaration, intersection_results[str(flight_declaration.id)], ussp_network_enabled)
    return asdict(creation_response), 200


def _do_set_flight_declarations_bulk(flight_declarations_list: list) -> tuple[dict, int]:
    from django.db import transaction

    from flight_blender.flight_declarations.views import (
        _process_intersection_result,
        _run_deconfliction,
        _validate_and_save_flight_declaration,
    )

    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))
    default_state = 0 if ussp_network_enabled else 1

    with transaction.atomic():
        saved: dict[int, Any] = {}
        results: list[dict] = []
        failed_count = 0

        for idx, item in enumerate(flight_declarations_list):
            try:
                flight_declaration, error = _validate_and_save_flight_declaration(item, default_state)
                if error or flight_declaration is None:
                    failed_count += 1
                    error = error or {"message": "Unknown error"}
                    results.append(
                        {"index": idx, "success": False, "message": error.get("message", "Validation error"), "errors": error.get("errors")}
                    )
                else:
                    saved[idx] = flight_declaration
            except Exception as exc:
                logger.error(f"Error at index {idx}: {exc}")
                failed_count += 1
                results.append({"index": idx, "success": False, "message": str(exc)})

        intersection_results = _run_deconfliction(list(saved.values()), ussp_network_enabled)
        submitted_count = 0
        for idx, flight_declaration in saved.items():
            fd_id = str(flight_declaration.id)
            creation_response = _process_intersection_result(flight_declaration, intersection_results[fd_id], ussp_network_enabled)
            submitted_count += 1
            results.append(
                {
                    "index": idx,
                    "success": True,
                    "id": creation_response.id,
                    "is_approved": creation_response.is_approved,
                    "state": creation_response.state,
                }
            )

    results.sort(key=lambda r: r["index"])
    bulk_response = BulkFlightDeclarationCreateResponse(submitted=submitted_count, failed=failed_count, results=results)
    http_status = 200 if failed_count == 0 else 207
    return asdict(bulk_response), http_status


def _do_set_operational_intents_bulk(operational_intents_list: list) -> tuple[dict, int]:
    from django.db import transaction

    from flight_blender.flight_declarations.views import (
        _process_intersection_result,
        _run_deconfliction,
        _validate_and_save_operational_intent,
    )

    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))
    default_state = 0 if ussp_network_enabled else 1

    with transaction.atomic():
        saved: dict[int, Any] = {}
        results: list[dict] = []
        failed_count = 0

        for idx, item in enumerate(operational_intents_list):
            try:
                flight_declaration, error = _validate_and_save_operational_intent(item, default_state)
                if error or flight_declaration is None:
                    failed_count += 1
                    error = error or {"message": "Unknown error"}
                    results.append(
                        {"index": idx, "success": False, "message": error.get("message", "Validation error"), "errors": error.get("errors")}
                    )
                else:
                    saved[idx] = flight_declaration
            except Exception as exc:
                logger.error(f"Error at index {idx}: {exc}")
                failed_count += 1
                results.append({"index": idx, "success": False, "message": str(exc)})

        intersection_results = _run_deconfliction(list(saved.values()), ussp_network_enabled)
        submitted_count = 0
        for idx, flight_declaration in saved.items():
            fd_id = str(flight_declaration.id)
            creation_response = _process_intersection_result(flight_declaration, intersection_results[fd_id], ussp_network_enabled)
            submitted_count += 1
            results.append(
                {
                    "index": idx,
                    "success": True,
                    "id": creation_response.id,
                    "is_approved": creation_response.is_approved,
                    "state": creation_response.state,
                }
            )

    results.sort(key=lambda r: r["index"])
    bulk_response = BulkFlightDeclarationCreateResponse(submitted=submitted_count, failed=failed_count, results=results)
    http_status = 200 if failed_count == 0 else 207
    return asdict(bulk_response), http_status


def _do_list_flight_declarations(start_date: str | None, end_date: str | None, view: str | None, states_raw: str | None) -> tuple[dict, int]:
    from flight_blender.flight_declarations.serializers import FlightDeclarationSerializer
    from flight_blender.flight_declarations.views import FlightDeclarationCreateList

    view_port = [float(i) for i in view.split(",")] if view else []
    states: list[int] | None = None
    if states_raw:
        tokens = [s.strip() for s in states_raw.split(",") if s.strip()]
        if tokens:
            try:
                states = [int(s) for s in tokens]
            except ValueError:
                return {"error": "State values must be integers."}, 400

    lister = FlightDeclarationCreateList()
    qs = lister.get_relevant_flight_declaration(start_date=start_date, end_date=end_date, view_port=view_port, states=states)
    count = qs.count()
    data = FlightDeclarationSerializer(qs, many=True).data
    return {"count": count, "next": None, "previous": None, "results": list(data)}, 200


def _do_create_flight_declaration_via_list(request_data: dict) -> tuple[dict, int]:
    from flight_blender.common.database_operations import FlightBlenderDatabaseWriter
    from flight_blender.flight_declarations.views import (
        _process_intersection_result,
        _run_deconfliction,
        _validate_and_save_flight_declaration,
    )

    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))
    default_state = 0 if ussp_network_enabled else 1
    flight_declaration, error = _validate_and_save_flight_declaration(request_data, default_state)
    if error or flight_declaration is None:
        return error or {"message": "Unknown error"}, 400

    my_database_writer = FlightBlenderDatabaseWriter()
    my_database_writer.create_flight_operational_intent_reference_from_flight_declaration_obj(flight_declaration=flight_declaration)

    intersection_results = _run_deconfliction([flight_declaration], ussp_network_enabled)
    creation_response = _process_intersection_result(flight_declaration, intersection_results[str(flight_declaration.id)], ussp_network_enabled)
    return asdict(creation_response), 200


def _do_get_flight_declaration(pk: str) -> tuple[dict | None, int]:
    from flight_blender.flight_declarations.models import FlightDeclaration
    from flight_blender.flight_declarations.serializers import FlightDeclarationSerializer

    try:
        fd = FlightDeclaration.objects.get(pk=pk)
    except FlightDeclaration.DoesNotExist:
        return None, 404
    data = FlightDeclarationSerializer(fd).data
    return dict(data), 200


def _do_update_state(pk: str, state: int) -> tuple[dict, int]:
    from flight_blender.flight_declarations.models import FlightDeclaration
    from flight_blender.flight_declarations.serializers import FlightDeclarationStateSerializer

    try:
        fd = FlightDeclaration.objects.get(pk=pk)
    except FlightDeclaration.DoesNotExist:
        return {"detail": "Not found"}, 404
    serializer = FlightDeclarationStateSerializer(fd, data={"state": state}, partial=True)
    if not serializer.is_valid():
        return {"detail": serializer.errors}, 400
    serializer.save()
    return dict(serializer.data), 200


def _do_update_approval(pk: str, is_approved: bool, approved_by: str | None) -> tuple[dict, int]:
    from flight_blender.flight_declarations.models import FlightDeclaration
    from flight_blender.flight_declarations.serializers import FlightDeclarationApprovalSerializer

    try:
        fd = FlightDeclaration.objects.get(pk=pk)
    except FlightDeclaration.DoesNotExist:
        return {"detail": "Not found"}, 404
    payload: dict = {"is_approved": is_approved}
    if approved_by is not None:
        payload["approved_by"] = approved_by
    serializer = FlightDeclarationApprovalSerializer(fd, data=payload, partial=True)
    if not serializer.is_valid():
        return {"detail": serializer.errors}, 400
    serializer.save()
    return dict(serializer.data), 200


def _do_delete_flight_declaration(declaration_id: str) -> int:
    from flight_blender.flight_declarations.models import FlightDeclaration

    try:
        fd = FlightDeclaration.objects.get(pk=declaration_id)
        fd.delete()
        return 204
    except FlightDeclaration.DoesNotExist:
        return 404


def _do_submit_to_dss(pk: str) -> tuple[dict, int]:
    from django.db import transaction

    from flight_blender.flight_declarations.models import FlightDeclaration, FlightOperationalIntentReference
    from flight_blender.flight_declarations.tasks import send_operational_update_message, submit_flight_declaration_to_dss_async

    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))
    if not ussp_network_enabled:
        return {"message": "USSP network is not enabled; DSS submission is only available when USSP_NETWORK_ENABLED=1."}, 400

    with transaction.atomic():
        try:
            flight_declaration = FlightDeclaration.objects.select_for_update().get(pk=pk)
        except FlightDeclaration.DoesNotExist:
            return {"message": "Flight declaration not found."}, 404

        if flight_declaration.state != 0:
            return {
                "message": (
                    "Flight declaration is not in 'Not Submitted' state (state=0). "
                    "Current state: %d. Only declarations in state=0 can be submitted to the DSS via this endpoint."
                )
                % flight_declaration.state
            }, 409

        if FlightOperationalIntentReference.objects.filter(declaration=flight_declaration).exists():
            return {"message": "A DSS operational intent reference already exists for this flight declaration."}, 409

        flight_declaration_id = str(flight_declaration.id)
        flight_declaration.add_state_history_entry(
            new_state=flight_declaration.state,
            original_state=flight_declaration.state,
            notes="DSS submission initiated via manual endpoint",
        )

    submit_flight_declaration_to_dss_async.delay(flight_declaration_id=flight_declaration_id)
    send_operational_update_message.delay(
        flight_declaration_id=flight_declaration_id,
        message_text="Manual DSS submission triggered for flight declaration %s" % flight_declaration_id,
        level="info",
    )
    return {"message": "DSS submission initiated.", "id": flight_declaration_id}, 200


def _do_network_declarations_by_view(view: str | None) -> tuple[dict, int]:
    from flight_blender.rid import view_port_ops
    from flight_blender.scd.dss_scd_helper import SCDOperations
    from flight_blender.flight_declarations.utils import OperationalIntentsConverter

    USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", "0"))

    if not view:
        return {"message": "A view bbox is necessary with four values: lat1, lng1, lat2, lng2"}, 400

    try:
        view_port = [float(i) for i in view.split(",")]
    except ValueError:
        return {"message": "A view bbox is necessary with four values: lat1, lng1, lat2, lng2"}, 400

    if not view_port_ops.check_view_port(view_port_coords=view_port):
        return {"message": "An incorrect view port bbox was provided"}, 400

    if not USSP_NETWORK_ENABLED:
        return asdict(HTTP400Response(message="USSP network cannot be queried since it is not enabled in Flight Blender")), 400

    start_datetime = arrow.now().shift(minutes=-1).isoformat()
    end_datetime = arrow.now().shift(minutes=10).isoformat()
    view_port_box = view_port_ops.build_view_port_box_lng_lat(view_port_coords=view_port)
    converted_geo_json = view_port_ops.convert_box_to_geojson_feature(box=view_port_box)

    my_operational_intent_converter = OperationalIntentsConverter()
    temporary_ref = my_operational_intent_converter.create_partial_operational_intent_ref(
        geo_json_fc=converted_geo_json,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        priority=0,
    )
    volumes = temporary_ref.volumes
    my_operational_intent_converter.convert_operational_intent_to_geo_json(volumes=volumes)

    my_scd_helper = SCDOperations()
    try:
        operational_intent_geojson = my_scd_helper.get_and_process_nearby_operational_intents(volumes=volumes)
    except (ValueError, ConnectionError):
        operational_intent_geojson = []

    return operational_intent_geojson, 200


def _do_network_declarations_by_id(flight_declaration_id: str) -> tuple[dict, int]:
    from flight_blender.common.database_operations import FlightBlenderDatabaseReader
    from flight_blender.scd.dss_scd_helper import OperationalIntentReferenceHelper, SCDOperations

    USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", "0"))
    my_database_reader = FlightBlenderDatabaseReader()

    if not USSP_NETWORK_ENABLED:
        return asdict(HTTP400Response(message="USSP network cannot be queried since it is not enabled in Flight Blender")), 400

    if not my_database_reader.check_flight_declaration_exists(flight_declaration_id=flight_declaration_id):
        return asdict(HTTP404Response(message=f"Flight Declaration with ID {flight_declaration_id} not found")), 404

    flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=flight_declaration_id)

    if flight_declaration.state not in [0, 1, 2, 3, 4]:
        return asdict(HTTP400Response(message="USSP network can only be queried for operational intents that are active")), 400

    try:
        operational_intent_volumes_raw = json.loads(flight_declaration.operational_intent)
        operational_intent_volumes = operational_intent_volumes_raw["volumes"]
    except (json.JSONDecodeError, KeyError):
        return _asdict(HTTP400Response(message="Flight declaration has invalid or missing operational intent volumes")), 400

    my_operational_intent_parser = OperationalIntentReferenceHelper()
    all_volumes = [my_operational_intent_parser.parse_volume_to_volume4D(volume=volume) for volume in operational_intent_volumes]

    my_scd_helper = SCDOperations()
    try:
        operational_intent_geojson = my_scd_helper.get_and_process_nearby_operational_intents(volumes=all_volumes)
    except (ValueError, ConnectionError):
        operational_intent_geojson = []

    return operational_intent_geojson, 200


# ── routes ────────────────────────────────────────────────────────────────────


@router.post("/set_flight_declaration")
async def set_flight_declaration(
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    data, status_code = await sync_to_async(_do_set_flight_declaration)(body)
    return JSONResponse(data, status_code=status_code)


@router.post("/set_operational_intent")
async def set_operational_intent(
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    data, status_code = await sync_to_async(_do_set_operational_intent)(body)
    return JSONResponse(data, status_code=status_code)


@router.post("/set_flight_declarations_bulk")
async def set_flight_declarations_bulk(
    body: Any = Body(...),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    if not isinstance(body, list):
        return JSONResponse({"message": "Request body must be a JSON array of flight declaration objects."}, status_code=400)
    data, status_code = await sync_to_async(_do_set_flight_declarations_bulk)(body)
    return JSONResponse(data, status_code=status_code)


@router.post("/set_operational_intents_bulk")
async def set_operational_intents_bulk(
    body: Any = Body(...),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    if not isinstance(body, list):
        return JSONResponse({"message": "Request body must be a JSON array of operational intent objects."}, status_code=400)
    data, status_code = await sync_to_async(_do_set_operational_intents_bulk)(body)
    return JSONResponse(data, status_code=status_code)


@router.get("/flight_declaration")
async def list_flight_declarations(
    start_date: str | None = None,
    end_date: str | None = None,
    view: str | None = None,
    state: str | None = None,
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    data, status_code = await sync_to_async(_do_list_flight_declarations)(start_date, end_date, view, state)
    return JSONResponse(data, status_code=status_code)


@router.post("/flight_declaration")
async def create_flight_declaration(
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE])),
):
    data, status_code = await sync_to_async(_do_create_flight_declaration_via_list)(body)
    return JSONResponse(data, status_code=status_code)


@router.get("/flight_declaration/{pk}")
async def get_flight_declaration(
    pk: uuid.UUID,
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    data, status_code = await sync_to_async(_do_get_flight_declaration)(str(pk))
    if data is None:
        return Response(status_code=404)
    return JSONResponse(data, status_code=status_code)


@router.get("/flight_declaration/{flight_declaration_id}/network_flight_declarations")
async def network_flight_declaration_details(
    flight_declaration_id: uuid.UUID,
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    data, status_code = await sync_to_async(_do_network_declarations_by_id)(str(flight_declaration_id))
    return JSONResponse(data, status_code=status_code)


@router.get("/network_flight_declarations_by_view")
async def network_flight_declarations_by_view(
    view: str | None = None,
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    data, status_code = await sync_to_async(_do_network_declarations_by_view)(view)
    return JSONResponse(data, status_code=status_code)


@router.put("/flight_declaration_review/{pk}")
async def update_flight_declaration_approval(
    pk: uuid.UUID,
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    is_approved = body.get("is_approved")
    approved_by = body.get("approved_by")
    if is_approved is None:
        return JSONResponse({"detail": "is_approved is required"}, status_code=422)
    data, status_code = await sync_to_async(_do_update_approval)(str(pk), bool(is_approved), approved_by)
    return JSONResponse(data, status_code=status_code)


@router.put("/flight_declaration_state/{pk}")
async def update_flight_declaration_state(
    pk: uuid.UUID,
    body: dict = Body(...),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    state = body.get("state")
    if state is None:
        return JSONResponse({"detail": "state is required"}, status_code=422)
    try:
        state_int = int(state)
    except (TypeError, ValueError):
        return JSONResponse({"detail": "state must be an integer"}, status_code=422)
    data, status_code = await sync_to_async(_do_update_state)(str(pk), state_int)
    return JSONResponse(data, status_code=status_code)


@router.delete("/flight_declaration/{declaration_id}/delete")
async def delete_flight_declaration(
    declaration_id: uuid.UUID,
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    status_code = await sync_to_async(_do_delete_flight_declaration)(str(declaration_id))
    return Response(status_code=status_code)


@router.post("/flight_declaration/{pk}/submit_to_dss")
async def submit_flight_declaration_to_dss(
    pk: uuid.UUID,
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    data, status_code = await sync_to_async(_do_submit_to_dss)(str(pk))
    return JSONResponse(data, status_code=status_code)
