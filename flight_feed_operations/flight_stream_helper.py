import json
from itertools import zip_longest

import arrow
import shapely
from dotenv import find_dotenv, load_dotenv
from shapely.geometry import Point
from shapely.geometry import box as shapely_box

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

    def __init__(self, view_port_box=None):
        self.view_port_box: shapely_box = view_port_box

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
            twenty_second_before_now = now.shift(seconds=-20)
            after_datetime = twenty_second_before_now

        r.set(key, arrow.now().isoformat())
        r.expire(key, 300)
        pending_messages = []
        all_flight_observations = my_database_reader.get_flight_observations(after_datetime=after_datetime)
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
            if self.view_port_box:
                if shapely.contains(
                    self.view_port_box,
                    Point(observation.latitude_dd, observation.longitude_dd),
                ):
                    pending_messages.append(observation)
            else:
                pending_messages.append(observation)

        return pending_messages

    def get_closest_observation_for_now(self, now: arrow.arrow.Arrow):
        my_database_reader = FlightBlenderDatabaseReader()

        all_observations = []
        closest_observations = my_database_reader.get_closest_flight_observation_for_now(now=now)
        for closest_observation in closest_observations:
            single_observation = FlightObservationSchema(
                id=str(closest_observation.id),
                session_id=str(closest_observation.session_id),
                latitude_dd=closest_observation.latitude_dd,
                longitude_dd=closest_observation.longitude_dd,
                altitude_mm=closest_observation.altitude_mm,
                traffic_source=closest_observation.traffic_source,
                source_type=closest_observation.source_type,
                icao_address=closest_observation.icao_address,
                created_at=closest_observation.created_at.isoformat(),
                updated_at=closest_observation.updated_at.isoformat(),
                metadata=json.loads(closest_observation.metadata),
            )
            if self.view_port_box:
                if shapely.contains(
                    self.view_port_box,
                    Point(single_observation.latitude_dd, single_observation.longitude_dd),
                ):
                    all_observations.append(single_observation)
            else:
                all_observations.append(single_observation)
        return all_observations

    def get_all_flight_observations(self) -> list[FlightObservationSchema]:
        my_database_reader = FlightBlenderDatabaseReader()

        pending_messages = []
        all_flight_observations = my_database_reader.get_flight_observation_objects()
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
            if self.view_port_box:
                if shapely.contains(
                    self.view_port_box,
                    Point(observation.latitude_dd, observation.longitude_dd),
                ):
                    pending_messages.append(observation)
            else:
                pending_messages.append(observation)

        return pending_messages

    def get_latest_flight_observation_by_flight_declaration_id(self, flight_declaration_id: str) -> FlightObservationSchema | None:
        my_database_reader = FlightBlenderDatabaseReader()

        latest_observation = my_database_reader.get_latest_flight_observation_by_session(session_id=flight_declaration_id)
        if latest_observation:
            return FlightObservationSchema(
                id=str(latest_observation.id),
                session_id=str(latest_observation.session_id),
                latitude_dd=latest_observation.latitude_dd,
                longitude_dd=latest_observation.longitude_dd,
                altitude_mm=latest_observation.altitude_mm,
                traffic_source=latest_observation.traffic_source,
                source_type=latest_observation.source_type,
                icao_address=latest_observation.icao_address,
                created_at=latest_observation.created_at.isoformat(),
                updated_at=latest_observation.updated_at.isoformat(),
                metadata=json.loads(latest_observation.metadata),
            )
        return None

    def get_temporal_flight_observations_by_session(self, session_id: str) -> list[FlightObservationSchema]:
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
            twenty_second_before_now = now.shift(seconds=-20)
            after_datetime = twenty_second_before_now

        r.set(key, arrow.now().isoformat())
        r.expire(key, 300)
        pending_messages = []
        all_flight_observations = my_database_reader.get_temporal_flight_observations_by_session(session_id=session_id, after_datetime=after_datetime)
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
            if self.view_port_box:
                if shapely.contains(
                    self.view_port_box,
                    Point(observation.latitude_dd, observation.longitude_dd),
                ):
                    pending_messages.append(observation)
            else:
                pending_messages.append(observation)

        return pending_messages
