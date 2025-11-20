from typing import List

from flight_feed_operations.data_definitions import SingleAirtrafficObservation
from surveillance_monitoring_operations.data_definitions import (
    AircraftPosition,
    AircraftState,
    SpeedAccuracy,
    TrackMessage,
)


class SpecializedTrafficDataFuser:
    """A placeholder data fuser to generate track messages: use this to implement your custom data fusion logic and set the ASTM_F3623_SDSP_CUSTOM_DATA_FUSER_CLASS environment variable to surveillance_monitoring_operations.custom_utils.SpecializedTrafficDataFuser to call this class"""

    def __init__(self, raw_observations: List[SingleAirtrafficObservation]):
        self.raw_observations = raw_observations

    def fuse_raw_observations(self) -> List[SingleAirtrafficObservation]:
        raise NotImplementedError

    def generate_track_messages(self, fused_observations: List[SingleAirtrafficObservation]) -> List[TrackMessage]:
        raise NotImplementedError
