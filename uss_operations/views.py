import json
import logging
import time
from dataclasses import asdict
from typing import List
from uuid import UUID

import arrow
from dacite import from_dict
from django.http import JsonResponse
from dotenv import find_dotenv, load_dotenv
from rest_framework.decorators import api_view
from shapely.geometry import Point

import rid_operations.view_port_ops as view_port_ops
from auth_helper.common import get_redis
from auth_helper.utils import requires_scopes
from common.data_definitions import FLIGHT_OPINT_KEY
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from common.utils import EnhancedJSONEncoder
from flight_feed_operations import flight_stream_helper
from rid_operations.data_definitions import (
    UASID,
    Altitude,
    LatLngPoint,
    OperatorLocation,
    UAClassificationEU,
)
from rid_operations.rid_utils import (
    RIDAircraftPosition,
    RIDAircraftState,
    RIDAuthData,
    RIDFlightResponse,
    RIDHeight,
    RIDLatLngPoint,
    RIDOperatorDetails,
    RIDTime,
    TelemetryFlightDetails,
)
from scd_operations.dss_scd_helper import (
    OperationalIntentReferenceHelper,
    VolumesConverter,
)
from scd_operations.scd_data_definitions import OperationalIntentStorage, Volume4D

from .uss_data_definitions import (
    FlightDetailsNotFoundMessage,
    GenericErrorResponseMessage,
    OperationalIntentDetails,
    OperationalIntentDetailsUSSResponse,
    OperationalIntentNotFoundResponse,
    OperationalIntentReferenceDSSResponse,
    OperationalIntentUSSDetails,
    OperatorDetailsSuccessResponse,
    Time,
    UpdateChangedOpIntDetailsPost,
    UpdateOperationalIntent,
    VehicleTelemetry,
    VehicleTelemetryResponse,
)

# Create your views here.


load_dotenv(find_dotenv())
logger = logging.getLogger("django")


def is_valid_uuid(uuid_to_test, version=4):
    try:
        uuid_obj = UUID(uuid_to_test, version=version)
    except ValueError:
        return False
    return str(uuid_obj) == uuid_to_test


@api_view(["POST"])
@requires_scopes(["utm.strategic_coordination"])
def uss_update_opint_details(request):
    # Get notifications from peer uss re changed operational intent details https://redocly.github.io/redoc/?url=https://raw.githubusercontent.com/astm-utm/Protocol/cb7cf962d3a0c01b5ab12502f5f54789624977bf/utm.yaml#tag/p2p_utm/operation/notifyOperationalIntentDetailsChanged
    database_reader = FlightBlenderDatabaseReader()
    database_writer = FlightBlenderDatabaseWriter()
    my_geo_json_converter = VolumesConverter()
    op_int_update_details_data = request.data
    r = get_redis()
    op_int_update_detail = from_dict(data_class=UpdateChangedOpIntDetailsPost, data=op_int_update_details_data)

    my_operational_intent_parser = OperationalIntentReferenceHelper()
    # Write the operational Intent
    operation_id_str = op_int_update_detail.operational_intent_id
    print("Operation ID %s" % operation_id_str)

    op_int_details_key = FLIGHT_OPINT_KEY + operation_id_str
    if r.exists(op_int_details_key):
        stored_opint_details_str = r.get(op_int_details_key)
        stored_opint_details = json.loads(stored_opint_details_str)

        original_dss_success_response = stored_opint_details["success_response"]
        logger.info("incoming...")
        # logger.info(op_int_update_detail)
        operational_intent_reference = op_int_update_detail.operational_intent.reference
        ovn = operational_intent_reference.ovn

        flight_authorization = database_reader.get_flight_authorization_by_operational_intent_ref_id(operational_intent_ref_id=str(operation_id_str))
        # update the ovn
        database_writer.update_flight_authorization_op_int_ovn(
            flight_authorization=flight_authorization, dss_operational_intent_id=operation_id_str, ovn=ovn
        )

        operational_intent_details = op_int_update_detail.operational_intent.details
        volumes = operational_intent_details.volumes

        all_volumes: List[Volume4D] = []
        for volume in volumes:
            volume_4D = my_operational_intent_parser.parse_volume_to_volume4D(volume=volume)
            all_volumes.append(volume_4D)

        my_geo_json_converter.convert_volumes_to_geojson(volumes=all_volumes)
        view_rect_bounds = my_geo_json_converter.get_bounds()
        # success_response = OpenS
        operational_intent_full_details = OperationalIntentStorage(
            bounds=view_rect_bounds,
            start_time=json.dumps(asdict(test_injection_data.operational_intent.volumes[0].time_start)),
            end_time=json.dumps(asdict(test_injection_data.operational_intent.volumes[0].time_end)),
            alt_max=50,
            alt_min=25,
            success_response=original_dss_success_response,
            operational_intent_details=op_int_update_detail.operational_intent,
        )

        r.set(op_int_details_key, json.dumps(asdict(operational_intent_full_details)))
        r.expire(name=op_int_details_key, time=opint_subscription_end_time)

    # Read the new operational intent
    # Store the opint, see what other operations conflict the opint

    updated_success = UpdateOperationalIntent(message="New or updated full operational intent information received successfully ")
    return JsonResponse(json.loads(json.dumps(updated_success, cls=EnhancedJSONEncoder)), status=204)


