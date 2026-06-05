from dataclasses import asdict
from typing import Any

import arrow
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.api.dependencies import require_scopes
from flight_blender.db.session import async_get_db
from flight_blender.domain_types.common import FLIGHTBLENDER_READ_SCOPE
from flight_blender.repositories.conformance_repo import SQLAlchemyConformanceRepository
from flight_blender.services.conformance_svc import ConformanceOperations

router = APIRouter(prefix="/conformance_monitoring_ops")


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


async def _ops(db: AsyncSession = Depends(async_get_db)) -> ConformanceOperations:
    return ConformanceOperations(repo=SQLAlchemyConformanceRepository(db))


@router.get("/conformance_status")
async def conformance_status(_auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE]))):
    return {"status": "OK"}


@router.get("/get_conformance_records")
async def get_conformance_records(
    start_date: str | None = None,
    end_date: str | None = None,
    ops: ConformanceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    dates, error = _parse_dates(start_date, end_date)
    if error:
        return error
    start, end = dates
    return {"conformance_records": await ops.get_records(start_time=start, end_time=end)}


@router.get("/conformance_record_summary")
@router.get("/get_conformance_record_summary")
async def get_conformance_record_summary(
    start_date: str | None = None,
    end_date: str | None = None,
    ops: ConformanceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    dates, error = _parse_dates(start_date, end_date)
    if error:
        return error
    start, end = dates
    summary = await ops.get_summary(
        start_time=start,
        end_time=end,
        start_date=start_date or "",
        end_date=end_date or "",
    )
    return {"summary": asdict(summary)}
