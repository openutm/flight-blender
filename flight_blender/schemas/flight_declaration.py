"""
Pydantic schemas for flight declaration operations.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


class FlightDeclarationCreate(BaseModel):
    operational_intent: str
    flight_declaration_raw_geojson: str | None = None
    type_of_operation: int = Field(default=1, ge=1, le=3)
    bounds: str
    aircraft_id: str
    state: int = Field(default=0, ge=0, le=8)
    originating_party: str = "Flight Blender Default"
    submitted_by: str | None = None
    start_datetime: datetime
    end_datetime: datetime

    @field_validator("end_datetime")
    @classmethod
    def end_after_start(cls, v: datetime, info) -> datetime:
        start = info.data.get("start_datetime")
        if start and v < start:
            raise ValueError("end_datetime must be after start_datetime")
        return v


class FlightDeclarationUpdate(BaseModel):
    operational_intent: str | None = None
    flight_declaration_raw_geojson: str | None = None
    type_of_operation: int | None = None
    bounds: str | None = None
    aircraft_id: str | None = None
    state: int | None = None
    originating_party: str | None = None
    is_approved: bool | None = None


class FlightDeclarationStateUpdate(BaseModel):
    state: int = Field(..., ge=0, le=8)


class FlightDeclarationApproval(BaseModel):
    is_approved: bool
    approved_by: str | None = None


class FlightDeclarationResponse(BaseModel):
    id: uuid.UUID
    operational_intent: str
    flight_declaration_raw_geojson: str | None
    type_of_operation: int
    bounds: str
    aircraft_id: str
    state: int
    originating_party: str
    submitted_by: str | None
    approved_by: str | None
    is_approved: bool
    start_datetime: datetime
    end_datetime: datetime
    latest_telemetry_datetime: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class FlightDeclarationListResponse(BaseModel):
    count: int
    results: list[FlightDeclarationResponse]


class FlightDeclarationCreateResponse(BaseModel):
    id: uuid.UUID
    message: str
    is_approved: bool
    state: int


class BulkFlightDeclarationResult(BaseModel):
    id: uuid.UUID | None
    message: str
    success: bool


class BulkFlightDeclarationCreateResponse(BaseModel):
    submitted: int
    failed: int
    results: list[BulkFlightDeclarationResult]


class OperationalIntentCreate(BaseModel):
    """Payload for creating/updating an operational intent."""

    volumes: list[dict[str, Any]]
    off_nominal_volumes: list[dict[str, Any]] = []
    priority: int = 0
    state: str = "Accepted"


class SubmitToDSSResponse(BaseModel):
    message: str
    dss_response: dict | None = None