@api_view(["GET"])
@requires_scopes(["utm.strategic_coordination"])
def USSOffNominalPositionDetails(request, entity_id):
    raise NotImplementedError


@api_view(["GET"])
@requires_scopes(["utm.conformance_monitoring_sa"])
def USSOpIntDetailTelemetry(request, opint_id):
    # Get the telemetry of a off-nominal USSP, for more information see https://redocly.github.io/redoc/?url=https://raw.githubusercontent.com/astm-utm/Protocol/cb7cf962d3a0c01b5ab12502f5f54789624977bf/utm.yaml
    now = arrow.now()
    five_seconds_from_now = now.shift(seconds=5)
    telemetry_response = VehicleTelemetryResponse(
        operational_intent_id=str(opint_id),
        telemetry=VehicleTelemetry(time_measured=Time(format="RFC3339", value=arrow.now().isoformat()), position=None, velocity=None),
        next_telemetry_opportunity=Time(format="RFC3339", value=five_seconds_from_now.isoformat()),
    )
    return JsonResponse(json.loads(json.dumps(asdict(telemetry_response), cls=EnhancedJSONEncoder)), status=200)


@api_view(["GET"])
@requires_scopes(["utm.strategic_coordination"])
def USSOpIntDetails(request, opint_id):
    r = get_redis()

    my_database_reader = FlightBlenderDatabaseReader()
    flight_authorization = my_database_reader.get_flight_authorization_by_operational_intent_ref_id(str(opint_id))
    if flight_authorization:
        operational_intent_id = str(flight_authorization.declaration.id)
        flight_opint = FLIGHT_OPINT_KEY + operational_intent_id
        if r.exists(flight_opint):
            op_int_details_raw = r.get(flight_opint)
            op_int_details = json.loads(op_int_details_raw)

            reference_full = op_int_details["success_response"]["operational_intent_reference"]
            details_full = op_int_details["operational_intent_details"]
            # Load existing opint details
            stored_operational_intent_id = reference_full["id"]
            stored_manager = reference_full["manager"]
            stored_uss_availability = reference_full["uss_availability"]
            stored_version = reference_full["version"]
            stored_state = reference_full["state"]
            stored_ovn = reference_full["ovn"]
            stored_uss_base_url = reference_full["uss_base_url"]
            stored_subscription_id = reference_full["subscription_id"]

            stored_time_start = Time(
                format=reference_full["time_start"]["format"],
                value=reference_full["time_start"]["value"],
            )
            stored_time_end = Time(
                format=reference_full["time_end"]["format"],
                value=reference_full["time_end"]["value"],
            )
            stored_volumes = details_full["volumes"]
            for v in stored_volumes:
                if "outline_circle" in v["volume"].keys():
                    if not v["volume"]["outline_circle"]:
                        v["volume"].pop("outline_circle")

            stored_priority = details_full["priority"]
            stored_off_nominal_volumes = details_full["off_nominal_volumes"]
            for v in stored_off_nominal_volumes:
                if "outline_circle" in v["volume"].keys():
                    if not v["volume"]["outline_circle"]:
                        v["volume"].pop("outline_circle")

            reference = OperationalIntentReferenceDSSResponse(
                id=stored_operational_intent_id,
                manager=stored_manager,
                uss_availability=stored_uss_availability,
                version=stored_version,
                state=stored_state,
                ovn=stored_ovn,
                time_start=stored_time_start,
                time_end=stored_time_end,
                uss_base_url=stored_uss_base_url,
                subscription_id=stored_subscription_id,
            )
            details = OperationalIntentUSSDetails(
                volumes=stored_volumes,
                priority=stored_priority,
                off_nominal_volumes=stored_off_nominal_volumes,
            )

            operational_intent = OperationalIntentDetailsUSSResponse(reference=reference, details=details)
            operational_intent_response = OperationalIntentDetails(operational_intent=operational_intent)

            return JsonResponse(
                json.loads(json.dumps(operational_intent_response, cls=EnhancedJSONEncoder)),
                status=200,
            )

        else:
            not_found_response = OperationalIntentNotFoundResponse(message="Requested Operational intent with id %s not found" % str(opint_id))

            return JsonResponse(
                json.loads(json.dumps(not_found_response, cls=EnhancedJSONEncoder)),
                status=404,
            )

    else:
        not_found_response = OperationalIntentNotFoundResponse(message="Requested Operational intent with id %s not found" % str(opint_id))

        return JsonResponse(
            json.loads(json.dumps(not_found_response, cls=EnhancedJSONEncoder)),
            status=404,
        )


