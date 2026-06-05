from pydantic import BaseModel, ConfigDict

from flight_blender.core.entities.flight_feed import ObservationIn, ObservationRequest

__all__ = [
    "ObservationIn",
    "ObservationRequest",
    "SignedTelemetryKeyCreate",
    "SignedTelemetryKeyUpdate",
]


class SignedTelemetryKeyCreate(BaseModel):
    key_id: str
    url: str
    is_active: bool = True


class SignedTelemetryKeyUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key_id: str | None = None
    url: str | None = None
    is_active: bool | None = None
