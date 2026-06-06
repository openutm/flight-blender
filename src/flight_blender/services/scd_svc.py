"""SCD/UTMRSS business logic."""

import inspect
import json
import uuid
from dataclasses import asdict
from enum import Enum
from itertools import cycle
from typing import Any

import dacite
from dacite import from_dict
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
    FlightDeclarationCreationPayload,
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
    TestInjectionResult,
    TestInjectionResultState,
    UpsertFlightPlanResponse,
    USSCapabilitiesResponseEnum,
)
from flight_blender.repositories.constraint_repo import SQLAlchemyConstraintRepository
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository
from flight_blender.utils.json_codecs import EnhancedJSONEncoder


async def _await_if_needed(value):
    if inspect.isawaitable(value):
        return await value
    return value


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

    def generate_operational_intent_state_from_planning_information(self, current_state: str = None):
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


def get_scd_test_status() -> dict:
    status = SCDTestStatusResponse(status="Ready", version="latest")
    return json.loads(json.dumps(status, cls=EnhancedJSONEncoder))


def get_scd_test_capabilities() -> dict:
    status = CapabilitiesResponse(
        capabilities=[
            USSCapabilitiesResponseEnum.BasicStrategicConflictDetection,
            USSCapabilitiesResponseEnum.FlightAuthorisationValidation,
            USSCapabilitiesResponseEnum.HighPriorityFlights,
        ]
    )
    return json.loads(json.dumps(status, cls=EnhancedJSONEncoder))


def get_flight_planning_status() -> dict:
    status = FlightPlanningTestStatus(
        status=FlightPlanningStatusResponse.Ready,
        system_version="v0.1",
        api_name="Flight Planning Automated Testing Interface",
        api_version="latest",
    )
    return json.loads(json.dumps(status, cls=EnhancedJSONEncoder))


