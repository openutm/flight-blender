"""
FastAPI router for geo fence operations.
"""

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from flight_blender.auth import GeoAwarenessTestDep, ReadDep, WriteDep
from flight_blender.database import get_db
from flight_blender.models.geo_fence import GeoFence
from flight_blender.schemas.geo_fence import (
    GeoAwarenessStatusResponse,
    GeoAwarenessTestStatus,
    GeoFenceCreate,
    GeoFenceListResponse,
    GeoFenceResponse,
    GeoFenceUpdate,
    GeoZoneCheckResult,
    GeoZoneChecksResponse,
    GeoZoneQueryRequest,
    GeoZoneSourceRequest,
)
from flight_blender.tasks.geo_fence import (
    delete_geozone_source_status,
    download_geozone_source,
    get_geozone_source_status,
    set_geozone_source_status,
    validate_geo_zone,
    write_geo_zone,
)

router = APIRouter()


def _compute_bounds(flat_coords: list[list[float]]) -> str:
    """Return a comma-separated ``"minx,miny,maxx,maxy"`` bounds string.

    Matches the Django ``unary_union(...).bounds`` formatting and the
    ``write_geo_zone`` task path, so all GeoFence rows share one bounds format.
    """
    if not flat_coords:
        return ""
    lons = [pt[0] for pt in flat_coords]
    lats = [pt[1] for pt in flat_coords]
    return f"{min(lons):.7f},{min(lats):.7f},{max(lons):.7f},{max(lats):.7f}"


def _parse_fence_dt(props: dict[str, Any], key: str, fallback: datetime) -> datetime:
    """Parse a datetime string from props or return the fallback."""
    try:
        dt = datetime.fromisoformat(props[key])
    except (KeyError, ValueError):
        dt = fallback
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


async def _get_fence_or_404(fence_id: uuid.UUID, db: AsyncSession, *, include_test: bool = True) -> GeoFence:
    obj = await db.get(GeoFence, fence_id)
    if not obj or (not include_test and obj.is_test_dataset):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Geo fence not found")
    return obj


# ── Geo Fence CRUD ─────────────────────────────────────────────────────────────


