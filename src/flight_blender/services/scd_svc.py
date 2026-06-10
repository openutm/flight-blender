"""SCD/UTMRSS business logic."""

import inspect
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import Enum
from itertools import cycle
from typing import Any

import dacite
from dacite import from_dict
from fastapi import HTTPException
from loguru import logger

from flight_blender.clients import dss_scd_client as dss_scd_helper
from flight_blender.clients.dss_scd_client import DSSAreaClearHandler, SCDTestHarnessHelper, VolumesConverter, VolumesValidator
from flight_blender.domain_types.common import ALTITUDE_REF_LOOKUP, OPERATION_STATES, OPERATION_STATES_LOOKUP
from flight_blender.domain_types.constraint import CompositeConstraintPayload, Constraint
from flight_blender.domain_types.geo_fence import GeofencePayload
from flight_blender.domain_types.scd import (
    AdvisoryInclusion,
    ASTMF354821OpIntentInformation,
    BasicFlightPlanInformation,
    CapabilitiesResponse,
    CloseFlightPlanResponse,
    CompositeOperationalIntentPayload,
    FlightAuthorisationData,
    FlightPlan,
    FlightPlanCurrentStatus,
    FlightPlanningInjectionData,
    FlightPlanningRequest,
    FlightPlanningStatusResponse,
    FlightPlanningTestStatus,
    OperationalIntentState,
    OperationalIntentSubmissionStatus,
    OperationalIntentUSSDetails,
    PlanningActivityResult,
    RPAS26FlightDetails,
    SCDTestStatusResponse,
    StatusResponseEnum,
    TestInjectionResult,
    TestInjectionResultState,
    UpsertFlightPlanResponse,
    USSCapabilitiesResponseEnum,
)
from flight_blender.repositories.constraint_repo import SQLAlchemyConstraintRepository
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository
from flight_blender.repositories.notifications_repo import SQLAlchemyNotificationsRepository
from flight_blender.schemas.scd import (
    ClearAreaResponseSchema,
    CloseFlightPlanResponseSchema,
    FlightPlanningStatusSchema,
    NotificationObservedAtSchema,
    SCDCapabilitiesSchema,
    SCDTestStatusSchema,
    UpsertFlightPlanResponseSchema,
    UserNotificationSchema,
    UserNotificationsResponseSchema,
)


def _upsert_response(response: UpsertFlightPlanResponse) -> UpsertFlightPlanResponseSchema:
    return UpsertFlightPlanResponseSchema.model_validate(response)


def _close_response(response: CloseFlightPlanResponse) -> CloseFlightPlanResponseSchema:
    return CloseFlightPlanResponseSchema.model_validate(response)


@dataclass
class FlightPlanningContext:
    operation_id: str
    request: FlightPlanningRequest
    data: FlightPlanningInjectionData
    volumes: list[Any]
    off_nominal_volumes: list[Any]
    priority: int
    uas_state: str
    usage_state: str
    generated_state: str
    view_rect_bounds_storage: str
    raw_geojson: dict[str, Any]


# ── Test harness response constants ──────────────────────────────────────────

failed_test_injection_response = TestInjectionResult(
    result=TestInjectionResultState.Failed,
    notes="Processing of operational intent has failed",
    operational_intent_id="",
)
rejected_test_injection_response = TestInjectionResult(
    result=TestInjectionResultState.Rejected,
    notes="An existing operational intent already exists and conflicts in space and time",
    operational_intent_id="",
)
planned_test_injection_response = TestInjectionResult(
    result=TestInjectionResultState.Planned,
    notes="Successfully created operational intent in the DSS",
    operational_intent_id="",
)
conflict_with_flight_test_injection_response = TestInjectionResult(
    result=TestInjectionResultState.ConflictWithFlight,
    notes="Processing of operational intent has failed, flight not deconflicted",
    operational_intent_id="",
)
ready_to_fly_injection_response = TestInjectionResult(
    result=TestInjectionResultState.ReadyToFly,
    notes="Processing of operational intent succeeded, flight is activated",
    operational_intent_id="",
)

