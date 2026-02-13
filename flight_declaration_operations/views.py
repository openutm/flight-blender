# Create your views here.
import json
from dataclasses import asdict
from datetime import datetime
from os import environ as env

import arrow
import geojson
from django.db.models import Q
from django.http import Http404, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from dotenv import find_dotenv, load_dotenv
from loguru import logger
from marshmallow.exceptions import ValidationError
from rest_framework import generics, mixins, status
from rest_framework.decorators import api_view
from shapely.geometry import shape

from auth_helper.utils import requires_scopes
from common.data_definitions import (
    ACTIVE_OPERATIONAL_STATES,
    FLIGHT_DECLARATION_INDEX_BASEPATH,
    FLIGHT_DECLARATION_OPINT_INDEX_BASEPATH,
    FLIGHTBLENDER_READ_SCOPE,
    FLIGHTBLENDER_WRITE_SCOPE,
    GEOFENCE_INDEX_BASEPATH,
    RESPONSE_CONTENT_TYPE,
)
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from geo_fence_operations import rtree_geo_fence_helper
from geo_fence_operations.models import GeoFence
from rid_operations import view_port_ops
from scd_operations.dss_scd_helper import (
    OperationalIntentReferenceHelper,
    SCDOperations,
    VolumesConverter,
)

from .data_definitions import (
    Altitude,
    BulkFlightDeclarationCreateResponse,
    CreateFlightDeclarationRequestSchema,
    CreateFlightDeclarationViaOperationalIntentRequestSchema,
    FlightDeclarationCreateResponse,
    HTTP400Response,
    HTTP404Response,
    IntersectionCheckResult,
)
from .flight_declarations_rtree_helper import FlightDeclarationRTreeIndexFactory
from .models import FlightDeclaration
from .pagination import StandardResultsSetPagination
from .serializers import (
    FlightDeclarationApprovalSerializer,
    FlightDeclarationSerializer,
    FlightDeclarationStateSerializer,
)
from .tasks import (
    send_operational_update_message,
    submit_flight_declaration_to_dss_async,
)
from .utils import OperationalIntentsConverter

load_dotenv(find_dotenv())


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

    def check_intersections(
        self,
        flight_declarations: list[FlightDeclaration],
        ussp_network_enabled: int,
    ) -> dict[str, IntersectionCheckResult]:
        """Check intersections for a batch of flight declarations.

        Each declaration's own ID is automatically excluded from the
        flight-declaration intersection check so it does not conflict with
        itself.

        No state updates are performed; the caller is responsible for acting
        on the results.

        Returns:
            A dict mapping declaration ID (str) to IntersectionCheckResult.
        """
        results: dict[str, IntersectionCheckResult] = {}
        approved_ids: set = set()
        batch_ids = {fd.pk for fd in flight_declarations}
        for fd in flight_declarations:
            view_box = [float(i) for i in fd.bounds.split(",")]
            start_datetime = fd.start_datetime
            end_datetime = fd.end_datetime
            all_relevant_fences = []
            all_relevant_declarations = []
            is_approved = True
            declaration_state = 0 if ussp_network_enabled else 1

            if GeoFence.objects.filter(start_datetime__lte=start_datetime, end_datetime__gte=end_datetime).exists():
                all_fences_within_timelimits = GeoFence.objects.filter(start_datetime__lte=start_datetime, end_datetime__gte=end_datetime)
                my_rtree_helper = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(index_name=GEOFENCE_INDEX_BASEPATH)
                my_rtree_helper.generate_geo_fence_index(all_fences=all_fences_within_timelimits)
                all_relevant_fences = my_rtree_helper.check_box_intersection(view_box=view_box)
                my_rtree_helper.clear_rtree_index()
                if all_relevant_fences:
                    is_approved = False
                    declaration_state = 8

            # Exclude the entire batch by default, then re-include only those
            # that were approved earlier in this batch iteration.
            declaration_queryset = FlightDeclaration.objects.filter(
                Q(state__in=ACTIVE_OPERATIONAL_STATES) | Q(pk__in=approved_ids),
                start_datetime__lte=end_datetime,
                end_datetime__gte=start_datetime,
            ).exclude(pk__in=batch_ids - approved_ids)

            if declaration_queryset.exists():
                my_fd_rtree_helper = FlightDeclarationRTreeIndexFactory(index_name=FLIGHT_DECLARATION_INDEX_BASEPATH)
                my_fd_rtree_helper.generate_flight_declaration_index(all_flight_declarations=declaration_queryset)
                all_relevant_declarations = my_fd_rtree_helper.check_flight_declaration_box_intersection(view_box=view_box)
                my_fd_rtree_helper.clear_rtree_index()
                if all_relevant_declarations:
                    is_approved = False
                    declaration_state = 8

            if is_approved:
                approved_ids.add(fd.pk)

            results[str(fd.id)] = IntersectionCheckResult(
                all_relevant_fences=all_relevant_fences,
                all_relevant_declarations=all_relevant_declarations,
                is_approved=is_approved,
                declaration_state=declaration_state,
            )
        return results


