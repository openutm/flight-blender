import enum
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from uss_operations.uss_data_definitions import Volume4D


class FlightPlanningStatusResponse(str, enum.Enum):
    """A enum to specify if the USS is ready (or not)"""

    Starting = "Starting"
    Ready = "Ready"


class AdvisoryInclusion(str, enum.Enum):
    Unknown = "Unknown"
    AtLeastOneAdvisoryOrCondition = "AtLeastOneAdvisoryOrCondition"
    NoAdvisoriesOrConditions = "NoAdvisoriesOrConditions"


class FlightPlanCurrentStatus(str, enum.Enum):
    NotPlanned = "NotPlanned"
    Planned = "Planned"
    OkToFly = "OkToFly"
    OffNominal = "OffNominal"
    Closed = "Closed"
    Processing = "Processing"  # Internal Flight Blender status


class PlanningActivityResult(str, enum.Enum):
    Completed = "Completed"
    Rejected = "Rejected"
    Failed = "Failed"
    NotSupported = "NotSupported"


@dataclass
class CloseFlightPlanResponse:
    planning_result: PlanningActivityResult
    notes: Optional[str]
    flight_plan_status: FlightPlanCurrentStatus
    includes_advisories: Optional[AdvisoryInclusion]


@dataclass
class UpsertFlightPlanResponse:
    flight_plan_status: FlightPlanCurrentStatus
    notes: str
    includes_advisories: Optional[AdvisoryInclusion]
    planning_result: PlanningActivityResult


@dataclass
class FlightPlanningTestStatus:
    status: FlightPlanningStatusResponse
    system_version: str
    api_name: str
    api_version: str


class OperatorType(Enum):
    Recreational = "Recreational"
    CommercialExcluded = "CommercialExcluded"
    ReOC = "ReOC"


class AircraftType(Enum):
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


class FlightProfile(Enum):
    AutomatedGrid = "AutomatedGrid"
    AutomatedWaypoint = "AutomatedWaypoint"
    Manual = "Manual"


class UsageState(Enum):
    Planned = "Planned"
    InUse = "InUse"
    Closed = "Closed"


class OperationCategory(Enum):
    Unknown = "Unknown"
    Open = "Open"
    Specific = "Specific"
    Certified = "Certified"


class UasState(Enum):
    Nominal = "Nominal"
    OffNominal = "OffNominal"
    Contingent = "Contingent"
    NotSpecified = "NotSpecified"


class OperationMode(Enum):
    Undeclared = "Undeclared"
    Vlos = "Vlos"
    Bvlos = "Bvlos"


class Result(Enum):
    Planned = "Planned"
    ReadyToFly = "ReadyToFly"
    Rejected = "Rejected"
    Failed = "Failed"
    NotSupported = "NotSupported"


class IncludesAdvisories(Enum):
    Unknown = "Unknown"
    True_ = True
    False_ = False


class UASClass(Enum):
    Other = "Other"
    C0 = "C0"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"
    C5 = "C5"
    C6 = "C6"


@dataclass
class FlightAuthorisationData:
    uas_serial_number: str
    operation_mode: OperationMode
    operation_category: OperationCategory
    uas_class: UASClass
    identification_technologies: List[str]
    uas_type_certificate: Optional[str]
    connectivity_methods: List[str]
    endurance_minutes: float
    emergency_procedure_url: str
    operator_id: str
    uas_id: Optional[str]


@dataclass
class BasicFlightPlanInformation:
    usage_state: UsageState
    uas_state: UasState
    area: Optional[List[Volume4D]]


@dataclass
class ASTMF354821OpIntentInformation:
    priority: int


@dataclass
class RPAS26FlightDetails:
    operator_type: Optional[OperatorType]
    uas_serial_numbers: Optional[List[str]]
    uas_registration_numbers: Optional[List[str]]
    aircraft_type: Optional[AircraftType]
    flight_profile: Optional[FlightProfile]
    pilot_license_number: Optional[str]
    pilot_phone_number: Optional[str]
    operator_number: Optional[str]


@dataclass
class FlightPlan:
    basic_information: BasicFlightPlanInformation
    astm_f3548_21: Optional[ASTMF354821OpIntentInformation]
    uspace_flight_authorisation: Optional[FlightAuthorisationData]
    rpas_operating_rules_2_6: Optional[RPAS26FlightDetails] = dict
    additional_information: Optional[Dict[str, Any]] = dict


@dataclass
class FlightPlanningRequest:
    intended_flight: FlightPlan
    request_id: str


@dataclass
class FlightPlanningInjectionData:
    """Class for keeping track of an operational intent test injections"""

    volumes: Optional[List[Volume4D]]
    priority: int
    off_nominal_volumes: Optional[List[Volume4D]]
    uas_state: UasState
    usage_state: UsageState
    state: str


@dataclass
class FlightPlanningUSSDetails:
    volumes: List[Volume4D]
    priority: int
    off_nominal_volumes: Optional[List[Volume4D]]
