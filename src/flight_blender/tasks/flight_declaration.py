"""
Celery tasks for flight declaration / DSS submission.
"""

from loguru import logger

from flight_blender.tasks.celery_app import celery_app


@celery_app.task(name="submit_flight_declaration_to_dss_async", bind=True, max_retries=3)
def submit_flight_declaration_to_dss_async(self, flight_declaration_id: str):
    """
    Submit a FlightDeclaration to the Discovery and Synchronisation Service (DSS).

    Validates start/end times, submits the operational intent, and updates the
    declaration state based on the DSS response.
    """
    import json
    import uuid
    from datetime import datetime, timezone

    from sqlalchemy.orm import Session

    from flight_blender.auth.dss import get_dss_auth_header
    from flight_blender.config import get_settings
    from flight_blender.models.flight_declaration import FlightDeclaration, FlightOperationalIntentReference
    from flight_blender.services.peer_uss_client import build_operational_intent_reference_payload

    from flight_blender.common.sync_engine import get_sync_engine

    settings = get_settings()
    engine = get_sync_engine(settings.database_url)

    with Session(engine) as session:
        decl = session.get(FlightDeclaration, uuid.UUID(flight_declaration_id))
        if not decl:
            logger.error("Flight declaration %s not found", flight_declaration_id)
            return

        # Validate start/end times are in the future
        now = datetime.now(tz=timezone.utc)
        end = decl.end_datetime if decl.end_datetime.tzinfo else decl.end_datetime.replace(tzinfo=timezone.utc)

        if end <= now:
            logger.error("Declaration %s end time is in the past", flight_declaration_id)
            _add_tracking(session, decl, "Time validation failed: end time is in the past")
            return

        logger.info("Submitting declaration %s to DSS", flight_declaration_id)

        try:
            dss_base_url = settings.dss_base_url
            headers = get_dss_auth_header(audience=settings.dss_self_audience, token_type="scd")

            import requests

            opint_id = str(uuid.uuid4())

            # Build non-empty extents from the operation's stored volumes (the
            # operational_intent JSON holds the list of Volume4Ds for op-intent
            # ingests). The airspace ``key`` requires a live DSS area query of
            # overlapping op-intent references — that round-trip is a documented
            # follow-up, so existing_references stays [] (key remains []).
            try:
                stored = json.loads(decl.operational_intent) if decl.operational_intent else []
            except (json.JSONDecodeError, TypeError):
                stored = []
            volumes = stored if isinstance(stored, list) else []

            body = build_operational_intent_reference_payload(
                volumes=volumes,
                state="Accepted",
                existing_references=[],
                uss_base_url=settings.dss_self_audience,
            )

            resp = requests.put(
                f"{dss_base_url}/dss/v1/operational_intent_references/{opint_id}",
                headers=headers,
                json=body,
                timeout=30,
            )

            if resp.status_code == 201:
                logger.info("Declaration %s submitted successfully", flight_declaration_id)
                decl.state = 1  # Accepted
                ref_data = resp.json().get("operational_intent_reference", {})
                ref = FlightOperationalIntentReference(
                    declaration_id=decl.id,
                    uss_availability="Unknown",
                    ovn=ref_data.get("ovn", ""),
                    manager=ref_data.get("manager", ""),
                    uss_base_url=ref_data.get("uss_base_url", ""),
                    version=str(ref_data.get("version", 1)),
                    state=ref_data.get("state", "Accepted"),
                    subscription_id=ref_data.get("subscription_id", ""),
                    is_live=True,
                )
                session.merge(ref)
                _add_tracking(session, decl, f"DSS submission successful: opint_id={opint_id}")
            else:
                logger.error("DSS error %s: %s", resp.status_code, resp.text)
                decl.state = 8  # Rejected
                _add_tracking(session, decl, f"DSS submission failed: {resp.status_code}")

            session.commit()

        except Exception as exc:
            logger.error("DSS submission error: %s", exc)
            raise self.retry(exc=exc, countdown=10)


def _add_tracking(session, decl, notes: str) -> None:
    import json

    from flight_blender.models.flight_declaration import FlightOperationTracking

    tracking = FlightOperationTracking(
        flight_declaration_id=decl.id,
        notes=notes,
        deltas=json.dumps({"original_state": str(decl.state), "new_state": str(decl.state)}),
    )
    session.add(tracking)


@celery_app.task(name="send_operational_update_message", bind=True)
def send_operational_update_message(self, flight_declaration_id: str, message_text: str, level: str = "info"):
    """
    Dispatch an AMQP / notification message about a flight declaration state change.
    """
    import json
    import os

    import pika

    amqp_url = os.getenv("AMQP_URL", "")
    if not amqp_url:
        # P2: without a broker the notification must not be silently dropped.
        # Persist it locally (mirroring the Django consumer's row creation) and
        # warn that the AMQP publish was skipped.
        logger.warning("No AMQP_URL configured; persisting notification locally for %s", flight_declaration_id)
        from flight_blender.tasks import notification as notification_tasks

        notification_tasks._persist_operator_rid_notification_sync(
            message=message_text,
            session_id=flight_declaration_id,
        )
        return

    try:
        params = pika.URLParameters(amqp_url)
        connection = pika.BlockingConnection(params)
        channel = connection.channel()
        channel.queue_declare(queue="flight_declaration_updates", durable=True)
        body = json.dumps(
            {
                "flight_declaration_id": flight_declaration_id,
                "message": message_text,
                "level": level,
            }
        )
        channel.basic_publish(
            exchange="",
            routing_key="flight_declaration_updates",
            body=body,
            properties=pika.BasicProperties(delivery_mode=2),
        )
        connection.close()
        logger.info("Notification sent for declaration %s", flight_declaration_id)
    except Exception as exc:
        logger.error("Notification dispatch error: %s", exc)
