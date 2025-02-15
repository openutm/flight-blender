import json
import logging
import time
from dataclasses import asdict
from datetime import timedelta
from itertools import cycle, islice
from os import environ as env
from typing import List

import arrow
from arrow.parser import ParserError
from dotenv import find_dotenv, load_dotenv
from shapely.geometry import MultiPoint, Point, box

from auth_helper.common import get_redis
from common.database_operations import FlightBlenderDatabaseWriter
from flight_blender.celery import app
from flight_feed_operations import flight_stream_helper
from flight_feed_operations.data_definitions import SingleRIDObservation
from flight_feed_operations.tasks import write_incoming_air_traffic_data
from rid_operations.data_definitions import (
    UASID,
    SignedUnsignedTelemetryObservation,
    UAClassificationEU,
)

from . import dss_rid_helper
from .rid_utils import (
    AuthData,
    LatLngPoint,
    RIDAircraftPosition,
    RIDAircraftState,
    RIDAltitude,
    RIDHeight,
    RIDOperatorDetails,
    RIDPolygon,
    RIDTestDataStorage,
    RIDTestDetailsResponse,
    RIDTestInjection,
    RIDTime,
    RIDVolume3D,
    RIDVolume4D,
    SingleObservationMetadata,
)

logger = logging.getLogger("django")

load_dotenv(find_dotenv())


