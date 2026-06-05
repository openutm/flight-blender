import enum
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal, Optional

from implicitdict import StringBasedDateTime
from shapely.geometry import Polygon as Plgn

from flight_blender.core.entities.constraint import Constraint


# --- Geometric primitives (from scd_data_definitions) ---

@dataclass
class LatLngPoint:
    lat: float
    lng: float


@dataclass
class Radius:
    value: float
    units: str


@dataclass
class Time:
    format: str
    value: str


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


class OperationalIntentState(str, enum.Enum):
    Accepted = "Accepted"
    Activated = "Activated"
    Nonconforming = "Nonconforming"
    Contingent = "Contingent"


@dataclass
class Volume4D:
    volume: Volume3D
    time_start: Time
    time_end: Time


@dataclass
class OperationalIntentStorageVolumes:
    volumes: list[Volume4D]


@dataclass
class OperationalIntentTestInjection:
    volumes: list[Volume4D]
    priority: int
    off_nominal_volumes: list[Volume4D] | None
    state: Literal[
        OperationalIntentState.Accepted,
        OperationalIntentState.Activated,
        OperationalIntentState.Nonconforming,
        OperationalIntentState.Contingent,
    ]


class OperationCategory(str, enum.Enum):
    Vlos = "vlos"
    Bvlos = "bvlos"


class UASClass(str, enum.Enum):
    C0 = "C0"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"


class DeleteFlightStatus(str, enum.Enum):
    Closed = "Closed"
    Failed = "Failed"


class TestInjectionResultState(str, enum.Enum):
    Planned = "Planned"
    Rejected = "Rejected"
    ConflictWithFlight = "ConflictWithFlight"
    ReadyToFly = "ReadyToFly"
    Failed = "Failed"


class IDTechnology(str, enum.Enum):
    Network = "network"
    Broadcast = "broadcast"


class StatusResponseEnum(str, enum.Enum):
    Starting = "Starting"
    Ready = "Ready"


class DeleteFlightStatusResponseEnum(str, enum.Enum):
    Closed = "Closed"
    Failed = "Failed"


class USSCapabilitiesResponseEnum(str, enum.Enum):
    BasicStrategicConflictDetection = "BasicStrategicConflictDetection"
    FlightAuthorisationValidation = "FlightAuthorisationValidation"
    HighPriorityFlights = "HighPriorityFlights"


@dataclass
class FlightAuthorizationDataPayload:
    uas_serial_number: str
    operation_mode: Literal[OperationCategory.Vlos, OperationCategory.Bvlos]
    operation_category: str
    uas_class: Literal[UASClass.C0, UASClass.C1, UASClass.C2, UASClass.C3, UASClass.C4]
    identification_technologies: Literal[IDTechnology.Network, IDTechnology.Broadcast]
    connectivity_methods: list[str]
    endurance_minutes: int
    emergency_procedure_url: str
    operator_id: str


@dataclass
class SCDTestInjectionDataPayload:
    operational_intent: OperationalIntentTestInjection
    flight_authorisation: FlightAuthorizationDataPayload


@dataclass
class TestInjectionResult:
    result: Literal[
        TestInjectionResultState.Planned,
        TestInjectionResultState.Rejected,
        TestInjectionResultState.ConflictWithFlight,
        TestInjectionResultState.Failed,
        TestInjectionResultState.ReadyToFly,
    ]
    notes: str
    operational_intent_id: str


@dataclass
class SCDTestStatusResponse:
    status: Literal[StatusResponseEnum.Starting, StatusResponseEnum.Ready]
    version: str


@dataclass
class CapabilitiesResponse:
    capabilities: list[
        Literal[
            USSCapabilitiesResponseEnum.BasicStrategicConflictDetection,
            USSCapabilitiesResponseEnum.FlightAuthorisationValidation,
            USSCapabilitiesResponseEnum.HighPriorityFlights,
        ]
    ]


@dataclass
class DeleteFlightResponse:
    result: Literal[DeleteFlightStatusResponseEnum.Failed, DeleteFlightStatusResponseEnum.Closed]
    notes: str


@dataclass
class ClearAreaResponseOutcome:
    success: bool
    message: str
    timestamp: StringBasedDateTime


@dataclass
class ClearAreaResponse:
    outcome: ClearAreaResponseOutcome


@dataclass
class ClearAreaRequestData:
    request_id: str
    extent: Volume4D


@dataclass
class ImplicitSubscriptionParameters:
    uss_base_url: str
    notify_for_constraints: bool = False


@dataclass
class OperationalIntentReference:
    extents: list[Volume4D]
    key: list[str]
    state: str
    uss_base_url: str
    new_subscription: ImplicitSubscriptionParameters | None = None


