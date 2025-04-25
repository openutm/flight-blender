import json
from itertools import zip_longest

import arrow
from dotenv import find_dotenv, load_dotenv

from auth_helper.common import get_redis
from common.database_operations import FlightBlenderDatabaseReader

from .data_definitions import FlightObservationSchema

load_dotenv(find_dotenv())


# iterate a list in batches of size n
def batcher(iterable, n):
    args = [iter(iterable)] * n
    return zip_longest(*args)


class ObservationReadOperations:
    """
    A class to handle operations related to reading flight observations.
    Methods:
        get_flight_observations(session_id: str) -> list[FlightObservationSchema]:
            Retrieves and processes flight observations for a given session ID.
        Retrieves and processes observations from the given session ID.
            session_id (str): The session ID for which to retrieve flight observations.
            list[FlightObservationSchema]: A list of FlightObservationSchema objects, each containing the following attributes:
                - id: The unique identifier of the observation.
                - session_id: The session ID associated with the observation.
                - latitude_dd: The latitude in decimal degrees.
                - longitude_dd: The longitude in decimal degrees.
                - altitude_mm: The altitude in millimeters.
                - traffic_source: The source of the traffic data.
                - source_type: The type of the source.
                - icao_address: The ICAO address extracted from the message data.
                - created_at: The timestamp when the observation was created.
                - updated_at: The timestamp when the observation was last updated.
                - metadata: The metadata extracted and parsed from the message data.

    """

    def get_flight_observations(self, session_id: str) -> list[FlightObservationSchema]:
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
        key = f"last_reading_for_{session_id}"
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
            observation = FlightObservationSchema(
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
