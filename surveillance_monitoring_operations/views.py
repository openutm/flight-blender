# Create your views here.
import logging
import uuid
from dataclasses import asdict
from datetime import timedelta

from django.http import JsonResponse
from django.utils import timezone
from rest_framework.decorators import api_view

from auth_helper.utils import requires_scopes
from common.data_definitions import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)
from surveillance_monitoring_operations.tasks import send_heartbeat_to_consumer

from .data_definitions import HealthMessage, SurveillanceStatus

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


@api_view(["PUT"])
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def start_stop_surveillance_heartbeat_track(request, session_id):
    database_writer = FlightBlenderDatabaseWriter()
    database_reader = FlightBlenderDatabaseReader()

    action = request.data.get("action")
    if action not in ["start", "stop"]:
        return JsonResponse({"error": "Invalid action"}, status=400)

    # Logic to start or stop the heartbeat task
    if action == "start":
        # Start the heartbeat task
        if not session_id:
            session_id = str(uuid.uuid4())
            end_datetime = (timezone.now() + timedelta(minutes=30)).isoformat()
        database_writer.create_surveillance_session(session_id=session_id, valid_until=end_datetime)
        database_writer.create_surveillance_monitoring_heartbeat_periodic_task(session_id=session_id)
        return JsonResponse({"status": "Surveillance monitoring heartbeat started"})
    else:
        # Stop the heartbeat task
        # Note: Stopping a Celery task programmatically can be complex and may require additional setup
        surveillance_task = database_reader.get_surveillance_session_by_id(session_id=session_id)
        if not surveillance_task:
            return JsonResponse({"error": "Invalid session_id"}, status=400)
        database_writer.remove_surveillance_monitoring_heartbeat_periodic_task(surveillance_monitoring_heartbeat_task=surveillance_task)
        return JsonResponse({"status": "Surveillance monitoring task removed successfully"})
