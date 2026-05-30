"""
Pydantic schemas for flight feed operations.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


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


class BulkObservationRequest(BaseModel):
    observations: list[SingleObservation]


class TelemetryObservation(BaseModel):
    """Single signed/unsigned telemetry entry (ASTM RID format)."""

    current_state: dict
    flight_details: dict | None = None


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
