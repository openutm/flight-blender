# Create your views here.
import json
import logging
from dataclasses import asdict
from os import environ as env
from typing import List

import arrow
from django.http import Http404, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from dotenv import find_dotenv, load_dotenv
from rest_framework import generics, mixins, status
from rest_framework.decorators import api_view
from shapely.geometry import shape

from auth_helper.utils import requires_scopes
from common.data_definitions import (
    ACTIVE_OPERATIONAL_STATES,
    FLIGHTBLENDER_READ_SCOPE,
    FLIGHTBLENDER_WRITE_SCOPE,
    RESPONSE_CONTENT_TYPE,
)
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from geo_fence_operations import rtree_geo_fence_helper
from geo_fence_operations.models import GeoFence
from scd_operations.dss_scd_helper import (
    OperationalIntentReferenceHelper,
    SCDOperations,
)

from .data_definitions import (
    Altitude,
    FlightDeclarationCreateResponse,
    FlightDeclarationRequest,
    HTTP400Response,
    HTTP404Response,
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

logger = logging.getLogger("django")


print("Flight Declaration Operations Views Loaded")


@method_decorator(requires_scopes(["FLIGHTBLENDER_WRITE_SCOPE"]), name="dispatch")
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


@api_view(["POST"])
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def set_flight_declaration(request):
    def validate_request(req):
        required_fields = {
            "originating_party",
            "start_datetime",
            "end_datetime",
            "flight_declaration_geo_json",
            "type_of_operation",
            "aircraft_id",
        }
        if not request.headers.get("Content-Type") == RESPONSE_CONTENT_TYPE:
            return {"message": "Unsupported Media Type"}, 415
        if not required_fields <= req.keys():
            return {
                "message": "Not all necessary fields were provided. Aircraft ID, Originating Party, Start Datetime, End Datetime, Flight Declaration and Type of operation must be provided."
            }, 400
        if "flight_declaration_geo_json" not in req:
            return {"message": "A valid flight declaration as specified by the A flight declaration protocol must be submitted."}, 400
        return None, None

    def validate_geojson(flight_declaration_geo_json):
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
            min_altitude = Altitude(meters=props["min_altitude"]["meters"], datum=props["min_altitude"]["datum"])
            max_altitude = Altitude(meters=props["max_altitude"]["meters"], datum=props["max_altitude"]["datum"])
            logging.debug(min_altitude, max_altitude)
            all_features.append(s)
        return all_features, None

    def validate_dates(start_datetime, end_datetime):
        now = arrow.now()
        s_datetime = arrow.get(start_datetime)
        e_datetime = arrow.get(end_datetime)
        two_days_from_now = now.shift(days=2)
        if s_datetime < now or e_datetime < now or e_datetime > two_days_from_now or s_datetime > two_days_from_now:
            return {"message": "A flight declaration cannot have a start / end time in the past or after two days from current time."}, 400
        return None, None

    def check_intersections(start_datetime, end_datetime, view_box):
        all_relevant_fences = []
        all_relevant_declarations = []
        is_approved = False
        declaration_state = 0 if USSP_NETWORK_ENABLED else 1

        if GeoFence.objects.filter(start_datetime__lte=start_datetime, end_datetime__gte=end_datetime).exists():
            all_fences_within_timelimits = GeoFence.objects.filter(start_datetime__lte=start_datetime, end_datetime__gte=end_datetime)
            my_rtree_helper = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(index_name="geofence_idx")
            my_rtree_helper.generate_geo_fence_index(all_fences=all_fences_within_timelimits)
            all_relevant_fences = my_rtree_helper.check_box_intersection(view_box=view_box)
            my_rtree_helper.clear_rtree_index()
            if all_relevant_fences:
                is_approved = 0
                declaration_state = 8

        if FlightDeclaration.objects.filter(
            start_datetime__lte=end_datetime, end_datetime__gte=start_datetime, state__in=ACTIVE_OPERATIONAL_STATES
        ).exists():
            all_declarations_within_timelimits = FlightDeclaration.objects.filter(
                start_datetime__lte=end_datetime, end_datetime__gte=start_datetime, state__in=ACTIVE_OPERATIONAL_STATES
            )
            my_fd_rtree_helper = FlightDeclarationRTreeIndexFactory(index_name="flight_declaration_idx")
            my_fd_rtree_helper.generate_flight_declaration_index(all_flight_declarations=all_declarations_within_timelimits)
            all_relevant_declarations = my_fd_rtree_helper.check_box_intersection(view_box=view_box)
            my_fd_rtree_helper.clear_rtree_index()
            if all_relevant_declarations:
                is_approved = 0
                declaration_state = 8

        return all_relevant_fences, all_relevant_declarations, is_approved, declaration_state

    req = request.data
    error_response, status_code = validate_request(req)
    if error_response:
        return JsonResponse(error_response, status=status_code, mimetype=RESPONSE_CONTENT_TYPE)

    flight_declaration_geo_json = req["flight_declaration_geo_json"]
    all_features, error_response = validate_geojson(flight_declaration_geo_json)
    if error_response:
        return JsonResponse(error_response, status=400, mimetype=RESPONSE_CONTENT_TYPE)

    start_datetime = req.get("start_datetime", arrow.now().isoformat())
    end_datetime = req.get("end_datetime", arrow.now().isoformat())
    error_response, status_code = validate_dates(start_datetime, end_datetime)
    if error_response:
        return JsonResponse(error_response, status=status_code, mimetype=RESPONSE_CONTENT_TYPE)

    my_database_writer = FlightBlenderDatabaseWriter()
    USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", 0))
    submitted_by = req.get("submitted_by")
    approved_by = req.get("approved_by")
    type_of_operation = req.get("type_of_operation", 0)
    originating_party = req.get("originating_party", "No Flight Information")
    aircraft_id = req["aircraft_id"]

    my_operational_intent_converter = OperationalIntentsConverter()
    parital_op_int_ref = my_operational_intent_converter.create_partial_operational_intent_ref(
        geo_json_fc=flight_declaration_geo_json,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        priority=0,
    )
    bounds = my_operational_intent_converter.get_geo_json_bounds()
    view_box = [float(i) for i in bounds.split(",")]

    all_relevant_fences, all_relevant_declarations, is_approved, declaration_state = check_intersections(start_datetime, end_datetime, view_box)

    flight_declaration = FlightDeclaration(
        operational_intent=json.dumps(asdict(parital_op_int_ref)),
        bounds=bounds,
        type_of_operation=type_of_operation,
        aircraft_id=aircraft_id,
        submitted_by=submitted_by,
        is_approved=is_approved,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        originating_party=originating_party,
        flight_declaration_raw_geojson=json.dumps(flight_declaration_geo_json),
        state=declaration_state,
    )
    flight_declaration.save()

    my_database_writer.create_flight_authorization_from_flight_declaration_obj(flight_declaration=flight_declaration)
    flight_declaration.add_state_history_entry(new_state=0, original_state=None, notes="Created Declaration")
    if declaration_state == 8:
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
        if declaration_state == 0 and USSP_NETWORK_ENABLED:
            submit_flight_declaration_to_dss_async.delay(flight_declaration_id=flight_declaration_id)

    creation_response = FlightDeclarationCreateResponse(
        id=flight_declaration_id,
        message="Submitted Flight Declaration",
        is_approved=is_approved,
        state=declaration_state,
    )
    return HttpResponse(json.dumps(asdict(creation_response)), status=200, content_type=RESPONSE_CONTENT_TYPE)


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

    def get_relevant_flight_declaration(self, start_date, end_date, view_port: List[float]):
        present = arrow.now()
        s_date = arrow.get(start_date, "YYYY-MM-DD") if start_date else present.shift(days=-1)
        e_date = arrow.get(end_date, "YYYY-MM-DD") if end_date else present.shift(days=1)

        all_fd_within_timelimits = FlightDeclaration.objects.filter(start_datetime__gte=s_date.isoformat(), end_datetime__lte=e_date.isoformat())
        logger.info("Found %s flight declarations" % len(all_fd_within_timelimits))

        if view_port:
            my_rtree_helper = FlightDeclarationRTreeIndexFactory(index_name="opint_idx")
            my_rtree_helper.generate_flight_declaration_index(all_flight_declarations=all_fd_within_timelimits)
            all_relevant_fences = my_rtree_helper.check_box_intersection(view_box=view_port)
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
            return JsonResponse({"message": "Unsupported Media Type"}, status=415, mimetype=RESPONSE_CONTENT_TYPE)

        req = request.data
        required_fields = {"originating_party", "start_datetime", "end_datetime", "flight_declaration_geo_json", "type_of_operation"}
        if not required_fields <= req.keys():
            return JsonResponse(
                {
                    "message": "Not all necessary fields were provided. Originating Party, Start Datetime, End Datetime, Flight Declaration and Type of operation must be provided."
                },
                status=400,
            )

        flight_declaration_geo_json = req.get("flight_declaration_geo_json")
        if not flight_declaration_geo_json:
            return JsonResponse(
                {"message": "A valid flight declaration as specified by the A flight declaration protocol must be submitted."}, status=400
            )

        now = arrow.now()
        start_datetime = arrow.get(req.get("start_datetime", now.isoformat())).isoformat()
        end_datetime = arrow.get(req.get("end_datetime", now.isoformat())).isoformat()
        if (
            arrow.get(start_datetime) < now
            or arrow.get(end_datetime) < now
            or arrow.get(end_datetime) > now.shift(days=2)
            or arrow.get(start_datetime) > now.shift(days=2)
        ):
            return JsonResponse(
                {"message": "A flight declaration cannot have a start / end time in the past or after two days from current time."}, status=400
            )

        all_features = []
        for feature in flight_declaration_geo_json["features"]:
            geometry = feature["geometry"]
            s = shape(geometry)
            if not s.is_valid:
                return JsonResponse(
                    {
                        "message": "Error in processing the submitted GeoJSON: every Feature in a GeoJSON FeatureCollection must have a valid geometry, please check your submitted FeatureCollection"
                    },
                    status=400,
                )
            props = feature["properties"]
            if "min_altitude" not in props or "max_altitude" not in props:
                return JsonResponse(
                    {
                        "message": "Error in processing the submitted GeoJSON every Feature in a GeoJSON FeatureCollection must have a min_altitude and max_altitude data structure"
                    },
                    status=400,
                )
            all_features.append(s)

        USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", 0))
        declaration_state = 0 if USSP_NETWORK_ENABLED else 1
        is_approved = False

        my_operational_intent_converter = OperationalIntentsConverter()
        parital_op_int_ref = my_operational_intent_converter.create_partial_operational_intent_ref(
            geo_json_fc=flight_declaration_geo_json,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            priority=0,
        )
        bounds = my_operational_intent_converter.get_geo_json_bounds()
        view_box = [float(i) for i in bounds.split(",")]

        all_relevant_fences, all_relevant_declarations = [], []
        if GeoFence.objects.filter(start_datetime__lte=start_datetime, end_datetime__gte=end_datetime).exists():
            all_fences_within_timelimits = GeoFence.objects.filter(start_datetime__lte=start_datetime, end_datetime__gte=end_datetime)
            my_rtree_helper = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(index_name="geofence_idx")
            my_rtree_helper.generate_geo_fence_index(all_fences=all_fences_within_timelimits)
            all_relevant_fences = my_rtree_helper.check_box_intersection(view_box=view_box)
            my_rtree_helper.clear_rtree_index()
            if all_relevant_fences:
                is_approved = 0
                declaration_state = 8

        if FlightDeclaration.objects.filter(start_datetime__lte=end_datetime, end_datetime__gte=start_datetime).exists():
            all_declarations_within_timelimits = FlightDeclaration.objects.filter(start_datetime__lte=end_datetime, end_datetime__gte=start_datetime)
            my_fd_rtree_helper = FlightDeclarationRTreeIndexFactory(index_name="flight_declaration_idx")
            my_fd_rtree_helper.generate_flight_declaration_index(all_flight_declarations=all_declarations_within_timelimits)
            all_relevant_declarations = my_fd_rtree_helper.check_box_intersection(view_box=view_box)
            my_fd_rtree_helper.clear_rtree_index()
            if all_relevant_declarations:
                is_approved = 0
                declaration_state = 8

        flight_declaration = FlightDeclaration(
            operational_intent=json.dumps(asdict(parital_op_int_ref)),
            bounds=bounds,
            type_of_operation=req.get("type_of_operation", 0),
            submitted_by=req.get("submitted_by"),
            is_approved=is_approved,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            originating_party=req.get("originating_party", "No Flight Information"),
            flight_declaration_raw_geojson=json.dumps(flight_declaration_geo_json),
            state=declaration_state,
        )
        flight_declaration.save()

        my_database_writer = FlightBlenderDatabaseWriter()
        my_database_writer.create_flight_authorization_from_flight_declaration_obj(flight_declaration=flight_declaration)
        flight_declaration.add_state_history_entry(new_state=0, original_state=None, notes="Created Declaration")
        if declaration_state == 8:
            flight_declaration.add_state_history_entry(
                new_state=declaration_state,
                original_state=0,
                notes="Rejected by Flight Blender because of time/space conflicts with existing operations",
            )

        flight_declaration_id = str(flight_declaration.id)
        send_operational_update_message.delay(flight_declaration_id=flight_declaration_id, message_text="Flight Declaration created..", level="info")

        if all_relevant_fences and all_relevant_declarations:
            send_operational_update_message.delay(
                flight_declaration_id=flight_declaration_id,
                message_text=f"Self deconfliction failed for operation {flight_declaration_id} did not pass self-deconfliction, there are existing operations declared in the area",
                level="error",
            )
        else:
            if declaration_state == 0 and USSP_NETWORK_ENABLED:
                submit_flight_declaration_to_dss_async.delay(flight_declaration_id=flight_declaration_id)

        creation_response = FlightDeclarationCreateResponse(
            id=flight_declaration_id, message="Submitted Flight Declaration", is_approved=is_approved, state=declaration_state
        )
        return HttpResponse(json.dumps(asdict(creation_response)), status=200, content_type=RESPONSE_CONTENT_TYPE)
