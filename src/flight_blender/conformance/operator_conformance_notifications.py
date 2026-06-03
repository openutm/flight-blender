from loguru import logger

from flight_blender.config import settings
from flight_blender.flight_declarations.tasks import send_operational_update_message


class OperationConformanceNotification:
    def __init__(self, flight_declaration_id: str):
        self.amqp_connection_url = settings.AMQP_URL
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
