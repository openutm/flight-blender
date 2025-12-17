# Create your views here.
import json
import uuid
from dataclasses import asdict
from os import environ as env

import arrow
import dacite
from django.http import JsonResponse
from django.utils.decorators import method_decorator
from django.views.generic import TemplateView
from dotenv import find_dotenv, load_dotenv
from jwcrypto import jwk
from loguru import logger
from marshmallow import ValidationError
from rest_framework import generics
from rest_framework.decorators import api_view

from auth_helper.utils import requires_scopes
from common.data_definitions import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from common.database_operations import FlightBlenderDatabaseReader
from rid_operations import view_port_ops
from rid_operations.data_definitions import (
    SignedUnSignedTelemetryObservations,
)
from rid_operations.tasks import stream_rid_telemetry_data

from . import flight_stream_helper
from .data_definitions import (
    FlightObservationsProcessingResponse,
    MessageVerificationFailedResponse,
    ObservationSchema,
    SingleAirtrafficObservation,
    TrafficInformationDiscoveryResponse,
)
from .models import SignedTelmetryPublicKey
from .pki_helper import MessageVerifier, ResponseSigningOperations
from .rid_telemetry_helper import FlightBlenderTelemetryValidator, NestedDict
from .serializers import SignedTelmetryPublicKeySerializer
from .tasks import start_opensky_network_stream, write_incoming_air_traffic_data

ENV_FILE = find_dotenv()
if ENV_FILE:
    load_dotenv(ENV_FILE)


class HomeView(TemplateView):
    template_name = "homebase/home.html"


