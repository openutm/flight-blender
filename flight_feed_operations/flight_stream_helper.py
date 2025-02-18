import json
from itertools import zip_longest

from dotenv import find_dotenv, load_dotenv

from auth_helper.common import get_walrus_database
from .data_definitions import Observation
from dataclasses import asdict
load_dotenv(find_dotenv())


# iterate a list in batches of size n
def batcher(iterable, n):
    args = [iter(iterable)] * n
    return zip_longest(*args)

class StreamHelperOps:
    """
    A class to handle operations related to stream helpers.
    Methods
    -------
    create_read_cg()
        Creates a consumer group for reading if it does not exist.
    get_read_cg(create=False)
        Retrieves the consumer group for reading, optionally creating it if it does not exist.
    create_pull_cg()
        Creates a consumer group for pulling if it does not exist.
    get_pull_cg(create=False)
        Retrieves the consumer group for pulling, optionally creating it if it does not exist.
    """

    def __init__(self):
        """
        Initializes the StreamHelperOps with a connection to the walrus database and sets the stream keys.
        """
        self.db = get_walrus_database()
        self.stream_keys = ["all_observations"]

    def create_read_cg(self):
        """
        Creates a consumer group for reading if it does not exist.
        """
        self.get_read_cg(create=True)

    def get_read_cg(self, create=False):
        """
        Retrieves the consumer group for reading, optionally creating it if it does not exist.
        Parameters
        ----------
        create : bool, optional
            If True, creates the consumer group if it does not exist (default is False).
        Returns
        -------
        ConsumerGroup
            The consumer group for reading.
        """
        cg = self.db.time_series("cg-read", self.stream_keys)
        if create:
            for stream in self.stream_keys:
                self.db.xadd(stream, {"data": ""})
            cg.create()
            cg.set_id("$")

        return cg

    def create_pull_cg(self):
        """
        Creates a consumer group for pulling if it does not exist.
        """
        self.get_pull_cg(create=True)

    def get_pull_cg(self, create=False):
        """
        Retrieves the consumer group for pulling, optionally creating it if it does not exist.
        Parameters
        ----------
        create : bool, optional
            If True, creates the consumer group if it does not exist (default is False).
        Returns
        -------
        ConsumerGroup
            The consumer group for pulling.
        """
        cg = self.db.time_series("cg-pull", self.stream_keys)
        if create:
            for stream in self.stream_keys:
                self.db.xadd(stream, {"data": ""})
            cg.create()
            cg.set_id("$")

        return cg

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

    def get_observations(self, cg)->list[Observation]:
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