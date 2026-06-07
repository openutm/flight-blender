from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.clients.redis_client import RedisStreamOperations
from flight_blender.domain_types.flight_declarations import DeconflictionRequest, DeconflictionResult
from flight_blender.domain_types.flight_feed import SingleAirtrafficObservation
from flight_blender.domain_types.scd import Volume4D
from flight_blender.domain_types.surveillance import TrackMessage


@runtime_checkable
class DeconflictionEngineProtocol(Protocol):
    """Structural interface for flight de-confliction engines."""

    async def check_deconfliction(self, request: DeconflictionRequest, db: AsyncSession) -> DeconflictionResult: ...


@runtime_checkable
class TrafficDataFuserProtocol(Protocol):
    """Structural interface for traffic data fusion implementations."""

    def __init__(
        self,
        *,
        session_id: str,
        raw_observations: list[SingleAirtrafficObservation],
        track_store: RedisStreamOperations,
    ): ...

    def generate_track_messages(self) -> list[TrackMessage]:
        """Run the full fusion pipeline and return track messages."""
        ...


@runtime_checkable
class Volume4DGeneratorProtocol(Protocol):
    """Structural interface for custom Volume4D generation plugins."""

    def __init__(
        self,
        *,
        default_uav_speed_m_per_s: float,
        default_uav_climb_rate_m_per_s: float,
        default_uav_descent_rate_m_per_s: float,
    ): ...

    def build_v4d_from_geojson(
        self,
        geo_json_fc: dict,
        start_datetime: str,
        end_datetime: str,
    ) -> list[Volume4D]:
        """Convert a GeoJSON FeatureCollection into a list of Volume4D objects."""
        ...
