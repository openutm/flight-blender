# Create your views here.
import json
import logging
from dataclasses import asdict
from os import environ as env
from typing import List

import arrow
import shapely.geometry
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView
from dotenv import find_dotenv, load_dotenv
from jwcrypto import jwk
from rest_framework import generics
from rest_framework.decorators import api_view

from auth_helper.utils import requires_scopes
from common.data_definitions import ARGONSERVER_READ_SCOPE, ARGONSERVER_WRITE_SCOPE
from common.database_operations import ArgonServerDatabaseReader
from rid_operations import view_port_ops
from rid_operations.data_definitions import (
    RIDAircraftState,
    RIDFlightDetails,
    SignedUnSignedTelemetryObservations,
)
from rid_operations.tasks import stream_rid_telemetry_data

from . import flight_stream_helper
from .data_definitions import (
    FlightObservationsProcessingResponse,
    MessageVerificationFailedResponse,
    SingleAirtrafficObservation,
    TrafficInformationDiscoveryResponse,
)
from .models import SignedTelmetryPublicKey
from .pki_helper import MessageVerifier, ResponseSigningOperations
from .rid_telemetry_helper import ArgonServerTelemetryValidator, NestedDict
from .serializers import SignedTelmetryPublicKeySerializer
from .tasks import start_opensky_network_stream, write_incoming_air_traffic_data

logger = logging.getLogger("django")

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)


class HomeView(TemplateView):
    template_name = "homebase/home.html"


@api_view(["GET"])
def public_key_view(request):
    # Source: https://github.com/jazzband/django-oauth-toolkit/blob/016c6c3bf62c282991c2ce3164e8233b81e3dd4d/oauth2_provider/views/oidc.py#L105
    keys = []
    private_key = env.get("SECRET_KEY", None)

    if private_key:
        try:
            for pem in [private_key]:
                key = jwk.JWK.from_pem(pem.encode("utf8"))
                data = {"alg": "RS256", "use": "sig", "kid": key.thumbprint()}
                data.update(json.loads(key.export_public()))
                keys.append(data)

            response = JsonResponse({"keys": keys})
            response["Access-Control-Allow-Origin"] = "*"
        except Exception:
            response = JsonResponse({})
    else:
        response = JsonResponse({})

    return response


@api_view(["GET"])
def ping(request):
    return JsonResponse({"message": "pong"}, status=200)


@api_view(["POST"])
@requires_scopes([ARGONSERVER_WRITE_SCOPE])
def set_air_traffic(request):
    """This is the main POST method that takes in a request for Air traffic observation and processes the input data"""

    try:
        assert request.headers["Content-Type"] == "application/json"
    except AssertionError:
        msg = {"message": "Unsupported Media Type"}
        return JsonResponse(msg, status=415)
    else:
        req = request.data

    try:
        observations = req["observations"]
    except KeyError:
        msg = FlightObservationsProcessingResponse(
            message="At least one observation is required: observations with a list of observation objects. One or more of these were not found in your JSON request. For sample data see: https://github.com/openskies-sh/airtraffic-data-protocol-development/blob/master/Airtraffic-Data-Protocol.md#sample-traffic-object",
            status=400,
        )

        m = asdict(msg)
        return JsonResponse(m, status=m["status"])

    for observation in observations:
        try:
            lat_dd = observation["lat_dd"]
            lon_dd = observation["lon_dd"]
            altitude_mm = observation["altitude_mm"]
            traffic_source = observation["traffic_source"]
            source_type = observation["source_type"]
            icao_address = observation["icao_address"]

        except KeyError:
            msg = {"message": "One of your observations do not have the mandatory required field"}
            return JsonResponse(msg, status=400)
            # logger.error("Not all data was provided")
        metadata = {}

        if "metadata" in observation.keys():
            metadata = observation["metadata"]

        so = SingleAirtrafficObservation(
            lat_dd=lat_dd,
            lon_dd=lon_dd,
            altitude_mm=altitude_mm,
            traffic_source=traffic_source,
            source_type=source_type,
            icao_address=icao_address,
            metadata=json.dumps(metadata),
        )

        write_incoming_air_traffic_data.delay(json.dumps(asdict(so)))  # Send a job to the task queue

    op = FlightObservationsProcessingResponse(message="OK", status=200)
    return JsonResponse(asdict(op), status=op.status)


