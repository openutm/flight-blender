from dataclasses import asdict
from .data_definitions import HealthMessage, SurveillanceStatus
from django.http import JsonResponse
from rest_framework.decorators import api_view
from auth_helper.utils import requires_scopes
from common.data_definitions import FLIGHTBLENDER_READ_SCOPE

# Create your views here.
import logging

logger = logging.getLogger("django")


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def surveillance_health(request):
    # Add logic to retrieve surveillance health data
    # For example, query the database or external APIs
    health_obj = HealthMessage(
        sdsp_identifier="SDSP123",
        current_status=SurveillanceStatus.OPERATIONAL,
        machine_readable_file_of_estimated_coverage="http://example.com/coverage",
        scheduled_degrations="None",
        timestamp="2024-10-01T12:00:00Z",
    )
    return JsonResponse(asdict(health_obj))
