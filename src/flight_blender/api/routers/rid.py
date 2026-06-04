import json
import uuid
from dataclasses import asdict
from typing import Any

import arrow
import shapely.geometry
from asgiref.sync import sync_to_async
from dacite import from_dict
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, Response
from loguru import logger
from uas_standards.astm.f3411.v22a.constants import NetDetailsMaxDisplayAreaDiagonalKm

from flight_blender.api.dependencies import require_scopes
from flight_blender.api.schemas.rid import CreateTestBody, ISACallbackBody
from flight_blender.auth.common import get_redis
from flight_blender.common.data_definitions import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from flight_blender.common.database_operations import FlightBlenderDatabaseReader, FlightBlenderDatabaseWriter
from flight_blender.flight_feed import flight_stream_helper
from flight_blender.rid import dss_rid_helper, view_port_ops
from flight_blender.rid.rid_utils import (
    CreateTestResponse,
    IdentificationServiceArea,
    Position,
    RIDDisplayDataResponse,
    RIDFlight,
    RIDFlightDetails,
    RIDFlightsRecord,
    RIDPositions,
    RIDSubscription,
    RIDVolume4D,
    SubscriptionState,
)
from flight_blender.rid.tasks import run_ussp_polling_for_rid, stream_rid_test_data
from flight_blender.rid.views import RIDOutputHelper, SubscriptionsHelper
from flight_blender.uss.uss_data_definitions import FlightDetailsNotFoundMessage, GenericErrorResponseMessage, OperatorDetailsSuccessResponse

router = APIRouter()


@router.get("/capabilities")
async def get_rid_capabilities(_auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE]))):
    return {"capabilities": ["ASTMRID2022"]}


@router.put("/create_dss_subscription")
async def create_dss_subscription(view: str | None = None, _auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE]))):
    try:
        view_port = [float(i) for i in (view or "").split(",")]
    except Exception:
        return JSONResponse({"message": "A view bounding box is necessary with four values: lat1,lng1,lat2,lng2."}, status_code=400)
    if not view_port_ops.check_view_port(view_port_coords=view_port):
        return JSONResponse({"message": "A view bounding box is necessary with four values: lat1,lng1,lat2,lng2."}, status_code=400)

    box = shapely.geometry.box(view_port[1], view_port[0], view_port[3], view_port[2])
    vertex_list = [{"lng": lng, "lat": lat} for lng, lat in list(zip(*box.exterior.coords.xy))[:-1]]
    request_id = str(uuid.uuid4())
    subscription_r = await sync_to_async(SubscriptionsHelper().create_new_rid_subscription)(
        request_id=request_id,
        vertex_list=vertex_list,
        view=view or "",
        is_simulated=False,
        subscription_duration_seconds=30,
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
async def get_rid_data(subscription_id: uuid.UUID, _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE]))):
    sub_id_str = str(subscription_id)

    def _fetch():
        reader = FlightBlenderDatabaseReader()
        if not reader.check_rid_subscription_record_by_subscription_id_exists(subscription_id=sub_id_str):
            return None
        record = reader.get_rid_subscription_record_by_subscription_id(subscription_id=sub_id_str)
        return record.flight_details if json.loads(record.flight_details) else None

    flight_details = await sync_to_async(_fetch)()
    if flight_details is None:
        return JSONResponse({}, status_code=404)

    observations = await sync_to_async(
        flight_stream_helper.ObservationReadOperations().get_temporal_flight_observations_by_session
    )(session_id=sub_id_str)
    return JSONResponse(observations or {}, status_code=200 if observations else 404)


@router.post("/uss/identification_service_areas/{isa_id}", status_code=204)
async def dss_isa_callback(
    isa_id: uuid.UUID,
    body: ISACallbackBody,
    _auth: Any = Depends(require_scopes(["dss.write.identification_service_areas"])),
):
    updated_service_area = from_dict(IdentificationServiceArea, body.service_area) if body.service_area else None
    for _subscription in body.subscriptions:
        subscription = from_dict(SubscriptionState, _subscription)
        extents = from_dict(RIDVolume4D, body.extents) if body.extents else None
        if updated_service_area:
            def _update(sub_id=subscription.subscription_id, sa=updated_service_area, isa=str(isa_id), ext=extents):
                reader = FlightBlenderDatabaseReader()
                existing_record = reader.get_rid_subscription_record_by_subscription_id(subscription_id=sub_id)
                existing_flight_details = json.loads(existing_record.flight_details)
                existing_subscription = from_dict(RIDSubscription, existing_flight_details["subscription"])
                updated_areas = [
                    sa if area["id"] == isa else from_dict(IdentificationServiceArea, area)
                    for area in existing_flight_details["service_areas"]
                ]
                flights_record = RIDFlightsRecord(
                    service_areas=updated_areas,
                    subscription=existing_subscription,
                    extents=ext,
                )
                FlightBlenderDatabaseWriter().update_flight_details_in_rid_subscription_record(
                    existing_subscription_record=existing_record,
                    flights_dict=json.dumps(
                        asdict(flights_record, dict_factory=lambda x: {k: v for (k, v) in x if v is not None})
                    ),
                )

            await sync_to_async(_update)()
    return Response(status_code=204)


