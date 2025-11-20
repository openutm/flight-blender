import logging
from dataclasses import asdict
from importlib import import_module

import arrow
from asgiref.sync import async_to_sync
from channels_redis.core import RedisChannelLayer
from dotenv import find_dotenv, load_dotenv

from common.redis_stream_operations import RedisStreamOperations
from flight_blender.celery import app
from flight_blender.settings import ASTM_F3623_SDSP_CUSTOM_DATA_FUSER_CLASS, BROKER_URL

from .data_definitions import HeartbeatMessage

logger = logging.getLogger("django")

load_dotenv(find_dotenv())


@app.task(name="send_and_generate_track_to_consumer")
def send_and_generate_track_to_consumer(session_id: str, flight_declaration_id: None | str = None) -> None:
    channel_layer = RedisChannelLayer(hosts=[BROKER_URL])

    stream_ops = RedisStreamOperations()
    consumer_id = stream_ops.create_consumer_reader()
    # This is observations from all sensors (it may contain multiple observations for the same aircraft)
    raw_observations = stream_ops.read_latest_air_traffic_data(stream_name="air_traffic_stream", consumer_id=consumer_id, count=20)
    logger.info(f"Received {len(raw_observations)} observations for session_id: {session_id}")
    module_name, class_name = ASTM_F3623_SDSP_CUSTOM_DATA_FUSER_CLASS.rsplit(".", 1)
    module = import_module(module_name)
    TrafficDataFuser = getattr(module, class_name)

    traffic_data_fuser = TrafficDataFuser(session_id=session_id, raw_observations=raw_observations)

    traffic_data_fuser.fuse_raw_observations()
    traffic_data_fuser.generate_active_tracks()
    active_tracks = stream_ops.get_all_active_tracks_in_session(session_id=session_id)
    track_messages = traffic_data_fuser.generate_track_messages(active_tracks=active_tracks)

    for track_message in track_messages:
        logger.debug("Fused track message:", asdict(track_message))
        async_to_sync(channel_layer.group_send)("track_group", {"type": "track.message", "data": asdict(track_message)})


@app.task(name="send_heartbeat_to_consumer")
def send_heartbeat_to_consumer(session_id: str, flight_declaration_id: None | str = None) -> None:
    channel_layer = RedisChannelLayer(hosts=[BROKER_URL])
    logger.info(f"Preparing to send heartbeat for session_id: {session_id}")
    heartbeat_data = HeartbeatMessage(
        surveillance_sdsp_name="heartbeat_123",
        meets_sla_surveillance_requirements=True,
        meets_sla_rr_lr_requirements=True,
        average_latency_or_95_percentile_latency_ms=150,
        horizontal_or_vertical_95_percentile_accuracy_m=5,
        timestamp=arrow.utcnow().isoformat(),
    )
    logger.debug("Sending heartbeat data:", asdict(heartbeat_data))
    async_to_sync(channel_layer.group_send)(
        "heartbeat_" + session_id,  # Assuming the group name for HeartBeatConsumer
        {"type": "heartbeat.message", "data": asdict(heartbeat_data)},
    )
