from typing import List

import arrow

from flight_feed_operations.data_definitions import SingleAirtrafficObservation
from surveillance_monitoring_operations.data_definitions import (
    AircraftPosition,
    AircraftState,
    SpeedAccuracy,
    TrackMessage,
)


class TrafficDataFuser:
    """A default data fuser to generate track messages"""

    def __init__(self, session_id: str, raw_observations: List[SingleAirtrafficObservation]):
        self.raw_observations = raw_observations
        self.SDSP_IDENTIFIER = "SDSP123"

    def fuse_raw_observations(self) -> List[SingleAirtrafficObservation]:
        # No fusing is actually done, this just returns the data that is in the redis streams.
        return self.raw_observations

    def generate_active_tracks(self, session_id: str):
        # This method generates active tracks for a session ID and stores it in Redis. Active tracks are objects that are being tracked for a session
        raise NotImplementedError

    def generate_track_messages(self, fused_observations: List[SingleAirtrafficObservation]) -> List[TrackMessage]:
        all_track_data = []

        for fused_observation in fused_observations:
            UNIQUE_AIRCRAFT_IDENTIFIER = fused_observation.icao_address
            aircraft_position = AircraftPosition(
                lat=fused_observation.lat_dd,
                lng=-fused_observation.lon_dd,
                alt=fused_observation.altitude_mm,
                accuracy_h="SA1mps",
                accuracy_v="SA3mps",
                extrapolated=True,
                pressure_altitude=fused_observation.altitude_mm,
            )
            speed_accuracy = SpeedAccuracy("SA1mps")
            aircraft_state = AircraftState(
                position=aircraft_position,  # Fill with appropriate AircraftPosition object
                speed=250,
                track=90,
                vertical_speed=5,
                speed_accuracy=speed_accuracy,  # Fill with appropriate SpeedAccuracy object
            )
            track_data = TrackMessage(
                sdsdp_identifier=self.SDSP_IDENTIFIER,
                unique_aircraft_identifier=UNIQUE_AIRCRAFT_IDENTIFIER,
                state=aircraft_state,  # Fill with appropriate AircraftState object
                timestamp=arrow.utcnow().isoformat(),
                source="TestSource",
                track_state="Active",
            )
            all_track_data.append(track_data)

        return all_track_data
