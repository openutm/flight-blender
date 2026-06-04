import json
import uuid
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from implicitdict import ImplicitDict
from marshmallow import ValidationError as MarshmallowValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.api.dependencies import require_scopes
from flight_blender.common.data_definitions import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from flight_blender.core.operations.geo_fence import GeoFenceOperations
from flight_blender.geo_fence.common import validate_geo_zone
from flight_blender.geo_fence.data_definitions import GeoFencePutSchema, GeoZoneCheckRequestBody, GeoZoneHttpsSource
from flight_blender.geo_fence.tasks import write_geo_zone
from flight_blender.infrastructure.database.repositories.sa_geo_fence import SQLAlchemyGeoFenceRepository
from flight_blender.infrastructure.database.session import async_get_db

router = APIRouter()

GA_TEST_SCOPE = "geo-awareness.test"


async def _ops(db: AsyncSession = Depends(async_get_db)) -> GeoFenceOperations:
    return GeoFenceOperations(repo=SQLAlchemyGeoFenceRepository(db))


# ── GeoFence CRUD ────────────────────────────────────────────────────────────


@router.put("/set_geo_fence")
async def set_geo_fence(
    request: Request,
    ops: GeoFenceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    if request.headers.get("content-type") != "application/json":
        return JSONResponse({"message": "Unsupported Media Type"}, status_code=415)

    body = await request.json()
    schema = GeoFencePutSchema()
    try:
        validated = schema.load(body)
    except MarshmallowValidationError as e:
        return JSONResponse(e.messages, status_code=400)

    try:
        result = await ops.create_geofence_from_feature_collection(validated)
    except ValueError as e:
        return JSONResponse({"message": str(e)}, status_code=400)
    return JSONResponse(result, status_code=200)


@router.post("/set_geozone")
async def set_geozone(
    request: Request,
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    if request.headers.get("content-type") != "application/json":
        return JSONResponse({"message": "Unsupported Media Type"}, status_code=415)

    body = await request.json()
    if not validate_geo_zone(body):
        return JSONResponse(
            {"message": "A valid geozone object with a description is necessary in the body of the request"},
            status_code=400,
        )

    write_geo_zone.delay(geo_zone=json.dumps(body))
    fence_id = str(uuid.uuid4())
    return JSONResponse({"message": "GeoZone Declaration submitted", "id": fence_id}, status_code=200)


@router.get("/geo_fence")
async def list_geo_fences(
    start_date: str | None = None,
    end_date: str | None = None,
    view: str | None = None,
    limit: int = 100,
    offset: int = 0,
    ops: GeoFenceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    fences = await ops.list_geofences(start_date=start_date, end_date=end_date, viewport=view)
    page = fences[offset : offset + limit]
    return {"count": len(fences), "results": page}


@router.get("/geo_fence/{pk}")
async def get_geo_fence(
    pk: uuid.UUID,
    ops: GeoFenceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    fence = await ops.get_geofence(pk)
    if fence is None:
        raise HTTPException(status_code=404, detail="Not found")
    return fence


@router.delete("/geo_fence/{pk}/delete", status_code=204)
async def delete_geo_fence(
    pk: uuid.UUID,
    ops: GeoFenceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    deleted = await ops.delete_geofence(pk)
    if not deleted:
        raise HTTPException(status_code=404, detail="Not found")


# ── Geo-awareness test harness ───────────────────────────────────────────────


@router.get("/geo_awareness/status")
async def geo_awareness_status(
    ops: GeoFenceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([GA_TEST_SCOPE])),
):
    return await ops.get_test_harness_status()


@router.get("/geo_awareness/geospatial_data_sources")
async def geospatial_data_sources(
    start_date: str | None = None,
    end_date: str | None = None,
    view: str | None = None,
    limit: int = 100,
    offset: int = 0,
    ops: GeoFenceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([GA_TEST_SCOPE])),
):
    sources = await ops.get_geospatial_data_sources(start_date=start_date, end_date=end_date, viewport=view)
    page = sources[offset : offset + limit]
    return {"count": len(sources), "results": page}


@router.put("/geo_awareness/geospatial_data_sources/{geozone_source_id}")
async def put_geozone_source(
    geozone_source_id: str,
    request: Request,
    ops: GeoFenceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([GA_TEST_SCOPE])),
):
    body = await request.json()
    try:
        source_details = ImplicitDict.parse(body, GeoZoneHttpsSource)
    except KeyError:
        return JSONResponse(
            {"result": "Rejected", "message": "A url and format key is required"},
            status_code=200,
        )

    url_validator = URLValidator()
    try:
        url_validator(source_details.https_source.url)
    except DjangoValidationError:
        return JSONResponse({"result": "Unsupported", "message": "Invalid url provided"}, status_code=200)

    result = await ops.put_geozone_source(geozone_source_id, source_details.https_source.url)
    return JSONResponse(result, status_code=200)


@router.get("/geo_awareness/geospatial_data_sources/{geozone_source_id}")
async def get_geozone_source(
    geozone_source_id: str,
    ops: GeoFenceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([GA_TEST_SCOPE])),
):
    result = await ops.get_geozone_source_status(geozone_source_id)
    if result is None:
        raise HTTPException(status_code=404)
    return result


@router.delete("/geo_awareness/geospatial_data_sources/{geozone_source_id}")
async def delete_geozone_source(
    geozone_source_id: str,
    ops: GeoFenceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([GA_TEST_SCOPE])),
):
    result = await ops.delete_geozone_source(geozone_source_id)
    if result is None:
        raise HTTPException(status_code=404)
    return result


@router.post("/geo_awareness/map/queries")
async def geo_awareness_check(
    request: Request,
    ops: GeoFenceOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([GA_TEST_SCOPE])),
):
    body = await request.json()
    check_body = ImplicitDict.parse(body, GeoZoneCheckRequestBody)
    return await ops.check_geozones(check_body)