@api_view(["GET"])
@requires_scopes([ARGONSERVER_READ_SCOPE])
def get_air_traffic(request):
    """This is the end point for the rid_qualifier test DSS network call once a subscription is updated"""

    # get the view bounding box
    # get the existing subscription id , if no subscription exists, then reject

    try:
        view = request.query_params["view"]
        view_port = [float(i) for i in view.split(",")]
    except Exception:
        incorrect_parameters = {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}
        return JsonResponse(
            json.loads(json.dumps(incorrect_parameters)),
            status=400,
            content_type="application/json",
        )

    view_port_valid = view_port_ops.check_view_port(view_port_coords=view_port)

    b = shapely.geometry.box(view_port[1], view_port[0], view_port[3], view_port[2])
    co_ordinates = list(zip(*b.exterior.coords.xy))
    # Convert bounds vertex list
    vertex_list = []
    for cur_co_ordinate in co_ordinates:
        lat_lng = {"lng": 0, "lat": 0}
        lat_lng["lng"] = cur_co_ordinate[0]
        lat_lng["lat"] = cur_co_ordinate[1]
        vertex_list.append(lat_lng)
    # remove the final point
    vertex_list.pop()

    if view_port_valid:
        stream_ops = flight_stream_helper.StreamHelperOps()
        pull_cg = stream_ops.get_pull_cg()
        all_streams_messages = pull_cg.read()

        unique_flights = []
        # Keep only the latest message
        try:
            for message in all_streams_messages:
                unique_flights.append(
                    {
                        "timestamp": message.timestamp,
                        "seq": message.sequence,
                        "msg_data": message.data,
                        "address": message.data["icao_address"],
                    }
                )
            # sort by date
            unique_flights.sort(key=lambda item: item["timestamp"], reverse=True)
            # Keep only the latest message
            distinct_messages = {i["address"]: i for i in reversed(unique_flights)}.values()

        except KeyError as ke:
            logger.error("Error in sorting distinct messages, ICAO name not defined %s" % ke)
            distinct_messages = []
        all_traffic_observations: List[SingleAirtrafficObservation] = []
        for observation in distinct_messages:
            observation_data = observation["msg_data"]
            observation_metadata = json.loads(observation_data["metadata"])
            so = SingleAirtrafficObservation(
                lat_dd=observation_data["lat_dd"],
                lon_dd=observation_data["lon_dd"],
                altitude_mm=observation_data["altitude_mm"],
                traffic_source=observation_data["traffic_source"],
                source_type=observation_data["source_type"],
                icao_address=observation_data["icao_address"],
                metadata=observation_metadata,
            )
            all_traffic_observations.append(asdict(so))

        return JsonResponse(
            {"observations": all_traffic_observations},
            status=200,
            content_type="application/json",
        )
    else:
        view_port_error = {"message": "A incorrect view port bbox was provided"}
        return JsonResponse(
            json.loads(json.dumps(view_port_error)),
            status=400,
            content_type="application/json",
        )


@api_view(["GET"])
@requires_scopes([ARGONSERVER_READ_SCOPE])
def start_opensky_feed(request):
    # This method takes in a view port as a lat1,lon1,lat2,lon2 coordinate system and for 60 seconds starts the stream of data from the OpenSky Network.

    try:
        view = request.query_params["view"]
        view_port = [float(i) for i in view.split(",")]
    except Exception:
        incorrect_parameters = {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}

        return JsonResponse(
            json.loads(json.dumps(incorrect_parameters)),
            status=400,
            content_type="application/json",
        )

    view_port_valid = view_port_ops.check_view_port(view_port_coords=view_port)

    if view_port_valid:
        start_opensky_network_stream.delay(view_port=json.dumps(view_port))

        return JsonResponse(
            {"message": "Openskies Network stream started"},
            status=200,
            content_type="application/json",
        )
    else:
        view_port_error = {"message": "An incorrect view port bbox was provided"}

        return JsonResponse(
            json.loads(json.dumps(view_port_error)),
            status=400,
            content_type="application/json",
        )


