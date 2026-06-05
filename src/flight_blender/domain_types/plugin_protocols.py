from typing import Protocol, runtime_checkable

from sqlalchemy.orm import Session

from flight_blender.domain_types.flight_declarations import DeconflictionRequest, DeconflictionResult
from flight_blender.domain_types.surveillance import TrackMessage


@runtime_checkable
class DeconflictionEngineProtocol(Protocol):
    """Structural interface for flight de-confliction engines."""

    def check_deconfliction(self, request: DeconflictionRequest, db: Session) -> DeconflictionResult: ...


@runtime_checkable
class TrafficDataFuserProtocol(Protocol):
    """Structural interface for traffic data fusion implementations."""

    def generate_track_messages(self) -> list[TrackMessage]:
        """Run the full fusion pipeline and return track messages."""
        ...