not_supported_planning_response = UpsertFlightPlanResponse(
    flight_plan_status=FlightPlanCurrentStatus.NotPlanned,
    notes="Flight Plan action is not supported",
    includes_advisories=AdvisoryInclusion.Unknown,
    planning_result=PlanningActivityResult.NotSupported,
)
planned_planning_response = UpsertFlightPlanResponse(
    flight_plan_status=FlightPlanCurrentStatus.Planned,
    notes="Flight Plan successfully processed and flight planned",
    includes_advisories=AdvisoryInclusion.Unknown,
    planning_result=PlanningActivityResult.Completed,
)
planned_off_nominal_planning_response = UpsertFlightPlanResponse(
    flight_plan_status=FlightPlanCurrentStatus.OffNominal,
    notes="Flight Plan successfully processed and flight planned",
    includes_advisories=AdvisoryInclusion.Unknown,
    planning_result=PlanningActivityResult.Completed,
)
ready_to_fly_planning_response = UpsertFlightPlanResponse(
    flight_plan_status=FlightPlanCurrentStatus.OkToFly,
    notes="Flight is ready to fly",
    includes_advisories=AdvisoryInclusion.Unknown,
    planning_result=PlanningActivityResult.Completed,
)
not_planned_planning_response = UpsertFlightPlanResponse(
    flight_plan_status=FlightPlanCurrentStatus.NotPlanned,
    notes="Flight Blender could not plan this flight",
    includes_advisories=AdvisoryInclusion.Unknown,
    planning_result=PlanningActivityResult.Rejected,
)
not_planned_activated_planning_response = UpsertFlightPlanResponse(
    flight_plan_status=FlightPlanCurrentStatus.Planned,
    notes="Flight Blender could not update this activated flight",
    includes_advisories=AdvisoryInclusion.Unknown,
    planning_result=PlanningActivityResult.Rejected,
)
not_planned_activated_higher_priority_planning_response = UpsertFlightPlanResponse(
    flight_plan_status=FlightPlanCurrentStatus.OkToFly,
    notes="Flight Blender could not update this activated flight",
    includes_advisories=AdvisoryInclusion.Unknown,
    planning_result=PlanningActivityResult.Rejected,
)
not_planned_closed_planning_response = UpsertFlightPlanResponse(
    flight_plan_status=FlightPlanCurrentStatus.Closed,
    notes="Flight Blender could not plan this flight",
    includes_advisories=AdvisoryInclusion.Unknown,
    planning_result=PlanningActivityResult.Rejected,
)
not_planned_already_planned_planning_response = UpsertFlightPlanResponse(
    flight_plan_status=FlightPlanCurrentStatus.Planned,
    notes="Flight Blender could not update this already planned flight",
    includes_advisories=AdvisoryInclusion.Unknown,
    planning_result=PlanningActivityResult.Rejected,
)
failed_planning_response = UpsertFlightPlanResponse(
    flight_plan_status=FlightPlanCurrentStatus.NotPlanned,
    notes="Flight Blender failed to process this flight",
    includes_advisories=AdvisoryInclusion.Unknown,
    planning_result=PlanningActivityResult.Failed,
)
flight_planning_deletion_success_response = CloseFlightPlanResponse(
    planning_result=PlanningActivityResult.Completed,
    notes="The flight was closed successfully by the USS and is now out of the UTM system.",
    flight_plan_status=FlightPlanCurrentStatus.Closed,
    includes_advisories=AdvisoryInclusion.Unknown,
)
flight_planning_deletion_failure_response = CloseFlightPlanResponse(
    planning_result=PlanningActivityResult.Failed,
    notes="The flight plan was not deleted by the system",
    flight_plan_status=FlightPlanCurrentStatus.Closed,
    includes_advisories=AdvisoryInclusion.Unknown,
)


# ── Pure processors ───────────────────────────────────────────────────────────


class FlightPlantoOperationalIntentProcessor:
    def __init__(self, flight_planning_request: FlightPlanningRequest):
        self.flight_planning_request = flight_planning_request

    def generate_operational_intent_state_from_planning_information(self):
        logger.debug("********************************")
        logger.debug(f"UAS State: {self.flight_planning_request.intended_flight.basic_information.uas_state.value}")
        logger.debug(f"Usage State: {self.flight_planning_request.intended_flight.basic_information.usage_state.value}")
        logger.debug("********************************")
        if (
            self.flight_planning_request.intended_flight.basic_information.uas_state.value == "Nominal"
            and self.flight_planning_request.intended_flight.basic_information.usage_state.value == "Planned"
        ):
            operational_intent_state = "Accepted"
        elif (
            self.flight_planning_request.intended_flight.basic_information.uas_state.value == "Nominal"
            and self.flight_planning_request.intended_flight.basic_information.usage_state.value == "InUse"
        ):
            operational_intent_state = "Activated"
        elif (
            self.flight_planning_request.intended_flight.basic_information.uas_state.value == "OffNominal"
            and self.flight_planning_request.intended_flight.basic_information.usage_state.value == "InUse"
        ):
            operational_intent_state = "Nonconforming"
        return operational_intent_state


