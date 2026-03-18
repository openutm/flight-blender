"""Protocol definition for pluggable traffic data fusion implementations.

Any class that implements ``generate_track_messages`` with the correct
signature is a valid ``TrafficDataFuser`` — no inheritance required
(structural subtyping via ``typing.Protocol``).
"""

from typing import Protocol, runtime_checkable

from surveillance_monitoring_operations.data_definitions import TrackMessage


@runtime_checkable
class TrafficDataFuser(Protocol):
    """Structural interface for traffic data fusion implementations."""

    def generate_track_messages(self) -> list[TrackMessage]:
        """Run the full fusion pipeline and return track messages."""
        ...
