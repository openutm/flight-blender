"""
Pydantic schemas for weather monitoring.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class WeatherRequest(BaseModel):
    latitude: float = Field(..., ge=-90.0, le=90.0)
    longitude: float = Field(..., ge=-180.0, le=180.0)
    time: datetime | None = None
    timezone: str = "UTC"


class WeatherResponse(BaseModel):
    latitude: float
    longitude: float
    current_weather: dict[str, Any] | None = None
    hourly: dict[str, Any] | None = None
