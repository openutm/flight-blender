"""Pure SCD/UTMRSS business logic — no infrastructure imports."""

from enum import Enum
from itertools import cycle

import dacite
from dacite import from_dict
from loguru import logger

from flight_blender.core.entities.scd import (
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
    TestInjectionResult,
    TestInjectionResultState,
    UpsertFlightPlanResponse,
)

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
