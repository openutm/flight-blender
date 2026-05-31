"""
Pydantic schemas for weather monitoring.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class WeatherRequest(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    time: datetime | None = None
    timezone: str = "UTC"


class WeatherResponse(BaseModel):
    """Weather forecast response mirroring the Django ``WeatherSerializer`` shape.

    Emits exactly the serializer's declared fields (latitude, longitude,
    generationtime_ms, utc_offset_seconds, timezone, timezone_abbreviation,
    elevation, hourly_units, hourly). Like the DRF serializer, undeclared
    upstream keys (e.g. ``current_weather``) are dropped (``extra="ignore"``).
    Fields are optional so partial upstream payloads validate without error.
    """

    model_config = ConfigDict(extra="ignore")

    latitude: float | None = None
    longitude: float | None = None
    generationtime_ms: float | None = None
    utc_offset_seconds: int | None = None
    timezone: str | None = None
    timezone_abbreviation: str | None = None
    elevation: float | None = None
    hourly_units: dict[str, Any] | None = None
    hourly: dict[str, Any] | None = None