@method_decorator(requires_scopes([FLIGHTBLENDER_WRITE_SCOPE]), name="dispatch")
class FlightDeclarationDelete(generics.DestroyAPIView):
    serializer_class = FlightDeclarationApprovalSerializer

    def get_object(self):
        declaration_id = self.kwargs.get("declaration_id")
        try:
            return FlightDeclaration.objects.get(pk=declaration_id)
        except FlightDeclaration.DoesNotExist:
            raise Http404

    def delete(self, request, *args, **kwargs):
        try:
            flight_declaration = self.get_object()
            flight_declaration.delete()
            return HttpResponse(status=status.HTTP_204_NO_CONTENT)
        except Http404:
            return HttpResponse(status=status.HTTP_404_NOT_FOUND)


def _validate_and_save_flight_declaration(request_data: dict, default_state: int) -> tuple[FlightDeclaration, None] | tuple[None, dict]:
    """Validate a GeoJSON flight declaration payload and persist it with *default_state*.

    The declaration is saved immediately so that subsequent intersection checks
    (run as a separate phase) can see it and all other declarations in the batch.

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

    parital_op_int_ref = my_operational_intent_converter.create_partial_operational_intent_ref(
        geo_json_fc=flight_declaration_geo_json,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        priority=0,
    )
    bounds = my_operational_intent_converter.get_geo_json_bounds()

    flight_declaration = FlightDeclaration(
        operational_intent=json.dumps(asdict(parital_op_int_ref)),
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
    flight_declaration.add_state_history_entry(new_state=0, original_state=0, notes="Created Declaration")
    return flight_declaration, None


def _validate_and_save_operational_intent(request_data: dict, default_state: int) -> tuple[FlightDeclaration, None] | tuple[None, dict]:
    """Validate an operational-intent payload and persist it with *default_state*.

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
    flight_declaration.add_state_history_entry(new_state=0, original_state=0, notes="Created Declaration")
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
        flight_declaration.is_approved = False
        flight_declaration.state = declaration_state
        flight_declaration.save()
        flight_declaration.add_state_history_entry(
            new_state=declaration_state,
            original_state=0,
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
    else:
        if declaration_state == 0 and ussp_network_enabled:
            submit_flight_declaration_to_dss_async.delay(flight_declaration_id=flight_declaration_id)

    return FlightDeclarationCreateResponse(
        id=flight_declaration_id,
        message="Submitted Flight Declaration",
        is_approved=is_approved,
        state=declaration_state,
    )


# ---------------------------------------------------------------------------
# Single-item endpoints
# ---------------------------------------------------------------------------


@api_view(["POST"])
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def set_operational_intent(request):
    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", 0))
    default_state = 0 if ussp_network_enabled else 1

    flight_declaration, error = _validate_and_save_operational_intent(request.data, default_state)
    if error or flight_declaration is None:
        return JsonResponse(error or {"message": "Unknown error"}, status=400)

    validator = FlightDeclarationRequestValidator()
    intersection_results = validator.check_intersections([flight_declaration], ussp_network_enabled)
    creation_response = _process_intersection_result(
        flight_declaration, intersection_results[str(flight_declaration.id)], ussp_network_enabled
    )
    return HttpResponse(
        json.dumps(asdict(creation_response)),
        status=200,
        content_type=RESPONSE_CONTENT_TYPE,
    )


@api_view(["POST"])
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def set_flight_declaration(request):
    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", 0))
    default_state = 0 if ussp_network_enabled else 1

    flight_declaration, error = _validate_and_save_flight_declaration(request.data, default_state)
    if error or flight_declaration is None:
        return JsonResponse(error or {"message": "Unknown error"}, status=400)

    validator = FlightDeclarationRequestValidator()
    intersection_results = validator.check_intersections([flight_declaration], ussp_network_enabled)
    creation_response = _process_intersection_result(
        flight_declaration, intersection_results[str(flight_declaration.id)], ussp_network_enabled
    )
    return HttpResponse(
        json.dumps(asdict(creation_response)),
        status=200,
        content_type=RESPONSE_CONTENT_TYPE,
    )


# ---------------------------------------------------------------------------
# Bulk endpoints
# ---------------------------------------------------------------------------


@api_view(["POST"])
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def set_flight_declarations_bulk(request):
    """Accept multiple flight declarations in a single request.

    Processing happens in two phases:

    1. **Validate & save** – every declaration is validated and persisted so
       that it is visible in the database alongside the other items in the
       same batch.
    2. **Intersection checks & notifications** – once all declarations have
       been saved, intersection checks run for each one.  Because the entire
       batch is already in the database, declarations within the same request
       can detect conflicts with each other.
    """
    flight_declarations_list = request.data
    if not isinstance(flight_declarations_list, list):
        return JsonResponse(
            {"message": "Request body must be a JSON array of flight declaration objects."},
            status=400,
            content_type=RESPONSE_CONTENT_TYPE,
        )

    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", 0))
    default_state = 0 if ussp_network_enabled else 1

    # Phase 1: validate and save all declarations
    saved: dict[int, FlightDeclaration] = {}
    results: list[dict] = []
    failed_count = 0

    for idx, item in enumerate(flight_declarations_list):
        try:
            flight_declaration, error = _validate_and_save_flight_declaration(item, default_state)
            if error or flight_declaration is None:
                failed_count += 1
                error = error or {"message": "Unknown error"}
                results.append({"index": idx, "success": False, "message": error.get("message", "Validation error"), "errors": error.get("errors")})
            else:
                saved[idx] = flight_declaration
        except Exception as e:
            logger.error(f"Error validating flight declaration at index {idx}: {e}")
            failed_count += 1
            results.append({"index": idx, "success": False, "message": str(e)})

    # Phase 2: check all intersections at once, then process results
    validator = FlightDeclarationRequestValidator()
    intersection_results = validator.check_intersections(list(saved.values()), ussp_network_enabled)

    submitted_count = 0
    for idx, flight_declaration in saved.items():
        try:
            fd_id = str(flight_declaration.id)
            creation_response = _process_intersection_result(
                flight_declaration, intersection_results[fd_id], ussp_network_enabled
            )
            submitted_count += 1
            results.append(
                {
                    "index": idx,
                    "success": True,
                    "id": creation_response.id,
                    "is_approved": creation_response.is_approved,
                    "state": creation_response.state,
                }
            )
        except Exception as e:
            logger.error(f"Error during intersection check for flight declaration at index {idx}: {e}")
            failed_count += 1
            results.append({"index": idx, "success": False, "message": str(e)})

    results.sort(key=lambda r: r["index"])

    bulk_response = BulkFlightDeclarationCreateResponse(
        submitted=submitted_count,
        failed=failed_count,
        results=results,
    )
    http_status = 200 if failed_count == 0 else 207
    return HttpResponse(
        json.dumps(asdict(bulk_response)),
        status=http_status,
        content_type=RESPONSE_CONTENT_TYPE,
    )


@api_view(["POST"])
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def set_operational_intents_bulk(request):
    """Accept multiple operational intents in a single request.

    Processing happens in two phases:

    1. **Validate & save** – every operational intent is validated and persisted.
    2. **Intersection checks & notifications** – once all declarations have
       been saved, intersection checks run for each one so that declarations
       within the same batch can detect conflicts with each other.
    """
    operational_intents_list = request.data
    if not isinstance(operational_intents_list, list):
        return JsonResponse(
            {"message": "Request body must be a JSON array of operational intent objects."},
            status=400,
            content_type=RESPONSE_CONTENT_TYPE,
        )

    ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", 0))
    default_state = 0 if ussp_network_enabled else 1

    # Phase 1: validate and save all operational intents
    saved: dict[int, FlightDeclaration] = {}
    results: list[dict] = []
    failed_count = 0

    for idx, item in enumerate(operational_intents_list):
        try:
            flight_declaration, error = _validate_and_save_operational_intent(item, default_state)
            if error or flight_declaration is None:
                failed_count += 1
                error = error or {"message": "Unknown error"}
                results.append({"index": idx, "success": False, "message": error.get("message", "Validation error"), "errors": error.get("errors")})
            else:
                saved[idx] = flight_declaration
        except Exception as e:
            logger.error(f"Error validating operational intent at index {idx}: {e}")
            failed_count += 1
            results.append({"index": idx, "success": False, "message": str(e)})

    # Phase 2: check all intersections at once, then process results
    validator = FlightDeclarationRequestValidator()
    intersection_results = validator.check_intersections(list(saved.values()), ussp_network_enabled)

    submitted_count = 0
    for idx, flight_declaration in saved.items():
        try:
            fd_id = str(flight_declaration.id)
            creation_response = _process_intersection_result(
                flight_declaration, intersection_results[fd_id], ussp_network_enabled
            )
            submitted_count += 1
            results.append(
                {
                    "index": idx,
                    "success": True,
                    "id": creation_response.id,
                    "is_approved": creation_response.is_approved,
                    "state": creation_response.state,
                }
            )
        except Exception as e:
            logger.error(f"Error during intersection check for operational intent at index {idx}: {e}")
            failed_count += 1
            results.append({"index": idx, "success": False, "message": str(e)})

    results.sort(key=lambda r: r["index"])

    bulk_response = BulkFlightDeclarationCreateResponse(
        submitted=submitted_count,
        failed=failed_count,
        results=results,
    )
    http_status = 200 if failed_count == 0 else 207
    return HttpResponse(
        json.dumps(asdict(bulk_response)),
        status=http_status,
        content_type=RESPONSE_CONTENT_TYPE,
    )


@method_decorator(requires_scopes([FLIGHTBLENDER_WRITE_SCOPE]), name="dispatch")
class FlightDeclarationApproval(mixins.UpdateModelMixin, generics.GenericAPIView):
    queryset = FlightDeclaration.objects.all()
    serializer_class = FlightDeclarationApprovalSerializer

    def put(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)


@method_decorator(requires_scopes([FLIGHTBLENDER_WRITE_SCOPE]), name="dispatch")
class FlightDeclarationStateUpdate(mixins.UpdateModelMixin, generics.GenericAPIView):
    queryset = FlightDeclaration.objects.all()
    serializer_class = FlightDeclarationStateSerializer

    def put(self, request, *args, **kwargs):
        return self.update(request, *args, **kwargs)


@method_decorator(requires_scopes([FLIGHTBLENDER_READ_SCOPE]), name="dispatch")
class FlightDeclarationDetail(mixins.RetrieveModelMixin, generics.GenericAPIView):
    queryset = FlightDeclaration.objects.all()
    serializer_class = FlightDeclarationSerializer

    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def network_flight_declaration_details_by_view(request):
    USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", 0))

    try:
        view = request.query_params["view"]
        view_port = [float(i) for i in view.split(",")]
    except (KeyError, ValueError):
        incorrect_parameters = {"message": "A view bbox is necessary with four values: lat1, lng1, lat2, lng2"}
        return JsonResponse(incorrect_parameters, status=400, content_type="application/json")

    if not view_port_ops.check_view_port(view_port_coords=view_port):
        view_port_error = {"message": "An incorrect view port bbox was provided"}
        return JsonResponse(view_port_error, status=400, content_type="application/json")

    if not USSP_NETWORK_ENABLED:
        return JsonResponse(
            asdict(HTTP400Response(message="USSP network cannot be queried since it is not enabled in Flight Blender")),
            status=400,
            content_type="application/json",
        )
    start_datetime = arrow.now().shift(minutes=-1).isoformat()
    end_datetime = arrow.now().shift(minutes=10).isoformat()
    view_port_box = view_port_ops.build_view_port_box_lng_lat(view_port_coords=view_port)
    # Convert view_port_box to GeoJSON FeatureCollection
    converted_geo_json = view_port_ops.convert_box_to_geojson_feature(box=view_port_box)

    my_operational_intent_converter = OperationalIntentsConverter()
    temporary_operational_intent_reference = my_operational_intent_converter.create_partial_operational_intent_ref(
        geo_json_fc=converted_geo_json,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        priority=0,
    )
    temporary_operational_intent_reference_volumes = temporary_operational_intent_reference.volumes
    my_operational_intent_converter.convert_operational_intent_to_geo_json(volumes=temporary_operational_intent_reference_volumes)
    logger.debug("Temporary Operational Intent Reference GeoJSON:")
    logger.debug(json.dumps(my_operational_intent_converter.geo_json))
    my_scd_helper = SCDOperations()
    try:
        operational_intent_geojson = my_scd_helper.get_and_process_nearby_operational_intents(volumes=temporary_operational_intent_reference_volumes)
    except (ValueError, ConnectionError):
        logger.info("The received data from peer USS had errors and failed validation checks..")
        operational_intent_geojson = []

    return JsonResponse(operational_intent_geojson, status=200, content_type="application/json")


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def network_flight_declaration_details(request, flight_declaration_id):
    my_database_reader = FlightBlenderDatabaseReader()
    USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", 0))

    if not USSP_NETWORK_ENABLED:
        return JsonResponse(
            asdict(HTTP400Response(message="USSP network cannot be queried since it is not enabled in Flight Blender")),
            status=400,
            content_type="application/json",
        )

    if not my_database_reader.check_flight_declaration_exists(flight_declaration_id=flight_declaration_id):
        return JsonResponse(
            asdict(HTTP404Response(message=f"Flight Declaration with ID {flight_declaration_id} not found")),
            status=404,
            content_type="application/json",
        )

    flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=flight_declaration_id)

    if flight_declaration.state not in [0, 1, 2, 3, 4]:
        return JsonResponse(
            asdict(HTTP400Response(message="USSP network can only be queried for operational intents that are active")),
            status=400,
            content_type="application/json",
        )

    operational_intent_volumes_raw = json.loads(flight_declaration.operational_intent)
    operational_intent_volumes = operational_intent_volumes_raw["volumes"]

    my_operational_intent_parser = OperationalIntentReferenceHelper()
    all_volumes = [my_operational_intent_parser.parse_volume_to_volume4D(volume=volume) for volume in operational_intent_volumes]

    my_scd_helper = SCDOperations()
    try:
        operational_intent_geojson = my_scd_helper.get_and_process_nearby_operational_intents(volumes=all_volumes)
    except (ValueError, ConnectionError):
        logger.info("The received data from peer USS had errors and failed validation checks..")
        operational_intent_geojson = []

    return JsonResponse(operational_intent_geojson, status=200, content_type="application/json")


