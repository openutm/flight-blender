import json
from dataclasses import asdict

import pika
from dotenv import find_dotenv, load_dotenv
from loguru import logger

# import signal, sys
from .data_definitions import FlightDeclarationUpdateMessage

load_dotenv(find_dotenv())


# def signal_handler(signal, frame):
#     sys.exit(0)

# signal.signal(signal.SIGINT, signal_handler)


class NotificationFactory:
    """
    A class to publish messages to the AMQP queue
    """

    def __init__(self, flight_declaration_id: str, amqp_connection_url: str):
        params = pika.URLParameters(amqp_connection_url)
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()

        self.exchange = "operational_events"
        self.flight_declaration_id = flight_declaration_id

    def send_message(self, message_details: FlightDeclarationUpdateMessage):
        msg_details = json.dumps(asdict(message_details))
        self.channel.basic_publish(
            exchange=self.exchange,
            routing_key=self.flight_declaration_id,
            body=msg_details,
        )
        logger.info(f"Sent message. Exchange: {self.exchange}, Routing Key: {self.flight_declaration_id}, Body: {msg_details}")

    def declare_queue(self, queue_name: str):
        logger.info(f"Trying to declare queue ({queue_name})...")
        self.channel.queue_declare(queue=queue_name)
        self.channel.queue_bind(
            exchange=self.exchange,
            queue=queue_name,
            routing_key=self.flight_declaration_id,
        )
        logger.info(f"Trying to bind queue ({self.exchange}) with routing key ({self.flight_declaration_id})...")

    def declare_exchange(self, exchange_name: str):
        logger.info(f"Trying to declare exchange ({exchange_name})...")
        self.channel.exchange_declare(exchange=exchange_name)

    def close(self):
        self.channel.close()
        self.connection.close()


class InitialNotificationFactory:
    """
    A class is used to create a Exchange initial
    """

    def __init__(self, amqp_connection_url: str, exchange_name: str = "operational_events"):
        params = pika.URLParameters(amqp_connection_url)
        self.connection = pika.BlockingConnection(params)
        self.channel = self.connection.channel()
        self.exchange_name = exchange_name

    def declare_exchange(self):
        logger.info(f"Trying to declare exchange ({self.exchange_name})...")
        self.channel.exchange_declare(exchange=self.exchange_name, durable=True)

    def close(self):
        self.channel.close()
        self.connection.close()
