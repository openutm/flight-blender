import logging

import arrow
from dotenv import find_dotenv, load_dotenv
from celery import shared_task
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import json
from .data_definitions import HeartbeatMessage
from dataclasses import asdict

logger = logging.getLogger("django")

load_dotenv(find_dotenv())


@shared_task
def send_sample_data_to_track_consumer():
    channel_layer = get_channel_layer()
    sample_data = {
        "track_id": "sample_123",
        "latitude": 37.7749,
        "longitude": -122.4194,
        "altitude": 10000,
        "timestamp": "2023-10-01T12:00:00Z",
    }
    async_to_sync(channel_layer.group_send)(
        "track_group", {"type": "track.message", "data": json.dumps(sample_data)}
    )


@shared_task
def send_heartbeat_to_consumer():
    channel_layer = get_channel_layer()
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
