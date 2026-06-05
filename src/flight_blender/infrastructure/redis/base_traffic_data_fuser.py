"""Back-compat re-export — ``BaseTrafficDataFuser`` now lives in core.operations.surveillance.

The class is framework-free (no I/O; the track store is injected) so it
belongs in core alongside the ``TrafficDataFuser`` it extends. This shim
keeps existing imports of
``flight_blender.infrastructure.redis.base_traffic_data_fuser.BaseTrafficDataFuser``
working.
"""

from flight_blender.core.operations.surveillance import BaseTrafficDataFuser

__all__ = ["BaseTrafficDataFuser"]
