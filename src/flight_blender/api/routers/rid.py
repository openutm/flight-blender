import asyncio
import json
import uuid
from dataclasses import asdict
from typing import Any

import arrow
import shapely.geometry
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from loguru import logger
from uas_standards.astm.f3411.v22a.constants import NetDetailsMaxDisplayAreaDiagonalKm

from flight_blender.api.dependencies import require_scopes
from flight_blender.api.schemas.rid import CreateTestBody, ISACallbackBody
from flight_blender.auth.common import get_redis
from flight_blender.common.data_definitions import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from flight_blender.core.entities.uss import (
    FlightDetailsNotFoundMessage,
    GenericErrorResponseMessage,
    OperatorDetailsSuccessResponse,
    RIDFlightDetails,
)
from flight_blender.core.operations import rid as view_port_ops
from flight_blender.core.operations.rid import (
    CreateTestResponse,
    IdentificationServiceArea,
    Position,
    RIDDisplayDataResponse,
    RIDFlight,
    RIDFlightsRecord,
    RIDPositions,
    RIDSubscription,
    RIDVolume4D,
    SubscriptionState,
)
from flight_blender.infrastructure.database.repositories.sa_flight_feed import SQLAlchemyFlightFeedRepository
from flight_blender.infrastructure.database.repositories.sa_rid import SQLAlchemyRIDRepository
from flight_blender.infrastructure.database.session import async_get_db

router = APIRouter(prefix="/rid")


async def _rid_ops(db=Depends(async_get_db)) -> SQLAlchemyRIDRepository:
    return SQLAlchemyRIDRepository(db)


async def _feed_ops(db=Depends(async_get_db)) -> SQLAlchemyFlightFeedRepository:
    return SQLAlchemyFlightFeedRepository(db)


@router.get("/capabilities")
async def get_rid_capabilities(_auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE]))):
    return {"capabilities": ["ASTMRID2022"]}