@api_view(["PUT"])
def set_signed_telemetry(request):
    # This endpoint sets signed telemetry details into Argon Server, use this endpoint to securely send signed telemetry information into Argon Server, since the messages are signed, we turn off any auth requirements for tokens and validate against allowed public keys in Argon Server.

    my_message_verifier = MessageVerifier()
    my_argon_server_database_reader = ArgonServerDatabaseReader()
    my_response_signer = ResponseSigningOperations()
    verified = my_message_verifier.verify_message(request)

    if not verified:
        message_verification_failed_response = MessageVerificationFailedResponse(message="Could not verify against public keys setup in Argon Server")
        return JsonResponse(
            asdict(message_verification_failed_response),
            status=400,
            content_type="application/json",
        )
    else:
        raw_data = request.data
        my_telemetry_validator = ArgonServerTelemetryValidator()
        observations_exist = my_telemetry_validator.validate_observation_key_exists(raw_request_data=raw_data)
        if not observations_exist:
            incorrect_parameters = {"message": "A flight observation object with current state and flight details is necessary"}
            return JsonResponse(incorrect_parameters, status=400, content_type="application/json")
        # Get a list of flight data

        rid_observations = raw_data["observations"]

        unsigned_telemetry_observations: List[SignedUnSignedTelemetryObservations] = []
        for flight in rid_observations:
            flight_details_current_states_exist = my_telemetry_validator.validate_flight_details_current_states_exist(flight=flight)
            if not flight_details_current_states_exist:
                incorrect_parameters = {"message": "A flights object with current states, flight details is necessary"}
                return JsonResponse(incorrect_parameters, status=400, content_type="application/json")

            current_states = flight["current_states"]
            flight_details = flight["flight_details"]
            try:
                all_states: List[RIDAircraftState] = my_telemetry_validator.parse_validate_current_states(current_states=current_states)
                rid_flight_details = flight_details["rid_details"]
                f_details: RIDFlightDetails = my_telemetry_validator.parse_validate_rid_details(rid_flight_details=rid_flight_details)

            except KeyError as ke:
                incorrect_parameters = {
                    "message": (
                        "A states object with a fully valid current states is necessary, the parsing the following key encountered errors %s" % ke
                    )
                }
                return JsonResponse(incorrect_parameters, status=400, content_type="application/json")

            single_observation_set = SignedUnSignedTelemetryObservations(current_states=all_states, flight_details=f_details)

            unsigned_telemetry_observations.append(asdict(single_observation_set, dict_factory=NestedDict))

            operation_id = f_details.id
            now = arrow.now().isoformat()
            relevant_operation_ids_qs = my_argon_server_database_reader.get_current_flight_declaration_ids(timestamp=now)
            relevant_operation_ids = [str(o) for o in relevant_operation_ids_qs.all()]
            if operation_id in relevant_operation_ids:
                # Get flight state:
                flight_operation = my_argon_server_database_reader.get_flight_declaration_by_id(flight_declaration_id=operation_id)

                if flight_operation.state in [
                    2,
                    3,
                    4,
                ]:  # Activated, Contingent, Non-conforming
                    stream_rid_telemetry_data.delay(rid_telemetry_observations=json.dumps(unsigned_telemetry_observations))
                else:
                    operation_state_incorrect_msg = {
                        "message": "The operation ID: {operation_id} is not one of Activated, Contingent or Non-conforming states in Argon Server, telemetry submission will be ignored, please change the state first.".format(
                            operation_id=operation_id
                        )
                    }
                    return JsonResponse(
                        operation_state_incorrect_msg,
                        status=400,
                        content_type="application/json",
                    )

            else:
                incorrect_operation_id_msg = {
                    "message": "The operation ID: {operation_id} in the flight details object provided does not match any current operation in Argon Server".format(
                        operation_id=operation_id
                    )
                }
                return JsonResponse(
                    incorrect_operation_id_msg,
                    status=400,
                    content_type="application/json",
                )

        submission_success = {"message": "Telemetry data successfully submitted"}
        content_digest = my_response_signer.generate_content_digest(submission_success)
        signed_data = my_response_signer.sign_json_via_django(submission_success)
        submission_success["signed"] = signed_data
        response = JsonResponse(submission_success, status=201, content_type="application/json")
        response["Content-Digest"] = content_digest
        response["req"] = request.headers["Signature"]

        return response


@api_view(["GET"])
@requires_scopes([ARGONSERVER_READ_SCOPE])
def traffic_information_discovery_view(request):
    try:
        view = request.query_params["view"]
        view_port = [float(i) for i in view.split(",")]
    except Exception:
        incorrect_parameters = {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}

        return JsonResponse(
            json.loads(json.dumps(incorrect_parameters)),
            status=400,
            content_type="application/json",
        )

    view_port_valid = view_port_ops.check_view_port(view_port_coords=view_port)

    if not view_port_valid:
        view_port_error = {"message": "An incorrect view port bbox was provided"}

        return JsonResponse(
            json.loads(json.dumps(view_port_error)),
            status=400,
            content_type="application/json",
        )

    data_format = request.query_params.get("format", None)

    if data_format and data_format == "asterix":
        incorrect_parameters = {"message": "A format query parameter can only be 'mavlink' since 'asterix' is not supported. "}
        return JsonResponse(
            json.loads(json.dumps(incorrect_parameters)),
            status=400,
            content_type="application/json",
        )

    traffic_information_url = env.get("TRAFFIC_INFORMATION_URL", "https://not_implemented_yet")

    traffic_information_discovery_response = TrafficInformationDiscoveryResponse(
        message="Traffic Information Discovery information successfully retrieved",
        url=traffic_information_url,
        description="Start a QUIC query to the traffic information url service to get traffic information in the specified view port",
    )

    return JsonResponse(asdict(traffic_information_discovery_response), status=200, content_type="application/json")


