from contextlib import suppress
from os import environ as env

from django.apps import AppConfig
from dotenv import find_dotenv, load_dotenv
from loguru import logger
from pika.exceptions import AMQPConnectionError, AMQPError

from flight_blender.notifications.notification_helper import InitialNotificationFactory

load_dotenv(find_dotenv())


class FlightDeclarationOperationsConfig(AppConfig):
    name = "flight_blender.flight_declarations"
    label = "flight_declaration_operations"

    def ready(self):
        amqp_connection_url = env.get("AMQP_URL", 0)

        if amqp_connection_url:
            logger.info(f"Connecting to AMQP {amqp_connection_url} for processing notifications..")
            my_notification_helper = None
            try:
                my_notification_helper = InitialNotificationFactory(
                    amqp_connection_url=amqp_connection_url,
                    exchange_name="operational_events",
                )
                my_notification_helper.declare_exchange()
                logger.info("Exchange declared on AMQP...")
            except (AMQPConnectionError, OSError):
                logger.exception("Failed to declare AMQP exchange at startup; notifications will retry per-message")
            finally:
                if my_notification_helper:
                    with suppress(AMQPError, OSError):
                        my_notification_helper.close()
        else:
            logger.info("AMQP not set, skipping exchange creation..")
