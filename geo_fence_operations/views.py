# Create your views here.

# Create your views here.
import json
import logging
import uuid
from dataclasses import asdict
from decimal import Decimal

import arrow
import pyproj
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from implicitdict import ImplicitDict
from marshmallow import ValidationError
from rest_framework import generics, mixins, status
from rest_framework.decorators import api_view
from rest_framework.renderers import JSONRenderer
from shapely.geometry import Point, shape
from shapely.ops import unary_union

from auth_helper.common import get_redis
from auth_helper.utils import requires_scopes
from common.data_definitions import (
    FLIGHTBLENDER_READ_SCOPE,
    FLIGHTBLENDER_WRITE_SCOPE,
    GEOFENCE_INDEX_BASEPATH,
)
from common.utils import EnhancedJSONEncoder
from flight_declaration_operations.pagination import StandardResultsSetPagination

from . import rtree_geo_fence_helper
from .buffer_helper import toFromUTM
from .common import validate_geo_zone
from .data_definitions import (
    GeoAwarenessTestStatus,
    GeoFencePutSchema,
    GeoSpatialMapTestHarnessStatus,
    GeoZoneCheckRequestBody,
    GeoZoneCheckResult,
    GeoZoneChecksResponse,
    GeoZoneFilterPosition,
    GeoZoneHttpsSource,
)
from .models import GeoFence
from .serializers import GeoFenceSerializer, GeoSpatialMapListSerializer
from .tasks import download_geozone_source, write_geo_zone

logger = logging.getLogger("django")

INDEX_NAME = "geofence_proc"


@api_view(["PUT"])
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def set_geo_fence(request: HttpRequest):
    try:
        assert request.headers["Content-Type"] == "application/json"
    except AssertionError:
        msg = {"message": "Unsupported Media Type"}
        return HttpResponse(
            json.dumps(msg),
            status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            content_type="application/json",
        )
    geofence_create_request = GeoFencePutSchema()
    geo_fence_request_data = request.data

    try:
        geo_fence_request_data = geofence_create_request.load(geo_fence_request_data)
    except ValidationError as err:
        return HttpResponse(
            JSONRenderer().render(err.messages),
            status=status.HTTP_400_BAD_REQUEST,
            content_type="application/json",
        )

    shp_features = []

    for feature in geo_fence_request_data["features"]:
        shp_features.append(shape(feature["geometry"]))
    combined_features = unary_union(shp_features)
    bnd_tuple = combined_features.bounds
    bounds = ",".join([f"{x:.7f}" for x in bnd_tuple])

    start_time = arrow.now().isoformat() if "start_time" not in feature["properties"] else arrow.get(feature["properties"]["start_time"]).isoformat()
    end_time = (
        arrow.now().shift(hours=1).isoformat()
        if "end_time" not in feature["properties"]
        else arrow.get(feature["properties"]["end_time"]).isoformat()
    )

    upper_limit = Decimal(feature["properties"]["upper_limit"])
    lower_limit = Decimal(feature["properties"]["lower_limit"])
    name = feature["properties"]["name"]

    raw_geo_fence = json.dumps(geo_fence_request_data)
    geo_f = GeoFence(
        raw_geo_fence=raw_geo_fence,
        start_datetime=start_time,
        end_datetime=end_time,
        upper_limit=upper_limit,
        lower_limit=lower_limit,
        bounds=bounds,
        name=name,
    )
    geo_f.save()

    op = json.dumps({"message": "Geofence Declaration submitted", "id": str(geo_f.id)})
    return HttpResponse(op, status=status.HTTP_200_OK, content_type="application/json")


