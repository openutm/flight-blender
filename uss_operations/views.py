import json
import time
import uuid
from dataclasses import asdict
from enum import Enum
from uuid import UUID

import arrow
from dacite import Config, from_dict
from django.http import JsonResponse
from dotenv import find_dotenv, load_dotenv

# Create your views here.
from loguru import logger
from rest_framework.decorators import api_view

import rid_operations.view_port_ops as view_port_ops
from auth_helper.utils import requires_scopes
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from common.utils import EnhancedJSONEncoder
from constraint_operations.data_definitions import PutConstraintDetailsParameters
from flight_feed_operations import flight_stream_helper
from rid_operations.data_definitions import (
    UASID,
    OperatorLocation,
    UAClassificationEU,
)
from rid_operations.rid_utils import RIDAuthData, RIDFlightDetails
from scd_operations.dss_scd_helper import (
    VolumesConverter,
)
from scd_operations.scd_data_definitions import CompositeOperationalIntentPayload

from .rid_data_definitions import (
    GetFlightsResponse,
    RIDAircraftPosition,
    RIDAircraftState,
    RIDFlight,
    RIDFormat,
    RIDHeight,
    RIDTime,
)
from .uss_data_definitions import (
    ErrorReport,
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

load_dotenv(find_dotenv())


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
    database_writer = FlightBlenderDatabaseWriter()
    my_geo_json_converter = VolumesConverter()
    op_int_update_details_data = request.data

    incoming_update_payload = from_dict(data_class=UpdateChangedOpIntDetailsPost, data=op_int_update_details_data)
    # Write the operational Intent
    operation_id_str = incoming_update_payload.operational_intent_id

    logger.info("Incoming data for operation ID %s" % operation_id_str)

    logger.info(incoming_update_payload)

    # Update the subscription state

    if incoming_update_payload.operational_intent:
        updated_operational_intent_reference = incoming_update_payload.operational_intent.reference

        update_operational_intent_details = incoming_update_payload.operational_intent.details

        database_writer.create_or_update_peer_operational_intent_details(
            peer_operational_intent_id=operation_id_str,
            operational_intent_details=update_operational_intent_details,
        )

        database_writer.create_or_update_peer_operational_intent_reference(
            peer_operational_intent_reference_id=operation_id_str,
            peer_operational_intent_reference=updated_operational_intent_reference,
        )
        if update_operational_intent_details.volumes:
            all_volumes = update_operational_intent_details.volumes

        elif update_operational_intent_details.off_nominal_volumes:
            all_volumes = update_operational_intent_details.off_nominal_volumes

        start_datetime = all_volumes[0].time_start.value
        end_datetime = all_volumes[0].time_end.value

        my_geo_json_converter.convert_volumes_to_geojson(volumes=all_volumes)
        view_rect_bounds = my_geo_json_converter.get_bounds()

        operational_intent_full_details = CompositeOperationalIntentPayload(
            bounds=view_rect_bounds,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            alt_max=50,
            alt_min=25,
            operational_intent_reference_id=operation_id_str,
            operational_intent_details_id=operation_id_str,
        )

        database_writer.create_or_update_peer_composite_operational_intent(
            operation_id=operation_id_str,
            composite_operational_intent=operational_intent_full_details,
        )

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
        telemetry=VehicleTelemetry(
            time_measured=Time(format="RFC3339", value=arrow.now().isoformat()),
            position=None,
            velocity=None,
        ),
        next_telemetry_opportunity=Time(format="RFC3339", value=five_seconds_from_now.isoformat()),
    )
    return JsonResponse(
        json.loads(json.dumps(asdict(telemetry_response), cls=EnhancedJSONEncoder)),
        status=200,
    )


@api_view(["POST"])
@requires_scopes(
    [
        "utm.strategic_coordination",
        "utm.constraint_processing",
        "utm.constraint_management",
        "utm.conformance_monitoring_sa",
        "utm.availability_arbitration",
    ],
    allow_any=True,
)
def peer_uss_report_notification(request):
    error_report = from_dict(data_class=ErrorReport, data=request.data, config=Config(cast=[Enum]))
    logger.info("Error report received: %s" % error_report)
    report_id = str(uuid.uuid4())
    error_report.report_id = report_id

    return JsonResponse(
        json.loads(json.dumps(asdict(error_report), cls=EnhancedJSONEncoder)),
        status=201,
    )


