# Create your views here.
import json
from dataclasses import asdict
from os import environ as env

import arrow
from django.db import transaction
from django.http import Http404, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from dotenv import find_dotenv, load_dotenv
from loguru import logger
from marshmallow.exceptions import ValidationError
from shapely.geometry import shape

from flight_blender.auth.utils import requires_scopes
from flight_blender.common.data_definitions import (
    FLIGHT_DECLARATION_OPINT_INDEX_BASEPATH,
    FLIGHTBLENDER_READ_SCOPE,
    FLIGHTBLENDER_WRITE_SCOPE,
    RESPONSE_CONTENT_TYPE,
)
from flight_blender.common.database_operations import FlightBlenderDatabaseReader, FlightBlenderDatabaseWriter
from flight_blender.plugins.loader import load_plugin
from flight_blender.rid import view_port_ops
from flight_blender.scd.dss_scd_helper import OperationalIntentReferenceHelper, SCDOperations
from flight_blender.settings import FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE

from .data_definitions import (
    Altitude,
    BulkFlightDeclarationCreateResponse,
    CreateFlightDeclarationRequestSchema,
    CreateFlightDeclarationViaOperationalIntentRequestSchema,
    DeconflictionRequest,
    FlightDeclarationCreateResponse,
    HTTP400Response,
    HTTP404Response,
    IntersectionCheckResult,
)
from .deconfliction_protocol import DeconflictionEngine
from .flight_declarations_rtree_helper import FlightDeclarationRTreeIndexFactory
from .models import FlightDeclaration, FlightOperationalIntentReference
from .tasks import send_operational_update_message, submit_flight_declaration_to_dss_async
from .utils import OperationalIntentsConverter

load_dotenv(find_dotenv())

# ── Plugin-loaded de-confliction engine ──────────────────────────────────
# The class is loaded once at module import time (cached by load_plugin).
# An instance is created per call since engines may hold request-specific state.
_DeconflictionEngineClass = load_plugin(
    FLIGHT_BLENDER_PLUGIN_DECONFLICTION_ENGINE,
    expected_protocol=DeconflictionEngine,
)


class FlightDeclarationRequestValidator:
    def validate_incoming_operational_intent(self, operational_intent_details_geojson):
        schema = CreateFlightDeclarationViaOperationalIntentRequestSchema()
        try:
            schema.load(operational_intent_details_geojson)
        except ValidationError as err:
            return {"message": "Validation error", "errors": err.messages}, 400
        return None, None

    def validate_request(self, request_data):
        schema = CreateFlightDeclarationRequestSchema()
        try:
            schema.load(request_data)
        except ValidationError as err:
            return {"message": "Validation error", "errors": err.messages}, 400
        return None, None

    def validate_geojson(self, flight_declaration_geo_json):
        all_features = []
        for feature in flight_declaration_geo_json["features"]:
            geometry = feature["geometry"]
            s = shape(geometry)
            if not s.is_valid:
                return {
                    "message": "Error in processing the submitted GeoJSON: every Feature in a GeoJSON FeatureCollection must have a valid geometry, please check your submitted FeatureCollection"
                }, 400
            props = feature["properties"]
            if "min_altitude" not in props or "max_altitude" not in props:
                return {
                    "message": "Error in processing the submitted GeoJSON every Feature in a GeoJSON FeatureCollection must have a min_altitude and max_altitude data structure"
                }, 400
            try:
                min_altitude = Altitude(
                    meters=props["min_altitude"]["meters"],
                    datum=props["min_altitude"]["datum"],
                )
                max_altitude = Altitude(
                    meters=props["max_altitude"]["meters"],
                    datum=props["max_altitude"]["datum"],
                )
            except TypeError:
                return {
                    "message": "Error in processing the submitted GeoJSON: every Feature in a GeoJSON FeatureCollection must have a min_altitude and max_altitude data structure"
                }, 400
            logger.debug(f"Min altitude: {min_altitude}, Max altitude: {max_altitude}")
            all_features.append(s)
        return all_features, None

    def validate_dates(self, start_datetime: str, end_datetime: str) -> tuple[dict, int] | tuple[None, None]:
        """
        Validates the start and end dates for the flight declaration.

        Args:
            start_datetime (str): The start datetime of the flight declaration in ISO format.
            end_datetime (str): The end datetime of the flight declaration in ISO format.

        Returns:
            Tuple[dict, int]: A tuple containing an error message and status code if validation fails, otherwise (None, None).
        """
        now = arrow.now()
        s_datetime = arrow.get(start_datetime)
        e_datetime = arrow.get(end_datetime)
        two_days_from_now = now.shift(days=2)
        if s_datetime < now or e_datetime < now or e_datetime > two_days_from_now or s_datetime > two_days_from_now:
            return {"message": "A flight declaration cannot have a start / end time in the past or after two days from current time."}, 400
        return None, None


