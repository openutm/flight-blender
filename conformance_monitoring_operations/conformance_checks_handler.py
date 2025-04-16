import logging
import os
from os import environ as env

from django.core import management
from dotenv import find_dotenv, load_dotenv

from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)

from .operation_state_helper import FlightOperationStateMachine, get_status

load_dotenv(find_dotenv())

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)

logger = logging.getLogger("django")


class FlightOperationConformanceHelper:
    """
    This class handles changes / transitions to a operation when the conformance check fails, it transitions
    """

    def __init__(self, flight_declaration_id: str):
        self.flight_declaration_id = flight_declaration_id
        self.database_reader = FlightBlenderDatabaseReader()
        self.flight_declaration = self.database_reader.get_flight_declaration_by_id(flight_declaration_id=self.flight_declaration_id)
        self.database_writer = FlightBlenderDatabaseWriter()
        self.ENABLE_CONFORMANCE_MONITORING = int(os.getenv("ENABLE_CONFORMANCE_MONITORING", 0))
        self.USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", 0))

    def verify_operation_state_transition(self, original_state: int, new_state: int, event: str) -> bool:
        """
        This class updates the state of a flight operation.
        """
        my_operation_state_machine = FlightOperationStateMachine(state=original_state)
        logger.info("Current Operation State %s" % my_operation_state_machine.state)

        my_operation_state_machine.on_event(event)
        changed_state = get_status(my_operation_state_machine.state)
        if changed_state == new_state:
            return True

        else:
            # The event cannot trigger a change of state, flight state is not updated

            logger.info("State change verification failed")
            return False

    def manage_operation_state_transition(self, original_state: int, new_state: int, event: str):
        """
        Handles state transitions for flight operations and performs associated actions.
        """
        state_transition_handlers = {
            5: self._handle_operation_ended,
            4: self._handle_contingent_state,
            3: self._handle_non_conforming_state,
            2: self._handle_activated_state,
        }

        handler = state_transition_handlers.get(new_state)
        if handler:
            handler(original_state, event)
        else:
            logger.info(f"No handler defined for new state: {new_state}")

    def _handle_operation_ended(self, original_state: int, event: str):
        if event != "operator_confirms_ended":
            logger.info("Operation has ended, but no confirmation received")
            return

        if self.USSP_NETWORK_ENABLED:
            self._clear_operation_from_dss()

        if self.ENABLE_CONFORMANCE_MONITORING:
            self._remove_conformance_monitoring_task()

    def _clear_operation_from_dss(self):
        management.call_command(
            "operation_ended_clear_dss",
            flight_declaration_id=self.flight_declaration_id,
            dry_run=0,
        )

    def _remove_conformance_monitoring_task(self):
        conformance_monitoring_job = self.database_reader.get_conformance_monitoring_task(
            flight_declaration=self.flight_declaration
        )
        if conformance_monitoring_job:
            self.database_writer.remove_conformance_monitoring_periodic_task(
                conformance_monitoring_task=conformance_monitoring_job
            )

    def _handle_contingent_state(self, original_state: int, event: str):
        valid_events_for_state_2 = [
            "operator_initiates_contingent",
            "flight_blender_confirms_contingent",
        ]
        valid_events_for_state_3 = ["timeout", "operator_confirms_contingent"]

        if self.USSP_NETWORK_ENABLED:
            if original_state in [2, 3] and event in (valid_events_for_state_2 if original_state == 2 else valid_events_for_state_3):
                management.call_command(
                    "operator_declares_contingency",
                    flight_declaration_id=self.flight_declaration_id,
                    dry_run=0,
                )
        else:
            logger.info("USSP Network is not enabled, skipping contingency state handling with DSS")

    def _handle_non_conforming_state(self, original_state: int, event: str):
        non_conforming_events = {
            "ua_exits_coordinated_op_intent": "update_operational_intent_to_non_conforming_update_expand_volumes",
            "ua_departs_early_late": "update_operational_intent_to_non_conforming",
        }

        if event in non_conforming_events and original_state in [1, 2]:
            if self.USSP_NETWORK_ENABLED:
                management.call_command(
                    non_conforming_events[event],
                    flight_declaration_id=self.flight_declaration_id,
                    dry_run=0,
                )

    def _handle_activated_state(self, original_state: int, event: str):
        if original_state != 1 or event != "operator_activates":
            logger.info("Invalid state or event for activation")
            return

        if self.USSP_NETWORK_ENABLED:
            self._update_operational_intent_to_activated()

        if self.ENABLE_CONFORMANCE_MONITORING:
            self._create_conformance_monitoring_task()

    def _update_operational_intent_to_activated(self):
        management.call_command(
            "update_operational_intent_to_activated",
            flight_declaration_id=self.flight_declaration_id,
            dry_run=0,
        )

    def _create_conformance_monitoring_task(self):
        conformance_monitoring_job = self.database_writer.create_conformance_monitoring_periodic_task(flight_declaration=self.flight_declaration)
        if conformance_monitoring_job:
            logger.info(f"Created conformance monitoring job for {self.flight_declaration_id}")
        else:
            logger.info(f"Error in creating conformance monitoring job for {self.flight_declaration_id}")
