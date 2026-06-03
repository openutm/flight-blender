import os
import uuid

import arrow
from loguru import logger


class TaskSchedulerService:
    """
    Isolates all django_celery_beat / TaskScheduler coupling behind a stateless facade.
    Instantiate fresh each call — never hold as a singleton.
    """

    @staticmethod
    def schedule_conformance_check(flight_declaration_id: str, session_id: str, expires: str) -> bool:
        from flight_blender.conformance.models import TaskScheduler
        from flight_blender.flight_declarations.models import FlightDeclaration

        try:
            flight_declaration = FlightDeclaration.objects.get(id=flight_declaration_id)
        except FlightDeclaration.DoesNotExist:
            logger.error(f"TaskSchedulerService: flight declaration {flight_declaration_id} not found")
            return False

        conformance_monitoring_job = TaskScheduler()
        every = int(os.getenv("HEARTBEAT_RATE_SECS", default=5))
        task_name = "check_flight_conformance"
        logger.info("TaskSchedulerService: scheduling conformance check, expires at %s" % expires)
        try:
            p_task = conformance_monitoring_job.schedule_every(
                task_name=task_name,
                period="seconds",
                every=every,
                expires=expires,
                flight_declaration=flight_declaration,
                session_id=session_id,
            )
            p_task.start()
            return True
        except Exception as e:
            logger.debug(f"TaskSchedulerService: error scheduling conformance check: {e}")
            logger.error("TaskSchedulerService: could not create periodic task")
            return False

    @staticmethod
    def schedule_rid_stream_monitoring(session_id: str, end_datetime: str) -> bool:
        from flight_blender.conformance.models import TaskScheduler

        rid_stream_monitoring_job = TaskScheduler()
        every = int(os.getenv("HEARTBEAT_RATE_SECS", default=5))
        now = arrow.now()
        stream_end = arrow.get(end_datetime)
        delta = stream_end - now
        expires = now.shift(seconds=delta.total_seconds())
        task_name = "check_rid_stream_conformance"
        try:
            p_task = rid_stream_monitoring_job.schedule_every(
                task_name=task_name,
                period="seconds",
                every=every,
                expires=expires.isoformat(),
                session_id=session_id,
                flight_declaration=None,
            )
            p_task.start()
            return True
        except Exception as e:
            logger.error("TaskSchedulerService: could not create RID stream observation task: %s" % e)
            return False

    @staticmethod
    def schedule_surveillance_heartbeat(surveillance_session_id: str) -> bool:
        from flight_blender.conformance.models import TaskScheduler

        surveillance_monitoring_job = TaskScheduler()
        every = 1
        now = arrow.now()
        session_id = surveillance_session_id if surveillance_session_id else str(uuid.uuid4())
        expires = now.shift(minutes=1)
        task_name = "send_heartbeat_to_consumer"
        logger.info("TaskSchedulerService: scheduling surveillance heartbeat, expires at %s" % expires)
        try:
            p_task = surveillance_monitoring_job.schedule_every(
                task_name=task_name,
                period="seconds",
                every=every,
                expires=expires.isoformat(),
                session_id=session_id,
                flight_declaration=None,
            )
            p_task.start()
            return True
        except Exception as e:
            logger.debug(f"TaskSchedulerService: {e}")
            logger.error("TaskSchedulerService: could not create surveillance heartbeat task")
            return False

    @staticmethod
    def schedule_surveillance_track(surveillance_session_id: str) -> bool:
        from flight_blender.conformance.models import TaskScheduler

        surveillance_monitoring_job = TaskScheduler()
        every = 1
        now = arrow.now()
        session_id = surveillance_session_id if surveillance_session_id else str(uuid.uuid4())
        expires = now.shift(minutes=1)
        task_name = "send_and_generate_track_to_consumer"
        logger.info("TaskSchedulerService: scheduling surveillance track task, expires at %s" % expires)
        try:
            p_task = surveillance_monitoring_job.schedule_every(
                task_name=task_name,
                period="seconds",
                every=every,
                expires=expires.isoformat(),
                session_id=session_id,
                flight_declaration=None,
            )
            p_task.start()
            return True
        except Exception as e:
            logger.debug(f"TaskSchedulerService: {e}")
            logger.error("TaskSchedulerService: could not create surveillance track task")
            return False

    @staticmethod
    def cancel_task(task) -> None:
        task.terminate()

    @staticmethod
    def cancel_session_tasks(session_id: str) -> None:
        from flight_blender.conformance.models import TaskScheduler

        for task in TaskScheduler.objects.filter(session_id=str(session_id)):
            task.terminate()