class FlightPlanningDataProcessor:
    def __init__(self, incoming_flight_information: dict):
        self.incoming_flight_information = incoming_flight_information
        if not self.incoming_flight_information.keys() & {"intended_flight", "request_id"}:
            raise KeyError("Some requested_flight and request_id must be present in the incoming data")
        self.intended_flight_information = self.incoming_flight_information["flight_plan"]
        self.request_id = self.incoming_flight_information["request_id"]
        if not self.intended_flight_information.keys() & {
            "basic_information",
            "astm_f3548_21",
            "uspace_flight_authorisation",
            "rpas_operating_rules_2_6",
            "additional_information",
        }:
            raise KeyError("Some keys are missing")

    def process_basic_flight_plan(self, basic_information_dict) -> BasicFlightPlanInformation:
        return from_dict(data_class=BasicFlightPlanInformation, data=basic_information_dict, config=dacite.Config(cast=[Enum]))

    def process_f3548_21_flight_plan_information(self, astm_f3548_op_int_information_dict) -> ASTMF354821OpIntentInformation:
        return from_dict(data_class=ASTMF354821OpIntentInformation, data=astm_f3548_op_int_information_dict, config=dacite.Config(cast=[Enum]))

    def process_uspace_flight_authorisation_information(self, uspace_flight_authorisation_information_dict) -> FlightAuthorisationData:
        return from_dict(data_class=FlightAuthorisationData, data=uspace_flight_authorisation_information_dict, config=dacite.Config(cast=[Enum]))

    def process_rpas_operating_rules_2_6_information(self, rpas_operating_rules_2_6_information_dict) -> RPAS26FlightDetails:
        return from_dict(data_class=RPAS26FlightDetails, data=rpas_operating_rules_2_6_information_dict, config=dacite.Config(cast=[Enum]))

    def process_additional_information(self) -> dict:
        return {}

    def process_intended_flight_data(self) -> FlightPlan:
        basic_information = self.process_basic_flight_plan(basic_information_dict=self.intended_flight_information["basic_information"])
        astm_f3548_21 = self.process_f3548_21_flight_plan_information(
            astm_f3548_op_int_information_dict=self.intended_flight_information["astm_f3548_21"]
        )
        uspace_flight_authorisation = self.process_uspace_flight_authorisation_information(
            self.intended_flight_information["uspace_flight_authorisation"]
        )
        return FlightPlan(basic_information=basic_information, astm_f3548_21=astm_f3548_21, uspace_flight_authorisation=uspace_flight_authorisation)

    def process_incoming_flight_plan_data(self) -> FlightPlanningRequest:
        intended_flight = self.process_intended_flight_data()
        return FlightPlanningRequest(intended_flight=intended_flight, request_id=self.request_id)


class UAVSerialNumberValidator:
    """Validate UAV serial number per ANSI/CTA-2063-A standard."""

    def code_contains_O_or_I(self, manufacturer_code):
        m_code = [c for c in manufacturer_code]
        if "O" in m_code or "I" in m_code:
            return True
        else:
            return False

    def __init__(self, serial_number):
        self.serial_number = serial_number
        self.serial_number_length_code_points = {
            "1": 1,
            "2": 2,
            "3": 3,
            "4": 4,
            "5": 5,
            "6": 6,
            "7": 7,
            "8": 8,
            "9": 9,
            "A": 10,
            "B": 11,
            "C": 12,
            "D": 13,
            "E": 14,
            "F": 15,
        }
        self.serial_number_code_points = [
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "A",
            "B",
            "C",
            "D",
            "E",
            "F",
            "G",
            "H",
            "J",
            "K",
            "L",
            "M",
            "N",
            "P",
            "Q",
            "R",
            "S",
            "T",
            "U",
            "V",
            "W",
            "X",
            "Y",
            "Z",
        ]

    def is_valid(self):
        manufacturer_code = self.serial_number[:4]
        if not len(manufacturer_code):
            return False
        if self.code_contains_O_or_I(manufacturer_code=manufacturer_code):
            return False
        character_length_code = self.serial_number[4:5]
        if character_length_code not in self.serial_number_length_code_points.keys():
            return False
        manufacturers_code = self.serial_number[5:]
        if len(manufacturers_code) != self.serial_number_length_code_points[character_length_code]:
            return False
        return True


