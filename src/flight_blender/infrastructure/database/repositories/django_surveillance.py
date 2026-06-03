import uuid
from datetime import datetime
from typing import Optional
from uuid import UUID

import arrow
from django.db.models import Q, QuerySet
from django.db.utils import IntegrityError
from loguru import logger

from flight_blender.conformance.models import TaskScheduler
from flight_blender.surveillance.models import (
    SurveillanceHeartbeatEvent,
    SurveillanceSensor,
    SurveillanceSensorFailureNotification,
    SurveillanceSensorHealth,
    SurveillanceSensortHealthTracking,
    SurveillanceSession,
    SurveillanceTrackEvent,
)


class DjangoSurveillanceRepository:
    def get_active_surveillance_sensors(self) -> QuerySet[SurveillanceSensor]:
        return SurveillanceSensor.objects.filter(is_active=True)

    def get_surveillance_sensor_by_id(self, sensor_id: UUID) -> Optional[SurveillanceSensor]:
        sensor_exists = SurveillanceSensor.objects.filter(id=sensor_id).exists()
        if not sensor_exists:
            return None
        return SurveillanceSensor.objects.get(id=sensor_id)

    def get_surveillance_session_by_id(self, surveillance_session_id: str) -> Optional[SurveillanceSession]:
        try:
            return SurveillanceSession.objects.get(id=surveillance_session_id)
        except SurveillanceSession.DoesNotExist:
            return None

    def get_surveillance_periodic_tasks_by_session_id(self, surveillance_session_id: UUID | str) -> QuerySet[TaskScheduler]:
        return TaskScheduler.objects.filter(session_id=surveillance_session_id)

    def get_all_active_surveillance_sessions(self) -> QuerySet[SurveillanceSession]:
        now = arrow.now().datetime
        return SurveillanceSession.objects.filter(valid_until__gte=now)

    def get_surveillance_sessions_with_events_in_window(self, start_time: datetime, end_time: datetime) -> QuerySet[SurveillanceSession]:
        return SurveillanceSession.objects.filter(
            Q(heartbeat_events__dispatched_at__gte=start_time, heartbeat_events__dispatched_at__lte=end_time)
            | Q(track_events__dispatched_at__gte=start_time, track_events__dispatched_at__lte=end_time)
        ).distinct()

    def get_sensor_health_record(self, sensor_id: str) -> Optional[SurveillanceSensorHealth]:
        try:
            return SurveillanceSensorHealth.objects.get(sensor__id=sensor_id)
        except SurveillanceSensorHealth.DoesNotExist:
            return None

    def get_health_tracking_records_for_sensor(self, sensor_id: str, start_time: datetime, end_time: datetime) -> QuerySet[SurveillanceSensortHealthTracking]:
        return SurveillanceSensortHealthTracking.objects.filter(
            sensor__id=sensor_id,
            recorded_at__gte=start_time,
            recorded_at__lte=end_time,
        ).order_by("recorded_at")

    def get_sensor_status_before_time(self, sensor_id: str, before_time: datetime) -> Optional[str]:
        record = (
            SurveillanceSensortHealthTracking.objects.filter(
                sensor__id=sensor_id,
                recorded_at__lt=before_time,
            )
            .order_by("-recorded_at")
            .first()
        )
        return record.status if record else None

    def get_heartbeat_events_in_window(self, start_time: datetime, end_time: datetime) -> QuerySet[SurveillanceHeartbeatEvent]:
        return SurveillanceHeartbeatEvent.objects.filter(
            dispatched_at__gte=start_time,
            dispatched_at__lte=end_time,
        ).order_by("dispatched_at")

    def get_heartbeat_events_for_session(self, surveillance_session_id: str, start_time: datetime, end_time: datetime) -> QuerySet[SurveillanceHeartbeatEvent]:
        return SurveillanceHeartbeatEvent.objects.filter(
            session__id=surveillance_session_id,
            dispatched_at__gte=start_time,
            dispatched_at__lte=end_time,
        ).order_by("dispatched_at")

    def get_track_events_for_session(self, surveillance_session_id: str, start_time: datetime, end_time: datetime) -> QuerySet[SurveillanceTrackEvent]:
        return SurveillanceTrackEvent.objects.filter(
            session__id=surveillance_session_id,
            dispatched_at__gte=start_time,
            dispatched_at__lte=end_time,
        ).order_by("dispatched_at")

    def get_failure_notifications_for_sensor(self, sensor_id: str, start_time: datetime, end_time: datetime) -> QuerySet[SurveillanceSensorFailureNotification]:
        return SurveillanceSensorFailureNotification.objects.filter(
            sensor__id=sensor_id,
            created_at__gte=start_time,
            created_at__lte=end_time,
        ).order_by("-created_at")

    def create_surveillance_session(self, surveillance_session_id: UUID | str, valid_until: str) -> bool:
        try:
            surveillance_session = SurveillanceSession(id=surveillance_session_id, valid_until=valid_until)
            surveillance_session.save()
            return True
        except IntegrityError:
            return False

    def create_surveillance_monitoring_heartbeat_periodic_task(self, surveillance_session_id: UUID | str) -> bool:
        surveillance_monitoring_job = TaskScheduler()
        every = 1
        now = arrow.now()
        surveillance_session_id = surveillance_session_id if surveillance_session_id else str(uuid.uuid4())
        expires = now.shift(minutes=1)
        task_name = "send_heartbeat_to_consumer"
        logger.info("Creating periodic task for surveillance monitoring, it expires at %s" % expires)
        try:
            p_task = surveillance_monitoring_job.schedule_every(
                task_name=task_name,
                period="seconds",
                every=every,
                expires=expires.isoformat(),
                session_id=surveillance_session_id,
                flight_declaration=None,
            )
            p_task.start()
            return True
        except Exception as e:
            logger.debug(f"{e}")
            logger.error("Could not create surveillance monitoring heartbeat periodic task")
            return False

    def create_surveillance_monitoring_track_periodic_task(self, surveillance_session_id: str) -> bool:
        surveillance_monitoring_job = TaskScheduler()
        every = 1
        now = arrow.now()
        surveillance_session_id = surveillance_session_id if surveillance_session_id else str(uuid.uuid4())
        expires = now.shift(minutes=1)
        task_name = "send_and_generate_track_to_consumer"
        logger.info("Creating periodic task for surveillance monitoring tracks, it expires at %s" % expires)
        try:
            p_task = surveillance_monitoring_job.schedule_every(
                task_name=task_name,
                period="seconds",
                every=every,
                expires=expires.isoformat(),
                session_id=surveillance_session_id,
                flight_declaration=None,
            )
            p_task.start()
            return True
        except Exception as e:
            logger.debug(f"Error creating surveillance monitoring heartbeat periodic task: {e}")
            logger.error("Could not create surveillance monitoring heartbeat periodic task")
            return False

    def remove_track_monitoring_heartbeat_periodic_task(self, track_monitoring_heartbeat_task: TaskScheduler) -> None:
        track_monitoring_heartbeat_task.terminate()

    def remove_surveillance_monitoring_heartbeat_periodic_task(self, surveillance_monitoring_heartbeat_task: TaskScheduler) -> None:
        surveillance_monitoring_heartbeat_task.terminate()

    def delete_surveillance_session(self, surveillance_session_id: UUID | str) -> None:
        for task in TaskScheduler.objects.filter(session_id=str(surveillance_session_id)):
            task.terminate()
        SurveillanceSession.objects.filter(id=surveillance_session_id).delete()

    def update_sensor_health_status(self, sensor_id: str, new_status: str, recovery_type: Optional[str] = None) -> bool:
        from flight_blender.surveillance.custom_signals import surveillance_sensor_failure_signal

        try:
            sensor = SurveillanceSensor.objects.get(id=sensor_id)
        except SurveillanceSensor.DoesNotExist:
            logger.error(f"update_sensor_health_status: sensor {sensor_id} not found")
            return False

        health, created = SurveillanceSensorHealth.objects.get_or_create(sensor=sensor, defaults={"status": new_status})
        previous_status = health.status if not created else new_status

        if not created:
            if previous_status == new_status:
                return True
            health.status = new_status
            health.save(update_fields=["status", "updated_at"])

        SurveillanceSensortHealthTracking.objects.create(
            sensor=sensor,
            status=new_status,
            recovery_type=recovery_type,
        )

        surveillance_sensor_failure_signal.send(
            sender="update_sensor_health_status",
            sensor_id=sensor_id,
            previous_status=previous_status,
            new_status=new_status,
            recovery_type=recovery_type,
        )
        return True

    def record_heartbeat_event(self, surveillance_session_id: str, expected_at: datetime, delivered_on_time: bool) -> bool:
        try:
            session = SurveillanceSession.objects.get(id=surveillance_session_id)
            SurveillanceHeartbeatEvent.objects.create(
                session=session,
                expected_at=expected_at,
                delivered_on_time=delivered_on_time,
            )
            return True
        except SurveillanceSession.DoesNotExist:
            logger.error(f"record_heartbeat_event: session {surveillance_session_id} not found")
            return False
        except Exception as e:
            logger.error(f"record_heartbeat_event: {e}")
            return False

    def record_track_event(self, surveillance_session_id: str, expected_at: datetime, had_active_tracks: bool) -> bool:
        try:
            session = SurveillanceSession.objects.get(id=surveillance_session_id)
            SurveillanceTrackEvent.objects.create(
                session=session,
                expected_at=expected_at,
                had_active_tracks=had_active_tracks,
            )
            return True
        except SurveillanceSession.DoesNotExist:
            logger.error(f"record_track_event: session {surveillance_session_id} not found")
            return False
        except Exception as e:
            logger.error(f"record_track_event: {e}")
            return False
