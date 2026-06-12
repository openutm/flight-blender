import asyncio
import json

import arrow
from dacite import from_dict
from loguru import logger

from flight_blender.celery import app
from flight_blender.clients.dss_scd_client import SCDOperations
from flight_blender.clients.notification_client import NotificationFactory
from flight_blender.config import settings
from flight_blender.domain_types.notifications import FlightDeclarationUpdateMessage
from flight_blender.domain_types.scd import NotifyPeerUSSPostPayload, OperationalIntentDetailsUSSResponse, OperationalIntentUSSDetails
from flight_blender.services.flight_declarations_svc import submit_flight_declaration_to_dss, verify_and_update_declaration_state


@app.task(name="submit_flight_declaration_to_dss_async")
def submit_flight_declaration_to_dss_async(flight_declaration_id: str):
    asyncio.run(_async_submit_flight_declaration_to_dss(flight_declaration_id))


async def _async_submit_flight_declaration_to_dss(flight_declaration_id: str) -> None:
    opint_submission_result = await submit_flight_declaration_to_dss(flight_declaration_id)

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

        flight_declaration, created_opint = await verify_and_update_declaration_state(flight_declaration_id)

        if not flight_declaration:
            logger.error("Flight Declaration with ID %s not found in the database" % flight_declaration_id)
            return

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
                subscriptions_raw = subscriber.subscriptions
                uss_base_url = subscriber.uss_base_url
                flight_blender_base_url = settings.FLIGHTBLENDER_FQDN

                if uss_base_url != flight_blender_base_url:
                    op_int_details = from_dict(
                        data_class=OperationalIntentUSSDetails,
                        data=json.loads(flight_declaration.operational_intent),
                    )
                    operational_intent = OperationalIntentDetailsUSSResponse(
                        reference=opint_submission_result.dss_response.operational_intent_reference,
                        details=op_int_details,
                    )
                    post_notification_payload = NotifyPeerUSSPostPayload(
                        operational_intent_id=str(created_opint),
                        operational_intent=operational_intent,
                        subscriptions=subscriptions_raw,
                    )
                    scd_ops = SCDOperations()
                    scd_ops.notify_peer_uss(
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
    amqp_connection_url = settings.AMQP_URL
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


class CelerySCDNotifier:
    def send_operational_update_message(self, flight_declaration_id: str, message_text: str, level: str) -> None:
        send_operational_update_message.delay(
            flight_declaration_id=flight_declaration_id,
            message_text=message_text,
            level=level,
        )

    def submit_flight_declaration_to_dss_async(self, flight_declaration_id: str) -> None:
        submit_flight_declaration_to_dss_async.delay(flight_declaration_id=flight_declaration_id)
