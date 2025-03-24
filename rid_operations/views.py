import hashlib
import json
import logging
import time
import uuid
from dataclasses import asdict
from datetime import timedelta
from os import environ as env
from typing import Any
from uuid import UUID

import arrow
import shapely.geometry
from dacite import from_dict
from django.http import HttpResponse, JsonResponse
from dotenv import find_dotenv, load_dotenv
from implicitdict import ImplicitDict
from rest_framework.decorators import api_view
from uas_standards.astm.f3411.v22a.constants import NetDetailsMaxDisplayAreaDiagonalKm
from uas_standards.interuss.automated_testing.rid.v1.injection import (
    Time,
    UserNotification,
)

from auth_helper.common import get_redis
from auth_helper.utils import requires_scopes
from common.data_definitions import (
    FLIGHTBLENDER_READ_SCOPE,
    FLIGHTBLENDER_WRITE_SCOPE,
    RESPONSE_CONTENT_TYPE,
)
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from common.utils import EnhancedJSONEncoder
from flight_feed_operations import flight_stream_helper
from uss_operations.uss_data_definitions import (
    FlightDetailsNotFoundMessage,
    GenericErrorResponseMessage,
    OperatorDetailsSuccessResponse,
)

from . import dss_rid_helper, view_port_ops
from .data_definitions import LatLngPoint, ServiceProviderUserNotifications
from .rid_utils import (
    CreateSubscriptionResponse,
    CreateTestResponse,
    HTTPErrorResponse,
    IdentificationServiceArea,
    Position,
    RIDCapabilitiesResponse,
    RIDDisplayDataResponse,
    RIDFlight,
    RIDFlightDetails,
    RIDFlightsRecord,
    RIDOperatorDetails,
    RIDPositions,
    RIDSubscription,
    RIDVolume4D,
    SubscriptionResponse,
    SubscriptionState,
)
from .tasks import (
    run_ussp_polling_for_rid,
    stream_rid_test_data,
    write_operator_rid_notification,
)

load_dotenv(find_dotenv())
logger = logging.getLogger("django")


class RIDOutputHelper:
    def make_json_compatible(self, struct: Any) -> Any:
        if isinstance(struct, tuple) and hasattr(struct, "_asdict"):
            return {k: self.make_json_compatible(v) for k, v in struct._asdict().items()}
        elif isinstance(struct, dict):
            return {k: self.make_json_compatible(v) for k, v in struct.items()}
        elif isinstance(struct, str):
            return struct
        try:
            return [self.make_json_compatible(v) for v in struct]
        except TypeError:
            return struct


class SubscriptionsHelper:
    """
    A class to help with DSS subscriptions, check if a subscription exists or create a new one

    """

    def __init__(self):
        self.my_rid_output_helper = RIDOutputHelper()

    def get_view_hash(self, view) -> int:
        return int(hashlib.sha256(view.encode("utf-8")).hexdigest(), 16) % 10**8

    def check_subscription_exists(self, view) -> bool:
        my_database_reader = FlightBlenderDatabaseReader()
        view_hash = self.get_view_hash(view)
        subscription_found = my_database_reader.check_rid_subscription_record_by_view_hash_exists(view_hash=view_hash)

        return subscription_found

    def create_new_rid_subscription(
        self, request_id: str, subscription_duration_seconds: int, view: str, vertex_list: list, is_simulated: bool
    ) -> SubscriptionResponse:
        my_dss_subscriber = dss_rid_helper.RemoteIDOperations()
        subscription_r = my_dss_subscriber.create_dss_subscription(
            vertex_list=vertex_list,
            view=view,
            request_uuid=request_id,
            subscription_duration_seconds=subscription_duration_seconds,
            is_simulated=is_simulated,
        )
        subscription_response = self.my_rid_output_helper.make_json_compatible(subscription_r)
        return subscription_response

    def start_ussp_polling(self):
        """
        This method starts the polling of USSP once a subscription has been created
        """
        pass


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def get_rid_capabilities(request):
    status = RIDCapabilitiesResponse(capabilities=["ASTMRID2022"])
    return JsonResponse(json.loads(json.dumps(status, cls=EnhancedJSONEncoder)), status=200)


