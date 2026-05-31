"""
Pydantic schemas for USS operations.
"""

import uuid
from typing import Any

from pydantic import BaseModel


class USSReportCreate(BaseModel):
    report: dict[str, Any]


class OperationalIntentDetailsResponse(BaseModel):
    operational_intent_id: uuid.UUID
    details: dict[str, Any]


class OperationalIntentDetailsUpdate(BaseModel):
    operational_intent: dict[str, Any]
    subscriptions: list[dict[str, Any]] = []


class TelemetryUpdate(BaseModel):
    operational_intent_id: uuid.UUID
    telemetry: dict[str, Any]
    off_nominal_positions: list[dict[str, Any]] = []


class ConstraintDetailsResponse(BaseModel):
    constraint_id: uuid.UUID
    details: dict[str, Any]


class USSFlightResponse(BaseModel):
    flights: list[dict[str, Any]]


class USSFlightDetailResponse(BaseModel):
    id: str
    details: dict[str, Any]