@requires_scopes(["utm.constraint_processing"])
def uss_constraint_details(request, constraint_id):
    my_database_reader = FlightBlenderDatabaseReader()
    constraint_id_exists = my_database_reader.check_constraint_id_exists(constraint_id=constraint_id)
    if constraint_id_exists:
        constraint_details = my_database_reader.get_constraint_details(constraint_id=constraint_id)
        if constraint_details:
            return JsonResponse(
                json.loads(json.dumps(constraint_details, cls=EnhancedJSONEncoder)),
                status=200,
            )
        else:
            not_found_response = GenericErrorResponseMessage(message="Requested Constraint with id %s not found" % str(constraint_id))
            return JsonResponse(
                json.loads(json.dumps(not_found_response, cls=EnhancedJSONEncoder)),
                status=404,
            )
    else:
        not_found_response = GenericErrorResponseMessage(message="Requested Constraint with id %s not found" % str(constraint_id))
        return JsonResponse(
            json.loads(json.dumps(not_found_response, cls=EnhancedJSONEncoder)),
            status=404,
        )


@api_view(["POST"])
@requires_scopes(["utm.constraint_processing"])
def uss_update_constraint_details(request):
    my_database_reader = FlightBlenderDatabaseReader()
    my_database_writer = FlightBlenderDatabaseWriter()
    constraint_update_details = request.data
    constraint_update_detail = from_dict(data_class=PutConstraintDetailsParameters, data=constraint_update_details)

    constraint_id = constraint_update_detail.constraint_id
    constraint_id_exists = my_database_reader.check_constraint_id_exists(constraint_id=constraint_id)
    if constraint_id_exists and constraint_update_detail.constraint:
        my_database_writer.write_constraint_details(constraint_id=constraint_id, constraint=constraint_update_detail.constraint)
    else:
        logger.error("Constraint ID %s does not exist" % constraint_id)

    constraint_reference_id = constraint_update_detail.constraint_id
    constraint_reference_exists = my_database_reader.check_constraint_reference_id_exists(constraint_reference_id=constraint_reference_id)
    if constraint_reference_exists and constraint_update_detail.constraint:
        my_database_writer.write_constraint_reference_details(constraint=constraint_update_detail.constraint)
    else:
        logger.error("Constraint reference ID %s does not exist" % constraint_reference_id)
    return JsonResponse({}, status=204)


