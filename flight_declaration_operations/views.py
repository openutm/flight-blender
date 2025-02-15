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
    ArgonServerDatabaseReader,
    ArgonServerDatabaseWriter,
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
    try:
        assert request.headers["Content-Type"] == RESPONSE_CONTENT_TYPE
    except AssertionError:
        msg = {"message": "Unsupported Media Type"}
        return JsonResponse(msg, status=415, mimetype=RESPONSE_CONTENT_TYPE)
    else:
        req = request.data

    try:
        assert req.keys() >= {
            "originating_party",
            "start_datetime",
            "end_datetime",
            "flight_declaration_geo_json",
            "type_of_operation",
            "aircraft_id",
        }

    except AssertionError:
        msg = json.dumps(
            {
                "message": "Not all necessary fields were provided. Aircraft ID, Originating Party, Start Datetime, End Datetime, Flight Declaration and Type of operation must be provided."
            }
        )
        return HttpResponse(msg, status=400)

    try:
        flight_declaration_geo_json = req["flight_declaration_geo_json"]
    except KeyError:
        msg = json.dumps({"message": "A valid flight declaration as specified by the A flight declaration protocol must be submitted."})
        return HttpResponse(msg, status=400)

    my_database_writer = ArgonServerDatabaseWriter()
    USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", 0))

    submitted_by = None if "submitted_by" not in req else req["submitted_by"]
    approved_by = None if "approved_by" not in req else req["approved_by"]
    is_approved = False
    type_of_operation = 0 if "type_of_operation" not in req else req["type_of_operation"]
    originating_party = "No Flight Information" if "originating_party" not in req else req["originating_party"]
    aircraft_id = req["aircraft_id"]
    now = arrow.now()

    start_datetime = now.isoformat() if "start_datetime" not in req else arrow.get(req["start_datetime"]).isoformat()
    end_datetime = now.isoformat() if "end_datetime" not in req else arrow.get(req["end_datetime"]).isoformat()

    two_days_from_now = now.shift(days=2)

    # verify start and end date time
    s_datetime = arrow.get(start_datetime)
    e_datetime = arrow.get(end_datetime)

    if s_datetime < now or e_datetime < now or e_datetime > two_days_from_now or s_datetime > two_days_from_now:
        msg = json.dumps({"message": "A flight declaration cannot have a start / end time in the past or after two days from current time."})
        return HttpResponse(msg, status=400)
    all_features = []

    for feature in flight_declaration_geo_json["features"]:
        geometry = feature["geometry"]
        s = shape(geometry)
        if s.is_valid:
            all_features.append(s)
        else:
            op = json.dumps(
                {
                    "message": "Error in processing the submitted GeoJSON: every Feature in a GeoJSON FeatureCollection must have a valid geometry, please check your submitted FeatureCollection"
                }
            )
            return HttpResponse(op, status=400, content_type=RESPONSE_CONTENT_TYPE)

        props = feature["properties"]
        try:
            assert "min_altitude" in props
            assert "max_altitude" in props
        except AssertionError:
            op = json.dumps(
                {
                    "message": "Error in processing the submitted GeoJSON every Feature in a GeoJSON FeatureCollection must have a min_altitude and max_altitude data structure"
                }
            )
            return HttpResponse(op, status=400, content_type=RESPONSE_CONTENT_TYPE)
        else:
            min_altitude = Altitude(meters=props["min_altitude"]["meters"], datum=props["min_altitude"]["datum"])
            max_altitude = Altitude(meters=props["max_altitude"]["meters"], datum=props["max_altitude"]["datum"])
            logging.debug(min_altitude, max_altitude)

    # Default state is Processing if working with a DSS, otherwise it is Accepted
    declaration_state = 0 if USSP_NETWORK_ENABLED else 1

    flight_declaration = FlightDeclarationRequest(
        features=all_features,
        type_of_operation=type_of_operation,
        submitted_by=submitted_by,
        approved_by=approved_by,
        is_approved=is_approved,
        state=declaration_state,
    )

    my_operational_intent_converter = OperationalIntentsConverter()

    parital_op_int_ref = my_operational_intent_converter.create_partial_operational_intent_ref(
        geo_json_fc=flight_declaration_geo_json,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        priority=0,
    )

    bounds = my_operational_intent_converter.get_geo_json_bounds()

    logger.info("Checking intersections with Geofences..")
    view_box = [float(i) for i in bounds.split(",")]

    fence_within_timelimits = GeoFence.objects.filter(start_datetime__lte=start_datetime, end_datetime__gte=end_datetime).exists()
    all_relevant_fences = []
    if fence_within_timelimits:
        all_fences_within_timelimits = GeoFence.objects.filter(start_datetime__lte=start_datetime, end_datetime__gte=end_datetime)
        INDEX_NAME = "geofence_idx"
        my_rtree_helper = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(index_name=INDEX_NAME)
        my_rtree_helper.generate_geo_fence_index(all_fences=all_fences_within_timelimits)
        all_relevant_fences = my_rtree_helper.check_box_intersection(view_box=view_box)
        relevant_id_set = []
        for i in all_relevant_fences:
            relevant_id_set.append(i["geo_fence_id"])

        my_rtree_helper.clear_rtree_index()
        logger.info("Geofence intersections checked, found {num_intersections} fences".format(num_intersections=len(relevant_id_set)))
        if all_relevant_fences:
            is_approved = 0
            declaration_state = 8

    all_relevant_declarations = []
    existing_declaration_within_timelimits = FlightDeclaration.objects.filter(
        start_datetime__lte=end_datetime, end_datetime__gte=start_datetime, state__in=ACTIVE_OPERATIONAL_STATES
    ).exists()
    if existing_declaration_within_timelimits:
        all_declarations_within_timelimits = FlightDeclaration.objects.filter(
            start_datetime__lte=end_datetime, end_datetime__gte=start_datetime, state__in=ACTIVE_OPERATIONAL_STATES
        )
        INDEX_NAME = "flight_declaration_idx"
        my_fd_rtree_helper = FlightDeclarationRTreeIndexFactory(index_name=INDEX_NAME)
        my_fd_rtree_helper.generate_flight_declaration_index(all_flight_declarations=all_declarations_within_timelimits)
        all_relevant_declarations = my_fd_rtree_helper.check_box_intersection(view_box=view_box)
        relevant_id_set = []
        for i in all_relevant_declarations:
            relevant_id_set.append(i["flight_declaration_id"])
        my_fd_rtree_helper.clear_rtree_index()
        logger.info(
            "Flight Declaration intersections checked, found {all_relevant_declarations} declarations".format(
                all_relevant_declarations=len(relevant_id_set)
            )
        )
        if all_relevant_declarations:
            logger.info("Setting state as rejected...")
            is_approved = 0
            declaration_state = 8

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
            notes="Rejected by Argon Server because of  time / space conflicts with existing operations",
        )

    flight_declaration_id = str(flight_declaration.id)

    send_operational_update_message.delay(
        flight_declaration_id=flight_declaration_id,
        message_text="Flight Declaration created..",
        level="info",
    )

    if all_relevant_fences and all_relevant_declarations:
        # Async submic flight declaration to DSS
        logger.info("Self deconfliction failed, this declaration cannot be sent to the DSS system..")

        self_deconfliction_failed_msg = "Self deconfliction failed for operation {operation_id} did not pass self-deconfliction, there are existing operations declared in the area".format(
            operation_id=flight_declaration_id
        )
        send_operational_update_message.delay(
            flight_declaration_id=flight_declaration_id,
            message_text=self_deconfliction_failed_msg,
            level="error",
        )

    else:
        logger.info("Self deconfliction success, this declaration will be sent to the DSS system, if a DSS URL is provided..")
        # Only send it to the USSP network if the declaration is accepted and the network is available.

        if declaration_state == 0 and USSP_NETWORK_ENABLED:
            submit_flight_declaration_to_dss_async.delay(flight_declaration_id=flight_declaration_id)
    creation_response = FlightDeclarationCreateResponse(
        id=flight_declaration_id,
        message="Submitted Flight Declaration",
        is_approved=is_approved,
        state=declaration_state,
    )

    op = json.dumps(asdict(creation_response))
    return HttpResponse(op, status=200, content_type=RESPONSE_CONTENT_TYPE)


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
    my_database_reader = ArgonServerDatabaseReader()
    USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", 0))
    # Check if the flight declaration exists
    if not USSP_NETWORK_ENABLED:
        network_not_enabled = HTTP400Response(message="USSP network can not be queried since it is not enabled in Argon Server")
        op = json.dumps(asdict(network_not_enabled))
        return HttpResponse(op, status=400, content_type="application/json")

    my_operational_intent_parser = OperationalIntentReferenceHelper()
    my_scd_helper = SCDOperations()

    flight_declaration_exists = my_database_reader.check_flight_declaration_exists(flight_declaration_id=flight_declaration_id)
    if not flight_declaration_exists:
        not_found_response = HTTP404Response(
            message="Flight Declaration with ID {flight_declaration_id} not found".format(flight_declaration_id=flight_declaration_id)
        )
        op = json.dumps(asdict(not_found_response))
        return HttpResponse(op, status=404, content_type="application/json")

    flight_declaration = my_database_reader.get_flight_declaration_by_id(flight_declaration_id=flight_declaration_id)

    current_state = flight_declaration.state
    # Check if the status is not rejected
    if current_state not in [
        0,
        1,
        2,
        3,
        4,
    ]:  # If the state is not Ended, Withdrawn, Cancelled, Rejected
        incorrect_state_response = HTTP400Response(message="USSP network can only be queried for operational intents that are active")
        op = json.dumps(asdict(incorrect_state_response))
        return HttpResponse(op, status=404, content_type="application/json")

    operational_intent_volumes_raw = json.loads(flight_declaration.operational_intent)
    all_volumes = []
    operational_intent_volumes = operational_intent_volumes_raw["volumes"]
    for operational_intent_volume in operational_intent_volumes:
        volume4D = my_operational_intent_parser.parse_volume_to_volume4D(volume=operational_intent_volume)
        all_volumes.append(volume4D)
    # Check redis for opints and generate geojson
    try:
        operational_intent_geojson = my_scd_helper.get_and_process_nearby_operational_intents(volumes=all_volumes)
    except ValueError:
        logger.info("The received data from peer USS had errors and failed validation checks..")
        operational_intent_geojson = []
    except ConnectionError:
        logger.info("The received data from peer USS had errors and failed validation checks..")
        operational_intent_geojson = []

    # return opints as GeoJSON
    return HttpResponse(
        json.dumps(operational_intent_geojson),
        status=200,
        content_type="application/json",
    )


