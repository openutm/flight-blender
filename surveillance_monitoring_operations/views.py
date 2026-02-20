# Create your views here.

import uuid
from dataclasses import asdict
from datetime import timedelta

import arrow
from django.http import JsonResponse
from django.utils import timezone
from loguru import logger
from rest_framework.decorators import api_view

from auth_helper.utils import requires_scopes
from common.data_definitions import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from common.database_operations import (
    FlightBlenderDatabaseReader,
    FlightBlenderDatabaseWriter,
)

from .data_definitions import (
    HealthMessage,
    SurveillanceMetrics,
    SurveillanceSensorDetail,
    SurveillanceSensorFailureNotificationDetail,
    SurveillanceStatus,
)
from .metric_calculator import SurveillanceMetricCalculator


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def surveillance_health(request):
    database_reader = FlightBlenderDatabaseReader()
    active_sensors = database_reader.get_active_surveillance_sensors()

    sensor_statuses = []
    for sensor in active_sensors:
        health = database_reader.get_sensor_health_record(sensor_id=str(sensor.id))
        if health:
            sensor_statuses.append(health.status)

    if not sensor_statuses or all(s == "outage" for s in sensor_statuses):
        current_status = SurveillanceStatus.OUTAGE
    elif any(s in ("degraded", "outage") for s in sensor_statuses):
        current_status = SurveillanceStatus.DEGRADED
    else:
        current_status = SurveillanceStatus.OPERATIONAL

    health_obj = HealthMessage(
        sdsp_identifier="FLIGHT_BLENDER_SDSP",
        current_status=current_status,
        machine_readable_file_of_estimated_coverage="",
        scheduled_degradations="None",
        timestamp=arrow.utcnow().isoformat(),
    )
    return JsonResponse(asdict(health_obj))


@api_view(["PUT"])
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def start_stop_surveillance_heartbeat_track(request, session_id):
    database_writer = FlightBlenderDatabaseWriter()
    database_reader = FlightBlenderDatabaseReader()

    action = request.data.get("action")
    if action not in ["start", "stop"]:
        return JsonResponse({"error": "Invalid action provided"}, status=400)

    # Logic to start or stop the heartbeat task
    if action == "start":
        # Start the heartbeat task
        surveillance_task_exists = database_reader.get_surveillance_session_by_id(session_id=session_id)
        # if task already exists, return error
        if surveillance_task_exists:
            return JsonResponse(
                {"error": "Surveillance monitoring heartbeat task already exists"},
                status=400,
            )
        end_datetime = (timezone.now() + timedelta(minutes=30)).isoformat()
        if not session_id:
            session_id = str(uuid.uuid4())

        database_writer.create_surveillance_session(session_id=session_id, valid_until=end_datetime)
        database_writer.create_surveillance_monitoring_heartbeat_periodic_task(session_id=str(session_id))
        database_writer.create_surveillance_monitoring_track_periodic_task(session_id=str(session_id))
        return JsonResponse({"status": "Surveillance monitoring heartbeat started"})
    else:
        # Stop the heartbeat task
        surveillance_session = database_reader.get_surveillance_session_by_id(session_id=session_id)
        if not surveillance_session:
            return JsonResponse({"error": f"Invalid session_id provided: {session_id}"}, status=400)
        surveillance_tasks = database_reader.get_surveillance_periodic_tasks_by_session_id(session_id=session_id)
        if not surveillance_tasks:
            return JsonResponse(
                {"error": f"No active surveillance monitoring tasks found for {session_id}"},
                status=400,
            )
        for surveillance_task in surveillance_tasks:
            database_writer.remove_surveillance_monitoring_heartbeat_periodic_task(surveillance_monitoring_heartbeat_task=surveillance_task)
        return JsonResponse({"status": "Surveillance monitoring tasks removed successfully"})


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def list_surveillance_sensors(request):
    database_reader = FlightBlenderDatabaseReader()
    sensors = database_reader.get_active_surveillance_sensors()
    sensor_list = [
        asdict(
            SurveillanceSensorDetail(
                id=str(sensor.id),
                sensor_type_display=sensor.sensor_type_display(),
                sensor_identifier=sensor.sensor_identifier,
                created_at=sensor.created_at.isoformat(),
                updated_at=sensor.updated_at.isoformat(),
            )
        )
        for sensor in sensors
    ]
    return JsonResponse({"active_sensors": sensor_list})


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def service_metrics(request):
    start_date_str = request.query_params.get("start_date", None)
    end_date_str = request.query_params.get("end_date", None)
    session_id = request.query_params.get("session_id", None)

    now = arrow.now()
    one_week_ago = now.shift(weeks=-1)
    start_date = arrow.get(start_date_str).datetime if start_date_str else one_week_ago.datetime
    end_date = arrow.get(end_date_str).datetime if end_date_str else now.datetime
    logger.info(f"Received request for service metrics with start_date: {start_date} and end_date: {end_date}")

    database_reader = FlightBlenderDatabaseReader()
    calculator = SurveillanceMetricCalculator(database_reader=database_reader)

    active_sessions = database_reader.get_all_active_surveillance_sessions()
    active_session_count = active_sessions.count()

    heartbeat_rate = None
    heartbeat_delivery_probability = None
    track_update_probability = None

    if session_id:
        heartbeat_rate = calculator.calculate_heartbeat_rate(session_id=session_id, start_time=start_date, end_time=end_date)
        heartbeat_delivery_probability = calculator.calculate_heartbeat_delivery_probability(
            session_id=session_id, start_time=start_date, end_time=end_date
        )
        track_update_probability = calculator.calculate_track_update_probability(session_id=session_id, start_time=start_date, end_time=end_date)

    active_sensors = database_reader.get_active_surveillance_sensors()
    per_sensor_health = []
    for sensor in active_sensors:
        per_sensor_health.append(calculator.calculate_sensor_health_metrics(sensor_id=str(sensor.id), start_time=start_date, end_time=end_date))

    aggregate_health = calculator.calculate_aggregate_health_metrics(sensor_metrics_list=per_sensor_health, start_time=start_date, end_time=end_date)

    metric_response = SurveillanceMetrics(
        heartbeat_rate=heartbeat_rate,
        heartbeat_delivery_probability=heartbeat_delivery_probability,
        track_update_probability=track_update_probability,
        per_sensor_health=per_sensor_health,
        aggregate_health=aggregate_health,
        active_sessions=active_session_count,
        window_start=start_date.isoformat(),
        window_end=end_date.isoformat(),
    )
    return JsonResponse(asdict(metric_response))


