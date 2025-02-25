import json
import logging
import time
from dataclasses import asdict
from datetime import timedelta
from itertools import cycle, islice
from os import environ as env
from typing import List

import arrow
from dotenv import find_dotenv, load_dotenv
from .rid_telemetry_monitoring import FlightTelemetryRIDEngine
from shapely.geometry import MultiPoint, Point, box

from auth_helper.common import get_redis
from common.database_operations import FlightBlenderDatabaseWriter
from flight_blender.celery import app
from flight_feed_operations import flight_stream_helper
from flight_feed_operations.data_definitions import SingleRIDObservation
from flight_feed_operations.tasks import write_incoming_air_traffic_data
from rid_operations.data_definitions import UASID, SignedUnsignedTelemetryObservation, OperatorRIDNotificationCreationPayload

from . import dss_rid_helper
from .rid_utils import (
    LatLngPoint,
    RIDAltitude,
    RIDPolygon,
    RIDTestInjection,
    RIDTime,
    RIDVolume3D,
    RIDVolume4D,
    SingleObservationMetadata,
    process_requested_flight,
)

logger = logging.getLogger("django")

load_dotenv(find_dotenv())


@app.task(name="submit_dss_subscription")
def submit_dss_subscription(view, vertex_list, request_uuid):
    subscription_time_delta = 30
    myDSSSubscriber = dss_rid_helper.RemoteIDOperations()
    subscription_created = myDSSSubscriber.create_dss_subscription(
        vertex_list=vertex_list,
        view=view,
        request_uuid=request_uuid,
        subscription_time_delta=subscription_time_delta,
    )
    logger.info("Subscription creation status: %s" % subscription_created.created)


@app.task(name="run_ussp_polling_for_rid")
def run_ussp_polling_for_rid():
    """This method is a wrapper for repeated polling of UTMSPs for Network RID information"""
    logger.debug("Starting USSP polling.. ")
    # Define start and end time

    async_polling_lock = "async_polling_lock"  # This
    r = get_redis()

    if r.exists(async_polling_lock):
        logger.info("Polling is ongoing, not setting additional polling tasks..")
    else:
        logger.info("Setting Polling Lock..")

        r.set(async_polling_lock, "1")
        r.expire(async_polling_lock, timedelta(minutes=5))

        for k in range(120):
            poll_uss_for_flights_async.apply_async(expires=2)
            time.sleep(2)

        r.delete(async_polling_lock)

    logger.debug("Finishing USSP polling..")


@app.task(name="poll_uss_for_flights_async")
def poll_uss_for_flights_async():
    myDSSSubscriber = dss_rid_helper.RemoteIDOperations()

    stream_ops = flight_stream_helper.StreamHelperOps()
    pull_cg = stream_ops.get_pull_cg()
    all_observations = pull_cg.all_observations

    # TODO: Get existing flight details from subscription
    r = get_redis()
    flights_dict = {}
    # Get the flights URL from the DSS and put it in
    for keybatch in flight_stream_helper.batcher(
        r.scan_iter("all_uss_flights:*"), 100
    ):  # reasonably we won't have more than 100 subscriptions active
        key_batch_set = set(keybatch)
        for key in key_batch_set:
            if key:
                flights_dict = r.hgetall(key)
                logger.debug("Flights Dict %s" % flights_dict)
                if bool(flights_dict):
                    subscription_id = key.split(":")[1]
                    myDSSSubscriber.query_uss_for_rid(flights_dict, all_observations, subscription_id)


@app.task(name="stream_rid_telemetry_data")
def stream_rid_telemetry_data(rid_telemetry_observations):
    my_database_writer = FlightBlenderDatabaseWriter()
    telemetry_observations = json.loads(rid_telemetry_observations)

    for observation in telemetry_observations:
        flight_details = observation["flight_details"]
        current_states = observation["current_states"]
        operation_id = flight_details["id"]
        # Update telemetry received timestamp
        my_database_writer.update_telemetry_timestamp(flight_declaration_id=operation_id)

        for current_state in current_states:
            observation_and_metadata = SignedUnsignedTelemetryObservation(current_state=current_state, flight_details=flight_details)

            flight_details_id = flight_details["uas_id"]["serial_number"]
            lat_dd = current_state["position"]["lat"]
            lon_dd = current_state["position"]["lng"]
            altitude_mm = current_state["position"]["alt"]
            traffic_source = 11  # Per the Air-traffic data protocol a source type of 11 means that the data is associated with RID observations
            source_type = 0
            icao_address = flight_details_id

            so = SingleRIDObservation(
                lat_dd=lat_dd,
                lon_dd=lon_dd,
                altitude_mm=altitude_mm,
                traffic_source=traffic_source,
                source_type=source_type,
                icao_address=icao_address,
                metadata=json.dumps(asdict(observation_and_metadata)),
            )
            write_incoming_air_traffic_data.delay(json.dumps(asdict(so)))  # Send a job to the task queue
            logger.debug("Submitted observation..")
            logger.debug("...")


