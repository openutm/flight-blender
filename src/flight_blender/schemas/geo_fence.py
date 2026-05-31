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
    message: str | None = None
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
    """InterUSS ED-269 geo-awareness map query.

    The InterUSS qualifier posts ``{"checks": [{"filter_sets": [...]}]}``. Each
    filter set may carry a ``position`` (a ``[lon, lat]`` pair), an ``after`` /
    ``before`` time window, and/or an ``ed269`` block. All fields are optional so
    an empty ``checks`` array is accepted (and resolves to ``Absent``).
    """

    checks: list[dict[str, Any]] = Field(default_factory=list)


class GeoZoneCheckResult(BaseModel):
    geozone: str


class GeoZoneChecksResponse(BaseModel):
    applicableGeozone: list[GeoZoneCheckResult]  # noqa: N815 - ED-269 wire name
    message: str | None = None


class GeoAwarenessStatusResponse(BaseModel):
    """ED-269 geo-awareness test-harness status."""

    status: str
    api_version: str = "latest"


class GeoZoneHttpsSource(BaseModel):
    url: str
    format: str = "ED-269"


class GeoZoneSourceRequest(BaseModel):
    """InterUSS geospatial data source create/update body."""

    https_source: GeoZoneHttpsSource


class GeoAwarenessTestStatus(BaseModel):
    """Status record stored per geozone source id."""

    result: str
    message: str = ""
