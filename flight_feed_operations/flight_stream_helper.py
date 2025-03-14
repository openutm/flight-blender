import json
from dataclasses import asdict
from itertools import zip_longest

import arrow
from dotenv import find_dotenv, load_dotenv

from auth_helper.common import get_redis
from common.database_operations import FlightBlenderDatabaseReader

from .data_definitions import FlightObeservationSchema, Observation

load_dotenv(find_dotenv())


# iterate a list in batches of size n
def batcher(iterable, n):
    args = [iter(iterable)] * n
    return zip_longest(*args)


class ObservationReadOperations:
    """
    A class to handle reading operations for observations.
    Methods
    -------
    get_observations(cg)
        Reads messages from the given consumer group and returns a list of pending messages.

    Parameters
    ----------
    cg : ConsumerGroup
        The consumer group from which to read messages.
    Returns
    -------
    list
        A list of dictionaries, each containing the following keys:
        - "timestamp": The timestamp of the message.
        - "seq": The sequence number of the message.
        - "msg_data": The data of the message.
        - "address": The ICAO address extracted from the message data.
        - "metadata": The metadata extracted from the message data and parsed as JSON.
    """

    def get_flight_observations(self, session_id: str) -> list[FlightObeservationSchema]:
        """
        Retrieves and processes observations from the given consumer group.
        Args:
            cg: The consumer group object from which to read messages.
        Returns:
            A list of dictionaries, each containing the following keys:
            - "timestamp": The timestamp of the message.
            - "seq": The sequence number of the message.
            - "msg_data": The data of the message.
            - "address": The ICAO address extracted from the message data.
            - "metadata": The metadata extracted and parsed from the message data.
        """
        my_database_reader = FlightBlenderDatabaseReader()

        r = get_redis()
        key = "last_reading_for_{session_id}".format(session_id=session_id)
        if r.exists(key):
            last_reading_time = r.get(key)
            after_datetime = arrow.get(last_reading_time)
        else:
            now = arrow.now()
            one_second_before_now = now.shift(seconds=-1)
            after_datetime = one_second_before_now

        r.set(key, arrow.now().isoformat())
        r.expire(key, 300)
        pending_messages = []
        all_flight_observations = my_database_reader.get_flight_observations_by_session(session_id=session_id, after_datetime=after_datetime)
        for message in all_flight_observations:
            observation = FlightObeservationSchema(
                id=message["id"],
                session_id=message["session_id"],
                latitude_dd=message["latitude_dd"],
                longitude_dd=message["longitude_dd"],
                altitude_mm=message["altitude_mm"],
                traffic_source=message["traffic_source"],
                source_type=message["source_type"],
                icao_address=message["icao_address"],
                created_at=message["created_at"],
                updated_at=message["updated_at"],
                metadata=json.loads(message["metadata"]),
            )
            pending_messages.append(observation)
        return pending_messages

    def get_observations(self, cg) -> list[Observation]:
        """
        Retrieves and processes observations from the given consumer group.
        Args:
            cg: The consumer group object from which to read messages.
        Returns:
            A list of dictionaries, each containing the following keys:
            - "timestamp": The timestamp of the message.
            - "seq": The sequence number of the message.
            - "msg_data": The data of the message.
            - "address": The ICAO address extracted from the message data.
            - "metadata": The metadata extracted and parsed from the message data.
        """

        messages = cg.read()
        pending_messages = []
        for message in messages:
            observation = Observation(
                timestamp=message.timestamp,
                seq=message.sequence,
                msg_data=message.data,
                address=message.data["icao_address"],
                metadata=json.loads(message.data["metadata"]),
            )
            pending_messages.append(asdict(observation))
        return pending_messages