class OperatorRegistrationNumberValidator:
    """Validate Operator Registration number per EN4709-02 standard."""

    def __init__(self, operator_registration_number):
        self.operator_registration_number = operator_registration_number
        self.registration_number_code_points = [
            "0",
            "1",
            "2",
            "3",
            "4",
            "5",
            "6",
            "7",
            "8",
            "9",
            "a",
            "b",
            "c",
            "d",
            "e",
            "f",
            "g",
            "h",
            "i",
            "j",
            "k",
            "l",
            "m",
            "n",
            "o",
            "p",
            "q",
            "r",
            "s",
            "t",
            "u",
            "v",
            "w",
            "x",
            "y",
            "z",
        ]

    def gen_checksum(self, raw_id):
        if not raw_id.isalnum():
            raise ValueError("raw_id must be alphanumeric")
        if len(raw_id) != 15:
            raise ValueError("raw_id must be 15 characters long")
        d = {v: k for k, v in enumerate(self.registration_number_code_points)}
        numeric_base_id = list(map(d.__getitem__, list(raw_id)))
        mult_factors = cycle([2, 1])

        def partial_sum(number, mult_factor):
            quotient, remainder = divmod(number * mult_factor, 36)
            return quotient + remainder

        final_sum = sum(partial_sum(int(character), mult_factor) for character, mult_factor in zip(numeric_base_id, mult_factors))
        control_number = -final_sum % 36
        return self.registration_number_code_points[control_number]

    def is_valid(self):
        try:
            oprn, secure_characters = self.operator_registration_number.split("-")
            if len(oprn) != 16:
                return False
            if len(secure_characters) != 3:
                return False
            base_id = oprn[3:-1]
            if not base_id.isalnum():
                return False
            checksum = self.operator_registration_number[-5]
            random_three_alnum_string = self.operator_registration_number[-3:]
            computed_checksum = self.gen_checksum(base_id + random_three_alnum_string)
            if computed_checksum != checksum:
                return False
            return True
        except ValueError:
            return False


# ── Sync orchestrators (extracted from scd_api.py) ────────────────────────────


def get_scd_test_status() -> SCDTestStatusSchema:
    status = SCDTestStatusResponse(status=StatusResponseEnum.Ready, version="latest")
    return SCDTestStatusSchema.model_validate(status)


def get_scd_test_capabilities() -> SCDCapabilitiesSchema:
    status = CapabilitiesResponse(
        capabilities=[
            USSCapabilitiesResponseEnum.BasicStrategicConflictDetection,
            USSCapabilitiesResponseEnum.FlightAuthorisationValidation,
            USSCapabilitiesResponseEnum.HighPriorityFlights,
        ]
    )
    return SCDCapabilitiesSchema.model_validate(status)


def get_flight_planning_status() -> FlightPlanningStatusSchema:
    status = FlightPlanningTestStatus(
        status=FlightPlanningStatusResponse.Ready,
        system_version="v0.1",
        api_name="Flight Planning Automated Testing Interface",
        api_version="latest",
    )
    return FlightPlanningStatusSchema.model_validate(status)


async def clear_area(request_data: dict) -> ClearAreaResponseSchema:
    try:
        request_id = request_data["request_id"]
        extent_raw = request_data["extent"]
    except KeyError as ke:
        raise HTTPException(status_code=400, detail={"result": "Could not parse clear area payload, expected key %s not found " % ke})
    handler = DSSAreaClearHandler(request_id=request_id)
    clear_area_response = await handler.clear_area_request(extent_raw=extent_raw)
    return ClearAreaResponseSchema.model_validate(clear_area_response)


