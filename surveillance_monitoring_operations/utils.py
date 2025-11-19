from typing import List

from flight_feed_operations.data_definitions import SingleAirtrafficObservation
from surveillance_monitoring_operations.data_definitions import (
    AircraftPosition,
    AircraftState,
    SpeedAccuracy,
    TrackMessage,
)


class TrafficDataFuser:
    def __init__(self, raw_observations: List[SingleAirtrafficObservation]):
        self.raw_observations = raw_observations

    def fuse_raw_observations(self) -> List[SingleAirtrafficObservation]:
        return self.raw_observations

    def generate_track_messages(self, fused_observations: List[SingleAirtrafficObservation]) -> List[TrackMessage]:
        # Implement fusion logic here

        # Get all unique targets
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
                sdsdp_identifier="SDSP123",
                unique_aircraft_identifier=UNIQUE_AIRCRAFT_IDENTIFIER,
                state=aircraft_state,  # Fill with appropriate AircraftState object
                timestamp=arrow.utcnow().isoformat(),
                source="TestSource",
                track_state="Active",
            )
            all_track_data.append(track_data)

        return all_track_data
