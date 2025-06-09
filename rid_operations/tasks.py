import json
import logging
import time
from dataclasses import asdict
from datetime import timedelta
from enum import Enum
from os import environ as env

import arrow
from arrow.parser import ParserError
from dacite import Config, from_dict
from dotenv import find_dotenv, load_dotenv
from shapely.geometry import MultiPoint, Point, box

from auth_helper.common import get_redis
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from flight_blender.celery import app
from flight_feed_operations.data_definitions import SingleRIDObservation
from flight_feed_operations.tasks import write_incoming_air_traffic_data
from rid_operations.data_definitions import (
    UASID,
    LatLngPoint,
    OperatorRIDNotificationCreationPayload,
    SignedUnsignedTelemetryObservation,
    UAClassificationEU,
)

from . import dss_rid_helper
from .rid_telemetry_monitoring import FlightTelemetryRIDEngine
from .rid_utils import (
    OperatorLocation,
    RIDAircraftPosition,
    RIDAircraftState,
    RIDAltitude,
    RIDAuthData,
    RIDFlightDetails,
    RIDHeight,
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


def process_requested_flight(
    requested_flight: dict, flight_injection_sorted_set: str, test_id: str
) -> tuple[RIDTestInjection, list[LatLngPoint], list[float]]:
    """
    Processes a requested flight by parsing flight details and telemetry data, storing relevant information in Redis,
    and returning structured representations of the flight, positions, and altitudes.
    Args:
        requested_flight (dict): Dictionary containing flight details and telemetry data. Expected keys are:
            - "telemetry": List of telemetry observations, each containing position, timestamp, and other flight state data.
            - "details_responses": List of flight detail responses, each with flight and operator information.
            - "injection_id": Unique identifier for the flight injection.
        flight_injection_sorted_set (str): Redis sorted set key for storing telemetry observations.
        test_id (str): Identifier for the test session, used for operator notifications.
    Returns:
        tuple:
            - RIDTestInjection: Structured object representing the injected flight, including telemetry and details responses.
            - list[LatLngPoint]: List of latitude/longitude points representing the flight path.
            - list[float]: List of altitudes corresponding to the telemetry positions.
    Side Effects:
        - Stores flight details and telemetry observations in Redis.
        - Sets expiration for stored flight details.
        - Sends operator notifications if telemetry timestamps are invalid.
    Raises:
        None directly, but logs and notifies on telemetry timestamp parsing errors.
    """
    r = get_redis()
    all_telemetry = []
    all_flight_details = []
    all_positions: list[LatLngPoint] = []
    all_altitudes = []
    provided_telemetries = requested_flight["telemetry"]
    provided_flight_details = requested_flight["details_responses"]

    for provided_flight_detail in provided_flight_details:
        fd = provided_flight_detail["details"]
        requested_flight_detail_id = fd["id"]
        operator_location = None
        uas_id = None
        eu_classification = None
        auth_data = None
        if "operator_location" in fd.keys():
            position = from_dict(data_class=LatLngPoint, data=fd["operator_location"])
            operator_location = OperatorLocation(position=position)
        if "auth_data" in fd.keys():
            auth_data = RIDAuthData(format=fd["auth_data"]["format"], data=fd["auth_data"]["data"])
        if "uas_id" in fd.keys():
            specific_session_id = fd["uas_id"].get("specific_session_id", None)
            serial_number = fd["uas_id"].get("serial_number", "")
            registration_id = fd["uas_id"].get("registration_id", "")
            utm_id = fd["uas_id"].get("utm_id", "")
            uas_id = UASID(
                specific_session_id=specific_session_id,
                serial_number=serial_number,
                registration_id=registration_id,
                utm_id=utm_id,
            )

        if fd.get("eu_classification"):
            eu_classification = from_dict(
                data_class=UAClassificationEU,
                data=fd["eu_classification"],
                config=Config(cast=[Enum]),
            )

        flight_detail = RIDFlightDetails(
            id=requested_flight_detail_id,
            operation_description=fd["operation_description"],
            operator_location=operator_location,
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

    # Iterate over telemetry details provided
    for telemetry_id, provided_telemetry in enumerate(provided_telemetries):
        pos = provided_telemetry["position"]

        # In provided telemetry position and pressure altitude and extrapolated values are optional use if provided else generate them.
        pressure_altitude = pos["pressure_altitude"] if "pressure_altitude" in pos else 0.0
        extrapolated = pos["extrapolated"] if "extrapolated" in pos else False

        if "height" in provided_telemetry.keys():
            height = RIDHeight(
                distance=provided_telemetry["height"]["distance"],
                reference=provided_telemetry["height"]["reference"],
            )
        else:
            height = None

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
            height=height,
        )

        try:
            formatted_timestamp = arrow.get(provided_telemetry["timestamp"])
        except (ParserError, TypeError):
            logger.info("Error in parsing telemetry timestamp")
            # Set an operator notification
            write_operator_rid_notification.delay(
                session_id=test_id,
                message="The mandatory timestamp provided in the telemetry is not in the correct format",
            )

            formatted_timestamp = arrow.now()

        teletemetry_observation = RIDAircraftState(
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

        closest_details_response = min(
            all_flight_details,
            key=lambda d: abs(arrow.get(d.effective_after) - formatted_timestamp),
        )
        flight_state_storage = RIDTestDataStorage(
            flight_state=teletemetry_observation,
            details_response=closest_details_response,
        )
        zadd_struct = {json.dumps(asdict(flight_state_storage)): formatted_timestamp.int_timestamp}
        # Add these as a sorted set in Redis
        r.zadd(flight_injection_sorted_set, zadd_struct)
        all_telemetry.append(teletemetry_observation)

    _requested_flight = RIDTestInjection(
        injection_id=requested_flight["injection_id"],
        telemetry=all_telemetry,
        details_responses=all_flight_details,
    )

    return _requested_flight, all_positions, all_altitudes


@app.task(name="submit_dss_subscription")
def submit_dss_subscription(view, vertex_list, request_uuid):
    subscription_duration_seconds = 30
    my_dss_subscriber = dss_rid_helper.RemoteIDOperations()
    subscription_created = my_dss_subscriber.create_dss_subscription(
        vertex_list=vertex_list,
        view=view,
        request_uuid=request_uuid,
        subscription_duration_seconds=subscription_duration_seconds,
    )
    logger.info("Subscription creation status: %s" % subscription_created.created)


@app.task(name="run_ussp_polling_for_rid")
def run_ussp_polling_for_rid(end_time: str, session_id: str):
    """This method is a wrapper for repeated polling of UTMSPs for Network RID information"""
    logger.info("Starting USSP polling.. ")
    # Define start and end time
    now = arrow.now()
    end_time_formatted = arrow.get(end_time)

    delta = end_time_formatted - now
    polling_duration = delta.total_seconds()
    logger.info("Polling duration: %s" % polling_duration)

    my_database_reader = FlightBlenderDatabaseReader()
    subscription_record = my_database_reader.get_rid_subscription_record_by_id(id=session_id)
    logger.info("Polling USSP for RID data..")

    r = get_redis()

    async_polling_lock = f"async_polling_lock_{session_id}"  # This

    my_dss_subscriber = dss_rid_helper.RemoteIDOperations()

    if r.exists(async_polling_lock):
        logger.info("Polling is ongoing, not setting additional polling tasks..")
    else:
        logger.info("Setting Polling Lock..")

        r.set(async_polling_lock, "1")
        r.expire(async_polling_lock, timedelta(seconds=polling_duration))
        while arrow.now() < end_time_formatted:
            subscription_id = str(subscription_record.subscription_id)
            view = subscription_record.view
            flight_details = subscription_record.flight_details
            my_dss_subscriber.query_uss_for_rid(
                flight_details=flight_details,
                subscription_id=subscription_id,
                view=view,
            )

            time.sleep(0.6)

        r.delete(async_polling_lock)

    logger.debug("Finished USSP polling..")


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
                session_id=operation_id,
                lat_dd=lat_dd,
                lon_dd=lon_dd,
                altitude_mm=altitude_mm,
                traffic_source=traffic_source,
                source_type=source_type,
                icao_address=icao_address,
                metadata=asdict(observation_and_metadata),
            )
            write_incoming_air_traffic_data.delay(json.dumps(asdict(so)))  # Send a job to the task queue
            logger.debug("Submitted observation..")


@app.task(name="stream_rid_test_data")
def stream_rid_test_data(requested_flights, test_id):
    test_id = test_id.split("_")[1]
    all_requested_flights: list[RIDTestInjection] = []
    rf = json.loads(requested_flights)
    all_positions: list[LatLngPoint] = []

    flight_injection_sorted_set = "requested_flight_ss"
    r = get_redis()

    if r.exists(flight_injection_sorted_set):
        r.delete(flight_injection_sorted_set)
    # Iterate over requested flights and process for storage / querying

    all_altitudes = []

    for requested_flight in rf:
        processed_flight, _all_positions, _all_altitudes = process_requested_flight(
            requested_flight=requested_flight,
            flight_injection_sorted_set=flight_injection_sorted_set,
            test_id=test_id,
        )
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
    position_list: list[Point] = []
    for position in all_positions:
        position_list.append(Point(position.lng, position.lat))

    multi_points = MultiPoint(position_list)
    bounds = multi_points.minimum_rotated_rectangle.bounds

    b = box(bounds[1], bounds[0], bounds[3], bounds[2])
    co_ordinates = list(zip(*b.exterior.coords.xy))

    polygon_verticies: list[LatLngPoint] = []
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
    # Calculate the target number of queries based on the provided telemetry item length and ASTM time shift
    query_target = provided_telemetry_item_length + ASTM_TIME_SHIFT_SECS  # one per second

    # Retrieve all telemetry details from the sorted set in Redis
    all_telemetry_details = r.zrange(flight_injection_sorted_set, 0, -1, withscores=True)

    # Initialize a list to store all timestamps
    # all_timestamps = []

    # # Iterate over all telemetry details and extract their timestamps
    # for telemetry_id, cur_telemetry_detail in enumerate(all_telemetry_details):
    #     all_timestamps.append(cur_telemetry_detail[1])

    # # Create a cycle iterator for the timestamps
    # cycled = cycle(all_timestamps)

    # # Generate a list of query times by cycling through the timestamps
    # query_time_lookup = list(islice(cycled, 0, query_target))

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
            last_observation_timestamp = r.get(last_observation_timestamp_key)
            if last_observation_timestamp:
                last_observation_timestamp = int(last_observation_timestamp)
                if abs(last_observation_timestamp - query_time.int_timestamp) <= 1:
                    logger.debug("The last observation was received less than 1 second ago..")
                else:  # The last observation was received more than 1 second ago
                    # Define a key to track the timestamp of the last notification sent
                    time_since_last_notification_key = test_id + "_rid_stream_last_notification_timestamp"
                    # Retrieve the timestamp of the last notification sent from Redis
                    time_since_last_notification = r.get(time_since_last_notification_key)
                    # Check if no notification has been sent or if the last notification was sent more than 10 seconds ago
                    if not time_since_last_notification or (query_time.int_timestamp - int(time_since_last_notification)) >= 10:
                        # Send a notification about the RID data stream error
                        write_operator_rid_notification.delay(
                            message="NET0040: RID data stream error, the last observation was received more than 1 second ago",
                            session_id=test_id,
                        )
                        # Update the timestamp of the last notification sent in Redis
                        r.set(time_since_last_notification_key, query_time.int_timestamp)
            r.set(last_observation_timestamp_key, query_time.int_timestamp)

            so = SingleRIDObservation(
                session_id=test_id,
                lat_dd=lat_dd,
                lon_dd=lon_dd,
                altitude_mm=altitude_mm,
                traffic_source=traffic_source,
                source_type=source_type,
                icao_address=icao_address,
                metadata=asdict(observation_metadata),
            )
            # TODO: Write to database
            write_incoming_air_traffic_data.delay(json.dumps(asdict(so)))  # Send a job to the task queue
            logger.debug("Submitted flight observation..")

    r.expire(flight_injection_sorted_set, time=3000)
    logger.info("Starting streaming of RID Test Data..")

    streaming_start_time = start_time_of_injections.shift(seconds=0.5)
    while should_continue:
        now = arrow.now()
        query_time = now
        _should_stop_streaming = r.get("stop_streaming_" + test_id)
        should_stop_streaming = int(_should_stop_streaming) if _should_stop_streaming else 0

        if should_stop_streaming or now > astm_rid_standard_end_time:
            should_continue = False
            logger.info("End flight streaming ... %s", arrow.now().isoformat())
            continue

        if now > end_time_of_injections:
            last_observation = all_telemetry_details[-1]
            query_time = arrow.get(last_observation[1])

        if now > streaming_start_time:
            _stream_data(query_time=query_time)

        time.sleep(0.3)


@app.task(name="write_operator_rid_notification")
def write_operator_rid_notification(message: str, session_id: str):
    operator_rid_notification = OperatorRIDNotificationCreationPayload(message=message, session_id=session_id)
    my_database_writer = FlightBlenderDatabaseWriter()
    my_database_writer.create_operator_rid_notification(operator_rid_notification=operator_rid_notification)


@app.task(name="check_rid_stream_conformance")
def check_rid_stream_conformance(session_id: str, flight_declaration_id=None, dry_run: str = "1"):
    # This method conducts flight conformance checks as a async task

    my_rid_stream_checker = FlightTelemetryRIDEngine(session_id=session_id)

    rid_stream_conformant, error_details = my_rid_stream_checker.check_rid_stream_ok()

    if rid_stream_conformant:
        logger.info(f"RID Data stream for  {session_id} is OK...")

    else:
        logger.info(f"RID Data stream for {session_id} is NOT OK...")
        my_database_writer = FlightBlenderDatabaseWriter()
        for error_detail in error_details:
            operator_rid_notification = OperatorRIDNotificationCreationPayload(message=error_detail.error_description, session_id=session_id)
            my_database_writer.create_operator_rid_notification(operator_rid_notification=operator_rid_notification)
