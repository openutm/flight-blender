from dataclasses import dataclass
from typing import Optional

from marshmallow import Schema, fields


class ObservationSchema(Schema):
    lat_dd = fields.Float(required=True)
    lon_dd = fields.Float(required=True)
    altitude_mm = fields.Float(required=True)
    icao_address = fields.String(required=True)
    traffic_source = fields.Integer(required=True)
    timestamp = fields.Integer(required=True)
    source_type = fields.Integer(required=False)
    metadata = fields.Dict(required=False)


@dataclass
class SingleObservationMetadata:
    """A class to store RemoteID metadata"""

    aircraft_type: str


@dataclass
class FlightObeservationSchema:
    id: str
    session_id: str
    latitude_dd: float
    longitude_dd: float
    altitude_mm: float
    traffic_source: int
    source_type: int
    icao_address: str

    created_at: str
    updated_at: str

    metadata: dict


@dataclass
class Observation:
    timestamp: str
    seq: int
    msg_data: dict
    address: str
    metadata: dict


# Extract unique flight messages with necessary details
@dataclass
class StoredFlightMessage:
    timestamp: str
    seq: int
    msg_data: dict
    icao_address: str


@dataclass
class SingleRIDObservation:
    """This is the object stores details of the observation"""

    lat_dd: float
    lon_dd: float
    altitude_mm: float
    traffic_source: int
    source_type: int
    icao_address: str
    metadata: Optional[dict]


@dataclass
class SingleAirtrafficObservation:
    """This is the object stores details of the observation"""

    lat_dd: float
    lon_dd: float
    altitude_mm: float
    traffic_source: int
    source_type: int
    icao_address: str
    metadata: Optional[dict]
    session_id: Optional[str] = ""


@dataclass
class FlightObservationsProcessingResponse:
    message: str
    status: int


@dataclass
class MessageVerificationFailedResponse:
    message: str


@dataclass
class TrafficInformationDiscoveryResponse:
    message: str
    url: str
    description: str
