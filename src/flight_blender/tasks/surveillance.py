"""
Celery tasks for surveillance monitoring (heartbeat and track dispatch).
"""

from datetime import datetime, timezone
from typing import Any

from loguru import logger

from flight_blender.tasks.celery_app import celery_app

# SDSP SLA thresholds (mirrors the Django surveillance heartbeat computation).
# Latency is derived from observation timestamps vs ``now``; accuracy from the
# reported horizontal / vertical accuracy of the observations in the stream.
SLA_LATENCY_THRESHOLD_MS = 1000.0
SLA_ACCURACY_THRESHOLD_M = 10.0


def _percentile(values: list[float], pct: float) -> float | None:
    """Return the ``pct`` (0-100) percentile of *values* using nearest-rank.

    Returns ``None`` for an empty input. Single-value inputs return that value.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = max(1, min(len(ordered), round(pct / 100.0 * len(ordered))))
    return ordered[rank - 1]


def _coerce_float(value: Any) -> float | None:
    """Best-effort float coercion; returns ``None`` on failure."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _observation_latency_ms(observation: dict, now: datetime) -> float | None:
    """Latency (ms) of one observation = ``now`` minus its timestamp.

    Tolerates a handful of timestamp encodings (ISO-8601 string, epoch seconds,
    or an already-parsed ``datetime``). Returns ``None`` when not parsable.
    """
    ts = observation.get("timestamp") or observation.get("time_stamp")
    if ts is None:
        return None
    parsed: datetime | None = None
    if isinstance(ts, datetime):
        parsed = ts
    elif isinstance(ts, (int, float)):
        parsed = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    elif isinstance(ts, str):
        try:
            parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            epoch = _coerce_float(ts)
            if epoch is not None:
                parsed = datetime.fromtimestamp(epoch, tz=timezone.utc)
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta_ms = (now - parsed).total_seconds() * 1000.0
    return max(0.0, delta_ms)


def _observation_accuracy_m(observation: dict) -> float | None:
    """Worst (largest) of the horizontal / vertical accuracy of an observation."""
    candidates = [
        _coerce_float(observation.get("horizontal_accuracy_m")),
        _coerce_float(observation.get("vertical_accuracy_m")),
        _coerce_float(observation.get("horizontal_accuracy")),
        _coerce_float(observation.get("vertical_accuracy")),
    ]
    present = [c for c in candidates if c is not None]
    return max(present) if present else None


def compute_sdsp_heartbeat(observations: list[dict], now: datetime | None = None) -> dict:
    """Derive SDSP heartbeat SLA metrics from the live observation stream.

    Replaces the previously hard-coded "always healthy" heartbeat values. The
    metrics mirror the Django surveillance heartbeat:

    * ``average_latency_or_95_percentile_latency_ms`` — 95th-percentile latency
      across observations (``now`` minus each observation timestamp).
    * ``horizontal_or_vertical_95_percentile_accuracy_m`` — 95th-percentile of
      the worst-axis accuracy across observations.
    * ``meets_sla_surveillance_requirements`` — True only when there is data and
      both latency and accuracy are within their SLA thresholds.
    * ``meets_sla_rr_lr_requirements`` — True only when there is fresh data
      (latency within the threshold).

    An empty stream is reported as degraded (both SLA booleans ``False`` and
    ``None`` latency/accuracy) rather than falsely healthy.
    """
    from flight_blender.config import get_settings

    settings = get_settings()
    now = now or datetime.now(tz=timezone.utc)

    latencies = [v for v in (_observation_latency_ms(o, now) for o in observations) if v is not None]
    accuracies = [v for v in (_observation_accuracy_m(o) for o in observations) if v is not None]

    latency_p95 = _percentile(latencies, 95.0)
    accuracy_p95 = _percentile(accuracies, 95.0)

    has_data = bool(observations) and latency_p95 is not None
    latency_ok = latency_p95 is not None and latency_p95 <= SLA_LATENCY_THRESHOLD_MS
    accuracy_ok = accuracy_p95 is None or accuracy_p95 <= SLA_ACCURACY_THRESHOLD_M

    meets_surveillance_sla = bool(has_data and latency_ok and accuracy_ok)
    meets_rr_lr_sla = bool(has_data and latency_ok)

    return {
        "surveillance_sdsp_name": settings.surveillance_sdsp_name,
        "meets_sla_surveillance_requirements": meets_surveillance_sla,
        "meets_sla_rr_lr_requirements": meets_rr_lr_sla,
        "average_latency_or_95_percentile_latency_ms": (round(latency_p95, 3) if latency_p95 is not None else None),
        "horizontal_or_vertical_95_percentile_accuracy_m": (round(accuracy_p95, 3) if accuracy_p95 is not None else None),
        "timestamp": now.isoformat(),
    }


def _get_sync_engine():
    from flight_blender.common.sync_engine import get_sync_engine
    from flight_blender.config import get_settings

    return get_sync_engine(get_settings().database_url)


@celery_app.task(name="send_heartbeat_to_consumer", bind=True)
def send_heartbeat_to_consumer(self, session_id: str):
    """
    Dispatch a 1Hz heartbeat for the given session.
    Records timing accuracy and broadcasts via WebSocket channel layer.
    """
    try:
        import uuid

        from sqlalchemy.orm import Session

        from flight_blender.models.surveillance import SurveillanceHeartbeatEvent, SurveillanceSession

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
        import uuid
        from datetime import datetime, timezone

        from sqlalchemy.orm import Session

        from flight_blender.models.surveillance import SurveillanceSession, SurveillanceTrackEvent

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
        from datetime import timedelta

        from sqlalchemy import create_engine, delete
        from sqlalchemy.orm import Session

        from flight_blender.config import get_settings
        from flight_blender.models.surveillance import SurveillanceHeartbeatEvent

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
