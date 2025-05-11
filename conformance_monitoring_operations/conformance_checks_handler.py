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
    FlightOperationConformanceHelper is a utility class that assists in managing and verifying
    state transitions for flight operations. It interacts with the database and external systems
    to ensure proper conformance monitoring and state management.
    Attributes:
        flight_declaration_id (str): The unique identifier for the flight declaration.
        database_reader (FlightBlenderDatabaseReader): Instance for reading from the database.
        flight_declaration: The flight declaration object retrieved from the database.
        database_writer (FlightBlenderDatabaseWriter): Instance for writing to the database.
        ENABLE_CONFORMANCE_MONITORING (int): Flag to enable or disable conformance monitoring.
        USSP_NETWORK_ENABLED (int): Flag to enable or disable USSP network integration.
    Methods:
        verify_operation_state_transition(original_state: int, new_state: int, event: str) -> bool:
            Verifies if a given event can trigger a valid state transition.
        manage_operation_state_transition(original_state: int, new_state: int, event: str):
            Manages state transitions for flight operations and invokes appropriate handlers.
        _handle_operation_ended(original_state: int, event: str):
            Handles the transition to the "operation ended" state and performs cleanup tasks.
        _clear_operation_from_dss():
            Clears the operation from the DSS (Distributed Spatial Services).
        _remove_conformance_monitoring_task():
            Removes the conformance monitoring task associated with the flight declaration.
        _handle_contingent_state(original_state: int, event: str):
            Handles the transition to the "contingent" state and performs associated actions.
        _handle_non_conforming_state(original_state: int, event: str):
            Handles the transition to the "non-conforming" state and updates the operational intent.
        _handle_activated_state(original_state: int, event: str):
            Handles the transition to the "activated" state and performs associated actions.
        _update_operational_intent_to_activated():
            Updates the operational intent to the "activated" state in the USSP network.
        _create_conformance_monitoring_task():
            Creates a periodic task for conformance monitoring of the flight operation.

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
        Verifies if a state transition for a flight operation is valid based on the given event.
        This method uses a state machine to simulate the transition from the original state
        to the new state triggered by the specified event. It checks if the resulting state
        matches the expected new state.
        Args:
            original_state (int): The initial state of the flight operation.
            new_state (int): The expected state after the transition.
            event (str): The event triggering the state transition.
        Returns:
            bool: True if the state transition is valid and matches the expected new state,
                  False otherwise.
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
        Manages the state transition of an operation based on the original state, new state, and an event.
        This method determines the appropriate handler for the new state and invokes it to process
        the state transition. If no handler is defined for the new state, a log message is recorded.
        Args:
            original_state (int): The current state of the operation before the transition.
            new_state (int): The target state of the operation after the transition.
            event (str): The event triggering the state transition.
        Handlers:
            - 5: Calls `self._handle_operation_ended` to handle the "operation ended" state.
            - 4: Calls `self._handle_contingent_state` to handle the "contingent" state.
            - 3: Calls `self._handle_non_conforming_state` to handle the "non-conforming" state.
            - 2: Calls `self._handle_activated_state` to handle the "activated" state.
        Logs:
            Logs an informational message if no handler is defined for the given new state.
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
        """
        Handles the termination of an operation based on the provided event.
        This method checks if the operation has ended and performs necessary
        cleanup tasks such as clearing the operation from the DSS (if enabled)
        and removing the conformance monitoring task (if enabled). If the event
        does not confirm the operation's end, it logs an informational message.
        Args:
            original_state (int): The original state of the operation before termination.
            event (str): The event indicating the operation's status.
        Returns:
            None
        """

        if event != "operator_confirms_ended":
            logger.info("Operation has ended, but no confirmation received")
            return

        if self.USSP_NETWORK_ENABLED:
            self._clear_operation_from_dss()

        if self.ENABLE_CONFORMANCE_MONITORING:
            self._remove_conformance_monitoring_task()

    def _clear_operation_from_dss(self):
        """
        Clears the operation from the DSS (Discovery and Synchronization Service) by invoking
        the "operation_ended_clear_dss" management command.
        This method uses the `management.call_command` function to execute the command,
        passing the `flight_declaration_id` and setting `dry_run` to 0.
        Args:
            None
        Returns:
            None
        """

        management.call_command(
            "operation_ended_clear_dss",
            flight_declaration_id=self.flight_declaration_id,
            dry_run=0,
        )

    def _remove_conformance_monitoring_task(self):
        """
        Removes the conformance monitoring task associated with the current flight declaration.
        This method retrieves the conformance monitoring task for the specified flight declaration
        from the database. If a task is found, it removes the corresponding periodic task
        using the database writer.
        Returns:
            None
        """

        conformance_monitoring_job = self.database_reader.get_conformance_monitoring_task(flight_declaration=self.flight_declaration)
        if conformance_monitoring_job:
            self.database_writer.remove_conformance_monitoring_periodic_task(conformance_monitoring_task=conformance_monitoring_job)

    def _handle_contingent_state(self, original_state: int, event: str):
        """
        Handles the contingent state transition based on the original state and the event.
        This method processes state transitions for a flight declaration in contingent scenarios.
        It validates the event against the current state and triggers the appropriate command
        if the USSP network is enabled. If the network is disabled, it logs the information.
        Args:
            original_state (int): The current state of the flight declaration. Expected values are:
                      - 2: Contingent state initiated by the operator.
                      - 3: Contingent state confirmed by the operator.
            event (str): The event triggering the state transition. Valid events depend on the original state:
                 - For state 2: "operator_initiates_contingent", "flight_blender_confirms_contingent".
                 - For state 3: "timeout", "operator_confirms_contingent".
        Behavior:
            - If the USSP network is enabled and the event is valid for the given state, it calls the
              "operator_declares_contingency" management command with the flight declaration ID.
            - If the USSP network is not enabled, it logs a message indicating that the contingency
              state handling with DSS is skipped.
        Returns:
            None
        """

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
        """
        Handles non-conforming states by triggering appropriate updates to the operational intent.
        This method checks if a given event is non-conforming and if the original state is valid
        for handling non-conformance. If both conditions are met and the USSP network is enabled,
        it invokes the corresponding management command to update the operational intent.
        Args:
            original_state (int): The original state of the operation. Expected values are 1 or 2.
            event (str): The event indicating a non-conforming state. Must be a key in the
                         `non_conforming_events` dictionary.
        Raises:
            KeyError: If the event is not found in the `non_conforming_events` dictionary.
        """

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
            else:
                logger.info("USSP Network is not enabled, skipping non-conforming state handling with DSS")

    def _handle_activated_state(self, original_state: int, event: str):
        """
        Handles the transition to the "activated" state for an operational intent.
        This method checks if the provided original state and event are valid for
        transitioning to the "activated" state. If valid, it performs the necessary
        actions based on the system's configuration, such as updating the operational
        intent and creating a conformance monitoring task.
        Args:
            original_state (int): The current state of the operational intent.
                                  Expected to be 1 for activation.
            event (str): The event triggering the state transition.
                         Expected to be "operator_activates".
        Returns:
            None
        """

        if original_state != 1 or event != "operator_activates":
            logger.info("Invalid state or event for activation")
            return

        if self.USSP_NETWORK_ENABLED:
            self._update_operational_intent_to_activated()

        if self.ENABLE_CONFORMANCE_MONITORING:
            self._create_conformance_monitoring_task()

    def _update_operational_intent_to_activated(self):
        """
        Updates the operational intent status to 'activated' for the current flight declaration.
        This method invokes the management command `update_operational_intent_to_activated`
        with the `flight_declaration_id` of the current instance and sets `dry_run` to 0,
        indicating that the operation should be executed.
        Args:
            None
        Returns:
            None
        """

        management.call_command(
            "update_operational_intent_to_activated",
            flight_declaration_id=self.flight_declaration_id,
            dry_run=0,
        )

    def _create_conformance_monitoring_task(self):
        """
        Creates a conformance monitoring task for the current flight declaration.
        This method interacts with the database writer to create a periodic task
        for conformance monitoring based on the flight declaration. It logs the
        success or failure of the task creation process.
        Returns:
            None
        """

        conformance_monitoring_job = self.database_writer.create_conformance_monitoring_periodic_task(flight_declaration=self.flight_declaration)
        if conformance_monitoring_job:
            logger.info(f"Created conformance monitoring job for {self.flight_declaration_id}")
        else:
            logger.info(f"Error in creating conformance monitoring job for {self.flight_declaration_id}")