@api_view(["PUT"])
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def create_dss_subscription(request, *args, **kwargs):
    """This module takes a lat, lng box from Flight Spotlight and puts in a subscription to the DSS for the ISA"""

    my_rid_output_helper = RIDOutputHelper()
    try:
        view = request.query_params["view"]
        view_port = [float(i) for i in view.split(",")]
    except Exception:
        incorrect_parameters = {"message": "A view bounding box is necessary with four values: lat1,lng1,lat2,lng2."}
        return HttpResponse(json.dumps(incorrect_parameters), status=400)

    view_port_valid = view_port_ops.check_view_port(view_port_coords=view_port)

    if not view_port_valid:
        incorrect_parameters = {"message": "A view bounding box is necessary with four values: lat1,lng1,lat2,lng2."}
        return HttpResponse(json.dumps(incorrect_parameters), status=400)

    b = shapely.geometry.box(view_port[1], view_port[0], view_port[3], view_port[2])
    co_ordinates = list(zip(*b.exterior.coords.xy))

    # Convert bounds vertex list
    vertex_list = []
    for cur_co_ordinate in co_ordinates:
        lat_lng = {"lng": 0.0, "lat": 0.0}
        lat_lng["lng"] = cur_co_ordinate[0]
        lat_lng["lat"] = cur_co_ordinate[1]
        vertex_list.append(lat_lng)
    # remove the final point
    vertex_list.pop()

    request_id = str(uuid.uuid4())

    my_subscription_helper = SubscriptionsHelper()
    subscription_r = my_subscription_helper.create_new_rid_subscription(
        request_id=request_id, vertex_list=vertex_list, view=view, is_simulated=False, subscription_duration_seconds=30
    )

    if subscription_r.created:
        m = CreateSubscriptionResponse(
            message="DSS Subscription created",
            id=request_id,
            dss_subscription_response=subscription_r,
        )
        status = 201
        # run_ussp_polling_for_rid.delay()

    else:
        m = CreateSubscriptionResponse(
            message="Error in creating DSS Subscription, please check the log or contact your administrator.",
            id=request_id,
            dss_subscription_response=asdict(subscription_r),
        )
        m = {
            "message": "Error in creating DSS Subscription, please check the log or contact your administrator.",
            "id": request_id,
        }
        status = 400
    msg = my_rid_output_helper.make_json_compatible(m)
    return HttpResponse(json.dumps(msg), status=status, content_type=RESPONSE_CONTENT_TYPE)


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def get_rid_data(request, subscription_id):
    """This is the GET endpoint for remote id data given a DSS subscription id. Flight Blender will store flight URLs and every time the data is queried, it is mainly used by Flight Spotlight"""

    try:
        UUID(subscription_id, version=4)
    except ValueError:
        return HttpResponse(
            "Incorrect UUID passed in the parameters, please send a valid subscription ID",
            status=400,
            mimetype=RESPONSE_CONTENT_TYPE,
        )

    my_database_reader = FlightBlenderDatabaseReader()
    flights_dict = {}
    # Get the flights URL from the DSS and put it in
    # reasonably we won't have more than 500 subscriptions active
    subscription_record_exists = my_database_reader.check_rid_subscription_record_by_subscription_id_exists(subscription_id=subscription_id)

    if subscription_record_exists:
        subscription_record = my_database_reader.get_rid_subscription_record_by_subscription_id(subscription_id=subscription_id)
        flights_dict = json.loads(subscription_record.flight_details)
        logger.info("Sleeping 2 seconds..")
        time.sleep(2)

    if bool(flights_dict):
        # Get the last observation of the flight telemetry
        obs_helper = flight_stream_helper.ObservationReadOperations()
        all_flights_telemetry_data = obs_helper.get_flight_observations(session_id=subscription_id)
        # Get the latest telemetry

        if not all_flights_telemetry_data:
            logger.error("No telemetry data found for session_id {subscription_id}".format(subscription_id=subscription_id))
            return
        return HttpResponse(
            json.dumps(all_flights_telemetry_data),
            status=200,
            content_type=RESPONSE_CONTENT_TYPE,
        )
    else:
        return HttpResponse(json.dumps({}), status=404, content_type=RESPONSE_CONTENT_TYPE)


