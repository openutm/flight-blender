"""
Pydantic schemas for flight feed operations.
"""

import json as _json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class SignedTelemetryPublicKeyBase(BaseModel):
    key_id: str
    url: str
    is_active: bool = True


class SignedTelemetryPublicKeyCreate(SignedTelemetryPublicKeyBase):
    pass


class SignedTelemetryPublicKeyUpdate(BaseModel):
    key_id: str | None = None
    url: str | None = None
    is_active: bool | None = None


class SignedTelemetryPublicKeyResponse(SignedTelemetryPublicKeyBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class SingleObservation(BaseModel):
    lat_dd: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees")
    lon_dd: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees")
    altitude_mm: float = Field(..., description="Altitude in millimetres")
    traffic_source: int = Field(..., description="Traffic source code")
    source_type: int = Field(..., description="Source type code")
    icao_address: str = Field(..., description="ICAO 24-bit address")
    metadata: str = Field(default="{}", description="Raw metadata JSON string")
    sensor_timestamp: datetime | None = None

    @field_validator("metadata", mode="before")
    @classmethod
    def coerce_metadata_to_str(cls, v: Any) -> str:
        if isinstance(v, dict):
            return _json.dumps(v)
        if v is None:
            return "{}"
        return str(v)


class BulkObservationRequest(BaseModel):
    observations: list[SingleObservation]


class TelemetryObservation(BaseModel):
    """Single signed/unsigned telemetry entry (ASTM RID format)."""

    current_state: dict
    flight_details: dict | None = None


class RIDTelemetryObservationEntry(BaseModel):
    """One entry in the RID telemetry bulk payload sent by the toolkit.

    Matches the structure built by ``_build_telemetry_payload`` in the
    verification toolkit::

        {"observations": [{"current_states": [...], "flight_details": {...}}]}
    """

    current_states: list[dict] = Field(default_factory=list)
    flight_details: dict | None = None

    model_config = {"extra": "allow"}


class RIDTelemetryRequest(BaseModel):
    """Bulk RID telemetry request as sent by the openutm verification toolkit.

    The toolkit calls ``PUT /flight_stream/set_telemetry`` with this payload and
    expects a 201 response.
    """

    observations: list[RIDTelemetryObservationEntry]

    model_config = {"extra": "allow"}


class FlightObservationResponse(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID | None
    latitude_dd: float
    longitude_dd: float
    altitude_mm: float
    traffic_source: int
    source_type: int
    icao_address: str
    sensor_timestamp: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
