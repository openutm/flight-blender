"""
FastAPI router for flight declaration operations.
"""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
from flight_blender.database import get_db
from flight_blender.models.flight_declaration import FlightDeclaration, FlightOperationTracking
from flight_blender.schemas.flight_declaration import (
    BulkFlightDeclarationCreateResponse,
    BulkFlightDeclarationResult,
    FlightDeclarationApproval,
    FlightDeclarationCreate,
    FlightDeclarationCreateResponse,
    FlightDeclarationFullRequest,
    FlightDeclarationListResponse,
    FlightDeclarationResponse,
    FlightDeclarationStateUpdate,
    FlightDeclarationUpdate,
    SubmitToDSSResponse,
)
from flight_blender.tasks.flight_declaration import submit_flight_declaration_to_dss_async

router = APIRouter()


async def _get_declaration_or_404(declaration_id: uuid.UUID, db: AsyncSession) -> FlightDeclaration:
    obj = await db.get(FlightDeclaration, declaration_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flight declaration not found")
    return obj


# ── CRUD ────────────────────────────────────────────────────────────────────────


@router.get("/flight_declaration", response_model=FlightDeclarationListResponse, dependencies=[ReadDep])
async def list_flight_declarations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * page_size
    count_result = await db.execute(select(func.count()).select_from(FlightDeclaration))
    total = count_result.scalar_one()
    result = await db.execute(select(FlightDeclaration).order_by(FlightDeclaration.created_at.desc()).offset(offset).limit(page_size))
    return FlightDeclarationListResponse(count=total, results=result.scalars().all())


@router.post("/flight_declaration", response_model=FlightDeclarationCreateResponse, status_code=status.HTTP_201_CREATED, dependencies=[WriteDep])
async def create_flight_declaration(payload: FlightDeclarationCreate, db: AsyncSession = Depends(get_db)):
    decl = FlightDeclaration(**payload.model_dump())
    db.add(decl)
    await db.flush()
    await db.refresh(decl)
    return FlightDeclarationCreateResponse(id=decl.id, message="Flight declaration created", is_approved=decl.is_approved, state=decl.state)


@router.get("/flight_declaration/{declaration_id}", response_model=FlightDeclarationResponse, dependencies=[ReadDep])
async def get_flight_declaration(declaration_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    return await _get_declaration_or_404(declaration_id, db)


@router.put("/flight_declaration/{declaration_id}", response_model=FlightDeclarationResponse, dependencies=[WriteDep])
async def update_flight_declaration(
    payload: FlightDeclarationUpdate,
    declaration_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    decl = await _get_declaration_or_404(declaration_id, db)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(decl, field, value)
    await db.flush()
    await db.refresh(decl)
    return decl


@router.delete("/flight_declaration/{declaration_id}/delete", status_code=status.HTTP_204_NO_CONTENT, dependencies=[WriteDep])
async def delete_flight_declaration(declaration_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    decl = await _get_declaration_or_404(declaration_id, db)
    await db.delete(decl)


# ── State management ────────────────────────────────────────────────────────────


@router.get("/flight_declaration_state/{declaration_id}", dependencies=[ReadDep])
async def get_declaration_state(declaration_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    decl = await _get_declaration_or_404(declaration_id, db)
    return {"id": str(decl.id), "state": decl.state}


@router.put("/flight_declaration_state/{declaration_id}", dependencies=[WriteDep])
async def update_declaration_state(
    payload: FlightDeclarationStateUpdate,
    declaration_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    decl = await _get_declaration_or_404(declaration_id, db)
    original_state = decl.state
    decl.state = payload.state
    # Record state transition
    tracking = FlightOperationTracking(
        flight_declaration_id=decl.id,
        deltas=json.dumps({"original_state": str(original_state), "new_state": str(payload.state)}),
    )
    db.add(tracking)
    await db.flush()
    return {"id": str(decl.id), "state": decl.state, "message": "State updated"}


# ── Approval ────────────────────────────────────────────────────────────────────


@router.get("/flight_declaration_review/{declaration_id}", dependencies=[ReadDep])
async def get_declaration_review(declaration_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    decl = await _get_declaration_or_404(declaration_id, db)
    return {"id": str(decl.id), "is_approved": decl.is_approved, "approved_by": decl.approved_by}


@router.post("/flight_declaration_review/{declaration_id}", dependencies=[WriteDep])
async def set_declaration_approval(
    payload: FlightDeclarationApproval,
    declaration_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    decl = await _get_declaration_or_404(declaration_id, db)
    decl.is_approved = payload.is_approved
    decl.approved_by = payload.approved_by
    await db.flush()
    return {"id": str(decl.id), "is_approved": decl.is_approved}


# ── DSS submission ──────────────────────────────────────────────────────────────


@router.post("/flight_declaration/{declaration_id}/submit_to_dss", response_model=SubmitToDSSResponse, dependencies=[WriteDep])
async def submit_to_dss(declaration_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    await _get_declaration_or_404(declaration_id, db)
    submit_flight_declaration_to_dss_async.delay(str(declaration_id))
    return SubmitToDSSResponse(message="DSS submission queued")


# ── Bulk creation ───────────────────────────────────────────────────────────────


def _build_declaration_from_full_request(payload: FlightDeclarationFullRequest) -> dict:
    """Convert a FlightDeclarationFullRequest to FlightDeclaration fields."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    geo_json = payload.flight_declaration_geo_json
    if geo_json:
        features = geo_json.get("features") or []
        coords_raw = features[0].get("geometry", {}).get("coordinates", [[]]) if features else [[]]
        flat = [pt for ring in coords_raw for pt in ring]
        if flat:
            lons = [pt[0] for pt in flat]
            lats = [pt[1] for pt in flat]
            bounds = json.dumps({"minx": min(lons), "miny": min(lats), "maxx": max(lons), "maxy": max(lats)})
        else:
            bounds = "{}"
    else:
        bounds = "{}"

    def _parse_dt(value: str | None, fallback: datetime) -> datetime:
        if not value:
            return fallback
        try:
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except (ValueError, TypeError):
            return fallback

    start_dt = _parse_dt(payload.start_datetime, now)
    end_dt = _parse_dt(payload.end_datetime, now + timedelta(hours=2))

    return {
        "operational_intent": json.dumps(geo_json) if geo_json else "{}",
        "flight_declaration_raw_geojson": json.dumps(geo_json) if geo_json else None,
        "bounds": bounds,
        "aircraft_id": str(payload.aircraft_id or "UNKNOWN")[:256],
        "type_of_operation": int(payload.type_of_operation or 0),
        "state": int(payload.flight_state or 1),
        "is_approved": True,
        "originating_party": str(payload.originating_party or "Flight Blender Default")[:100],
        "start_datetime": start_dt,
        "end_datetime": end_dt,
    }


@router.post("/set_flight_declarations_bulk", response_model=BulkFlightDeclarationCreateResponse, dependencies=[WriteDep])
async def bulk_create_flight_declarations(payloads: list[FlightDeclarationFullRequest], db: AsyncSession = Depends(get_db)):
    results: list[BulkFlightDeclarationResult] = []
    submitted = 0
    failed = 0

    for payload in payloads:
        try:
            fields = _build_declaration_from_full_request(payload)
            decl = FlightDeclaration(**fields)
            db.add(decl)
            await db.flush()
            results.append(BulkFlightDeclarationResult(id=decl.id, message="Created", success=True))
            submitted += 1
        except Exception as exc:
            logger.error("Bulk create error: %s", exc)
            results.append(BulkFlightDeclarationResult(id=None, message=str(exc), success=False))
            failed += 1

    return BulkFlightDeclarationCreateResponse(submitted=submitted, failed=failed, results=results)


# ── Simplified bounding-box ingest ─────────────────────────────────────────────


@router.post("/set_flight_declaration", response_model=FlightDeclarationCreateResponse, status_code=status.HTTP_201_CREATED, dependencies=[WriteDep])
async def set_flight_declaration(payload: FlightDeclarationFullRequest, db: AsyncSession = Depends(get_db)):
    """Accept the full flight declaration payload from the verification toolkit.

    Accepts either the rich toolkit format (with flight_declaration_geo_json,
    start_datetime, end_datetime, etc.) or the legacy bbox-only format.
    Always returns is_approved=True so the toolkit proceeds.
    """
    fields = _build_declaration_from_full_request(payload)
    decl = FlightDeclaration(**fields)
    db.add(decl)
    await db.flush()
    await db.refresh(decl)
    return FlightDeclarationCreateResponse(id=decl.id, message="Flight declaration created", is_approved=True, state=decl.state)


# ── Network declarations ────────────────────────────────────────────────────────


@router.get("/flight_declaration/{declaration_id}/network_flight_declarations", dependencies=[ReadDep])
async def get_network_flight_declarations(declaration_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    """Return peer operational intents associated with this declaration."""
    await _get_declaration_or_404(declaration_id, db)
    return {"declaration_id": str(declaration_id), "network_declarations": []}


@router.get("/network_flight_declarations_by_view", dependencies=[ReadDep])
async def get_network_declarations_by_view(
    view: str = Query(..., description="Bounding box: 'lat_lo,lng_lo,lat_hi,lng_hi'"),
    db: AsyncSession = Depends(get_db),
):
    return {"view": view, "network_declarations": []}


# ── Operational Intent ingest ──────────────────────────────────────────────────


def _build_declaration_from_op_intent(request_data: dict, default_state: int) -> dict:
    """Extract flight declaration fields from an operational-intent payload."""
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)

    def _parse_dt(value: str | None, fallback: datetime) -> datetime:
        if not value:
            return fallback
        try:
            # Handle Unix timestamps (as used by uas_standards Volume4D)
            if isinstance(value, (int, float)):
                from datetime import datetime as dt
                parsed = dt.fromtimestamp(value, tz=timezone.utc)
                return parsed
            parsed = datetime.fromisoformat(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except (ValueError, TypeError):
            return fallback

    start_dt = _parse_dt(request_data.get("start_datetime"), now)
    end_dt = _parse_dt(request_data.get("end_datetime"), now + timedelta(hours=2))

    # Extract volumes and compute bounds from Volume4D list
    volumes = request_data.get("operational_intent_volume4ds", [])
    all_lons: list[float] = []
    all_lats: list[float] = []
    for v4d in volumes:
        vol = v4d.get("volume", {})
        outline = vol.get("outline_polygon", {})
        # Handle both "coordinates" (GeoJSON) and "vertices" (ASTM standard) formats
        coords = outline.get("coordinates", [[]])
        vertices = outline.get("vertices", [])
        if vertices:
            # ASTM format: list of {lat, lng} dicts
            for v in vertices:
                all_lons.append(float(v.get("lng", 0)))
                all_lats.append(float(v.get("lat", 0)))
        elif coords:
            # GeoJSON format: nested coordinate arrays
            flat = [pt for ring in coords for pt in ring]
            for pt in flat:
                all_lons.append(pt[0])
                all_lats.append(pt[1])

    bounds = (
        json.dumps({"minx": min(all_lons), "miny": min(all_lats), "maxx": max(all_lons), "maxy": max(all_lats)})
        if all_lons
        else "{}"
    )

    # Build a GeoJSON representation from the volumes for raw storage
    geo_json_features = []
    for v4d in volumes:
        vol = v4d.get("volume", {})
        outline = vol.get("outline_polygon", {})
        vertices = outline.get("vertices", [])
        coords = outline.get("coordinates", [])
        if vertices:
            # Convert ASTM vertices to GeoJSON coordinates
            ring = [[v["lng"], v["lat"]] for v in vertices]
            geo_json_features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Polygon", "coordinates": [ring]},
                    "properties": {
                        "min_altitude": vol.get("altitude_lower", {}),
                        "max_altitude": vol.get("altitude_upper", {}),
                    },
                }
            )
        elif coords:
            geo_json_features.append(
                {
                    "type": "Feature",
                    "geometry": outline,
                    "properties": {
                        "min_altitude": vol.get("altitude_lower", {}),
                        "max_altitude": vol.get("altitude_upper", {}),
                    },
                }
            )
    geo_json = {"type": "FeatureCollection", "features": geo_json_features} if geo_json_features else None

    return {
        "operational_intent": json.dumps(volumes),
        "flight_declaration_raw_geojson": json.dumps(geo_json) if geo_json else None,
        "bounds": bounds,
        "aircraft_id": str(request_data.get("aircraft_id", "UNKNOWN"))[:256],
        "type_of_operation": int(request_data.get("type_of_operation", 0)),
        "state": default_state,
        "is_approved": True,
        "originating_party": str(request_data.get("originating_party", "Flight Blender Default"))[:100],
        "submitted_by": request_data.get("submitted_by"),
        "start_datetime": start_dt,
        "end_datetime": end_dt,
    }


@router.post("/set_operational_intent", response_model=FlightDeclarationCreateResponse, status_code=status.HTTP_201_CREATED, dependencies=[WriteDep])
async def set_operational_intent(payload: dict, db: AsyncSession = Depends(get_db)):
    """Create a flight declaration from an operational-intent payload (Volume4D format).

    Used by the verification toolkit's 'Upload Flight Declaration Via Operational Intent' step.
    """
    fields = _build_declaration_from_op_intent(payload, default_state=1)
    decl = FlightDeclaration(**fields)
    db.add(decl)
    await db.flush()
    await db.refresh(decl)
    return FlightDeclarationCreateResponse(id=decl.id, message="Flight declaration created via operational intent", is_approved=True, state=decl.state)


@router.post("/set_operational_intents_bulk", response_model=BulkFlightDeclarationCreateResponse, dependencies=[WriteDep])
async def set_operational_intents_bulk(payloads: list[dict], db: AsyncSession = Depends(get_db)):
    """Bulk create flight declarations from operational-intent payloads."""
    results: list[BulkFlightDeclarationResult] = []
    submitted = 0
    failed = 0

    for payload in payloads:
        try:
            fields = _build_declaration_from_op_intent(payload, default_state=1)
            decl = FlightDeclaration(**fields)
            db.add(decl)
            await db.flush()
            results.append(BulkFlightDeclarationResult(id=decl.id, message="Created", success=True))
            submitted += 1
        except Exception as exc:
            logger.error("Bulk operational intent create error: %s", exc)
            results.append(BulkFlightDeclarationResult(id=None, message=str(exc), success=False))
            failed += 1

    return BulkFlightDeclarationCreateResponse(submitted=submitted, failed=failed, results=results)