@method_decorator(requires_scopes([FLIGHTBLENDER_READ_SCOPE]), name="dispatch")
class FlightDeclarationCreateList(mixins.ListModelMixin, generics.GenericAPIView):
    """
    FlightDeclarationCreateList is a view that handles the creation and listing of flight declarations.
    This class-based view supports GET and POST requests to manage flight declarations within the UTMAdapter project.
    It provides functionality to filter flight declarations based on date and viewport, and to create new flight declarations
    with validation and conflict checking.
    Attributes:
        queryset (QuerySet): The queryset of FlightDeclaration objects.
        serializer_class (Serializer): The serializer class for FlightDeclaration.
        pagination_class (Pagination): The pagination class for the results.
    Methods:
        get_relevant_flight_declaration(start_date, end_date, view_port):
            Filters flight declarations based on the provided date range and viewport.
        get_queryset():
            Retrieves the queryset of flight declarations based on query parameters.
        get(request, *args, **kwargs):
            Handles GET requests to list flight declarations.
        post(request, *args, **kwargs):
            Handles POST requests to create a new flight declaration with validation and conflict checking.
    This class is part of the UTMAdapter project: https://github.com/Dronecode/utm-adapter
    """

    queryset = FlightDeclaration.objects.all()
    serializer_class = FlightDeclarationSerializer
    pagination_class = StandardResultsSetPagination

    def get_relevant_flight_declaration(self, start_date, end_date, view_port: list[float]):
        present = arrow.now()
        s_date = arrow.get(start_date, "YYYY-MM-DD") if start_date else present.shift(days=-1)
        e_date = arrow.get(end_date, "YYYY-MM-DD") if end_date else present.shift(days=1)

        all_fd_within_timelimits = FlightDeclaration.objects.filter(start_datetime__gte=s_date.isoformat(), end_datetime__lte=e_date.isoformat())
        logger.info("Found %s flight declaration" % len(all_fd_within_timelimits))

        if view_port:
            my_rtree_helper = FlightDeclarationRTreeIndexFactory(index_name=FLIGHT_DECLARATION_OPINT_INDEX_BASEPATH)
            my_rtree_helper.generate_flight_declaration_index(all_flight_declarations=all_fd_within_timelimits)
            all_relevant_fences = my_rtree_helper.check_flight_declaration_box_intersection(view_box=view_port)
            relevant_id_set = [i["flight_declaration_id"] for i in all_relevant_fences]
            my_rtree_helper.clear_rtree_index()
            return FlightDeclaration.objects.filter(id__in=relevant_id_set)
        else:
            return all_fd_within_timelimits

    def get_queryset(self):
        start_date = self.request.query_params.get("start_date", None)
        end_date = self.request.query_params.get("end_date", None)
        view = self.request.query_params.get("view", None)
        view_port = [float(i) for i in view.split(",")] if view else []

        return self.get_relevant_flight_declaration(view_port=view_port, start_date=start_date, end_date=end_date)

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        if request.headers.get("Content-Type") != RESPONSE_CONTENT_TYPE:
            return JsonResponse(
                {"message": "Unsupported Media Type"},
                status=415,
                content_type=RESPONSE_CONTENT_TYPE,
            )

        ussp_network_enabled = int(env.get("USSP_NETWORK_ENABLED", 0))
        default_state = 0 if ussp_network_enabled else 1

        flight_declaration, error = _validate_and_save_flight_declaration(request.data, default_state)
        if error or flight_declaration is None:
            return JsonResponse(error or {"message": "Unknown error"}, status=400)

        my_database_writer = FlightBlenderDatabaseWriter()
        my_database_writer.create_flight_operational_intent_reference_from_flight_declaration_obj(flight_declaration=flight_declaration)

        validator = FlightDeclarationRequestValidator()
        intersection_results = validator.check_intersections([flight_declaration], ussp_network_enabled)
        creation_response = _process_intersection_result(
            flight_declaration, intersection_results[str(flight_declaration.id)], ussp_network_enabled
        )
        return HttpResponse(
            json.dumps(asdict(creation_response)),
            status=200,
            content_type=RESPONSE_CONTENT_TYPE,
        )
