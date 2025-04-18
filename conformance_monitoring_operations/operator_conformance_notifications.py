import logging
from os import environ as env

from dotenv import find_dotenv, load_dotenv

from flight_declaration_operations.tasks import send_operational_update_message

load_dotenv(find_dotenv())

logger = logging.getLogger("django")


class OperationConformanceNotification:
    def __init__(self, flight_declaration_id: str):
        self.amqp_connection_url = env.get("AMQP_URL", 0)
        self.flight_declaration_id = flight_declaration_id

    def send_conformance_status_notification(self, message: str, level: str):
        if self.amqp_connection_url:
            send_operational_update_message.delay(
                flight_declaration_id=self.flight_declaration_id,
                message_text=message,
                level=level,
            )
        else:
            # If no AMQP is specified then
            logger.error(f"Conformance Notification for {self.flight_declaration_id}")
            logger.error(message)