class ConstraintsWriter:
    def __init__(self, constraint_repo: SQLAlchemyConstraintRepository):
        self.constraint_repo = constraint_repo

    async def write_nearby_constraints(self, constraints: list[Constraint], flight_declaration: Any):
        my_volumes_converter = VolumesConverter()
        for constraint in constraints:
            constraint_reference = constraint.reference
            constraint_details = constraint.details
            ref_uuid = uuid.UUID(str(constraint_reference.id))

            existing_ref = await self.constraint_repo.get_constraint_reference_by_id(ref_uuid)
            if existing_ref is not None:
                existing_geo = await self.constraint_repo.get_geofence_by_constraint_reference_id(ref_uuid)
                geofence_id = str(existing_geo.id) if existing_geo else str(uuid.uuid4())
            else:
                geofence_id = str(uuid.uuid4())

            my_volumes_converter.convert_volumes_to_geojson(volumes=constraint_details.volumes)
            altitude_ref_int = ALTITUDE_REF_LOOKUP.get(my_volumes_converter.altitude_ref, 4)
            bounds = my_volumes_converter.get_bounds()
            bounds_str = ",".join(map(str, bounds))
            geofence_payload = GeofencePayload(
                id=geofence_id,
                raw_geo_fence=my_volumes_converter.geo_json,
                upper_limit=my_volumes_converter.upper_altitude,
                lower_limit=my_volumes_converter.lower_altitude,
                altitude_ref=altitude_ref_int,
                name=constraint_details.geozone.name,
                bounds=bounds_str,
                status="1",
                message="Constraint from peer USS",
                is_test_dataset=False,
                start_datetime=constraint_reference.time_start,
                end_datetime=constraint_reference.time_end,
                geozone=asdict(constraint_details.geozone),
            )

            geo_fence = await self.constraint_repo.create_or_update_geofence(geofence_payload=geofence_payload)
            constraint_reference_obj = await self.constraint_repo.create_or_update_constraint_reference(
                constraint_reference=constraint_reference,
                geofence_id=geo_fence.id,
                declaration_id=flight_declaration.id,
            )
            constraint_detail_obj = await self.constraint_repo.create_or_update_constraint_detail(
                constraint=constraint_details,
                geofence_id=geo_fence.id,
            )
            composite_constraint_payload = CompositeConstraintPayload(
                constraint_reference_id=str(constraint_reference_obj.id),
                constraint_detail_id=str(constraint_detail_obj.id),
                flight_declaration_id=str(flight_declaration.id),
                bounds=bounds_str,
                start_datetime=constraint_reference_obj.time_start,
                end_datetime=constraint_reference_obj.time_end,
                alt_max=my_volumes_converter.upper_altitude,
                alt_min=my_volumes_converter.lower_altitude,
            )
            await self.constraint_repo.create_or_update_composite_constraint(payload=composite_constraint_payload)


