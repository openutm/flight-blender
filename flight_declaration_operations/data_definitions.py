from dataclasses import dataclass

from marshmallow import Schema, fields

from flight_declaration_operations.models import FlightDeclaration
from geo_fence_operations.models import GeoFence


class CreateFlightDeclarationViaOperationalIntentRequestSchema(Schema):
    """
    Schema for creating a flight declaration request by submitting an operational intent.
    """

    originating_party = fields.Str(required=True)
    start_datetime = fields.DateTime(required=True)
    end_datetime = fields.DateTime(required=True)
    operational_intent_volume4ds = fields.List(fields.Dict(), required=True)
    type_of_operation = fields.Int(required=True)
    aircraft_id = fields.Str(required=True)

    expect_telemetry = fields.Bool(required=False)
    vehicle_id = fields.Str(required=False)
    plan_id = fields.Str(required=False)
    sequence_number = fields.Int(required=False)
    operator_id = fields.Str(required=False)
    version = fields.Str(required=False)
    flight_approved = fields.Bool(required=False)
    purpose = fields.Str(required=False)
    flight_state = fields.Int(required=False)
    exchange_type = fields.Str(required=False)
    flight_id = fields.Str(required=False)
    contact_url = fields.Url(required=False)
    is_approved = fields.Bool(required=False)
    submitted_by = fields.Str(required=False)


class CreateFlightDeclarationRequestSchema(Schema):
    """
    Schema for creating a flight declaration request.

    Attributes:
        originating_party (str): The party originating the flight declaration.
        start_datetime (datetime): The start date and time of the flight.
        end_datetime (datetime): The end date and time of the flight.
        flight_declaration_geo_json (dict): The GeoJSON representation of the flight declaration.
        type_of_operation (int): The type of operation for the flight.
        aircraft_id (str): The ID of the aircraft.

    Optional Attributes:
        expect_telemetry (bool): Indicates if telemetry is expected.
        vehicle_id (str): The ID of the vehicle.
        plan_id (str): The ID of the flight plan.
        sequence_number (int): The sequence number of the flight.
        operator_id (str): The ID of the operator.
        version (str): The version of the flight declaration.
        flight_approved (bool): Indicates if the flight is approved.
        purpose (str): The purpose of the flight.
        flight_state (int): The state of the flight.
        exchange_type (str): The type of exchange.
        flight_id (str): The ID of the flight.
        contact_url (str): The contact URL for the flight.
    """

    originating_party = fields.Str(required=True)
    start_datetime = fields.DateTime(required=True)
    end_datetime = fields.DateTime(required=True)
    flight_declaration_geo_json = fields.Dict(required=True)
    type_of_operation = fields.Int(required=True)
    aircraft_id = fields.Str(required=True)

    expect_telemetry = fields.Bool(required=False)
    vehicle_id = fields.Str(required=False)
    plan_id = fields.Str(required=False)
    sequence_number = fields.Int(required=False)
    operator_id = fields.Str(required=False)
    version = fields.Str(required=False)
    flight_approved = fields.Bool(required=False)
    purpose = fields.Str(required=False)
    flight_state = fields.Int(required=False)
    exchange_type = fields.Str(required=False)
    flight_id = fields.Str(required=False)
    contact_url = fields.Url(required=False)
    is_approved = fields.Bool(required=False)
    submitted_by = fields.Str(required=False)


@dataclass
class IntersectionCheckResult:
    all_relevant_fences: list[GeoFence]
    all_relevant_declarations: list[FlightDeclaration]
    is_approved: bool
    declaration_state: int


@dataclass
class Altitude:
    meters: int
    datum: str


@dataclass
class FlightDeclarationCreateResponse:
    """Hold data for success response"""

    id: str
    message: str
    is_approved: int
    state: int


@dataclass
class BulkFlightDeclarationCreateResponse:
    """Hold data for bulk submission response"""

    submitted: int
    failed: int
    results: list


@dataclass
class HTTP404Response:
    message: str


@dataclass
class HTTP400Response:
    message: str


@dataclass
class FlightDeclarationMetadata:
    start_date: str
    end_date: str
    flight_declaration_id: str