@router.put("/create_dss_subscription")
async def create_dss_subscription(
    view: str | None = None,
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    from flight_blender.infrastructure.dss.rid import RemoteIDOperations

    try:
        view_port = [float(i) for i in (view or "").split(",")]
    except Exception:
        return JSONResponse({"message": "A view bounding box is necessary with four values: lat1,lng1,lat2,lng2."}, status_code=400)
    if not view_port_ops.check_view_port(view_port_coords=view_port):
        return JSONResponse({"message": "A view bounding box is necessary with four values: lat1,lng1,lat2,lng2."}, status_code=400)

    box = shapely.geometry.box(view_port[1], view_port[0], view_port[3], view_port[2])
    vertex_list = [{"lng": lng, "lat": lat} for lng, lat in list(zip(*box.exterior.coords.xy))[:-1]]
    request_id = str(uuid.uuid4())

    subscription_r = await asyncio.to_thread(
        RemoteIDOperations().create_dss_subscription,
        vertex_list,
        view or "",
        request_id,
        30,
        False,
    )
    if subscription_r.created:
        return JSONResponse(
            {
                "message": "DSS Subscription created",
                "id": request_id,
                "dss_subscription_response": asdict(subscription_r),
            },
            status_code=201,
        )
    return JSONResponse(
        {"message": "Error in creating DSS Subscription, please check the log or contact your administrator.", "id": request_id},
        status_code=400,
    )


@router.get("/get_rid_data/{subscription_id}")
async def get_rid_data(
    subscription_id: uuid.UUID,
    repo: SQLAlchemyRIDRepository = Depends(_rid_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    sub_id_str = str(subscription_id)
    if not await repo.check_subscription_exists_by_subscription_id(sub_id_str):
        return JSONResponse({}, status_code=404)
    record = await repo.get_subscription_by_subscription_id(sub_id_str)
    if not record or not record.flight_details or not json.loads(record.flight_details):
        return JSONResponse({}, status_code=404)

    from flight_blender.core.operations import flight_feed as flight_stream_helper

    observations = flight_stream_helper.ObservationReadOperations().get_temporal_flight_observations_by_session(session_id=sub_id_str)
    return JSONResponse(observations or {}, status_code=200 if observations else 404)


@router.post("/uss/identification_service_areas/{isa_id}", status_code=204)
async def dss_isa_callback(
    isa_id: uuid.UUID,
    body: ISACallbackBody,
    repo: SQLAlchemyRIDRepository = Depends(_rid_ops),
    _auth: Any = Depends(require_scopes(["dss.write.identification_service_areas"])),
):
    from dacite import from_dict

    updated_service_area = from_dict(IdentificationServiceArea, body.service_area) if body.service_area else None
    for _subscription in body.subscriptions:
        subscription = from_dict(SubscriptionState, _subscription)
        extents = from_dict(RIDVolume4D, body.extents) if body.extents else None
        if updated_service_area:
            sub_id = subscription.subscription_id
            existing_record = await repo.get_subscription_by_subscription_id(subscription_id=sub_id)
            if existing_record and existing_record.flight_details:
                existing_flight_details = json.loads(existing_record.flight_details)
                existing_subscription = from_dict(RIDSubscription, existing_flight_details["subscription"])
                updated_areas = [
                    updated_service_area if area["id"] == str(isa_id) else from_dict(IdentificationServiceArea, area)
                    for area in existing_flight_details["service_areas"]
                ]
                flights_record = RIDFlightsRecord(
                    service_areas=updated_areas,
                    subscription=existing_subscription,
                    extents=extents,
                )
                await repo.update_subscription_flight_details(
                    existing_record,
                    json.dumps(asdict(flights_record, dict_factory=lambda x: {k: v for (k, v) in x if v is not None})),
                )
    return Response(status_code=204)


@router.get("/display_data/{flight_id}")
async def get_flight_data(
    flight_id: uuid.UUID,
    repo: SQLAlchemyRIDRepository = Depends(_rid_ops),
    _auth: Any = Depends(require_scopes(["dss.read.identification_service_areas"])),
):
    fid_str = str(flight_id)
    if not await repo.check_flight_detail_exists(fid_str):
        return JSONResponse(asdict(FlightDetailsNotFoundMessage(message="The requested flight could not be found")), status_code=404)
    flight_details = await repo.get_flight_detail_by_id(fid_str)
    detail = RIDFlightDetails(
        id=str(flight_details.id),
        operator_id=flight_details.operator_id,
        operator_location=json.loads(flight_details.operator_location or "{}") or None,
        operation_description=flight_details.operation_description,
        auth_data=json.loads(flight_details.auth_data or "{}") or None,
        eu_classification=json.loads(flight_details.eu_classification or "{}") or None,
        uas_id=json.loads(flight_details.uas_id or "{}") or None,
    )
    return {
        "details": asdict(
            OperatorDetailsSuccessResponse(details=detail).details,
            dict_factory=lambda x: {k: v for (k, v) in x if v is not None},
        )
    }


@router.get("/display_data")
async def get_display_data(
    view: str | None = None,
    repo: SQLAlchemyRIDRepository = Depends(_rid_ops),
    feed_repo: SQLAlchemyFlightFeedRepository = Depends(_feed_ops),
    _auth: Any = Depends(require_scopes(["dss.read.identification_service_areas"])),
):

    from flight_blender.infrastructure.celery.tasks.rid import run_ussp_polling_for_rid
    from flight_blender.infrastructure.dss import rid as dss_rid_helper

    try:
        view_port = [float(i) for i in (view or "").split(",")]
    except Exception:
        return JSONResponse({"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}, status_code=400)

    view_port_valid = view_port_ops.check_view_port(view_port_coords=view_port)
    view_port_diagonal = view_port_ops.get_view_port_diagonal_length_kms(view_port_coords=view_port)
    if view_port_diagonal > 7:
        return JSONResponse(asdict(GenericErrorResponseMessage(message=f"The requested view {view} rectangle is too large")), status_code=413)
    if not view_port_valid:
        return JSONResponse({"message": "A incorrect view port bbox was provided"}, status_code=400)

    should_cluster = view_port_diagonal >= NetDetailsMaxDisplayAreaDiagonalKm
    box = shapely.geometry.box(view_port[1], view_port[0], view_port[3], view_port[2])

    request_id = str(uuid.uuid4())
    view_hash = _view_hash(view or "")
    import hashlib

    view_hash_int = int(hashlib.sha256((view or "").encode("utf-8")).hexdigest(), 16) % 10**8

    # Check if subscription exists by view hash — async SA
    from sqlalchemy import select

    from flight_blender.infrastructure.database.models.rid import ISASubscriptionORM

    result = await repo.db.execute(select(ISASubscriptionORM).where(ISASubscriptionORM.view_hash == view_hash_int))
    subscription_exists = result.scalar_one_or_none() is not None

    if not subscription_exists:
        subscription_duration_seconds = 20
        vertex_list = [{"lng": lng, "lat": lat} for lng, lat in list(zip(*box.exterior.coords.xy))[:-1]]
        subscription_end_time = arrow.utcnow().shift(seconds=subscription_duration_seconds).isoformat()
        await asyncio.to_thread(
            dss_rid_helper.RemoteIDOperations().create_dss_subscription,
            vertex_list,
            view or "",
            request_id,
            subscription_duration_seconds,
            True,
        )
        run_ussp_polling_for_rid.delay(session_id=request_id, end_time=subscription_end_time)

    now = arrow.utcnow()
    observations = await feed_repo.get_all_flight_observations_in_window(start_time=now.shift(seconds=-30).datetime, end_time=now.datetime)
    unique = {}
    for observation in observations or []:
        unique.setdefault(observation.icao_address, observation)

    rid_flights = []
    for observation in unique.values():
        recent_paths = []
        try:
            recent_positions = json.loads(observation.raw_metadata).get("recent_positions", [])
            recent_paths.append(
                RIDPositions(
                    positions=[Position(lat=p["position"]["lat"], lng=p["position"]["lng"], alt=p["position"]["alt"]) for p in recent_positions]
                )
            )
        except Exception as exc:
            logger.error("Error parsing recent_positions for {}: {}", observation.icao_address, exc)
            recent_paths = []
        rid_flights.append(
            RIDFlight(
                id=observation.icao_address,
                most_recent_position=Position(lat=observation.latitude_dd, lng=observation.longitude_dd, alt=observation.altitude_mm),
                recent_paths=recent_paths,
            )
        )

    clusters = []
    if should_cluster:
        clusters = dss_rid_helper.RemoteIDOperations().generate_cluster_details(rid_flights=rid_flights, view_box=box)
        rid_flights = []

    response = _make_json_compatible(RIDDisplayDataResponse(flights=rid_flights, clusters=clusters))
    return {"flights": response["flights"], "clusters": response["clusters"]}


@router.put("/tests/{test_id}")
async def create_test(test_id: uuid.UUID, body: CreateTestBody, _auth: Any = Depends(require_scopes(["rid.inject_test_data"]))):
    from flight_blender.infrastructure.celery.tasks.rid import stream_rid_test_data

    redis_key = "rid-test_" + str(test_id)
    redis_client = get_redis()
    if redis_client.exists(redis_key):
        return JSONResponse({}, status_code=409)
    redis_client.set(redis_key, json.dumps({"created_at": arrow.utcnow().isoformat()}))
    redis_client.expire(redis_key, 300)
    stream_rid_test_data.delay(requested_flights=json.dumps(body.requested_flights), test_id=redis_key)
    return asdict(CreateTestResponse(injected_flights=body.requested_flights, version=1))


@router.delete("/tests/{test_id}/{version}")
async def delete_test(
    test_id: uuid.UUID,
    version: str,
    repo: SQLAlchemyRIDRepository = Depends(_rid_ops),
    feed_repo: SQLAlchemyFlightFeedRepository = Depends(_feed_ops),
    _auth: Any = Depends(require_scopes(["rid.inject_test_data"])),
):
    from sqlalchemy import delete

    from flight_blender.infrastructure.database.models.flight_feed import FlightObservationORM

    redis_client = get_redis()
    test_id_str = str(test_id)
    if redis_client.exists(test_id_str):
        redis_client.delete(test_id_str)

    await repo.delete_simulated_subscriptions()
    await repo.delete_all_flight_details()
    # delete all flight observations
    await repo.db.execute(delete(FlightObservationORM))
    await repo.db.flush()

    redis_client.set("stop_streaming_" + test_id_str, "1")
    return {}


@router.get("/user_notifications")
async def user_notifications(
    after: str | None = None,
    before: str | None = None,
    repo: SQLAlchemyRIDRepository = Depends(_rid_ops),
    _auth: Any = Depends(require_scopes(["rid.inject_test_data"])),
):
    if not after or not before:
        return JSONResponse({"message": "Both 'after' and 'before' parameter is required."}, status_code=400)
    try:
        after_dt = arrow.get(after).datetime
        before_dt = arrow.get(before).datetime
    except Exception:
        return JSONResponse({"message": "Invalid date format. Use ISO 8601 format."}, status_code=400)

    notifications = await repo.get_active_notifications_between(after_dt, before_dt)
    return {"user_notifications": [{"message": n.message, "observed_at": {"value": n.created_at, "format": "RFC3339"}} for n in notifications]}


def _view_hash(view: str) -> int:
    import hashlib

    return int(hashlib.sha256(view.encode("utf-8")).hexdigest(), 16) % 10**8


def _make_json_compatible(struct):

    if isinstance(struct, tuple) and hasattr(struct, "_asdict"):
        return {k: _make_json_compatible(v) for k, v in struct._asdict().items()}
    if isinstance(struct, dict):
        return {k: _make_json_compatible(v) for k, v in struct.items()}
    if isinstance(struct, str):
        return struct
    try:
        return [_make_json_compatible(v) for v in struct]
    except TypeError:
        return struct
