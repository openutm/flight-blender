"""
Celery tasks for Remote ID operations.
"""

from loguru import logger

from flight_blender.tasks.celery_app import celery_app


@celery_app.task(name="submit_dss_subscription", bind=True, max_retries=3)
def submit_dss_subscription(self, subscription_db_id: str, view: str, end_datetime: str):
    """Create a RID subscription on the DSS and persist the resulting subscription_id."""
    try:
        import uuid
        import requests
        from sqlalchemy.orm import Session
        from flight_blender.auth.dss import get_dss_auth_header
        from flight_blender.common.sync_engine import get_sync_engine
        from flight_blender.config import get_settings
        from flight_blender.models.rid import ISASubscription

        settings = get_settings()
        engine = get_sync_engine(settings.database_url)

        headers = get_dss_auth_header(audience=settings.dss_self_audience, token_type="rid")

        dss_base_url = settings.dss_base_url
        sub_id = str(uuid.uuid4())
        # Parse bounding box: lat_lo,lng_lo,lat_hi,lng_hi
        coords = [float(c) for c in view.split(",")]
        body = {
            "extents": {
                "volume": {
                    "outline_polygon": {
                        "vertices": [
                            {"lat": coords[0], "lng": coords[1]},
                            {"lat": coords[2], "lng": coords[1]},
                            {"lat": coords[2], "lng": coords[3]},
                            {"lat": coords[0], "lng": coords[3]},
                        ]
                    },
                    "altitude_lower": {"value": 0, "reference": "W84", "units": "M"},
                    "altitude_upper": {"value": 3000, "reference": "W84", "units": "M"},
                },
                "time_end": {"value": end_datetime, "format": "RFC3339"},
            },
            "uss_base_url": settings.dss_self_audience,
        }

        resp = requests.put(
            f"{dss_base_url}/dss/v1/identification_service_areas/{sub_id}",
            headers=headers,
            json=body,
            timeout=30,
        )

        with Session(engine) as session:
            sub = session.get(ISASubscription, uuid.UUID(subscription_db_id))
            if sub and resp.status_code == 200:
                sub.subscription_id = sub_id
                session.commit()
                logger.info("RID subscription created: %s", sub_id)
            elif resp.status_code != 200:
                logger.error("DSS RID subscription failed: %s %s", resp.status_code, resp.text)

    except Exception as exc:
        logger.error("RID subscription error: %s", exc)
        raise self.retry(exc=exc, countdown=10)


@celery_app.task(name="write_operator_rid_notification", bind=True)
def write_operator_rid_notification(self, session_id: str, message: str, flight_declaration_id: str | None = None):
    """Persist an operator RID notification."""
    try:
        import uuid
        from sqlalchemy.orm import Session
        from flight_blender.common.sync_engine import get_sync_engine
        from flight_blender.config import get_settings
        from flight_blender.models.notification import OperatorRIDNotification

        engine = get_sync_engine(get_settings().database_url)

        with Session(engine) as session:
            notif = OperatorRIDNotification(
                session_id=session_id,
                message=message,
                flight_declaration_id=uuid.UUID(flight_declaration_id) if flight_declaration_id else None,
            )
            session.add(notif)
            session.commit()
    except Exception as exc:
        logger.error("RID notification write error: %s", exc)


@celery_app.task(name="stream_rid_telemetry_data", bind=True, max_retries=3)
def stream_rid_telemetry_data(self, telemetry_payload: dict):
    """Parse and store RID telemetry data, then broadcast via Redis stream."""
    try:
        from flight_blender.common.redis_stream_operations import add_air_traffic_data

        add_air_traffic_data({"type": "rid_telemetry", **telemetry_payload})
        logger.info("RID telemetry streamed")
    except Exception as exc:
        logger.error("RID telemetry stream error: %s", exc)
        raise self.retry(exc=exc, countdown=2)
