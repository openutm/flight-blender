from typing import Any, Protocol, runtime_checkable

from flight_blender.geo_fence.data_definitions import GeofencePayload


@runtime_checkable
class GeoFenceRepository(Protocol):
    def get_active_geofences(self) -> list: ...
    def create_or_update_geofence(self, geofence_payload: GeofencePayload) -> Any | None: ...
