from dataclasses import dataclass
from datetime import datetime

from marshmallow import Schema, fields


class CreateFlightDeclarationViaOperationalIntentRequestSchema(Schema):
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
class DeconflictionRequest:
    start_datetime: datetime
    end_datetime: datetime
    view_box: list[float]
    ussp_network_enabled: int
    declaration_id: str | None = None
    flight_declaration_geo_json: dict | None = None
    type_of_operation: int = 0
    priority: int = 0


@dataclass
class DeconflictionResult:
    all_relevant_fences: list
    all_relevant_declarations: list
    is_approved: bool
    declaration_state: int


IntersectionCheckResult = DeconflictionResult


@dataclass
class FlightDeclarationCreateResponse:
    id: str
    message: str
    is_approved: int
    state: int


@dataclass
class BulkFlightDeclarationCreateResponse:
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
class Altitude:
    meters: int
    datum: str


@dataclass
class FlightDeclarationMetadata:
    start_date: str
    end_date: str
    flight_declaration_id: str
