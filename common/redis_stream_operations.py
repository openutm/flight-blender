import logging
import uuid
from typing import List, Optional

from dotenv import find_dotenv, load_dotenv

from auth_helper.common import get_redis
from flight_feed_operations.data_definitions import SingleAirtrafficObservation

logger = logging.getLogger("django")

load_dotenv(find_dotenv())

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)


class RedisStreamOperations:
    def __init__(self):
        self.redis = get_redis()

    def create_air_traffic_stream(self, stream_name: str) -> None:
        """
        Create a Redis stream for air traffic data.

        Args:
            stream_name (str): The name of the Redis stream.
        """
        try:
            # Adding an initial entry to create the stream
            self.redis.xadd(stream_name, {"message": "stream_created"})
            logger.info(f"Redis stream '{stream_name}' created successfully.")
        except Exception as e:
            logger.error(f"Error creating Redis stream '{stream_name}': {e}")

    def add_air_traffic_data(self, stream_name: str, data: dict) -> None:
        """
        Add air traffic data to the Redis stream.

        Args:
            stream_name (str): The name of the Redis stream.
            data (dict): The air traffic data to add to the stream.
        """
        try:
            self.redis.xadd(stream_name, data)
            logger.info(f"Data added to Redis stream '{stream_name}': {data}")
        except Exception as e:
            logger.error(f"Error adding data to Redis stream '{stream_name}': {e}")

    def create_consumer_group(self, stream_name: str, group_name: str) -> bool:
        """
        Create a consumer group for the Redis stream.

        Args:
            stream_name (str): The name of the Redis stream.
            group_name (str): The name of the consumer group.

        Returns:
            bool: True if group was created or already exists, False on error.
        """
        try:
            # Use MKSTREAM to create the stream if it doesn't exist
            self.redis.xgroup_create(stream_name, group_name, id="0", mkstream=True)
            logger.info(f"Consumer group '{group_name}' created for stream '{stream_name}'.")
            return True
        except Exception as e:
            # Group might already exist, which is fine
            if "BUSYGROUP" in str(e):
                logger.debug(f"Consumer group '{group_name}' already exists for stream '{stream_name}'.")
                return True
            logger.error(f"Error creating consumer group '{group_name}' for stream '{stream_name}': {e}")
            return False

    def create_consumer_reader(self) -> str:
        """
        Create a unique consumer identifier using UUID.

        Returns:
            str: A unique consumer identifier.
        """
        consumer_id = str(uuid.uuid4())
        logger.info(f"Created consumer reader with ID: {consumer_id}")
        return consumer_id

    def read_latest_air_traffic_data(
        self, stream_name: str, consumer_group: str = "air_traffic_readers", consumer_id: Optional[str] = None, count: int = 10, block: int = 1000
    ) -> List[SingleAirtrafficObservation]:
        """
        Read the latest air traffic data from the Redis stream since last access by this consumer.
        Messages are automatically acknowledged after reading.

        Args:
            stream_name (str): The name of the Redis stream.
            consumer_group (str): The name of the consumer group. Defaults to "air_traffic_readers".
            consumer_id (str): The unique consumer identifier. If None, creates a new one.
            count (int): Maximum number of messages to read. Defaults to 10.
            block (int): Time to block waiting for new messages in milliseconds. Defaults to 1000.

        Returns:
            List[SingleAirtrafficObservation]: List of air traffic observations.
        """
        try:
            # Create consumer ID if not provided
            if consumer_id is None:
                consumer_id = self.create_consumer_reader()

            # Ensure consumer group exists
            if not self.create_consumer_group(stream_name, consumer_group):
                logger.error(f"Failed to create consumer group '{consumer_group}' for stream '{stream_name}'")
                return []

            # Read messages from the stream using consumer group
            messages = self.redis.xreadgroup(consumer_group, consumer_id, {stream_name: ">"}, count=count, block=block)

            observations = []
            message_ids_to_ack = []

            for stream, stream_messages in messages:
                for message_id, fields in stream_messages:
                    try:
                        # Parse message into SingleAirtrafficObservation
                        observation = self._parse_stream_message_to_observation(fields)
                        if observation:
                            observations.append(observation)
                            message_ids_to_ack.append(message_id)
                    except Exception as e:
                        logger.error(f"Error parsing message {message_id} from stream '{stream_name}': {e}")
                        # Still acknowledge the message to prevent it from being reprocessed
                        message_ids_to_ack.append(message_id)

            # Auto-acknowledge all processed messages
            if message_ids_to_ack:
                try:
                    self.redis.xack(stream_name, consumer_group, *message_ids_to_ack)
                    logger.debug(f"Acknowledged {len(message_ids_to_ack)} messages from stream '{stream_name}'")
                except Exception as e:
                    logger.error(f"Error acknowledging messages from stream '{stream_name}': {e}")

            logger.info(f"Read {len(observations)} air traffic observations from stream '{stream_name}' for consumer '{consumer_id}'")
            return observations

        except Exception as e:
            logger.error(f"Error reading from Redis stream '{stream_name}' with consumer '{consumer_id}': {e}")
            return []

    def _parse_stream_message_to_observation(self, fields: dict) -> SingleAirtrafficObservation | None:
        """
        Parse Redis stream message fields into a SingleAirtrafficObservation instance.

        Args:
            fields (dict): The message fields from Redis stream.

        Returns:
            SingleAirtrafficObservation | None: Parsed observation or None if parsing fails.
        """
        try:
            # Convert Redis bytes to appropriate types
            field_data = {}
            for key, value in fields.items():
                if isinstance(key, bytes):
                    key = key.decode("utf-8")
                if isinstance(value, bytes):
                    value = value.decode("utf-8")
                field_data[key] = value

            # Skip initialization messages
            if field_data.get("message") == "stream_created":
                return None

            # Parse metadata if it exists as JSON string
            metadata = {}
            if "metadata" in field_data:
                try:
                    import json

                    metadata = json.loads(field_data["metadata"]) if field_data["metadata"] else {}
                except (json.JSONDecodeError, TypeError):
                    metadata = {}

            # Create SingleAirtrafficObservation with required fields
            observation = SingleAirtrafficObservation(
                lat_dd=float(field_data.get("lat_dd", 0.0)),
                lon_dd=float(field_data.get("lon_dd", 0.0)),
                altitude_mm=float(field_data.get("altitude_mm", 0.0)),
                traffic_source=int(field_data.get("traffic_source", 0)),
                source_type=int(field_data.get("source_type", 0)),
                icao_address=str(field_data.get("icao_address", "")),
                metadata=metadata,
                session_id=field_data.get("session_id"),
            )

            return observation

        except (ValueError, KeyError, TypeError) as e:
            logger.error(f"Error parsing stream message fields into SingleAirtrafficObservation: {e}")
            return None