@api_view(["PUT"])
@requires_scopes([FLIGHTBLENDER_WRITE_SCOPE])
def update_sensor_health(request, sensor_id):
    """
    Update the health status of a surveillance sensor.

    Body: { "status": "operational"|"degraded"|"outage", "recovery_type": "automatic"|"manual" }
    recovery_type is required when status is "operational" and the sensor was previously in a
    failure state. Pass "automatic" if recovered by an automated system, "manual" otherwise.
    """
    new_status = request.data.get("status")
    recovery_type = request.data.get("recovery_type", None)

    valid_statuses = {"operational", "degraded", "outage"}
    if new_status not in valid_statuses:
        return JsonResponse(
            {"error": f"Invalid status. Must be one of: {', '.join(sorted(valid_statuses))}"},
            status=400,
        )

    valid_recovery_types = {"automatic", "manual", None}
    if recovery_type not in valid_recovery_types:
        return JsonResponse(
            {"error": "Invalid recovery_type. Must be 'automatic', 'manual', or omitted."},
            status=400,
        )

    if new_status == "operational" and recovery_type is None:
        logger.warning(f"update_sensor_health: status set to operational for sensor {sensor_id} without recovery_type")

    database_writer = FlightBlenderDatabaseWriter()
    success = database_writer.update_sensor_health_status(
        sensor_id=str(sensor_id),
        new_status=new_status,
        recovery_type=recovery_type,
    )

    if not success:
        return JsonResponse({"error": f"Sensor {sensor_id} not found or update failed"}, status=404)

    return JsonResponse({"status": "Sensor health updated", "sensor_id": str(sensor_id), "new_status": new_status})


@api_view(["GET"])
@requires_scopes([FLIGHTBLENDER_READ_SCOPE])
def list_sensor_failure_notifications(request):
    """
    List sensor failure and recovery notifications.

    Query params:
        sensor_id (optional): filter by sensor UUID
        start_date (optional): ISO8601 start of window (default: 7 days ago)
        end_date (optional): ISO8601 end of window (default: now)
    """
    sensor_id = request.query_params.get("sensor_id", None)
    start_date_str = request.query_params.get("start_date", None)
    end_date_str = request.query_params.get("end_date", None)

    now = arrow.now()
    one_week_ago = now.shift(weeks=-1)
    start_date = arrow.get(start_date_str).datetime if start_date_str else one_week_ago.datetime
    end_date = arrow.get(end_date_str).datetime if end_date_str else now.datetime

    database_reader = FlightBlenderDatabaseReader()

    if sensor_id:
        notifications = database_reader.get_failure_notifications_for_sensor(sensor_id=sensor_id, start_time=start_date, end_time=end_date)
    else:
        from surveillance_monitoring_operations.models import SurveillanceSensorFailureNotification

        notifications = SurveillanceSensorFailureNotification.objects.filter(
            created_at__gte=start_date,
            created_at__lte=end_date,
        ).order_by("-created_at")

    notification_list = [
        asdict(
            SurveillanceSensorFailureNotificationDetail(
                id=str(n.id),
                sensor_id=str(n.sensor.id),
                sensor_identifier=n.sensor.sensor_identifier,
                previous_status=n.previous_status,
                new_status=n.new_status,
                recovery_type=n.recovery_type,
                message=n.message,
                created_at=n.created_at.isoformat(),
            )
        )
        for n in notifications
    ]
    return JsonResponse({"notifications": notification_list})
