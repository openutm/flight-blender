"""
FastAPI router for flight declaration operations.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
from flight_blender.common.enums import OperationState
from flight_blender.common.plugin_loader import load_plugin
from flight_blender.config import get_settings
from flight_blender.database import get_db
from flight_blender.models.flight_declaration import FlightDeclaration, FlightOperationTracking
from flight_blender.models.geo_fence import GeoFence
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
    OperationalIntentIngestRequest,
    SubmitToDSSResponse,
)
from flight_blender.services.deconfliction import DeconflictionEngine, DeconflictionRequest
from flight_blender.tasks.flight_declaration import submit_flight_declaration_to_dss_async

router = APIRouter()


# ── Shared helpers ────────────────────────────────────────────────────────────


def _parse_utc_dt(value: str | int | float | None, fallback: datetime) -> datetime:
    """Parse a datetime from an ISO string or Unix timestamp, defaulting to *fallback*."""
    if value is None:
        return fallback
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        parsed = datetime.fromisoformat(str(value))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return fallback


def _safe_int(value: object, default: int) -> int:
    """Convert *value* to int, returning *default* on failure."""
    try:
        return int(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return default


def _bounds_from_flat_coords(flat: list[list[float]]) -> str:
    """Return a JSON bounds string from a flat list of [lon, lat] pairs."""
    if not flat:
        return "{}"
    lons = [pt[0] for pt in flat]
    lats = [pt[1] for pt in flat]
    return json.dumps({"minx": min(lons), "miny": min(lats), "maxx": max(lons), "maxy": max(lats)})


def _bounds_from_geojson(geo_json: dict | None) -> str:
    """Extract a bounds string from a GeoJSON FeatureCollection."""
    if not geo_json:
        return "{}"
    features = geo_json.get("features") or []
    if not features:
        return "{}"
    coords_raw = features[0].get("geometry", {}).get("coordinates", [[]])
    flat = [pt for ring in coords_raw for pt in ring]
    return _bounds_from_flat_coords(flat)


def _bounds_from_vertices(vertices: list[dict]) -> tuple[list[float], list[float]]:
    """Return (lons, lats) lists from ASTM-format {lat, lng} vertices."""
    lons = [float(v.get("lng", 0)) for v in vertices]
    lats = [float(v.get("lat", 0)) for v in vertices]
    return lons, lats


def _bounds_from_volumes(volumes: list[dict]) -> str:
    """Compute a bounds string from a list of Volume4D dicts."""
    all_lons: list[float] = []
    all_lats: list[float] = []
    for v4d in volumes:
        outline = v4d.get("volume", {}).get("outline_polygon", {})
        vertices = outline.get("vertices", [])
        coords = outline.get("coordinates", [[]])
        if vertices:
            lons, lats = _bounds_from_vertices(vertices)
            all_lons.extend(lons)
            all_lats.extend(lats)
        elif coords:
            flat = [pt for ring in coords for pt in ring]
            all_lons.extend(pt[0] for pt in flat)
            all_lats.extend(pt[1] for pt in flat)
    return _bounds_from_flat_coords([[lon, lat] for lon, lat in zip(all_lons, all_lats)])


def _geo_features_from_volumes(volumes: list[dict]) -> list[dict]:
    """Build a GeoJSON FeatureCollection features list from Volume4D dicts."""
    features = []
    for v4d in volumes:
        vol = v4d.get("volume", {})
        outline = vol.get("outline_polygon", {})
        vertices = outline.get("vertices", [])
        coords = outline.get("coordinates", [])
        props = {
            "min_altitude": vol.get("altitude_lower", {}),
            "max_altitude": vol.get("altitude_upper", {}),
        }
        if vertices:
            ring = [[v["lng"], v["lat"]] for v in vertices]
            features.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]}, "properties": props})
        elif coords:
            features.append({"type": "Feature", "geometry": outline, "properties": props})
    return features


def _view_box_from_bounds(bounds_json: str) -> list[float]:
    try:
        b = json.loads(bounds_json)
        return [b["minx"], b["miny"], b["maxx"], b["maxy"]]
    except (json.JSONDecodeError, KeyError, TypeError):
        return []


_ACTIVE_STATES = [1, 2, 3, 4]  # Accepted, Activated, NonConforming, Contingent


async def _run_deconfliction(
    geo_json: dict | None,
    start_datetime: datetime,
    end_datetime: datetime,
    db: AsyncSession,
    bounds: str = "{}",
    type_of_operation: int = 0,
    exclude_id: uuid.UUID | None = None,
) -> tuple[bool, int]:
    """Pre-fetch spatial data from DB then run the configured deconfliction engine.

    Strategic deconfliction is safety critical and *fails closed*: any error
    building inputs or running the engine results in the operation NOT being
    approved (state Rejected), rather than being silently accepted.
    """
    settings = get_settings()
    try:
        # Pre-fetch active geofences overlapping the time window
        fence_result = await db.execute(
            select(GeoFence).where(
                GeoFence.status == 1,
                GeoFence.start_datetime <= end_datetime,
                GeoFence.end_datetime >= start_datetime,
            )
        )
        fences = fence_result.scalars().all()
        prefetched_fences = [{"id": str(f.id), "bounds": f.bounds} for f in fences]

        # Pre-fetch active flight declarations overlapping the time window
        decl_query = select(FlightDeclaration).where(
            FlightDeclaration.state.in_(_ACTIVE_STATES),
            FlightDeclaration.start_datetime <= end_datetime,
            FlightDeclaration.end_datetime >= start_datetime,
        )
        if exclude_id is not None:
            decl_query = decl_query.where(FlightDeclaration.id != exclude_id)
        decl_result = await db.execute(decl_query)
        declarations = decl_result.scalars().all()
        prefetched_declarations = [{"id": str(d.id), "bounds": d.bounds} for d in declarations]

        engine_cls = load_plugin(settings.plugin_deconfliction_engine, expected_protocol=DeconflictionEngine)
        engine = engine_cls()
        # Bounding-box (R-tree) deconfliction — a faithful port of the Django
        # DefaultDeconflictionEngine create-path. The full 4D volume-mode check
        # (deconflict_operational_intent) is exercised directly by the engine
        # unit tests and used by the SCD flight-planning endpoint.
        req = DeconflictionRequest(
            start_datetime=start_datetime.isoformat(),
            end_datetime=end_datetime.isoformat(),
            flight_declaration_geo_json=geo_json,
            view_box=_view_box_from_bounds(bounds),
            ussp_network_enabled=int(settings.ussp_network_enabled),
            type_of_operation=type_of_operation,
            priority=0,
            prefetched_fences=prefetched_fences,
            prefetched_declarations=prefetched_declarations,
        )
        result = engine.check_deconfliction(req)
        return result.is_approved, result.declaration_state
    except Exception as exc:
        # Fail closed: a deconfliction failure must never auto-approve an operation.
        logger.error("Deconfliction engine error; failing closed (not approving): {}", exc)
        return False, int(OperationState.REJECTED)


async def _get_declaration_or_404(declaration_id: uuid.UUID, db: AsyncSession) -> FlightDeclaration:
    obj = await db.get(FlightDeclaration, declaration_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flight declaration not found")
    return obj


def _maybe_submit_to_dss(declaration_id: uuid.UUID, is_approved: bool, declaration_state: int) -> None:
    """Submit an approved operation to the DSS when the USSP network is enabled.

    Mirrors the Django acceptance path
    (``flight_declaration_operations/views.py``): a clear deconfliction result
    with the USSP network enabled leaves the operation Processing (state 0) and
    queues an async DSS submission, gated additionally by ``AUTO_SUBMIT_TO_DSS``.
    With the network disabled the operation is Accepted locally (state 1) and no
    submission is made.
    """
    settings = get_settings()
    if is_approved and declaration_state == int(OperationState.NOT_SUBMITTED) and settings.ussp_network_enabled and settings.auto_submit_to_dss:
        submit_flight_declaration_to_dss_async.delay(str(declaration_id))


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
    now = datetime.now(timezone.utc)
    geo_json = payload.flight_declaration_geo_json
    bounds = _bounds_from_geojson(geo_json)
    start_dt = _parse_utc_dt(payload.start_datetime, now)
    end_dt = _parse_utc_dt(payload.end_datetime, now + timedelta(hours=2))

    return {
        "operational_intent": json.dumps(geo_json) if geo_json else "{}",
        "flight_declaration_raw_geojson": json.dumps(geo_json) if geo_json else None,
        "bounds": bounds,
        "aircraft_id": str(payload.aircraft_id or "UNKNOWN")[:256],
        "type_of_operation": int(payload.type_of_operation or 0),
        "state": int(payload.flight_state or 1),
        "originating_party": str(payload.originating_party or "Flight Blender Default")[:100],
        "start_datetime": start_dt,
        "end_datetime": end_dt,
    }


@router.post("/set_flight_declarations_bulk", response_model=BulkFlightDeclarationCreateResponse, dependencies=[WriteDep])
async def bulk_create_flight_declarations(payloads: list[FlightDeclarationFullRequest], db: AsyncSession = Depends(get_db)):
    results: list[BulkFlightDeclarationResult] = []
    submitted = 0
    failed = 0

    settings = get_settings()
    default_state = 0 if settings.ussp_network_enabled else 1

    # Each declaration runs the same strategic deconfliction as the single-create
    # path: a conflict (or engine error) fails closed and is not approved.
    for payload in payloads:
        try:
            fields = _build_declaration_from_full_request(payload)
            fields["state"] = default_state
            is_approved, deconf_state = await _run_deconfliction(
                payload.flight_declaration_geo_json,
                fields["start_datetime"],
                fields["end_datetime"],
                db=db,
                bounds=fields["bounds"],
                type_of_operation=fields["type_of_operation"],
            )
            fields["is_approved"] = is_approved
            fields["state"] = deconf_state
            decl = FlightDeclaration(**fields)
            db.add(decl)
            await db.flush()
            await db.refresh(decl)
            _maybe_submit_to_dss(decl.id, is_approved, deconf_state)
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
    settings = get_settings()
    default_state = 0 if settings.ussp_network_enabled else 1
    fields = _build_declaration_from_full_request(payload)
    fields["state"] = default_state
    is_approved, deconf_state = await _run_deconfliction(
        payload.flight_declaration_geo_json,
        fields["start_datetime"],
        fields["end_datetime"],
        db=db,
        bounds=fields["bounds"],
        type_of_operation=fields["type_of_operation"],
    )
    fields["is_approved"] = is_approved
    fields["state"] = deconf_state
    decl = FlightDeclaration(**fields)
    db.add(decl)
    await db.flush()
    await db.refresh(decl)
    _maybe_submit_to_dss(decl.id, is_approved, deconf_state)
    return FlightDeclarationCreateResponse(id=decl.id, message="Flight declaration created", is_approved=decl.is_approved, state=decl.state)


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
    now = datetime.now(timezone.utc)
    start_dt = _parse_utc_dt(request_data.get("start_datetime"), now)
    end_dt = _parse_utc_dt(request_data.get("end_datetime"), now + timedelta(hours=2))

    volumes = request_data.get("operational_intent_volume4ds", [])
    bounds = _bounds_from_volumes(volumes)
    features = _geo_features_from_volumes(volumes)
    geo_json = {"type": "FeatureCollection", "features": features} if features else None

    return {
        "operational_intent": json.dumps(volumes),
        "flight_declaration_raw_geojson": json.dumps(geo_json) if geo_json else None,
        "bounds": bounds,
        "aircraft_id": str(request_data.get("aircraft_id", "UNKNOWN"))[:256],
        "type_of_operation": _safe_int(request_data.get("type_of_operation"), 0),
        "state": default_state,
        "originating_party": str(request_data.get("originating_party", "Flight Blender Default"))[:100],
        "submitted_by": request_data.get("submitted_by"),
        "start_datetime": start_dt,
        "end_datetime": end_dt,
    }


@router.post("/set_operational_intent", response_model=FlightDeclarationCreateResponse, status_code=status.HTTP_201_CREATED, dependencies=[WriteDep])
async def set_operational_intent(payload: OperationalIntentIngestRequest, db: AsyncSession = Depends(get_db)):
    """Create a flight declaration from an operational-intent payload (Volume4D format)."""
    settings = get_settings()
    default_state = 0 if settings.ussp_network_enabled else 1
    fields = _build_declaration_from_op_intent(payload.model_dump(), default_state=default_state)
    geo_json_str = fields.get("flight_declaration_raw_geojson")
    geo_json = json.loads(geo_json_str) if geo_json_str else None
    is_approved, deconf_state = await _run_deconfliction(
        geo_json,
        fields["start_datetime"],
        fields["end_datetime"],
        db=db,
        bounds=fields["bounds"],
        type_of_operation=fields["type_of_operation"],
    )
    fields["is_approved"] = is_approved
    fields["state"] = deconf_state
    decl = FlightDeclaration(**fields)
    db.add(decl)
    await db.flush()
    await db.refresh(decl)
    _maybe_submit_to_dss(decl.id, is_approved, deconf_state)
    return FlightDeclarationCreateResponse(
        id=decl.id, message="Flight declaration created via operational intent", is_approved=decl.is_approved, state=decl.state
    )


@router.post("/set_operational_intents_bulk", response_model=BulkFlightDeclarationCreateResponse, dependencies=[WriteDep])
async def set_operational_intents_bulk(payloads: list[OperationalIntentIngestRequest], db: AsyncSession = Depends(get_db)):
    """Bulk create flight declarations from operational-intent payloads."""
    results: list[BulkFlightDeclarationResult] = []
    submitted = 0
    failed = 0

    settings = get_settings()
    default_state = 0 if settings.ussp_network_enabled else 1

    # Each op-intent runs the same strategic deconfliction as the single-create
    # path: a conflict (or engine error) fails closed and is not approved.
    for payload in payloads:
        try:
            fields = _build_declaration_from_op_intent(payload.model_dump(), default_state=default_state)
            geo_json_str = fields.get("flight_declaration_raw_geojson")
            geo_json = json.loads(geo_json_str) if geo_json_str else None
            is_approved, deconf_state = await _run_deconfliction(
                geo_json,
                fields["start_datetime"],
                fields["end_datetime"],
                db=db,
                bounds=fields["bounds"],
                type_of_operation=fields["type_of_operation"],
            )
            fields["is_approved"] = is_approved
            fields["state"] = deconf_state
            decl = FlightDeclaration(**fields)
            db.add(decl)
            await db.flush()
            await db.refresh(decl)
            _maybe_submit_to_dss(decl.id, is_approved, deconf_state)
            results.append(BulkFlightDeclarationResult(id=decl.id, message="Created", success=True))
            submitted += 1
        except Exception as exc:
            logger.error("Bulk operational intent create error: %s", exc)
            results.append(BulkFlightDeclarationResult(id=None, message=str(exc), success=False))
            failed += 1

    return BulkFlightDeclarationCreateResponse(submitted=submitted, failed=failed, results=results)