class SCDService:
    def __init__(
        self,
        fd_repo: SQLAlchemyFlightDeclarationRepository,
        notifications_repo: SQLAlchemyNotificationsRepository | None = None,
    ) -> None:
        self.fd_repo = fd_repo
        self.notifications_repo = notifications_repo

    async def upsert_flight_plan(self, flight_plan_id: str, request_data: dict) -> UpsertFlightPlanResponseSchema:
        scd_helper = dss_scd_helper.SCDOperations()
        volumes_validator = VolumesValidator()
        ctx = self._build_flight_planning_context(str(flight_plan_id), request_data)

        validation_response = self._validate_flight_plan(ctx, volumes_validator)
        if validation_response is not None:
            return validation_response

        auth_token = await scd_helper.get_auth_token()
        if not auth_token or "error" in auth_token:
            return _upsert_response(failed_planning_response)

        test_harness_helper = SCDTestHarnessHelper(fd_repo=self.fd_repo)
        flight_plan_exists_in_flight_blender = await test_harness_helper.check_if_same_flight_id_exists(operation_id=ctx.operation_id)

        if flight_plan_exists_in_flight_blender:
            return await self._update_existing_flight_plan(ctx, scd_helper)
        return await self._create_new_flight_plan(ctx, scd_helper, volumes_validator)

    def _build_flight_planning_context(self, operation_id: str, request_data: dict) -> FlightPlanningContext:
        try:
            processor = FlightPlanningDataProcessor(incoming_flight_information=request_data)
        except KeyError as ke:
            raise HTTPException(status_code=500, detail={"result": "Could not parse flight plan payload: %s" % ke})

        scd_test_data = processor.process_incoming_flight_plan_data()
        op_int_bridge = FlightPlantoOperationalIntentProcessor(flight_planning_request=scd_test_data)
        volumes = scd_test_data.intended_flight.basic_information.area
        priority = scd_test_data.intended_flight.astm_f3548_21.priority or 0
        off_nominal_volumes: list[Any] = []
        planning_data = FlightPlanningInjectionData(
            volumes=volumes,
            priority=priority,
            off_nominal_volumes=off_nominal_volumes,
            uas_state=scd_test_data.intended_flight.basic_information.uas_state.value,
            usage_state=scd_test_data.intended_flight.basic_information.usage_state.value,
            state="Accepted",
        )
        volume_converter = VolumesConverter()
        volume_converter.convert_volumes_to_geojson(volumes=volumes)
        view_rect_bounds = volume_converter.get_bounds()
        return FlightPlanningContext(
            operation_id=operation_id,
            request=scd_test_data,
            data=planning_data,
            volumes=volumes,
            off_nominal_volumes=off_nominal_volumes,
            priority=priority,
            uas_state=planning_data.uas_state,
            usage_state=planning_data.usage_state,
            generated_state=op_int_bridge.generate_operational_intent_state_from_planning_information(),
            view_rect_bounds_storage=",".join([str(i) for i in view_rect_bounds]),
            raw_geojson=volume_converter.geo_json,
        )

    def _validate_flight_plan(
        self, ctx: FlightPlanningContext, volumes_validator: VolumesValidator
    ) -> UpsertFlightPlanResponseSchema | None:
        data_validator = dss_scd_helper.FlightPlanningDataValidator(incoming_flight_planning_data=ctx.data)
        if not data_validator.validate_flight_planning_test_data():
            return _upsert_response(not_planned_planning_response)
        if not volumes_validator.validate_volumes(volumes=ctx.volumes):
            return _upsert_response(not_planned_planning_response)
        auth = ctx.request.intended_flight.uspace_flight_authorisation
        if not UAVSerialNumberValidator(serial_number=auth.uas_serial_number).is_valid():
            return _upsert_response(not_planned_planning_response)
        if not OperatorRegistrationNumberValidator(operator_registration_number=auth.operator_id).is_valid():
            return _upsert_response(not_planned_planning_response)
        return None

    async def _update_existing_flight_plan(
        self, ctx: FlightPlanningContext, scd_helper: dss_scd_helper.SCDOperations
    ) -> UpsertFlightPlanResponseSchema:
        opint_parser = dss_scd_helper.OperationalIntentReferenceHelper()
        existing_op_int_details = await opint_parser.parse_stored_operational_intent_details(operation_id=ctx.operation_id)
        flight_declaration = await self.fd_repo.get_by_id(uuid.UUID(ctx.operation_id))
        if not flight_declaration:
            failed_planning_response.notes = "Flight Declaration with ID %s not found in Flight Blender" % ctx.operation_id
            return _upsert_response(failed_planning_response)

        current_state_str = OPERATION_STATES[flight_declaration.state][1]
        stored_details = await opint_parser.parse_and_load_stored_flight_operational_intent_reference(operation_id=ctx.operation_id)
        update_job = await scd_helper.update_specified_operational_intent_reference(
            operational_intent_ref_id=str(stored_details.reference.id),
            extents=ctx.volumes,
            new_state=ctx.generated_state,
            current_state=current_state_str,
            subscription_id=stored_details.reference.subscription_id,
            deconfliction_check=self._should_deconflict(current_state_str, ctx.generated_state),
            priority=ctx.request.intended_flight.astm_f3548_21.priority,
            ovn=stored_details.reference.ovn,
        )
        if update_job.status == 200:
            return await self._handle_successful_update(ctx, update_job, stored_details, existing_op_int_details)
        if update_job.status == 999:
            return self._handle_update_rejection(ctx, update_job, current_state_str)
        return _upsert_response(failed_planning_response)

    def _should_deconflict(self, current_state: str, generated_state: str) -> bool:
        return not (current_state in ["Accepted", "Activated"] and generated_state == "Nonconforming")

    async def _handle_successful_update(
        self, ctx: FlightPlanningContext, update_job: Any, stored_details: Any, existing_op_int_details: Any
    ) -> UpsertFlightPlanResponseSchema:
        fd_repo = self.fd_repo
        opint_reference = await fd_repo.get_opint_reference_by_id(uuid.UUID(str(stored_details.reference.id)))
        if opint_reference is None:
            raise HTTPException(status_code=404, detail={"message": "Flight operational intent reference not found"})
        flight_declaration = await fd_repo.get_by_id(opint_reference.declaration_id)
        if flight_declaration is None:
            raise HTTPException(status_code=404, detail={"message": "Flight declaration not found"})
        opint_details = await fd_repo.get_opint_detail_by_declaration_id(flight_declaration.id)
        if opint_details is None:
            raise HTTPException(status_code=404, detail={"message": "Flight operational intent details not found"})
        await fd_repo.update_opint_reference(ref_id=opint_reference.id, payload=update_job.dss_response.operational_intent_reference)
        notification_details = OperationalIntentUSSDetails(
            volumes=ctx.volumes or [],
            off_nominal_volumes=ctx.off_nominal_volumes,
            priority=ctx.priority,
        )
        await fd_repo.update_opint_detail(
            detail_id=opint_details.id,
            payload=notification_details,
        )
        scd_helper = dss_scd_helper.SCDOperations()
        await scd_helper.process_peer_uss_notifications(
            all_subscribers=update_job.dss_response.subscribers,
            operational_intent_details=notification_details,
            operational_intent_reference=update_job.dss_response.operational_intent_reference,
            operational_intent_id=str(opint_reference.id),
        )
        if ctx.generated_state == "Activated":
            return await self._finish_activated_update(ctx, update_job, flight_declaration, opint_reference, opint_details)
        if ctx.generated_state == "Nonconforming":
            return await self._finish_nonconforming_update(ctx, existing_op_int_details, flight_declaration)
        return _upsert_response(failed_planning_response)

    async def _finish_activated_update(
        self, ctx: FlightPlanningContext, update_job: Any, flight_declaration: Any, opint_reference: Any, opint_details: Any
    ) -> UpsertFlightPlanResponseSchema:
        ready_to_fly_planning_response.notes = f"Created Operational Intent ID {opint_reference.id}"
        await self.fd_repo.update(uuid.UUID(ctx.operation_id), state=2)
        await self.fd_repo.create_opint_reference_subscribers(
            declaration_id=flight_declaration.id,
            subscribers=update_job.dss_response.subscribers,
        )
        await self.fd_repo.create_or_update_composite_opint(
            declaration_id=flight_declaration.id,
            payload=self._composite_opint_payload(ctx, str(opint_reference.id), str(opint_details.id)),
        )
        return _upsert_response(ready_to_fly_planning_response)

    async def _finish_nonconforming_update(
        self, ctx: FlightPlanningContext, existing_op_int_details: Any, flight_declaration: Any
    ) -> UpsertFlightPlanResponseSchema:
        await self.fd_repo.update(uuid.UUID(ctx.operation_id), state=3)
        existing_op_int_details.operational_intent_details.off_nominal_volumes = ctx.volumes
        existing_op_int_details.success_response.operational_intent_reference.state = OperationalIntentState.Nonconforming
        existing_op_int_details.operational_intent_details.state = OperationalIntentState.Nonconforming
        await self.fd_repo.create_or_update_composite_opint(declaration_id=flight_declaration.id, payload=existing_op_int_details)
        return _upsert_response(planned_off_nominal_planning_response)

    def _handle_update_rejection(self, ctx: FlightPlanningContext, update_job: Any, current_state: str) -> UpsertFlightPlanResponseSchema:
        additional = update_job.additional_information
        if additional.check_id.value == "B":
            if additional.tentative_flight_plan_processing_response.value == "OkToFly":
                return _upsert_response(not_planned_activated_higher_priority_planning_response)
            return _upsert_response(not_planned_activated_planning_response)
        if ctx.priority == 100:
            return _upsert_response(not_planned_activated_higher_priority_planning_response)
        if current_state in ("Accepted", "Activated"):
            return _upsert_response(not_planned_already_planned_planning_response)
        return _upsert_response(not_planned_planning_response)

    async def _create_new_flight_plan(
        self, ctx: FlightPlanningContext, scd_helper: dss_scd_helper.SCDOperations, volumes_validator: VolumesValidator
    ) -> UpsertFlightPlanResponseSchema:
        flight_declaration = await self._create_flight_declaration(ctx)
        if not volumes_validator.pre_operational_intent_creation_checks(volumes=ctx.volumes):
            return _upsert_response(not_planned_planning_response)

        submission = await scd_helper.create_and_submit_operational_intent_reference(
            state=ctx.generated_state,
            volumes=ctx.volumes,
            off_nominal_volumes=ctx.volumes if ctx.uas_state in ["OffNominal", "Contingent"] else [],
            priority=ctx.priority,
        )
        return await self._handle_new_flight_submission(ctx, scd_helper, flight_declaration, submission)

    async def _create_flight_declaration(self, ctx: FlightPlanningContext):
        return await self.fd_repo.create(
            id=uuid.UUID(ctx.operation_id),
            operational_intent=json.dumps(asdict(ctx.data)),
            flight_declaration_raw_geojson=json.dumps(ctx.raw_geojson) if ctx.raw_geojson else "",
            bounds=ctx.view_rect_bounds_storage,
            aircraft_id="0000",
            state=OPERATION_STATES_LOOKUP[ctx.generated_state],
        )

    async def _handle_new_flight_submission(
        self, ctx: FlightPlanningContext, scd_helper: dss_scd_helper.SCDOperations, flight_declaration: Any, submission: OperationalIntentSubmissionStatus
    ) -> UpsertFlightPlanResponseSchema:
        if submission.status == "success":
            await self._store_successful_new_submission(ctx, scd_helper, flight_declaration, submission)
            return _upsert_response(planned_planning_response)
        if submission.status == "conflict_with_flight":
            return _upsert_response(not_planned_planning_response)
        if submission.status in ["failure", "peer_uss_data_sharing_issue"]:
            return self._new_submission_failure_response(submission)
        return _upsert_response(planned_planning_response)

    async def _store_successful_new_submission(
        self, ctx: FlightPlanningContext, scd_helper: dss_scd_helper.SCDOperations, flight_declaration: Any, submission: OperationalIntentSubmissionStatus
    ) -> None:
        ctx.data.state = ctx.generated_state
        opint_details = OperationalIntentUSSDetails(
            volumes=ctx.data.volumes,
            off_nominal_volumes=ctx.data.off_nominal_volumes,
            priority=ctx.data.priority,
        )
        opint_detail = await self.fd_repo.create_opint_detail(declaration_id=flight_declaration.id, payload=opint_details)
        opint_reference = await self.fd_repo.create_opint_reference(
            declaration_id=flight_declaration.id,
            payload=submission.dss_response.operational_intent_reference,
        )
        await self.fd_repo.create_opint_reference_subscribers(
            declaration_id=flight_declaration.id,
            subscribers=submission.dss_response.subscribers,
        )
        await self.fd_repo.create_or_update_composite_opint(
            declaration_id=flight_declaration.id,
            payload=self._composite_opint_payload(ctx, str(opint_reference.id), str(opint_detail.id), alt_max=50, alt_min=25),
        )
        if submission.constraints:
            await ConstraintsWriter(SQLAlchemyConstraintRepository(self.fd_repo.db)).write_nearby_constraints(
                flight_declaration=flight_declaration,
                constraints=submission.constraints,
            )
        await scd_helper.process_peer_uss_notifications(
            all_subscribers=submission.dss_response.subscribers,
            operational_intent_details=ctx.data,
            operational_intent_reference=submission.dss_response.operational_intent_reference,
            operational_intent_id=submission.operational_intent_id,
        )
        planned_test_injection_response.operational_intent_id = submission.operational_intent_id

    def _new_submission_failure_response(self, submission: OperationalIntentSubmissionStatus) -> UpsertFlightPlanResponseSchema:
        if submission.status_code in [408, 409]:
            return _upsert_response(not_planned_planning_response)
        return _upsert_response(failed_planning_response)

    def _composite_opint_payload(
        self, ctx: FlightPlanningContext, opint_reference_id: str, opint_detail_id: str, alt_max: float | None = None, alt_min: float | None = None
    ) -> CompositeOperationalIntentPayload:
        first_volume = ctx.request.intended_flight.basic_information.area[0]
        return CompositeOperationalIntentPayload(
            bounds=ctx.view_rect_bounds_storage,
            start_datetime=first_volume.time_start.value,
            end_datetime=first_volume.time_end.value,
            alt_max=alt_max if alt_max is not None else first_volume.volume.altitude_upper.value,
            alt_min=alt_min if alt_min is not None else first_volume.volume.altitude_lower.value,
            operational_intent_reference_id=opint_reference_id,
            operational_intent_details_id=opint_detail_id,
        )

    async def delete_flight_plan(self, flight_plan_id: str) -> CloseFlightPlanResponseSchema:
        operation_id_str = str(flight_plan_id)
        my_scd_dss_helper = dss_scd_helper.SCDOperations()

        fd_repo = self.fd_repo
        flight_operational_intent_reference = await fd_repo.get_opint_reference_by_declaration_id(uuid.UUID(operation_id_str))
        opint_id = flight_operational_intent_reference.id if flight_operational_intent_reference else None
        ovn = flight_operational_intent_reference.ovn if flight_operational_intent_reference else None

        if not flight_operational_intent_reference or ovn is None:
            return _close_response(flight_planning_deletion_failure_response)
        deletion_response = await my_scd_dss_helper.delete_operational_intent(dss_operational_intent_ref_id=str(opint_id), ovn=ovn)
        if deletion_response.status == 200:
            await fd_repo.delete(uuid.UUID(operation_id_str))
            return _close_response(flight_planning_deletion_success_response)
        return _close_response(flight_planning_deletion_failure_response)

    async def query_user_notifications(self, after: datetime, before: datetime | None) -> UserNotificationsResponseSchema:
        if self.notifications_repo is None:
            message = "notifications_repo is required to query user notifications"
            raise RuntimeError(message)
        notifications = await self.notifications_repo.get_active_notifications_between(after, before or datetime.now(UTC))
        return UserNotificationsResponseSchema(
            user_notifications=[
                UserNotificationSchema(
                    observed_at=NotificationObservedAtSchema(
                        value=notification.created_at.isoformat() if notification.created_at else datetime.now(UTC).isoformat(),
                        format="RFC3339",
                    ),
                    message=notification.message,
                )
                for notification in notifications
            ],
        )