class ASGIHomeView(TemplateView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        session_id = self.request.GET.get("session_id", "00000000-0000-0000-0000-000000000000")
        context["session_id"] = session_id
        return context

    template_name = "homebase/realtime.html"


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
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def set_air_traffic(request, session_id):
    """
    This is the main POST method that takes in a request for Air traffic observation and processes the input data.

    Args:
        request (HttpRequest): The HTTP request object containing air traffic observation data.

    Returns:
        JsonResponse: A JSON response indicating the result of the processing.

    Raises:
        AssertionError: If the Content-Type of the request is not 'application/json'.
        KeyError: If required keys are missing in the request data.

    The function performs the following steps:
    1. Checks if the Content-Type of the request is 'application/json'.
    2. Extracts the 'observations' from the request data.
    3. Iterates through each observation and extracts required fields.
    4. If any required field is missing in an observation, returns a 400 status with an error message.
    5. If 'metadata' is present in an observation, it is included in the processing.
    6. Creates a SingleAirtrafficObservation object for each observation and sends it to the task queue.
    7. Returns a 200 status with an "OK" message upon successful processing.

    Example:
        A sample request JSON:
        {
            "observations": [
                {
                    "lat_dd": 37.7749,
                    "lon_dd": -122.4194,
                    "altitude_mm": 10000,
                    "traffic_source": "ADS-B",
                    "source_type": "sensor",
                    "icao_address": "ABC123",
                    "metadata": {
                        "speed": 500,
                        "heading": 90
                    }
                }
            ]
        }
    """

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
    schema = ObservationSchema()
    validated_observations = []

    # Validate all observations first
    for observation in observations:
        try:
            validated_data = schema.load(observation)
            validated_observations.append({**validated_data, "metadata": observation.get("metadata", {})})
        except ValidationError as err:
            msg = {
                "message": "One of your observations do not have mandatory required fields or has incorrect data types. Please see error details.",
                "errors": err.messages,
            }
            return JsonResponse(msg, status=400)

    # Process validated observations
    session_id_str = str(session_id) if session_id else "00000000-0000-0000-0000-000000000000"

    for validated_data in validated_observations:
        so = SingleAirtrafficObservation(
            session_id=session_id_str,
            lat_dd=validated_data["lat_dd"],
            lon_dd=validated_data["lon_dd"],
            altitude_mm=validated_data["altitude_mm"],
            traffic_source=validated_data["traffic_source"],
            source_type=validated_data["source_type"],
            icao_address=validated_data["icao_address"],
            metadata=validated_data["metadata"],
        )
        write_incoming_air_traffic_data.delay(json.dumps(asdict(so)))

    op = FlightObservationsProcessingResponse(message="OK", status=201)
    return JsonResponse(asdict(op), status=op.status)


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def get_air_traffic(request, session_id):
    """
    This endpoint retrieves air traffic data within a specified view bounding box.

    Args:
        request (HttpRequest): The HTTP request object containing query parameters.

    Returns:
        JsonResponse: A JSON response containing air traffic observations within the specified view bounding box.

    Raises:
        JsonResponse: If the view bounding box is not provided or is invalid, returns a 400 status with an error message.
    """

    try:
        view = request.query_params["view"]
        view_port = list(map(float, view.split(",")))
    except (KeyError, ValueError):
        incorrect_parameters = {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}
        return JsonResponse(incorrect_parameters, status=400, content_type="application/json")

    view_port_valid = view_port_ops.check_view_port(view_port_coords=view_port)

    if not view_port_valid:
        view_port_error = {"message": "A incorrect view port bbox was provided"}
        return JsonResponse(
            json.loads(json.dumps(view_port_error)),
            status=400,
            content_type="application/json",
        )
    try:
        view_port_box = view_port_ops.build_view_port_box(view_port_coords=view_port)
        my_observation_reader = flight_stream_helper.ObservationReadOperations(view_port_box=view_port_box)
        all_observations = my_observation_reader.get_flight_observations(session_id=session_id)

        # Filter the all observations to get the latest one for each ICAO address
        latest_observations = {}
        for observation in all_observations:
            if observation.icao_address not in latest_observations:
                latest_observations[observation.icao_address] = observation
            else:
                # Compare timestamps to keep the latest one
                if observation.created_at > latest_observations[observation.icao_address].created_at:
                    latest_observations[observation.icao_address] = observation

        logger.info("Distinct messages: %s" % len(latest_observations))
    except KeyError as ke:
        # Log error if ICAO address is not defined in any message
        logger.error("Error in sorting distinct messages, ICAO name not defined %s" % ke)

    all_traffic_observations = []
    for icao_address in latest_observations:
        observation = latest_observations[icao_address]
        so = SingleAirtrafficObservation(
            lat_dd=observation.latitude_dd,
            lon_dd=observation.longitude_dd,
            altitude_mm=observation.altitude_mm,
            traffic_source=observation.traffic_source,
            source_type=observation.source_type,
            icao_address=icao_address,
            metadata=observation.metadata,
            session_id=observation.session_id,
        )
        all_traffic_observations.append(asdict(so))

    return JsonResponse(
        {"observations": all_traffic_observations},
        status=200,
        content_type="application/json",
    )


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def start_opensky_feed(request):
    """
    Starts the OpenSky Network data stream for a specified viewport.
    This method takes in a viewport as a lat1, lon1, lat2, lon2 coordinate system and starts the stream of data from the OpenSky Network for 60 seconds.
    Args:
        request (HttpRequest): The HTTP request object containing query parameters.
    Returns:
        JsonResponse: A JSON response indicating the result of the operation.
            - If successful, returns a message indicating the stream has started with status 200.
            - If the viewport parameters are incorrect or invalid, returns an error message with status 400.
    Raises:
        None
    Notes:
        The viewport must be provided as a query parameter named "view" with four comma-separated values representing the bounding box coordinates: minx, miny, maxx, and maxy.
    """
    try:
        view = request.query_params["view"]
        view_port = [float(i) for i in view.split(",")]
    except (KeyError, ValueError):
        incorrect_parameters = {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"}
        return JsonResponse(incorrect_parameters, status=400, content_type="application/json")

    if not view_port_ops.check_view_port(view_port_coords=view_port):
        view_port_error = {"message": "An incorrect view port bbox was provided"}
        return JsonResponse(view_port_error, status=400, content_type="application/json")

    sesion_id = uuid.uuid4()
    start_opensky_network_stream.delay(view_port=json.dumps(view_port), session_id=str(sesion_id))
    return JsonResponse(
        {"message": "Openskies Network stream started"},
        status=200,
        content_type="application/json",
    )


@api_view(["PUT"])
def set_signed_telemetry(request):
    """
    This endpoint sets signed telemetry details into Flight Blender. It securely receives signed telemetry information
    and validates it against allowed public keys in Flight Blender. Since the messages are signed, authentication
    requirements for tokens are turned off.
    Args:
        request (HttpRequest): The HTTP request object containing the signed telemetry data.
    Returns:
        JsonResponse: A JSON response indicating the result of the operation. Possible responses include:
            - 400 Bad Request: If message verification fails, required observation keys are missing, flight details
              or current states are invalid, or the operation ID does not match any current operation in Flight Blender.
            - 201 Created: If telemetry data is successfully submitted.
    The function performs the following steps:
        1. Verifies the signed message using the MessageVerifier.
        2. Validates the presence of required observation keys in the request data.
        3. Iterates through the flight observations and validates the presence of flight details and current states.
        4. Parses and validates the current states and flight details.
        5. Checks if the operation ID exists in the current flight declarations and if the operation state is valid.
        6. Streams the telemetry data if the operation state is valid.
        7. Returns appropriate JSON responses based on the validation and processing results.
    """
    my_message_verifier = MessageVerifier()
    my_flight_blender_database_reader = FlightBlenderDatabaseReader()
    my_response_signer = ResponseSigningOperations()
    verified = my_message_verifier.verify_message(request)

    if not verified:
        return JsonResponse(
            asdict(MessageVerificationFailedResponse(message="Could not verify against public keys setup in Flight Blender")),
            status=400,
            content_type="application/json",
        )

    raw_data = request.data
    my_telemetry_validator = FlightBlenderTelemetryValidator()

    if not my_telemetry_validator.validate_observation_key_exists(raw_request_data=raw_data):
        return JsonResponse(
            {"message": "A flight observation object with current state and flight details is necessary"},
            status=400,
            content_type="application/json",
        )

    rid_observations = raw_data["observations"]
    unsigned_telemetry_observations = []

    for flight in rid_observations:
        if not my_telemetry_validator.validate_flight_details_current_states_exist(flight=flight):
            return JsonResponse(
                {"message": "A flights object with current states, flight details is necessary"},
                status=400,
                content_type="application/json",
            )

        current_states = flight["current_states"]
        flight_details = flight["flight_details"]

        try:
            all_states = my_telemetry_validator.parse_validate_current_states(current_states=current_states)
            f_details = my_telemetry_validator.parse_validate_rid_details(rid_flight_details=flight_details["rid_details"])
        except KeyError as ke:
            return JsonResponse(
                {"message": f"A states object with a fully valid current states is necessary, the parsing the following key encountered errors {ke}"},
                status=400,
                content_type="application/json",
            )

        single_observation_set = SignedUnSignedTelemetryObservations(current_states=all_states, flight_details=f_details)
        unsigned_telemetry_observations.append(asdict(single_observation_set, dict_factory=NestedDict))

        operation_id = f_details.id
        now = arrow.now().datetime
        flight_declaration_active = my_flight_blender_database_reader.check_flight_declaration_active(flight_declaration_id=operation_id, now=now)

        if flight_declaration_active:
            flight_operation = my_flight_blender_database_reader.get_flight_declaration_by_id(flight_declaration_id=operation_id)

            if flight_operation.state in [
                2,
                3,
                4,
            ]:  # Activated, Contingent, Non-conforming
                stream_rid_telemetry_data.delay(rid_telemetry_observations=json.dumps(unsigned_telemetry_observations))
            else:
                return JsonResponse(
                    {
                        "message": f"The operation ID: {operation_id} is not one of Activated, Contingent or Non-conforming states in Flight Blender, telemetry submission will be ignored, please change the state first."
                    },
                    status=400,
                    content_type="application/json",
                )
        else:
            return JsonResponse(
                {
                    "message": f"The operation ID: {operation_id} in the flight details object provided does not match any current operation in Flight Blender"
                },
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
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def traffic_information_discovery_view(request):
    """
    Handles the traffic information discovery request.
    This view processes the request to retrieve traffic information within a specified view port.
    It validates the view port parameters and checks the format of the requested data.
    Parameters:
    request (HttpRequest): The HTTP request object containing query parameters.
    Query Parameters:
    - view (str): A comma-separated string representing the bounding box coordinates (minx, miny, maxx, maxy).
    - format (str, optional): The format of the requested data. Only 'mavlink' is supported.
    Returns:
    JsonResponse: A JSON response containing the traffic information discovery details or an error message.
    Response Codes:
    - 200: Traffic information discovery information successfully retrieved.
    - 400: Bad request due to incorrect parameters or unsupported format.
    """
    try:
        view = request.query_params["view"]
        view_port = [float(i) for i in view.split(",")]
    except (KeyError, ValueError):
        return JsonResponse(
            {"message": "A view bbox is necessary with four values: minx, miny, maxx and maxy"},
            status=400,
            content_type="application/json",
        )

    if not view_port_ops.check_view_port(view_port_coords=view_port):
        return JsonResponse(
            {"message": "An incorrect view port bbox was provided"},
            status=400,
            content_type="application/json",
        )

    data_format = request.query_params.get("format", None)
    if data_format and data_format != "mavlink":
        return JsonResponse(
            {"message": "A format query parameter can only be 'mavlink' since 'asterix' is not supported."},
            status=400,
            content_type="application/json",
        )

    traffic_information_url = env.get("TRAFFIC_INFORMATION_URL", "https://not_implemented_yet")
    response_data = TrafficInformationDiscoveryResponse(
        message="Traffic Information Discovery information successfully retrieved",
        url=traffic_information_url,
        description="Start a QUIC query to the traffic information url service to get traffic information in the specified view port",
    )

    return JsonResponse(asdict(response_data), status=200, content_type="application/json")


@api_view(["PUT"])
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def set_telemetry(request):
    """
    A view to handle the submission of telemetry data from GCS and/or flights.
    This endpoint receives telemetry data in JSON format, validates it, and processes it accordingly.
    The data is expected to contain flight observations with current states and flight details.
    Args:
        request (HttpRequest): The HTTP request object containing telemetry data in JSON format.
    Returns:
        JsonResponse: A JSON response indicating the result of the telemetry data submission.
                      - 201 status code if the submission is successful.
                      - 400 status code if there are validation errors or incorrect parameters.
    Raises:
        KeyError: If required keys are missing in the telemetry data.
    Notes:
        - Uses dacite to parse incoming JSON into a dataclass.
        - Validates the presence of necessary keys and the correctness of the data.
        - Checks if the operation ID exists and is in the correct state before processing telemetry data.
    """

    raw_data = request.data

    my_flight_blender_database_reader = FlightBlenderDatabaseReader()
    my_telemetry_validator = FlightBlenderTelemetryValidator()

    observations_exist = my_telemetry_validator.validate_observation_key_exists(raw_request_data=raw_data)
    if not observations_exist:
        incorrect_parameters = {"message": "A flight observation object with current state and flight details is necessary"}
        return JsonResponse(incorrect_parameters, status=400, content_type="application/json")
    # Get a list of flight data

    rid_observations = raw_data["observations"]

    unsigned_telemetry_observations: list[SignedUnSignedTelemetryObservations] = []
    for flight in rid_observations:
        if not my_telemetry_validator.validate_flight_details_current_states_exist(flight=flight):
            return JsonResponse(
                {"message": "A flights object with current states, flight details is necessary"},
                status=400,
                content_type="application/json",
            )

        current_states = flight["current_states"]
        flight_details = flight["flight_details"]
        try:
            all_states = my_telemetry_validator.parse_validate_current_states(current_states=current_states)
            f_details = my_telemetry_validator.parse_validate_rid_details(rid_flight_details=flight_details)
        except KeyError as ke:
            return JsonResponse(
                {"message": f"A states object with a fully valid current states is necessary, the parsing the following key encountered errors {ke}"},
                status=400,
                content_type="application/json",
            )
        except dacite.exceptions.WrongTypeError as wte:
            return JsonResponse(
                {"message": f"The parsing of telemetry object raised the following errors {wte}"},
                status=400,
                content_type="application/json",
            )

        single_observation_set = SignedUnSignedTelemetryObservations(current_states=all_states, flight_details=f_details)
        unsigned_telemetry_observations.append(asdict(single_observation_set, dict_factory=NestedDict))

        operation_id = f_details.id
        now = arrow.now().datetime
        flight_declaration_active = my_flight_blender_database_reader.check_flight_declaration_active(flight_declaration_id=operation_id, now=now)

        if not flight_declaration_active:
            return JsonResponse(
                {
                    "message": f"The operation ID: {operation_id} in the flight details object provided does not match any current operation in Flight Blender"
                },
                status=400,
                content_type="application/json",
            )

        flight_operation = my_flight_blender_database_reader.get_flight_declaration_by_id(flight_declaration_id=operation_id)

        allowed_states = [2, 3, 4]  # Activated, Contingent, Non-conforming
        USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", 0))
        if not USSP_NETWORK_ENABLED:
            allowed_states.append(1)  # USSP Network is not enabled, so we allow the state 1 (Accepted) as well

        if flight_operation.state in allowed_states:  # Activated, Contingent, Non-conforming
            stream_rid_telemetry_data.delay(rid_telemetry_observations=json.dumps(unsigned_telemetry_observations))
        else:
            return JsonResponse(
                {
                    "message": f"The operation ID: {operation_id} is not one of Activated, Contingent or Non-conforming states in Flight Blender, telemetry submission will be ignored, please change the state first."
                },
                status=400,
                content_type="application/json",
            )
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
