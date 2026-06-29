import hashlib
import json
import uuid
from math import atan2, cos, radians, sin, sqrt
from typing import Never

import arrow
import httpx
import shapely.geometry
from dacite import from_dict
from geojson import Feature, FeatureCollection, Polygon
from loguru import logger
from shapely.geometry import Polygon as ShapelyPolygon
from shapely.geometry import box as shapely_box

from flight_blender.auth import dss_auth as dss_auth_helper
from flight_blender.auth.token_audience import generate_audience_from_base_url
from flight_blender.domain_types.common import RESPONSE_CONTENT_TYPE
from flight_blender.domain_types.flight_feed import SingleAirtrafficObservation
from flight_blender.domain_types.rid import UASID, OperatorLocation, RIDStreamErrorDetail, UAClassificationEU
from flight_blender.domain_types.rid_operations import (
    IdentificationServiceArea,
    Position,
    RIDAltitude,
    RIDAuthData,
    RIDDisplayDataResponse,
    RIDFlight,
    RIDFlightDetails,
    RIDFlightsRecord,
    RIDPolygon,
    RIDPositions,
    RIDSubscription,
    RIDTime,
    RIDVolume3D,
    RIDVolume4D,
    SubscriptionState,
)
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository
from flight_blender.repositories.flight_feed_repo import SQLAlchemyFlightFeedRepository
from flight_blender.repositories.notifications_repo import SQLAlchemyNotificationsRepository
from flight_blender.repositories.rid_repo import SQLAlchemyRIDRepository

__all__ = [
    "IdentificationServiceArea",
    "Position",
    "RIDAltitude",
    "RIDAuthData",
    "RIDDisplayDataResponse",
    "RIDFlight",
    "RIDFlightDetails",
    "RIDFlightsRecord",
    "RIDPolygon",
    "RIDPositions",
    "RIDSubscription",
    "RIDTime",
    "RIDVolume3D",
    "RIDVolume4D",
    "SubscriptionState",
]

# ── viewport helpers (from rid/view_port_ops.py) ─────────────────────────────


def build_view_port_box(view_port_coords) -> ShapelyPolygon:
    return shapely_box(
        view_port_coords[0],
        view_port_coords[1],
        view_port_coords[2],
        view_port_coords[3],
    )


def build_view_port_box_lng_lat(view_port_coords) -> ShapelyPolygon:
    return shapely_box(
        view_port_coords[1],
        view_port_coords[0],
        view_port_coords[3],
        view_port_coords[2],
    )


def convert_box_to_geojson_feature(box: ShapelyPolygon) -> FeatureCollection:
    geo_json_coordinates = [list(box.exterior.coords)]
    geo_json_polygon = Polygon(coordinates=geo_json_coordinates)
    geo_json_feature = Feature(
        geometry=geo_json_polygon,
        properties={
            "min_altitude": {"meters": 0, "datum": "W84"},
            "max_altitude": {"meters": 120, "datum": "W84"},
        },
    )
    return FeatureCollection(features=[geo_json_feature])


def get_view_port_diagonal_length_kms(view_port_coords) -> float:
    R = 6373.0
    lat1 = radians(min(view_port_coords[0], view_port_coords[2]))
    lon1 = radians(min(view_port_coords[1], view_port_coords[3]))
    lat2 = radians(max(view_port_coords[0], view_port_coords[2]))
    lon2 = radians(max(view_port_coords[1], view_port_coords[3]))
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return R * c


def check_view_port(view_port_coords) -> bool:
    if len(view_port_coords) != 4:
        return False
    lat_min, lat_max = sorted(view_port_coords[::2])
    lng_min, lng_max = sorted(view_port_coords[1::2])
    if not (-90 <= lat_min < 90 and -90 < lat_max <= 90 and -180 <= lng_min < 360 and -180 < lng_max <= 360):
        return False
    return True


def parse_view_bbox(view: str | None) -> list[float] | None:
    if not view:
        return None
    try:
        return [float(i) for i in view.split(",")]
    except Exception:
        return None


def compute_view_hash(view: str) -> int:
    return int(hashlib.sha256(view.encode("utf-8")).hexdigest(), 16) % 10**8


def build_view_port_box_lng_lat_str(view: str) -> ShapelyPolygon:
    view_port = [float(i) for i in view.split(",")]
    return shapely.geometry.box(view_port[1], view_port[0], view_port[3], view_port[2])


def build_vertex_list_from_box(box) -> list[dict]:
    return [{"lng": lng, "lat": lat} for lng, lat in list(zip(*box.exterior.coords.xy))[:-1]]


