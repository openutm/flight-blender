import json
import logging
from enum import Enum

import dacite
from dacite import from_dict

from auth_helper.common import get_redis
from rid_operations import rtree_helper
from common.database_operations import FlightBlenderDatabaseReader
from .dss_scd_helper import OperationalIntentReferenceHelper, VolumesConverter
from .flight_planning_data_definitions import (
    AdvisoryInclusion,
    ASTMF354821OpIntentInformation,
    BasicFlightPlanInformation,
    CloseFlightPlanResponse,
    FlightAuthorisationData,
    FlightPlan,
    FlightPlanCurrentStatus,
    FlightPlanningRequest,
    PlanningActivityResult,
    RPAS26FlightDetails,
    UpsertFlightPlanResponse,
)
from .scd_data_definitions import (
    TestInjectionResult,
    TestInjectionResultState,
    Volume4D,
)

logger = logging.getLogger("django")

# Set the responses to be used
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

# Flight Planning responses
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


class SCDTestHarnessHelper:
    """This class is used in the SCD Test harness to include transformations"""

    def __init__(self):
        self.my_operational_intent_helper = OperationalIntentReferenceHelper()
        self.r = get_redis()
        self.my_volumes_converter = VolumesConverter()
        self.my_database_reader = FlightBlenderDatabaseReader()
        self.my_operational_intent_comparator = rtree_helper.OperationalIntentComparisonFactory()

    def check_if_same_flight_id_exists(self, operation_id: str) -> bool:
        flight_operational_intent_reference = self.my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_id(flight_declaration_id=operation_id)

        if flight_operational_intent_reference:
            return True
        else:
            return False

class FlightPlantoOperationalIntentProcessor:
    def __init__(self, flight_planning_request: FlightPlanningRequest):
        self.flight_planning_request = flight_planning_request

    def generate_operational_intent_state_from_planning_information(self, current_state: str = None):
        logger.debug("********************************")
        logger.debug(self.flight_planning_request.intended_flight.basic_information.uas_state.value)
        logger.debug(self.flight_planning_request.intended_flight.basic_information.usage_state.value)
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
        basic_flight_plan_information = from_dict(
            data_class=BasicFlightPlanInformation, data=basic_information_dict, config=dacite.Config(cast=[Enum])
        )

        return basic_flight_plan_information

    def process_f3548_21_flight_plan_information(self, astm_f3548_op_int_information_dict) -> ASTMF354821OpIntentInformation:
        basic_flight_plan_information = from_dict(
            data_class=ASTMF354821OpIntentInformation, data=astm_f3548_op_int_information_dict, config=dacite.Config(cast=[Enum])
        )

        return basic_flight_plan_information

    def process_uspace_flight_authorisation_information(self, uspace_flight_authorisation_information_dict) -> FlightAuthorisationData:
        uspace_flight_authorisation = from_dict(
            data_class=FlightAuthorisationData, data=uspace_flight_authorisation_information_dict, config=dacite.Config(cast=[Enum])
        )

        return uspace_flight_authorisation

    def process_rpas_operating_rules_2_6_information(self, rpas_operating_rules_2_6_information_dict) -> RPAS26FlightDetails:
        rpas_operating_rules_2_6 = from_dict(
            data_class=RPAS26FlightDetails, data=rpas_operating_rules_2_6_information_dict, config=dacite.Config(cast=[Enum])
        )

        return rpas_operating_rules_2_6

    def process_additional_information(self) -> dict:
        additional_information = {}
        return additional_information

    def process_intended_flight_data(self) -> FlightPlan:
        basic_information = self.process_basic_flight_plan(basic_information_dict=self.intended_flight_information["basic_information"])
        astm_f3548_21 = self.process_f3548_21_flight_plan_information(
            astm_f3548_op_int_information_dict=self.intended_flight_information["astm_f3548_21"]
        )
        uspace_flight_authorisation = self.process_uspace_flight_authorisation_information(
            self.intended_flight_information["uspace_flight_authorisation"]
        )

        flight_plan = FlightPlan(
            basic_information=basic_information, astm_f3548_21=astm_f3548_21, uspace_flight_authorisation=uspace_flight_authorisation
        )

        return flight_plan

    def process_incoming_flight_plan_data(self) -> FlightPlanningRequest:
        intended_flight = self.process_intended_flight_data()
        flight_planning_request = FlightPlanningRequest(intended_flight=intended_flight, request_id=self.request_id)
        return flight_planning_request
