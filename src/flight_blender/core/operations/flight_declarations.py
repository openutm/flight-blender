import json
import uuid
from dataclasses import asdict
from os import environ as env
from typing import Any

import arrow
import asyncio
from geojson import Feature, FeatureCollection, Polygon
from loguru import logger
from marshmallow.exceptions import ValidationError
from shapely.geometry import box as shapely_box
from shapely.geometry import shape
from shapely.ops import unary_union

from flight_blender.config import settings
from flight_blender.core.entities.flight_declarations import (
    BulkFlightDeclarationCreateResponse,
    CreateFlightDeclarationRequestSchema,
    CreateFlightDeclarationViaOperationalIntentRequestSchema,
    DeconflictionRequest,
    FlightDeclarationCreateResponse,
    HTTP400Response,
    HTTP404Response,
    IntersectionCheckResult,
)
from flight_blender.core.repositories.flight_declarations import FlightDeclarationRepository
from flight_blender.flight_declarations.deconfliction_protocol import DeconflictionEngine
from flight_blender.flight_declarations.utils import OperationalIntentsConverter
from flight_blender.plugins.loader import load_plugin


def _check_view_port(view_port_coords: list[float]) -> bool:
    if len(view_port_coords) != 4:
        return False

    lat_min, lat_max = sorted(view_port_coords[::2])
    lng_min, lng_max = sorted(view_port_coords[1::2])
    return -90 <= lat_min < 90 and -90 < lat_max <= 90 and -180 <= lng_min < 360 and -180 < lng_max <= 360


def _build_view_port_box_lng_lat(view_port_coords: list[float]):
    return shapely_box(
        view_port_coords[1],
        view_port_coords[0],
        view_port_coords[3],
        view_port_coords[2],
    )


def _convert_box_to_geojson_feature(box) -> FeatureCollection:
    geo_json_polygon = Polygon(coordinates=[list(box.exterior.coords)])
    geo_json_feature = Feature(
        geometry=geo_json_polygon,
        properties={
            "min_altitude": {"meters": 0, "datum": "W84"},
            "max_altitude": {"meters": 120, "datum": "W84"},
        },
    )
    return FeatureCollection(features=[geo_json_feature])


def _get_deconfliction_engine():
    engine_path = settings.FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE
    if not engine_path:
        return None
    return load_plugin(engine_path, expected_protocol=DeconflictionEngine)


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
    repo: FlightDeclarationRepository,
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

    provided_state = request_data.get("flight_state", default_state)
    # TODO: Should this check flight_approved?
    is_approved = provided_state not in (0, 8)

    created = await repo.create(
        operational_intent=json.dumps(partial_op_int),
        flight_declaration_raw_geojson=json.dumps(request_data["flight_declaration_geo_json"]),
        bounds=bounds,
        aircraft_id=request_data["aircraft_id"],
        state=provided_state,
        is_approved=is_approved,
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


def _run_deconfliction_sa(
    flight_declarations: list[Any],
    ussp_network_enabled: int,
) -> dict[str, IntersectionCheckResult]:
    if not flight_declarations:
        return {}
    results: dict[str, IntersectionCheckResult] = {}
    for fd in flight_declarations:
        view_box = [float(i) for i in fd.bounds.split(",")]
        raw_geojson = fd.flight_declaration_raw_geojson
        flight_declaration_geo_json = json.loads(raw_geojson) if raw_geojson else None
        request = DeconflictionRequest(
            start_datetime=fd.start_datetime,
            end_datetime=fd.end_datetime,
            view_box=view_box,
            ussp_network_enabled=ussp_network_enabled,
            declaration_id=str(fd.id),
            flight_declaration_geo_json=flight_declaration_geo_json,
            type_of_operation=fd.type_of_operation,
            priority=0,
        )
        engine_cls = _get_deconfliction_engine()
        if engine_cls is None:
            logger.warning("No deconfliction engine configured; skipping deconfliction for %s", fd.id)
            continue
        engine = engine_cls()
        result = engine.check_deconfliction(request)
        results[str(fd.id)] = result
    return results


def do_network_declarations_by_view(view: str | None) -> tuple[dict, int]:
    from flight_blender.infrastructure.dss.scd import SCDOperations  # noqa: PLC0415

    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))

    if not view:
        return {"message": "A view bbox is necessary with four values: lat1, lng1, lat2, lng2"}, 400

    try:
        view_port = [float(i) for i in view.split(",")]
    except ValueError:
        return {"message": "A view bbox is necessary with four values: lat1, lng1, lat2, lng2"}, 400

    if not _check_view_port(view_port_coords=view_port):
        return {"message": "An incorrect view port bbox was provided"}, 400

    if not ussp_network_enabled:
        return asdict(HTTP400Response(message="USSP network cannot be queried since it is not enabled in Flight Blender")), 400

    start_datetime = arrow.now().shift(minutes=-1).isoformat()
    end_datetime = arrow.now().shift(minutes=10).isoformat()
    view_port_box = _build_view_port_box_lng_lat(view_port_coords=view_port)
    converted_geo_json = _convert_box_to_geojson_feature(box=view_port_box)

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


