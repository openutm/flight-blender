from dataclasses import asdict

from surveillance_monitoring_operations.tasks import send_heartbeat_to_consumer
from .data_definitions import HealthMessage, SurveillanceStatus
from django.http import JsonResponse
from rest_framework.decorators import api_view
from auth_helper.utils import requires_scopes
from common.data_definitions import FLIGHTBLENDER_READ_SCOPE

from common.database_operations import (
    FlightBlenderDatabaseWriter,
)

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


@api_view(["POST"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def start_stop_surveillance_heartbeat_track(request):

    database_writer = FlightBlenderDatabaseWriter()
    
    action = request.data.get("action")
    if action not in ["start", "stop"]:
        return JsonResponse({"error": "Invalid action"}, status=400)

    # Logic to start or stop the heartbeat task
    if action == "start":
        # Start the heartbeat task
        database_writer.create_surveillance_monitoring_heartbeat_periodic_task()
        return JsonResponse({"status": "Surveillance monitoring heartbeat started"})
    else:
        # Stop the heartbeat task
        # Note: Stopping a Celery task programmatically can be complex and may require additional setup
        return JsonResponse(
            {"status": "Surveillance monitoring heartbeat stopping not implemented"}
        )
