"""Deconfliction engine protocol and default R-tree implementation.

The DefaultDeconflictionEngine performs in-memory R-tree bounding-box checks
against pre-fetched geofences and flight declarations.  The router is
responsible for querying the database and passing the results via
DeconflictionRequest.prefetched_fences / prefetched_declarations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from loguru import logger

try:
    from rtree import index as _rtree_index

    _RTREE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _rtree_index = None  # type: ignore[assignment]
    _RTREE_AVAILABLE = False
    logger.warning("rtree library not installed — falling back to pure-Python bbox deconfliction")


def _parse_bounds(bounds_str: str) -> tuple[float, float, float, float] | None:
    """Parse a JSON bounds string → (minx, miny, maxx, maxy) or None."""
    try:
        b = json.loads(bounds_str)
        return float(b["minx"]), float(b["miny"]), float(b["maxx"]), float(b["maxy"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _bbox_intersects(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> bool:
    """True when two axis-aligned bounding boxes overlap."""
    return a[0] <= b[2] and a[2] >= b[0] and a[1] <= b[3] and a[3] >= b[1]


def _check_rtree(view_box: list[float], items: list[dict], id_field: str) -> list[str]:
    """Return IDs of items whose bbox intersects view_box using an in-memory R-tree."""
    if not items or len(view_box) < 4:
        return []

    if _RTREE_AVAILABLE:
        idx = _rtree_index.Index()
        mapping: dict[int, str] = {}
        for item in items:
            coords = _parse_bounds(item.get("bounds", "{}"))
            if coords is None:
                continue
            int_id = int(hashlib.sha256(item[id_field].encode()).hexdigest(), 16) % (10**8)
            mapping[int_id] = item[id_field]
            idx.insert(int_id, coords)
        results = [mapping[r] for r in idx.intersection(tuple(view_box)) if r in mapping]
    else:
        vb = (view_box[0], view_box[1], view_box[2], view_box[3])
        results = []
        for item in items:
            coords = _parse_bounds(item.get("bounds", "{}"))
            if coords and _bbox_intersects(vb, coords):
                results.append(item[id_field])

    return results


@dataclass
class DeconflictionRequest:
    declaration_id: str | None = None
    start_datetime: str | None = None
    end_datetime: str | None = None
    flight_declaration_geo_json: dict | None = None
    view_box: list[float] = field(default_factory=list)  # [minx, miny, maxx, maxy]
    ussp_network_enabled: int = 0
    type_of_operation: int = 0
    priority: int = 0
    # Pre-fetched spatial data — populated by the router before calling the engine
    prefetched_fences: list[dict] = field(default_factory=list)  # [{id: str, bounds: str}, ...]
    prefetched_declarations: list[dict] = field(default_factory=list)  # [{id: str, bounds: str}, ...]


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
    """Built-in R-tree bounding-box deconfliction engine.

    Mirrors the Django DefaultDeconflictionEngine logic:
    1. Check geofence bbox conflicts via R-tree.
    2. Check active flight declaration bbox conflicts via R-tree.
    3. Any intersection → rejected (state 8, is_approved=False).

    Requires DeconflictionRequest.prefetched_fences and
    prefetched_declarations to be populated by the caller.
    """

    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult:
        view_box = request.view_box
        is_approved = True
        declaration_state = 0 if request.ussp_network_enabled else 1

        all_relevant_fences: list[str] = []
        all_relevant_declarations: list[str] = []

        if not view_box or len(view_box) < 4:
            logger.debug("Deconfliction skipped: no view_box provided")
            return DeconflictionResult(
                is_approved=is_approved,
                declaration_state=declaration_state,
            )

        # ── GeoFence check ───────────────────────────────────────────────
        if request.prefetched_fences:
            all_relevant_fences = _check_rtree(view_box, request.prefetched_fences, "id")
            if all_relevant_fences:
                logger.info("Deconfliction: {} geofence(s) conflict", len(all_relevant_fences))
                is_approved = False
                declaration_state = 8

        # ── Flight declaration check ─────────────────────────────────────
        if request.prefetched_declarations and is_approved:
            all_relevant_declarations = _check_rtree(view_box, request.prefetched_declarations, "id")
            if all_relevant_declarations:
                logger.info("Deconfliction: {} declaration(s) conflict", len(all_relevant_declarations))
                is_approved = False
                declaration_state = 8

        return DeconflictionResult(
            all_relevant_fences=all_relevant_fences,
            all_relevant_declarations=all_relevant_declarations,
            is_approved=is_approved,
            declaration_state=declaration_state,
        )