@api_view(["GET"])
@requires_scopes(["utm.strategic_coordination"])
def uss_operational_intent_details(request, opint_id):
    my_database_reader = FlightBlenderDatabaseReader()
    flight_operational_intent_reference = my_database_reader.get_flight_operational_intent_reference_by_id(str(opint_id))
    if flight_operational_intent_reference:
        operational_intent_id = str(flight_operational_intent_reference.declaration.id)

        stored_details = my_database_reader.get_composite_operational_intent_by_declaration_id(flight_declaration_id=operational_intent_id)
        details_full = stored_details.operational_intent_details
        reference_full = stored_details.operational_intent_reference
        # Load existing opint details
        stored_operational_intent_id = reference_full.id
        stored_manager = reference_full.manager
        stored_uss_availability = reference_full.uss_availability
        stored_version = reference_full.version
        stored_state = reference_full.state
        stored_ovn = reference_full.ovn
        stored_uss_base_url = reference_full.uss_base_url
        stored_subscription_id = reference_full.subscription_id

        stored_volumes = json.loads(details_full.volumes)

        for v in stored_volumes:
            if "outline_circle" in v["volume"].keys():
                if not v["volume"]["outline_circle"]:
                    v["volume"].pop("outline_circle")

        stored_priority = details_full.priority
        stored_off_nominal_volumes = json.loads(details_full.off_nominal_volumes)
        for v in stored_off_nominal_volumes:
            if "outline_circle" in v["volume"].keys():
                if not v["volume"]["outline_circle"]:
                    v["volume"].pop("outline_circle")

        reference = OperationalIntentReferenceDSSResponse(
            id=str(stored_operational_intent_id),
            manager=stored_manager,
            uss_availability=stored_uss_availability,
            version=int(stored_version),
            state=stored_state,
            ovn=stored_ovn,
            time_start=Time(format="RFC3339", value=reference_full.time_start.isoformat()),
            time_end=Time(format="RFC3339", value=reference_full.time_end.isoformat()),
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

    # Get the last observation of the flight telemetry
    obs_helper = flight_stream_helper.ObservationReadOperations(view_port_box=view_box)
    all_flights_telemetry_data = obs_helper.get_closest_observation_for_now(now=arrow.now())
    # Get the latest telemetry

    if not all_flights_telemetry_data:
        logger.info(f"No telemetry data found for view port {view_port}")

    now = arrow.now().isoformat()
    if all_flights_telemetry_data:
        for observation_data in all_flights_telemetry_data:
            # if summary_information_only:
            #     summary = SummaryFlightsOnly(number_of_flights=len(distinct_messages), timestamp=now)
            #     return JsonResponse(json.loads(json.dumps(asdict(summary))), status=200)
            # else:
            rid_flights = []
            observation_data_dict = {}

            try:
                observation_data_dict = observation_data.metadata

            except KeyError as ke:
                logger.error("Error in metadata data in the stream %s" % ke)

            telemetry_data_dict = observation_data_dict["telemetry"]
            # details_response_dict = observation_data_dict["details_response"]["details"]

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
            )

            current_flight = RIDFlight(
                id=observation_data_dict["injection_id"],
                aircraft_type=observation_data_dict["aircraft_type"],
                current_state=current_state,
                simulated=True,
                recent_positions=[],
            )

            rid_flights.append(current_flight)

        all_flights = []
        for flight in rid_flights:
            flight_dict = asdict(flight, dict_factory=lambda x: {k: v for (k, v) in x if (v is not None)})
            all_flights.append(flight_dict)

        rid_response = GetFlightsResponse(timestamp=RIDTime(value=now, format=RIDFormat.RFC3339), flights=all_flights)

    else:
        # show / add metadata it if it does
        rid_response = GetFlightsResponse(timestamp=RIDTime(value=now, format=RIDFormat.RFC3339), flights=[])

    return JsonResponse(json.loads(json.dumps(asdict(rid_response))), status=200)


@api_view(["GET"])
@requires_scopes(["rid.display_provider"])
def get_uss_flight_details(request, flight_id):
    """This is the end point for the rid_qualifier to get details of a flight"""

    my_database_reader = FlightBlenderDatabaseReader()
    flight_details_exists = my_database_reader.check_flight_details_exist(flight_detail_id=flight_id)
    if flight_details_exists:
        flight_details = my_database_reader.get_flight_details_by_id(flight_detail_id=flight_id)
        _operator_location = json.loads(flight_details.operator_location)
        operator_location = from_dict(
            data_class=OperatorLocation,
            data=_operator_location,
            config=Config(cast=[Enum]),
        )

        eu_classification = None
        _eu_classification = json.loads(flight_details.eu_classification)
        if _eu_classification.keys():
            eu_classification = UAClassificationEU(
                category=_eu_classification["category"],
                class_=_eu_classification["class"],
            )
        uas_id = None
        _uas_id = json.loads(flight_details.uas_id)
        if _uas_id.keys():
            uas_id = UASID(
                specific_session_id=_uas_id["specific_session_id"],
                serial_number=_uas_id["serial_number"],
                registration_id=_uas_id["registration_id"],
                utm_id=_uas_id["utm_id"],
            )
        auth_data = None
        _auth_data = json.loads(flight_details.auth_data)
        if _auth_data.keys():
            auth_data = RIDAuthData(
                format=int(_auth_data["format"]),
                data=_auth_data["data"],
            )
        f_detail = RIDFlightDetails(
            id=str(flight_details.id),
            operator_id=flight_details.operator_id,
            operator_location=operator_location,
            operation_description=flight_details.operation_description,
            auth_data=auth_data,
            uas_id=uas_id,
            eu_classification=eu_classification,
        )

        flight_details_full = OperatorDetailsSuccessResponse(details=f_detail)

        return JsonResponse(
            json.loads(
                json.dumps(
                    asdict(
                        flight_details_full,
                        dict_factory=lambda x: {k: v for (k, v) in x if (v is not None)},
                    )
                )
            ),
            status=200,
        )
    else:
        fd = FlightDetailsNotFoundMessage(message="The requested flight could not be found")
        return JsonResponse(json.loads(json.dumps(asdict(fd))), status=404)
