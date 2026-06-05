from dataclasses import dataclass
from datetime import datetime


@dataclass
class ConformanceRecord:
    id: str
    flight_declaration_id: str
    conformance_state: int
    timestamp: datetime
    description: str
    event_type: str
    geofence_breach: bool
    geofence_id: str | None
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
