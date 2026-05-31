"""
FastAPI router for USS interoperability operations.
"""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, RIDDisplayProviderDep, WriteDep
from flight_blender.common.redis_stream_operations import read_all_observations
from flight_blender.database import get_db
from flight_blender.schemas.uss import (
    ConstraintDetailsResponse,
    OperationalIntentDetailsResponse,
    OperationalIntentDetailsUpdate,
    TelemetryUpdate,
    USSFlightDetailResponse,
    USSFlightResponse,
    USSReportCreate,
)

router = APIRouter()


# ── Reports ────────────────────────────────────────────────────────────────────


@router.post("/v1/reports", status_code=status.HTTP_201_CREATED, dependencies=[WriteDep])
async def submit_uss_report(payload: USSReportCreate):
    return {"message": "Report received"}


# ── Operational Intents ────────────────────────────────────────────────────────


@router.get("/v1/operational_intents/{intent_id}", response_model=OperationalIntentDetailsResponse, dependencies=[ReadDep])
async def get_operational_intent(intent_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    from flight_blender.models.flight_declaration import FlightDeclaration

    obj = await db.get(FlightDeclaration, intent_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operational intent not found")
    return OperationalIntentDetailsResponse(operational_intent_id=intent_id, details={"state": obj.state})


@router.put("/v1/operational_intents/{intent_id}", response_model=OperationalIntentDetailsResponse, dependencies=[WriteDep])
async def update_operational_intent(
    payload: OperationalIntentDetailsUpdate,
    intent_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    from flight_blender.models.flight_declaration import FlightDeclaration

    obj = await db.get(FlightDeclaration, intent_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Operational intent not found")
    return OperationalIntentDetailsResponse(operational_intent_id=intent_id, details=payload.operational_intent)


@router.post("/v1/operational_intents/{intent_id}/telemetry", status_code=status.HTTP_204_NO_CONTENT, dependencies=[WriteDep])
async def submit_operational_intent_telemetry(payload: TelemetryUpdate, intent_id: uuid.UUID = Path(...)):
    pass


@router.post("/v1/operational_intents", status_code=status.HTTP_200_OK, dependencies=[WriteDep])
async def notify_operational_intent_change(payload: OperationalIntentDetailsUpdate):
    return {"message": "Operational intent change acknowledged"}


# ── Constraints ────────────────────────────────────────────────────────────────


@router.get("/v1/constraints/{constraint_id}", response_model=ConstraintDetailsResponse, dependencies=[ReadDep])
async def get_constraint(constraint_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    from flight_blender.models.constraint import ConstraintReference

    obj = await db.get(ConstraintReference, constraint_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Constraint not found")
    return ConstraintDetailsResponse(constraint_id=constraint_id, details={"uss_availability": obj.uss_availability})


@router.put("/v1/constraints/{constraint_id}", response_model=ConstraintDetailsResponse, dependencies=[WriteDep])
async def update_constraint(constraint_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    return ConstraintDetailsResponse(constraint_id=constraint_id, details={})


@router.post("/v1/constraints", status_code=status.HTTP_200_OK, dependencies=[WriteDep])
async def notify_constraint_change(payload: dict):
    return {"message": "Constraint change acknowledged"}


# ── Peer-USS Remote-ID exchange (ASTM F3411) ────────────────────────────────────
#
# These USS-to-USS RID endpoints are presented by OTHER USSs (RID display
# providers) and therefore require the RID-specific ``rid.display_provider``
# scope rather than the generic blender read scope. Django served them at
# ``/uss/flights`` and ``/uss/flights/<flight_id>/details``.


@router.get("/flights", response_model=USSFlightResponse, dependencies=[RIDDisplayProviderDep])
async def get_uss_flights(
    view: str | None = Query(None, description="Bounding box: 'lat_lo,lng_lo,lat_hi,lng_hi'"),
):
    """Return RID flights visible within *view* (ASTM F3411 ``getFlights``).

    Mirrors Django ``get_uss_flights``: when a *view* is supplied it is
    validated, then the most recent telemetry observations are read from the
    live stream. Returns the ASTM ``GetFlightsResponse`` shape
    ``{"timestamp": ..., "flights": [...]}``.
    """
    from datetime import datetime, timezone

    if view is not None:
        try:
            view_port = [float(v) for v in view.split(",")]
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A view bbox is necessary with four values: minx, miny, maxx and maxy",
            ) from exc
        if len(view_port) != 4:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="The requested view rectangle is not valid format: lat1,lng1,lat2,lng2",
            )

    try:
        observations = read_all_observations() or []
    except Exception as exc:  # pragma: no cover - stream unavailable -> no flights
        logger.warning("RID flight stream unavailable: {}", exc)
        observations = []

    flights: list[dict] = []
    for obs in observations:
        metadata = obs.get("metadata") if isinstance(obs, dict) else None
        if isinstance(metadata, dict) and "telemetry" in metadata:
            flights.append(
                {
                    "id": metadata.get("injection_id") or obs.get("icao_address", ""),
                    "aircraft_type": metadata.get("aircraft_type"),
                    "current_state": metadata.get("telemetry"),
                    "simulated": True,
                    "recent_positions": [],
                }
            )

    return USSFlightResponse(timestamp={"value": datetime.now(timezone.utc).isoformat(), "format": "RFC3339"}, flights=flights)


@router.get("/flights/{flight_id}/details", response_model=USSFlightDetailResponse, dependencies=[RIDDisplayProviderDep])
async def get_uss_flight_details(flight_id: str = Path(...), db: AsyncSession = Depends(get_db)):
    """Return operator details for a RID flight (ASTM F3411 ``getFlightDetails``).

    Mirrors Django ``get_uss_flight_details``: looks up the persisted
    ``RIDFlightDetail`` by id and returns the ASTM ``{"details": {...}}`` shape,
    or 404 when the flight is unknown.
    """
    from flight_blender.models.rid import RIDFlightDetail

    try:
        detail_pk = uuid.UUID(flight_id)
    except (ValueError, AttributeError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The requested flight could not be found") from None

    obj = await db.get(RIDFlightDetail, detail_pk)
    if obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="The requested flight could not be found")

    def _maybe_json(raw):
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return raw

    details = {
        "id": str(obj.id),
        "operator_id": obj.operator_id,
        "operator_location": _maybe_json(obj.operator_location),
        "operation_description": obj.operation_description,
        "auth_data": _maybe_json(obj.auth_data),
        "uas_id": _maybe_json(obj.uas_id),
        "eu_classification": _maybe_json(obj.eu_classification),
    }
    details = {k: v for k, v in details.items() if v is not None}
    return USSFlightDetailResponse(details=details)
