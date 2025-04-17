from dataclasses import dataclass
from scd_operations.scd_data_definitions import SubscriptionState,Time, Volume4D
from typing import List, Optional, Dict, Any
from enum import Enum
from datetime import timedelta
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
    name: Optional[TextShortType] = None
    service: Optional[TextShortType] = None
    contact_name: Optional[TextShortType] = None
    site_url: Optional[TextShortType] = None
    email: Optional[TextShortType] = None
    phone: Optional[TextShortType] = None
    purpose: Optional[CodeAuthorityRole] = None
    interval_before: Optional[timedelta] = None


@dataclass
class GeoZone:
    identifier: CodeZoneIdentifierType
    country: CodeCountryISOType
    zone_authority: List[Authority]
    type: CodeZoneType
    restriction: CodeRestrictionType
    name: Optional[TextShortType] = None
    restriction_conditions: Optional[List[ConditionExpressionType]] = None
    region: Optional[int] = None
    reason: Optional[List[CodeZoneReasonType]] = None
    other_reason_info: Optional[str] = None
    regulation_exemption: Optional[CodeYesNoType] = None
    u_space_class: Optional[CodeUSpaceClassType] = None
    message: Optional[TextShortType] = None
    additional_properties: Optional[Dict[str, Any]] = None


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
    ovn: Optional[EntityOVN] = None


@dataclass
class ConstraintDetails:
    volumes: List[Volume4D]
    type: Optional[str] = None
    geozone: Optional[GeoZone] = None


@dataclass
class Constraint:
    reference: ConstraintReference
    details: ConstraintDetails


@dataclass
class PutConstraintDetailsParameters:
    constraint_id: EntityID
    subscriptions: List[SubscriptionState]
    constraint: Optional[Constraint] = None

@dataclass
class QueryConstraintsPayload:
    area_of_interest: Volume4D

