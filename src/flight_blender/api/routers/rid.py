import json
import uuid
from dataclasses import asdict
from typing import Any

import arrow
import shapely.geometry
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from uas_standards.astm.f3411.v22a.constants import NetDetailsMaxDisplayAreaDiagonalKm

from flight_blender.api.dependencies import require_scopes
from flight_blender.auth.common import get_redis
from flight_blender.common.data_definitions import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from flight_blender.common.database_operations import FlightBlenderDatabaseReader, FlightBlenderDatabaseWriter
from flight_blender.flight_feed import flight_stream_helper
from flight_blender.rid import dss_rid_helper, view_port_ops
from flight_blender.rid.rid_utils import (
    CreateTestResponse,
    HTTPErrorResponse,
    Position,
    RIDDisplayDataResponse,
    RIDFlight,
    RIDFlightDetails,
    RIDPositions,
)
from flight_blender.rid.tasks import stream_rid_test_data
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
    subscription_r = SubscriptionsHelper().create_new_rid_subscription(
        request_id=request_id,
        vertex_list=vertex_list,
        view=view,
        is_simulated=False,
        subscription_duration_seconds=30,
    )
    if subscription_r.created:
        return JSONResponse(
            {
                "message": "DSS Subscription created",
                "id": request_id,
                "dss_subscription_response": RIDOutputHelper().make_json_compatible(subscription_r),
            },
            status_code=201,
        )
    return JSONResponse(
        {"message": "Error in creating DSS Subscription, please check the log or contact your administrator.", "id": request_id},
        status_code=400,
    )


@router.get("/get_rid_data/{subscription_id}")
async def get_rid_data(subscription_id: uuid.UUID, _auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE]))):
    reader = FlightBlenderDatabaseReader()
    subscription_id_str = str(subscription_id)
    if not reader.check_rid_subscription_record_by_subscription_id_exists(subscription_id=subscription_id_str):
        return JSONResponse({}, status_code=404)
    record = reader.get_rid_subscription_record_by_subscription_id(subscription_id=subscription_id_str)
    if not json.loads(record.flight_details):
        return JSONResponse({}, status_code=404)
    observations = flight_stream_helper.ObservationReadOperations().get_temporal_flight_observations_by_session(session_id=subscription_id_str)
    return JSONResponse(observations or {}, status_code=200 if observations else 404)


@router.post("/uss/identification_service_areas/{isa_id}", status_code=204)
async def dss_isa_callback(
    isa_id: uuid.UUID,
    request: Request,
    _auth: Any = Depends(require_scopes(["dss.write.identification_service_areas"])),
):
    # Preserve the DSS callback contract; full subscription mutation remains in the Django view.
    await request.json()
    return Response(status_code=204)


@router.get("/display_data/{flight_id}")
async def get_flight_data(flight_id: uuid.UUID, _auth: Any = Depends(require_scopes(["dss.read.identification_service_areas"]))):
    reader = FlightBlenderDatabaseReader()
    if not reader.check_flight_details_exist(flight_detail_id=str(flight_id)):
        return JSONResponse(asdict(FlightDetailsNotFoundMessage(message="The requested flight could not be found")), status_code=404)
    flight_details = reader.get_flight_details_by_id(flight_detail_id=str(flight_id))
    detail = RIDFlightDetails(
        id=str(flight_details.id),
        operator_id=flight_details.operator_id,
        operator_location=json.loads(flight_details.operator_location or "{}") or None,
        operation_description=flight_details.operation_description,
        auth_data=json.loads(flight_details.auth_data or "{}") or None,
        eu_classification=json.loads(flight_details.eu_classification or "{}") or None,
        uas_id=json.loads(flight_details.uas_id or "{}") or None,
    )
    return {"details": asdict(OperatorDetailsSuccessResponse(details=detail).details)}


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
    observations = FlightBlenderDatabaseReader().get_active_rid_observations_for_view(
        start_time=arrow.utcnow().shift(seconds=-30).datetime,
        end_time=arrow.utcnow().datetime,
    )
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
        except Exception:
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
        box = shapely.geometry.box(view_port[1], view_port[0], view_port[3], view_port[2])
        clusters = dss_rid_helper.RemoteIDOperations().generate_cluster_details(rid_flights=rid_flights, view_box=box)
        rid_flights = []
    response = RIDOutputHelper().make_json_compatible(RIDDisplayDataResponse(flights=rid_flights, clusters=clusters))
    return {"flights": response["flights"], "clusters": response["clusters"]}


@router.put("/tests/{test_id}")
async def create_test(test_id: uuid.UUID, request: Request, _auth: Any = Depends(require_scopes(["rid.inject_test_data"]))):
    payload = await request.json()
    try:
        requested_flights = payload["requested_flights"]
    except KeyError:
        msg = HTTPErrorResponse(message="Requested Flights not present in the payload", status=400)
        return JSONResponse(asdict(msg)["message"], status_code=msg.status)
    redis_key = "rid-test_" + str(test_id)
    redis_client = get_redis()
    if redis_client.exists(redis_key):
        return JSONResponse({}, status_code=409)
    redis_client.set(redis_key, json.dumps({"created_at": arrow.utcnow().isoformat()}))
    redis_client.expire(redis_key, 300)
    stream_rid_test_data.delay(requested_flights=json.dumps(requested_flights), test_id=redis_key)
    return asdict(CreateTestResponse(injected_flights=requested_flights, version=1))


@router.delete("/tests/{test_id}/{version}")
async def delete_test(test_id: uuid.UUID, version: str, _auth: Any = Depends(require_scopes(["rid.inject_test_data"]))):
    redis_client = get_redis()
    test_id_str = str(test_id)
    if redis_client.exists(test_id_str):
        redis_client.delete(test_id_str)
    writer = FlightBlenderDatabaseWriter()
    writer.delete_all_simulated_rid_subscription_records()
    writer.delete_all_flight_observations()
    writer.delete_all_flight_details()
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
    notifications = FlightBlenderDatabaseReader().get_active_user_notifications_between_interval(
        start_time=arrow.get(after).datetime,
        end_time=arrow.get(before).datetime,
    )
    return {
        "user_notifications": [
            {"message": n.message, "observed_at": {"value": n.created_at, "format": "RFC3339"}} for n in notifications
        ]
    }