@api_view(["GET"])
@requires_scopes(["rid.display_provider"])
def get_uss_flights(request):
    """This is the end point for the rid_qualifier to get details of a flight"""
    # try:
    #     include_recent_positions = request.query_params["include_recent_positions"]
    # except MultiValueDictKeyError:
    #     include_recent_positions = False

    try:
        view = request.query_params["view"]
        view_port = [float(i) for i in view.split(",")]
    except Exception:
        incorrect_parameters = {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}
        return JsonResponse(json.loads(json.dumps(incorrect_parameters)), status=400)
    view_port_valid = view_port_ops.check_view_port(view_port_coords=view_port)

    if not view_port_valid:
        view_port_not_ok = GenericErrorResponseMessage(message="The requested view %s rectangle is not valid format: lat1,lng1,lat2,lng2" % view)
        return JsonResponse(json.loads(json.dumps(asdict(view_port_not_ok))), status=400)
    view_box = view_port_ops.build_view_port_box(view_port_coords=view_port)

    view_port_diagonal = view_port_ops.get_view_port_diagonal_length_kms(view_port_coords=view_port)

    # logger.info("View port diagonal %s" % view_port_diagonal)
    if (view_port_diagonal) > 7:
        view_port_too_large_msg = GenericErrorResponseMessage(message="The requested view %s rectangle is too large" % view)
        return JsonResponse(json.loads(json.dumps(asdict(view_port_too_large_msg))), status=413)

    time.sleep(0.5)

    # summary_information_only = True if view_port_area > 22500 else False

    stream_ops = flight_stream_helper.StreamHelperOps()
    pull_cg = stream_ops.get_pull_cg()
    all_streams_messages = pull_cg.read()
    unique_flights = []
    distinct_messages = []
    # Keep only the latest message

    for message in all_streams_messages:
        message_exist = message.data.get("icao_address", None) or None
        if message_exist:
            lat = float(message.data["lat_dd"])
            lng = float(message.data["lon_dd"])
            point = Point(lat, lng)
            point_in_polygon = view_box.contains(point)
            # logger.debug(point_in_polygon)
            if point_in_polygon:
                unique_flights.append(
                    {
                        "timestamp": message.timestamp,
                        "seq": message.sequence,
                        "msg_data": message.data,
                        "address": message.data["icao_address"],
                    }
                )
            else:
                logger.info("Point not in polygon %s " % view_box)
    # sort by date
    unique_flights.sort(key=lambda item: item["timestamp"], reverse=True)

    now = arrow.now().isoformat()
    if unique_flights:
        # Keep only the latest message
        distinct_messages = {i["address"]: i for i in reversed(unique_flights)}.values()

        # except KeyError as ke:
        #     logger.error("Error in sorting distinct messages, key %s name not found" % ke)
        #     error_msg = GenericErrorResponseMessage(message="Error in retrieving flight data")
        #     return JsonResponse(json.loads(json.dumps(asdict(error_msg))), status=500)

        for all_observations_messages in distinct_messages:
            # if summary_information_only:
            #     summary = SummaryFlightsOnly(number_of_flights=len(distinct_messages), timestamp=now)
            #     return JsonResponse(json.loads(json.dumps(asdict(summary))), status=200)
            # else:
            rid_flights = []
            observation_data_dict = {}
            try:
                observation_data = all_observations_messages["msg_data"]
            except KeyError as ke:
                logger.error("Error in data in the stream %s" % ke)
            else:
                try:
                    observation_metadata = observation_data["metadata"]
                    observation_data_dict = json.loads(observation_metadata)
                except KeyError as ke:
                    logger.error("Error in metadata data in the stream %s" % ke)

                telemetry_data_dict = observation_data_dict["telemetry"]
                details_response_dict = observation_data_dict["details_response"]["details"]

                height = RIDHeight(
                    distance=telemetry_data_dict["height"]["distance"],
                    reference=telemetry_data_dict["height"]["reference"],
                )
                position = RIDAircraftPosition(
                    lat=telemetry_data_dict["position"]["lat"],
                    lng=telemetry_data_dict["position"]["lng"],
                    alt=telemetry_data_dict["position"]["alt"],
                    accuracy_h=telemetry_data_dict["position"]["accuracy_h"],
                    accuracy_v=telemetry_data_dict["position"]["accuracy_v"],
                    extrapolated=telemetry_data_dict["position"]["extrapolated"],
                    pressure_altitude=telemetry_data_dict["position"]["pressure_altitude"],
                    height=height,
                )
                current_state = RIDAircraftState(
                    timestamp=RIDTime(
                        value=telemetry_data_dict["timestamp"]["value"],
                        format=telemetry_data_dict["timestamp"]["format"],
                    ),
                    timestamp_accuracy=telemetry_data_dict["timestamp_accuracy"],
                    operational_status=telemetry_data_dict["operational_status"],
                    position=position,
                    track=telemetry_data_dict["track"],
                    speed=telemetry_data_dict["speed"],
                    speed_accuracy=telemetry_data_dict["speed_accuracy"],
                    vertical_speed=telemetry_data_dict["vertical_speed"],
                    height=height,
                )

                operator_details = RIDOperatorDetails(
                    id=details_response_dict["id"],
                    operator_location=RIDLatLngPoint(
                        lat=details_response_dict["operator_location"]["lat"],
                        lng=details_response_dict["operator_location"]["lng"],
                    ),
                    operator_id=details_response_dict["operator_id"],
                    operation_description=details_response_dict["operation_description"],
                    serial_number=details_response_dict["serial_number"],
                    registration_number=details_response_dict["registration_number"],
                    auth_data=RIDAuthData(
                        format=details_response_dict["auth_data"]["format"],
                        data=details_response_dict["auth_data"]["data"],
                    ),
                    aircraft_type=details_response_dict["aircraft_type"],
                )

                current_flight = TelemetryFlightDetails(
                    operator_details=operator_details,
                    id=details_response_dict["id"],
                    aircraft_type=details_response_dict["aircraft_type"],
                    current_state=current_state,
                    simulated=True,
                    recent_positions=[],
                )

                rid_flights.append(current_flight)

        _rid_response = RIDFlightResponse(timestamp=RIDTime(value=now, format="RFC3339"), flights=rid_flights)
        all_flights = []
        for flight in _rid_response.flights:
            flight_dict = asdict(flight, dict_factory=lambda x: {k: v for (k, v) in x if (v is not None)})
            all_flights.append(flight_dict)

        timestamp = asdict(_rid_response.timestamp)

        rid_response = {"timestamp": timestamp, "flights": all_flights}

        return JsonResponse(json.loads(json.dumps(rid_response)), status=200)

    else:
        # show / add metadata it if it does
        rid_response = RIDFlightResponse(timestamp=RIDTime(value=now, format="RFC3339"), flights=[])

        return JsonResponse(json.loads(json.dumps(asdict(rid_response))), status=200)


