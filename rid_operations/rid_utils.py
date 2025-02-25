import enum
import json
from dataclasses import asdict, dataclass, field
from typing import List, Literal, NamedTuple, Optional, Tuple, Union

import arrow
from arrow.parser import ParserError
from implicitdict import StringBasedDateTime

from auth_helper.common import get_redis
from scd_operations.scd_data_definitions import Volume4D

from .data_definitions import UASID, UAClassificationEU


@dataclass
class RIDTime:
    value: str
    format: str


@dataclass
class LatLngPoint:
    lat: float
    lng: float


class Position(NamedTuple):
    """A class to hold most recent position for remote id data"""

    lat: float
    lng: float
    alt: float


class RIDPositions(NamedTuple):
    """A list of positions for RID"""

    positions: List[Position]


class RIDFlight(NamedTuple):
    id: str
    most_recent_position: Position
    recent_paths: List[RIDPositions]


class ClusterDetails(NamedTuple):
    corners: List[Position]
    area_sqm: float
    number_of_flights: float


class RIDDisplayDataResponse(NamedTuple):
    flights: List[RIDFlight]
    clusters: List[ClusterDetails]


@dataclass
class SubscriptionResponse:
    """A object to hold details of a request for creation of subscription in the DSS"""

    created: bool
    dss_subscription_id: Optional[str]
    notification_index: int


@dataclass
class RIDAltitude:
    """A class to hold altitude"""

    value: Union[int, float]
    reference: str
    units: str


@dataclass
class RIDPolygon:
    vertices: List[LatLngPoint]


@dataclass
class RIDVolume3D:
    outline_polygon: RIDPolygon
    altitude_upper: RIDAltitude
    altitude_lower: RIDAltitude


@dataclass
class RIDVolume4D:
    volume: RIDVolume3D
    time_start: RIDTime
    time_end: RIDTime


@dataclass
class SubscriptionState:
    subscription_id: str
    notification_index: int = 0


@dataclass
class SubscriberToNotify:
    url: str
    subscriptions: List[SubscriptionState] = field(default_factory=[])


@dataclass
class ISACreationRequest:
    """A object to hold details of a request that indicates the DSS"""

    extents: Volume4D
    uss_base_url: str


@dataclass
class IdentificationServiceArea:
    uss_base_url: str
    owner: str
    time_start: StringBasedDateTime
    time_end: StringBasedDateTime
    version: str
    id: str


@dataclass
class ISACreationResponse:
    """A object to hold details of a request for creation of an ISA in the DSS"""

    created: bool
    subscribers: List[SubscriberToNotify]
    service_area: IdentificationServiceArea


class CreateSubscriptionResponse(NamedTuple):
    """Output of a request to create subscription"""

    message: str
    id: str
    dss_subscription_response: Optional[SubscriptionResponse]


class RIDCapabilitiesResponseEnum(str, enum.Enum):
    """A enum to hold USS capabilities operation"""

    ASTMRID2019 = "ASTMRID2019"
    ASTMRID2022 = "ASTMRID2022"


@dataclass
class RIDCapabilitiesResponse:
    capabilities: List[
        Literal[
            RIDCapabilitiesResponseEnum.ASTMRID2019,
            RIDCapabilitiesResponseEnum.ASTMRID2022,
        ]
    ]


@dataclass
class RIDAircraftPosition:
    lat: float
    lng: float
    alt: float
    accuracy_h: str
    accuracy_v: str
    extrapolated: Optional[bool]
    pressure_altitude: Optional[float]


@dataclass
class RIDHeight:
    distance: float
    reference: str

@dataclass
class AuthData:
    format: str
    data: str


@dataclass
class RIDAuthData:
    format: str
    data: str


@dataclass
class OperatorAltitude:
    altitude: int
    altitude_type: str


@dataclass
class RIDOperatorDetails:
    id: str

    operator_id: Optional[str]
    operator_location: Optional[LatLngPoint]
    operation_description: Optional[str]
    auth_data: Optional[RIDAuthData]
    serial_number: Optional[str]
    registration_number: Optional[str]
    aircraft_type: Optional[str] = None
    eu_classification: Optional[UAClassificationEU] = None
    uas_id: Optional[UASID] = None


@dataclass
class FlightState:
    timestamp: StringBasedDateTime
    timestamp_accuracy: float
    operational_status: Optional[str]
    position: RIDAircraftPosition
    track: float
    speed: float
    speed_accuracy: str
    vertical_speed: float
    height: Optional[RIDHeight]
    group_radius: int
    group_ceiling: int
    group_floor: int
    group_count: int
    group_time_start: StringBasedDateTime
    group_time_end: StringBasedDateTime


@dataclass
class RIDTestDetailsResponse:
    effective_after: str
    details: RIDOperatorDetails


@dataclass
class RIDTestInjection:
    injection_id: str
    telemetry: List[FlightState]
    details_responses: List[RIDTestDetailsResponse]


@dataclass
class RIDTestDataStorage:
    flight_state: FlightState
    details_response: RIDTestDetailsResponse


@dataclass
class HTTPErrorResponse:
    message: str
    status: int


@dataclass
class CreateTestPayload:
    requested_flights: List[RIDTestInjection]
    test_id: str


@dataclass
class CreateTestResponse:
    injected_flights: List[RIDTestInjection]
    version: int


@dataclass
class RIDAircraftState:
    timestamp: RIDTime
    timestamp_accuracy: float
    speed_accuracy: str
    position: RIDAircraftPosition
    operational_status: Optional[str] = None
    track: Optional[float] = None
    speed: Optional[float] = None
    vertical_speed: Optional[float] = None
    height: Optional[RIDHeight] = None

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
    recent_positions: List[RIDRecentAircraftPosition]
    operator_details: RIDOperatorDetails