@api_view(["PUT"])
@requires_scopes([ARGONSERVER_WRITE_SCOPE])
def set_telemetry(request):
    """A RIDOperatorDetails object is posted here"""
    # This endpoints receives data from GCS and / or flights and processes telemetry data.
    # TODO: Use dacite to parse incoming json into a dataclass
    raw_data = request.data

    my_argon_server_database_reader = ArgonServerDatabaseReader()
    my_telemetry_validator = ArgonServerTelemetryValidator()

    observations_exist = my_telemetry_validator.validate_observation_key_exists(raw_request_data=raw_data)
    if not observations_exist:
        incorrect_parameters = {"message": "A flight observation object with current state and flight details is necessary"}
        return JsonResponse(incorrect_parameters, status=400, content_type="application/json")
    # Get a list of flight data

    rid_observations = raw_data["observations"]

    unsigned_telemetry_observations: List[SignedUnSignedTelemetryObservations] = []
    for flight in rid_observations:
        flight_details_current_states_exist = my_telemetry_validator.validate_flight_details_current_states_exist(flight=flight)
        if not flight_details_current_states_exist:
            incorrect_parameters = {"message": "A flights object with current states, flight details is necessary"}
            return JsonResponse(incorrect_parameters, status=400, content_type="application/json")

        current_states = flight["current_states"]
        flight_details = flight["flight_details"]
        try:
            all_states: List[RIDAircraftState] = my_telemetry_validator.parse_validate_current_states(current_states=current_states)
            rid_flight_details = flight_details["rid_details"]
            f_details: RIDFlightDetails = my_telemetry_validator.parse_validate_rid_details(rid_flight_details=rid_flight_details)

        except KeyError as ke:
            incorrect_parameters = {
                "message": "A states object with a fully valid current states is necessary, the parsing the following key encountered errors %s" % ke
            }
            return JsonResponse(incorrect_parameters, status=400, content_type="application/json")

        single_observation_set = SignedUnSignedTelemetryObservations(current_states=all_states, flight_details=f_details)

        unsigned_telemetry_observations.append(asdict(single_observation_set, dict_factory=NestedDict))
        operation_id = f_details.id
        now = arrow.now().isoformat()
        relevant_operation_ids_qs = my_argon_server_database_reader.get_current_flight_declaration_ids(now=now)
        relevant_operation_ids = [str(o) for o in relevant_operation_ids_qs.all()]
        if operation_id in list(relevant_operation_ids):
            # Get flight state:
            flight_operation = my_argon_server_database_reader.get_flight_declaration_by_id(flight_declaration_id=operation_id)

            if flight_operation.state in [
                2,
                3,
                4,
            ]:  # Activated, Contingent, Non-conforming
                stream_rid_telemetry_data.delay(rid_telemetry_observations=json.dumps(unsigned_telemetry_observations))
            else:
                operation_state_incorrect_msg = {
                    "message": "The operation ID: {operation_id} is not one of Activated, Contingent or Non-conforming states in Argon Server, telemetry submission will be ignored, please change the state first.".format(
                        operation_id=operation_id
                    )
                }
                return JsonResponse(
                    operation_state_incorrect_msg,
                    status=400,
                    content_type="application/json",
                )

        else:
            incorrect_operation_id_msg = {
                "message": "The operation ID: {operation_id} in the flight details object provided does not match any current operation in Argon Server".format(
                    operation_id=operation_id
                )
            }
            return JsonResponse(incorrect_operation_id_msg, status=400, content_type="application/json")

    submission_success = {"message": "Telemetry data successfully submitted"}
    return JsonResponse(submission_success, status=201, content_type="application/json")


@method_decorator(requires_scopes(["geo-awareness.test"]), name="dispatch")
class SignedTelmetryPublicKeyList(generics.ListCreateAPIView):
    queryset = SignedTelmetryPublicKey.objects.all()
    serializer_class = SignedTelmetryPublicKeySerializer


@method_decorator(requires_scopes(["geo-awareness.test"]), name="dispatch")
class SignedTelmetryPublicKeyDetail(generics.RetrieveUpdateDestroyAPIView):
    queryset = SignedTelmetryPublicKey.objects.all()
    serializer_class = SignedTelmetryPublicKeySerializer
