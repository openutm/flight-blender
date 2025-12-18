import enum
from dataclasses import dataclass
from typing import Literal, Optional

from implicitdict import StringBasedDateTime
from shapely.geometry import Polygon as Plgn

from constraint_operations.data_definitions import Constraint


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
class Time:
    """A class to hold time objects"""

    format: str
    value: str


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
class OperationalIntentStorageVolumes:
    volumes: list[Volume4D]


@dataclass
class OperationalIntentTestInjection:
    """Class for keeping track of an operational intent test injections"""

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
    """A enum to hold all categories of an operation"""

    Vlos = "vlos"
    Bvlos = "bvlos"


class UASClass(str, enum.Enum):
    """A enum to hold all UAS Classes"""

    C0 = "C0"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"
    C4 = "C4"


class DeleteFlightStatus(str, enum.Enum):
    Closed = "Closed"
    Failed = "Failed"


class TestInjectionResultState(str, enum.Enum):
    """A test is either pass or fail or could not be processed, currently not"""

    Planned = "Planned"
    Rejected = "Rejected"
    ConflictWithFlight = "ConflictWithFlight"
    ReadyToFly = "ReadyToFly"
    Failed = "Failed"


class IDTechnology(str, enum.Enum):
    """A enum to hold ID technologies for an operation"""

    Network = "network"
    Broadcast = "broadcast"


class StatusResponseEnum(str, enum.Enum):
    """A enum to specify if the USS is ready (or not)"""

    Starting = "Starting"
    Ready = "Ready"


class DeleteFlightStatusResponseEnum(str, enum.Enum):
    """A enum to hold Flight Status"""

    Closed = "Closed"
    Failed = "Failed"


class USSCapabilitiesResponseEnum(str, enum.Enum):
    """A enum to hold USS capabilities operation"""

    BasicStrategicConflictDetection = "BasicStrategicConflictDetection"
    FlightAuthorisationValidation = "FlightAuthorisationValidation"
    HighPriorityFlights = "HighPriorityFlights"


@dataclass
class FlightAuthorizationDataPayload:
    """A class to hold information about Flight Authorization Test"""

    uas_serial_number: str
    operation_mode: Literal[OperationCategory.Vlos, OperationCategory.Bvlos]
    operation_category: str
    uas_class: Literal[
        UASClass.C0,
        UASClass.C1,
        UASClass.C2,
        UASClass.C3,
        UASClass.C4,
    ]
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
    """Delete flight status response"""

    result: Literal[DeleteFlightStatusResponseEnum.Failed, DeleteFlightStatusResponseEnum.Closed]
    notes: str


@dataclass
class ClearAreaResponseOutcome:
    """Response after clearing flights in an area"""

    success: bool
    message: str
    timestamp: StringBasedDateTime


@dataclass
class ClearAreaResponse:
    outcome: ClearAreaResponseOutcome


@dataclass
class ClearAreaRequestData:
    """Request to clear flights in an area"""

    request_id: str
    extent: Volume4D


@dataclass
class ImplicitSubscriptionParameters:
    uss_base_url: str
    notify_for_constraints: bool = False


@dataclass
class OperationalIntentReference:
    """A operational intent reference for the DSS"""

    extents: list[Volume4D]
    key: list[str]
    state: str
    uss_base_url: str
    new_subscription: ImplicitSubscriptionParameters | None = None


@dataclass
class PartialCreateOperationalIntentReference:
    """A operational intent reference for the DSS that is stored in the Database"""

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
    """This method holds details of the data the DSS provides once a operational intent is deleted"""

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
    """This method holds information to send to the DSS to delete a Operational intent"""

    entity_id: str
    ovn: str


@dataclass
class DeleteOperationalIntentResponseSuccess:
    """This method holds details of the data the DSS provides once a operational intent is deleted"""

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
    Processing = "Processing"  # Internal Flight Blender status


class OpIntUpdateCheckResultCodes(str, enum.Enum):
    """A set of codes to specify why an operational update should not be sent to the DSS by the USS.
    A - If the current state is Activate and new state is Non-conforming or Contingent an update request should be sent to DSS
    B - If the current state is activated and new state is also activated but the new extents conflict with existing DSS volumes, the update request should not be sent to the DSS
    C - If the current state is activate and new state is also activate and the volumes dont intersect with the volumes in the DSS the update request should be sent to the DSS
    D - If the priority of the updated request is 100 then it should be submitted to the DSS
    E - If the extents conflict then dont submit
    F-  If the extents dont conflict then submit it
    Z - Default state
    """

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
