import asyncio
import inspect
import json
import uuid
from dataclasses import asdict
from typing import Any

import arrow
import geojson
import shapely.geometry
from geojson import Feature, FeatureCollection, Polygon
from loguru import logger
from marshmallow.exceptions import ValidationError
from pyproj import Geod, Proj
from shapely.geometry import Point, shape
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import box as shapely_box
from shapely.ops import unary_union

from flight_blender.clients.dss_scd_client import OperationalIntentReferenceHelper, SCDOperations
from flight_blender.config import settings
from flight_blender.domain_types.flight_declarations import (
    DEFAULT_UAV_CLIMB_RATE_M_PER_S,
    DEFAULT_UAV_DESCENT_RATE_M_PER_S,
    DEFAULT_UAV_SPEED_M_PER_S,
    BulkFlightDeclarationCreateResponse,
    CreateFlightDeclarationRequestSchema,
    CreateFlightDeclarationViaOperationalIntentRequestSchema,
    DeconflictionRequest,
    FlightDeclarationCreateResponse,
    HTTP400Response,
    HTTP404Response,
    IntersectionCheckResult,
)
from flight_blender.domain_types.plugin_protocols import DeconflictionEngineProtocol
from flight_blender.domain_types.scd import (
    Altitude,
    LatLngPoint,
    OperationalIntentBoundsTimeAltitude,
    OperationalIntentUSSDetails,
    PartialCreateOperationalIntentReference,
    Time,
    Volume3D,
    Volume4D,
)
from flight_blender.domain_types.scd import Polygon as Plgn
from flight_blender.plugins.loader import load_plugin
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository
from flight_blender.tasks.flight_declarations_task import CelerySCDNotifier

FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR = settings.FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR


# ── OperationalIntentsConverter (from flight_declarations/utils.py) ───────────


