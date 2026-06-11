from datetime import datetime

from pydantic import BaseModel, ConfigDict

from flight_blender.domain_types.scd import (
    AdvisoryInclusion,
    FlightPlanCurrentStatus,
    FlightPlanningStatusResponse,
    OperationCategoryFP,
    OperationMode,
    PlanningActivityResult,
    StatusResponseEnum,
    UASClassFP,
    UasState,
    UsageState,
    USSCapabilitiesResponseEnum,
)


class SCDSchema(BaseModel):
    model_config = ConfigDict(extra="ignore", from_attributes=True, use_enum_values=True)


class TimeSchema(SCDSchema):
    value: str | None = None
    format: str | None = None


class LatLngPointSchema(SCDSchema):
    lat: float | None = None
    lng: float | None = None


class RadiusSchema(SCDSchema):
    value: float | None = None
    units: str | None = None


class PolygonSchema(SCDSchema):
    vertices: list[LatLngPointSchema] | None = None


class CircleSchema(SCDSchema):
    center: LatLngPointSchema | None = None
    radius: RadiusSchema | None = None


class AltitudeSchema(SCDSchema):
    value: int | float | None = None
    reference: str | None = None
    units: str | None = None


class Volume3DSchema(SCDSchema):
    outline_polygon: PolygonSchema | None = None
    altitude_lower: AltitudeSchema | None = None
    altitude_upper: AltitudeSchema | None = None
    outline_circle: CircleSchema | None = None


class Volume4DSchema(SCDSchema):
    volume: Volume3DSchema | None = None
    time_start: TimeSchema | None = None
    time_end: TimeSchema | None = None


class ClearAreaRequestSchema(SCDSchema):
    request_id: str | None = None
    extent: Volume4DSchema | None = None


class BasicFlightPlanInformationSchema(SCDSchema):
    area: list[Volume4DSchema] | None = None
    uas_state: UasState | str | None = None
    usage_state: UsageState | str | None = None


class ASTMF354821OpIntentInformationSchema(SCDSchema):
    priority: int | None = None


class FlightAuthorisationDataSchema(SCDSchema):
    uas_serial_number: str | None = None
    operation_mode: OperationMode | str | None = None
    operation_category: OperationCategoryFP | str | None = None
    uas_class: UASClassFP | str | None = None
    identification_technologies: list[str] | None = None
    uas_type_certificate: str | None = None
    connectivity_methods: list[str] | None = None
    endurance_minutes: float | None = None
    emergency_procedure_url: str | None = None
    operator_id: str | None = None
    uas_id: str | None = None


class RPAS26FlightDetailsSchema(SCDSchema):
    operator_type: str | None = None
    uas_serial_numbers: list[str] | None = None
    uas_registration_numbers: list[str] | None = None
    aircraft_type: str | None = None
    flight_profile: str | None = None
    pilot_license_number: str | None = None
    pilot_phone_number: str | None = None
    operator_number: str | None = None


class FlightPlanSchema(SCDSchema):
    basic_information: BasicFlightPlanInformationSchema | None = None
    astm_f3548_21: ASTMF354821OpIntentInformationSchema | None = None
    uspace_flight_authorisation: FlightAuthorisationDataSchema | None = None
    rpas_operating_rules_2_6: RPAS26FlightDetailsSchema | None = None
    additional_information: dict[str, object] | None = None


class UpsertFlightPlanRequestSchema(SCDSchema):
    request_id: str | None = None
    flight_plan: FlightPlanSchema | None = None


class SCDTestStatusSchema(SCDSchema):
    status: StatusResponseEnum
    version: str


class SCDCapabilitiesSchema(SCDSchema):
    capabilities: list[USSCapabilitiesResponseEnum]


class FlightPlanningStatusSchema(SCDSchema):
    status: FlightPlanningStatusResponse
    system_version: str
    api_name: str
    api_version: str


class ClearAreaOutcomeSchema(SCDSchema):
    success: bool
    message: str
    timestamp: datetime | str


class ClearAreaResponseSchema(SCDSchema):
    outcome: ClearAreaOutcomeSchema


class UpsertFlightPlanResponseSchema(SCDSchema):
    flight_plan_status: FlightPlanCurrentStatus
    notes: str
    includes_advisories: AdvisoryInclusion | None
    planning_result: PlanningActivityResult


class CloseFlightPlanResponseSchema(SCDSchema):
    planning_result: PlanningActivityResult
    notes: str | None
    flight_plan_status: FlightPlanCurrentStatus
    includes_advisories: AdvisoryInclusion | None


class NotificationObservedAtSchema(SCDSchema):
    value: str
    format: str


class UserNotificationSchema(SCDSchema):
    observed_at: NotificationObservedAtSchema
    message: str


class UserNotificationsResponseSchema(SCDSchema):
    user_notifications: list[UserNotificationSchema]
