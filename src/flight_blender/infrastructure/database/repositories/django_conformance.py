import os
import uuid
from datetime import datetime
from typing import Optional

import arrow
from django.db.utils import IntegrityError
from loguru import logger

from flight_blender.conformance.models import ConformanceRecord, TaskScheduler
from flight_blender.flight_declarations.models import FlightDeclaration
from flight_blender.geo_fence.models import GeoFence


class DjangoConformanceRepository:
    def get_conformance_records_for_duration(self, start_time: datetime, end_time: datetime):
        try:
            return ConformanceRecord.objects.filter(created_at__gte=start_time, created_at__lte=end_time).order_by("-created_at")
        except ConformanceRecord.DoesNotExist:
            return None

    def get_conformance_record_by_flight_declaration(self, flight_declaration: FlightDeclaration):
        try:
            return ConformanceRecord.objects.filter(flight_declaration=flight_declaration)
        except ConformanceRecord.DoesNotExist:
            return None

    def get_conformance_monitoring_task(self, flight_declaration: FlightDeclaration) -> Optional[TaskScheduler]:
        try:
            return TaskScheduler.objects.get(flight_declaration=flight_declaration)
        except TaskScheduler.DoesNotExist:
            return None

    def write_flight_conformance_record(
        self,
        flight_declaration: FlightDeclaration,
        conformance_non_conformance_state: int,
        description: str,
        event_type: str,
        geofence_breach: bool,
        resolved: bool,
        geofence: Optional[GeoFence],
    ) -> Optional[ConformanceRecord]:
        try:
            conformance_record = ConformanceRecord(
                flight_declaration=flight_declaration,
                conformance_state=conformance_non_conformance_state,
                description=description,
                event_type=event_type,
                geofence_breach=geofence_breach,
                geofence=geofence,
                resolved=resolved,
            )
            conformance_record.save()
            return conformance_record
        except IntegrityError:
            return None

    def create_conformance_monitoring_periodic_task(self, flight_declaration: FlightDeclaration) -> bool:
        conformance_monitoring_job = TaskScheduler()
        every = int(os.getenv("HEARTBEAT_RATE_SECS", default=5))
        now = arrow.now()
        session_id = str(uuid.uuid4())
        fd_end = arrow.get(flight_declaration.end_datetime)
        delta = fd_end - now
        delta_seconds = delta.total_seconds()
        expires = now.shift(seconds=delta_seconds)
        task_name = "check_flight_conformance"
        logger.info("Creating periodic task for conformance monitoring expires at %s" % expires)
        try:
            p_task = conformance_monitoring_job.schedule_every(
                task_name=task_name,
                period="seconds",
                every=every,
                expires=expires.isoformat(),
                flight_declaration=flight_declaration,
                session_id=session_id,
            )
            p_task.start()
            return True
        except Exception as e:
            logger.debug(f"Error creating conformance monitoring periodic task: {e}")
            logger.error("Could not create periodic task")
            return False

    def remove_conformance_monitoring_periodic_task(self, conformance_monitoring_task: TaskScheduler) -> None:
        conformance_monitoring_task.terminate()
