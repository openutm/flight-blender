from dataclasses import dataclass
from typing import List, Optional

from shapely.geometry import shape

# from marshmallow import Schema, fields

# class FlightDeclarationSchema(Schema):
#     name = fields.String(required=True)
#     type_of_operation = fields.Str(required=True)
#     submitted_by = fields.Email()
#     approved_by = fields.Email()
#     is_approved = fields.Bool()
#     state = fields.int()
#     originating_party = fields.Str()

#     #Computed fields
#     #bounds = fields.Str()
#     #state = fields.Int()
#     #type_of_operation = fields.Int()
#     #flight_declaration_raw_geojson = fields.Str()
#     #operational_intent = fields.Str()


@dataclass
class FlightDeclarationRequest:
    """Class for keeping track of an operational intent test injections"""

    features: List[shape]
    type_of_operation: int
    submitted_by: Optional[str]
    approved_by: Optional[str]
    is_approved: bool
    state: int


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
class HTTP404Response:
    message: str


@dataclass
class HTTP400Response:
    message: str


@dataclass
class GeoFenceMetadata:
    start_date: str
    end_date: str
    geo_fence_id: str


@dataclass
class FlightDeclarationMetadata:
    start_date: str
    end_date: str
    flight_declaration_id: str