class OperationalIntentsConverter:
    def __init__(self):
        self.geo_json = {"type": "FeatureCollection", "features": []}
        self.utm_zone = settings.UTM_ZONE
        self.all_features = []

    def generate_bounds_altitude_time_for_volumes(
        self,
        operational_intent_details_payload: OperationalIntentUSSDetails,
        flight_declaration_id: str,
    ) -> OperationalIntentBoundsTimeAltitude:
        all_volumes = operational_intent_details_payload.volumes
        min_altitude = float("inf")
        max_altitude = float("-inf")
        start_time = None
        end_time = None

        for volume in all_volumes:
            if volume.volume.altitude_lower.value < min_altitude:
                min_altitude = volume.volume.altitude_lower.value
            if volume.volume.altitude_upper.value > max_altitude:
                max_altitude = volume.volume.altitude_upper.value
            start_time = min(
                start_time or arrow.get(volume.time_start.value),
                arrow.get(volume.time_start.value),
            )
            end_time = max(
                end_time or arrow.get(volume.time_end.value),
                arrow.get(volume.time_end.value),
            )

        self.convert_operational_intent_to_geo_json(all_volumes)
        bounds = self.get_geo_json_bounds()
        return OperationalIntentBoundsTimeAltitude(
            bounds=bounds,
            alt_min=min_altitude,
            alt_max=max_altitude,
            start_datetime=start_time.isoformat(),
            end_datetime=end_time.isoformat(),
            flight_declaration_id=flight_declaration_id,
        )

    def utm_converter(self, shapely_shape: shapely.geometry.base.BaseGeometry, inverse: bool = False) -> shapely.geometry.base.BaseGeometry:
        zone_str = self.utm_zone.strip()
        zone_num = int("".join(c for c in zone_str if c.isdigit()))
        is_south = zone_str.upper().endswith("S")
        proj = Proj(proj="utm", zone=zone_num, south=is_south, ellps="WGS84", datum="WGS84")

        geo_interface = shapely_shape.__geo_interface__
        point_or_polygon = geo_interface["type"]
        coordinates = geo_interface["coordinates"]

        if point_or_polygon == "Polygon":
            new_coordinates = [[proj(*point, inverse=inverse) for point in linring] for linring in coordinates]
        elif point_or_polygon == "Point":
            new_coordinates = proj(*coordinates, inverse=inverse)
        else:
            raise RuntimeError(f"Unexpected geo_interface type: {point_or_polygon}")

        return shapely.geometry.shape({"type": point_or_polygon, "coordinates": tuple(new_coordinates)})

    def convert_operational_intent_to_geo_json(self, volumes: list[Volume4D]):
        for volume in volumes:
            geo_json_features = self._convert_operational_intent_to_geojson_features(volume)
            _seralized_features = [json.loads(geojson.dumps(feature)) for feature in geo_json_features]
            for _serialized_feature in _seralized_features:
                self.geo_json["features"].append(_serialized_feature)

    def create_partial_operational_intent_ref(
        self,
        start_datetime: str,
        end_datetime: str,
        geo_json_fc: FeatureCollection,
        priority: int,
        state: str = "Accepted",
    ) -> PartialCreateOperationalIntentReference:
        all_v4d = self.convert_geo_json_to_volume_4_d(
            geo_json_fc=geo_json_fc,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
        )
        return PartialCreateOperationalIntentReference(volumes=all_v4d, state=state, priority=priority, off_nominal_volumes=[])

    def parse_volume4ds_to_V4D_list(self, operational_intent_volume4ds: list[dict]) -> list[Volume4D]:
        volume4d_list = []
        for volume_dict in operational_intent_volume4ds:
            volume_3d_dict = volume_dict.get("volume", {})
            outline_polygon_dict = volume_3d_dict.get("outline_polygon")
            outline_circle_dict = volume_3d_dict.get("outline_circle")

            outline_polygon = None
            if outline_polygon_dict:
                vertices = [LatLngPoint(lat=vertex["lat"], lng=vertex["lng"]) for vertex in outline_polygon_dict.get("vertices", [])]
                outline_polygon = Plgn(vertices=vertices)

            outline_circle = None
            if outline_circle_dict:
                center_dict = outline_circle_dict.get("center", {})
                radius_dict = outline_circle_dict.get("radius", {})
                center = LatLngPoint(lat=center_dict.get("lat"), lng=center_dict.get("lng"))
                radius = Altitude(
                    value=radius_dict.get("value"),
                    reference=radius_dict.get("reference"),
                    units=radius_dict.get("units"),
                )
                outline_circle = {"center": center, "radius": radius}

            volume_3d = Volume3D(
                outline_polygon=outline_polygon,
                outline_circle=outline_circle,
                altitude_lower=Altitude(**volume_3d_dict.get("altitude_lower", {})),
                altitude_upper=Altitude(**volume_3d_dict.get("altitude_upper", {})),
            )
            time_start = Time(**volume_dict.get("time_start", {}))
            time_end = Time(**volume_dict.get("time_end", {}))
            volume4d_list.append(Volume4D(volume=volume_3d, time_start=time_start, time_end=time_end))
        return volume4d_list

    def convert_geo_json_to_volume_4_d(self, geo_json_fc: FeatureCollection, start_datetime: str, end_datetime: str) -> list[Volume4D]:
        if FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR:
            CustomVolumeGenerator = load_plugin(FLIGHT_BLENDER_PLUGIN_VOLUME_4D_GENERATOR)
            custom_volume_generator = CustomVolumeGenerator(
                default_uav_speed_m_per_s=DEFAULT_UAV_SPEED_M_PER_S,
                default_uav_climb_rate_m_per_s=DEFAULT_UAV_CLIMB_RATE_M_PER_S,
                default_uav_descent_rate_m_per_s=DEFAULT_UAV_DESCENT_RATE_M_PER_S,
            )
            for feature in geo_json_fc["features"]:
                geom = feature["geometry"]
                shapely_geom = shape(geom)
                buffered_geom = shapely_geom.buffer(0.0005)
                self.all_features.append(buffered_geom)
            all_volumes = custom_volume_generator.build_v4d_from_geojson(
                geo_json_fc=geo_json_fc,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
            self.convert_operational_intent_to_geo_json(all_volumes)
            return all_volumes
        else:
            all_v4d = []
            for feature in geo_json_fc["features"]:
                geom = feature["geometry"]
                max_altitude = feature["properties"]["max_altitude"]["meters"]
                min_altitude = feature["properties"]["min_altitude"]["meters"]
                shapely_geom = shape(geom)
                buffered_geom = shapely_geom.buffer(0.0005)
                self.all_features.append(buffered_geom)
                coordinates = list(zip(*buffered_geom.exterior.coords.xy))
                polygon_vertices = [LatLngPoint(lat=coord[1], lng=coord[0]) for coord in coordinates[:-1]]
                volume_3d = Volume3D(
                    outline_polygon=Plgn(vertices=polygon_vertices),
                    altitude_lower=Altitude(value=min_altitude, reference="W84", units="M"),
                    altitude_upper=Altitude(value=max_altitude, reference="W84", units="M"),
                )
                time_start = feature["properties"].get("start_time", start_datetime)
                time_end = feature["properties"].get("end_time", end_datetime)
                volume_4d = Volume4D(
                    volume=volume_3d,
                    time_start=Time(format="RFC3339", value=time_start),
                    time_end=Time(format="RFC3339", value=time_end),
                )
                all_v4d.append(volume_4d)
            return all_v4d

    def buffer_point_to_volume4d(
        self, lat: float, lng: float, max_altitude: float, min_altitude: float, start_datetime: str, end_datetime: str
    ) -> Volume4D:
        point = Point(lng, lat)
        buffered_shape = point.buffer(0.0001)
        coordinates = list(zip(*buffered_shape.exterior.coords.xy))
        polygon_vertices = [LatLngPoint(lat=coord[1], lng=coord[0]) for coord in coordinates[:-1]]
        volume_3d = Volume3D(
            outline_polygon=Plgn(vertices=polygon_vertices),
            altitude_lower=Altitude(value=min_altitude, reference="W84", units="M"),
            altitude_upper=Altitude(value=max_altitude, reference="W84", units="M"),
        )
        return Volume4D(
            volume=volume_3d,
            time_start=Time(format="RFC3339", value=start_datetime),
            time_end=Time(format="RFC3339", value=end_datetime),
        )

    def get_geo_json_bounds(self) -> str:
        combined_features = unary_union(self.all_features)
        bnd_tuple = combined_features.bounds
        return ",".join([f"{x:.7f}" for x in bnd_tuple])

    def _convert_operational_intent_to_geojson_features(self, volume: Volume4D) -> list[Feature]:
        geo_json_features = []
        volume_dict = asdict(volume.volume)
        time_start = volume.time_start.value
        time_end = volume.time_end.value

        if "outline_polygon" in volume_dict and volume_dict["outline_polygon"] is not None:
            outline_polygon = volume_dict["outline_polygon"]
            point_list = [Point(vertex["lng"], vertex["lat"]) for vertex in outline_polygon["vertices"]]
            outline_polygon = ShapelyPolygon([[p.x, p.y] for p in point_list])
            self.all_features.append(outline_polygon)
            oriented_polygon = shapely.geometry.polygon.orient(outline_polygon)
            outline_polygon_geojson = shapely.geometry.mapping(oriented_polygon)
            polygon_feature = Feature(
                properties={"time_start": time_start, "time_end": time_end},
                geometry=outline_polygon_geojson,
            )
            geo_json_features.append(polygon_feature)

        if "outline_circle" in volume_dict and volume_dict["outline_circle"] is not None:
            outline_circle = volume_dict["outline_circle"]
            circle_radius = outline_circle["radius"]["value"]
            center_point = Point(outline_circle["center"]["lng"], outline_circle["center"]["lat"])
            utm_center = self.utm_converter(shapely_shape=center_point)
            buffered_circle = utm_center.buffer(circle_radius)
            converted_circle = self.utm_converter(buffered_circle, inverse=True)
            self.all_features.append(converted_circle)
            outline_circle_geojson = shapely.geometry.mapping(converted_circle)
            circle_feature = Feature(
                properties={"time_start": time_start, "time_end": time_end},
                geometry=outline_circle_geojson,
            )
            geo_json_features.append(circle_feature)

        return geo_json_features


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
    return load_plugin(engine_path, expected_protocol=DeconflictionEngineProtocol)


def _validate_geojson(fc: dict) -> tuple[bool, str | None]:
    features = fc.get("features", [])
    if not features:
        return False, "Flight declaration GeoJSON is required."
    for feature in features:
        geometry = feature.get("geometry")
        props = feature.get("properties", {})
        shp = shape(geometry)
        if not shp.is_valid:
            return (
                False,
                "Error in processing the submitted GeoJSON: every Feature in a GeoJSON FeatureCollection must have a valid geometry, please check your submitted FeatureCollection",
            )
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
    ussp_network_enabled = settings.USSP_NETWORK_ENABLED
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


async def do_network_declarations_by_view(
    view: str | None,
    scd_client: SCDOperations,
) -> tuple[dict, int]:
    ussp_network_enabled = settings.USSP_NETWORK_ENABLED

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

    try:
        operational_intent_geojson = await scd_client.get_and_process_nearby_operational_intents(volumes=volumes)
    except (ValueError, ConnectionError):
        operational_intent_geojson = []

    return operational_intent_geojson, 200


class FlightDeclarationOperations:
    def __init__(
        self,
        repo: SQLAlchemyFlightDeclarationRepository,
        scd_client: SCDOperations,
        parser: OperationalIntentReferenceHelper,
        notifier: CelerySCDNotifier,
    ):
        self.repo = repo
        self.scd_client = scd_client
        self.parser = parser
        self.notifier = notifier

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

    async def validate_bulk_declarations_payload(self, body: Any) -> tuple[dict, int] | None:
        if not isinstance(body, list):
            return {"message": "Request body must be a JSON array of flight declaration objects."}, 400
        return None

    async def validate_bulk_operational_intents_payload(self, body: Any) -> tuple[dict, int] | None:
        if not isinstance(body, list):
            return {"message": "Request body must be a JSON array of operational intent objects."}, 400
        return None

    async def update_approval_from_request(
        self,
        pk: uuid.UUID,
        body: dict,
    ) -> tuple[dict, int]:
        is_approved = body.get("is_approved")
        approved_by = body.get("approved_by")
        if is_approved is None:
            return {"detail": "is_approved is required"}, 422
        return await self.update_flight_declaration_approval(pk, bool(is_approved), approved_by)

    async def update_state_from_request(
        self,
        pk: uuid.UUID,
        body: dict,
    ) -> tuple[dict, int]:
        state = body.get("state")
        if state is None:
            return {"detail": "state is required"}, 422
        try:
            state_int = int(state)
        except (TypeError, ValueError):
            return {"detail": "state must be an integer"}, 422
        return await self.update_flight_declaration_state(pk, state_int)

    async def submit_flight_declaration_to_dss(self, pk: uuid.UUID) -> tuple[dict, int]:
        ussp_network_enabled = settings.USSP_NETWORK_ENABLED
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
        ussp_network_enabled = settings.USSP_NETWORK_ENABLED
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
        ussp_network_enabled = settings.USSP_NETWORK_ENABLED
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

    async def _process_intersection_result_sa(
        self,
        fd: Any,
        intersection_result: IntersectionCheckResult,
        ussp_network_enabled: int,
    ) -> FlightDeclarationCreateResponse:
        notifier: CelerySCDNotifier = self.notifier

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
        notifier.send_operational_update_message(
            flight_declaration_id=flight_declaration_id,
            message_text="Flight Declaration created..",
            level="info",
        )

        if all_relevant_fences and all_relevant_declarations:
            self_deconfliction_failed_msg = f"Self deconfliction failed for operation {flight_declaration_id} did not pass self-deconfliction, there are existing operations declared in the area"
            notifier.send_operational_update_message(
                flight_declaration_id=flight_declaration_id,
                message_text=self_deconfliction_failed_msg,
                level="error",
            )

        auto_submit_to_dss = settings.AUTO_SUBMIT_TO_DSS
        if is_approved and declaration_state == 0 and ussp_network_enabled and auto_submit_to_dss:
            notifier.submit_flight_declaration_to_dss_async(flight_declaration_id=flight_declaration_id)

        return FlightDeclarationCreateResponse(
            id=flight_declaration_id,
            message="Submitted Flight Declaration",
            is_approved=is_approved,
            state=declaration_state,
        )

    async def get_network_declarations_by_id(self, flight_declaration_id: str) -> tuple[dict, int]:
        ussp_network_enabled = settings.USSP_NETWORK_ENABLED

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

        all_volumes = [self.parser.parse_volume_to_volume4D(volume=volume) for volume in operational_intent_volumes]

        try:
            operational_intent_geojson = await self.scd_client.get_and_process_nearby_operational_intents(volumes=all_volumes)
        except (ValueError, ConnectionError):
            operational_intent_geojson = []

        return operational_intent_geojson, 200


# ── CustomVolumeGenerator (from flight_declarations/custom_volume_generation.py) ─


class CustomVolumeGenerator:
    def __init__(
        self,
        default_uav_speed_m_per_s: float,
        default_uav_climb_rate_m_per_s: float,
        default_uav_descent_rate_m_per_s: float,
    ):
        self.default_uav_speed_m_per_s = default_uav_speed_m_per_s
        self.default_uav_climb_rate_m_per_s = default_uav_climb_rate_m_per_s
        self.default_uav_descent_rate_m_per_s = default_uav_descent_rate_m_per_s
        self.all_features = []

    def _break_linestring_to_smaller_pieces(self, line_feature: Feature, piece_length_m: float = 5.5) -> list[Feature]:
        geod = Geod(ellps="WGS84")
        line_coords = line_feature["geometry"]["coordinates"]
        if len(line_coords) < 2:
            return [line_feature]

        pieces = []
        current_piece = [line_coords[0]]
        current_length = 0.0
        i = 1

        while i < len(line_coords):
            start_point = current_piece[-1]
            end_point = line_coords[i]
            az12, az21, dist = geod.inv(start_point[0], start_point[1], end_point[0], end_point[1])

            if current_length + dist <= piece_length_m:
                current_piece.append(end_point)
                current_length += dist
                i += 1
            else:
                remaining = piece_length_m - current_length
                lon2, lat2, az = geod.fwd(start_point[0], start_point[1], az12, remaining)
                interp_point = [lon2, lat2]
                current_piece.append(interp_point)
                pieces.append(current_piece)
                current_piece = [interp_point]
                current_length = 0.0

        if current_piece:
            pieces.append(current_piece)

        new_features = []
        for piece in pieces:
            new_feature = Feature(
                geometry={"type": "LineString", "coordinates": piece},
                properties=line_feature["properties"],
            )
            new_features.append(new_feature)
        logger.info(f"Broken into {len(new_features)} pieces.")
        return new_features

    def build_v4d_from_geojson(self, geo_json_fc: FeatureCollection, start_datetime: str, end_datetime: str) -> list[Volume4D]:
        feature_types = set(feature["geometry"]["type"] for feature in geo_json_fc["features"])
        if len(feature_types) == 1 and "LineString" in feature_types:
            collection_type = "all_linesstrings"
        elif len(feature_types) == 1 and "Polygon" in feature_types:
            collection_type = "all_polygons"
        else:
            collection_type = "linestrings_and_polygons"

        if collection_type == "all_linesstrings":
            return self.build_v4d_from_linestrings(
                geo_json_fc=geo_json_fc,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )
        else:
            return self.build_v4d_from_mixed_polygons_and_linestrings(
                geo_json_fc=geo_json_fc,
                start_datetime=start_datetime,
                end_datetime=end_datetime,
            )

    def build_v4d_from_mixed_polygons_and_linestrings(self, geo_json_fc: FeatureCollection, start_datetime: str, end_datetime: str) -> list[Volume4D]:
        all_v4d = []
        for feature in geo_json_fc["features"]:
            geom = feature["geometry"]
            max_altitude = feature["properties"]["max_altitude"]["meters"]
            min_altitude = feature["properties"]["min_altitude"]["meters"]
            shapely_geom = shape(geom)
            buffered_geom = shapely_geom.buffer(0.0005)
            self.all_features.append(buffered_geom)

            coordinates = list(zip(*buffered_geom.exterior.coords.xy))
            polygon_vertices = [LatLngPoint(lat=coord[1], lng=coord[0]) for coord in coordinates[:-1]]

            volume_3d = Volume3D(
                outline_polygon=Plgn(vertices=polygon_vertices),
                altitude_lower=Altitude(value=min_altitude, reference="W84", units="M"),
                altitude_upper=Altitude(value=max_altitude, reference="W84", units="M"),
            )

            time_start = feature["properties"].get("start_time", start_datetime)
            time_end = feature["properties"].get("end_time", end_datetime)

            volume_4d = Volume4D(
                volume=volume_3d,
                time_start=Time(format="RFC3339", value=time_start),
                time_end=Time(format="RFC3339", value=time_end),
            )

            all_v4d.append(volume_4d)

        return all_v4d

    def build_v4d_from_linestrings(self, geo_json_fc: FeatureCollection, start_datetime: str, end_datetime: str) -> list[Volume4D]:
        geo_json_features = geo_json_fc["features"]
        geo_json_features.sort(key=lambda x: x["properties"].get("id", 0))
        geo_json_fc["features"] = geo_json_features
        all_v4d = []
        _takeoff_start = arrow.get(start_datetime).shift(seconds=1).isoformat()
        _landing_time = arrow.get(end_datetime).shift(seconds=-1).isoformat()

        first_feature = geo_json_fc["features"][0]
        last_feature = geo_json_fc["features"][-1]
        first_coord = first_feature["geometry"]["coordinates"][0]
        last_coord = last_feature["geometry"]["coordinates"][-1]
        takeoff_location = LatLngPoint(lat=first_coord[1], lng=first_coord[0])
        landing_location = LatLngPoint(lat=last_coord[1], lng=last_coord[0])

        max_altitude = first_feature["properties"]["max_altitude"]["meters"]
        min_altitude = first_feature["properties"]["min_altitude"]["meters"]

        takeoff_volume_4d = self._create_buffered_volume_4d(
            point=takeoff_location,
            max_altitude=max_altitude,
            min_altitude=min_altitude,
            time_start=start_datetime,
            time_end=_takeoff_start,
        )
        all_v4d.append(takeoff_volume_4d)

        landing_volume_4d = self._create_buffered_volume_4d(
            point=landing_location,
            max_altitude=max_altitude,
            min_altitude=min_altitude,
            time_start=_landing_time,
            time_end=end_datetime,
        )
        all_v4d.append(landing_volume_4d)

        for feature in geo_json_fc["features"]:
            max_altitude = feature["properties"]["max_altitude"]["meters"]
            min_altitude = feature["properties"]["min_altitude"]["meters"]

            broken_down_features = self._break_linestring_to_smaller_pieces(line_feature=feature, piece_length_m=self.default_uav_speed_m_per_s * 3)

            climb_time_s = abs(max_altitude - min_altitude) / self.default_uav_climb_rate_m_per_s

            for idx, piece in enumerate(broken_down_features):
                piece_start_time = arrow.get(_takeoff_start).shift(seconds=int(idx * 3 + climb_time_s)).isoformat()
                piece_end_time = arrow.get(piece_start_time).shift(seconds=3).isoformat()

                piece_geom = piece["geometry"]
                shapely_piece_geom = shape(piece_geom)
                buffered_shape = shapely_piece_geom.buffer(0.0001)

                coordinates = list(zip(*buffered_shape.exterior.coords.xy))
                polygon_vertices = [LatLngPoint(lat=coord[1], lng=coord[0]) for coord in coordinates[:-1]]

                volume_3d = Volume3D(
                    outline_polygon=Plgn(vertices=polygon_vertices),
                    altitude_lower=Altitude(value=min_altitude, reference="W84", units="M"),
                    altitude_upper=Altitude(value=max_altitude, reference="W84", units="M"),
                )

                volume_4d = Volume4D(
                    volume=volume_3d,
                    time_start=Time(format="RFC3339", value=piece_start_time),
                    time_end=Time(format="RFC3339", value=piece_end_time),
                )
                all_v4d.append(volume_4d)

        if all_v4d and all_v4d[-1].time_end.value != _landing_time:
            logger.warning(f"Piece end time {all_v4d[-1].time_end.value} does not match landing time {_landing_time}")
            logger.info("The landing time has been changed and is computed using the default UAV speed.")

        return all_v4d

    def _create_buffered_volume_4d(self, point: LatLngPoint, max_altitude: float, min_altitude: float, time_start: str, time_end: str) -> Volume4D:
        shapely_point = Point(point.lng, point.lat)
        buffered_geom = shapely_point.buffer(0.0005)
        coordinates = list(zip(*buffered_geom.exterior.coords.xy))
        polygon_vertices = [LatLngPoint(lat=coord[1], lng=coord[0]) for coord in coordinates[:-1]]

        volume_3d = Volume3D(
            outline_polygon=Plgn(vertices=polygon_vertices),
            altitude_lower=Altitude(value=min_altitude, reference="W84", units="M"),
            altitude_upper=Altitude(value=max_altitude, reference="W84", units="M"),
        )

        return Volume4D(
            volume=volume_3d,
            time_start=Time(format="RFC3339", value=time_start),
            time_end=Time(format="RFC3339", value=time_end),
        )