@method_decorator(requires_scopes([FLIGHTBLENDER_READ_SCOPE]), name="dispatch")
class FlightDeclarationCreateList(mixins.ListModelMixin, generics.GenericAPIView):
    queryset = FlightDeclaration.objects.all()
    serializer_class = FlightDeclarationSerializer
    pagination_class = StandardResultsSetPagination

    def get_relevant_flight_declaration(self, start_date, end_date, view_port: List[float]):
        present = arrow.now()
        if start_date and end_date:
            s_date = arrow.get(start_date, "YYYY-MM-DD")
            e_date = arrow.get(end_date, "YYYY-MM-DD")

        else:
            s_date = present.shift(days=-1)
            e_date = present.shift(days=1)
        all_fd_within_timelimits = FlightDeclaration.objects.filter(start_datetime__gte=s_date.isoformat(), end_datetime__lte=e_date.isoformat())

        logger.info("Found %s flight declarations" % len(all_fd_within_timelimits))
        if view_port:
            INDEX_NAME = "opint_idx"
            my_rtree_helper = FlightDeclarationRTreeIndexFactory(index_name=INDEX_NAME)
            my_rtree_helper.generate_flight_declaration_index(all_flight_declarations=all_fd_within_timelimits)

            all_relevant_fences = my_rtree_helper.check_box_intersection(view_box=view_port)
            relevant_id_set = []
            for i in all_relevant_fences:
                relevant_id_set.append(i["flight_declaration_id"])

            my_rtree_helper.clear_rtree_index()
            filtered_relevant_fd = FlightDeclaration.objects.filter(id__in=relevant_id_set)

        else:
            filtered_relevant_fd = all_fd_within_timelimits

        return filtered_relevant_fd

    def get_queryset(self):
        start_date = self.request.query_params.get("start_date", None)
        end_date = self.request.query_params.get("end_date", None)

        view = self.request.query_params.get("view", None)
        view_port = []
        if view:
            view_port = [float(i) for i in view.split(",")]

        responses = self.get_relevant_flight_declaration(view_port=view_port, start_date=start_date, end_date=end_date)
        return responses

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        try:
            assert request.headers["Content-Type"] == RESPONSE_CONTENT_TYPE
        except AssertionError:
            msg = {"message": "Unsupported Media Type"}
            return JsonResponse(msg, status=415, mimetype=RESPONSE_CONTENT_TYPE)
        else:
            req = request.data

        try:
            assert req.keys() >= {
                "originating_party",
                "start_datetime",
                "end_datetime",
                "flight_declaration_geo_json",
                "type_of_operation",
            }

        except AssertionError:
            msg = json.dumps(
                {
                    "message": "Not all necessary fields were provided. Originating Party, Start Datetime, End Datetime, Flight Declaration and Type of operation must be provided."
                }
            )
            return HttpResponse(msg, status=400)

        try:
            flight_declaration_geo_json = req["flight_declaration_geo_json"]
        except KeyError:
            msg = json.dumps({"message": "A valid flight declaration as specified by the A flight declaration protocol must be submitted."})
            return HttpResponse(msg, status=400)

        my_database_writer = ArgonServerDatabaseWriter()
        USSP_NETWORK_ENABLED = int(env.get("USSP_NETWORK_ENABLED", 0))

        submitted_by = None if "submitted_by" not in req else req["submitted_by"]
        approved_by = None if "approved_by" not in req else req["approved_by"]
        is_approved = False
        type_of_operation = 0 if "type_of_operation" not in req else req["type_of_operation"]
        originating_party = "No Flight Information" if "originating_party" not in req else req["originating_party"]
        now = arrow.now()

        start_datetime = now.isoformat() if "start_datetime" not in req else arrow.get(req["start_datetime"]).isoformat()
        end_datetime = now.isoformat() if "end_datetime" not in req else arrow.get(req["end_datetime"]).isoformat()

        two_days_from_now = now.shift(days=2)

        # verify start and end date time
        s_datetime = arrow.get(start_datetime)
        e_datetime = arrow.get(end_datetime)

        if s_datetime < now or e_datetime < now or e_datetime > two_days_from_now or s_datetime > two_days_from_now:
            msg = json.dumps({"message": "A flight declaration cannot have a start / end time in the past or after two days from current time."})
            return HttpResponse(msg, status=400)
        all_features = []

        for feature in flight_declaration_geo_json["features"]:
            geometry = feature["geometry"]
            s = shape(geometry)
            if s.is_valid:
                all_features.append(s)
            else:
                op = json.dumps(
                    {
                        "message": "Error in processing the submitted GeoJSON: every Feature in a GeoJSON FeatureCollection must have a valid geometry, please check your submitted FeatureCollection"
                    }
                )
                return HttpResponse(op, status=400, content_type=RESPONSE_CONTENT_TYPE)

            props = feature["properties"]
            try:
                assert "min_altitude" in props
                assert "max_altitude" in props
            except AssertionError:
                op = json.dumps(
                    {
                        "message": "Error in processing the submitted GeoJSON every Feature in a GeoJSON FeatureCollection must have a min_altitude and max_altitude data structure"
                    }
                )
                return HttpResponse(op, status=400, content_type=RESPONSE_CONTENT_TYPE)
            else:
                min_altitude = Altitude(meters=props["min_altitude"]["meters"], datum=props["min_altitude"]["datum"])
                max_altitude = Altitude(meters=props["max_altitude"]["meters"], datum=props["max_altitude"]["datum"])
                logging.debug(min_altitude, max_altitude)

        # Default state is Processing if working with a DSS, otherwise it is Accepted
        declaration_state = 0 if USSP_NETWORK_ENABLED else 1

        flight_declaration = FlightDeclarationRequest(
            features=all_features,
            type_of_operation=type_of_operation,
            submitted_by=submitted_by,
            approved_by=approved_by,
            is_approved=is_approved,
            state=declaration_state,
        )

        my_operational_intent_converter = OperationalIntentsConverter()

        parital_op_int_ref = my_operational_intent_converter.create_partial_operational_intent_ref(
            geo_json_fc=flight_declaration_geo_json,
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            priority=0,
        )

        bounds = my_operational_intent_converter.get_geo_json_bounds()

        logger.info("Checking intersections with Geofences..")
        view_box = [float(i) for i in bounds.split(",")]

        fence_within_timelimits = GeoFence.objects.filter(start_datetime__lte=start_datetime, end_datetime__gte=end_datetime).exists()
        all_relevant_fences = []
        if fence_within_timelimits:
            all_fences_within_timelimits = GeoFence.objects.filter(start_datetime__lte=start_datetime, end_datetime__gte=end_datetime)
            INDEX_NAME = "geofence_idx"
            my_rtree_helper = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(index_name=INDEX_NAME)
            my_rtree_helper.generate_geo_fence_index(all_fences=all_fences_within_timelimits)
            all_relevant_fences = my_rtree_helper.check_box_intersection(view_box=view_box)
            relevant_id_set = []
            for i in all_relevant_fences:
                relevant_id_set.append(i["geo_fence_id"])

            my_rtree_helper.clear_rtree_index()
            logger.info("Geofence intersections checked, found {num_intersections} fences".format(num_intersections=len(relevant_id_set)))
            if all_relevant_fences:
                is_approved = 0
                declaration_state = 8

        all_relevant_declarations = []
        existing_declaration_within_timelimits = FlightDeclaration.objects.filter(
            start_datetime__lte=end_datetime, end_datetime__gte=start_datetime
        ).exists()
        if existing_declaration_within_timelimits:
            all_declarations_within_timelimits = FlightDeclaration.objects.filter(start_datetime__lte=end_datetime, end_datetime__gte=start_datetime)
            INDEX_NAME = "flight_declaration_idx"
            my_fd_rtree_helper = FlightDeclarationRTreeIndexFactory(index_name=INDEX_NAME)
            my_fd_rtree_helper.generate_flight_declaration_index(all_flight_declarations=all_declarations_within_timelimits)
            all_relevant_declarations = my_fd_rtree_helper.check_box_intersection(view_box=view_box)
            relevant_id_set = []
            for i in all_relevant_declarations:
                relevant_id_set.append(i["flight_declaration_id"])
            my_fd_rtree_helper.clear_rtree_index()
            logger.info(
                "Flight Declaration intersections checked, found {all_relevant_declarations} declarations".format(
                    all_relevant_declarations=len(relevant_id_set)
                )
            )
            if all_relevant_declarations:
                logger.info("Setting state as rejected...")
                is_approved = 0
                declaration_state = 8

        flight_declaration = FlightDeclaration(
            operational_intent=json.dumps(asdict(parital_op_int_ref)),
            bounds=bounds,
            type_of_operation=type_of_operation,
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
                notes="Rejected by Argon Server because of  time / space conflicts with existing operations",
            )

        flight_declaration_id = str(flight_declaration.id)

        send_operational_update_message.delay(
            flight_declaration_id=flight_declaration_id,
            message_text="Flight Declaration created..",
            level="info",
        )

        if all_relevant_fences and all_relevant_declarations:
            # Async submit flight declaration to DSS
            logger.info("Self deconfliction failed, this declaration cannot be sent to the DSS system..")

            self_deconfliction_failed_msg = "Self deconfliction failed for operation {operation_id} did not pass self-deconfliction, there are existing operations declared in the area".format(
                operation_id=flight_declaration_id
            )
            send_operational_update_message.delay(
                flight_declaration_id=flight_declaration_id,
                message_text=self_deconfliction_failed_msg,
                level="error",
            )

        else:
            logger.info("Self deconfliction success, this declaration will be sent to the DSS system, if a DSS URL is provided..")
            # Only send it to the USSP network if the declaration is accepted and the network is available.

            if declaration_state == 0 and USSP_NETWORK_ENABLED:
                submit_flight_declaration_to_dss_async.delay(flight_declaration_id=flight_declaration_id)
        creation_response = FlightDeclarationCreateResponse(
            id=flight_declaration_id,
            message="Submitted Flight Declaration",
            is_approved=is_approved,
            state=declaration_state,
        )

        op = json.dumps(asdict(creation_response))
        return HttpResponse(op, status=200, content_type=RESPONSE_CONTENT_TYPE)
