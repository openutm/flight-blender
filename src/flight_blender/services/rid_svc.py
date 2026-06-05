import enum
from dataclasses import asdict, dataclass
from math import atan2, cos, radians, sin, sqrt
from typing import Literal, Never

import arrow
from geojson import Feature, FeatureCollection, Polygon
from implicitdict import StringBasedDateTime
from shapely.geometry import box as shapely_box

from flight_blender.domain_types.rid import UASID, OperatorLocation, RIDStreamErrorDetail, UAClassificationEU
from flight_blender.domain_types.rid_operations import RIDAuthData, RIDFlightDetails, RIDTime
from flight_blender.repositories.flight_feed_repo import SyncFlightFeedReader

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


# ── telemetry monitoring (from rid/rid_telemetry_monitoring.py) ───────────────

all_rid_errors = [
    RIDStreamErrorDetail(
        error_code="NET0040",
        error_description="Error in receiving position updates from the aircraft",
    )
]


class FlightTelemetryRIDEngine:
    def __init__(self, session_id: str, db_reader: SyncFlightFeedReader):
        self.session_id = session_id
        self.db_reader: SyncFlightFeedReader = db_reader

    def check_rid_stream_ok(self) -> tuple[bool, list[Never] | list[RIDStreamErrorDetail]]:
        now = arrow.now()
        four_seconds_before_now = arrow.now().shift(seconds=-4)
        relevant_observations = self.db_reader.get_active_rid_observations_for_session_between_interval(
            session_id=self.session_id, start_time=four_seconds_before_now, end_time=now
        )

        if not relevant_observations:
            return (True, [])

        errors = []
        for i in range(1, len(relevant_observations)):
            prev_observation = relevant_observations[i - 1]
            current_observation = relevant_observations[i]
            time_diff = (current_observation.timestamp - prev_observation.timestamp).total_seconds()
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
