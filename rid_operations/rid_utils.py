import enum
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Literal, NamedTuple

from implicitdict import ImplicitDict, StringBasedDateTime
from shapely.geometry import Point

from scd_operations.scd_data_definitions import Volume4D

from .data_definitions import UASID, OperatorLocation, UAClassificationEU


@dataclass
class RIDTime:
    value: str
    format: str


@dataclass
class RIDLatLngPoint:
    lat: float
    lng: float


class Position(NamedTuple):
    """A class to hold most recent position for remote id data"""

    lat: float
    lng: float
    alt: float


class ClusterPosition(NamedTuple):
    """A class to hold most recent position for remote id data"""

    lat: float
    lng: float
    alt: float | None = None


class RIDPositions(NamedTuple):
    """A list of positions for RID"""

    positions: list[Position]


class RIDFlight(NamedTuple):
    id: str
    most_recent_position: Position
    recent_paths: list[RIDPositions]


class Cluster(ImplicitDict):
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    points: list[Point]


class ClusterDetail(NamedTuple):
    corners: list[ClusterPosition]
    area_sqm: float
    number_of_flights: float


class RIDDisplayDataResponse(NamedTuple):
    flights: list[RIDFlight]
    clusters: list[ClusterDetail]


@dataclass
class SubscriptionResponse:
    """A object to hold details of a request for creation of subscription in the DSS"""

    created: bool
    dss_subscription_id: str | None
    notification_index: int


@dataclass
class RIDAltitude:
    """A class to hold altitude"""

    value: int | float
    reference: str
    units: str


@dataclass
class RIDPolygon:
    vertices: list[RIDLatLngPoint]


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
    subscriptions: list[SubscriptionState] = field(default_factory=[])


@dataclass
class RIDSubscription:
    id: str
    uss_base_url: str
    owner: str
    notification_index: int
    time_end: RIDTime
    time_start: RIDTime
    version: str


@dataclass
class ISACreationRequest:
    """A object to hold details of a request that indicates the DSS"""

    extents: Volume4D
    uss_base_url: str


@dataclass
class IdentificationServiceArea:
    uss_base_url: str
    owner: str
    time_start: RIDTime
    time_end: RIDTime
    version: str
    id: str


@dataclass
class ISACreationResponse:
    """A object to hold details of a request for creation of an ISA in the DSS"""

    created: bool
    subscribers: list[SubscriberToNotify]
    service_area: IdentificationServiceArea


class CreateSubscriptionResponse(NamedTuple):
    """Output of a request to create subscription"""

    message: str
    id: str
    dss_subscription_response: SubscriptionResponse | None


class RIDCapabilitiesResponseEnum(str, enum.Enum):
    """A enum to hold USS capabilities operation"""

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
class RIDAuthData:
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
class RIDFlightDetails:
    id: str
    operator_id: str | None
    operator_location: OperatorLocation | None
    operation_description: str | None
    auth_data: RIDAuthData | None
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
class RIDFlightsRecord:
    service_areas: list[IdentificationServiceArea]
    subscription: RIDSubscription
    extents: RIDVolume4D | None = None


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
