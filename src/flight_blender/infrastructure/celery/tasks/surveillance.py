import json
import uuid
from dataclasses import asdict

import arrow
import redis
from loguru import logger

from flight_blender.celery import app
from flight_blender.config import settings
from flight_blender.core.entities.surveillance import HeartbeatMessage
from flight_blender.core.repositories.surveillance import TrafficDataFuser as TrafficDataFuserProtocol
from flight_blender.infrastructure.database.repositories.sa_surveillance import SQLAlchemySurveillanceSyncRepository
from flight_blender.infrastructure.database.session import session_scope
from flight_blender.infrastructure.redis.stream_operations import RedisStreamOperations
from flight_blender.plugins.loader import load_plugin

BROKER_URL = settings.REDIS_BROKER_URL
FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER = settings.FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER

# A heartbeat is considered on-time if the dispatch succeeds within this many seconds
# of the scheduled tick (accommodates Celery scheduling jitter).
_MAX_ACCEPTABLE_LATENCY_SECS = settings.HEARTBEAT_MAX_LATENCY_SECS


def _publish_realtime_message(channel_name: str, payload: object) -> None:
    redis_client = redis.from_url(BROKER_URL, decode_responses=True)
    try:
        redis_client.publish(channel_name, json.dumps(payload))
    finally:
        redis_client.close()


@app.task(name="send_and_generate_track_to_consumer")
def send_and_generate_track_to_consumer(session_id: str, flight_declaration_id: None | str = None, expires_iso: str | None = None) -> None:
    from flight_blender.infrastructure.auth.redis_helpers import get_redis

    r = get_redis()
    if r.exists(f"stop_task_{session_id}"):
        return
    if expires_iso and arrow.utcnow() > arrow.get(expires_iso):
        return

    surveillance_session_id = session_id

    expected_at = arrow.utcnow().datetime

    stream_ops = RedisStreamOperations()
    consumer_id = stream_ops.create_consumer_reader()
    raw_observations = stream_ops.read_latest_air_traffic_data(stream_name="air_traffic_stream", consumer_id=consumer_id, count=20)
    logger.info(f"Received {len(raw_observations)} observations for surveillance session id: {surveillance_session_id}")

    FuserClass = load_plugin(FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER, expected_protocol=TrafficDataFuserProtocol)

    traffic_data_fuser = FuserClass(
        session_id=surveillance_session_id,
        raw_observations=raw_observations,
        track_store=stream_ops,
    )
    track_messages = traffic_data_fuser.generate_track_messages()

    all_track_data = []
    for track_message in track_messages:
        all_track_data.append(asdict(track_message))
        logger.debug(f"Fused track message: {asdict(track_message)}")

    _publish_realtime_message(f"track_{surveillance_session_id}", all_track_data)

    with session_scope() as db:
        repo = SQLAlchemySurveillanceSyncRepository(db)
        repo.record_track_event(
            session_id=uuid.UUID(surveillance_session_id),
            expected_at=expected_at,
            had_active_tracks=len(track_messages) > 0,
        )

    send_and_generate_track_to_consumer.apply_async(
        args=[session_id, flight_declaration_id],
        kwargs={"expires_iso": expires_iso},
        countdown=1,
    )


@app.task(name="send_heartbeat_to_consumer")
def send_heartbeat_to_consumer(session_id: str, flight_declaration_id: None | str = None, expires_iso: str | None = None) -> None:
    from flight_blender.infrastructure.auth.redis_helpers import get_redis

    r = get_redis()
    if r.exists(f"stop_task_{session_id}"):
        return
    if expires_iso and arrow.utcnow() > arrow.get(expires_iso):
        return

    surveillance_session_id = session_id

    logger.info(f"Preparing to send heartbeat for surveillance session with id: {surveillance_session_id}")

    expected_at = arrow.utcnow().datetime

    avg_latency_ms = 0
    h_accuracy_m = 0
    with session_scope() as db:
        repo = SQLAlchemySurveillanceSyncRepository(db)
        active_sensors = repo.get_active_surveillance_sensors()
        if active_sensors:
            primary_sensor = active_sensors[0]
            avg_latency_ms = primary_sensor.expected_latency_ms
            h_accuracy_m = int(primary_sensor.horizontal_accuracy_m)

    heartbeat_data = HeartbeatMessage(
        surveillance_sdsp_name=surveillance_session_id,
        meets_sla_surveillance_requirements=True,
        meets_sla_rr_lr_requirements=True,
        average_latency_or_95_percentile_latency_ms=avg_latency_ms,
        horizontal_or_vertical_95_percentile_accuracy_m=h_accuracy_m,
        timestamp=arrow.utcnow().isoformat(),
    )

    logger.debug(f"Sending heartbeat data: {asdict(heartbeat_data)}")
    dispatch_succeeded = True
    try:
        _publish_realtime_message(f"heartbeat_{surveillance_session_id}", asdict(heartbeat_data))
    except Exception as e:
        logger.error(f"Failed to send heartbeat for surveillance session {surveillance_session_id}: {e}")
        dispatch_succeeded = False

    dispatch_at = arrow.utcnow().datetime
    latency_secs = abs((dispatch_at - expected_at).total_seconds())
    delivered_on_time = dispatch_succeeded and latency_secs <= _MAX_ACCEPTABLE_LATENCY_SECS

    with session_scope() as db:
        repo = SQLAlchemySurveillanceSyncRepository(db)
        repo.record_heartbeat_event(
            session_id=uuid.UUID(surveillance_session_id),
            expected_at=expected_at,
            delivered_on_time=delivered_on_time,
        )

    send_heartbeat_to_consumer.apply_async(
        args=[session_id, flight_declaration_id],
        kwargs={"expires_iso": expires_iso},
        countdown=1,
    )


@app.task(name="cleanup_old_heartbeat_events")
def cleanup_old_heartbeat_events() -> None:
    """
    Data retention task. Deletes SurveillanceHeartbeatEvent and SurveillanceTrackEvent
    records older than HEARTBEAT_RETENTION_DAYS (default: 30).
    At 1 Hz per session this prevents unbounded table growth.
    Schedule this task daily via django-celery-beat.
    """
    retention_days = settings.HEARTBEAT_RETENTION_DAYS
    cutoff = arrow.utcnow().shift(days=-retention_days).datetime

    with session_scope() as db:
        repo = SQLAlchemySurveillanceSyncRepository(db)
        deleted_heartbeats, deleted_tracks = repo.cleanup_old_events(cutoff=cutoff)

    logger.info(
        f"cleanup_old_heartbeat_events: deleted {deleted_heartbeats} heartbeat events "
        f"and {deleted_tracks} track events older than {retention_days} days"
    )
