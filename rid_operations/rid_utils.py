import enum
from dataclasses import asdict, dataclass, field
from typing import List, Literal, NamedTuple, Optional, Union

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
    alt: Optional[float] = None


class RIDPositions(NamedTuple):
    """A list of positions for RID"""

    positions: List[Position]


class RIDFlight(NamedTuple):
    id: str
    most_recent_position: Position
    recent_paths: List[RIDPositions]


class Cluster(ImplicitDict):
    x_min: float
    x_max: float
    y_min: float
    y_max: float
    points: List[Point]


class ClusterDetail(NamedTuple):
    corners: List[ClusterPosition]
    area_sqm: float
    number_of_flights: float


class RIDDisplayDataResponse(NamedTuple):
    flights: List[RIDFlight]
    clusters: List[ClusterDetail]


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
    vertices: List[RIDLatLngPoint]


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
    extrapolated: Optional[bool]
    pressure_altitude: Optional[float]
    height: Optional[RIDHeight]


@dataclass
class AuthData:
    format: int
    data: Optional[str] = ""


@dataclass
class RIDAuthData:
    format: int
    data: Optional[str] = ""


@dataclass
class OperatorAltitude:
    altitude: int
    altitude_type: str


@dataclass
class RIDOperatorDetails:
    id: str

    operator_id: Optional[str]
    operator_location: Optional[RIDLatLngPoint]
    operation_description: Optional[str]
    auth_data: Optional[RIDAuthData]
    serial_number: Optional[str]
    registration_number: Optional[str]
    aircraft_type: Optional[str] = None
    eu_classification: Optional[UAClassificationEU] = None
    uas_id: Optional[UASID] = None


@dataclass
class RIDFlightDetails:
    id: str
    operator_id: Optional[str]
    operator_location: Optional[OperatorLocation]
    operation_description: Optional[str]
    auth_data: Optional[RIDAuthData]
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
    details: RIDFlightDetails


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
    timestamp: RIDTime
    flights: List[TelemetryFlightDetails]


@dataclass
class SingleObservationMetadata:
    details_response: RIDTestDetailsResponse
    telemetry: RIDAircraftState


@dataclass
class RIDFlightsRecord:
    service_areas: List[IdentificationServiceArea]
    subscription: RIDSubscription
    extents: Optional[RIDVolume4D] = None