@app.task(name="stream_rid_test_data")
def stream_rid_test_data(requested_flights, test_id):
    test_id = test_id.split('_')[1]
    all_requested_flights: List[RIDTestInjection] = []
    rf = json.loads(requested_flights)

    my_database_writer = FlightBlenderDatabaseWriter()

    all_positions: List[LatLngPoint] = []

    flight_injection_sorted_set = "requested_flight_ss"
    r = get_redis()

    if r.exists(flight_injection_sorted_set):
        r.delete(flight_injection_sorted_set)
    # Iterate over requested flights and process for storage / querying

    all_altitudes = []

    for requested_flight in rf:
        processed_flight, _all_positions, _all_altitudes = process_requested_flight(requested_flight=requested_flight, flight_injection_sorted_set = flight_injection_sorted_set)
        all_positions.extend(_all_positions)
        all_altitudes.extend(_all_altitudes)

        all_requested_flights.append(processed_flight)

    start_time_of_injection_list = r.zrange(flight_injection_sorted_set, 0, 0, withscores=True)
    start_time_of_injections = arrow.get(start_time_of_injection_list[0][1])

    # Computing when the requested flight data will end
    end_time_of_injection_list = r.zrevrange(flight_injection_sorted_set, 0, 0, withscores=True)
    end_time_of_injections = arrow.get(end_time_of_injection_list[0][1])

    logger.info("Provided Telemetry Starts at %s" % start_time_of_injections)
    logger.info("Provided Telemetry Ends at %s" % end_time_of_injections)

    isa_start_time = start_time_of_injections
    # isa_end_time =  end_time_of_injections
    provided_telemetry_item_length = r.zcard(flight_injection_sorted_set)
    logger.info("Provided Telemetry Item Count: %s" % provided_telemetry_item_length)

    provided_telemetry_duration_seconds = (end_time_of_injections - start_time_of_injections).total_seconds()
    logger.info("Provided Telemetry Duration in seconds: %s" % provided_telemetry_duration_seconds)
    ASTM_TIME_SHIFT_SECS = 65  # Enable querying for upto sixty seconds after end time.
    astm_rid_standard_end_time = end_time_of_injections.shift(seconds=ASTM_TIME_SHIFT_SECS)

    # Create an ISA in the DSS
    position_list: List[Point] = []
    for position in all_positions:
        position_list.append(Point(position.lng, position.lat))

    multi_points = MultiPoint(position_list)
    bounds = multi_points.minimum_rotated_rectangle.bounds

    b = box(bounds[1], bounds[0], bounds[3], bounds[2])
    co_ordinates = list(zip(*b.exterior.coords.xy))

    polygon_verticies: List[LatLngPoint] = []
    for co_ordinate in co_ordinates:
        ll = LatLngPoint(lat=co_ordinate[0], lng=co_ordinate[1])
        polygon_verticies.append(ll)
    polygon_verticies.pop()
    outline_polygon = RIDPolygon(vertices=polygon_verticies)
    # Buffer the altitude by 5 m
    altitude_lower = RIDAltitude(value=min(all_altitudes) - 5, reference="W84", units="M")
    altitude_upper = RIDAltitude(value=min(all_altitudes) + 5, reference="W84", units="M")

    volume_3_d = RIDVolume3D(
        outline_polygon=outline_polygon,
        altitude_upper=altitude_upper,
        altitude_lower=altitude_lower,
    )
    volume_4_d = RIDVolume4D(
        volume=volume_3_d,
        time_start=RIDTime(value=isa_start_time.isoformat(), format="RFC3339"),
        time_end=RIDTime(value=astm_rid_standard_end_time.isoformat(), format="RFC3339"),
    )

    uss_base_url = env.get("FLIGHTBLENDER_FQDN", "http://flight-blender:8000")
    my_dss_helper = dss_rid_helper.RemoteIDOperations()

    logger.info("Creating a DSS ISA..")
    my_dss_helper.create_dss_isa(flight_extents=volume_4_d, uss_base_url=uss_base_url)
    # # End create ISA in the DSS

    r.expire(flight_injection_sorted_set, time=3000)
    time.sleep(2)  # Wait 2 seconds before starting mission
    should_continue = True
    query_target = provided_telemetry_item_length + ASTM_TIME_SHIFT_SECS  # one per second
    all_telemetry_details = r.zrange(flight_injection_sorted_set, 0, -1, withscores=True)
    all_timestamps = []
    for telemetry_id, cur_telemetry_detail in enumerate(all_telemetry_details):
        all_timestamps.append(cur_telemetry_detail[1])
    cycled = cycle(all_timestamps)
    query_time_lookup = list(islice(cycled, 0, query_target))

    def _stream_data(query_time: arrow.arrow.Arrow):
        """
        Stream data based on the given query time.
        This function retrieves the closest observations from a sorted set in Redis
        based on the provided query time. It then processes each observation, extracts
        relevant telemetry and details response data, and creates a SingleRIDObservation
        object. Finally, it sends a job to the task queue to write the incoming air traffic
        data to the database.
        Args:
            query_time (arrow.arrow.Arrow): The time to query the closest observations.
        Returns:
            None
        Raises:
            None
        Note:
            This function uses Redis to retrieve data and Celery to queue tasks for writing
            data to the database.
        """
        # Function implementation here
        last_observation_timestamp_key = test_id + "_rid_stream_last_observation_timestamp"
        closest_observations = r.zrangebyscore(
            flight_injection_sorted_set,
            query_time.int_timestamp,
            query_time.int_timestamp,
        )
        # obs_query_dict = {
        #     "closest_observation_count": len(closest_observations),
        #     "q_time": query_time.isoformat(),
        # }
        # logger.info("Closest observations: {closest_observation_count} found, at query time {q_time}".format(**obs_query_dict))


        for closest_observation in closest_observations:
            c_o = json.loads(closest_observation)
            single_telemetry_data = c_o["flight_state"]
            single_details_response = c_o["details_response"]
            observation_metadata = SingleObservationMetadata(
                telemetry=single_telemetry_data,
                details_response=single_details_response,
            )
            flight_details_id = single_details_response["details"]["id"]
            lat_dd = single_telemetry_data["position"]["lat"]
            lon_dd = single_telemetry_data["position"]["lng"]
            altitude_mm = single_telemetry_data["position"]["alt"]
            traffic_source = 3
            source_type = 0
            icao_address = flight_details_id

            last_observation_timestamp = r.get(last_observation_timestamp_key)
            if last_observation_timestamp:
                last_observation_timestamp = int(last_observation_timestamp)
                if abs(last_observation_timestamp - query_time.int_timestamp) <= 1:
                    logger.info("The last observation was received less than 1 second ago..")
                else: # The last observation was received more than 1 second ago
                    write_operator_rid_notification.delay(message="NET0040: RID data stream error, the last observation was received more than 1 second ago", session_id=test_id)
                    

            r.set(last_observation_timestamp_key, query_time.int_timestamp)

            so = SingleRIDObservation(
                lat_dd=lat_dd,
                lon_dd=lon_dd,
                altitude_mm=altitude_mm,
                traffic_source=traffic_source,
                source_type=source_type,
                icao_address=icao_address,
                metadata=json.dumps(asdict(observation_metadata)),
            )
            # TODO: Write to database 
            write_incoming_air_traffic_data.delay(json.dumps(asdict(so)))  # Send a job to the task queue
            logger.debug("Submitted flight observation..")

    r.expire(flight_injection_sorted_set, time=3000)
    logger.info("Starting streaming of RID Test Data..")
    while should_continue:
        now = arrow.now()
        query_time = now
        if now > astm_rid_standard_end_time:
            should_continue = False
            logger.info("End streaming ... %s" % arrow.now().isoformat())

        elif now > end_time_of_injections:
            # the current time is more than the end time for flight injection, we must provide closest observation
            seconds_now_after_end_of_injections = (now - end_time_of_injections).total_seconds()
            q_index = provided_telemetry_item_length + seconds_now_after_end_of_injections
            query_time = arrow.get(query_time_lookup[int(q_index)])
            logger.info("Exceeded normal end time of injections, looking up iteration, query time: %s" % query_time.isoformat())

        _stream_data(query_time=query_time)
        # Sleep for .2 seconds before submitting the next iteration.
        time.sleep(0.25)