def _validate_and_save_flight_declaration(request_data: dict, default_state: int) -> tuple[FlightDeclaration, None] | tuple[None, dict]:
    """Validate a GeoJSON flight declaration payload and persist it with *default_state*.

    The declaration is saved immediately with ``is_approved=True`` and
    ``state=default_state`` so that subsequent intersection checks (run as a
    separate phase) can see it and all other declarations in the batch.

    .. note::

       There is a deliberate window between the initial save and the
       intersection-check phase during which a declaration appears as
       *approved* in the database.  If the intersection check later rejects
       it, ``_process_intersection_result`` updates the row to
       ``is_approved=False, state=8``.  For bulk requests this window is
       bounded by a ``transaction.atomic()`` block in the calling endpoint,
       so external queries will not observe the intermediate state.

    Returns:
        (FlightDeclaration, None) on success.
        (None, error_dict)      on validation failure.
    """
    my_flight_declaration_validator = FlightDeclarationRequestValidator()
    my_operational_intent_converter = OperationalIntentsConverter()

    error_response, status_code = my_flight_declaration_validator.validate_request(request_data=request_data)
    if error_response:
        return None, error_response

    flight_declaration_geo_json = request_data.get("flight_declaration_geo_json")
    if not flight_declaration_geo_json:
        return None, {"message": "Flight declaration GeoJSON is required."}

    validated_features_or_error, error_val = my_flight_declaration_validator.validate_geojson(flight_declaration_geo_json)
    if error_val:
        return None, validated_features_or_error

    start_datetime = request_data.get("start_datetime", arrow.now().isoformat())
    end_datetime = request_data.get("end_datetime", arrow.now().isoformat())
    error_response, status_code = my_flight_declaration_validator.validate_dates(start_datetime, end_datetime)
    if error_response:
        return None, error_response

    submitted_by = request_data.get("submitted_by")
    type_of_operation = request_data.get("type_of_operation", 0)
    originating_party = request_data.get("originating_party", "No Flight Information")
    aircraft_id = request_data["aircraft_id"]

    partial_op_int_ref = my_operational_intent_converter.create_partial_operational_intent_ref(
        geo_json_fc=flight_declaration_geo_json,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        priority=0,
    )
    bounds = my_operational_intent_converter.get_geo_json_bounds()

    flight_declaration = FlightDeclaration(
        operational_intent=json.dumps(asdict(partial_op_int_ref)),
        bounds=bounds,
        type_of_operation=type_of_operation,
        aircraft_id=aircraft_id,
        submitted_by=submitted_by,
        is_approved=True,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        originating_party=originating_party,
        flight_declaration_raw_geojson=json.dumps(flight_declaration_geo_json),
        state=default_state,
    )
    flight_declaration.save()
    flight_declaration.add_state_history_entry(new_state=default_state, original_state=0, notes="Created Declaration")
    return flight_declaration, None


def _validate_and_save_operational_intent(request_data: dict, default_state: int) -> tuple[FlightDeclaration, None] | tuple[None, dict]:
    """Validate an operational-intent payload and persist it with *default_state*.

    See :func:`_validate_and_save_flight_declaration` for important notes on
    the ``is_approved=True`` initial state and transactional guarantees.

    Returns:
        (FlightDeclaration, None) on success.
        (None, error_dict)      on validation failure.
    """
    my_flight_declaration_validator = FlightDeclarationRequestValidator()
    my_operational_intent_converter = OperationalIntentsConverter()

    error_response, status_code = my_flight_declaration_validator.validate_incoming_operational_intent(
        operational_intent_details_geojson=request_data
    )
    if error_response:
        return None, error_response

    operational_intent_volume4ds = request_data.get("operational_intent_volume4ds")
    parsed_operational_intent = my_operational_intent_converter.parse_volume4ds_to_V4D_list(operational_intent_volume4ds)
    _seralized_operational_intent = [asdict(v4d) for v4d in parsed_operational_intent]
    my_operational_intent_converter.convert_operational_intent_to_geo_json(volumes=parsed_operational_intent)
    flight_declaration_geo_json = my_operational_intent_converter.geo_json

    start_datetime = request_data.get("start_datetime", arrow.now().isoformat())
    end_datetime = request_data.get("end_datetime", arrow.now().isoformat())
    error_response, status_code = my_flight_declaration_validator.validate_dates(start_datetime, end_datetime)
    if error_response:
        return None, error_response

    submitted_by = request_data.get("submitted_by")
    type_of_operation = request_data.get("type_of_operation", 0)
    originating_party = request_data.get("originating_party", "No Flight Information")
    aircraft_id = request_data["aircraft_id"]
    bounds = my_operational_intent_converter.get_geo_json_bounds()

    flight_declaration = FlightDeclaration(
        operational_intent=json.dumps(_seralized_operational_intent),
        bounds=bounds,
        type_of_operation=type_of_operation,
        aircraft_id=aircraft_id,
        submitted_by=submitted_by,
        is_approved=True,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        originating_party=originating_party,
        flight_declaration_raw_geojson=json.dumps(flight_declaration_geo_json),
        state=default_state,
    )
    flight_declaration.save()
    flight_declaration.add_state_history_entry(new_state=default_state, original_state=0, notes="Created Declaration")
    return flight_declaration, None


