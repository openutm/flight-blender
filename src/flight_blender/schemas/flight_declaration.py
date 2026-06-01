"""
Pydantic schemas for flight declaration operations.
"""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class FlightDeclarationBBoxRequest(BaseModel):
    """Simplified bounding-box payload for the /set_flight_declaration endpoint."""

    minx: float
    miny: float
    maxx: float
    maxy: float


from flight_blender.common.datetime_utils import parse_iso_utc


def _parse_utc(value: str) -> datetime:
    """Parse an ISO-8601 string into a timezone-aware UTC datetime (raises on failure)."""
    result = parse_iso_utc(value)
    if result is None:
        raise ValueError(f"Invalid datetime: {value!r}")
    return result


def _validate_date_window(start_raw: str, end_raw: str) -> None:
    """Raise ValueError if start/end fail the date-window rules."""
    try:
        start = _parse_utc(start_raw)
        end = _parse_utc(end_raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid datetime format: {exc}") from exc
    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=2)
    if start < now:
        raise ValueError("start_datetime must not be in the past")
    if end < now:
        raise ValueError("end_datetime must not be in the past")
    if start > horizon:
        raise ValueError("start_datetime must not be more than 2 days in the future")
    if end > horizon:
        raise ValueError("end_datetime must not be more than 2 days in the future")
    if end <= start:
        raise ValueError("end_datetime must be after start_datetime")


class FlightDeclarationFullRequest(BaseModel):
    """Full flight declaration payload as sent by the openutm verification toolkit."""

    start_datetime: str
    end_datetime: str
    aircraft_id: str
    originating_party: str = "Flight Blender Default"
    flight_declaration_geo_json: dict | None = None
    type_of_operation: int = 0
    flight_state: int = 1

    model_config = {"extra": "allow"}

    @model_validator(mode="after")
    def validate_dates_and_geometry(self) -> "FlightDeclarationFullRequest":
        _validate_date_window(self.start_datetime, self.end_datetime)
        geo = self.flight_declaration_geo_json
        if geo is not None:
            if geo.get("type") != "FeatureCollection":
                raise ValueError("flight_declaration_geo_json must be a GeoJSON FeatureCollection")
            for feat in geo.get("features") or []:
                props = feat.get("properties") or {}
                if "min_altitude" not in props or "max_altitude" not in props:
                    raise ValueError("every GeoJSON Feature must have min_altitude and max_altitude properties")
                try:
                    min_alt = props["min_altitude"]
                    max_alt = props["max_altitude"]
                    min_val = float(min_alt["meters"] if isinstance(min_alt, dict) else min_alt)
                    max_val = float(max_alt["meters"] if isinstance(max_alt, dict) else max_alt)
                    if min_val > max_val:
                        raise ValueError("min_altitude must not exceed max_altitude")
                except (TypeError, KeyError):
                    raise ValueError("min_altitude and max_altitude must be numeric or {meters, datum} objects")
        return self


class OperationalIntentIngestRequest(BaseModel):
    """Payload for the operational-intent ingest endpoints (Volume4D format)."""

    start_datetime: str
    end_datetime: str
    aircraft_id: str
    originating_party: str = "Flight Blender Default"
    type_of_operation: int = 0
    operational_intent_volume4ds: list[dict] = Field(min_length=1)
    submitted_by: str | None = None

    model_config = {"extra": "allow"}

    @model_validator(mode="after")
    def validate_dates_and_volumes(self) -> "OperationalIntentIngestRequest":
        _validate_date_window(self.start_datetime, self.end_datetime)
        for v4d in self.operational_intent_volume4ds:
            vol = v4d.get("volume", {})
            alt_lower = vol.get("altitude_lower")
            alt_upper = vol.get("altitude_upper")
            if isinstance(alt_lower, dict) and isinstance(alt_upper, dict):
                low_val = alt_lower.get("value")
                up_val = alt_upper.get("value")
                if low_val is not None and up_val is not None:
                    try:
                        if float(low_val) > float(up_val):
                            raise ValueError("altitude_lower must not exceed altitude_upper")
                    except TypeError:
                        pass
        return self


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
