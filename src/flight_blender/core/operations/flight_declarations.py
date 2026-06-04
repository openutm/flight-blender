import json
import uuid
from dataclasses import asdict
from os import environ as env
from typing import Any

import arrow
from django.db import transaction
from loguru import logger
from marshmallow.exceptions import ValidationError
from shapely.geometry import shape
from shapely.ops import unary_union

from flight_blender.common.database_operations import FlightBlenderDatabaseReader
from flight_blender.flight_declarations.data_definitions import (
    BulkFlightDeclarationCreateResponse,
    CreateFlightDeclarationRequestSchema,
    FlightDeclarationCreateResponse,
    HTTP400Response,
    HTTP404Response,
)
from flight_blender.flight_declarations.utils import OperationalIntentsConverter
from flight_blender.flight_declarations.views import _process_intersection_result, _run_deconfliction, _validate_and_save_operational_intent
from flight_blender.infrastructure.database.repositories.sa_flight_declarations import SQLAlchemyFlightDeclarationRepository
from flight_blender.rid import view_port_ops
from flight_blender.scd.dss_scd_helper import OperationalIntentReferenceHelper, SCDOperations


def _validate_geojson(fc: dict) -> tuple[bool, str | None]:
    features = fc.get("features", [])
    if not features:
        return False, "Flight declaration GeoJSON is required."
    for feature in features:
        geometry = feature.get("geometry")
        props = feature.get("properties", {})
        shp = shape(geometry)
        if not shp.is_valid:
            return False, "Error in processing the submitted GeoJSON: every Feature in a GeoJSON FeatureCollection must have a valid geometry, please check your submitted FeatureCollection"
        if "min_altitude" not in props or "max_altitude" not in props:
            return (
                False,
                "Error in processing the submitted GeoJSON every Feature in a GeoJSON FeatureCollection must have a min_altitude and max_altitude data structure",
            )
    return True, None


def _validate_dates(start_datetime: str, end_datetime: str) -> tuple[bool, str | None]:
    now = arrow.now()
    s_datetime = arrow.get(start_datetime)
    e_datetime = arrow.get(end_datetime)
    two_days_from_now = now.shift(days=2)
    if s_datetime < now or e_datetime < now or e_datetime > two_days_from_now or s_datetime > two_days_from_now:
        return False, "A flight declaration cannot have a start / end time in the past or after two days from current time."
    return True, None


def _build_partial_operational_intent(request_data: dict) -> tuple[dict, str]:
    geometries = [shape(feature["geometry"]) for feature in request_data["flight_declaration_geo_json"].get("features", [])]
    unioned = unary_union(geometries)
    min_lng, min_lat, max_lng, max_lat = unioned.bounds
    bounds = ",".join(str(v) for v in (min_lng, min_lat, max_lng, max_lat))

    volumes: list[dict[str, Any]] = []
    for feature in request_data["flight_declaration_geo_json"].get("features", []):
        props = feature.get("properties", {})
        volumes.append(
            {
                "outline_polygon": feature.get("geometry"),
                "altitude_lower": props.get("min_altitude"),
                "altitude_upper": props.get("max_altitude"),
                "time_start": request_data["start_datetime"],
                "time_end": request_data["end_datetime"],
            }
        )

    partial = {
        "state": "Accepted",
        "priority": 0,
        "volumes": volumes,
        "off_nominal_volumes": [],
    }
    return partial, bounds


async def _create_flight_declaration_record(
    repo: SQLAlchemyFlightDeclarationRepository,
    request_data: dict,
    *,
    response_message: str,
) -> tuple[dict, int]:
    schema = CreateFlightDeclarationRequestSchema()
    try:
        schema.load(request_data)
    except ValidationError as err:
        return {"message": "Validation error", "errors": err.messages}, 400

    geojson_ok, geojson_error = _validate_geojson(request_data["flight_declaration_geo_json"])
    if not geojson_ok:
        return {"message": geojson_error}, 400

    dates_ok, dates_error = _validate_dates(request_data["start_datetime"], request_data["end_datetime"])
    if not dates_ok:
        return {"message": dates_error}, 400

    partial_op_int, bounds = _build_partial_operational_intent(request_data)
    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))
    default_state = 0 if ussp_network_enabled else 1

    created = await repo.create(
        operational_intent=json.dumps(partial_op_int),
        flight_declaration_raw_geojson=json.dumps(request_data["flight_declaration_geo_json"]),
        bounds=bounds,
        aircraft_id=request_data["aircraft_id"],
        state=request_data.get("flight_state", default_state),
        is_approved=bool(request_data.get("flight_approved", False)),
        originating_party=request_data.get("originating_party", "No Flight Information"),
        submitted_by=request_data.get("submitted_by"),
        approved_by=request_data.get("approved_by"),
        start_datetime=arrow.get(request_data["start_datetime"]).datetime,
        end_datetime=arrow.get(request_data["end_datetime"]).datetime,
        type_of_operation=request_data.get("type_of_operation", 0),
    )

    response = FlightDeclarationCreateResponse(
        id=str(created.id),
        message=response_message,
        is_approved=created.is_approved,
        state=created.state,
    )
    return asdict(response), 200


def do_set_operational_intent(request_data: dict) -> tuple[dict, int]:
    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))
    default_state = 0 if ussp_network_enabled else 1
    flight_declaration, error = _validate_and_save_operational_intent(request_data, default_state)
    if error or flight_declaration is None:
        return error or {"message": "Unknown error"}, 400
    intersection_results = _run_deconfliction([flight_declaration], ussp_network_enabled)
    creation_response = _process_intersection_result(flight_declaration, intersection_results[str(flight_declaration.id)], ussp_network_enabled)
    return asdict(creation_response), 200