@api_view(["POST"])
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def set_geozone(request):
    try:
        assert request.headers["Content-Type"] == "application/json"
    except AssertionError:
        msg = {"message": "Unsupported Media Type"}
        return HttpResponse(
            json.dumps(msg),
            status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            content_type="application/json",
        )

    try:
        geo_zone = request.data
    except KeyError:
        msg = json.dumps({"message": "A geozone object is necessary in the body of the request"})
        return HttpResponse(msg, status=status.HTTP_400_BAD_REQUEST)

    is_geo_zone_valid = validate_geo_zone(geo_zone)

    if is_geo_zone_valid:
        write_geo_zone.delay(geo_zone=json.dumps(geo_zone))

        geo_f = uuid.uuid4()
        op = json.dumps({"message": "GeoZone Declaration submitted", "id": str(geo_f)})
        return HttpResponse(op, status=status.HTTP_200_OK, content_type="application/json")

    else:
        msg = json.dumps({"message": "A valid geozone object with a description is necessary the body of the request"})
        return HttpResponse(msg, status=status.HTTP_400_BAD_REQUEST, content_type="application/json")


@method_decorator(requires_scopes([FLIGHTBLENDER_READ_SCOPE]), name="dispatch")
class GeoFenceDetail(mixins.RetrieveModelMixin, generics.GenericAPIView):
    queryset = GeoFence.objects.filter(is_test_dataset=False)
    serializer_class = GeoFenceSerializer

    def get(self, request, *args, **kwargs):
        return self.retrieve(request, *args, **kwargs)


@method_decorator(requires_scopes([FLIGHTBLENDER_READ_SCOPE]), name="dispatch")
class GeoFenceList(mixins.ListModelMixin, generics.GenericAPIView):
    queryset = GeoFence.objects.filter(is_test_dataset=False)
    serializer_class = GeoFenceSerializer
    pagination_class = StandardResultsSetPagination

    def get_relevant_geo_fence(self, start_date, end_date, view_port: list[float]):
        present = arrow.now()
        if start_date and end_date:
            s_date = arrow.get(start_date, "YYYY-MM-DD")
            e_date = arrow.get(end_date, "YYYY-MM-DD")

        else:
            s_date = present.shift(days=-1)
            e_date = present.shift(days=1)

        all_fences_within_timelimits = GeoFence.objects.filter(start_datetime__lte=e_date.isoformat(), end_datetime__gte=s_date.isoformat())

        logger.info("Found %s geofences" % len(all_fences_within_timelimits))

        if view_port:
            my_rtree_helper = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(index_name=GEOFENCE_INDEX_BASEPATH)
            my_rtree_helper.generate_geo_fence_index(all_fences=all_fences_within_timelimits)
            all_relevant_fences = my_rtree_helper.check_box_intersection(view_box=view_port)
            relevant_id_set = []
            for i in all_relevant_fences:
                relevant_id_set.append(i["geo_fence_id"])

            my_rtree_helper.clear_rtree_index()
            filtered_relevant_fences = GeoFence.objects.filter(id__in=relevant_id_set)

        else:
            filtered_relevant_fences = all_fences_within_timelimits

        return filtered_relevant_fences

    def get_queryset(self):
        start_date = self.request.query_params.get("start_date", None)
        end_date = self.request.query_params.get("end_date", None)

        view = self.request.query_params.get("view", None)
        view_port = []
        if view:
            view_port = [float(i) for i in view.split(",")]

        responses = self.get_relevant_geo_fence(view_port=view_port, start_date=start_date, end_date=end_date)
        return responses

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


@method_decorator(requires_scopes([FLIGHTBLENDER_READ_SCOPE]), name="dispatch")
class GeospatialMapList(mixins.ListModelMixin, generics.GenericAPIView):
    queryset = GeoFence.objects.filter(is_test_dataset=False).order_by("created_at")
    serializer_class = GeoSpatialMapListSerializer

    def get_relevant_geo_fence(self, start_date, end_date, view_port: list[float]):
        present = arrow.now()
        if start_date and end_date:
            s_date = arrow.get(start_date, "YYYY-MM-DD")
            e_date = arrow.get(end_date, "YYYY-MM-DD")

        else:
            s_date = present.shift(days=-1)
            e_date = present.shift(days=1)

        all_fences_within_timelimits = GeoFence.objects.filter(start_datetime__gte=s_date.isoformat(), end_datetime__lte=e_date.isoformat())
        logger.info("Found %s geofences" % len(all_fences_within_timelimits))

        if view_port:
            my_rtree_helper = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(index_name=GEOFENCE_INDEX_BASEPATH)
            my_rtree_helper.generate_geo_fence_index(all_fences=all_fences_within_timelimits)
            all_relevant_fences = my_rtree_helper.check_box_intersection(view_box=view_port)
            relevant_id_set = []
            for i in all_relevant_fences:
                relevant_id_set.append(i["geo_fence_id"])

            my_rtree_helper.clear_rtree_index()
            filtered_relevant_fences = GeoFence.objects.filter(id__in=relevant_id_set)

        else:
            filtered_relevant_fences = all_fences_within_timelimits

        return filtered_relevant_fences

    def get_queryset(self):
        start_date = self.request.query_params.get("start_date", None)
        end_date = self.request.query_params.get("end_date", None)

        view = self.request.query_params.get("view", None)
        view_port = []
        if view:
            view_port = [float(i) for i in view.split(",")]

        responses = self.get_relevant_geo_fence(view_port=view_port, start_date=start_date, end_date=end_date)
        return responses

    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)


