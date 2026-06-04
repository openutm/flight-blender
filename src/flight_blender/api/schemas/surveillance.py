from typing import Literal

from pydantic import BaseModel


class SurveillanceSessionAction(BaseModel):
    action: Literal["start", "stop"]


class SensorHealthUpdate(BaseModel):
    status: str
    recovery_type: str | None = None
