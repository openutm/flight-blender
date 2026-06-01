"""
FastAPI router for SCD (Strategic Conflict Detection) operations.
"""

import json
import uuid
from datetime import datetime, timezone
from enum import StrEnum

from fastapi import APIRouter, Depends, HTTPException, Path, status
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
from flight_blender.common.enums import OperationState, VALID_OPERATIONAL_INTENT_STATES
from flight_blender.common.plugin_loader import load_plugin
from flight_blender.config import get_settings
from flight_blender.database import get_db
from flight_blender.schemas.scd import (
    ClearAreaRequest,
    ClearAreaResponse,
    FlightPlanResponse,
    FlightPlanUpsertRequest,
    SCDCapabilitiesResponse,
    SCDStatusResponse,
)
from flight_blender.services.deconfliction import DeconflictionEngine, DeconflictionRequest

router = APIRouter()

# Active operational states whose declarations must be deconflicted against.
_ACTIVE_STATES = list(VALID_OPERATIONAL_INTENT_STATES)


class PlanningResult(StrEnum):
    """ASTM F3548-21 flight-planning result statuses (Django SCD parity)."""

    PLANNED = "Planned"
    CONFLICT = "ConflictWithFlight"
    NOT_PLANNED = "NotPlanned"
    FAILED = "Failed"


class UsageState(StrEnum):
    """ASTM operational-intent usage states that trigger strategic deconfliction."""

    PLANNED = "Planned"
    IN_USE = "InUse"


# usage_state values that trigger strategic deconfliction (Django Planned/InUse).
_PLANNING_USAGE_STATES = {UsageState.PLANNED, UsageState.IN_USE}


def _altitude_value(alt: dict | None) -> float | None:
    if not isinstance(alt, dict):
        return None
    for key in ("meters", "value", "altitude"):
        if key in alt:
            try:
                return float(alt[key])
            except (TypeError, ValueError):
                return None
    return None


def _parse_dt(value: object) -> datetime | None:
    """Parse a datetime from an ISO string, optionally unwrapping ``{"value": ...}`` dicts."""
    if isinstance(value, dict):
        value = value.get("value")
    from flight_blender.common.datetime_utils import parse_iso_utc

    return parse_iso_utc(value)


def _candidate_volume_from_astm_volume(v4d: dict) -> dict | None:
    """Extract a 4D volume (coordinates + altitude + time) from a single ASTM
    Volume4D dict (``{volume: {outline_polygon, altitude_lower/upper}, time_start, time_end}``)."""
    volume = v4d.get("volume", v4d)
    outline = volume.get("outline_polygon", {})
    vertices = outline.get("vertices", [])
    coords: list[list[float]] = []
    if vertices:
        coords = [[float(v.get("lng", 0)), float(v.get("lat", 0))] for v in vertices]
    else:
        ring = outline.get("coordinates", [[]])
        ring = ring[0] if ring else []
        coords = [[float(pt[0]), float(pt[1])] for pt in ring]
    if not coords:
        return None
    min_alt = _altitude_value(volume.get("altitude_lower"))
    max_alt = _altitude_value(volume.get("altitude_upper"))
    return {
        "coordinates": coords,
        "min_alt": min_alt if min_alt is not None else float("-inf"),
        "max_alt": max_alt if max_alt is not None else float("inf"),
        "start": _parse_dt(v4d.get("time_start")),
        "end": _parse_dt(v4d.get("time_end")),
    }


def _candidate_volumes_from_intended_flight(intended_flight: dict) -> list[dict]:
    """Extract candidate 4D volumes from an ASTM ``intended_flight`` payload."""
    op_intent = intended_flight.get("operational_intent", {}) or {}
    volumes = (op_intent.get("volumes") or []) + (op_intent.get("off_nominal_volumes") or [])
    candidates = []
    for v4d in volumes:
        cand = _candidate_volume_from_astm_volume(v4d)
        if cand is not None:
            candidates.append(cand)
    return candidates


def _volume_from_geojson(geo_json: dict | None, start: datetime, end: datetime) -> dict | None:
    if not geo_json:
        return None
    features = geo_json.get("features") or []
    if not features:
        return None
    feature = features[0]
    coords_raw = feature.get("geometry", {}).get("coordinates", [[]])
    ring = coords_raw[0] if coords_raw else []
    if not ring:
        return None
    props = feature.get("properties", {}) or {}
    min_alt = _altitude_value(props.get("min_altitude"))
    max_alt = _altitude_value(props.get("max_altitude"))
    return {
        "coordinates": [[float(pt[0]), float(pt[1])] for pt in ring],
        "min_alt": min_alt if min_alt is not None else float("-inf"),
        "max_alt": max_alt if max_alt is not None else float("inf"),
        "start": start,
        "end": end,
    }


async def _existing_volumes(db: AsyncSession) -> list[dict]:
    """Build 4D volumes for all active declarations to deconflict against."""
    from flight_blender.models.flight_declaration import FlightDeclaration

    decl_result = await db.execute(select(FlightDeclaration).where(FlightDeclaration.state.in_(_ACTIVE_STATES)))
    declarations = decl_result.scalars().all()
    volumes: list[dict] = []
    for d in declarations:
        d_start = d.start_datetime if d.start_datetime.tzinfo else d.start_datetime.replace(tzinfo=timezone.utc)
        d_end = d.end_datetime if d.end_datetime.tzinfo else d.end_datetime.replace(tzinfo=timezone.utc)
        d_geo = json.loads(d.flight_declaration_raw_geojson) if d.flight_declaration_raw_geojson else None
        d_vol = _volume_from_geojson(d_geo, d_start, d_end)
        if d_vol is not None:
            volumes.append(d_vol)
    return volumes


