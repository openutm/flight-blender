import uuid
from dataclasses import dataclass
from datetime import datetime

from shapely.geometry import Polygon as ShapelyPolygon


@dataclass
class PolygonAltitude:
    polygon: ShapelyPolygon
    altitude_upper: float
    altitude_lower: float


@dataclass
class ConformanceRecord:
    id: uuid.UUID
    flight_declaration_id: uuid.UUID
    conformance_state: int
    timestamp: datetime
    description: str
    event_type: str
    geofence_breach: bool
    geofence_id: uuid.UUID | None
    resolved: bool
    created_at: datetime
    updated_at: datetime


@dataclass
class ConformanceSummary:
    total_records: int
    conforming_records: int
    non_conforming_records: int
    conformance_rate_percentage: float
    start_date: str
    end_date: str
