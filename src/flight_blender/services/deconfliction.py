"""Deconfliction engine protocol and default implementation.

The DefaultDeconflictionEngine performs strategic conflict detection between a
candidate operational intent and the set of existing / peer operational
intents.  Two checking modes are supported:

* **Volume mode** (preferred): full 4D strategic deconfliction — a conflict
  requires overlap in space *and* time *and* altitude.  Geometry is pure
  Python (ray-casting point-in-polygon, matching the style of
  ``services/conformance_engine.py``); shapely is deliberately not used.
* **Bbox mode** (legacy/fallback): in-memory R-tree bounding-box checks against
  pre-fetched geofences and flight declarations.  This mirrors the Django
  ``DefaultDeconflictionEngine`` (``flight_declaration_operations/
  deconfliction_engine.py``) exactly.

The router is responsible for querying the database and passing the results via
the DeconflictionRequest fields.

Safety note: strategic deconfliction must *fail closed*.  When the engine
cannot produce a trustworthy answer (import error, malformed geometry, etc.)
the decision logic refuses approval rather than accepting by default.  See
``resolve_decision``.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from loguru import logger

from flight_blender.common.enums import OperationState

try:
    from rtree import index as _rtree_index

    _RTREE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _rtree_index = None  # type: ignore[assignment]
    _RTREE_AVAILABLE = False
    logger.warning("rtree library not installed — falling back to pure-Python bbox deconfliction")


# ── Pure geometric / temporal / altitude helpers ───────────────────────────


from flight_blender.common.geometry import point_in_polygon as _pip


def _point_in_polygon(pt: list[float], poly: list[list[float]]) -> bool:
    """Adapter: call shared ``point_in_polygon`` with the deconfliction data shapes."""
    return _pip(pt[0], pt[1], [(p[0], p[1]) for p in poly])


def check_polygon_intersection(polygon_a: list[list[float]], polygon_b: list[list[float]]) -> bool:
    """Return True when two polygons (lists of [lon, lat]) overlap.

    Uses a bounding-box quick-reject followed by mutual vertex containment.
    Pure Python — no third-party geometry deps.
    """
    if len(polygon_a) < 3 or len(polygon_b) < 3:
        return False

    def _bbox(coords: list[list[float]]) -> tuple[float, float, float, float]:
        xs = [c[0] for c in coords]
        ys = [c[1] for c in coords]
        return min(xs), min(ys), max(xs), max(ys)

    ax0, ay0, ax1, ay1 = _bbox(polygon_a)
    bx0, by0, bx1, by1 = _bbox(polygon_b)

    # bbox quick-reject
    if ax1 < bx0 or bx1 < ax0 or ay1 < by0 or by1 < ay0:
        return False

    for pt in polygon_a:
        if _point_in_polygon(pt, polygon_b):
            return True
    for pt in polygon_b:
        if _point_in_polygon(pt, polygon_a):
            return True
    return False


def check_time_overlap(start_a: datetime, end_a: datetime, start_b: datetime, end_b: datetime) -> bool:
    """Return True when two (closed) time intervals overlap or touch."""
    return start_a <= end_b and start_b <= end_a


def check_altitude_overlap(min_a: float, max_a: float, min_b: float, max_b: float) -> bool:
    """Return True when two (closed) altitude bands overlap or touch."""
    return min_a <= max_b and min_b <= max_a


def _coerce_dt(value: object) -> datetime | None:
    """Best-effort coercion of an ISO string / datetime into a datetime."""
    if isinstance(value, datetime):
        return value
    from flight_blender.common.datetime_utils import parse_iso_utc

    return parse_iso_utc(value)


def deconflict_operational_intent(candidate: dict, existing_volumes: list[dict]) -> bool:
    """Full strategic deconfliction.

    Returns True when the candidate is **CLEAR** (no conflict) against every
    existing volume, considering space + time + altitude.  A pairwise overlap in
    *all three* dimensions constitutes a conflict and returns False.

    Each volume dict has keys: ``coordinates`` (list of [lon, lat]),
    ``min_alt``, ``max_alt``, ``start``, ``end`` (datetime or ISO string).
    """
    cand_start = _coerce_dt(candidate.get("start"))
    cand_end = _coerce_dt(candidate.get("end"))
    cand_coords = candidate.get("coordinates") or []

    for existing in existing_volumes:
        ex_start = _coerce_dt(existing.get("start"))
        ex_end = _coerce_dt(existing.get("end"))

        # temporal — only enforce when both windows are known
        if cand_start and cand_end and ex_start and ex_end:
            if not check_time_overlap(cand_start, cand_end, ex_start, ex_end):
                continue

        # altitude
        if not check_altitude_overlap(
            candidate.get("min_alt", float("-inf")),
            candidate.get("max_alt", float("inf")),
            existing.get("min_alt", float("-inf")),
            existing.get("max_alt", float("inf")),
        ):
            continue

        # spatial
        if check_polygon_intersection(cand_coords, existing.get("coordinates") or []):
            return False  # conflict found

    return True  # clear


# ── Legacy bbox helpers (faithful Django port) ──────────────────────────────


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


# ── Data classes ────────────────────────────────────────────────────────────


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
    # Volume mode — full 4D strategic deconfliction
    candidate_volume: dict | None = None
    prefetched_volumes: list[dict] = field(default_factory=list)
    # Pre-fetched spatial data — populated by the router for legacy bbox mode
    prefetched_fences: list[dict] = field(default_factory=list)  # [{id: str, bounds: str}, ...]
    prefetched_declarations: list[dict] = field(default_factory=list)  # [{id: str, bounds: str}, ...]


@dataclass
class DeconflictionResult:
    all_relevant_fences: list[str] = field(default_factory=list)
    all_relevant_declarations: list[str] = field(default_factory=list)
    is_approved: bool = True
    declaration_state: int = 1  # Accepted


@dataclass
class DeconflictionDecision:
    """Outcome of mapping (clear?/error?/network?) → (approval, db state)."""

    is_approved: bool
    declaration_state: int


def resolve_decision(*, engine_error: bool, is_clear: bool, ussp_network_enabled: int | bool) -> DeconflictionDecision:
    """Pure, fail-closed decision logic for strategic deconfliction.

    Mirrors the Django acceptance path
    (``flight_declaration_operations/deconfliction_engine.py`` +
    ``views.py``):

    * engine error  → never approved, Rejected (fail closed — safety critical).
    * conflict      → not approved, Rejected (state 8).
    * clear + USSP  → approved, NotSubmitted (state 0) — pending DSS submission.
    * clear, no USSP→ approved, Accepted (state 1).
    """
    if engine_error or not is_clear:
        return DeconflictionDecision(is_approved=False, declaration_state=int(OperationState.REJECTED))
    if ussp_network_enabled:
        return DeconflictionDecision(is_approved=True, declaration_state=int(OperationState.NOT_SUBMITTED))
    return DeconflictionDecision(is_approved=True, declaration_state=int(OperationState.ACCEPTED))


# ── Protocol + engine ───────────────────────────────────────────────────────


@runtime_checkable
class DeconflictionEngine(Protocol):
    """Protocol that all deconfliction engine plugins must satisfy."""

    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult: ...


class DefaultDeconflictionEngine:
    """Built-in strategic deconfliction engine.

    Volume mode (when ``candidate_volume`` is supplied): full 4D check via
    ``deconflict_operational_intent``.  Bbox mode (otherwise): R-tree
    bounding-box checks against pre-fetched geofences and declarations,
    mirroring the Django engine.

    Any conflict → rejected (state 8, is_approved=False).  A clear result maps
    via ``resolve_decision`` so the USSP gate is honoured consistently.
    """

    def check_deconfliction(self, request: DeconflictionRequest) -> DeconflictionResult:
        if request.candidate_volume is not None:
            return self._check_volumes(request)
        return self._check_bbox(request)

    # — Volume (4D) strategic mode —
    def _check_volumes(self, request: DeconflictionRequest) -> DeconflictionResult:
        is_clear = deconflict_operational_intent(request.candidate_volume or {}, request.prefetched_volumes)
        if not is_clear:
            logger.info("Deconfliction: candidate conflicts with an existing operational intent")
        decision = resolve_decision(
            engine_error=False,
            is_clear=is_clear,
            ussp_network_enabled=request.ussp_network_enabled,
        )
        return DeconflictionResult(
            is_approved=decision.is_approved,
            declaration_state=decision.declaration_state,
        )

    # — Legacy bbox mode (faithful Django port) —
    def _check_bbox(self, request: DeconflictionRequest) -> DeconflictionResult:
        view_box = request.view_box

        all_relevant_fences: list[str] = []
        all_relevant_declarations: list[str] = []

        if not view_box or len(view_box) < 4:
            logger.debug("Deconfliction skipped: no view_box provided")
            decision = resolve_decision(engine_error=False, is_clear=True, ussp_network_enabled=request.ussp_network_enabled)
            return DeconflictionResult(is_approved=decision.is_approved, declaration_state=decision.declaration_state)

        if request.prefetched_fences:
            all_relevant_fences = _check_rtree(view_box, request.prefetched_fences, "id")
        if request.prefetched_declarations:
            all_relevant_declarations = _check_rtree(view_box, request.prefetched_declarations, "id")

        is_clear = not (all_relevant_fences or all_relevant_declarations)
        if not is_clear:
            logger.info(
                "Deconfliction: {} fence(s), {} declaration(s) conflict",
                len(all_relevant_fences),
                len(all_relevant_declarations),
            )
        decision = resolve_decision(engine_error=False, is_clear=is_clear, ussp_network_enabled=request.ussp_network_enabled)
        return DeconflictionResult(
            all_relevant_fences=all_relevant_fences,
            all_relevant_declarations=all_relevant_declarations,
            is_approved=decision.is_approved,
            declaration_state=decision.declaration_state,
        )
