"""FastAPI router for Detect and Avoid (DAA) operations (ASTM F3442)."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep
from flight_blender.database import get_db
from flight_blender.models.daa import DAAAlert, DAAIncidentLog

router = APIRouter()


def _parse_iso_date(value: str, field: str) -> datetime:
    """Parse an ISO-8601 datetime filter, returning 422 on malformed input.

    The original port silently swallowed ``ValueError`` and dropped the filter,
    so a client passing a bad date got unfiltered results with no error signal.
    """
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Invalid ISO-8601 datetime for {field}: {value!r}",
        ) from exc


def _filter_incident_query(
    query: Select,
    event_type: str | None,
    alert_level: int | None,
    alert_id: str | None,
    start_date: str | None,
    end_date: str | None,
) -> Select:
    """Apply optional server-side filters to a DAAIncidentLog query."""
    if event_type:
        query = query.where(DAAIncidentLog.event_type == event_type)
    if alert_level is not None:
        query = query.where(DAAIncidentLog.alert_level == alert_level)
    if alert_id:
        query = query.where(DAAIncidentLog.alert_id == alert_id)
    if start_date:
        query = query.where(DAAIncidentLog.created_at >= _parse_iso_date(start_date, "start_date"))
    if end_date:
        query = query.where(DAAIncidentLog.created_at <= _parse_iso_date(end_date, "end_date"))
    return query


@router.get("/alerts/active/", dependencies=[ReadDep])
async def get_active_daa_alerts(db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    """Return all currently active DAA alerts."""
    result = await db.execute(
        select(DAAAlert).where(DAAAlert.is_active == True).order_by(DAAAlert.created_at.desc()).limit(100)  # noqa: E712
    )
    alerts = result.scalars().all()
    return [
        {
            "id": str(a.id),
            "ownship_declaration_id": str(a.ownship_declaration_id) if a.ownship_declaration_id else None,
            "intruder_icao_address": a.intruder_icao_address,
            "alert_level": a.alert_level,
            "alert_type": a.alert_type,
            "geometry": a.geometry,
            "initial_cpa_seconds": a.initial_cpa_seconds,
            "closest_range_m": a.closest_range_m,
            "is_active": a.is_active,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in alerts
    ]


@router.get("/logs/incident/", dependencies=[ReadDep])
async def get_daa_incident_logs(
    event_type: str | None = Query(default=None),
    alert_level: int | None = Query(default=None),
    start_date: str | None = Query(default=None),
    end_date: str | None = Query(default=None),
    alert_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    """Return DAA incident logs with optional server-side filtering."""
    base = select(DAAIncidentLog).order_by(DAAIncidentLog.created_at.desc()).limit(500)
    query = _filter_incident_query(base, event_type, alert_level, alert_id, start_date, end_date)
    logs = (await db.execute(query)).scalars().all()
    return [
        {
            "id": str(log.id),
            "alert_id": str(log.alert_id) if log.alert_id else None,
            "ownship_declaration_id": str(log.ownship_declaration_id) if log.ownship_declaration_id else None,
            "intruder_icao_address": log.intruder_icao_address,
            "event_type": log.event_type,
            "alert_level": log.alert_level,
            "geometry": log.geometry,
            "range_m": log.range_m,
            "bearing_deg": log.bearing_deg,
            "cpa_seconds": log.cpa_seconds,
            "altitude_diff_m": log.altitude_diff_m,
            "timestamp": log.timestamp.isoformat() if log.timestamp else None,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        }
        for log in logs
    ]