@method_decorator(requires_scopes(["geo-awareness.test"]), name="dispatch")
class GeoZoneTestHarnessStatus(generics.GenericAPIView):
    def get(self, request, *args, **kwargs):
        status = GeoSpatialMapTestHarnessStatus(status="Ready", api_version="latest")
        return JsonResponse(json.loads(json.dumps(status, cls=EnhancedJSONEncoder)), status=200)


@method_decorator(requires_scopes(["geo-awareness.test"]), name="dispatch")
class GeoZoneSourcesOperations(generics.GenericAPIView):
    def put(self, request, geozone_source_id):
        r = get_redis()
        try:
            geo_zone_url_details = ImplicitDict.parse(request.data, GeoZoneHttpsSource)
        except KeyError:
            ga_import_response = GeoAwarenessTestStatus(
                result="Rejected",
                message="There was an error in processing the request payload, a url and format key is required for successful processing",
            )
            return JsonResponse(
                json.loads(json.dumps(ga_import_response, cls=EnhancedJSONEncoder)),
                status=200,
            )

        url_validator = URLValidator()
        try:
            url_validator(geo_zone_url_details.https_source.url)
        except ValidationError:
            ga_import_response = GeoAwarenessTestStatus(result="Unsupported", message="There was an error in the url provided")
            return JsonResponse(
                json.loads(json.dumps(ga_import_response, cls=EnhancedJSONEncoder)),
                status=200,
            )

        geoawareness_test_data_store = "geoawarenes_test." + str(geozone_source_id)

        ga_import_response = GeoAwarenessTestStatus(result="Activating", message="")
        download_geozone_source.delay(
            geo_zone_url=geo_zone_url_details.https_source.url,
            geozone_source_id=geozone_source_id,
        )

        r.set(geoawareness_test_data_store, json.dumps(asdict(ga_import_response)))
        r.expire(name=geoawareness_test_data_store, time=3000)

        return JsonResponse(
            json.loads(json.dumps(ga_import_response, cls=EnhancedJSONEncoder)),
            status=200,
        )

    def get(self, request, geozone_source_id):
        geoawareness_test_data_store = "geoawarenes_test." + str(geozone_source_id)
        r = get_redis()

        if r.exists(geoawareness_test_data_store):
            test_data_status = r.get(geoawareness_test_data_store)
            test_status = json.loads(test_data_status)
            ga_test_status = GeoAwarenessTestStatus(result=test_status["result"], message="")
            return JsonResponse(
                json.loads(json.dumps(ga_test_status, cls=EnhancedJSONEncoder)),
                status=200,
            )
        else:
            return JsonResponse({}, status=404)

    def delete(self, request, geozone_source_id):
        geoawareness_test_data_store = "geoawarenes_test." + str(geozone_source_id)
        r = get_redis()
        if r.exists(geoawareness_test_data_store):
            # TODO: delete the test and dataset
            all_test_geozones = GeoFence.objects.filter(is_test_dataset=1)
            for geozone in all_test_geozones.all():
                geozone.delete()
            deletion_status = GeoAwarenessTestStatus(
                result="Deactivating",
                message="Test data has been scheduled to be deleted",
            )
            r.set(geoawareness_test_data_store, json.dumps(asdict(deletion_status)))
            return JsonResponse(
                json.loads(json.dumps(deletion_status, cls=EnhancedJSONEncoder)),
                status=200,
            )

        else:
            return JsonResponse({}, status=404)


