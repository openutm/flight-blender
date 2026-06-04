from typing import List

from flight_blender.flight_feed.data_definitions import SingleAirtrafficObservation
from flight_blender.surveillance.data_definitions import TrackMessage


class SpecializedTrafficDataFuser:
    """A placeholder data fuser to generate track messages.

    Use this to implement your custom data fusion logic and set the
    ``FLIGHT_BLENDER_PLUGIN_TRAFFIC_DATA_FUSER`` environment variable to
    ``flight_blender.surveillance.custom_utils.SpecializedTrafficDataFuser``
    to call this class.

    The old env var name ``ASTM_F3623_SDSP_CUSTOM_DATA_FUSER_CLASS`` is still
    supported as a backward-compatible fallback.
    """

    def __init__(self, raw_observations: List[SingleAirtrafficObservation]):
        self.raw_observations = raw_observations

    def fuse_raw_observations(self) -> List[SingleAirtrafficObservation]:
        raise NotImplementedError

    def generate_track_messages(self, fused_observations: List[SingleAirtrafficObservation]) -> List[TrackMessage]:
        raise NotImplementedError