@api_view(["POST"])
@requires_scopes(["dss.write.identification_service_areas"])
def dss_isa_callback(request, isa_id):
    """This is the call back end point that other USSes in the DSS network call once a subscription is updated"""

    _service_area = request.data["service_area"] if "service_area" in request.data else None

    if _service_area:
        updated_service_area = from_dict(data_class=IdentificationServiceArea, data=request.data["service_area"])
    else:
        updated_service_area = None

    _subscriptions = request.data["subscriptions"]

    for _subscription in _subscriptions:
        subscription = from_dict(data_class=SubscriptionState, data=_subscription)

        if "extents" in request.data:
            extents = from_dict(data_class=RIDVolume4D, data=request.data["extents"])
        else:
            extents = None

        if updated_service_area:
            my_database_reader = FlightBlenderDatabaseReader()
            existing_subscription_record = my_database_reader.get_rid_subscription_record_by_subscription_id(
                subscription_id=subscription.subscription_id
            )

            existing_flight_details = json.loads(existing_subscription_record.flight_details)
            subscription = from_dict(data_class=RIDSubscription, data=existing_flight_details["subscription"])
            existing_service_areas = existing_flight_details["service_areas"]

            updated_service_areas_db = []
            for _exisiting_service_area in existing_service_areas:
                service_area = from_dict(data_class=IdentificationServiceArea, data=_exisiting_service_area)
                if _exisiting_service_area["id"] == isa_id:
                    updated_service_areas_db.append(updated_service_area)
                else:
                    updated_service_areas_db.append(service_area)

            flights_record = RIDFlightsRecord(service_areas=updated_service_areas_db, subscription=subscription, extents=extents)
            # Update flight details in the database
            my_database_writer = FlightBlenderDatabaseWriter()
            my_database_writer.update_flight_details_in_rid_subscription_record(
                existing_subscription_record=existing_subscription_record,
                flights_dict=json.dumps(asdict(flights_record, dict_factory=lambda x: {k: v for (k, v) in x if (v is not None)})),
            )

    return HttpResponse(status=204, content_type=RESPONSE_CONTENT_TYPE)


@api_view(["GET"])
@requires_scopes(["dss.read.identification_service_areas"])
def get_flight_data(request, flight_id):
    """This is the end point for the rid_qualifier to get details of a flight"""
    r = get_redis()
    flight_details_storage = "flight_details:" + str(flight_id)

    if r.exists(flight_details_storage):
        flight_details = r.get(flight_details_storage)

        flight_detail = from_dict(data_class=RIDFlightDetails, data=json.loads(flight_details))

        flight_details_full = OperatorDetailsSuccessResponse(details=flight_detail)
        flight_details_response = asdict(flight_details_full, dict_factory=lambda x: {k: v for (k, v) in x if (v is not None)})
        return JsonResponse(json.loads(json.dumps(flight_details_response)), status=200)
    else:
        fd = FlightDetailsNotFoundMessage(message="The requested flight could not be found")
        return JsonResponse(json.loads(json.dumps(asdict(fd))), status=404)


