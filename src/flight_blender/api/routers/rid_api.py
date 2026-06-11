import asyncio
import json
import uuid
from dataclasses import asdict
from typing import Any

import arrow
from dacite import from_dict
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy import delete
from uas_standards.astm.f3411.v22a.constants import NetDetailsMaxDisplayAreaDiagonalKm

from flight_blender.api.dependencies import require_scopes
from flight_blender.auth.token_cache import get_redis
from flight_blender.clients import dss_rid_client as dss_rid_helper
from flight_blender.clients.dss_rid_client import RemoteIDOperations
from flight_blender.clients.redis_client import RedisStreamOperations
from flight_blender.db.session import async_get_db
from flight_blender.domain_types.common import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from flight_blender.domain_types.rid_operations import (
    IdentificationServiceArea,
    RIDDisplayDataResponse,
    RIDFlightsRecord,
    RIDSubscription,
    RIDVolume4D,
    SubscriptionState,
)
from flight_blender.domain_types.uss import GenericErrorResponseMessage, OperatorDetailsSuccessResponse, RIDFlightDetails
from flight_blender.models.flight_feed_orm import FlightObservationORM
from flight_blender.repositories.flight_feed_repo import SQLAlchemyFlightFeedRepository
from flight_blender.repositories.rid_repo import SQLAlchemyRIDRepository
from flight_blender.schemas.rid import CreateTestBody, ISACallbackBody
from flight_blender.schemas.scd import NotificationObservedAtSchema, UserNotificationSchema, UserNotificationsResponseSchema
from flight_blender.services import rid_svc as view_port_ops
from flight_blender.services.flight_feed_svc import ObservationReadOperations
from flight_blender.services.rid_svc import CreateTestResponse
from flight_blender.tasks import rid_task

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
    repo: SQLAlchemyRIDRepository = Depends(_rid_ops),
    feed_repo: SQLAlchemyFlightFeedRepository = Depends(_feed_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    try:
        view_port = [float(i) for i in (view or "").split(",")]
    except Exception:
        return JSONResponse({"message": "A view bounding box is necessary with four values: lat1,lng1,lat2,lng2."}, status_code=400)
    if not view_port_ops.check_view_port(view_port_coords=view_port):
        return JSONResponse({"message": "A view bounding box is necessary with four values: lat1,lng1,lat2,lng2."}, status_code=400)

    box = view_port_ops.build_view_port_box_lng_lat(view_port_coords=view_port)
    vertex_list = view_port_ops.build_vertex_list_from_box(box)
    request_id = str(uuid.uuid4())

    subscription_r = await asyncio.to_thread(
        RemoteIDOperations.create_dss_subscription,
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
    feed_repo: SQLAlchemyFlightFeedRepository = Depends(_feed_ops),
    _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    sub_id_str = str(subscription_id)
    if not await repo.check_subscription_exists_by_subscription_id(sub_id_str):
        return JSONResponse({}, status_code=404)
    record = await repo.get_subscription_by_subscription_id(sub_id_str)
    if not record or not record.flight_details or not json.loads(record.flight_details):
        return JSONResponse({}, status_code=404)

    observations = await ObservationReadOperations(repo=feed_repo, redis=get_redis()).get_temporal_flight_observations_by_session(
        session_id=sub_id_str
    )
    return JSONResponse(observations or {}, status_code=200 if observations else 404)


@router.post("/uss/identification_service_areas/{isa_id}", status_code=204)
async def dss_isa_callback(
    isa_id: uuid.UUID,
    body: ISACallbackBody,
    repo: SQLAlchemyRIDRepository = Depends(_rid_ops),
    _auth: Any = Depends(require_scopes(["dss.write.identification_service_areas"])),
):
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
    flight_details = await repo.get_flight_detail_by_id(flight_id)
    if not flight_details:
        raise HTTPException(status_code=404, detail="The requested flight could not be found")
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
    view_port = view_port_ops.parse_view_bbox(view)
    if not view_port:
        return JSONResponse({"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}, status_code=400)

    view_port_valid = view_port_ops.check_view_port(view_port_coords=view_port)
    view_port_diagonal = view_port_ops.get_view_port_diagonal_length_kms(view_port_coords=view_port)
    if view_port_diagonal > 7:
        return JSONResponse(asdict(GenericErrorResponseMessage(message=f"The requested view {view} rectangle is too large")), status_code=413)
    if not view_port_valid:
        return JSONResponse({"message": "A incorrect view port bbox was provided"}, status_code=400)

    should_cluster = view_port_diagonal >= NetDetailsMaxDisplayAreaDiagonalKm
    box = view_port_ops.build_view_port_box_lng_lat(view_port_coords=view_port)

    request_id = str(uuid.uuid4())
    view_hash_int = view_port_ops.compute_view_hash(view or "")
    subscription_exists = await repo.check_subscription_exists_by_view_hash(view_hash_int)

    if not subscription_exists:
        subscription_duration_seconds = 20
        vertex_list = view_port_ops.build_vertex_list_from_box(box)
        subscription_end_time = arrow.utcnow().shift(seconds=subscription_duration_seconds).isoformat()
        await asyncio.to_thread(
            dss_rid_helper.RemoteIDOperations.create_dss_subscription,
            vertex_list,
            view or "",
            request_id,
            subscription_duration_seconds,
            True,
        )
        rid_task.run_ussp_polling_for_rid.delay(session_id=request_id, end_time=subscription_end_time)

    now = arrow.utcnow()
    observations = await feed_repo.get_all_flight_observations_in_window(start_time=now.shift(seconds=-30).datetime, end_time=now.datetime)
    unique = view_port_ops.deduplicate_observations_by_icao(observations)

    rid_flights = [view_port_ops.rid_flight_from_observation(obs) for obs in unique.values()]

    clusters = []
    if should_cluster:
        clusters = dss_rid_helper.RemoteIDOperations(rid_repo=repo, feed_repo=feed_repo).generate_cluster_details(
            rid_flights=rid_flights, view_box=box
        )
        rid_flights = []

    response = view_port_ops.make_json_compatible(RIDDisplayDataResponse(flights=rid_flights, clusters=clusters))
    return {"flights": response["flights"], "clusters": response["clusters"]}


@router.put("/tests/{test_id}")
async def create_test(test_id: uuid.UUID, body: CreateTestBody, _auth: Any = Depends(require_scopes(["rid.inject_test_data"]))):
    redis_key = "rid-test_" + str(test_id)
    redis_ops = RedisStreamOperations()
    if not redis_ops.register_rid_test(redis_key):
        return JSONResponse({}, status_code=409)
    rid_task.stream_rid_test_data.delay(requested_flights=json.dumps(body.requested_flights), test_id=redis_key)
    return asdict(CreateTestResponse(injected_flights=body.requested_flights, version=1))


@router.delete("/tests/{test_id}/{version}")
async def delete_test(
    test_id: uuid.UUID,
    version: str,
    repo: SQLAlchemyRIDRepository = Depends(_rid_ops),
    _auth: Any = Depends(require_scopes(["rid.inject_test_data"])),
):
    test_id_str = str(test_id)
    redis_ops = RedisStreamOperations()
    redis_ops.delete_rid_test_key(test_id_str)

    await repo.delete_simulated_subscriptions()
    await repo.delete_all_flight_details()
    await repo.db.execute(delete(FlightObservationORM))
    await repo.db.flush()

    redis_ops.stop_rid_test_stream(test_id_str)
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
    return UserNotificationsResponseSchema(
        user_notifications=[
            UserNotificationSchema(
                message=n.message,
                observed_at=NotificationObservedAtSchema(value=n.created_at.isoformat(), format="RFC3339"),
            )
            for n in notifications
        ]
    )