@method_decorator(requires_scopes(["geo-awareness.test"]), name="dispatch")
class GeoZoneCheck(generics.GenericAPIView):
    def post(self, request, *args, **kwargs):
        proj = pyproj.Proj("+proj=utm +zone=24 +south +datum=WGS84 +units=m +no_defs ")

        geo_zone_body = ImplicitDict.parse(request.data, GeoZoneCheckRequestBody)
        geo_zones_of_interest = False
        geo_zone_checks = geo_zone_body.checks

        for geo_zone_check in geo_zone_checks:
            for filter_set in geo_zone_check["filter_sets"]:
                if "position" in filter_set:
                    filter_position = ImplicitDict.parse(filter_set["position"], GeoZoneFilterPosition)
                    relevant_geo_fences = GeoFence.objects.filter(is_test_dataset=1)
                    my_rtree_helper = rtree_geo_fence_helper.GeoFenceRTreeIndexFactory(index_name=GEOFENCE_INDEX_BASEPATH)
                    # Buffer the point to get a small view port / bounds
                    init_point = Point(filter_position)
                    init_shape_utm = toFromUTM(init_point, proj)
                    buffer_shape_utm = init_shape_utm.buffer(1)
                    buffer_shape_lonlat = toFromUTM(buffer_shape_utm, proj, inv=True)
                    view_port = buffer_shape_lonlat.bounds

                    my_rtree_helper.generate_geo_fence_index(all_fences=relevant_geo_fences)
                    all_relevant_fences = my_rtree_helper.check_box_intersection(view_box=view_port)
                    my_rtree_helper.clear_rtree_index()
                    if all_relevant_fences:
                        geo_zones_of_interest = True

                if "after" in filter_set:
                    after_query = arrow.get(filter_set["after"])
                    geo_zones_exist = GeoFence.objects.filter(start_datetime__gte=after_query, is_test_dataset=1).exists()
                    if geo_zones_exist:
                        geo_zones_of_interest = True
                if "before" in filter_set:
                    before_query = arrow.get(filter_set["before"])
                    geo_zones_exist = GeoFence.objects.filter(before_datetime__lte=before_query, is_test_dataset=1).exists()
                    if geo_zones_exist:
                        geo_zones_of_interest = True
                if "ed269" in filter_set:
                    ed269_filter_set = filter_set["ed269"]
                    if "uSpaceClass" in ed269_filter_set:
                        uSpace_class_to_query = ed269_filter_set["uSpaceClass"]
                        # Iterate over Geofences to see if there is a uSpace class
                        geo_zones_exist = False
                        all_geo_zones = GeoFence.objects.filter(is_test_dataset=1)
                        for current_geo_zone in all_geo_zones:
                            current_geo_zone_uSpace_class = current_geo_zone["uSpaceClass"]
                            if uSpace_class_to_query == current_geo_zone_uSpace_class:
                                geo_zones_exist = True
                                break

                        if geo_zones_exist:
                            geo_zones_of_interest = True

                    if "acceptableRestrictions" in ed269_filter_set:
                        acceptable_restrictions = ed269_filter_set["acceptableRestrictions"]
                        geo_zones_exist = False
                        all_geo_zones = GeoFence.objects.filter(is_test_dataset=1)
                        for current_geo_zone in all_geo_zones:
                            current_geo_zone_restriction = current_geo_zone["restriction"]
                            if current_geo_zone_restriction in acceptable_restrictions:
                                geo_zones_exist = True
                                break

                        if geo_zones_exist:
                            geo_zones_of_interest = True

        if geo_zones_of_interest:
            geo_zone_check_result = GeoZoneCheckResult(geozone="Present")
        else:
            geo_zone_check_result = GeoZoneCheckResult(geozone="Absent")

        geo_zone_response = GeoZoneChecksResponse(applicableGeozone=geo_zone_check_result, message="Test")
        return JsonResponse(
            json.loads(json.dumps(geo_zone_response, cls=EnhancedJSONEncoder)),
            status=200,
        )
