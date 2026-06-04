from dataclasses import asdict
from typing import Any

import arrow
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse

from flight_blender.api.dependencies import require_scopes
from flight_blender.common.data_definitions import FLIGHTBLENDER_READ_SCOPE
from flight_blender.conformance.data_definitions import ConformanceSummary
from flight_blender.infrastructure.database.repositories.django_conformance import DjangoConformanceRepository

router = APIRouter()


def _serialize_record(record: Any) -> dict[str, Any]:
    return {
        "id": str(record.id),
        "flight_declaration_id": str(record.flight_declaration_id),
        "conformance_state": record.conformance_state,
        "timestamp": record.timestamp,
        "description": record.description,
        "event_type": record.event_type,
        "geofence_breach": record.geofence_breach,
        "geofence_id": str(record.geofence_id) if record.geofence_id else None,
        "resolved": record.resolved,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
    }


def _parse_dates(start_date: str | None, end_date: str | None):
    if not start_date or not end_date:
        return None, JSONResponse({"error": "start_date and end_date are required"}, status_code=400)
    try:
        start = arrow.get(start_date).datetime
        end = arrow.get(end_date).datetime
    except arrow.parser.ParserError:
        return None, JSONResponse({"error": "Invalid date format. Use ISO 8601 format."}, status_code=400)
    if start >= end:
        return None, JSONResponse({"error": "start_date must be before end_date"}, status_code=400)
    return (start, end), None


@router.get("/conformance_status")
async def conformance_status(_auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE]))):
    return {"status": "OK"}


@router.get("/get_conformance_records")
async def get_conformance_records(
    start_date: str | None = None,
    end_date: str | None = None,
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    dates, error = _parse_dates(start_date, end_date)
    if error:
        return error
    start, end = dates
    records = DjangoConformanceRepository().get_conformance_records_for_duration(start_time=start, end_time=end) or []
    return {"conformance_records": [_serialize_record(record) for record in records]}


@router.get("/conformance_record_summary")
@router.get("/get_conformance_record_summary")
async def get_conformance_record_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    dates, error = _parse_dates(start_date, end_date)
    if error:
        return error
    start, end = dates
    records = list(DjangoConformanceRepository().get_conformance_records_for_duration(start_time=start, end_time=end) or [])
    total_records = len(records)
    conforming_records = sum(1 for record in records if record.conformance_state == 1)
    non_conforming_records = total_records - conforming_records
    conformance_rate = (conforming_records / total_records * 100) if total_records else 0
    summary = ConformanceSummary(
        total_records=total_records,
        conforming_records=conforming_records,
        non_conforming_records=non_conforming_records,
        conformance_rate_percentage=conformance_rate,
        start_date=start_date,
        end_date=end_date,
    )
    return {"summary": asdict(summary)}
