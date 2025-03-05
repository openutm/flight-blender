import enum
from dataclasses import dataclass
from typing import List, Literal, Optional, Union

from rid_operations.rid_utils import RIDOperatorDetails


# TODO: Need to consolidate all data_definitions across different apps
@dataclass
class OperationalIntentNotFoundResponse:
    message: str


@dataclass
class Time:
    """A class to hold time objects"""

    format: str
    value: str


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
    details: RIDOperatorDetails


@dataclass
class SubscriptionState:
    subscription_id: str
    notification_index: int


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

    vertices: List[LatLngPoint]  # A minimum of three LatLngPoints


@dataclass
class Circle:
    """Hold the details of a circle object"""

    center: LatLngPoint
    radius: Radius


@dataclass
class Altitude:
    """A class to hold altitude"""

    value: Union[int, float]
    reference: str
    units: str


@dataclass
class Volume3D:
    """A class to hold Volume3D objects"""

    outline_polygon: Polygon
    altitude_lower: Altitude
    altitude_upper: Altitude
    outline_circle: Optional[Circle] = None


class OperationalIntentState(str, enum.Enum):
    """A test is either pass or fail or could not be processed, currently not"""

    Accepted = "Accepted"
    Activated = "Activated"
    Nonconforming = "Nonconforming"
    Contingent = "Contingent"


@dataclass
class Volume4D:
    """A class to hold Volume4D objects"""

    volume: Volume3D
    time_start: Time
    time_end: Time


@dataclass
class OperationalIntentReferenceDSSResponse:
    id: str
    manager: str
    uss_availability: str
    version: int
    state: Literal[
        OperationalIntentState.Accepted,
        OperationalIntentState.Activated,
        OperationalIntentState.Nonconforming,
        OperationalIntentState.Contingent,
    ]
    ovn: str
    time_start: Time
    time_end: Time
    uss_base_url: str
    subscription_id: str


@dataclass
class OperationalIntentUSSDetails:
    volumes: List[Volume4D]
    priority: int
    off_nominal_volumes: Optional[List[Volume4D]]


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
    subscriptions: List[SubscriptionState]
    operational_intent: Optional[OperationalIntentDetailsUSSResponse] = None


Latitude = float
"""Degrees of latitude north of the equator, with reference to the WGS84 ellipsoid."""


Longitude = float
"""Degrees of longitude east of the Prime Meridian, with reference to the WGS84 ellipsoid."""


class PositionAccuracyVertical(str, enum.Enum):
    """Vertical error that is likely to be present in this reported position. This is the GVA enumeration from ADS-B, plus some finer values for UAS."""

    VAUnknown = "VAUnknown"
    VA150mPlus = "VA150mPlus"
    VA150m = "VA150m"
    VA45m = "VA45m"
    VA25m = "VA25m"
    VA10m = "VA10m"
    VA3m = "VA3m"
    VA1m = "VA1m"


class PositionAccuracyHorizontal(str, enum.Enum):
    """Horizontal error that is likely to be present in this reported position. This is the NACp enumeration from ADS-B, plus 1m for a more complete range for UAS."""

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
    """Location of the vehicle (UAS) as reported for UTM. Note: 'accuracy' values are required when extrapolated field is true."""

    longitude: Optional[Longitude]
    latitude: Optional[Latitude]
    accuracy_h: Optional[PositionAccuracyHorizontal]
    accuracy_v: Optional[PositionAccuracyVertical]
    altitude: Optional[Altitude]
    extrapolated: Optional[bool] = False


class VelocityUnitsSpeed(str, enum.Enum):
    MetersPerSecond = "MetersPerSecond"


@dataclass
class Velocity:
    speed: float
    """Ground speed in meters/second."""
    units_speed: VelocityUnitsSpeed = VelocityUnitsSpeed.MetersPerSecond
    track: Optional[float] = 0
    """Direction of flight expressed as a "True North-based" ground track angle. This value is provided in degrees East of North with a minimum resolution of 1 degree. A value of 360 indicates invalid, no value, or unknown."""


@dataclass
class VehicleTelemetry:
    """Vehicle position, altitude, and velocity."""

    time_measured: Time
    position: Optional[Position]
    velocity: Optional[Velocity]


@dataclass
class VehicleTelemetryResponse:
    operational_intent_id: str
    telemetry: Optional[VehicleTelemetry]
    next_telemetry_opportunity: Optional[Time]