@router.get("/display_data/{flight_id}")
async def get_flight_data(flight_id: uuid.UUID, _auth: Any = Depends(require_scopes(["dss.read.identification_service_areas"]))):
    fid_str = str(flight_id)

    def _fetch():
        reader = FlightBlenderDatabaseReader()
        if not reader.check_flight_details_exist(flight_detail_id=fid_str):
            return None
        return reader.get_flight_details_by_id(flight_detail_id=fid_str)

    flight_details = await sync_to_async(_fetch)()
    if flight_details is None:
        return JSONResponse(asdict(FlightDetailsNotFoundMessage(message="The requested flight could not be found")), status_code=404)

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
async def get_display_data(view: str | None = None, _auth: Any = Depends(require_scopes(["dss.read.identification_service_areas"]))):
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
    subscription_helper = SubscriptionsHelper()
    subscription_exists = await sync_to_async(subscription_helper.check_subscription_exists)(view)
    if not subscription_exists:
        subscription_duration_seconds = 20
        vertex_list = [{"lng": lng, "lat": lat} for lng, lat in list(zip(*box.exterior.coords.xy))[:-1]]
        subscription_end_time = arrow.utcnow().shift(seconds=subscription_duration_seconds).isoformat()
        await sync_to_async(subscription_helper.create_new_rid_subscription)(
            subscription_duration_seconds=subscription_duration_seconds,
            request_id=request_id,
            vertex_list=vertex_list,
            view=view or "",
            is_simulated=True,
        )
        run_ussp_polling_for_rid.delay(session_id=request_id, end_time=subscription_end_time)

    def _fetch_observations():
        now = arrow.utcnow()
        return list(
            FlightBlenderDatabaseReader().get_active_rid_observations_for_view(
                start_time=now.shift(seconds=-30).datetime,
                end_time=now.datetime,
            ) or []
        )

    observations = await sync_to_async(_fetch_observations)()
    unique = {}
    for observation in observations or []:
        unique.setdefault(observation.icao_address, observation)

    rid_flights = []
    for observation in unique.values():
        recent_paths = []
        try:
            recent_positions = json.loads(observation.metadata).get("recent_positions", [])
            recent_paths.append(
                RIDPositions(
                    positions=[
                        Position(lat=p["position"]["lat"], lng=p["position"]["lng"], alt=p["position"]["alt"])
                        for p in recent_positions
                    ]
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
    response = RIDOutputHelper().make_json_compatible(RIDDisplayDataResponse(flights=rid_flights, clusters=clusters))
    return {"flights": response["flights"], "clusters": response["clusters"]}


@router.put("/tests/{test_id}")
async def create_test(test_id: uuid.UUID, body: CreateTestBody, _auth: Any = Depends(require_scopes(["rid.inject_test_data"]))):
    redis_key = "rid-test_" + str(test_id)
    redis_client = get_redis()
    if redis_client.exists(redis_key):
        return JSONResponse({}, status_code=409)
    redis_client.set(redis_key, json.dumps({"created_at": arrow.utcnow().isoformat()}))
    redis_client.expire(redis_key, 300)
    stream_rid_test_data.delay(requested_flights=json.dumps(body.requested_flights), test_id=redis_key)
    return asdict(CreateTestResponse(injected_flights=body.requested_flights, version=1))


@router.delete("/tests/{test_id}/{version}")
async def delete_test(test_id: uuid.UUID, version: str, _auth: Any = Depends(require_scopes(["rid.inject_test_data"]))):
    redis_client = get_redis()
    test_id_str = str(test_id)
    if redis_client.exists(test_id_str):
        redis_client.delete(test_id_str)

    def _cleanup():
        writer = FlightBlenderDatabaseWriter()
        writer.delete_all_simulated_rid_subscription_records()
        writer.delete_all_flight_observations()
        writer.delete_all_flight_details()

    await sync_to_async(_cleanup)()
    redis_client.set("stop_streaming_" + test_id_str, "1")
    return {}


@router.get("/user_notifications")
async def user_notifications(
    after: str | None = None,
    before: str | None = None,
    _auth: Any = Depends(require_scopes(["rid.inject_test_data"])),
):
    if not after or not before:
        return JSONResponse({"message": "Both 'after' and 'before' parameter is required."}, status_code=400)
    try:
        after_dt = arrow.get(after).datetime
        before_dt = arrow.get(before).datetime
    except Exception:
        return JSONResponse({"message": "Invalid date format. Use ISO 8601 format."}, status_code=400)

    def _fetch_notifications():
        result = FlightBlenderDatabaseReader().get_active_user_notifications_between_interval(
            start_time=after_dt, end_time=before_dt
        )
        return list(result) if result else []

    notifications = await sync_to_async(_fetch_notifications)()
    return {
        "user_notifications": [
            {"message": n.message, "observed_at": {"value": n.created_at, "format": "RFC3339"}}
            for n in notifications
        ]
    }
