import os
import uuid
from dataclasses import asdict

import arrow
from asgiref.sync import async_to_sync
from channels_redis.core import RedisChannelLayer
from dotenv import find_dotenv, load_dotenv
from loguru import logger

from flight_blender.celery import app
from flight_blender.common.redis_stream_operations import RedisStreamOperations
from flight_blender.infrastructure.database.repositories.sa_surveillance import SQLAlchemySurveillanceSyncRepository
from flight_blender.infrastructure.database.session import session_scope
from flight_blender.plugins.loader import load_plugin
from flight_blender.settings import BROKER_URL, FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER
from flight_blender.surveillance.traffic_data_fuser_protocol import TrafficDataFuser as TrafficDataFuserProtocol

from .data_definitions import HeartbeatMessage

load_dotenv(find_dotenv())

# A heartbeat is considered on-time if the dispatch succeeds within this many seconds
# of the scheduled tick (accommodates Celery scheduling jitter).
_MAX_ACCEPTABLE_LATENCY_SECS = float(os.getenv("HEARTBEAT_MAX_LATENCY_SECS", "1.5"))


@app.task(name="send_and_generate_track_to_consumer")
def send_and_generate_track_to_consumer(session_id: str, flight_declaration_id: None | str = None) -> None:
    surveillance_session_id = session_id
    channel_layer = RedisChannelLayer(hosts=[BROKER_URL])

    expected_at = arrow.utcnow().datetime

    stream_ops = RedisStreamOperations()
    consumer_id = stream_ops.create_consumer_reader()
    raw_observations = stream_ops.read_latest_air_traffic_data(stream_name="air_traffic_stream", consumer_id=consumer_id, count=20)
    logger.info(f"Received {len(raw_observations)} observations for surveillance session id: {surveillance_session_id}")

    FuserClass = load_plugin(FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER, expected_protocol=TrafficDataFuserProtocol)

    traffic_data_fuser = FuserClass(session_id=surveillance_session_id, raw_observations=raw_observations)
    track_messages = traffic_data_fuser.generate_track_messages()

    all_track_data = []
    for track_message in track_messages:
        all_track_data.append(asdict(track_message))
        logger.debug(f"Fused track message: {asdict(track_message)}")

    async_to_sync(channel_layer.group_send)("track_" + surveillance_session_id, {"type": "track.message", "data": all_track_data})

    with session_scope() as db:
        repo = SQLAlchemySurveillanceSyncRepository(db)
        repo.record_track_event(
            session_id=uuid.UUID(surveillance_session_id),
            expected_at=expected_at,
            had_active_tracks=len(track_messages) > 0,
        )


@app.task(name="send_heartbeat_to_consumer")
def send_heartbeat_to_consumer(session_id: str, flight_declaration_id: None | str = None) -> None:
    surveillance_session_id = session_id
    channel_layer = RedisChannelLayer(hosts=[BROKER_URL])

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
        async_to_sync(channel_layer.group_send)(
            "heartbeat_" + surveillance_session_id,
            {"type": "heartbeat.message", "data": asdict(heartbeat_data)},
        )
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


@app.task(name="cleanup_old_heartbeat_events")
def cleanup_old_heartbeat_events() -> None:
    """
    Data retention task. Deletes SurveillanceHeartbeatEvent and SurveillanceTrackEvent
    records older than HEARTBEAT_RETENTION_DAYS (default: 30).
    At 1 Hz per session this prevents unbounded table growth.
    Schedule this task daily via django-celery-beat.
    """
    retention_days = int(os.getenv("HEARTBEAT_RETENTION_DAYS", "30"))
    cutoff = arrow.utcnow().shift(days=-retention_days).datetime

    with session_scope() as db:
        repo = SQLAlchemySurveillanceSyncRepository(db)
        deleted_heartbeats, deleted_tracks = repo.cleanup_old_events(cutoff=cutoff)

    logger.info(
        f"cleanup_old_heartbeat_events: deleted {deleted_heartbeats} heartbeat events "
        f"and {deleted_tracks} track events older than {retention_days} days"
    )