@api_view(["GET"])
@requires_scopes(["dss.read.identification_service_areas"])
def get_display_data(request):
    """This is the end point for the rid_qualifier test DSS network call once a subscription is updated"""

    # get the view bounding box
    # get the existing subscription id , if no subscription exists, then reject
    request_id = str(uuid.uuid4())
    my_rid_output_helper = RIDOutputHelper()
    my_database_reader = FlightBlenderDatabaseReader()
    try:
        view = request.query_params["view"]
        view_port = [float(i) for i in view.split(",")]
    except Exception:
        incorrect_parameters = {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}
        return HttpResponse(
            json.dumps(incorrect_parameters),
            status=400,
            content_type=RESPONSE_CONTENT_TYPE,
        )

    view_port_valid = view_port_ops.check_view_port(view_port_coords=view_port)

    view_port_diagonal = view_port_ops.get_view_port_diagonal_length_kms(view_port_coords=view_port)
    logger.info("********")
    logger.info("View port diagonal %s" % view_port_diagonal)
    logger.info("********")
    if (view_port_diagonal) > 7:
        view_port_too_large_msg = GenericErrorResponseMessage(message="The requested view %s rectangle is too large" % view)
        return JsonResponse(json.loads(json.dumps(asdict(view_port_too_large_msg))), status=413)
    should_cluster = True if view_port_diagonal >= NetDetailsMaxDisplayAreaDiagonalKm else False

    b = shapely.geometry.box(view_port[1], view_port[0], view_port[3], view_port[2])
    co_ordinates = list(zip(*b.exterior.coords.xy))

    vertex_list = []
    for cur_co_ordinate in co_ordinates:
        lat_lng = {"lng": 0, "lat": 0}
        lat_lng["lng"] = cur_co_ordinate[0]
        lat_lng["lat"] = cur_co_ordinate[1]
        vertex_list.append(lat_lng)
    # remove the final point
    vertex_list.pop()
    rid_flights = []

    if view_port_valid:
        # stream_id = hashlib.md5(view.encode('utf-8')).hexdigest()
        # create a subscription
        my_subscription_helper = SubscriptionsHelper()
        subscription_exists = my_subscription_helper.check_subscription_exists(view)
        view_hash = my_subscription_helper.get_view_hash(view)

        if not subscription_exists:
            subscription_duration_seconds = 20
            subscription_end_time = arrow.now().shift(seconds=subscription_duration_seconds).datetime
            logger.info("Creating Subscription till end time %s" % subscription_end_time)
            my_subscription_helper.create_new_rid_subscription(
                subscription_duration_seconds=subscription_duration_seconds,
                request_id=request_id,
                vertex_list=vertex_list,
                view=view,
                is_simulated=True,
            )

            run_ussp_polling_for_rid.delay(session_id=request_id, end_time=subscription_end_time)

            logger.info("Sleeping 2 seconds..")
            time.sleep(2)

        # Keep only the latest message

        # Get the last reading for view hash

        r = get_redis()
        key = "last_reading_for_view_hash_{view_hash}".format(view_hash=view_hash)
        if r.exists(key):
            last_reading_time = r.get(key)
            after_datetime = arrow.get(last_reading_time)
        else:
            now = arrow.now()
            one_second_before_now = now.shift(seconds=-1)
            after_datetime = one_second_before_now

        r.set(key, arrow.now().isoformat())
        r.expire(key, 300)

        distinct_messages = my_database_reader.get_active_rid_observations_for_view(start_time=after_datetime.datetime, end_time=arrow.now().datetime)
        logger.debug("Found %s distinct messages" % len(distinct_messages))

        distinct_messages = distinct_messages if distinct_messages else []
        unique_messages = {}
        for message in distinct_messages:
            if message.icao_address not in unique_messages:
                unique_messages[message.icao_address] = message
        distinct_messages = list(unique_messages.values())
        logger.debug("Found %s distinct messages" % len(distinct_messages))

        for observation_message in distinct_messages:
            all_recent_positions = []
            recent_paths = []
            try:
                observation_metadata = observation_message.metadata
                observation_metadata_dict = json.loads(observation_metadata)
                recent_positions = observation_metadata_dict["recent_positions"]

                for recent_position in recent_positions:
                    all_recent_positions.append(
                        Position(
                            lat=recent_position["position"]["lat"],
                            lng=recent_position["position"]["lng"],
                            alt=recent_position["position"]["alt"],
                        )
                    )

                recent_paths.append(RIDPositions(positions=all_recent_positions))

            except KeyError as ke:
                logger.error("Error in metadata data in the stream %s" % ke)

            most_recent_position = Position(
                lat=observation_message.latitude_dd,
                lng=observation_message.longitude_dd,
                alt=observation_message.altitude_mm,
            )

            current_flight = RIDFlight(
                id=observation_message.icao_address,
                most_recent_position=most_recent_position,
                recent_paths=recent_paths,
            )

            rid_flights.append(current_flight)

        # my_rid_helper = dss_rid_helper.RemoteIDOperations()
        # if should_cluster:
        #     clusters = my_rid_helper.generate_cluster_details(rid_flights=rid_flights, view_box=b)
        # else:
        #     clusters = []
        rid_display_data = RIDDisplayDataResponse(flights=rid_flights, clusters=[])

        rid_flights_dict = my_rid_output_helper.make_json_compatible(rid_display_data)

        return JsonResponse(
            {
                "flights": rid_flights_dict["flights"],
                "clusters": rid_flights_dict["clusters"],
            },
            status=200,
            content_type=RESPONSE_CONTENT_TYPE,
        )
    else:
        view_port_error = {"message": "A incorrect view port bbox was provided"}
        return JsonResponse(view_port_error, status=400, content_type=RESPONSE_CONTENT_TYPE)


