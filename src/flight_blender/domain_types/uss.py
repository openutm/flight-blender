# USS entities — merged from uss/rid_data_definitions.py and uss/uss_data_definitions.py
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from flight_blender.domain_types.airspace import Altitude as _AirspaceAltitude
from flight_blender.domain_types.airspace import Circle as _AirspaceCircle
from flight_blender.domain_types.airspace import LatLngPoint as _AirspaceLatLngPoint
from flight_blender.domain_types.airspace import Polygon as _AirspacePolygon
from flight_blender.domain_types.airspace import Radius as _AirspaceRadius
from flight_blender.domain_types.airspace import SubscriptionState as _AirspaceSubscriptionState
from flight_blender.domain_types.airspace import Time as _AirspaceTime
from flight_blender.domain_types.airspace import Volume3D as _AirspaceVolume3D
from flight_blender.domain_types.airspace import Volume4D as _AirspaceVolume4D

Time = _AirspaceTime
USSSubscriptionState = _AirspaceSubscriptionState
USSLatLngPoint = _AirspaceLatLngPoint
USSRadius = _AirspaceRadius
USSPolygon = _AirspacePolygon
USSCircle = _AirspaceCircle
USSAltitude = _AirspaceAltitude
USSVolume3D = _AirspaceVolume3D
USSVolume4D = _AirspaceVolume4D

# --- RID spec types (from uss/rid_data_definitions.py) ---


class RIDFormat(str, Enum):
    RFC3339 = "RFC3339"


class Units(str, Enum):
    M = "M"


class HorizontalAccuracy(str, Enum):
    HAUnknown = "HAUnknown"
    HA10NMPlus = "HA10NMPlus"
    HA10NM = "HA10NM"
    HA4NM = "HA4NM"
    HA2NM = "HA2NM"
    HA1NM = "HA1NM"
    HA05NM = "HA05NM"
    HA03NM = "HA03NM"
    HA01NM = "HA01NM"
    HA005NM = "HA005NM"
    HA30m = "HA30m"
    HA10m = "HA10m"
    HA3m = "HA3m"
    HA1m = "HA1m"


class VerticalAccuracy(str, Enum):
    VAUnknown = "VAUnknown"
    VA150mPlus = "VA150mPlus"
    VA150m = "VA150m"
    VA45m = "VA45m"
    VA25m = "VA25m"
    VA10m = "VA10m"
    VA3m = "VA3m"
    VA1m = "VA1m"


class SpeedAccuracy(str, Enum):
    SAUnknown = "SAUnknown"
    SA10mpsPlus = "SA10mpsPlus"
    SA10mps = "SA10mps"
    SA3mps = "SA3mps"
    SA1mps = "SA1mps"
    SA03mps = "SA03mps"


class RIDOperationalStatus(str, Enum):
    Undeclared = "Undeclared"
    Ground = "Ground"
    Airborne = "Airborne"
    Emergency = "Emergency"
    RemoteIDSystemFailure = "RemoteIDSystemFailure"


class AltitudeType(str, Enum):
    Takeoff = "Takeoff"
    Dynamic = "Dynamic"
    Fixed = "Fixed"


class Category(str, Enum):
    EUCategoryUndefined = "EUCategoryUndefined"
    Open = "Open"
    Specific = "Specific"
    Certified = "Certified"


class Class(str, Enum):
    EUClassUndefined = "EUClassUndefined"
    Class0 = "Class0"
    Class1 = "Class1"
    Class2 = "Class2"
    Class3 = "Class3"
    Class4 = "Class4"
    Class5 = "Class5"
    Class6 = "Class6"


class UAType(str, Enum):
    NotDeclared = "NotDeclared"
    Aeroplane = "Aeroplane"
    Helicopter = "Helicopter"
    Gyroplane = "Gyroplane"
    HybridLift = "HybridLift"
    Ornithopter = "Ornithopter"
    Glider = "Glider"
    Kite = "Kite"
    FreeBalloon = "FreeBalloon"
    CaptiveBalloon = "CaptiveBalloon"
    Airship = "Airship"
    FreeFallOrParachute = "FreeFallOrParachute"
    Rocket = "Rocket"
    TetheredPoweredAircraft = "TetheredPoweredAircraft"
    GroundObstacle = "GroundObstacle"
    Other = "Other"


class WGSReference(str, Enum):
    W84 = "W84"


class Reference(str, Enum):
    TakeoffLocation = "TakeoffLocation"
    GroundLevel = "GroundLevel"


URL = str
SubscriptionNotificationIndex = int
UUIDv4 = str
Version = str
EntityUUID = UUIDv4
SubscriptionUUID = UUIDv4
RIDFlightID = str
SpecificSessionID = str
USSBaseURL = str
SubscriptionUSSBaseURL = USSBaseURL
FlightsUSSBaseURL = USSBaseURL


@dataclass
class RIDTime:
    value: str
    format: RIDFormat


@dataclass
class Radius:
    value: float
    units: Units


@dataclass
class RIDAuthData:
    format: Optional[int] = 0
    data: Optional[str] = ""