def make_json_compatible(struct):
    if isinstance(struct, tuple) and hasattr(struct, "_asdict"):
        return {k: make_json_compatible(v) for k, v in struct._asdict().items()}
    if isinstance(struct, dict):
        return {k: make_json_compatible(v) for k, v in struct.items()}
    if isinstance(struct, str):
        return struct
    try:
        return [make_json_compatible(v) for v in struct]
    except TypeError:
        return struct


def deduplicate_observations_by_icao(observations) -> dict:
    unique: dict = {}
    for observation in observations or []:
        unique.setdefault(observation.icao_address, observation)
    return unique


def rid_flight_from_observation(observation) -> RIDFlight:
    recent_paths: list[RIDPositions] = []
    metadata: dict = {}
    try:
        metadata = json.loads(observation.raw_metadata) if observation.raw_metadata else {}
    except Exception as exc:
        logger.error("Error parsing metadata for {}: {}", observation.icao_address, exc)
        metadata = {}

    try:
        recent_positions = metadata.get("recent_positions", [])
        if recent_positions:
            recent_paths.append(
                RIDPositions(
                    positions=[Position(lat=p["position"]["lat"], lng=p["position"]["lng"], alt=p["position"]["alt"]) for p in recent_positions]
                )
            )
    except Exception as exc:
        logger.error("Error parsing recent_positions for {}: {}", observation.icao_address, exc)
        recent_paths = []

    wgs84_alt: float | None = None
    try:
        current_state = metadata.get("current_state", {}) or {}
        position = current_state.get("position", {}) or {}
        if position.get("alt") is not None:
            wgs84_alt = float(position["alt"])
    except Exception:
        wgs84_alt = None
    if wgs84_alt is None:
        wgs84_alt = (observation.altitude_mm or 0) / 1000.0

    return RIDFlight(
        id=observation.icao_address,
        most_recent_position=Position(
            lat=observation.latitude_dd,
            lng=observation.longitude_dd,
            alt=wgs84_alt,
        ),
        recent_paths=recent_paths,
    )


# ── telemetry monitoring (from rid/rid_telemetry_monitoring.py) ───────────────

all_rid_errors = [
    RIDStreamErrorDetail(
        error_code="NET0040",
        error_description="Error in receiving position updates from the aircraft",
    )
]


class FlightTelemetryRIDEngine:
    def __init__(self, session_id: str, db_reader: SQLAlchemyFlightFeedRepository):
        self.session_id = session_id
        self.db_reader: SQLAlchemyFlightFeedRepository = db_reader

    async def check_rid_stream_ok(self) -> tuple[bool, list[Never] | list[RIDStreamErrorDetail]]:
        now = arrow.now()
        four_seconds_before_now = arrow.now().shift(seconds=-4)
        relevant_observations = await self.db_reader.get_active_rid_observations_for_session_between_interval(
            session_id=self.session_id, start_time=four_seconds_before_now, end_time=now
        )

        if not relevant_observations:
            return (True, [])

        errors = []
        for i in range(1, len(relevant_observations)):
            prev_observation = relevant_observations[i - 1]
            current_observation = relevant_observations[i]
            time_diff = (current_observation.created_at - prev_observation.created_at).total_seconds()
            if time_diff != 1:
                errors.append(
                    RIDStreamErrorDetail(
                        error_code="NET0040",
                        error_description=f"NET0040: Timestamp difference error: {time_diff} seconds between observations {i - 1} and {i}",
                    )
                )

        if errors:
            return (False, errors)
        return (True, [])


