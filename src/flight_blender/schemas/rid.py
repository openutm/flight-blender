"""
Pydantic schemas for Remote ID operations.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateDSSSubscriptionRequest(BaseModel):
    view: str = Field(..., description="Bounding box: 'lat_lo,lng_lo,lat_hi,lng_hi'")
    end_datetime: datetime


class ISASubscriptionResponse(BaseModel):
    id: uuid.UUID
    subscription_id: str
    view: str
    end_datetime: datetime
    view_hash: str
    is_simulated: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class RIDFlightDetailCreate(BaseModel):
    operation_description: str | None = None
    operator_location: dict[str, Any] | None = None
    operator_id: str | None = None
    auth_data: dict[str, Any] | None = None
    uas_id: dict[str, Any] | None = None
    eu_classification: dict[str, Any] | None = None


class RIDFlightDetailResponse(BaseModel):
    id: uuid.UUID
    operation_description: str | None
    operator_id: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RIDDisplayDataResponse(BaseModel):
    flights: list[dict[str, Any]]


class RIDUserNotificationsResponse(BaseModel):
    notifications: list[dict[str, Any]]


class RIDCapabilitiesResponse(BaseModel):
    capabilities: list[str]


class CreateTestRequest(BaseModel):
    requested_flights: list[dict[str, Any]]


class RIDTestResponse(BaseModel):
    version: int


class RIDFlightDetailsResponse(BaseModel):
    id: str
    aircraft_type: str | None = None
    current_state: dict[str, Any] | None = None
    operating_area: dict[str, Any] | None = None
    simulated: bool = False
    recent_positions: list[dict[str, Any]] = []
