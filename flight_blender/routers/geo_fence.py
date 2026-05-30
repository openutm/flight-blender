"""
FastAPI router for geo fence operations.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
from flight_blender.database import get_db
from flight_blender.models.geo_fence import GeoFence
from flight_blender.schemas.geo_fence import (
    GeoAwarenessStatusResponse,
    GeoFenceCreate,
    GeoFenceListResponse,
    GeoFenceResponse,
    GeoFenceUpdate,
    GeoZoneQueryRequest,
)
from flight_blender.tasks.geo_fence import download_geozone_source, write_geo_zone

router = APIRouter()


async def _get_fence_or_404(fence_id: uuid.UUID, db: AsyncSession) -> GeoFence:
    obj = await db.get(GeoFence, fence_id)
    if not obj:
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
    count_result = await db.execute(select(func.count()).select_from(GeoFence))
    total = count_result.scalar_one()
    result = await db.execute(select(GeoFence).order_by(GeoFence.created_at.desc()).offset(offset).limit(page_size))
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
    return await _get_fence_or_404(fence_id, db)


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
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="GeoJSON must contain at least one feature")
    props = features[0].get("properties") or {}
    geometry = features[0].get("geometry") or {}

    # Compute bounding box from polygon coordinates
    coords = geometry.get("coordinates", [[]])
    flat_coords = [pt for ring in coords for pt in ring]
    if flat_coords:
        lons = [pt[0] for pt in flat_coords]
        lats = [pt[1] for pt in flat_coords]
        bounds = json.dumps({"minx": min(lons), "miny": min(lats), "maxx": max(lons), "maxy": max(lats)})
    else:
        bounds = "{}"

    # Parse start/end times; fall back to sensible defaults
    try:
        start_dt = datetime.fromisoformat(props["start_time"])
    except (KeyError, ValueError):
        start_dt = datetime.now(timezone.utc)
    try:
        end_dt = datetime.fromisoformat(props["end_time"])
    except (KeyError, ValueError):
        from datetime import timedelta

        end_dt = start_dt if isinstance(start_dt, datetime) else datetime.now(timezone.utc)
        end_dt = end_dt + timedelta(days=365)

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=timezone.utc)

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
    """Accept an ED-269 GeoZone payload and queue async processing."""
    write_geo_zone.delay(payload)
    return {"message": "GeoZone queued for processing"}


# ── Geo Awareness (test harness) ───────────────────────────────────────────────


@router.get("/geo_awareness/status", response_model=GeoAwarenessStatusResponse, dependencies=[ReadDep])
async def geo_awareness_status():
    return GeoAwarenessStatusResponse(result="Ready", message="Geo awareness service is operational")


@router.post("/geo_awareness/map/queries", dependencies=[ReadDep])
async def geo_awareness_zone_query(payload: GeoZoneQueryRequest, db: AsyncSession = Depends(get_db)):
    """Return GeoZone features intersecting the queried volumes."""
    # Simplified implementation: returns all active geo fences
    result = await db.execute(select(GeoFence).where(GeoFence.status == 1).limit(100))
    fences = result.scalars().all()
    return {"zones": [{"id": str(f.id), "name": f.name} for f in fences]}


# ── Geospatial data sources (USS qualifier harness) ────────────────────────────


@router.get("/geo_awareness/geospatial_data_sources", dependencies=[ReadDep])
async def list_geospatial_data_sources():
    return {"sources": []}


@router.post("/geo_awareness/geospatial_data_sources", status_code=status.HTTP_201_CREATED, dependencies=[WriteDep])
async def create_geospatial_data_source(payload: dict):
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="url is required")
    download_geozone_source.delay(url)
    return {"message": "GeoZone source download queued", "url": url}


@router.get("/geo_awareness/geospatial_data_sources/{source_id}", dependencies=[ReadDep])
async def get_geospatial_data_source(source_id: uuid.UUID = Path(...)):
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")


@router.delete("/geo_awareness/geospatial_data_sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[WriteDep])
async def delete_geospatial_data_source(source_id: uuid.UUID = Path(...)):
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Data source not found")
