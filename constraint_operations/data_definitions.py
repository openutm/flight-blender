from dataclasses import dataclass
from datetime import timedelta
from enum import Enum
from typing import Any

from scd_operations.scd_data_definitions import SubscriptionState, Time, Volume4D

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


class CodeZoneReasonType(Enum):
    AIR_TRAFFIC = "AIR_TRAFFIC"
    SENSITIVE = "SENSITIVE"
    PRIVACY = "PRIVACY"
    POPULATION = "POPULATION"
    NATURE = "NATURE"
    NOISE = "NOISE"
    FOREIGN_TERRITORY = "FOREIGN_TERRITORY"
    EMERGENCY = "EMERGENCY"
    OTHER = "OTHER"


class CodeZoneType(Enum):
    COMMON = "COMMON"
    CUSTOMIZED = "CUSTOMIZED"
    PROHIBITED = "PROHIBITED"
    REQ_AUTHORISATION = "REQ_AUTHORISATION"
    CONDITIONAL = "CONDITIONAL"
    NO_RESTRICTION = "NO_RESTRICTION"


class CodeAuthorityRole(Enum):
    AUTHORIZATION = "AUTHORIZATION"
    NOTIFICATION = "NOTIFICATION"
    INFORMATION = "INFORMATION"


class CodeYesNoType(Enum):
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
