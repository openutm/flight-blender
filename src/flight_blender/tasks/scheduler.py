"""Celery task scheduling without django_celery_beat.

Uses apply_async with countdown-based self-rescheduling tasks.
Redis stop-signal keys are used for session cancellation.
"""

import uuid

import arrow
from loguru import logger

from flight_blender.config import settings


class TaskSchedulerService:
    """
    Stateless factory for scheduling periodic Celery tasks.
    Uses apply_async(countdown=N) instead of django_celery_beat PeriodicTask rows.
    """

    @staticmethod
    def schedule_conformance_check(flight_declaration_id: str, session_id: str, expires: str) -> bool:
        from flight_blender.tasks.conformance_task import check_flight_conformance  # Lazy import — break circular

        every = settings.HEARTBEAT_RATE_SECS
        logger.info("TaskSchedulerService: scheduling conformance check, expires at %s" % expires)
        try:
            check_flight_conformance.apply_async(
                args=[flight_declaration_id, session_id],
                kwargs={"expires_iso": expires},
                countdown=every,
            )
            return True
        except Exception as e:
            logger.error("TaskSchedulerService: could not schedule conformance check: %s" % e)
            return False

    @staticmethod
    def schedule_rid_stream_monitoring(session_id: str, end_datetime: str) -> bool:
        from flight_blender.tasks.conformance_task import check_rid_stream_conformance  # Lazy import — break circular

        every = settings.HEARTBEAT_RATE_SECS
        try:
            check_rid_stream_conformance.apply_async(
                args=[session_id],
                kwargs={"expires_iso": end_datetime},
                countdown=every,
            )
            return True
        except Exception as e:
            logger.error("TaskSchedulerService: could not create RID stream observation task: %s" % e)
            return False

    @staticmethod
    def schedule_surveillance_heartbeat(surveillance_session_id: str) -> bool:
        from flight_blender.tasks.surveillance_task import send_heartbeat_to_consumer

        session_id = surveillance_session_id if surveillance_session_id else str(uuid.uuid4())
        expires = arrow.now().shift(minutes=1).isoformat()
        logger.info("TaskSchedulerService: scheduling surveillance heartbeat, expires at %s" % expires)
        try:
            send_heartbeat_to_consumer.apply_async(
                args=[session_id],
                kwargs={"expires_iso": expires},
                countdown=1,
            )
            return True
        except Exception as e:
            logger.error("TaskSchedulerService: could not create surveillance heartbeat task: %s" % e)
            return False

    @staticmethod
    def schedule_surveillance_track(surveillance_session_id: str) -> bool:
        from flight_blender.tasks.surveillance_task import send_and_generate_track_to_consumer

        session_id = surveillance_session_id if surveillance_session_id else str(uuid.uuid4())
        expires = arrow.now().shift(minutes=1).isoformat()
        logger.info("TaskSchedulerService: scheduling surveillance track task, expires at %s" % expires)
        try:
            send_and_generate_track_to_consumer.apply_async(
                args=[session_id],
                kwargs={"expires_iso": expires},
                countdown=1,
            )
            return True
        except Exception as e:
            logger.error("TaskSchedulerService: could not create surveillance track task: %s" % e)
            return False

    @staticmethod
    def cancel_session_tasks(session_id: str) -> None:
        """Signal running tasks for session_id to stop via Redis stop-signal key."""
        from flight_blender.auth.token_cache import get_redis

        r = get_redis()
        r.set(f"stop_task_{session_id}", "1", ex=300)