@app.task(name="write_operator_rid_notification")
def write_operator_rid_notification(message: str, session_id: str):
    operator_rid_notification = OperatorRIDNotificationCreationPayload(message=message, session_id=session_id)
    my_database_writer = FlightBlenderDatabaseWriter()
    my_database_writer.create_operator_rid_notification(operator_rid_notification=operator_rid_notification)



@app.task(name="check_rid_stream_conformance")
def check_rid_stream_conformance(session_id: str, dry_run: str = "1"):
    # This method conducts flight conformance checks as a async task

    # amqp_connection_url = env.get("AMQP_URL", 0)
    # is_dry_run = True if dry_run == "1" else False

    my_rid_stream_checker = FlightTelemetryRIDEngine(session_id=session_id)

    rid_stream_conformant, error_details = my_rid_stream_checker.check_rid_stream_ok()

    if rid_stream_conformant:
        logger.info("RID Data stream for  {session_id} is OK...".format(session_id=session_id))

    else:
        logger.info("RID Data stream for {session_id} is NOT OK...".format(session_id=session_id))
        my_database_writer = FlightBlenderDatabaseWriter()
        for error_detail in error_details:
            operator_rid_notification = OperatorRIDNotificationCreationPayload(message=error_detail.error_description, session_id=session_id)
            my_database_writer.create_operator_rid_notification(operator_rid_notification=operator_rid_notification)