@app.task(name="submit_dss_subscription")
def submit_dss_subscription(view, vertex_list, request_uuid):
    subscription_time_delta = 30
    myDSSSubscriber = dss_rid_helper.RemoteIDOperations()
    subscription_created = myDSSSubscriber.create_dss_subscription(
        vertex_list=vertex_list,
        view_port=view,
        request_uuid=request_uuid,
        subscription_time_delta=subscription_time_delta,
    )
    logger.info("Subscription creation status: %s" % subscription_created["created"])


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
def stream_rid_test_data(requested_flights):
    all_requested_flights: List[RIDTestInjection] = []
    rf = json.loads(requested_flights)
    all_positions: List[LatLngPoint] = []

    flight_injection_sorted_set = "requested_flight_ss"
    r = get_redis()

    if r.exists(flight_injection_sorted_set):
        r.delete(flight_injection_sorted_set)
    # Iterate over requested flights and process for storage / querying
    all_altitudes = []
    for requested_flight in rf:
        all_telemetry = []
        all_flight_details = []
        provided_telemetries = requested_flight["telemetry"]
        provided_flight_details = requested_flight["details_responses"]

        for provided_flight_detail in provided_flight_details:
            fd = provided_flight_detail["details"]
            requested_flight_detail_id = fd["id"]

            op_location = LatLngPoint(lat=fd["operator_location"]["lat"], lng=fd["operator_location"]["lng"])
            if "auth_data" in fd.keys():
                auth_data = AuthData(format=fd["auth_data"]["format"], data=fd["auth_data"]["data"])
            else:
                auth_data = AuthData(format=0, data="")
            serial_number = fd["serial_number"] if "serial_number" in fd else "MFR1C123456789ABC"
            if "uas_id" in fd.keys():
                uas_id = UASID(
                    specific_session_id=fd["uas_id"]["specific_session_id"],
                    serial_number=fd["uas_id"]["serial_number"],
                    registration_id=fd["uas_id"]["registration_id"],
                    utm_id=fd["uas_id"]["utm_id"],
                )
            else:
                uas_id = UASID(
                    specific_session_id="02-a1b2c3d4e5f60708",
                    serial_number=serial_number,
                    utm_id="ae1fa066-6d68-4018-8274-af867966978e",
                    registration_id="MFR1C123456789ABC",
                )
            if "eu_classification" in fd.keys():
                eu_classification = UAClassificationEU(
                    category=fd["eu_classification"]["category"],
                    class_=fd["eu_classification"]["class"],
                )
            else:
                eu_classification = UAClassificationEU(category="EUCategoryUndefined", class_="EUClassUndefined")

            flight_detail = RIDOperatorDetails(
                id=requested_flight_detail_id,
                operation_description=fd["operation_description"],
                serial_number=serial_number,
                registration_number=fd["registration_number"],
                operator_location=op_location,
                aircraft_type="NotDeclared",
                operator_id=fd["operator_id"],
                auth_data=auth_data,
                uas_id=uas_id,
                eu_classification=eu_classification,
            )
            pfd = RIDTestDetailsResponse(
                effective_after=provided_flight_detail["effective_after"],
                details=flight_detail,
            )
            all_flight_details.append(pfd)

            flight_details_storage = "flight_details:" + requested_flight_detail_id

            r.set(flight_details_storage, json.dumps(asdict(flight_detail)))
            # expire in 5 mins
            r.expire(flight_details_storage, time=3000)

        # Iterate over telemetry details profided
        for telemetry_id, provided_telemetry in enumerate(provided_telemetries):
            pos = provided_telemetry["position"]
            # In provided telemetry position and pressure altitude and extrapolated values are optional use if provided else generate them.
            pressure_altitude = pos["pressure_altitude"] if "pressure_altitude" in pos else 0.0
            extrapolated = pos["extrapolated"] if "extrapolated" in pos else 0

            llp = LatLngPoint(lat=pos["lat"], lng=pos["lng"])
            all_positions.append(llp)
            all_altitudes.append(pos["alt"])
            position = RIDAircraftPosition(
                lat=pos["lat"],
                lng=pos["lng"],
                alt=pos["alt"],
                accuracy_h=pos["accuracy_h"],
                accuracy_v=pos["accuracy_v"],
                extrapolated=extrapolated,
                pressure_altitude=pressure_altitude,
            )

            if "height" in provided_telemetry.keys():
                height = RIDHeight(
                    distance=provided_telemetry["height"]["distance"],
                    reference=provided_telemetry["height"]["reference"],
                )
            else:
                height = None

            try:
                formatted_timestamp = arrow.get(provided_telemetry["timestamp"])
            except ParserError:
                logger.info("Error in parsing telemetry timestamp")
            else:
                t = RIDAircraftState(
                    timestamp=RIDTime(value=provided_telemetry["timestamp"], format="RFC3339"),
                    timestamp_accuracy=provided_telemetry["timestamp_accuracy"],
                    operational_status=provided_telemetry["operational_status"],
                    position=position,
                    track=provided_telemetry["track"],
                    speed=provided_telemetry["speed"],
                    speed_accuracy=provided_telemetry["speed_accuracy"],
                    vertical_speed=provided_telemetry["vertical_speed"],
                    height=height,
                )
                #
                closest_details_response = min(
                    all_flight_details,
                    key=lambda d: abs(arrow.get(d.effective_after) - formatted_timestamp),
                )
                flight_state_storage = RIDTestDataStorage(flight_state=t, details_response=closest_details_response)
                zadd_struct = {json.dumps(asdict(flight_state_storage)): formatted_timestamp.int_timestamp}
                # Add these as a sorted set in Redis
                r.zadd(flight_injection_sorted_set, zadd_struct)
                all_telemetry.append(t)

        requested_flight = RIDTestInjection(
            injection_id=requested_flight["injection_id"],
            telemetry=all_telemetry,
            details_responses=all_flight_details,
        )

        all_requested_flights.append(requested_flight)

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
        position_list.append((position.lng, position.lat))

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

    volume3D = RIDVolume3D(
        outline_polygon=outline_polygon,
        altitude_upper=altitude_upper,
        altitude_lower=altitude_lower,
    )
    volume4D = RIDVolume4D(
        volume=volume3D,
        time_start=RIDTime(value=isa_start_time.isoformat(), format="RFC3339"),
        time_end=RIDTime(value=astm_rid_standard_end_time.isoformat(), format="RFC3339"),
    )

    uss_base_url = env.get("FLIGHTBLENDER_FQDN", "http://host.docker.internal:8000")
    my_dss_helper = dss_rid_helper.RemoteIDOperations()

    logger.info("Creating a DSS ISA..")
    my_dss_helper.create_dss_isa(flight_extents=volume4D, uss_base_url=uss_base_url)
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
        closest_observations = r.zrangebyscore(
            flight_injection_sorted_set,
            query_time.int_timestamp,
            query_time.int_timestamp,
        )
        obs_query_dict = {
            "closest_observation_count": len(closest_observations),
            "q_time": query_time.isoformat(),
        }
        logger.info("Closest observations: {closest_observation_count} found, at query time {q_time}".format(**obs_query_dict))
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

            so = SingleRIDObservation(
                lat_dd=lat_dd,
                lon_dd=lon_dd,
                altitude_mm=altitude_mm,
                traffic_source=traffic_source,
                source_type=source_type,
                icao_address=icao_address,
                metadata=json.dumps(asdict(observation_metadata)),
            )
            write_incoming_air_traffic_data.delay(json.dumps(asdict(so)))  # Send a job to the task queue
            logger.debug("Submitted flight observation..")

    r.expire(flight_injection_sorted_set, time=3000)

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
        time.sleep(0.2)