class USSPollingService:
    """Polls remote USSes for RID flight data and persists observations to the DB."""

    def __init__(self, rid_repo: SQLAlchemyRIDRepository, feed_repo: SQLAlchemyFlightFeedRepository):
        self.rid_repo = rid_repo
        self.feed_repo = feed_repo

    async def query_uss_for_rid_details(self, rid_flight_details_query_url: str, flight_id: uuid.UUID, headers: dict):
        """Queries USS for RID flight details and persists them."""
        flight_details_exist = await self.rid_repo.check_flight_detail_exists(flight_detail_id=flight_id)

        if not flight_details_exist:
            async with httpx.AsyncClient(timeout=30) as client:
                flight_details_request = await client.get(rid_flight_details_query_url, headers=headers)
            if flight_details_request.status_code != 200:
                logger.info("Error in retrieving flight details for %s" % flight_id)
                logger.error(flight_details_request.text)
                return

            _fd_raw = flight_details_request.json()
            fd = _fd_raw["details"]

            logger.info("Retrieved Flight Details for %s" % flight_id)
            operation_description = fd.get("operation_description")
            operator_id = fd.get("operator_id")
            operator_location = None
            if "operator_location" in fd.keys():
                operator_location = from_dict(data_class=OperatorLocation, data=fd["operator_location"])
            auth_data = None
            if "auth_data" in fd.keys():
                auth_data = from_dict(data_class=RIDAuthData, data=fd["auth_data"])
            uas_id = None
            if "uas_id" in fd.keys():
                uas_id = from_dict(data_class=UASID, data=fd["uas_id"])
            eu_classification = None
            if fd.get("eu_classification"):
                eu_classification = from_dict(data_class=UAClassificationEU, data=fd["eu_classification"])

            flight_detail = RIDFlightDetails(
                id=flight_id,
                operation_description=operation_description,
                operator_location=operator_location,
                operator_id=operator_id,
                auth_data=auth_data,
                uas_id=uas_id,
                eu_classification=eu_classification,
            )
            await self.rid_repo.create_or_update_flight_detail(rid_flight_details_payload=flight_detail)

    async def query_uss_for_rid(self, flight_details: str, subscription_id: str, view: str):
        _flight_details = from_dict(data_class=RIDFlightsRecord, data=json.loads(flight_details))

        authority_credentials = dss_auth_helper.AuthorityCredentialsGetter()

        for _service_area in _flight_details.service_areas:
            rid_query_url = _service_area.uss_base_url + "/uss/flights" + "?view=" + view

            audience = generate_audience_from_base_url(base_url=_service_area.uss_base_url)
            auth_credentials = await authority_credentials.get_cached_credentials(audience=audience, token_type="rid")  # nosec B106
            headers = {
                "content-type": RESPONSE_CONTENT_TYPE,
                "Authorization": "Bearer " + auth_credentials["access_token"],
            }
            async with httpx.AsyncClient(timeout=30) as client:
                flights_request = await client.get(rid_query_url, headers=headers)

            if flights_request.status_code == 200:
                flights_response = flights_request.json()
                for flight in flights_response["flights"]:
                    flight_id = flight["id"]
                    rid_flight_details_query_url = f"{_service_area.uss_base_url}/uss/flights/{flight_id}/details"
                    await self.query_uss_for_rid_details(
                        rid_flight_details_query_url=rid_flight_details_query_url,
                        flight_id=uuid.UUID(flight_id),
                        headers=headers,
                    )

                    if flight.get("current_state") is None:
                        logger.error("There is no current_state provided by SP on the flights url %s" % rid_query_url)
                        logger.debug(f"{json.dumps(flight)}")
                    else:
                        flight_current_state = flight["current_state"]
                        position = flight_current_state["position"]
                        recent_positions = flight.get("recent_positions", [])
                        flight_metadata = {
                            "id": flight_id,
                            "simulated": flight["simulated"],
                            "aircraft_type": flight["aircraft_type"],
                            "subscription_id": subscription_id,
                            "current_state": flight_current_state,
                            "recent_positions": recent_positions,
                        }
                        if {"lat", "lng", "alt"} <= position.keys():
                            single_observation = SingleAirtrafficObservation(
                                session_id=subscription_id,
                                icao_address=flight_id,
                                traffic_source=11,
                                source_type=1,
                                lat_dd=position["lat"],
                                lon_dd=position["lng"],
                                altitude_mm=position["alt"],
                                metadata=flight_metadata,
                            )
                            logger.debug("Writing flight remote-id data..")
                            await self.feed_repo.write_flight_observation(single_observation=single_observation)
                        else:
                            logger.error("Error in received flights data: %{url}s ".format(**flight))
            else:
                logger.info("Received a non 200 error from {url} : {status_code} ".format(url=rid_query_url, status_code=flights_request.status_code))
                logger.info("Detailed Response %s" % flights_request.text)


# ── Task helper functions ────────────────────────────────────────────────


async def create_rid_notification(message: str, session_id, repo: SQLAlchemyNotificationsRepository):
    """Create a notification for RID operations."""
    try:
        session_uuid = uuid.UUID(session_id)
    except ValueError, AttributeError:
        session_uuid = None
    await repo.create_notification(message=message, session_id=session_uuid)


async def update_telemetry_timestamp(operation_id, repo: SQLAlchemyFlightDeclarationRepository):
    """Update telemetry timestamp for an operation."""
    await repo.update_telemetry_timestamp(uuid.UUID(operation_id))


async def get_rid_subscription(subscription_id, repo: SQLAlchemyRIDRepository):
    """Get a RID subscription."""
    return await repo.get_subscription_by_id(uuid.UUID(subscription_id))
