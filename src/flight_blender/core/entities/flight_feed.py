from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, Field


class ObservationIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    lat_dd: float
    lon_dd: float
    altitude_mm: float
    icao_address: str
    traffic_source: int
    timestamp: int
    source_type: int = 0
    metadata: dict = Field(default_factory=dict)


class ObservationRequest(BaseModel):
    observations: list[ObservationIn]


@dataclass
class FlightObservationSchema:
    id: str
    session_id: str
    latitude_dd: float
    longitude_dd: float
    altitude_mm: float
    traffic_source: int
    source_type: int
    icao_address: str

    created_at: str
    updated_at: str

    metadata: dict


@dataclass
class SingleAirtrafficObservation:
    lat_dd: float
    lon_dd: float
    altitude_mm: float
    traffic_source: int
    source_type: int
    icao_address: str
    timestamp: int = 0
    metadata: dict = field(default_factory=dict)
    session_id: str | None = ""
    ingested_at_ms: int = 0


@dataclass
class FlightObservationsProcessingResponse:
    message: str
    status: int
