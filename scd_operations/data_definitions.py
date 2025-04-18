from dataclasses import dataclass
from typing import Literal


@dataclass
class FlightDeclarationCreationPayload:
    id: str
    operational_intent: str
    flight_declaration_raw_geojson: str
    bounds: str
    aircraft_id: str
    state: int


@dataclass
class SCDLatLngPoint:
    """A class to hold information about a location as Latitude / Longitude pair"""

    lat: float
    lng: float


@dataclass
class SCDRadius:
    """A class to hold the radius of a circle for the outline_circle object"""

    value: float
    units: str


@dataclass
class SCDPolygon:
    """A class to hold the polygon object, used in the outline_polygon of the Volume3D object"""

    vertices: list[SCDLatLngPoint]  # A minimum of three LatLngPoints are required


@dataclass
class SCDCircle:
    """A class the details of a circle object used in the outline_circle object"""

    center: SCDLatLngPoint
    radius: SCDRadius


@dataclass
class SCDAltitude:
    """A class to hold altitude information"""

    value: float
    reference: Literal["W84"]
    units: str


@dataclass
class SCDTime:
    """A class to hold Time details"""

    value: str
    format: Literal["RFC3339"]


@dataclass
class SCDVolume3D:
    """A class to hold Volume3D objects"""

    outline_circle: SCDCircle | None
    outline_polygon: SCDPolygon | None
    altitude_lower: SCDAltitude | None
    altitude_upper: SCDAltitude | None


@dataclass
class SCDVolume4D:
    """A class to hold Volume4D objects"""

    volume: SCDVolume3D
    time_start: SCDTime | None
    time_end: SCDTime | None


# class OperationalIntentReference(ImplicitDict):
#     id: str
#     manager: str
#     uss_availability: str
#     version: int
#     state: str
#     ovn: str
#     time_start: Time
#     time_end: Time
#     uss_base_url: str
#     subscription_id: str


# class OperationalIntentDetails(ImplicitDict):
#     volumes: List[Volume4D]
#     off_nominal_volumes: List[Volume4D]
#     priority: int


@dataclass
class FlightDeclarationOperationalIntentStorageDetails:
    volumes: list[SCDVolume4D]
    off_nominal_volumes: list[SCDVolume4D]
    priority: int
    state: str


# class OperationalIntent(ImplicitDict):
#     reference: OperationalIntentReference
#     details: OperationalIntentDetails
