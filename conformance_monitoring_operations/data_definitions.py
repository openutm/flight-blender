from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from uuid import UUID

from shapely.geometry import Polygon


@dataclass
class PolygonAltitude:
    polygon: Polygon
    altitude_upper: float
    altitude_lower: float


@dataclass
class ConformanceRecord:
    id: str
    flight_declaration_id: str
    conformance_state: int
    timestamp: datetime
    description: str
    event_type: str
    geofence_breach: bool
    geofence_id: Optional[str]
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