@dataclass
class PartialCreateOperationalIntentReference:
    volumes: list[Volume4D]
    priority: int
    state: str
    off_nominal_volumes: list[Volume4D]


@dataclass
class OpIntSubscribers:
    subscribers: list[str]


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
class SubscriptionState:
    subscription_id: str
    notification_index: int


@dataclass
class SubscriberToNotify:
    subscriptions: list[SubscriptionState]
    uss_base_url: str


@dataclass
class OperationalIntentSubmissionSuccess:
    subscribers: list[SubscriberToNotify]
    operational_intent_reference: OperationalIntentReferenceDSSResponse


@dataclass
class OperationalIntentUSSDetails:
    volumes: list[Volume4D]
    priority: int
    off_nominal_volumes: list[Volume4D] | None


@dataclass
class OperationalIntentDetailsUSSResponse:
    reference: OperationalIntentReferenceDSSResponse
    details: OperationalIntentUSSDetails


@dataclass
class PeerUSSUnavailableResponse:
    message: str
    status: int


@dataclass
class LatLng:
    lat: float
    lng: float


@dataclass
class OperationalIntentStorage:
    bounds: str
    start_datetime: str
    end_datetime: str
    alt_max: float
    alt_min: float
    success_response: OperationalIntentSubmissionSuccess
    operational_intent_details: OperationalIntentTestInjection


@dataclass
class CompositeOperationalIntentPayload:
    bounds: str
    start_datetime: str
    end_datetime: str
    alt_max: float
    alt_min: float
    operational_intent_reference_id: str
    operational_intent_details_id: str


@dataclass
class OperationalIntentBoundsTimeAltitude:
    bounds: str
    start_datetime: StringBasedDateTime
    end_datetime: StringBasedDateTime
    alt_max: float
    alt_min: float
    flight_declaration_id: str


@dataclass
class OperationalIntentSubmissionError:
    result: str
    notes: str


@dataclass
class OtherError:
    notes: str


@dataclass
class EmtptyResponse:
    message: str


@dataclass
class OperationalIntentSubmissionStatus:
    dss_response: OperationalIntentSubmissionSuccess | OperationalIntentSubmissionError | OtherError | EmtptyResponse
    status: str
    status_code: int
    message: str
    operational_intent_id: str
    constraints: Optional[list[Constraint]] = None


@dataclass
class NotifyPeerUSSPostPayload:
    operational_intent_id: str
    operational_intent: OperationalIntentDetailsUSSResponse
    subscriptions: list[SubscriptionState]


@dataclass
class DeleteOperationalIntentConstuctor:
    entity_id: str
    ovn: str


@dataclass
class DeleteOperationalIntentResponseSuccess:
    subscribers: list[str]
    operational_intent_reference: OperationalIntentReferenceDSSResponse


@dataclass
class CommonPeer9xxResponse:
    message: str


@dataclass
class CommonPeer4xxResponse:
    message: str


@dataclass
class CommonDSS4xxResponse:
    message: str


@dataclass
class CommonDSS2xxResponse:
    message: str


@dataclass
class DeleteOperationalIntentResponse:
    dss_response: DeleteOperationalIntentResponseSuccess | CommonDSS4xxResponse
    status: int
    message: CommonDSS4xxResponse | CommonDSS2xxResponse


@dataclass
class OperationalIntentUpdateSuccessResponse:
    subscribers: list[SubscriberToNotify]
    operational_intent_reference: OperationalIntentReferenceDSSResponse


@dataclass
class OperationalIntentUpdateErrorResponse:
    message: str


class FlightPlanCurrentStatus(str, enum.Enum):
    NotPlanned = "NotPlanned"
    Planned = "Planned"
    OkToFly = "OkToFly"
    OffNominal = "OffNominal"
    Closed = "Closed"
    Processing = "Processing"


