from dataclasses import dataclass


@dataclass
class LatLngPoint:
    lat: float
    lng: float


@dataclass
class Radius:
    value: float
    units: str


@dataclass
class Polygon:
    vertices: list[LatLngPoint]


@dataclass
class Circle:
    center: LatLngPoint
    radius: Radius


@dataclass
class Altitude:
    value: int | float
    reference: str
    units: str


@dataclass
class Volume3D:
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
    volume: Volume3D
    time_start: Time
    time_end: Time