@dataclass
class RIDHeight:
    reference: Reference
    distance: Optional[float] = 0


@dataclass
class LatLngPoint:
    lng: float
    lat: float


@dataclass
class Altitude:
    value: float
    reference: WGSReference
    units: Units


@dataclass
class OperatingArea:
    aircraft_count: Optional[int] = None
    volumes: Optional[List[OperatingArea]] = field(default_factory=lambda: [])


@dataclass
class Polygon:
    vertices: List[LatLngPoint]


@dataclass
class Circle:
    center: Optional[LatLngPoint] = None
    radius: Optional[Radius] = None


@dataclass
class Volume3D:
    outline_circle: Optional[Circle] = None
    outline_polygon: Optional[Polygon] = None
    altitude_lower: Optional[Altitude] = None
    altitude_upper: Optional[Altitude] = None


@dataclass
class Volume4D:
    volume: Volume3D
    time_start: Optional[RIDTime] = None
    time_end: Optional[RIDTime] = None


@dataclass
class SubscriptionState:
    subscription_id: SubscriptionUUID
    notification_index: Optional[SubscriptionNotificationIndex] = 0


@dataclass
class SubscriberToNotify:
    subscriptions: List[SubscriptionState]
    url: URL


@dataclass
class UASID:
    serial_number: Optional[str] = ""
    registration_id: Optional[str] = ""
    utm_id: Optional[str] = ""
    specific_session_id: Optional[SpecificSessionID] = ""


@dataclass
class UAClassificationEU:
    category: Optional[Category] = Category.EUCategoryUndefined
    class_: Optional[Class] = Class.EUClassUndefined


@dataclass
class OperatorLocation:
    position: LatLngPoint
    altitude: Optional[Altitude] = None
    altitude_type: Optional[AltitudeType] = None


@dataclass
class RIDFlightDetails:
    id: str
    eu_classification: Optional[UAClassificationEU] = None
    uas_id: Optional[UASID] = None
    operator_id: Optional[str] = ""
    operator_location: Optional[OperatorLocation] = None
    operation_description: Optional[str] = ""
    auth_data: Optional[RIDAuthData] = None


@dataclass
class RIDAircraftPosition:
    lat: Optional[float] = None
    lng: Optional[float] = None
    alt: Optional[float] = -1000
    accuracy_h: Optional[HorizontalAccuracy] = None
    accuracy_v: Optional[VerticalAccuracy] = None
    extrapolated: Optional[bool] = False
    pressure_altitude: Optional[float] = -1000
    height: Optional[RIDHeight] = None


@dataclass
class RIDRecentAircraftPosition:
    time: RIDTime
    position: RIDAircraftPosition


@dataclass
class RIDAircraftState:
    timestamp: RIDTime
    timestamp_accuracy: float
    position: RIDAircraftPosition
    speed_accuracy: SpeedAccuracy
    operational_status: Optional[RIDOperationalStatus] = RIDOperationalStatus.Undeclared
    track: Optional[float] = 361
    speed: Optional[float] = 255
    vertical_speed: Optional[float] = 63


@dataclass
class RIDFlight:
    id: RIDFlightID
    aircraft_type: UAType
    current_state: Optional[RIDAircraftState] = None
    operating_area: Optional[OperatingArea] = None
    simulated: Optional[bool] = False
    recent_positions: Optional[List[RIDRecentAircraftPosition]] = field(default_factory=lambda: [])


@dataclass
class GetFlightDetailsResponse:
    details: RIDFlightDetails


@dataclass
class GetIdentificationServiceAreaDetailsResponse:
    extents: Volume4D


@dataclass
class CreateIdentificationServiceAreaParameters:
    extents: Volume4D
    uss_base_url: FlightsUSSBaseURL


@dataclass
class UpdateIdentificationServiceAreaParameters:
    extents: Volume4D
    uss_base_url: FlightsUSSBaseURL


@dataclass
class CreateSubscriptionParameters:
    extents: Volume4D
    uss_base_url: SubscriptionUSSBaseURL


@dataclass
class UpdateSubscriptionParameters:
    extents: Volume4D
    uss_base_url: SubscriptionUSSBaseURL


@dataclass
class Subscription:
    id: SubscriptionUUID
    uss_base_url: SubscriptionUSSBaseURL
    owner: str
    version: Version
    notification_index: Optional[SubscriptionNotificationIndex] = 0
    time_end: Optional[RIDTime] = None
    time_start: Optional[RIDTime] = None


@dataclass
class IdentificationServiceArea:
    uss_base_url: FlightsUSSBaseURL
    owner: str
    time_start: RIDTime
    time_end: RIDTime
    version: Version
    id: EntityUUID


@dataclass
class PutIdentificationServiceAreaResponse:
    service_area: IdentificationServiceArea
    subscribers: Optional[List[SubscriberToNotify]] = field(default_factory=lambda: [])


@dataclass
class SearchIdentificationServiceAreasResponse:
    service_areas: Optional[List[IdentificationServiceArea]] = field(default_factory=lambda: [])


