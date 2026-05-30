"""
Pydantic schemas for SCD (Strategic Conflict Detection) operations.
"""

import uuid
from typing import Any

from pydantic import BaseModel


class SCDStatusResponse(BaseModel):
    status: str
    version: str = "1.0.0"


class SCDCapabilitiesResponse(BaseModel):
    capabilities: list[str]


class FlightPlanUpsertRequest(BaseModel):
    """PUT body for ASTM flight planning endpoints."""

    intended_flight: dict[str, Any]
    usage_state: str = "Planned"
    uas_state: str = "Nominal"


class ClearAreaRequest(BaseModel):
    extent: dict[str, Any]
    request_id: str


class ClearAreaResponse(BaseModel):
    outcome: dict[str, Any]


class FlightPlanResponse(BaseModel):
    flight_plan_id: uuid.UUID
    planning_result: str
    notes: str | None = None
    flight_plan: dict[str, Any] | None = None