@api_view(["GET"])
@requires_scopes(["rid.display_provider"])
def get_uss_flight_details(request, flight_id):
    """This is the end point for the rid_qualifier to get details of a flight"""
    r = get_redis()
    flight_details_storage = "flight_details:" + flight_id
    if r.exists(flight_details_storage):
        flight_details_raw = r.get(flight_details_storage)
        flight_details = json.loads(flight_details_raw)

        operator_location = OperatorLocation(
            position=LatLngPoint(
                lat=flight_details["operator_location"]["lat"],
                lng=flight_details["operator_location"]["lng"],
            ),
            altitude=Altitude(value=500, reference="W84", units="M"),
            altitude_type="Takeoff",
        )
        eu_classification = None
        if flight_details.get("eu_classification"):
            eu_classification = UAClassificationEU(
                category=flight_details["eu_classification"]["category"],
                class_=flight_details["eu_classification"]["class"],
            )

        f_detail = RIDOperatorDetails(
            id=flight_details["id"],
            operator_id=flight_details["operator_id"],
            operator_location=operator_location,
            operation_description=flight_details["operation_description"],
            auth_data=RIDAuthData(
                format=int(flight_details["auth_data"]["format"]),
                data=flight_details["auth_data"]["data"],
            ),
            serial_number=flight_details["serial_number"],
            registration_number=flight_details["registration_number"],
            uas_id=UASID(
                specific_session_id=flight_details["uas_id"]["specific_session_id"],
                serial_number=flight_details["uas_id"]["serial_number"],
                registration_id=flight_details["uas_id"]["registration_id"],
                utm_id=flight_details["uas_id"]["utm_id"],
            ),
            eu_classification=eu_classification,
        )

        flight_details_full = OperatorDetailsSuccessResponse(details=f_detail)

        return JsonResponse(
            json.loads(json.dumps(asdict(flight_details_full, dict_factory=lambda x: {k: v for (k, v) in x if (v is not None)}))), status=200
        )
    else:
        fd = FlightDetailsNotFoundMessage(message="The requested flight could not be found")
        return JsonResponse(json.loads(json.dumps(asdict(fd))), status=404)