@api_view(["PUT"])
@requires_scopes(["rid.inject_test_data"])
def create_test(request, test_id):
    """This is the end point for the rid_qualifier to get details of a flight"""

    rid_qualifier_payload = request.data

    try:
        requested_flights = rid_qualifier_payload["requested_flights"]
    except KeyError:
        msg = HTTPErrorResponse(message="Requested Flights not present in the payload", status=400)
        msg_dict = asdict(msg)
        return JsonResponse(msg_dict["message"], status=msg_dict["status"])

    r = get_redis()

    test_id = "rid-test_" + str(test_id)
    # Test already exists
    if r.exists(test_id):
        return JsonResponse({}, status=409)
    else:
        # Create a ISA in the DSS
        now = arrow.now()
        r.set(test_id, json.dumps({"created_at": now.isoformat()}))
        r.expire(test_id, timedelta(seconds=300))

        stream_rid_test_data.delay(requested_flights=json.dumps(requested_flights), test_id=str(test_id))  # Send a job to the task queue

    create_test_response = CreateTestResponse(injected_flights=requested_flights, version=1)

    return JsonResponse(asdict(create_test_response), status=200)


@api_view(["DELETE"])
@requires_scopes(["rid.inject_test_data"])
def delete_test(request, test_id, version):
    """This is the end point for the rid_qualifier to get details of a flight"""
    # Deleting test
    test_id_str = str(test_id)
    r = get_redis()

    if r.exists(test_id_str):
        r.delete(test_id_str)

    my_database_writer = FlightBlenderDatabaseWriter()
    my_database_writer.delete_all_simulated_rid_subscription_records()

    # Stop streaming if it exists for this test
    r.set("stop_streaming_" + test_id_str, "1")

    return JsonResponse({}, status=200)


@api_view(["GET"])
@requires_scopes(["rid.inject_test_data"])
def user_notifications(request):
    try:
        after_datetime = request.query_params["after"]
        before_datetime = request.query_params["before"]
    except KeyError:
        return HttpResponse(
            json.dumps({"message": "Both 'after' and 'before' parameter is required."}),
            status=400,
            content_type=RESPONSE_CONTENT_TYPE,
        )
    all_user_notifications = []
    after_datetime = arrow.get(after_datetime).datetime
    before_datetime = arrow.get(before_datetime).datetime
    my_database_reader = FlightBlenderDatabaseReader()
    all_user_notifications = my_database_reader.get_active_user_notifications_between_interval(start_time=after_datetime, end_time=before_datetime)
    logger.debug(f"Found {len(all_user_notifications)} user notifications..")
    all_notifications = []
    for user_notification in all_user_notifications:
        time = ImplicitDict.parse({"value": user_notification.created_at, "format": "RFC3339"}, Time)
        user_notification = ImplicitDict.parse({"message": user_notification.message, "observed_at": time}, UserNotification)
        all_notifications.append(user_notification)

    user_notifications = ImplicitDict.parse({"user_notifications": all_notifications}, ServiceProviderUserNotifications)

    return JsonResponse(user_notifications, status=200)
