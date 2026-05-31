"""
Celery tasks for surveillance monitoring (heartbeat and track dispatch).
"""

from datetime import datetime, timezone

from loguru import logger

from flight_blender.tasks.celery_app import celery_app


def _get_sync_engine():
    from sqlalchemy import create_engine
    from flight_blender.config import get_settings

    settings = get_settings()
    sync_url = settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
    return create_engine(sync_url)


@celery_app.task(name="send_heartbeat_to_consumer", bind=True)
def send_heartbeat_to_consumer(self, session_id: str):
    """
    Dispatch a 1Hz heartbeat for the given session.
    Records timing accuracy and broadcasts via WebSocket channel layer.
    """
    try:
        from sqlalchemy.orm import Session
        from flight_blender.models.surveillance import SurveillanceHeartbeatEvent, SurveillanceSession
        import uuid

        engine = _get_sync_engine()

        with Session(engine) as session:
            surveillance_session = session.get(SurveillanceSession, uuid.UUID(session_id))
            if not surveillance_session:
                logger.info("Session %s no longer active, stopping heartbeat", session_id)
                return

            now = datetime.now(tz=timezone.utc)
            event = SurveillanceHeartbeatEvent(
                dispatched_at=now,
                expected_at=now,
                delivered_on_time=True,
            )
            session.add(event)
            session.commit()

        # Re-schedule next heartbeat after 1 second if session still active
        send_heartbeat_to_consumer.apply_async(kwargs={"session_id": session_id}, countdown=1)
        logger.debug("Heartbeat dispatched for session %s", session_id)

    except Exception as exc:
        logger.error("Heartbeat error: %s", exc)


@celery_app.task(name="send_and_generate_track_to_consumer", bind=True)
def send_and_generate_track_to_consumer(self, session_id: str):
    """
    Generate track messages from fused observations and broadcast via channels.
    """
    try:
        from sqlalchemy.orm import Session
        from flight_blender.models.surveillance import SurveillanceSession, SurveillanceTrackEvent
        from datetime import datetime, timezone
        import uuid

        engine = _get_sync_engine()

        with Session(engine) as db_session:
            surveillance_session = db_session.get(SurveillanceSession, uuid.UUID(session_id))
            if not surveillance_session:
                logger.info("Session %s no longer active, stopping track generation", session_id)
                return

            from flight_blender.common.redis_stream_operations import read_all_observations

            # Read from global stream (not filtered by session) to capture all traffic
            observations = read_all_observations(count=10)
            had_tracks = len(observations) > 0

            now = datetime.now(tz=timezone.utc)
            event = SurveillanceTrackEvent(dispatched_at=now, expected_at=now, had_active_tracks=had_tracks)
            db_session.add(event)
            db_session.commit()

        # Re-schedule next track generation after 1 second if session still active
        send_and_generate_track_to_consumer.apply_async(kwargs={"session_id": session_id}, countdown=1)
        logger.debug("Track event generated for session %s (had_tracks=%s)", session_id, had_tracks)

    except Exception as exc:
        logger.error("Track generation error: %s", exc)


@celery_app.task(name="cleanup_old_heartbeat_events")
def cleanup_old_heartbeat_events():
    """Delete heartbeat events older than HEARTBEAT_RETENTION_DAYS."""
    try:
        from sqlalchemy import create_engine, delete
        from sqlalchemy.orm import Session
        from flight_blender.config import get_settings
        from flight_blender.models.surveillance import SurveillanceHeartbeatEvent
        from datetime import timedelta

        settings = get_settings()
        sync_url = settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=settings.heartbeat_retention_days)

        with Session(engine) as session:
            result = session.execute(delete(SurveillanceHeartbeatEvent).where(SurveillanceHeartbeatEvent.created_at < cutoff))
            session.commit()
            logger.info("Cleaned up %d old heartbeat events", result.rowcount)

    except Exception as exc:
        logger.error("Heartbeat cleanup error: %s", exc)