def do_set_operational_intents_bulk(operational_intents_list: list) -> tuple[dict, int]:
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


def do_network_declarations_by_view(view: str | None) -> tuple[dict, int]:
    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))

    if not view:
        return {"message": "A view bbox is necessary with four values: lat1, lng1, lat2, lng2"}, 400

    try:
        view_port = [float(i) for i in view.split(",")]
    except ValueError:
        return {"message": "A view bbox is necessary with four values: lat1, lng1, lat2, lng2"}, 400

    if not view_port_ops.check_view_port(view_port_coords=view_port):
        return {"message": "An incorrect view port bbox was provided"}, 400

    if not ussp_network_enabled:
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


def do_network_declarations_by_id(flight_declaration_id: str) -> tuple[dict, int]:
    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))
    my_database_reader = FlightBlenderDatabaseReader()

    if not ussp_network_enabled:
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
        return asdict(HTTP400Response(message="Flight declaration has invalid or missing operational intent volumes")), 400

    my_operational_intent_parser = OperationalIntentReferenceHelper()
    all_volumes = [my_operational_intent_parser.parse_volume_to_volume4D(volume=volume) for volume in operational_intent_volumes]

    my_scd_helper = SCDOperations()
    try:
        operational_intent_geojson = my_scd_helper.get_and_process_nearby_operational_intents(volumes=all_volumes)
    except (ValueError, ConnectionError):
        operational_intent_geojson = []

    return operational_intent_geojson, 200


class FlightDeclarationOperations:
    def __init__(self, repo: SQLAlchemyFlightDeclarationRepository):
        self.repo = repo

    async def create_flight_declaration(
        self,
        request_data: dict,
        *,
        response_message: str,
    ) -> tuple[dict, int]:
        return await _create_flight_declaration_record(self.repo, request_data, response_message=response_message)

    async def bulk_create_flight_declarations(
        self,
        flight_declarations_list: list[dict],
    ) -> tuple[dict, int]:
        results: list[dict] = []
        submitted_count = 0
        failed_count = 0

        for idx, item in enumerate(flight_declarations_list):
            data, status_code = await self.create_flight_declaration(
                item,
                response_message="Submitted Flight Declaration",
            )
            if status_code < 400:
                submitted_count += 1
                results.append({"index": idx, "success": True, **data})
            else:
                failed_count += 1
                results.append(
                    {
                        "index": idx,
                        "success": False,
                        "message": data.get("message", "Validation error"),
                        "errors": data.get("errors"),
                    }
                )

        bulk_response = BulkFlightDeclarationCreateResponse(submitted=submitted_count, failed=failed_count, results=results)
        return asdict(bulk_response), (200 if failed_count == 0 else 207)

    async def list_flight_declarations(
        self,
        start_date: str | None,
        end_date: str | None,
        states_raw: str | None,
        page: int,
        page_size: int,
    ) -> tuple[dict, int]:
        present = arrow.now()
        s_date = arrow.get(start_date, "YYYY-MM-DD").floor("day").datetime if start_date else present.shift(days=-1).floor("day").datetime
        e_date = arrow.get(end_date, "YYYY-MM-DD").ceil("day").datetime if end_date else present.shift(days=1).ceil("day").datetime

        states: list[int] | None = None
        if states_raw:
            tokens = [s.strip() for s in states_raw.split(",") if s.strip()]
            if tokens:
                try:
                    states = [int(s) for s in tokens]
                except ValueError:
                    return {"error": "State values must be integers."}, 400

        items = await self.repo.list(start_date=s_date, end_date=e_date, states=states)
        offset = max(page - 1, 0) * page_size
        paged_items = items[offset : offset + page_size]
        data = [self.repo.serialize(item) for item in paged_items]
        return {"count": len(items), "next": None, "previous": None, "results": data}, 200

    async def get_flight_declaration(self, pk: uuid.UUID) -> tuple[dict | None, int]:
        fd = await self.repo.get_by_id(pk)
        if fd is None:
            return None, 404
        return self.repo.serialize(fd), 200

    async def update_flight_declaration_state(self, pk: uuid.UUID, state: int) -> tuple[dict, int]:
        fd = await self.repo.update(pk, state=state)
        if fd is None:
            return {"detail": "Not found"}, 404
        return {"state": fd.state, "submitted_by": fd.submitted_by}, 200

    async def update_flight_declaration_approval(
        self,
        pk: uuid.UUID,
        is_approved: bool,
        approved_by: str | None,
    ) -> tuple[dict, int]:
        fields: dict[str, Any] = {"is_approved": is_approved}
        if approved_by is not None:
            fields["approved_by"] = approved_by
        fd = await self.repo.update(pk, **fields)
        if fd is None:
            return {"detail": "Not found"}, 404
        return {"is_approved": fd.is_approved, "approved_by": fd.approved_by}, 200

    async def delete_flight_declaration(self, declaration_id: uuid.UUID) -> int:
        deleted = await self.repo.delete(declaration_id)
        if not deleted:
            return 404
        return 204

    async def submit_flight_declaration_to_dss(self, pk: uuid.UUID) -> tuple[dict, int]:
        ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))
        if not ussp_network_enabled:
            return {"message": "USSP network is not enabled; DSS submission is only available when USSP_NETWORK_ENABLED=1."}, 400

        flight_declaration = await self.repo.get_by_id(pk)
        if flight_declaration is None:
            return {"message": "Flight declaration not found."}, 404

        if flight_declaration.state != 0:
            return {
                "message": (
                    "Flight declaration is not in 'Not Submitted' state (state=0). "
                    f"Current state: {flight_declaration.state}. Only declarations in state=0 can be submitted to the DSS via this endpoint."
                )
            }, 409

        return {"message": "DSS submission initiated.", "id": str(flight_declaration.id)}, 200