async def clear_area(request_data: dict) -> tuple[dict, int]:
    try:
        request_id = request_data["request_id"]
        extent_raw = request_data["extent"]
    except KeyError as ke:
        return {"result": "Could not parse clear area payload, expected key %s not found " % ke}, 400
    handler = DSSAreaClearHandler(request_id=request_id)
    clear_area_response = await handler.clear_area_request(extent_raw=extent_raw)
    return json.loads(json.dumps(clear_area_response, cls=EnhancedJSONEncoder)), 200


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
                status=1,
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
    def __init__(self, fd_repo: SQLAlchemyFlightDeclarationRepository) -> None:
        self.fd_repo = fd_repo

    async def upsert_flight_plan(self, flight_plan_id: str, request_data: dict) -> tuple[dict, int]:
        my_operational_intent_parser = dss_scd_helper.OperationalIntentReferenceHelper()
        my_scd_dss_helper = dss_scd_helper.SCDOperations()
        my_geo_json_converter = VolumesConverter()
        my_volumes_validator = VolumesValidator()

        operation_id_str = str(flight_plan_id)

        scd_test_data = request_data
        try:
            my_flight_plan_processor = FlightPlanningDataProcessor(incoming_flight_information=scd_test_data)
        except KeyError as ke:
            return {"result": "Could not parse flight plan payload: %s" % ke}, 500
        scd_test_data = my_flight_plan_processor.process_incoming_flight_plan_data()
        my_flight_plan_op_intent_bridge = FlightPlantoOperationalIntentProcessor(flight_planning_request=scd_test_data)

        flight_planning_off_nominal_volumes = []
        flight_planning_volumes = scd_test_data.intended_flight.basic_information.area
        flight_planning_priority = scd_test_data.intended_flight.astm_f3548_21.priority if scd_test_data.intended_flight.astm_f3548_21.priority else 0
        flight_planning_uas_state = scd_test_data.intended_flight.basic_information.uas_state.value
        flight_planning_usage_state = scd_test_data.intended_flight.basic_information.usage_state.value

        flight_planning_data = FlightPlanningInjectionData(
            volumes=flight_planning_volumes,
            priority=flight_planning_priority,
            off_nominal_volumes=flight_planning_off_nominal_volumes,
            uas_state=flight_planning_uas_state,
            usage_state=flight_planning_usage_state,
            state="Accepted",
        )

        my_flight_planning_data_validator = dss_scd_helper.FlightPlanningDataValidator(incoming_flight_planning_data=flight_planning_data)
        flight_planning_data_valid = my_flight_planning_data_validator.validate_flight_planning_test_data()

        if not flight_planning_data_valid:
            return json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)), 200

        volumes_valid = my_volumes_validator.validate_volumes(volumes=scd_test_data.intended_flight.basic_information.area)
        if not volumes_valid:
            return json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)), 200

        my_serial_number_validator = UAVSerialNumberValidator(
            serial_number=scd_test_data.intended_flight.uspace_flight_authorisation.uas_serial_number
        )
        my_reg_number_validator = OperatorRegistrationNumberValidator(
            operator_registration_number=scd_test_data.intended_flight.uspace_flight_authorisation.operator_id
        )

        if not my_serial_number_validator.is_valid():
            return json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)), 200

        if not my_reg_number_validator.is_valid():
            return json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)), 200

        auth_token = my_scd_dss_helper.get_auth_token()
        if not auth_token or "error" in auth_token:
            return json.loads(json.dumps(failed_planning_response, cls=EnhancedJSONEncoder)), 200

        my_geo_json_converter.convert_volumes_to_geojson(volumes=flight_planning_volumes)
        view_rect_bounds = my_geo_json_converter.get_bounds()
        view_rect_bounds_storage = ",".join([str(i) for i in view_rect_bounds])

        my_test_harness_helper = SCDTestHarnessHelper(fd_repo=self.fd_repo)
        flight_plan_exists_in_flight_blender = await _await_if_needed(
            my_test_harness_helper.check_if_same_flight_id_exists(operation_id=operation_id_str)
        )

        flight_planning_notification_payload = flight_planning_data
        generated_operational_intent_state = my_flight_plan_op_intent_bridge.generate_operational_intent_state_from_planning_information()

        if flight_plan_exists_in_flight_blender:
            existing_op_int_details = await my_operational_intent_parser.parse_stored_operational_intent_details(operation_id=operation_id_str)
            fd_repo = self.fd_repo
            flight_declaration = await fd_repo.get_by_id(uuid.UUID(operation_id_str))
            if not flight_declaration:
                failed_planning_response.notes = "Flight Declaration with ID %s not found in Flight Blender" % operation_id_str
                return json.loads(json.dumps(failed_planning_response, cls=EnhancedJSONEncoder)), 200
            flight_operational_intent_reference = await fd_repo.get_opint_reference_by_declaration_id(flight_declaration.id)
            current_state = flight_declaration.state
            dss_operational_intent_reference_id = str(flight_operational_intent_reference.id) if flight_operational_intent_reference else None
            current_state_str = OPERATION_STATES[current_state][1]
            stored_operational_intent_details = await my_operational_intent_parser.parse_and_load_stored_flight_operational_intent_reference(
                operation_id=operation_id_str
            )
            provided_volumes_off_nominal_volumes = scd_test_data.intended_flight.basic_information.area
            deconfliction_check = True

            if current_state_str in ["Accepted", "Activated"] and generated_operational_intent_state == "Nonconforming":
                deconfliction_check = False
            elif current_state_str == "Activated" and generated_operational_intent_state == "Activated":
                deconfliction_check = True

            operational_intent_update_job = await _await_if_needed(
                my_scd_dss_helper.update_specified_operational_intent_reference(
                    operational_intent_ref_id=str(stored_operational_intent_details.reference.id),
                    extents=provided_volumes_off_nominal_volumes,
                    new_state=generated_operational_intent_state,
                    current_state=current_state_str,
                    subscription_id=stored_operational_intent_details.reference.subscription_id,
                    deconfliction_check=deconfliction_check,
                    priority=scd_test_data.intended_flight.astm_f3548_21.priority,
                    ovn=stored_operational_intent_details.reference.ovn,
                )
            )

            if operational_intent_update_job.status == 200:
                flight_operational_intent_reference = await fd_repo.get_opint_reference_by_id(
                    uuid.UUID(str(stored_operational_intent_details.reference.id))
                )
                if flight_operational_intent_reference is None:
                    return {"message": "Flight operational intent reference not found"}, 404
                flight_declaration = await fd_repo.get_by_id(flight_operational_intent_reference.declaration_id)
                flight_operational_intent_details = await fd_repo.get_opint_detail_by_declaration_id(flight_declaration.id)
                await fd_repo.update_opint_reference(
                    ref_id=flight_operational_intent_reference.id,
                    payload=operational_intent_update_job.dss_response.operational_intent_reference,
                )
                updated_flight_operational_intent_details = OperationalIntentUSSDetails(
                    volumes=flight_planning_volumes or [],  # type: ignore[arg-type]
                    off_nominal_volumes=flight_planning_off_nominal_volumes,
                    priority=flight_planning_priority,
                )
                await fd_repo.update_opint_detail(
                    detail_id=flight_operational_intent_details.id,
                    payload=updated_flight_operational_intent_details,
                )
                my_scd_dss_helper.process_peer_uss_notifications(
                    all_subscribers=operational_intent_update_job.dss_response.subscribers,
                    operational_intent_details=flight_planning_notification_payload,
                    operational_intent_reference=operational_intent_update_job.dss_response.operational_intent_reference,
                    operational_intent_id=dss_operational_intent_reference_id,
                )

                if generated_operational_intent_state == "Activated":
                    ready_to_fly_planning_response.notes = "Created Operational Intent ID {operational_intent_id}".format(
                        operational_intent_id=dss_operational_intent_reference_id
                    )
                    await fd_repo.update(uuid.UUID(operation_id_str), state=2)
                    await fd_repo.create_opint_reference_subscribers(
                        declaration_id=flight_declaration.id,
                        subscribers=operational_intent_update_job.dss_response.subscribers,
                    )
                    await fd_repo.create_or_update_composite_opint(
                        declaration_id=flight_declaration.id,
                        payload=CompositeOperationalIntentPayload(
                            bounds=view_rect_bounds_storage,
                            start_datetime=scd_test_data.intended_flight.basic_information.area[0].time_start.value,
                            end_datetime=scd_test_data.intended_flight.basic_information.area[0].time_end.value,
                            alt_max=scd_test_data.intended_flight.basic_information.area[0].volume.altitude_upper.value,
                            alt_min=scd_test_data.intended_flight.basic_information.area[0].volume.altitude_lower.value,
                            operational_intent_reference_id=str(flight_operational_intent_reference.id),
                            operational_intent_details_id=str(flight_operational_intent_details.id),
                        ),
                    )
                    return json.loads(json.dumps(ready_to_fly_planning_response, cls=EnhancedJSONEncoder)), 200

                elif generated_operational_intent_state == "Nonconforming":
                    await fd_repo.update(uuid.UUID(operation_id_str), state=3)
                    existing_op_int_details.operational_intent_details.off_nominal_volumes = scd_test_data.intended_flight.basic_information.area
                    existing_op_int_details.success_response.operational_intent_reference.state = OperationalIntentState.Nonconforming
                    existing_op_int_details.operational_intent_details.state = OperationalIntentState.Nonconforming
                    await fd_repo.create_or_update_composite_opint(
                        declaration_id=flight_declaration.id,
                        payload=existing_op_int_details,
                    )
                    return json.loads(json.dumps(planned_off_nominal_planning_response, cls=EnhancedJSONEncoder)), 200

            elif operational_intent_update_job.status == 999:
                if flight_plan_exists_in_flight_blender and operational_intent_update_job.additional_information.check_id.value == "B":
                    if operational_intent_update_job.additional_information.tentative_flight_plan_processing_response.value == "OkToFly":
                        return json.loads(json.dumps(not_planned_activated_higher_priority_planning_response, cls=EnhancedJSONEncoder)), 200
                    else:
                        return json.loads(json.dumps(not_planned_activated_planning_response, cls=EnhancedJSONEncoder)), 200
                elif scd_test_data.intended_flight.astm_f3548_21.priority == 100:
                    return json.loads(json.dumps(not_planned_activated_higher_priority_planning_response, cls=EnhancedJSONEncoder)), 200
                return json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)), 200
            else:
                return json.loads(json.dumps(failed_planning_response, cls=EnhancedJSONEncoder)), 200
        else:
            flight_declaration_creation = FlightDeclarationCreationPayload(
                id=operation_id_str,
                operational_intent=json.loads(json.dumps(flight_planning_data, cls=EnhancedJSONEncoder)),
                flight_declaration_raw_geojson=my_geo_json_converter.geo_json,
                bounds=view_rect_bounds_storage,
                aircraft_id="0000",
                state=OPERATION_STATES_LOOKUP[generated_operational_intent_state],
            )
            fd_repo = self.fd_repo
            opint = flight_declaration_creation.operational_intent
            raw_geojson = flight_declaration_creation.flight_declaration_raw_geojson
            flight_declaration = await fd_repo.create(
                id=uuid.UUID(str(flight_declaration_creation.id)),
                operational_intent=json.dumps(opint) if not isinstance(opint, str) else opint,
                flight_declaration_raw_geojson=json.dumps(raw_geojson) if raw_geojson and not isinstance(raw_geojson, str) else raw_geojson,
                bounds=flight_declaration_creation.bounds,
                aircraft_id=flight_declaration_creation.aircraft_id or "unknown",
                state=flight_declaration_creation.state,
            )

            pre_creation_checks_passed = my_volumes_validator.pre_operational_intent_creation_checks(
                volumes=scd_test_data.intended_flight.basic_information.area
            )
            if not pre_creation_checks_passed:
                return json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)), 200

            off_nominal_volumes = (
                scd_test_data.intended_flight.basic_information.area if flight_planning_uas_state in ["OffNominal", "Contingent"] else []
            )
            flight_planning_submission: OperationalIntentSubmissionStatus = await _await_if_needed(
                my_scd_dss_helper.create_and_submit_operational_intent_reference(
                    state=generated_operational_intent_state,
                    volumes=scd_test_data.intended_flight.basic_information.area,
                    off_nominal_volumes=off_nominal_volumes,
                    priority=flight_planning_priority,
                )
            )

            if flight_planning_submission.status == "success":
                flight_planning_data.state = generated_operational_intent_state

                _operational_intent_details = OperationalIntentUSSDetails(
                    volumes=flight_planning_notification_payload.volumes,
                    off_nominal_volumes=flight_planning_notification_payload.off_nominal_volumes,
                    priority=flight_planning_notification_payload.priority,
                )
                flight_operational_intent_detail = await fd_repo.create_opint_detail(
                    declaration_id=flight_declaration.id,
                    payload=_operational_intent_details,
                )
                flight_operational_intent_reference = await fd_repo.create_opint_reference(
                    declaration_id=flight_declaration.id,
                    payload=flight_planning_submission.dss_response.operational_intent_reference,
                )
                await fd_repo.create_opint_reference_subscribers(
                    declaration_id=flight_declaration.id,
                    subscribers=flight_planning_submission.dss_response.subscribers,
                )
                composite_payload = CompositeOperationalIntentPayload(
                    bounds=view_rect_bounds_storage,
                    start_datetime=scd_test_data.intended_flight.basic_information.area[0].time_start.value,
                    end_datetime=scd_test_data.intended_flight.basic_information.area[0].time_end.value,
                    alt_max=50,
                    alt_min=25,
                    operational_intent_reference_id=str(flight_operational_intent_reference.id),
                    operational_intent_details_id=str(flight_operational_intent_detail.id),
                )
                await fd_repo.create_or_update_composite_opint(
                    declaration_id=flight_declaration.id,
                    payload=composite_payload,
                )

                if flight_planning_submission.constraints:
                    my_constraints_writer = ConstraintsWriter(
                        constraint_repo=SQLAlchemyConstraintRepository(self.fd_repo.db),
                    )
                    await my_constraints_writer.write_nearby_constraints(
                        flight_declaration=flight_declaration,
                        constraints=flight_planning_submission.constraints,
                    )

                my_scd_dss_helper.process_peer_uss_notifications(
                    all_subscribers=flight_planning_submission.dss_response.subscribers,
                    operational_intent_details=flight_planning_notification_payload,
                    operational_intent_reference=flight_planning_submission.dss_response.operational_intent_reference,
                    operational_intent_id=flight_planning_submission.operational_intent_id,
                )
                planned_test_injection_response.operational_intent_id = flight_planning_submission.operational_intent_id

            elif flight_planning_submission.status == "conflict_with_flight":
                if flight_plan_exists_in_flight_blender:
                    if generated_operational_intent_state == "Accepted":
                        return json.loads(json.dumps(not_planned_already_planned_planning_response, cls=EnhancedJSONEncoder)), 200
                return json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)), 200

            elif flight_planning_submission.status in ["failure", "peer_uss_data_sharing_issue"]:
                if flight_planning_submission.status_code == 408:
                    return json.loads(json.dumps(not_planned_planning_response, cls=EnhancedJSONEncoder)), 200
                else:
                    return json.loads(json.dumps(failed_planning_response, cls=EnhancedJSONEncoder)), 200

            if flight_planning_usage_state == "Planned":
                return json.loads(json.dumps(planned_planning_response, cls=EnhancedJSONEncoder)), 200
            else:
                return json.loads(json.dumps(planned_planning_response, cls=EnhancedJSONEncoder)), 200

    async def delete_flight_plan(self, flight_plan_id: str) -> tuple[dict, int]:
        operation_id_str = str(flight_plan_id)
        my_scd_dss_helper = dss_scd_helper.SCDOperations()

        fd_repo = self.fd_repo
        flight_operational_intent_reference = await fd_repo.get_opint_reference_by_declaration_id(uuid.UUID(operation_id_str))
        opint_id = flight_operational_intent_reference.id if flight_operational_intent_reference else None
        ovn = flight_operational_intent_reference.ovn if flight_operational_intent_reference else None

        if flight_operational_intent_reference:
            deletion_response = my_scd_dss_helper.delete_operational_intent(dss_operational_intent_ref_id=str(opint_id), ovn=ovn)
            if deletion_response.status == 200:
                await fd_repo.delete(uuid.UUID(operation_id_str))
                return json.loads(json.dumps(flight_planning_deletion_success_response, cls=EnhancedJSONEncoder)), 200
            else:
                return json.loads(json.dumps(flight_planning_deletion_failure_response, cls=EnhancedJSONEncoder)), 200
        else:
            return json.loads(json.dumps(flight_planning_deletion_failure_response, cls=EnhancedJSONEncoder)), 200