@dataclass
class RIDFlightResponse:
    timestamp: str
    flights: List[TelemetryFlightDetails]


@dataclass
class SingleObservationMetadata:
    details_response: RIDTestDetailsResponse
    telemetry: RIDAircraftState


def process_requested_flight(
    requested_flight: dict,
    flight_injection_sorted_set:str
) -> tuple[RIDTestInjection, List[LatLngPoint], List[float]]:
    r = get_redis()
    all_telemetry = []
    all_flight_details = []
    all_positions: List[LatLngPoint] = []
    all_altitudes = []
    provided_telemetries = requested_flight["telemetry"]
    provided_flight_details = requested_flight["details_responses"]

    for provided_flight_detail in provided_flight_details:
        fd = provided_flight_detail["details"]
        requested_flight_detail_id = fd["id"]

        op_location = LatLngPoint(lat=fd["operator_location"]["lat"], lng=fd["operator_location"]["lng"])
        if "auth_data" in fd.keys():
            auth_data = RIDAuthData(format=fd["auth_data"]["format"], data=fd["auth_data"]["data"])
        else:
            auth_data = RIDAuthData(format="0", data="")
        serial_number = fd["serial_number"] if "serial_number" in fd else "MFR1C123456789ABC"
        if "uas_id" in fd.keys():
            uas_id = UASID(
                specific_session_id=fd["uas_id"]["specific_session_id"],
                serial_number=fd["uas_id"]["serial_number"],
                registration_id=fd["uas_id"]["registration_id"],
                utm_id=fd["uas_id"]["utm_id"],
            )
        else:
            uas_id = UASID(
                specific_session_id="02-a1b2c3d4e5f60708",
                serial_number=serial_number,
                utm_id="ae1fa066-6d68-4018-8274-af867966978e",
                registration_id="MFR1C123456789ABC",
            )
        if "eu_classification" in fd.keys():
            eu_classification = UAClassificationEU(
                category=fd["eu_classification"]["category"],
                class_=fd["eu_classification"]["class"],
            )
        else:
            eu_classification = UAClassificationEU(category="EUCategoryUndefined", class_="EUClassUndefined")

        flight_detail = RIDOperatorDetails(
            id=requested_flight_detail_id,
            operation_description=fd["operation_description"],
            serial_number=serial_number,
            registration_number=fd["registration_number"],
            operator_location=op_location,
            aircraft_type="NotDeclared",
            operator_id=fd["operator_id"],
            auth_data=auth_data,
            uas_id=uas_id,
            eu_classification=eu_classification,
        )
        pfd = RIDTestDetailsResponse(
            effective_after=provided_flight_detail["effective_after"],
            details=flight_detail,
        )
        all_flight_details.append(pfd)

        flight_details_storage = "flight_details:" + requested_flight_detail_id

        r.set(flight_details_storage, json.dumps(asdict(flight_detail)))
        # expire in 5 mins
        r.expire(flight_details_storage, time=3000)

    # Iterate over telemetry details provided
    for telemetry_id, provided_telemetry in enumerate(provided_telemetries):
        pos = provided_telemetry["position"]
        # In provided telemetry position and pressure altitude and extrapolated values are optional use if provided else generate them.
        pressure_altitude = pos["pressure_altitude"] if "pressure_altitude" in pos else 0.0
        extrapolated = pos["extrapolated"] if "extrapolated" in pos else False

        llp = LatLngPoint(lat=pos["lat"], lng=pos["lng"])
        all_positions.append(llp)
        all_altitudes.append(pos["alt"])
        position = RIDAircraftPosition(
            lat=pos["lat"],
            lng=pos["lng"],
            alt=pos["alt"],
            accuracy_h=pos["accuracy_h"],
            accuracy_v=pos["accuracy_v"],
            extrapolated=extrapolated,
            pressure_altitude=pressure_altitude,
        )

        if "height" in provided_telemetry.keys():
            height = RIDHeight(
                distance=provided_telemetry["height"]["distance"],
                reference=provided_telemetry["height"]["reference"],
            )
        else:
            height = None

        try:
            formatted_timestamp = arrow.get(provided_telemetry["timestamp"])
        except ParserError:
            logger.info("Error in parsing telemetry timestamp")
        else:
            teletemetry_observation = RIDAircraftState(
                timestamp=RIDTime(value=provided_telemetry["timestamp"], format="RFC3339"),
                timestamp_accuracy=provided_telemetry["timestamp_accuracy"],
                operational_status=provided_telemetry["operational_status"],
                position=position,
                track=provided_telemetry["track"],
                speed=provided_telemetry["speed"],
                speed_accuracy=provided_telemetry["speed_accuracy"],
                vertical_speed=provided_telemetry["vertical_speed"],
                height=height,
            )
            closest_details_response = min(
                all_flight_details,
                key=lambda d: abs(arrow.get(d.effective_after) - formatted_timestamp),
            )
            flight_state_storage = RIDTestDataStorage(flight_state=teletemetry_observation, details_response=closest_details_response)
            zadd_struct = {json.dumps(asdict(flight_state_storage)): formatted_timestamp.int_timestamp}
            # Add these as a sorted set in Redis
            r.zadd(flight_injection_sorted_set, zadd_struct)
            all_telemetry.append(teletemetry_observation)

    _requested_flight = RIDTestInjection(
        injection_id=requested_flight["injection_id"],
        telemetry=all_telemetry,
        details_responses=all_flight_details,
    )

    return _requested_flight, all_positions, all_altitudes