def _resolve_usage_state(payload: FlightPlanUpsertRequest, intended: dict) -> str:
    """Return the effective usage_state, preferring a nested basic_information one."""
    basic = intended.get("basic_information", {}) or {}
    return basic.get("usage_state") or payload.usage_state


def _conflicts_with_existing(candidates: list[dict], existing: list[dict], ussp_network_enabled: int) -> bool:
    """Return True if any candidate volume conflicts with an existing one."""
    engine_cls = load_plugin(get_settings().plugin_deconfliction_engine, expected_protocol=DeconflictionEngine)
    engine = engine_cls()
    for candidate in candidates:
        req = DeconflictionRequest(
            candidate_volume=candidate,
            prefetched_volumes=existing,
            ussp_network_enabled=ussp_network_enabled,
        )
        if not engine.check_deconfliction(req).is_approved:
            return True
    return False


async def _strategic_planning_result(payload: FlightPlanUpsertRequest, db: AsyncSession) -> str:
    """Compute an ASTM planning result by running strategic deconfliction
    against active declarations in the DB.

    Mirrors Django ``upsert_close_flight_plan``: only Planned/InUse usage states
    with at least one candidate volume run deconfliction. Fails closed: any
    error yields ``Failed`` rather than an optimistic ``Planned``.
    """
    settings = get_settings()
    try:
        intended = payload.intended_flight or {}
        if _resolve_usage_state(payload, intended) not in _PLANNING_USAGE_STATES:
            return PlanningResult.NOT_PLANNED

        candidates = _candidate_volumes_from_intended_flight(intended)
        if not candidates:
            # Nothing concrete to plan -> Django returns NotPlanned for an empty op-intent.
            return PlanningResult.NOT_PLANNED

        existing = await _existing_volumes(db)
        if _conflicts_with_existing(candidates, existing, int(settings.ussp_network_enabled)):
            return PlanningResult.CONFLICT
        return PlanningResult.PLANNED
    except Exception as exc:
        logger.error("SCD strategic deconfliction error; failing closed: {}", exc)
        return PlanningResult.FAILED


# ── SCD v1 ─────────────────────────────────────────────────────────────────────


@router.get("/v1/status", response_model=SCDStatusResponse, dependencies=[ReadDep])
async def scd_status():
    return SCDStatusResponse(status="operational")


@router.get("/v1/capabilities", response_model=SCDCapabilitiesResponse, dependencies=[ReadDep])
async def scd_capabilities():
    return SCDCapabilitiesResponse(capabilities=["BasicStrategicConflictDetection", "HighPriorityFlights"])


# ── Flight planning ─────────────────────────────────────────────────────────────


async def _upsert_flight_plan_impl(
    payload: FlightPlanUpsertRequest,
    flight_plan_id: uuid.UUID,
    db: AsyncSession,
) -> FlightPlanResponse:
    settings = get_settings()
    if settings.ussp_network_enabled:
        raise HTTPException(status_code=503, detail="DSS integration not yet implemented in FastAPI port")
    planning_result = await _strategic_planning_result(payload, db)
    return FlightPlanResponse(
        flight_plan_id=flight_plan_id, planning_result=planning_result, notes="Local strategic deconfliction (USSP network disabled)"
    )


def _clear_area_impl() -> ClearAreaResponse:
    settings = get_settings()
    if settings.ussp_network_enabled:
        raise HTTPException(status_code=503, detail="DSS integration not yet implemented in FastAPI port")
    return ClearAreaResponse(outcome={"success": False, "message": "USSP network not enabled"})


@router.put("/flight_planning/flight_plans/{flight_plan_id}", response_model=FlightPlanResponse, dependencies=[WriteDep])
async def upsert_flight_plan(
    payload: FlightPlanUpsertRequest,
    flight_plan_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    return await _upsert_flight_plan_impl(payload, flight_plan_id, db)


@router.delete("/flight_planning/flight_plans/{flight_plan_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[WriteDep])
async def delete_flight_plan(flight_plan_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    pass


@router.post("/flight_planning/clear_area_requests", response_model=ClearAreaResponse, dependencies=[WriteDep])
async def clear_area(payload: ClearAreaRequest):
    return _clear_area_impl()


@router.get("/flight_planning/status", response_model=SCDStatusResponse, dependencies=[ReadDep])
async def flight_planning_status():
    return SCDStatusResponse(status="operational")


# ── U-Space variants ────────────────────────────────────────────────────────────


@router.put("/flight_planning/u_space/flight_plans/{flight_plan_id}", response_model=FlightPlanResponse, dependencies=[WriteDep])
async def upsert_uspace_flight_plan(
    payload: FlightPlanUpsertRequest,
    flight_plan_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    return await _upsert_flight_plan_impl(payload, flight_plan_id, db)


@router.delete("/flight_planning/u_space/flight_plans/{flight_plan_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[WriteDep])
async def delete_uspace_flight_plan(flight_plan_id: uuid.UUID = Path(...)):
    pass


@router.post("/flight_planning/u_space/clear_area_requests", response_model=ClearAreaResponse, dependencies=[WriteDep])
async def clear_uspace_area(payload: ClearAreaRequest):
    return _clear_area_impl()


@router.get("/flight_planning/u_space/status", response_model=SCDStatusResponse, dependencies=[ReadDep])
async def uspace_status():
    return SCDStatusResponse(status="operational")
