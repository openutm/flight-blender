"""
Pydantic schemas for geo fence operations.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GeoFenceCreate(BaseModel):
    raw_geo_fence: str | None = None
    geozone: str | None = None
    upper_limit: float
    lower_limit: float
    altitude_ref: int = 0
    name: str = Field(..., max_length=50)
    bounds: str
    start_datetime: datetime
    end_datetime: datetime
    is_test_dataset: bool = False


class GeoFenceUpdate(BaseModel):
    raw_geo_fence: str | None = None
    geozone: str | None = None
    upper_limit: float | None = None
    lower_limit: float | None = None
    altitude_ref: int | None = None
    name: str | None = None
    bounds: str | None = None
    status: int | None = None
    start_datetime: datetime | None = None
    end_datetime: datetime | None = None


class GeoFenceResponse(BaseModel):
    id: uuid.UUID
    raw_geo_fence: str | None
    geozone: str | None
    upper_limit: float
    lower_limit: float
    altitude_ref: int
    name: str
    bounds: str
    status: int
    is_test_dataset: bool
    start_datetime: datetime
    end_datetime: datetime
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class GeoFenceListResponse(BaseModel):
    count: int
    results: list[GeoFenceResponse]


class GeoZoneQueryRequest(BaseModel):
    """ED-269 geo-awareness query."""

    volumes: list[dict[str, Any]]
    after: datetime | None = None
    before: datetime | None = None


class GeoAwarenessStatusResponse(BaseModel):
    result: str
    message: str | None = None


class GeospatialDataSourceCreate(BaseModel):
    url: str
    name: str
    description: str = ""


class GeospatialDataSourceResponse(BaseModel):
    id: uuid.UUID
    url: str
    name: str
    description: str
    created_at: datetime

    model_config = {"from_attributes": True}