@dataclass
class PutIdentificationServiceAreaNotificationParameters:
    subscriptions: List[SubscriptionState]
    service_area: Optional[IdentificationServiceArea] = None
    extents: Optional[Volume4D] = None


@dataclass
class DeleteIdentificationServiceAreaResponse:
    service_area: IdentificationServiceArea
    subscribers: Optional[List[SubscriberToNotify]] = field(default_factory=lambda: [])


@dataclass
class PutSubscriptionResponse:
    subscription: Subscription
    service_areas: Optional[List[IdentificationServiceArea]] = field(default_factory=lambda: [])


@dataclass
class GetIdentificationServiceAreaResponse:
    service_area: IdentificationServiceArea


@dataclass
class GetSubscriptionResponse:
    subscription: Subscription


@dataclass
class SearchSubscriptionsResponse:
    subscriptions: Optional[List[Subscription]] = field(default_factory=lambda: [])


@dataclass
class DeleteSubscriptionResponse:
    subscription: Subscription


@dataclass
class GetFlightsResponse:
    timestamp: RIDTime
    flights: Optional[List[RIDFlight]] = field(default_factory=lambda: [])
    no_isas_present: Optional[bool] = False


@dataclass
class ErrorResponse:
    message: Optional[str] = ""


# --- USS operational types (from uss/uss_data_definitions.py) ---


@dataclass
class OperationalIntentNotFoundResponse:
    message: str


@dataclass
class UpdateOperationalIntent:
    message: str


@dataclass
class GenericErrorResponseMessage:
    message: str


@dataclass
class SummaryFlightsOnly:
    number_of_flights: int
    timestamp: str


@dataclass
class FlightDetailsNotFoundMessage:
    message: str


@dataclass
class OperatorDetailsSuccessResponse:
    details: RIDFlightDetails


class OperationalIntentState(str, Enum):
    Accepted = "Accepted"
    Activated = "Activated"
    Nonconforming = "Nonconforming"
    Contingent = "Contingent"


@dataclass
class OperationalIntentReferenceDSSResponse:
    id: str
    manager: str
    uss_availability: str
    version: int
    state: str
    ovn: str
    time_start: Time
    time_end: Time
    uss_base_url: str
    subscription_id: str


@dataclass
class OperationalIntentUSSDetails:
    volumes: list[USSVolume4D]
    priority: int
    off_nominal_volumes: list[USSVolume4D] | None


@dataclass
class OperationalIntentDetailsUSSResponse:
    reference: OperationalIntentReferenceDSSResponse
    details: OperationalIntentUSSDetails


@dataclass
class OperationalIntentDetails:
    operational_intent: OperationalIntentDetailsUSSResponse


@dataclass
class UpdateChangedOpIntDetailsPost:
    operational_intent_id: str
    subscriptions: list[USSSubscriptionState]
    operational_intent: OperationalIntentDetailsUSSResponse | None = None


Latitude = float
Longitude = float


class PositionAccuracyVertical(str, Enum):
    VAUnknown = "VAUnknown"
    VA150mPlus = "VA150mPlus"
    VA150m = "VA150m"
    VA45m = "VA45m"
    VA25m = "VA25m"
    VA10m = "VA10m"
    VA3m = "VA3m"
    VA1m = "VA1m"


class PositionAccuracyHorizontal(str, Enum):
    HAUnknown = "HAUnknown"
    HA10NMPlus = "HA10NMPlus"
    HA10NM = "HA10NM"
    HA4NM = "HA4NM"
    HA2NM = "HA2NM"
    HA1NM = "HA1NM"
    HA05NM = "HA05NM"
    HA03NM = "HA03NM"
    HA01NM = "HA01NM"
    HA005NM = "HA005NM"
    HA30m = "HA30m"
    HA10m = "HA10m"
    HA3m = "HA3m"
    HA1m = "HA1m"


@dataclass
class Position:
    longitude: Longitude | None
    latitude: Latitude | None
    accuracy_h: PositionAccuracyHorizontal | None
    accuracy_v: PositionAccuracyVertical | None
    altitude: USSAltitude | None
    extrapolated: bool | None = False


class VelocityUnitsSpeed(str, Enum):
    MetersPerSecond = "MetersPerSecond"


@dataclass
class Velocity:
    speed: float
    units_speed: VelocityUnitsSpeed = VelocityUnitsSpeed.MetersPerSecond
    track: float | None = 0


@dataclass
class VehicleTelemetry:
    time_measured: Time
    position: Position | None
    velocity: Velocity | None


@dataclass
class VehicleTelemetryResponse:
    operational_intent_id: str
    telemetry: VehicleTelemetry | None
    next_telemetry_opportunity: Time | None


class ExchangeRecordRecorderRole(str, Enum):
    Client = "Client"
    Server = "Server"


@dataclass
class ExchangeRecord:
    url: str
    method: str
    recorder_role: ExchangeRecordRecorderRole
    request_time: Time
    response_time: Time | None
    problem: str | None
    headers: list | None = field(default_factory=list)
    request_body: str | None = ""
    response_body: str | None = ""
    response_code: int | None = 0


@dataclass
class ErrorReport:
    report_id: str | None
    exchange: ExchangeRecord
