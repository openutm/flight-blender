from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any

UUIDv4Format = str
CodeUSpaceClassType = str
TextShortType = str
CodeZoneIdentifierType = str
CodeCountryISOType = str
ConditionExpressionType = str
CodeRestrictionType = str
EntityOVN = str
ConstraintUssBaseURL = str
EntityVersion = int
EntityID = UUIDv4Format


@dataclass
class LatLngPoint:
    """A clas to hold information about LatLngPoint"""

    lat: float
    lng: float


@dataclass
class Radius:
    """A class to hold the radius object"""

    value: float
    units: str


@dataclass
class Polygon:
    """A class to hold the polygon object"""

    vertices: list[LatLngPoint]  # A minimum of three LatLngPoints


@dataclass
class Circle:
    """Hold the details of a circle object"""

    center: LatLngPoint
    radius: Radius


@dataclass
class Altitude:
    """A class to hold altitude"""

    value: int | float
    reference: str
    units: str


@dataclass
class Volume3D:
    """A class to hold Volume3D objects"""

    outline_polygon: Polygon
    altitude_lower: Altitude
    altitude_upper: Altitude
    outline_circle: Circle | None = None


@dataclass
class SubscriptionState:
    subscription_id: str
    notification_index: int


@dataclass
class Time:
    format: str
    value: str


@dataclass
class Volume4D:
    """A class to hold Volume4D objects"""

    volume: Volume3D
    time_start: Time
    time_end: Time


class CodeZoneReasonType(str, Enum):
    AIR_TRAFFIC = "AIR_TRAFFIC"
    SENSITIVE = "SENSITIVE"
    PRIVACY = "PRIVACY"
    POPULATION = "POPULATION"
    NATURE = "NATURE"
    NOISE = "NOISE"
    FOREIGN_TERRITORY = "FOREIGN_TERRITORY"
    EMERGENCY = "EMERGENCY"
    OTHER = "OTHER"


class CodeZoneType(str, Enum):
    COMMON = "COMMON"
    CUSTOMIZED = "CUSTOMIZED"
    PROHIBITED = "PROHIBITED"
    REQ_AUTHORISATION = "REQ_AUTHORISATION"
    CONDITIONAL = "CONDITIONAL"
    NO_RESTRICTION = "NO_RESTRICTION"


class CodeAuthorityRole(str, Enum):
    AUTHORIZATION = "AUTHORIZATION"
    NOTIFICATION = "NOTIFICATION"
    INFORMATION = "INFORMATION"


class CodeYesNoType(str, Enum):
    True_ = True
    False_ = False


@dataclass
class Authority:
    name: TextShortType | None = None
    service: TextShortType | None = None
    contact_name: TextShortType | None = None
    site_url: TextShortType | None = None
    email: TextShortType | None = None
    phone: TextShortType | None = None
    purpose: CodeAuthorityRole | None = None
    interval_before: timedelta | None = None


@dataclass
class GeoZone:
    identifier: CodeZoneIdentifierType
    country: CodeCountryISOType
    zone_authority: list[Authority]
    type: CodeZoneType
    restriction: CodeRestrictionType
    name: TextShortType | None = None
    restriction_conditions: list[ConditionExpressionType] | None = None
    region: int | None = None
    reason: list[CodeZoneReasonType] | None = None
    other_reason_info: str | None = None
    regulation_exemption: CodeYesNoType | None = None
    u_space_class: CodeUSpaceClassType | None = None
    message: TextShortType | None = None
    additional_properties: dict[str, Any] | None = None


class UssAvailabilityState(Enum):
    Unknown = "Unknown"
    Normal = "Normal"
    Down = "Down"


@dataclass
class ConstraintReference:
    id: EntityID
    manager: str
    uss_availability: UssAvailabilityState
    version: EntityVersion
    time_start: Time
    time_end: Time
    uss_base_url: ConstraintUssBaseURL
    ovn: EntityOVN | None = None


@dataclass
class ConstraintDetails:
    volumes: list[Volume4D]
    type: str | None = None
    geozone: GeoZone | None = None


@dataclass
class Constraint:
    reference: ConstraintReference
    details: ConstraintDetails


@dataclass
class PutConstraintDetailsParameters:
    constraint_id: EntityID
    subscriptions: list[SubscriptionState]
    constraint: Constraint | None = None


@dataclass
class QueryConstraintsPayload:
    area_of_interest: Volume4D


@dataclass
class CompositeConstraintPayload:
    constraint_reference_id: str
    constraint_detail_id: str
    flight_declaration_id: str
    bounds: str
    start_datetime: str
    end_datetime: str
    alt_max: str
    alt_min: str