class FlightDeclarationOperations:
    def __init__(self, repo: FlightDeclarationRepository):
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

    async def set_operational_intent(self, request_data: dict) -> tuple[dict, int]:
        ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))
        default_state = 0 if ussp_network_enabled else 1

        schema = CreateFlightDeclarationViaOperationalIntentRequestSchema()
        try:
            schema.load(request_data)
        except ValidationError as err:
            return {"message": "Validation error", "errors": err.messages}, 400

        operational_intent_volume4ds = request_data.get("operational_intent_volume4ds")
        my_operational_intent_converter = OperationalIntentsConverter()
        parsed_operational_intent = my_operational_intent_converter.parse_volume4ds_to_V4D_list(operational_intent_volume4ds)
        _serialized_operational_intent = [asdict(v4d) for v4d in parsed_operational_intent]
        my_operational_intent_converter.convert_operational_intent_to_geo_json(volumes=parsed_operational_intent)
        flight_declaration_geo_json = my_operational_intent_converter.geo_json

        start_datetime = request_data.get("start_datetime", arrow.now().isoformat())
        end_datetime = request_data.get("end_datetime", arrow.now().isoformat())
        dates_ok, dates_error = _validate_dates(start_datetime, end_datetime)
        if not dates_ok:
            return {"message": dates_error}, 400

        bounds = my_operational_intent_converter.get_geo_json_bounds()
        fd = await self.repo.create(
            operational_intent=json.dumps(_serialized_operational_intent),
            bounds=bounds,
            type_of_operation=request_data.get("type_of_operation", 0),
            aircraft_id=request_data["aircraft_id"],
            submitted_by=request_data.get("submitted_by"),
            is_approved=True,
            start_datetime=arrow.get(start_datetime).datetime,
            end_datetime=arrow.get(end_datetime).datetime,
            originating_party=request_data.get("originating_party", "No Flight Information"),
            flight_declaration_raw_geojson=json.dumps(flight_declaration_geo_json),
            state=default_state,
        )
        await self.repo.add_state_history_entry(fd.id, 0, default_state, "Created Declaration")

        intersection_results = await asyncio.to_thread(_run_deconfliction_sa, [fd], ussp_network_enabled)
        creation_response = await self._process_intersection_result_sa(fd, intersection_results[str(fd.id)], ussp_network_enabled)
        return asdict(creation_response), 200

    async def set_operational_intents_bulk(self, operational_intents_list: list) -> tuple[dict, int]:
        ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))
        default_state = 0 if ussp_network_enabled else 1

        saved: dict[int, Any] = {}
        results: list[dict] = []
        failed_count = 0

        for idx, item in enumerate(operational_intents_list):
            schema = CreateFlightDeclarationViaOperationalIntentRequestSchema()
            try:
                schema.load(item)
            except ValidationError as err:
                failed_count += 1
                results.append({"index": idx, "success": False, "message": "Validation error", "errors": err.messages})
                continue

            try:
                operational_intent_volume4ds = item.get("operational_intent_volume4ds")
                my_operational_intent_converter = OperationalIntentsConverter()
                parsed_operational_intent = my_operational_intent_converter.parse_volume4ds_to_V4D_list(operational_intent_volume4ds)
                _serialized_operational_intent = [asdict(v4d) for v4d in parsed_operational_intent]
                my_operational_intent_converter.convert_operational_intent_to_geo_json(volumes=parsed_operational_intent)
                flight_declaration_geo_json = my_operational_intent_converter.geo_json

                start_datetime = item.get("start_datetime", arrow.now().isoformat())
                end_datetime = item.get("end_datetime", arrow.now().isoformat())
                dates_ok, dates_error = _validate_dates(start_datetime, end_datetime)
                if not dates_ok:
                    failed_count += 1
                    results.append({"index": idx, "success": False, "message": dates_error})
                    continue

                bounds = my_operational_intent_converter.get_geo_json_bounds()
                fd = await self.repo.create(
                    operational_intent=json.dumps(_serialized_operational_intent),
                    bounds=bounds,
                    type_of_operation=item.get("type_of_operation", 0),
                    aircraft_id=item["aircraft_id"],
                    submitted_by=item.get("submitted_by"),
                    is_approved=True,
                    start_datetime=arrow.get(start_datetime).datetime,
                    end_datetime=arrow.get(end_datetime).datetime,
                    originating_party=item.get("originating_party", "No Flight Information"),
                    flight_declaration_raw_geojson=json.dumps(flight_declaration_geo_json),
                    state=default_state,
                )
                await self.repo.add_state_history_entry(fd.id, 0, default_state, "Created Declaration")
                saved[idx] = fd
            except Exception as exc:
                logger.error(f"Error at index {idx}: {exc}")
                failed_count += 1
                results.append({"index": idx, "success": False, "message": str(exc)})

        intersection_results = await asyncio.to_thread(_run_deconfliction_sa, list(saved.values()), ussp_network_enabled)
        submitted_count = 0
        for idx, fd in saved.items():
            creation_response = await self._process_intersection_result_sa(fd, intersection_results[str(fd.id)], ussp_network_enabled)
            submitted_count += 1
            results.append({
                "index": idx,
                "success": True,
                "id": creation_response.id,
                "is_approved": creation_response.is_approved,
                "state": creation_response.state,
            })

        results.sort(key=lambda r: r["index"])
        bulk_response = BulkFlightDeclarationCreateResponse(submitted=submitted_count, failed=failed_count, results=results)
        http_status = 200 if failed_count == 0 else 207
        return asdict(bulk_response), http_status

    async def _process_intersection_result_sa(
        self,
        fd: Any,
        intersection_result: IntersectionCheckResult,
        ussp_network_enabled: int,
    ) -> FlightDeclarationCreateResponse:
        from flight_blender.infrastructure.celery.tasks.flight_declarations import send_operational_update_message, submit_flight_declaration_to_dss_async  # noqa: PLC0415

        is_approved = intersection_result.is_approved
        declaration_state = intersection_result.declaration_state
        all_relevant_fences = intersection_result.all_relevant_fences
        all_relevant_declarations = intersection_result.all_relevant_declarations

        if not is_approved:
            original_state = fd.state
            updated_fd = await self.repo.update(fd.id, is_approved=False, state=declaration_state)
            fd = updated_fd or fd
            await self.repo.add_state_history_entry(
                fd.id,
                original_state,
                declaration_state,
                "Rejected by Flight Blender because of time/space conflicts with existing operations",
            )

        flight_declaration_id = str(fd.id)
        send_operational_update_message.delay(
            flight_declaration_id=flight_declaration_id,
            message_text="Flight Declaration created..",
            level="info",
        )

        if all_relevant_fences and all_relevant_declarations:
            self_deconfliction_failed_msg = f"Self deconfliction failed for operation {flight_declaration_id} did not pass self-deconfliction, there are existing operations declared in the area"
            send_operational_update_message.delay(
                flight_declaration_id=flight_declaration_id,
                message_text=self_deconfliction_failed_msg,
                level="error",
            )

        auto_submit_to_dss = int(env.get("AUTO_SUBMIT_TO_DSS", 1))
        if is_approved and declaration_state == 0 and ussp_network_enabled and auto_submit_to_dss:
            submit_flight_declaration_to_dss_async.delay(flight_declaration_id=flight_declaration_id)

        return FlightDeclarationCreateResponse(
            id=flight_declaration_id,
            message="Submitted Flight Declaration",
            is_approved=is_approved,
            state=declaration_state,
        )

    async def get_network_declarations_by_id(self, flight_declaration_id: str) -> tuple[dict, int]:
        from flight_blender.infrastructure.dss.scd import OperationalIntentReferenceHelper, SCDOperations  # noqa: PLC0415

        ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", "0"))

        if not ussp_network_enabled:
            return asdict(HTTP400Response(message="USSP network cannot be queried since it is not enabled in Flight Blender")), 400

        try:
            fd_uuid = uuid.UUID(flight_declaration_id)
        except ValueError:
            return asdict(HTTP404Response(message=f"Flight Declaration with ID {flight_declaration_id} not found")), 404

        fd = await self.repo.get_by_id(fd_uuid)
        if fd is None:
            return asdict(HTTP404Response(message=f"Flight Declaration with ID {flight_declaration_id} not found")), 404

        if fd.state not in [0, 1, 2, 3, 4]:
            return asdict(HTTP400Response(message="USSP network can only be queried for operational intents that are active")), 400

        try:
            operational_intent_volumes_raw = json.loads(fd.operational_intent)
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
