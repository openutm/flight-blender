"""Volume 4D generator protocol and default implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class Volume4D:
    volume: dict[str, Any]
    time_start: dict[str, str]
    time_end: dict[str, str]


@runtime_checkable
class Volume4DGenerator(Protocol):
    """Protocol that all volume 4D generator plugins must satisfy."""

    def __init__(
        self,
        default_uav_speed_m_per_s: float,
        default_uav_climb_rate_m_per_s: float,
        default_uav_descent_rate_m_per_s: float,
    ): ...

    def build_v4d_from_geojson(
        self,
        geo_json_fc: dict[str, Any],
        start_datetime: str,
        end_datetime: str,
    ) -> list[Volume4D]: ...


class DefaultVolume4DGenerator:
    """Default generator: wraps each GeoJSON feature in a Volume4D with the full time window."""

    def __init__(
        self,
        default_uav_speed_m_per_s: float = 5.5,
        default_uav_climb_rate_m_per_s: float = 2.0,
        default_uav_descent_rate_m_per_s: float = 2.0,
    ):
        self.default_uav_speed_m_per_s = default_uav_speed_m_per_s
        self.default_uav_climb_rate_m_per_s = default_uav_climb_rate_m_per_s
        self.default_uav_descent_rate_m_per_s = default_uav_descent_rate_m_per_s

    def build_v4d_from_geojson(
        self,
        geo_json_fc: dict[str, Any],
        start_datetime: str,
        end_datetime: str,
    ) -> list[Volume4D]:
        features = geo_json_fc.get("features", [])
        volumes: list[Volume4D] = []
        for feature in features:
            props = feature.get("properties", {})
            geometry = feature.get("geometry", {})
            volumes.append(
                Volume4D(
                    volume={
                        "outline_polygon": geometry,
                        "altitude_lower": {
                            "value": props.get("min_altitude", {}).get("meters", 0),
                            "reference": "W84",
                            "units": "M",
                        },
                        "altitude_upper": {
                            "value": props.get("max_altitude", {}).get("meters", 500),
                            "reference": "W84",
                            "units": "M",
                        },
                    },
                    time_start={"format": "RFC3339", "value": start_datetime},
                    time_end={"format": "RFC3339", "value": end_datetime},
                )
            )
        return volumes
