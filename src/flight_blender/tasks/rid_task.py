import asyncio
import json
import uuid
from dataclasses import asdict
from datetime import timedelta
from enum import Enum

import arrow
from arrow.parser import ParserError
from dacite import Config, from_dict
from dacite.exceptions import DaciteFieldError
from loguru import logger
from shapely.geometry import MultiPoint, Point, box

from flight_blender.auth.token_cache import get_redis
from flight_blender.celery import app
from flight_blender.clients import dss_rid_client as dss_rid_helper
from flight_blender.config import settings
from flight_blender.db.session import async_task_session
from flight_blender.domain_types.flight_feed import SingleRIDObservation
from flight_blender.domain_types.rid import UASID, LatLngPoint, SignedUnsignedTelemetryObservation, UAClassificationEU
from flight_blender.domain_types.rid import RIDAircraftState as LocalRIDAircraftState
from flight_blender.domain_types.rid import RIDFlightDetails as LocalRIDFlightDetails
from flight_blender.domain_types.rid_operations import RIDLatLngPoint
from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository
from flight_blender.repositories.flight_feed_repo import SQLAlchemyFlightFeedRepository
from flight_blender.repositories.notifications_repo import SQLAlchemyNotificationsRepository
from flight_blender.repositories.rid_repo import SQLAlchemyRIDRepository
from flight_blender.services.altitude import wgs84_to_barometric
from flight_blender.services.rid_svc import (
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
from flight_blender.tasks.flight_feed_task import write_incoming_air_traffic_data


def _parse_rid_timestamp_us(rid_ts_value, context: str) -> int:
    """Parse an RID RFC3339 timestamp into epoch microseconds."""
    if not rid_ts_value:
        logger.warning("Missing RID timestamp for {}. Defaulting sensor timestamp to 0", context)
        return 0

    parsed = _parse_rid_timestamp_value(rid_ts_value)
    if parsed is None:
        logger.warning("Invalid RID timestamp {!r} for {}. Defaulting sensor timestamp to 0", rid_ts_value, context)
        return 0

    try:
        return int(parsed.float_timestamp * 1_000_000)
    except (TypeError, ValueError) as exc:
        logger.warning(
            "Failed to convert RID timestamp {!r} for {}. Defaulting sensor timestamp to 0. Error: {}",
            rid_ts_value,
            context,
            exc,
        )
        return 0


def _parse_rid_timestamp_value(rid_ts_value) -> arrow.Arrow | None:
    """Parse an RID timestamp (RFC3339 or epoch-seconds) into an Arrow."""
    if rid_ts_value is None:
        return None
    if isinstance(rid_ts_value, (int, float)):
        return arrow.get(rid_ts_value)
    s = str(rid_ts_value)
    if s.endswith("Z") and "." in s:
        s = s[:-1] + "+00:00"
    try:
        return arrow.get(s)
    except (ParserError, TypeError, ValueError):
        try:
            return arrow.get(float(s))
        except (ValueError, TypeError):
            return None


def _parse_rid_timestamp(rid_ts_value) -> arrow.Arrow:
    """Parse an RID timestamp, defaulting invalid values to the current time."""
    return _parse_rid_timestamp_value(rid_ts_value) or arrow.now()


@app.task(name="write_operator_rid_notification")
def write_operator_rid_notification(message: str, session_id: str):
    asyncio.run(_async_write_operator_rid_notification(message, session_id))


async def _async_write_operator_rid_notification(message: str, session_id: str) -> None:
    try:
        session_uuid = uuid.UUID(session_id)
    except (ValueError, AttributeError):
        session_uuid = None
    async with async_task_session() as db:
        repo = SQLAlchemyNotificationsRepository(db)
        await repo.create_notification(message=message, session_id=session_uuid)


async def _async_process_requested_flight(
    requested_flight: dict,
    flight_injection_sorted_set: str,
    test_id: str,
    injection_id: str,
    rid_repo: SQLAlchemyRIDRepository,
) -> tuple[RIDTestInjection, list[LatLngPoint], list[float]]:
    """
    Processes a requested flight by parsing flight details and telemetry data, storing relevant information in Redis,
    and returning structured representations of the flight, positions, and altitudes.
    """
    r = get_redis()
    all_telemetry = []
    all_flight_details = []
    all_positions: list[LatLngPoint] = []
    all_altitudes = []
    provided_telemetries = requested_flight["telemetry"]
    provided_flight_details = requested_flight["details_responses"]

    MANDATORY_TELEMETRY_FIELDS = [
        "timestamp",
        "timestamp_accuracy",
        "position",
        "track",
        "speed",
        "speed_accuracy",
        "vertical_speed",
    ]
    MANDATORY_POSITION_FIELDS = ["lat", "lng", "alt"]
    for provided_flight_detail in provided_flight_details:
        fd = provided_flight_detail["details"]

        operator_location = None
        uas_id = None
        eu_classification = None
        auth_data = None
        if "operator_location" in fd.keys() and fd["operator_location"]:
            operator_location_dict = fd["operator_location"]
            if "position" in operator_location_dict and operator_location_dict["position"]:
                position = from_dict(data_class=LatLngPoint, data=operator_location_dict["position"])
            else:
                position = from_dict(data_class=LatLngPoint, data=operator_location_dict)
            operator_location = OperatorLocation(position=position)
        if "auth_data" in fd.keys() and fd["auth_data"]:
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
            id=injection_id,
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

        await rid_repo.create_or_update_flight_detail(rid_flight_details_payload=flight_detail)

    for telemetry_id, provided_telemetry in enumerate(provided_telemetries):
        missing_fields = [field for field in MANDATORY_TELEMETRY_FIELDS if field not in provided_telemetry or provided_telemetry[field] is None]
        logger.debug(f"Processing telemetry entry {telemetry_id}")
        logger.debug(f"Number of missing fields: {len(missing_fields)}")
        if missing_fields:
            logger.info("Missing telemetry fields, in telemetry: %s", missing_fields)
            logger.info(f"Telemetry entry {telemetry_id} is missing mandatory fields: {missing_fields}")
            write_operator_rid_notification.delay(
                session_id=test_id,
                message=f"NET0030: RID data stream error, telemetry entry {telemetry_id} is missing mandatory fields: {', '.join(missing_fields)}",
            )
            continue

        missing_position_fields = [
            field
            for field in MANDATORY_POSITION_FIELDS
            if field not in provided_telemetry["position"] or provided_telemetry["position"][field] is None
        ]
        logger.debug(f"Processing position entry {telemetry_id}")
        logger.debug(f"Number of missing position fields: {len(missing_position_fields)}")
        if missing_position_fields:
            logger.info("Missing position fields: %s", missing_position_fields)
            logger.warning(f"Telemetry position data is missing mandator fields: {missing_fields}")
            write_operator_rid_notification.delay(
                session_id=test_id,
                message=f"NET0030: RID data stream error, telemetry position entry is missing mandatory fields: {', '.join(missing_fields)}",
            )
            continue

        pos = provided_telemetry["position"]
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
            formatted_timestamp = _parse_rid_timestamp(provided_telemetry["timestamp"])
        except Exception:
            logger.info("Error in parsing telemetry timestamp")
            write_operator_rid_notification.delay(
                session_id=test_id,
                message="The mandatory timestamp provided in the telemetry is not in the correct format",
            )
            continue

        raw_ts = provided_telemetry["timestamp"]
        if isinstance(raw_ts, dict):
            ts_value = raw_ts.get("value", "")
            ts_format = raw_ts.get("format", "RFC3339")
        else:
            ts_value = str(raw_ts)
            ts_format = "RFC3339"
        try:
            telemetry_observation = RIDAircraftState(
                timestamp=RIDTime(value=ts_value, format=ts_format),
                timestamp_accuracy=provided_telemetry["timestamp_accuracy"],
                operational_status=provided_telemetry["operational_status"],
                position=position,
                track=provided_telemetry["track"],
                speed=provided_telemetry["speed"],
                speed_accuracy=provided_telemetry["speed_accuracy"],
                vertical_speed=provided_telemetry["vertical_speed"],
                height=height,
            )
        except DaciteFieldError as e:
            logger.error(
                "Error in parsing telemetry observation: %s. Error: %s",
                provided_telemetry,
                e,
            )
            logger.info("Skipping telemetry observation due to parsing error. Please check the provided telemetry data.")
            continue

        closest_details_response = min(
            all_flight_details,
            key=lambda d: abs(_parse_rid_timestamp(d.effective_after) - formatted_timestamp),
        )
        flight_state_storage = RIDTestDataStorage(
            flight_state=telemetry_observation,
            details_response=closest_details_response,
            aircraft_type=requested_flight["aircraft_type"],
            injection_id=requested_flight["injection_id"],
        )
        zadd_struct = {json.dumps(asdict(flight_state_storage)): formatted_timestamp.int_timestamp}
        r.zadd(flight_injection_sorted_set, zadd_struct)
        all_telemetry.append(telemetry_observation)

    _requested_flight = RIDTestInjection(
        aircraft_type=requested_flight["aircraft_type"],
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
    asyncio.run(_async_run_ussp_polling_for_rid(end_time, session_id))


async def _async_run_ussp_polling_for_rid(end_time: str, session_id: str) -> None:
    logger.info("Starting USSP polling.. ")
    now = arrow.now()
    end_time_formatted = arrow.get(end_time)

    delta = end_time_formatted - now
    polling_duration = delta.total_seconds()
    logger.info("Polling duration: %s" % polling_duration)

    logger.info("Polling USSP for RID data..")

    r = get_redis()
    async_polling_lock = f"async_polling_lock_{session_id}"

    async with async_task_session() as db:
        rid_repo = SQLAlchemyRIDRepository(db)
        feed_repo = SQLAlchemyFlightFeedRepository(db)
        subscription_record = await rid_repo.get_subscription_by_id(session_id)
        my_dss_subscriber = dss_rid_helper.RemoteIDOperations(rid_repo=rid_repo, feed_repo=feed_repo)

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
                await my_dss_subscriber.query_uss_for_rid(
                    flight_details=flight_details,
                    subscription_id=subscription_id,
                    view=view,
                )

                await asyncio.sleep(0.6)

            r.delete(async_polling_lock)

    logger.debug("Finished USSP polling..")


@app.task(name="stream_rid_telemetry_data")
def stream_rid_telemetry_data(rid_telemetry_observations):
    asyncio.run(_async_stream_rid_telemetry_data(rid_telemetry_observations))


async def _async_stream_rid_telemetry_data(rid_telemetry_observations) -> None:
    telemetry_observations = json.loads(rid_telemetry_observations)

    for observation in telemetry_observations:
        flight_details = observation["flight_details"]
        current_states = observation["current_states"]
        operation_id = flight_details["id"]

        async with async_task_session() as db:
            fd_repo = SQLAlchemyFlightDeclarationRepository(db)
            await fd_repo.update_telemetry_timestamp(uuid.UUID(operation_id))

        for current_state in current_states:
            _current_state = from_dict(data_class=LocalRIDAircraftState, data=current_state, config=Config(cast=[Enum]))
            _flight_details = from_dict(data_class=LocalRIDFlightDetails, data=flight_details, config=Config(cast=[Enum]))
            observation_and_metadata = SignedUnsignedTelemetryObservation(current_state=_current_state, flight_details=_flight_details)
            current_wgs84_m_altitude = observation_and_metadata.current_state.position.alt

            msl_height, pressure_altitude = wgs84_to_barometric(
                lat=observation_and_metadata.current_state.position.lat,
                lon=observation_and_metadata.current_state.position.lng,
                hae_meters=current_wgs84_m_altitude,
            )
            altitude_mm = pressure_altitude * 1000
            flight_details_id = observation_and_metadata.flight_details.uas_id.serial_number
            lat_dd = observation_and_metadata.current_state.position.lat
            lon_dd = observation_and_metadata.current_state.position.lng
            traffic_source = 11
            source_type = 0
            icao_address = flight_details_id

            rid_ts_value = getattr(_current_state.timestamp, "value", None)
            rid_timestamp_us = _parse_rid_timestamp_us(rid_ts_value, f"operation {operation_id}")

            so = SingleRIDObservation(
                session_id=operation_id,
                lat_dd=lat_dd,
                lon_dd=lon_dd,
                altitude_mm=altitude_mm,
                traffic_source=traffic_source,
                source_type=source_type,
                icao_address=icao_address,
                timestamp=rid_timestamp_us,
                metadata=asdict(observation_and_metadata),
            )
            write_incoming_air_traffic_data.delay(json.dumps(asdict(so)))
            logger.debug("Submitted observation..")


@app.task(name="stream_rid_test_data")
def stream_rid_test_data(requested_flights, test_id):
    asyncio.run(_async_stream_rid_test_data(requested_flights, test_id))


async def _async_stream_rid_test_data(requested_flights, test_id) -> None:
    test_id = test_id.split("_")[1]
    all_requested_flights: list[RIDTestInjection] = []
    rf = json.loads(requested_flights)

    all_positions: list[LatLngPoint] = []
    injection_id = rf[0]["injection_id"] if "injection_id" in rf[0] else "00000000-0000-0000-0000-000000000000"
    aircraft_type = rf[0]["aircraft_type"] if "aircraft_type" in rf[0] else "Unknown"

    flight_injection_sorted_set = "requested_flight_ss"
    r = get_redis()

    if r.exists(flight_injection_sorted_set):
        r.delete(flight_injection_sorted_set)

    all_altitudes = []

    async with async_task_session() as db:
        rid_repo = SQLAlchemyRIDRepository(db)
        for requested_flight in rf:
            processed_flight, _all_positions, _all_altitudes = await _async_process_requested_flight(
                requested_flight=requested_flight,
                flight_injection_sorted_set=flight_injection_sorted_set,
                test_id=test_id,
                injection_id=injection_id,
                rid_repo=rid_repo,
            )
            all_positions.extend(_all_positions)
            all_altitudes.extend(_all_altitudes)
            all_requested_flights.append(processed_flight)

    start_time_of_injection_list = r.zrange(flight_injection_sorted_set, 0, 0, withscores=True)
    start_time_of_injections = arrow.get(start_time_of_injection_list[0][1])

    end_time_of_injection_list = r.zrevrange(flight_injection_sorted_set, 0, 0, withscores=True)
    end_time_of_injections = arrow.get(end_time_of_injection_list[0][1])

    logger.info("Provided Telemetry Starts at %s" % start_time_of_injections)
    logger.info("Provided Telemetry Ends at %s" % end_time_of_injections)

    isa_start_time = start_time_of_injections
    provided_telemetry_item_length = r.zcard(flight_injection_sorted_set)
    logger.info("Provided Telemetry Item Count: %s" % provided_telemetry_item_length)

    provided_telemetry_duration_seconds = (end_time_of_injections - start_time_of_injections).total_seconds()
    logger.info("Provided Telemetry Duration in seconds: %s" % provided_telemetry_duration_seconds)
    ASTM_TIME_SHIFT_SECS = 600
    RID_STREAM_END_GRACE_SECS = 1
    astm_rid_standard_end_time = end_time_of_injections.shift(seconds=ASTM_TIME_SHIFT_SECS)
    rid_stream_end_time = end_time_of_injections.shift(seconds=RID_STREAM_END_GRACE_SECS)

    position_list: list[Point] = []
    for position in all_positions:
        position_list.append(Point(position.lng, position.lat))

    if len(position_list) == 1:
        p = position_list[0]
        padding = 0.0001
        polygon_verticies = [
            RIDLatLngPoint(lat=p.y - padding, lng=p.x - padding),
            RIDLatLngPoint(lat=p.y - padding, lng=p.x + padding),
            RIDLatLngPoint(lat=p.y + padding, lng=p.x + padding),
            RIDLatLngPoint(lat=p.y + padding, lng=p.x - padding),
        ]
    else:
        multi_points = MultiPoint(position_list)
        bounds = multi_points.minimum_rotated_rectangle.bounds
        b = box(bounds[1], bounds[0], bounds[3], bounds[2])
        co_ordinates = list(zip(*b.exterior.coords.xy))
        polygon_verticies = []
        for co_ordinate in co_ordinates:
            ll = LatLngPoint(lat=co_ordinate[0], lng=co_ordinate[1])
            polygon_verticies.append(ll)
        polygon_verticies.pop()
    outline_polygon = RIDPolygon(vertices=polygon_verticies)
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

    uss_base_url = settings.FLIGHTBLENDER_FQDN
    my_dss_helper = dss_rid_helper.RemoteIDOperations()

    logger.info("Creating a DSS ISA..")
    my_dss_helper.create_dss_isa(flight_extents=volume_4_d, uss_base_url=uss_base_url)

    r.expire(flight_injection_sorted_set, time=3000)
    await asyncio.sleep(2)
    should_continue = True

    all_telemetry_details = r.zrange(flight_injection_sorted_set, 0, -1, withscores=True)

    def _stream_data(query_time: arrow.arrow.Arrow):
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
                aircraft_type=aircraft_type,
                injection_id=injection_id,
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
                else:
                    time_since_last_notification_key = test_id + "_rid_stream_last_notification_timestamp"
                    time_since_last_notification = r.get(time_since_last_notification_key)
                    if not time_since_last_notification or (query_time.int_timestamp - int(time_since_last_notification)) >= 10:
                        write_operator_rid_notification.delay(
                            message="NET0040: RID data stream error, the last observation was received more than 1 second ago",
                            session_id=test_id,
                        )
                        r.set(time_since_last_notification_key, query_time.int_timestamp)
            r.set(last_observation_timestamp_key, query_time.int_timestamp)

            telemetry_timestamp = single_telemetry_data.get("timestamp") if isinstance(single_telemetry_data, dict) else None
            rid_ts_value = telemetry_timestamp.get("value") if isinstance(telemetry_timestamp, dict) else None
            rid_timestamp_us = _parse_rid_timestamp_us(rid_ts_value, f"test {test_id}")

            so = SingleRIDObservation(
                session_id=test_id,
                lat_dd=lat_dd,
                lon_dd=lon_dd,
                altitude_mm=altitude_mm,
                traffic_source=traffic_source,
                source_type=source_type,
                icao_address=icao_address,
                timestamp=rid_timestamp_us,
                metadata=asdict(observation_metadata),
            )
            write_incoming_air_traffic_data.delay(json.dumps(asdict(so)))
            logger.debug("Submitted flight observation..")

    r.expire(flight_injection_sorted_set, time=3000)
    logger.info("Starting streaming of RID Test Data..")

    streaming_start_time = start_time_of_injections.shift(seconds=0.5)
    while should_continue:
        now = arrow.now()
        query_time = now
        _should_stop_streaming = r.get("stop_streaming_" + test_id)
        should_stop_streaming = int(_should_stop_streaming) if _should_stop_streaming else 0

        if should_stop_streaming or now > rid_stream_end_time:
            should_continue = False
            logger.info("End flight streaming ... %s", arrow.now().isoformat())
            continue

        if now > end_time_of_injections:
            last_observation = all_telemetry_details[-1]
            query_time = arrow.get(last_observation[1])

        if now > streaming_start_time:
            _stream_data(query_time=query_time)

        await asyncio.sleep(0.3)


@app.task(name="check_rid_stream_conformance")
def check_rid_stream_conformance(session_id: str, flight_declaration_id=None, dry_run: str = "1"):
    asyncio.run(_async_check_rid_stream_conformance(session_id))


async def _async_check_rid_stream_conformance(session_id: str) -> None:
    now = arrow.now()
    four_seconds_before_now = now.shift(seconds=-4)

    async with async_task_session() as db:
        feed_repo = SQLAlchemyFlightFeedRepository(db)
        relevant_observations = await feed_repo.get_active_rid_observations_for_session_between_interval(
            session_id=session_id,
            start_time=four_seconds_before_now,
            end_time=now,
        )

    if not relevant_observations:
        logger.info(f"RID Data stream for {session_id} is OK...")
        return

    errors = []
    for i in range(1, len(relevant_observations)):
        prev_obs = relevant_observations[i - 1]
        curr_obs = relevant_observations[i]
        time_diff = (curr_obs.created_at - prev_obs.created_at).total_seconds()
        if time_diff != 1:
            errors.append(f"NET0040: Timestamp difference error: {time_diff} seconds between observations {i - 1} and {i}")

    if not errors:
        logger.info(f"RID Data stream for {session_id} is OK...")
        return

    logger.info(f"RID Data stream for {session_id} is NOT OK...")
    try:
        session_uuid = uuid.UUID(session_id)
    except (ValueError, AttributeError):
        session_uuid = None

    async with async_task_session() as db:
        notif_repo = SQLAlchemyNotificationsRepository(db)
        for error_msg in errors:
            await notif_repo.create_notification(message=error_msg, session_id=session_uuid)
