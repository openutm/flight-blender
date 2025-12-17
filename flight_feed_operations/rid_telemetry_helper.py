from enum import Enum

import dacite
from dacite import from_dict

from rid_operations.data_definitions import (
    RIDAircraftState,
    RIDFlightDetails,
    SignedTelemetryRequest,
    SignedUnSignedTelemetryObservations,
    SubmittedTelemetryFlightDetails,
)


class NestedDict(dict):
    def convert_value(self, obj):
        if isinstance(obj, Enum):
            return obj.value
        return obj

    def __init__(self, data):
        super().__init__(self.convert_value(x) for x in data if x[1] is not None)


def generate_rid_telemetry_objects(
    signed_telemetry_request: SignedTelemetryRequest,
) -> list[SubmittedTelemetryFlightDetails]:
    """
    Generate a list of RID telemetry objects from signed telemetry requests.
    Args:
        signed_telemetry_request (SignedTelemetryRequest): A list of signed telemetry requests.
    Returns:
        List[SubmittedTelemetryFlightDetails]: A list of submitted telemetry flight details objects.
    """
    all_rid_data = []

    for current_signed_telemetry_request in signed_telemetry_request:
        s = from_dict(
            data_class=SubmittedTelemetryFlightDetails,
            data=current_signed_telemetry_request,
            config=dacite.Config(cast=[Enum]),
        )
        all_rid_data.append(s)

    return all_rid_data


def generate_unsigned_rid_telemetry_objects(
    telemetry_request: list[SignedUnSignedTelemetryObservations],
) -> list[SubmittedTelemetryFlightDetails]:
    """
    Generate a list of unsigned RID telemetry objects from the given telemetry request.
    Args:
        telemetry_request (List[SignedUnSignedTelemetryObservations]): A list of telemetry observations
        that need to be converted to unsigned RID telemetry objects.
    Returns:
        List[SubmittedTelemetryFlightDetails]: A list of submitted telemetry flight details generated
        from the given telemetry request.
    """

    all_rid_data = []

    for current_unsigned_telemetry_request in telemetry_request:
        s = from_dict(
            data_class=SubmittedTelemetryFlightDetails,
            data=current_unsigned_telemetry_request,
            config=dacite.Config(cast=[Enum]),
        )
        all_rid_data.append(s)

    return all_rid_data


class FlightBlenderTelemetryValidator:
    """
    A class to validate and parse telemetry data for Flight Blender.
    Methods
    -------
    parse_validate_current_states(current_states) -> List[RIDAircraftState]:
        Parses and validates a list of current state objects and returns a list of RIDAircraftState dataclasses.
    parse_validate_rid_details(rid_flight_details) -> RIDFlightDetails:
        Parses and validates RID flight details and returns an RIDFlightDetails dataclass.
    validate_flight_details_current_states_exist(flight) -> bool:
        Validates that both flight details and current states exist in the flight data.
    validate_observation_key_exists(raw_request_data) -> bool:
        Validates that the 'observations' key exists in the raw request data.
    """

    def parse_validate_current_states(self, current_states) -> list[RIDAircraftState]:
        """
        Parses and validates a list of current aircraft states.
        Args:
            current_states (List[dict]): A list of dictionaries representing the current states of aircraft.
        Returns:
            List[RIDAircraftState]: A list of validated and parsed RIDAircraftState objects.
        """

        all_states = []

        for state in current_states:
            aircraft_state = from_dict(
                data_class=RIDAircraftState,
                data=state,
                config=dacite.Config(cast=[Enum]),
            )
            all_states.append(aircraft_state)
        return all_states

    def parse_validate_rid_details(self, rid_flight_details) -> RIDFlightDetails:
        flight_details = from_dict(
            data_class=RIDFlightDetails,
            data=rid_flight_details,
            config=dacite.Config(cast=[Enum]),
        )

        return flight_details

    def validate_flight_details_current_states_exist(self, flight) -> bool:
        """
        Validates if the given flight dictionary contains both 'flight_details' and 'current_states' keys.
        Args:
            flight (dict): The flight dictionary to validate.
        Returns:
            bool: True if both 'flight_details' and 'current_states' keys exist in the flight dictionary, False otherwise.
        """

        return "flight_details" in flight and "current_states" in flight

    def validate_observation_key_exists(self, raw_request_data) -> bool:
        """
        Validate that the 'observations' key exists in the provided raw request data.
        Args:
            raw_request_data (dict): The raw request data to be validated.
        Returns:
            bool: True if the 'observations' key exists in the raw request data, False otherwise.
        """
        return "observations" in raw_request_data
