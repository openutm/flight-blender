from dataclasses import dataclass, field

from marshmallow import Schema
from marshmallow import fields as ma_fields
from pydantic import BaseModel, ConfigDict, Field


class ObservationIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lat_dd: float
    lon_dd: float
    altitude_mm: float
    icao_address: str
    traffic_source: int
    timestamp: int
    source_type: int = 0
    metadata: dict = Field(default_factory=dict)


class ObservationRequest(BaseModel):
    observations: list[ObservationIn]


@dataclass
class FlightObservationSchema:
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
class SingleAirtrafficObservation:
    lat_dd: float
    lon_dd: float
    altitude_mm: float
    traffic_source: int
    source_type: int
    icao_address: str
    timestamp: int = 0
    metadata: dict = field(default_factory=dict)
    session_id: str | None = ""
    ingested_at_ms: int = 0


@dataclass
class FlightObservationsProcessingResponse:
    message: str
    status: int


class ObservationSchema(Schema):
    lat_dd = ma_fields.Float(required=True)
    lon_dd = ma_fields.Float(required=True)
    altitude_mm = ma_fields.Float(required=True)
    icao_address = ma_fields.String(required=True)
    traffic_source = ma_fields.Integer(required=True)
    timestamp = ma_fields.Integer(required=True)
    source_type = ma_fields.Integer(required=False)
    metadata = ma_fields.Dict(required=False)


@dataclass
class SingleObservationMetadata:
    aircraft_type: str


@dataclass
class Observation:
    timestamp: str
    seq: int
    msg_data: dict
    address: str
    metadata: dict


@dataclass
class StoredFlightMessage:
    timestamp: str
    seq: int
    msg_data: dict
    icao_address: str


@dataclass
class SingleRIDObservation:
    lat_dd: float
    lon_dd: float
    altitude_mm: float
    traffic_source: int
    source_type: int
    icao_address: str
    timestamp: int = 0
    metadata: dict = field(default_factory=dict)
    session_id: str | None = ""


@dataclass
class MessageVerificationFailedResponse:
    message: str


@dataclass
class TrafficInformationDiscoveryResponse:
    message: str
    url: str
    description: str


# --- HTTP request/response schemas ---
from pydantic import BaseModel, ConfigDict

from flight_blender.schemas.flight_feed import ObservationIn, ObservationRequest

__all__ = [
    "ObservationIn",
    "ObservationRequest",
    "SignedTelemetryKeyCreate",
    "SignedTelemetryKeyUpdate",
]


class SignedTelemetryKeyCreate(BaseModel):
    key_id: str
    url: str
    is_active: bool = True


class SignedTelemetryKeyUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key_id: str | None = None
    url: str | None = None
    is_active: bool | None = None
