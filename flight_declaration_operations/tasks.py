import json
import logging
from dataclasses import asdict
from datetime import timedelta
from os import environ as env

import arrow
from dacite import from_dict
from dotenv import find_dotenv, load_dotenv

from auth_helper.common import get_redis
from common.data_definitions import OPERATION_STATES
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from conformance_monitoring_operations.conformance_checks_handler import (
    FlightOperationConformanceHelper,
)
from flight_blender.celery import app
from notification_operations.data_definitions import FlightDeclarationUpdateMessage
from notification_operations.notification_helper import NotificationFactory
from scd_operations.opint_helper import DSSOperationalIntentsCreator
from scd_operations.scd_data_definitions import (
    NotifyPeerUSSPostPayload,
    OperationalIntentDetailsUSSResponse,
    OperationalIntentStorage,
    OperationalIntentUSSDetails,
    SubscriptionState,
)

logger = logging.getLogger("django")

load_dotenv(find_dotenv())


@app.task(name="submit_flight_declaration_to_dss_async")
def submit_flight_declaration_to_dss_async(flight_declaration_id: str):
    my_dss_opint_creator = DSSOperationalIntentsCreator(flight_declaration_id)
    my_database_reader = FlightBlenderDatabaseReader()
    my_database_writer = FlightBlenderDatabaseWriter()

    start_end_time_validated = my_dss_opint_creator.validate_flight_declaration_start_end_time()

    logger.info("Flight Operation start end time status %s" % start_end_time_validated)

    if not start_end_time_validated:
        logger.error(
            "Flight Declaration start / end times are not valid, please check the submitted declaration, this operation will not be sent to the DSS for strategic deconfliction"
        )
        validation_not_ok_msg = (
            "Flight Operation with ID {operation_id} did not pass time validation, start and end time may not be ahead of two hours".format(
                operation_id=flight_declaration_id
            )
        )
        send_operational_update_message.delay(
            flight_declaration_id=flight_declaration_id,
            message_text=validation_not_ok_msg,
            level="error",
        )
        return

    validation_ok_msg = "Flight Operation with ID {operation_id} validated for start and end time, submitting to DSS..".format(
        operation_id=flight_declaration_id
    )
    send_operational_update_message.delay(
        flight_declaration_id=flight_declaration_id,
        message_text=validation_ok_msg,
        level="info",
    )
    logger.info("Submitting flight declaration to DSS..")

    opint_submission_result = my_dss_opint_creator.submit_flight_declaration_to_dss()

    if opint_submission_result.status_code == 500:
        logger.error("Error in submitting Flight Declaration to the DSS %s" % opint_submission_result.status)

        dss_submission_error_msg = (
            "Flight Operation with ID {operation_id} could not be submitted to the DSS, check the Auth server and / or the DSS URL".format(
                operation_id=flight_declaration_id
            )
        )
        send_operational_update_message.delay(
            flight_declaration_id=flight_declaration_id,
            message_text=dss_submission_error_msg,
            level="error",
        )

    elif opint_submission_result.status_code in [400, 409, 401, 412]:
        logger.error("Error in submitting Flight Declaration to the DSS %s" % opint_submission_result.status)

        dss_submission_error_msg = (
            "Flight Operation with ID {operation_id} was rejected by the DSS, there was a error in data submitted by Flight Blender".format(
                operation_id=flight_declaration_id
            )
        )
        send_operational_update_message.delay(
            flight_declaration_id=flight_declaration_id,
            message_text=dss_submission_error_msg,
            level="error",
        )

    elif opint_submission_result.status_code == 201:
        logger.info("Successfully submitted Flight Declaration to the DSS %s" % opint_submission_result.status)

        submission_success_msg = "Flight Operation with ID {operation_id} submitted successfully to the DSS".format(
            operation_id=flight_declaration_id
        )
        send_operational_update_message.delay(
            flight_declaration_id=flight_declaration_id,
            message_text=submission_success_msg,
            level="info",
        )

        ###### Change via new state check helper

        flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=flight_declaration_id)

        if not flight_declaration:
            logger.error("Flight Declaration with ID %s not found in the database" % flight_declaration_id)
            return

        fa = my_database_reader.get_flight_operational_intent_reference_by_flight_declaration_obj(flight_declaration=flight_declaration)

        logger.info("Saving created operational intent details..")
        created_opint = fa.id

        my_database_writer.update_flight_operational_intent_reference_with_dss_response(
            flight_declaration=flight_declaration,
            dss_operational_intent_reference_id=str(created_opint),
            dss_response=opint_submission_result.dss_response,
            ovn=opint_submission_result.dss_response.operational_intent_reference.ovn,
        )

        logger.info("Changing operation state..")
        original_state = flight_declaration.state
        accepted_state = OPERATION_STATES[1][0]
        my_conformance_helper = FlightOperationConformanceHelper(flight_declaration_id=flight_declaration_id)
        transition_valid = my_conformance_helper.verify_operation_state_transition(
            original_state=original_state,
            new_state=accepted_state,
            event="dss_accepts",
        )
        if transition_valid:
            my_database_writer.update_flight_operation_state(flight_declaration_id=flight_declaration_id, state=accepted_state)
            logger.info("The state change transition to Accepted state from current state Created is valid..")
            flight_declaration.add_state_history_entry(
                new_state=accepted_state,
                original_state=original_state,
                notes="Successfully submitted to the DSS",
            )

        submission_state_updated_msg = "Flight Operation with ID {operation_id} has a updated state: Accepted. ".format(
            operation_id=flight_declaration_id
        )
        send_operational_update_message.delay(
            flight_declaration_id=flight_declaration_id,
            message_text=submission_state_updated_msg,
            level="info",
        )

        logger.info("Notifying subscribers..")

        subscribers = opint_submission_result.dss_response.subscribers
        if subscribers:
            for subscriber in subscribers:
                subscriptions_raw = subscriber["subscriptions"]
                uss_base_url = subscriber["uss_base_url"]
                flight_blender_base_url = env.get("FLIGHTBLENDER_FQDN", "http://localhost:8000")

                if uss_base_url != flight_blender_base_url:  # There are others who are subscribesd, not just ourselves
                    subscriptions = from_dict(data_class=SubscriptionState, data=subscriptions_raw)
                    op_int_details = from_dict(
                        data_class=OperationalIntentUSSDetails,
                        data=json.loads(flight_declaration.operational_intent),
                    )
                    operational_intent = OperationalIntentDetailsUSSResponse(
                        reference=opint_submission_result.dss_response.operational_intent_reference,
                        details=op_int_details,
                    )
                    post_notification_payload = NotifyPeerUSSPostPayload(
                        operational_intent_id=created_opint,
                        operational_intent=operational_intent,
                        subscriptions=subscriptions,
                    )
                    # Notify Subscribers
                    my_dss_opint_creator.notify_peer_uss(
                        uss_base_url=uss_base_url,
                        notification_payload=post_notification_payload,
                    )

    logger.info("Details of the submission status %s" % opint_submission_result.message)


@app.task(name="send_operational_update_message")
def send_operational_update_message(
    flight_declaration_id: str,
    message_text: str,
    level: str = "info",
    timestamp: str = arrow.now().isoformat(),
) -> None:
    """
    Sends an operational update message for a flight declaration.

    Args:
        flight_declaration_id (str): The ID of the flight declaration.
        message_text (str): The message text to be sent.
        level (str, optional): The level of the message (e.g., "info", "error"). Defaults to "info".
        timestamp (str, optional): The timestamp of the message. If not provided, the current time is used.

    Returns:
        None
    """

    update_message = FlightDeclarationUpdateMessage(body=message_text, level=level, timestamp=timestamp)
    amqp_connection_url = env.get("AMQP_URL", "")
    if amqp_connection_url:
        my_notification_helper = NotificationFactory(
            flight_declaration_id=flight_declaration_id,
            amqp_connection_url=amqp_connection_url,
        )
        my_notification_helper.declare_queue(queue_name=flight_declaration_id)
        my_notification_helper.send_message(message_details=update_message)
        logger.info("Submitted Flight Declaration Notification")
    else:
        logger.info("No AMQP URL specified..")
