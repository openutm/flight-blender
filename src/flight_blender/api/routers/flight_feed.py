import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.api.dependencies import require_scopes
from flight_blender.api.schemas.flight_feed import ObservationRequest, SignedTelemetryKeyCreate, SignedTelemetryKeyUpdate
from flight_blender.common.data_definitions import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from flight_blender.core.operations.flight_feed import FlightFeedOperations
from flight_blender.core.operations.flight_feed import FlightBlenderTelemetryValidator
from flight_blender.infrastructure.auth.pki_helper import MessageVerifier, ResponseSigningOperations
from flight_blender.infrastructure.celery.flight_feed_dispatcher import CeleryFlightFeedTaskDispatcher
from flight_blender.infrastructure.database.repositories.sa_flight_feed import SQLAlchemyFlightFeedRepository
from flight_blender.infrastructure.database.session import async_get_db

router = APIRouter(prefix="/flight_stream")

GA_TEST_SCOPE = "geo-awareness.test"


async def _ops(db: AsyncSession = Depends(async_get_db)) -> FlightFeedOperations:
    return FlightFeedOperations(
        repo=SQLAlchemyFlightFeedRepository(db),
        dispatcher=CeleryFlightFeedTaskDispatcher(),
        telemetry_validator=FlightBlenderTelemetryValidator(),
    )


# ── Air Traffic ───────────────────────────────────────────────────────────────


@router.post("/set_air_traffic/{session_id}", status_code=201)
async def set_air_traffic(
    session_id: uuid.UUID,
    body: ObservationRequest,
    ops: FlightFeedOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    result, status_code = await ops.set_air_traffic(session_id=session_id, body=body)
    return JSONResponse(result, status_code=status_code)


@router.post("/bulk_set_air_traffic/{session_id}", status_code=201)
async def bulk_set_air_traffic(
    session_id: uuid.UUID,
    body: ObservationRequest,
    ops: FlightFeedOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    result, status_code = await ops.bulk_set_air_traffic(session_id=session_id, body=body)
    return JSONResponse(result, status_code=status_code)


@router.get("/get_air_traffic/{session_id}")
async def get_air_traffic(
    session_id: uuid.UUID,
    view: str | None = None,
    ops: FlightFeedOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    result, status_code = await ops.get_air_traffic(session_id=session_id, view=view)
    return JSONResponse(result, status_code=status_code)


@router.get("/start_opensky_feed")
async def start_opensky_feed(
    view: str | None = None,
    ops: FlightFeedOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    result, status_code = await ops.start_opensky_feed(view=view)
    return JSONResponse(result, status_code=status_code)


# ── RID Telemetry ─────────────────────────────────────────────────────────────


@router.put("/set_telemetry", status_code=201)
async def set_telemetry(
    request: Request,
    ops: FlightFeedOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    raw_data = await request.json()
    result, status_code = await ops.submit_telemetry(raw_data=raw_data)
    return JSONResponse(result, status_code=status_code)


@router.put("/set_signed_telemetry", status_code=201)
async def set_signed_telemetry(
    request: Request,
    ops: FlightFeedOperations = Depends(_ops),
):
    body = await request.body()
    headers = dict(request.headers)
    url = str(request.url)

    if not MessageVerifier().verify_message(body=body, headers=headers, url=url):
        raise HTTPException(
            status_code=400,
            detail={"message": "Could not verify against public keys setup in Flight Blender"},
        )

    result, status_code = await ops.submit_signed_telemetry(raw_data=json.loads(body))
    if status_code != 201:
        return JSONResponse(result, status_code=status_code)

    signer = ResponseSigningOperations()
    content_digest = signer.generate_content_digest(result)
    result["signed"] = signer.sign_json_via_django(result)
    return Response(
        content=json.dumps(result).encode(),
        status_code=201,
        media_type="application/json",
        headers={"Content-Digest": content_digest, "req": headers.get("signature", "")},
    )


# ── Signed Telemetry Public Keys ─────────────────────────────────────────────


@router.get("/public_keys/")
async def list_signed_telemetry_keys(
    ops: FlightFeedOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([GA_TEST_SCOPE])),
):
    return await ops.list_signed_telemetry_keys()


@router.post("/public_keys/", status_code=201)
async def create_signed_telemetry_key(
    body: SignedTelemetryKeyCreate,
    ops: FlightFeedOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([GA_TEST_SCOPE])),
):
    return await ops.create_signed_telemetry_key(key_id=body.key_id, url=body.url, is_active=body.is_active)


@router.get("/public_keys/{pk}/")
async def get_signed_telemetry_key(
    pk: uuid.UUID,
    ops: FlightFeedOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([GA_TEST_SCOPE])),
):
    key = await ops.get_signed_telemetry_key(pk)
    if key is None:
        raise HTTPException(status_code=404, detail="Not found")
    return key


@router.put("/public_keys/{pk}/")
async def update_signed_telemetry_key(
    pk: uuid.UUID,
    body: SignedTelemetryKeyUpdate,
    ops: FlightFeedOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([GA_TEST_SCOPE])),
):
    key = await ops.update_signed_telemetry_key(pk, **body.model_dump(exclude_none=True))
    if key is None:
        raise HTTPException(status_code=404, detail="Not found")
    return key


@router.delete("/public_keys/{pk}/", status_code=204)
async def delete_signed_telemetry_key(
    pk: uuid.UUID,
    ops: FlightFeedOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([GA_TEST_SCOPE])),
):
    deleted = await ops.delete_signed_telemetry_key(pk)
    if not deleted:
        raise HTTPException(status_code=404, detail="Not found")
