"""
Pydantic schemas for surveillance monitoring operations.
"""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SurveillanceSensorCreate(BaseModel):
    sensor_type: int
    sensor_identifier: str = Field(..., max_length=256)
    refresh_rate_seconds: float = 1.0
    is_active: bool = True
    horizontal_accuracy_m: float = 5.0
    vertical_accuracy_m: float = 5.0
    expected_latency_ms: int = 150


class SurveillanceSensorResponse(BaseModel):
    id: uuid.UUID
    sensor_type: int
    sensor_identifier: str
    refresh_rate_seconds: float
    is_active: bool
    horizontal_accuracy_m: float
    vertical_accuracy_m: float
    expected_latency_ms: int
    created_at: datetime

    model_config = {"from_attributes": True}


class SurveillanceSensorHealthUpdate(BaseModel):
    status: str = Field(..., pattern="^(operational|degraded|outage)$")
    recovery_type: str | None = Field(default=None, pattern="^(automatic|manual)$")


class SurveillanceSensorHealthResponse(BaseModel):
    id: uuid.UUID
    sensor_id: uuid.UUID
    status: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class SurveillanceHealthResponse(BaseModel):
    status: str
    active_sessions: int
    sensors: list[SurveillanceSensorResponse]


class StartStopHeartbeatRequest(BaseModel):
    action: str = Field(..., pattern="^(start|stop)$")


class SurveillanceMetricsResponse(BaseModel):
    heartbeat_delivery_probability: float
    track_update_probability: float
    per_sensor_health: list[dict[str, Any]]
    aggregate_health: str
    active_sessions: int


class SensorFailureNotificationResponse(BaseModel):
    id: uuid.UUID
    sensor_id: uuid.UUID
    previous_status: str
    new_status: str
    recovery_type: str | None
    message: str
    created_at: datetime

    model_config = {"from_attributes": True}
