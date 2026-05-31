"""
Pydantic schemas for conformance monitoring operations.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel


class ConformanceRecordResponse(BaseModel):
    id: uuid.UUID
    flight_declaration_id: uuid.UUID | None
    conformance_state: int
    description: str
    event_type: str
    geofence_breach: bool
    resolved: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ConformanceSummaryResponse(BaseModel):
    total_records: int
    conforming_records: int
    non_conforming_records: int
    conformance_rate_percent: float
    start_date: datetime | None
    end_date: datetime | None


class ConformanceStatusResponse(BaseModel):
    is_conforming: bool
    active_nonconforming_count: int
    last_checked: datetime | None
