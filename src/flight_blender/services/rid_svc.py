import enum
import hashlib
import json
from dataclasses import asdict, dataclass
from math import atan2, cos, radians, sin, sqrt
from typing import Literal, Never

import arrow
import shapely.geometry
from geojson import Feature, FeatureCollection, Polygon
from implicitdict import StringBasedDateTime
from loguru import logger
from shapely.geometry import box as shapely_box

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
from flight_blender.repositories.flight_feed_repo import SQLAlchemyFlightFeedRepository

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


def build_view_port_box(view_port_coords) -> shapely_box:
    return shapely_box(
        view_port_coords[0],
        view_port_coords[1],
        view_port_coords[2],
        view_port_coords[3],
    )


def build_view_port_box_lng_lat(view_port_coords) -> shapely_box:
    return shapely_box(
        view_port_coords[1],
        view_port_coords[0],
        view_port_coords[3],
        view_port_coords[2],
    )


def convert_box_to_geojson_feature(box: shapely_box) -> FeatureCollection:
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


def build_view_port_box_lng_lat_str(view: str) -> shapely_box:
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


class RIDCapabilitiesResponseEnum(str, enum.Enum):
    ASTMRID2019 = "ASTMRID2019"
    ASTMRID2022 = "ASTMRID2022"


@dataclass
class RIDCapabilitiesResponse:
    capabilities: list[
        Literal[
            RIDCapabilitiesResponseEnum.ASTMRID2019,
            RIDCapabilitiesResponseEnum.ASTMRID2022,
        ]
    ]


@dataclass
class RIDHeight:
    distance: float
    reference: str


@dataclass
class RIDAircraftPosition:
    lat: float
    lng: float
    alt: float
    accuracy_h: str
    accuracy_v: str
    extrapolated: bool | None
    pressure_altitude: float | None
    height: RIDHeight | None


@dataclass
class AuthData:
    format: int
    data: str | None = ""


@dataclass
class OperatorAltitude:
    altitude: int
    altitude_type: str


@dataclass
class RIDOperatorDetails:
    id: str
    operator_id: str | None
    operator_location: OperatorLocation | None
    operation_description: str | None
    auth_data: RIDAuthData | None
    serial_number: str | None
    registration_number: str | None
    aircraft_type: str | None = None
    eu_classification: UAClassificationEU | None = None
    uas_id: UASID | None = None


@dataclass
class RIDTestDetailsResponse:
    effective_after: str
    details: RIDFlightDetails


@dataclass
class HTTPErrorResponse:
    message: str
    status: int


@dataclass
class RIDAircraftState:
    timestamp: RIDTime
    timestamp_accuracy: float
    speed_accuracy: str
    position: RIDAircraftPosition
    operational_status: str | None = None
    track: float | None = None
    speed: float | None = None
    vertical_speed: float | None = None
    height: RIDHeight | None = None

    def as_dict(self):
        data = asdict(self)
        return {key: value for key, value in data.items() if value is not None}


@dataclass
class RIDRecentAircraftPosition:
    time: StringBasedDateTime
    position: RIDAircraftPosition


@dataclass
class FullRequestedFlightDetails:
    id: str
    telemetry_length: int


@dataclass
class TelemetryFlightDetails:
    id: str
    aircraft_type: str
    current_state: RIDAircraftState
    simulated: bool
    recent_positions: list[RIDRecentAircraftPosition]
    operator_details: RIDOperatorDetails


@dataclass
class RIDFlightResponse:
    timestamp: RIDTime
    flights: list[TelemetryFlightDetails]


@dataclass
class SingleObservationMetadata:
    details_response: RIDTestDetailsResponse
    telemetry: RIDAircraftState
    aircraft_type: str
    injection_id: str


@dataclass
class RIDTestInjection:
    aircraft_type: str
    injection_id: str
    telemetry: list[RIDAircraftState]
    details_responses: list[RIDTestDetailsResponse]


@dataclass
class RIDTestDataStorage:
    flight_state: RIDAircraftState
    details_response: RIDTestDetailsResponse
    aircraft_type: str
    injection_id: str


@dataclass
class CreateTestPayload:
    requested_flights: list[RIDTestInjection]
    test_id: str


@dataclass
class CreateTestResponse:
    injected_flights: list[RIDTestInjection]
    version: int