class OpIntUpdateCheckResultCodes(str, enum.Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    Z = "Z"

    def __str__(self):
        return str(self.value)


@dataclass
class ShouldSendtoDSSProcessingResponse:
    check_id: OpIntUpdateCheckResultCodes
    should_submit_update_payload_to_dss: int
    tentative_flight_plan_processing_response: FlightPlanCurrentStatus


@dataclass
class OperationalIntentUpdateResponse:
    dss_response: OperationalIntentUpdateSuccessResponse | CommonDSS4xxResponse | CommonPeer9xxResponse
    status: int
    message: CommonDSS4xxResponse | CommonDSS2xxResponse | str
    additional_information: ShouldSendtoDSSProcessingResponse | None = None


@dataclass
class USSNotificationResponse:
    status: int
    message: CommonDSS4xxResponse | CommonDSS2xxResponse


@dataclass
class OperationalIntentUpdateRequest:
    extents: list[Volume4D]
    state: str
    key: list[str]
    uss_base_url: str
    subscription_id: str


@dataclass
class QueryOperationalIntentPayload:
    area_of_interest: Volume4D


@dataclass
class OperationalIntentReferenceDSSDetails:
    operational_intent_reference: OperationalIntentReferenceDSSResponse
    operational_intent_id: str


@dataclass
class OpInttoCheckDetails:
    ovn: str
    shape: Plgn
    id: str
    time_start: Optional[str] = None
    time_end: Optional[str] = None


# --- Flight planning types (from flight_planning_data_definitions) ---

class FlightPlanningStatusResponse(str, enum.Enum):
    Starting = "Starting"
    Ready = "Ready"


class AdvisoryInclusion(str, enum.Enum):
    Unknown = "Unknown"
    AtLeastOneAdvisoryOrCondition = "AtLeastOneAdvisoryOrCondition"
    NoAdvisoriesOrConditions = "NoAdvisoriesOrConditions"


class PlanningActivityResult(str, enum.Enum):
    Completed = "Completed"
    Rejected = "Rejected"
    Failed = "Failed"
    NotSupported = "NotSupported"


@dataclass
class CloseFlightPlanResponse:
    planning_result: PlanningActivityResult
    notes: str | None
    flight_plan_status: FlightPlanCurrentStatus
    includes_advisories: AdvisoryInclusion | None


@dataclass
class UpsertFlightPlanResponse:
    flight_plan_status: FlightPlanCurrentStatus
    notes: str
    includes_advisories: AdvisoryInclusion | None
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


class OperationCategoryFP(Enum):
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


class UASClassFP(Enum):
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
    operation_category: OperationCategoryFP
    uas_class: UASClassFP
    identification_technologies: list[str]
    uas_type_certificate: str | None
    connectivity_methods: list[str]
    endurance_minutes: float
    emergency_procedure_url: str
    operator_id: str
    uas_id: str | None


@dataclass
class BasicFlightPlanInformation:
    usage_state: UsageState
    uas_state: UasState
    area: list[Volume4D] | None


@dataclass
class ASTMF354821OpIntentInformation:
    priority: int


@dataclass
class RPAS26FlightDetails:
    operator_type: OperatorType | None
    uas_serial_numbers: list[str] | None
    uas_registration_numbers: list[str] | None
    aircraft_type: AircraftType | None
    flight_profile: FlightProfile | None
    pilot_license_number: str | None
    pilot_phone_number: str | None
    operator_number: str | None


@dataclass
class FlightPlan:
    basic_information: BasicFlightPlanInformation
    astm_f3548_21: ASTMF354821OpIntentInformation | None
    uspace_flight_authorisation: FlightAuthorisationData | None
    rpas_operating_rules_2_6: RPAS26FlightDetails | None = None
    additional_information: dict[str, Any] | None = None


@dataclass
class FlightPlanningRequest:
    intended_flight: FlightPlan
    request_id: str


@dataclass
class FlightPlanningInjectionData:
    volumes: list[Volume4D] | None
    priority: int
    off_nominal_volumes: list[Volume4D] | None
    uas_state: UasState
    usage_state: UsageState
    state: str


@dataclass
class FlightPlanningUSSDetails:
    volumes: list[Volume4D]
    priority: int
    off_nominal_volumes: list[Volume4D] | None


# --- SCD-prefixed types (from scd/data_definitions.py) ---

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
    lat: float
    lng: float


@dataclass
class SCDRadius:
    value: float
    units: str


@dataclass
class SCDPolygon:
    vertices: list[SCDLatLngPoint]


@dataclass
class SCDCircle:
    center: SCDLatLngPoint
    radius: SCDRadius


@dataclass
class SCDAltitude:
    value: float
    reference: Literal["W84"]
    units: str


@dataclass
class SCDTime:
    value: str
    format: Literal["RFC3339"]


@dataclass
class SCDVolume3D:
    outline_circle: SCDCircle | None
    outline_polygon: SCDPolygon | None
    altitude_lower: SCDAltitude | None
    altitude_upper: SCDAltitude | None


@dataclass
class SCDVolume4D:
    volume: SCDVolume3D
    time_start: SCDTime | None
    time_end: SCDTime | None


@dataclass
class FlightDeclarationOperationalIntentStorageDetails:
    volumes: list[SCDVolume4D]
    off_nominal_volumes: list[SCDVolume4D]
    priority: int
    state: str
