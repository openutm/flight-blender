"""Deconfliction engine protocol and default implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class DeconflictionRequest:
    declaration_id: str | None = None
    start_datetime: str | None = None
    end_datetime: str | None = None
    flight_declaration_geo_json: dict | None = None
    ussp_network_enabled: bool = False


@dataclass
class DeconflictionResult:
    all_relevant_fences: list[str] = field(default_factory=list)
    all_relevant_declarations: list[str] = field(default_factory=list)
    is_approved: bool = True
    declaration_state: int = 1  # Accepted


@runtime_checkable
class DeconflictionEngine(Protocol):
    """Protocol that all deconfliction engine plugins must satisfy."""

    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult: ...


class DefaultDeconflictionEngine:
    """Default deconfliction engine that approves all declarations."""

    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult:
        return DeconflictionResult(
            all_relevant_fences=[],
            all_relevant_declarations=[],
            is_approved=True,
            declaration_state=1,
        )
