import json
import logging
from dataclasses import asdict

import arrow
from asgiref.sync import async_to_sync
from channels_redis.core import RedisChannelLayer
from dotenv import find_dotenv, load_dotenv

from common.redis_stream_operations import RedisStreamOperations
from flight_blender.celery import app
from flight_blender.settings import BROKER_URL

from .data_definitions import (
    AircraftPosition,
    AircraftState,
    HeartbeatMessage,
    SpeedAccuracy,
    TrackMessage,
)

logger = logging.getLogger("django")

load_dotenv(find_dotenv())


# Generate unique aircraft positions
def generate_unique_aircraft_position(session_id: str, unique_aircraft_identifier: str) -> AircraftPosition:
    # This method would contain logic to generate unique positions based on session_id and unique_aircraft_identifier since the last time it was processed. This needs to be implemented for your deployment.
    stream_ops = RedisStreamOperations()
    consumer_id = stream_ops.create_consumer_reader()
    observations = stream_ops.read_latest_air_traffic_data(stream_name="air_traffic_stream", consumer_id=consumer_id, count=20)
    for obs in observations:
        print(f"Aircraft {obs.icao_address} at {obs.lat_dd}, {obs.lon_dd}")

    return AircraftPosition(
        lat=37.7749,
        lng=-122.4194,
        alt=10000,
        accuracy_h="SA1mps",
        accuracy_v="SA3mps",
        extrapolated=False,
        pressure_altitude=10050,
    )


@app.task(name="send_track_to_consumer")
def send_track_to_consumer(session_id: str, flight_declaration_id: str = None) -> None:
    channel_layer = RedisChannelLayer(hosts=[BROKER_URL])
    logger.info(f"Preparing to send sample data for session_id: {session_id}")
    UNIQUE_AIRCRAFT_IDENTIFIER = "UA123"
    position = generate_unique_aircraft_position(session_id=session_id, unique_aircraft_identifier=UNIQUE_AIRCRAFT_IDENTIFIER)
    speed_accuracy = SpeedAccuracy("SA1mps")
    aircraft_state = AircraftState(
        position=position,  # Fill with appropriate AircraftPosition object
        speed=250,
        track=90,
        vertical_speed=5,
        speed_accuracy=speed_accuracy,  # Fill with appropriate SpeedAccuracy object
    )
    track_data = TrackMessage(
        sdsdp_identifier="SDSP123",
        unique_aircraft_identifier=UNIQUE_AIRCRAFT_IDENTIFIER,
        state=aircraft_state,  # Fill with appropriate AircraftState object
        timestamp=arrow.utcnow().isoformat(),
        source="TestSource",
        track_state="Active",
    )
    logger.debug("Sending track data:", asdict(track_data))
    async_to_sync(channel_layer.group_send)("track_group", {"type": "track.message", "data": asdict(track_data)})


@app.task(name="send_heartbeat_to_consumer")
def send_heartbeat_to_consumer(session_id: str, flight_declaration_id: str = None) -> None:
    channel_layer = RedisChannelLayer(hosts=[BROKER_URL])
    logger.info(f"Preparing to send heartbeat for session_id: {session_id}")
    heartbeat_data = HeartbeatMessage(
        surveillance_sdsp_name="heartbeat_123",
        meets_sla_surveillance_requirements=True,
        meets_sla_rr_lr_requirements=True,
        average_latenccy_or_95_percentile_latency_ms=150,
        horizontal_or_vertical_95_percentile_accuracy_m=5,
        timestamp=arrow.utcnow().isoformat(),
    )
    logger.debug("Sending heartbeat data:", asdict(heartbeat_data))
    async_to_sync(channel_layer.group_send)(
        "heartbeat_" + session_id,  # Assuming the group name for HeartBeatConsumer
        {"type": "heartbeat.message", "data": asdict(heartbeat_data)},
    )
