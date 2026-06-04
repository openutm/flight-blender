from pydantic import BaseModel, ConfigDict


class ObservationIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lat_dd: float
    lon_dd: float
    altitude_mm: float
    icao_address: str
    traffic_source: int
    timestamp: int
    source_type: int = 0
    metadata: dict = {}


class ObservationRequest(BaseModel):
    observations: list[ObservationIn]


class SignedTelemetryKeyCreate(BaseModel):
    key_id: str
    url: str
    is_active: bool = True


class SignedTelemetryKeyUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    key_id: str | None = None
    url: str | None = None
    is_active: bool | None = None
