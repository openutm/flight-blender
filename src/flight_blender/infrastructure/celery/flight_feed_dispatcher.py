import json

from flight_blender.infrastructure.celery.tasks.flight_feed import bulk_write_incoming_air_traffic_data, start_opensky_network_stream
from flight_blender.infrastructure.celery.tasks.rid import stream_rid_telemetry_data


class CeleryFlightFeedTaskDispatcher:
    def dispatch_observations(self, observations: list[dict]) -> None:
        for i in range(0, len(observations), 250):
            bulk_write_incoming_air_traffic_data.delay(json.dumps(observations[i : i + 250]))

    def start_opensky_network_stream(self, view_port: str, session_id: str) -> None:
        start_opensky_network_stream.delay(view_port=view_port, session_id=session_id)

    def stream_rid_telemetry_data(self, rid_telemetry_observations: str) -> None:
        stream_rid_telemetry_data.delay(rid_telemetry_observations=rid_telemetry_observations)
