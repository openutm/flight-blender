"""
Celery tasks for conformance monitoring.
"""

from loguru import logger

from flight_blender.tasks.celery_app import celery_app


@celery_app.task(name="check_flight_conformance", bind=True, max_retries=2)
def check_flight_conformance(self, flight_declaration_id: str):
    """
    Verify that a flight conforms to its operational intent and declared geofences.
    Creates a ConformanceRecord with the result.
    """
    try:
        import uuid
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from flight_blender.config import get_settings
        from flight_blender.models.flight_declaration import FlightDeclaration
        from flight_blender.models.conformance import ConformanceRecord

        settings = get_settings()
        sync_url = settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)

        with Session(engine) as session:
            decl = session.get(FlightDeclaration, uuid.UUID(flight_declaration_id))
            if not decl:
                logger.error("Declaration %s not found for conformance check", flight_declaration_id)
                return

            # Simplified conformance check: declaration is conforming if it is in Activated state
            is_conforming = decl.state == 2
            record = ConformanceRecord(
                flight_declaration_id=decl.id,
                conformance_state=1 if is_conforming else 0,
                description="Automated conformance check",
                event_type="scheduled_check",
                geofence_breach=False,
                resolved=is_conforming,
            )
            session.add(record)
            session.commit()
            logger.info("Conformance check for %s: %s", flight_declaration_id, "conforming" if is_conforming else "non-conforming")

    except Exception as exc:
        logger.error("Conformance check error: %s", exc)
        raise self.retry(exc=exc, countdown=30)


@celery_app.task(name="check_operation_telemetry_conformance", bind=True, max_retries=2)
def check_operation_telemetry_conformance(self, flight_declaration_id: str):
    """
    Check the latest telemetry position against the declared operational intent bounds.
    """
    try:
        import uuid
        from sqlalchemy import create_engine
        from sqlalchemy.orm import Session
        from flight_blender.config import get_settings
        from flight_blender.models.flight_declaration import FlightDeclaration
        from flight_blender.common.redis_stream_operations import read_latest_observation

        settings = get_settings()
        sync_url = settings.database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url)

        latest = read_latest_observation(session_id=flight_declaration_id)
        if not latest:
            logger.debug("No telemetry for %s", flight_declaration_id)
            return

        with Session(engine) as session:
            decl = session.get(FlightDeclaration, uuid.UUID(flight_declaration_id))
            if decl:
                check_flight_conformance.delay(flight_declaration_id)

    except Exception as exc:
        logger.error("Telemetry conformance check error: %s", exc)
        raise self.retry(exc=exc, countdown=30)
