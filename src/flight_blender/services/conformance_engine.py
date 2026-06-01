"""
Pure conformance check engine (C2-C11).

This is a faithful FastAPI port of the Django
``conformance_monitoring_operations.utils.FlightBlenderConformanceEngine``.

The original Django engine read directly from the database and the live
telemetry stream inside its methods, which made it impossible to unit test.
Here the decision logic is expressed as **pure functions** that take their
inputs as plain values / dataclasses, so the C2-C11 checks can be exercised
in isolation. The Celery task layer is responsible for loading the
declaration, telemetry and geofences and feeding them into these functions.

Geometry note: the Django engine used shapely's ``Point.within(Polygon)`` for
the horizontal volume / geofence checks. shapely happens to be importable in
the current project venv (2.1.x), but it is deliberately NOT declared in this
FastAPI project's ``pyproject.toml`` or ``uv.lock`` -- it is only present
incidentally and would disappear on a clean ``uv sync``. Rather than depend on
an undeclared package (or modify dependency files that are outside the
conformance scope), the equivalent point-in-polygon test is implemented here
with a self-contained ray-casting algorithm. The semantics match the Django
check (a point strictly inside the polygon is "within"). If shapely is later
added as a first-class dependency, ``point_in_polygon`` can be swapped for
``Point(lng, lat).within(Polygon(vertices))`` without changing any call site.

Conformance vocabulary (mirrors Django ``ConformanceChecksList``):

    C2  - Flight authorization / operational intent reference not granted
    C3  - Telemetry aircraft-id does not match the authorization
    C4  - Operation state invalid (Not Submitted / Ended / Withdrawn /
          Cancelled / Rejected)
    C5  - Operation accepted but not yet activated
    C6  - Telemetry timestamp outside the operation start/end window
    C7a - Aircraft outside the horizontal (4D volume) footprint
    C7b - Aircraft outside the volume altitude band
    C8  - Aircraft breaching an active geofence
    C9a - No telemetry received at all (contingent)
    C9b - Telemetry not received within the liveness window
    C10 - Operation state not in {Activated, Nonconforming, Contingent}
    C11 - No operational intent reference / flight authorization
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum

from flight_blender.common.enums import ACTIVE_OPERATIONAL_STATES, OperationState

# States that make an operation invalid for telemetry conformance (Django C4):
# Not Submitted (0), Ended (5), Withdrawn (6), Cancelled (7), Rejected (8).
_INVALID_TELEMETRY_STATES: frozenset[int] = frozenset(
    {
        int(OperationState.NOT_SUBMITTED),
        int(OperationState.ENDED),
        int(OperationState.WITHDRAWN),
        int(OperationState.CANCELLED),
        int(OperationState.REJECTED),
    }
)

# States in which the operation is considered actively airborne (Django [2, 3, 4]).
_ACTIVE_STATES: frozenset[int] = frozenset(int(s) for s in ACTIVE_OPERATIONAL_STATES)

# Liveness window for the most-recent telemetry (Django used +/- 15 seconds).
TELEMETRY_LIVENESS_WINDOW = timedelta(seconds=15)


class ConformanceCheck(IntEnum):
    """The engine's return vocabulary.

    ``CONFORMANT`` is the success sentinel. The Django engine used ``100`` for a
    telemetry-conformant operation and ``1`` for an op-intent-conformant one; a
    single explicit sentinel is clearer for callers, and the numeric C-codes are
    preserved so they can be mapped back onto the legacy values if needed.
    """

    CONFORMANT = 1
    C2 = 2
    C3 = 3
    C4 = 4
    C5 = 5
    C6 = 6
    C7a = 7
    C7b = 8
    C8 = 9
    C9a = 10
    C9b = 11
    C10 = 12
    C11 = 13


# Human-readable labels, mirroring Django ``ConformanceChecksList.options``.
CONFORMANCE_CHECK_LABELS: dict[ConformanceCheck, str] = {
    ConformanceCheck.CONFORMANT: "Conformant",
    ConformanceCheck.C2: "Flight Auth not granted",
    ConformanceCheck.C3: "Telemetry Auth mismatch",
    ConformanceCheck.C4: "Operation state invalid",
    ConformanceCheck.C5: "Operation not activated",
    ConformanceCheck.C6: "Telemetry time incorrect",
    ConformanceCheck.C7a: "Flight out of bounds",
    ConformanceCheck.C7b: "Flight altitude out of bounds",
    ConformanceCheck.C8: "Geofence breached",
    ConformanceCheck.C9a: "Telemetry not received",
    ConformanceCheck.C9b: "Telemetry not received within liveness window",
    ConformanceCheck.C10: "State not in activated, non-conforming, contingent",
    ConformanceCheck.C11: "No Flight Authorization",
}


@dataclass
class VolumeBound:
    """A horizontal polygon footprint plus its altitude band (metres, WGS84)."""

    vertices: list[tuple[float, float]]  # (lng, lat) pairs
    altitude_lower: float
    altitude_upper: float


@dataclass
class GeoFencePolygon:
    """A single geofence polygon ring as (lng, lat) vertices."""

    vertices: list[tuple[float, float]]


@dataclass
class TelemetryObservation:
    """A single telemetry position report."""

    aircraft_id: str
    lat: float
    lng: float
    altitude_m_wgs_84: float


@dataclass
class OperationalIntentSnapshot:
    """The declaration / operational-intent state needed for telemetry checks."""

    aircraft_id: str
    state: int
    start_datetime: datetime
    end_datetime: datetime
    volumes: list[VolumeBound] = field(default_factory=list)
    operational_intent_reference_exists: bool = True


from flight_blender.common.datetime_utils import ensure_utc as _aware


def is_time_between(begin_time: datetime, end_time: datetime, check_time: datetime) -> bool:
    """Return whether ``check_time`` falls within [begin_time, end_time].

    Ported from the Django helper of the same name, including the
    crosses-midnight branch.
    """
    begin_time = _aware(begin_time)
    end_time = _aware(end_time)
    check_time = _aware(check_time)
    if begin_time < end_time:
        return begin_time <= check_time <= end_time
    # crosses midnight
    return check_time >= begin_time or check_time <= end_time


from flight_blender.common.geometry import point_in_polygon  # re-exported for backward compat


def check_operation_conformant_via_telemetry(
    *,
    snapshot: OperationalIntentSnapshot,
    telemetry: TelemetryObservation,
    geofences: list[GeoFencePolygon],
    now: datetime | None = None,
    ussp_network_enabled: bool = False,
) -> ConformanceCheck:
    """Run the telemetry-driven conformance sequence (C2-C8).

    Returns :data:`ConformanceCheck.CONFORMANT` when every check passes, or the
    first failing C-code otherwise. The check order matches the Django engine.
    """
    now = _aware(now) if now is not None else datetime.now(timezone.utc)

    # C2: flight authorization / operational intent reference exists.
    # Django only enforced this when the USSP network was enabled; with the
    # network disabled the reference is treated as implicitly present.
    if ussp_network_enabled and not snapshot.operational_intent_reference_exists:
        return ConformanceCheck.C2

    # C3: telemetry aircraft id matches the declared aircraft id.
    if snapshot.aircraft_id != telemetry.aircraft_id:
        return ConformanceCheck.C3

    # C4: operation is not in an invalid state.
    if snapshot.state in _INVALID_TELEMETRY_STATES:
        return ConformanceCheck.C4

    # C5: operation is activated (or nonconforming / contingent).
    if snapshot.state not in _ACTIVE_STATES:
        return ConformanceCheck.C5

    # C6: telemetry timestamp within the operation time window.
    if not is_time_between(snapshot.start_datetime, snapshot.end_datetime, now):
        return ConformanceCheck.C6

    # C7a / C7b: aircraft within the 4D volume (footprint + altitude band).
    bounds_conformant = False
    altitude_conformant = False
    for volume in snapshot.volumes:
        if point_in_polygon(telemetry.lng, telemetry.lat, volume.vertices):
            bounds_conformant = True
        if volume.altitude_lower <= telemetry.altitude_m_wgs_84 <= volume.altitude_upper:
            altitude_conformant = True

    # Django checks altitude (C7b) before horizontal bounds (C7a).
    if not altitude_conformant:
        return ConformanceCheck.C7b
    if not bounds_conformant:
        return ConformanceCheck.C7a

    # C8: aircraft not breaching an active geofence.
    for geofence in geofences:
        if point_in_polygon(telemetry.lng, telemetry.lat, geofence.vertices):
            return ConformanceCheck.C8

    return ConformanceCheck.CONFORMANT


def check_operational_intent_reference_conformance(
    *,
    state: int,
    latest_telemetry_datetime: datetime | None,
    operational_intent_reference_exists: bool = True,
    now: datetime | None = None,
    ussp_network_enabled: bool = False,
    liveness_window: timedelta = TELEMETRY_LIVENESS_WINDOW,
) -> ConformanceCheck:
    """Run the telemetry-independent conformance sequence (C9-C11).

    Mirrors Django ``check_flight_operational_intent_reference_conformance``:

    * C11 - operational intent reference missing (only when USSP enabled).
    * C10 - operation state not in {Activated, Nonconforming, Contingent}.
    * C9a - no telemetry has ever been received.
    * C9b - telemetry exists but is older than the liveness window.
    """
    now = _aware(now) if now is not None else datetime.now(timezone.utc)

    # C11: operational intent reference exists (only checked on USSP network).
    if ussp_network_enabled and not operational_intent_reference_exists:
        return ConformanceCheck.C11

    # C10: operation state is one in which the operation is active.
    if state not in _ACTIVE_STATES:
        return ConformanceCheck.C10

    # C9a / C9b: telemetry liveness.
    if latest_telemetry_datetime is None:
        return ConformanceCheck.C9a

    latest = _aware(latest_telemetry_datetime)
    window_start = now - liveness_window
    window_end = now + liveness_window
    if not (window_start <= latest <= window_end):
        return ConformanceCheck.C9b

    return ConformanceCheck.CONFORMANT
