import json
import logging
from dataclasses import asdict

import arrow
from asgiref.sync import async_to_sync
from channels_redis.core import RedisChannelLayer
from dotenv import find_dotenv, load_dotenv

from flight_blender.celery import app

from .data_definitions import HeartbeatMessage

logger = logging.getLogger("django")

load_dotenv(find_dotenv())


@app.task(name="send_sample_data_to_track_consumer")
def send_sample_data_to_track_consumer():
    channel_layer = RedisChannelLayer()
    sample_data = {
        "track_id": "sample_123",
        "latitude": 37.7749,
        "longitude": -122.4194,
        "altitude": 10000,
        "timestamp": "2023-10-01T12:00:00Z",
    }
    async_to_sync(channel_layer.group_send)("track_group", {"type": "track.message", "data": json.dumps(sample_data)})


@app.task(name="send_heartbeat_to_consumer")
def send_heartbeat_to_consumer():
    channel_layer = RedisChannelLayer()
    heartbeat_data = HeartbeatMessage(
        surveillance_sdsp_name="heartbeat_123",
        meets_sla_surveillance_requirements=True,
        meets_sla_rr_lr_requirements=True,
        average_latenccy_or_95_percentile_latency_ms=150,
        horizontal_or_vertical_95_percentile_accuracy_m=5,
        timestamp=arrow.utcnow().isoformat(),
    )
    async_to_sync(channel_layer.group_send)(
        "heartbeat_group",  # Assuming the group name for HeartBeatConsumer
        {"type": "heartbeat.message", "data": json.dumps(asdict(heartbeat_data))},
    )