@router.get("/geo_fence", response_model=GeoFenceListResponse, dependencies=[ReadDep])
async def list_geo_fences(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * page_size
    # Django ``GeoFenceList`` filters out test datasets so the InterUSS test
    # harness data never leaks into the operational listing.
    base = select(GeoFence).where(GeoFence.is_test_dataset.is_(False))
    count_result = await db.execute(select(func.count()).select_from(base.subquery()))
    total = count_result.scalar_one()
    result = await db.execute(base.order_by(GeoFence.created_at.desc()).offset(offset).limit(page_size))
    return GeoFenceListResponse(count=total, results=result.scalars().all())


@router.post("/geo_fence", response_model=GeoFenceResponse, status_code=status.HTTP_201_CREATED, dependencies=[WriteDep])
async def create_geo_fence(payload: GeoFenceCreate, db: AsyncSession = Depends(get_db)):
    fence = GeoFence(**payload.model_dump())
    db.add(fence)
    await db.flush()
    await db.refresh(fence)
    return fence


@router.get("/geo_fence/{fence_id}", response_model=GeoFenceResponse, dependencies=[ReadDep])
async def get_geo_fence(fence_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    # Django ``GeoFenceDetail`` hides test datasets (404 for them).
    return await _get_fence_or_404(fence_id, db, include_test=False)


@router.put("/geo_fence/{fence_id}", response_model=GeoFenceResponse, dependencies=[WriteDep])
async def update_geo_fence(payload: GeoFenceUpdate, fence_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    fence = await _get_fence_or_404(fence_id, db)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(fence, field, value)
    await db.flush()
    await db.refresh(fence)
    return fence


@router.delete("/geo_fence/{fence_id}/delete", status_code=status.HTTP_204_NO_CONTENT, dependencies=[WriteDep])
async def delete_geo_fence(fence_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    fence = await _get_fence_or_404(fence_id, db)
    await db.delete(fence)


# ── Ingest helpers ─────────────────────────────────────────────────────────────


@router.post("/set_geo_fence", response_model=GeoFenceResponse, dependencies=[WriteDep])
async def set_geo_fence(payload: GeoFenceCreate, db: AsyncSession = Depends(get_db)):
    """Convenience endpoint for creating/replacing a geo fence."""
    fence = GeoFence(**payload.model_dump())
    db.add(fence)
    await db.flush()
    await db.refresh(fence)
    return fence


def _parse_geojson_fence(geojson: dict[str, Any]) -> dict[str, Any]:
    """Extract GeoFence fields from a GeoJSON FeatureCollection."""
    features = geojson.get("features") or []
    if not features:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="GeoJSON must contain at least one feature")
    props = features[0].get("properties") or {}
    geometry = features[0].get("geometry") or {}

    flat_coords = [pt for ring in geometry.get("coordinates", [[]]) for pt in ring]
    bounds = _compute_bounds(flat_coords)

    now = datetime.now(timezone.utc)
    start_dt = _parse_fence_dt(props, "start_time", now)
    end_dt = _parse_fence_dt(props, "end_time", start_dt + timedelta(days=365))

    return {
        "raw_geo_fence": json.dumps(geojson),
        "upper_limit": float(props.get("upper_limit", 500)),
        "lower_limit": float(props.get("lower_limit", 0)),
        "name": str(props.get("name", "Geofence"))[:50],
        "bounds": bounds,
        "start_datetime": start_dt,
        "end_datetime": end_dt,
        "is_test_dataset": False,
    }


@router.put("/set_geo_fence", response_model=GeoFenceResponse, dependencies=[WriteDep])
async def set_geo_fence_put(geojson: dict[str, Any] = Body(...), db: AsyncSession = Depends(get_db)):
    """Accept a GeoJSON FeatureCollection via PUT and create a geo fence.

    Used by the verification toolkit which sends a GeoJSON document.
    """
    fields = _parse_geojson_fence(geojson)
    fence = GeoFence(**fields)
    db.add(fence)
    await db.flush()
    await db.refresh(fence)
    return fence


@router.post("/set_geozone", dependencies=[WriteDep])
async def set_geozone(payload: dict, db: AsyncSession = Depends(get_db)):
    """Accept an ED-269 GeoZone payload, validate it, and queue async processing.

    Restores the Django request-time ``validate_geo_zone`` gate: an invalid or
    description-less GeoZone is rejected with 400 rather than silently queued.
    """
    if not validate_geo_zone(payload):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid geozone object with a description is necessary in the body of the request",
        )
    write_geo_zone.delay(payload)
    return {"message": "GeoZone queued for processing", "id": str(uuid.uuid4())}


# ── Geo Awareness (InterUSS ED-269 test harness) ───────────────────────────────


@router.get("/geo_awareness/status", response_model=GeoAwarenessStatusResponse, dependencies=[GeoAwarenessTestDep])
async def geo_awareness_status():
    """InterUSS geo-awareness test-harness status (guarded by geo-awareness.test)."""
    return GeoAwarenessStatusResponse(status="Ready", api_version="latest")


def _point_in_ring(lon: float, lat: float, ring: list[list[float]]) -> bool:
    """Ray-casting point-in-polygon test for a single ``[lon, lat]`` ring."""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    j = n - 1
    for i in range(n):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-12) + xi):
            inside = not inside
        j = i
    return inside


def _bounds_contains_point(bounds: str, lon: float, lat: float) -> bool:
    """Return True if the comma-separated ``minx,miny,maxx,maxy`` bounds cover the point."""
    try:
        minx, miny, maxx, maxy = (float(x) for x in bounds.split(","))
    except (ValueError, AttributeError):
        return False
    return minx <= lon <= maxx and miny <= lat <= maxy


def _geozone_covers_point(fence: GeoFence, lon: float, lat: float) -> bool:
    """Point membership: try the stored geometry ring, fall back to the bbox."""
    from flight_blender.tasks.geo_fence import feature_to_coordinates

    if fence.geozone:
        try:
            ring = feature_to_coordinates(json.loads(fence.geozone))
            if ring:
                return _point_in_ring(lon, lat, ring)
        except (TypeError, ValueError):
            pass
    return _bounds_contains_point(fence.bounds or "", lon, lat)


