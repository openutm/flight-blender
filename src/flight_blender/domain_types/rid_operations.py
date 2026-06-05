"""Operational RID domain types for DSS interaction.

These types represent the wire format for the RID DSS API and are used
by both the DSS client (clients/) and service layer (services/).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import NamedTuple

from implicitdict import ImplicitDict
from shapely.geometry import Point

from flight_blender.domain_types.rid import UASID, OperatorLocation, UAClassificationEU
from flight_blender.domain_types.scd import Volume4D


@dataclass
class RIDTime:
    value: str
    format: str


@dataclass
class RIDLatLngPoint:
    lat: float
    lng: float


class Position(NamedTuple):
    lat: float
    lng: float
    alt: float


class ClusterPosition(NamedTuple):
    lat: float
    lng: float
    alt: float | None = None


class RIDPositions(NamedTuple):
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
    created: bool
    dss_subscription_id: str | None
    notification_index: int


@dataclass
class RIDAltitude:
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
    subscriptions: list[SubscriptionState] = field(default_factory=list)


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
    extents: Volume4D | RIDVolume4D
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
    created: int
    subscribers: list[SubscriberToNotify]
    service_area: IdentificationServiceArea | None


class CreateSubscriptionResponse(NamedTuple):
    message: str
    id: str
    dss_subscription_response: SubscriptionResponse | None


@dataclass
class RIDAuthData:
    format: int
    data: str | None = ""


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
class RIDFlightsRecord:
    service_areas: list[IdentificationServiceArea]
    subscription: RIDSubscription
    extents: RIDVolume4D | None = None