def _process_intersection_result(
    flight_declaration: FlightDeclaration,
    intersection_result: IntersectionCheckResult,
    ussp_network_enabled: int,
) -> FlightDeclarationCreateResponse:
    """Update a declaration's state and send notifications based on intersection check results.

    No intersection checks are performed here; the caller must supply the
    pre-computed ``IntersectionCheckResult``.
    """
    all_relevant_fences = intersection_result.all_relevant_fences
    all_relevant_declarations = intersection_result.all_relevant_declarations
    is_approved = intersection_result.is_approved
    declaration_state = intersection_result.declaration_state

    # Update state if the intersection check rejected the declaration
    if not is_approved:
        # Capture the original state before updating, so history reflects the true transition
        original_state = flight_declaration.state
        flight_declaration.is_approved = False
        flight_declaration.state = declaration_state
        flight_declaration.save()
        flight_declaration.add_state_history_entry(
            new_state=declaration_state,
            original_state=original_state,
            notes="Rejected by Flight Blender because of time/space conflicts with existing operations",
        )

    flight_declaration_id = str(flight_declaration.id)

    send_operational_update_message.delay(
        flight_declaration_id=flight_declaration_id,
        message_text="Flight Declaration created..",
        level="info",
    )

    if all_relevant_fences and all_relevant_declarations:
        self_deconfliction_failed_msg = f"Self deconfliction failed for operation {flight_declaration_id} did not pass self-deconfliction, there are existing operations declared in the area"
        send_operational_update_message.delay(
            flight_declaration_id=flight_declaration_id,
            message_text=self_deconfliction_failed_msg,
            level="error",
        )

    # Only submit to DSS when the declaration was approved, USSP is enabled,
    # and auto-submit is not disabled.  Setting AUTO_SUBMIT_TO_DSS=0 keeps the
    # declaration in state=0 (ProcessingNotSubmittedToDss) so operators can
    # create a set of candidate declarations and pick one to submit manually
    # via the dedicated submit_to_dss endpoint.
    auto_submit_to_dss = int(env.get("AUTO_SUBMIT_TO_DSS", 1))
    if is_approved and declaration_state == 0 and ussp_network_enabled and auto_submit_to_dss:
        submit_flight_declaration_to_dss_async.delay(flight_declaration_id=flight_declaration_id)

    return FlightDeclarationCreateResponse(
        id=flight_declaration_id,
        message="Submitted Flight Declaration",
        is_approved=is_approved,
        state=declaration_state,
    )


def _run_deconfliction(
    flight_declarations: list[FlightDeclaration],
    ussp_network_enabled: int,
) -> dict[str, IntersectionCheckResult]:
    """Run the plugin-loaded de-confliction engine for a batch of declarations.

    Each declaration is evaluated individually via the engine's
    ``check_deconfliction`` method. The engine class is loaded once at module
    import time and a fresh instance is created per call.

    Returns:
        A dict mapping declaration ID (str) to ``IntersectionCheckResult``
        (which is an alias for ``DeconflictionResult``).
    """
    if not flight_declarations:
        return {}

    results: dict[str, IntersectionCheckResult] = {}
    for fd in flight_declarations:
        view_box = [float(i) for i in fd.bounds.split(",")]
        raw_geojson = fd.flight_declaration_raw_geojson
        flight_declaration_geo_json = json.loads(raw_geojson) if raw_geojson else None

        request = DeconflictionRequest(
            start_datetime=fd.start_datetime,
            end_datetime=fd.end_datetime,
            view_box=view_box,
            ussp_network_enabled=ussp_network_enabled,
            declaration_id=str(fd.id),
            flight_declaration_geo_json=flight_declaration_geo_json,
            type_of_operation=fd.type_of_operation,
            priority=0,
        )
        engine = _DeconflictionEngineClass()
        result = engine.check_deconfliction(request)
        results[str(fd.id)] = result

    return results
