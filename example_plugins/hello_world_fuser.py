"""Example: latest-observation traffic data fuser plugin.

A traffic data fuser that groups raw observations by aircraft ICAO
address, keeps only the most recent observation per aircraft, and
emits one ``TrackMessage`` per active aircraft.  This is the simplest
useful fusion strategy — it de-duplicates multiple reports for the
same aircraft and always presents the newest position.

To activate, set the environment variable::

    FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER=example_plugins.hello_world_fuser.HelloWorldFuser

See PLUGINS.md for the full guide.
"""

from typing import List

import arrow
from loguru import logger

from flight_feed_operations.data_definitions import SingleAirtrafficObservation
from surveillance_monitoring_operations.data_definitions import (
    AircraftPosition,
    AircraftState,
    SpeedAccuracy,
    TrackMessage,
)

# Observations older than this many seconds are considered stale.
_STALE_THRESHOLD_SECS = 60


class HelloWorldFuser:
    """Latest-observation traffic data fuser.

    Groups raw observations by ICAO address, discards stale data,
    and emits one ``TrackMessage`` per aircraft using the most recent
    observation.
    """

    def __init__(self, session_id: str, raw_observations: List[SingleAirtrafficObservation]):
        self.session_id = session_id
        self.raw_observations = raw_observations

    def generate_track_messages(self) -> list[TrackMessage]:
        now_ts = arrow.utcnow().int_timestamp
        cutoff_ts = now_ts - _STALE_THRESHOLD_SECS

        # Group observations by aircraft, keeping only the latest per ICAO.
        latest_by_icao: dict[str, SingleAirtrafficObservation] = {}
        stale_count = 0
        for obs in self.raw_observations:
            if obs.timestamp < cutoff_ts:
                stale_count += 1
                continue  # discard stale observations
            existing = latest_by_icao.get(obs.icao_address)
            if existing is None or obs.timestamp > existing.timestamp:
                latest_by_icao[obs.icao_address] = obs

        duplicate_count = len(self.raw_observations) - stale_count - len(latest_by_icao)
        logger.info(
            "Session %s: %d raw observations → %d active aircraft (dropped %d stale, %d duplicates)",
            self.session_id,
            len(self.raw_observations),
            len(latest_by_icao),
            stale_count,
            duplicate_count,
        )

        track_messages: list[TrackMessage] = []
        for icao_address, obs in latest_by_icao.items():
            position = AircraftPosition(
                lat=obs.lat_dd,
                lng=obs.lon_dd,
                alt=obs.altitude_mm,
                accuracy_h="HAUnknown",
                accuracy_v="VAUnknown",
                extrapolated=False,
                pressure_altitude=None,
            )
            state = AircraftState(
                position=position,
                speed_accuracy=SpeedAccuracy.SAUnknown,
            )
            track_messages.append(
                TrackMessage(
                    sdsdp_identifier="EXAMPLE_SDSP",
                    unique_aircraft_identifier=icao_address,
                    state=state,
                    timestamp=arrow.get(obs.timestamp).isoformat(),
                    source="hello_world_fuser",
                    track_state="active",
                )
            )

        return track_messages