@router.post("/geo_awareness/map/queries", response_model=GeoZoneChecksResponse, dependencies=[GeoAwarenessTestDep])
async def geo_awareness_zone_query(payload: GeoZoneQueryRequest, db: AsyncSession = Depends(get_db)):
    """ED-269 spatial check over test-dataset geozones.

    Accepts the InterUSS ``{"checks": [{"filter_sets": [...]}]}`` body and returns
    ``{"applicableGeozone": [{"geozone": "Present" | "Absent"}]}``.
    """
    result = await db.execute(select(GeoFence).where(GeoFence.is_test_dataset.is_(True)))
    fences = result.scalars().all()

    geo_zones_of_interest = False
    for check in payload.checks:
        for filter_set in check.get("filter_sets", []):
            if "position" in filter_set:
                pos = filter_set["position"]
                lon, lat = None, None
                if isinstance(pos, dict):
                    lon = pos.get("lng", pos.get("longitude", pos.get("lon")))
                    lat = pos.get("lat", pos.get("latitude"))
                elif isinstance(pos, (list, tuple)) and len(pos) >= 2:
                    lon, lat = pos[0], pos[1]
                if lon is not None and lat is not None:
                    if any(_geozone_covers_point(f, float(lon), float(lat)) for f in fences):
                        geo_zones_of_interest = True
            if "after" in filter_set:
                after_dt = _parse_fence_dt(filter_set, "after", datetime.now(timezone.utc))
                if any(f.end_datetime and f.end_datetime >= after_dt for f in fences):
                    geo_zones_of_interest = True
            if "before" in filter_set:
                before_dt = _parse_fence_dt(filter_set, "before", datetime.now(timezone.utc))
                if any(f.start_datetime and f.start_datetime <= before_dt for f in fences):
                    geo_zones_of_interest = True

    geozone = "Present" if geo_zones_of_interest else "Absent"
    return GeoZoneChecksResponse(applicableGeozone=[GeoZoneCheckResult(geozone=geozone)], message="Test")


# ── Geospatial data sources (InterUSS qualifier harness) ───────────────────────


@router.put("/geo_awareness/geospatial_data_sources/{source_id}", response_model=GeoAwarenessTestStatus, dependencies=[GeoAwarenessTestDep])
async def put_geospatial_data_source(payload: GeoZoneSourceRequest, source_id: str = Path(...)):
    """Register/replace a geozone source: enqueue the download and record status."""
    geo_zone_url = payload.https_source.url
    download_geozone_source.delay(geo_zone_url=geo_zone_url, geozone_source_id=source_id)
    status_record = {"result": "Activating", "message": ""}
    set_geozone_source_status(source_id, status_record)
    return GeoAwarenessTestStatus(**status_record)


@router.get("/geo_awareness/geospatial_data_sources/{source_id}", response_model=GeoAwarenessTestStatus, dependencies=[GeoAwarenessTestDep])
async def get_geospatial_data_source(source_id: str = Path(...)):
    record = get_geozone_source_status(source_id)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")
    return GeoAwarenessTestStatus(result=record.get("result", ""), message=record.get("message", ""))


@router.delete("/geo_awareness/geospatial_data_sources/{source_id}", response_model=GeoAwarenessTestStatus, dependencies=[GeoAwarenessTestDep])
async def delete_geospatial_data_source(source_id: str = Path(...), db: AsyncSession = Depends(get_db)):
    if get_geozone_source_status(source_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")
    # Schedule deletion of the test datasets associated with this source.
    result = await db.execute(select(GeoFence).where(GeoFence.is_test_dataset.is_(True)))
    for fence in result.scalars().all():
        await db.delete(fence)
    deletion_status = {"result": "Deactivating", "message": "Test data has been scheduled to be deleted"}
    set_geozone_source_status(source_id, deletion_status)
    delete_geozone_source_status(source_id)
    return GeoAwarenessTestStatus(**deletion_status)
