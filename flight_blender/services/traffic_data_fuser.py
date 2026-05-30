"""Traffic data fuser protocol and default implementation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class TrackMessage:
    sdsdp_identifier: str
    unique_aircraft_identifier: str
    state: dict[str, Any]
    timestamp: str
    source: str
    track_state: str = "active"


@runtime_checkable
class TrafficDataFuser(Protocol):
    """Protocol that all traffic data fuser plugins must satisfy."""

    def __init__(self, session_id: str, raw_observations: list[dict[str, Any]]): ...
    def generate_track_messages(self) -> list[TrackMessage]: ...


class DefaultTrafficDataFuser:
    """Default fuser: keeps latest observation per aircraft."""

    def __init__(self, session_id: str, raw_observations: list[dict[str, Any]]):
        self.session_id = session_id
        self.raw_observations = raw_observations

    def generate_track_messages(self) -> list[TrackMessage]:
        latest_by_icao: dict[str, dict[str, Any]] = {}
        for obs in self.raw_observations:
            icao = obs.get("icao_address", "")
            if not icao:
                continue
            existing = latest_by_icao.get(icao)
            if existing is None or obs.get("timestamp", 0) > existing.get("timestamp", 0):
                latest_by_icao[icao] = obs

        track_messages: list[TrackMessage] = []
        for icao, obs in latest_by_icao.items():
            track_messages.append(
                TrackMessage(
                    sdsdp_identifier="FLIGHT_BLENDER_SDSP",
                    unique_aircraft_identifier=icao,
                    state={
                        "position": {
                            "lat": obs.get("lat_dd", 0.0),
                            "lng": obs.get("lon_dd", 0.0),
                            "alt": obs.get("altitude_mm", 0.0),
                        },
                    },
                    timestamp=str(obs.get("timestamp", "")),
                    source="default_fuser",
                    track_state="active",
                )
            )
        return track_messages
