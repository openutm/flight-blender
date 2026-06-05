# --- HTTP request/response schemas ---
from typing import Any

from pydantic import BaseModel, ConfigDict


class CreateTestBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    requested_flights: list[Any]


class ISACallbackBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    service_area: dict | None = None
    subscriptions: list[dict]
    extents: dict | None = None
