"""
Unit tests for the pure conformance check engine.

These tests exercise the C2-C11 conformance logic ported from the Django
``conformance_monitoring_operations.utils.FlightBlenderConformanceEngine``.
They are pure-logic tests: no database, no network, no Celery.
"""

from datetime import datetime, timedelta, timezone

from flight_blender.services.conformance_engine import (
    ConformanceCheck,
    GeoFencePolygon,
    OperationalIntentSnapshot,
    TelemetryObservation,
    VolumeBound,
    check_operation_conformant_via_telemetry,
    check_operational_intent_reference_conformance,
    point_in_polygon,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _square_volume(
    *,
    min_lng: float = 0.0,
    min_lat: float = 0.0,
    max_lng: float = 1.0,
    max_lat: float = 1.0,
    altitude_lower: float = 0.0,
    altitude_upper: float = 120.0,
) -> VolumeBound:
    """A square volume from (min_lng, min_lat) to (max_lng, max_lat)."""
    return VolumeBound(
        vertices=[
            (min_lng, min_lat),
            (max_lng, min_lat),
            (max_lng, max_lat),
            (min_lng, max_lat),
        ],
        altitude_lower=altitude_lower,
        altitude_upper=altitude_upper,
    )


def _snapshot(**overrides) -> OperationalIntentSnapshot:
    defaults = dict(
        aircraft_id="abc-123",
        state=2,  # Activated
        start_datetime=_now() - timedelta(hours=1),
        end_datetime=_now() + timedelta(hours=1),
        volumes=[_square_volume()],
        operational_intent_reference_exists=True,
    )
    defaults.update(overrides)
    return OperationalIntentSnapshot(**defaults)


def _telemetry(**overrides) -> TelemetryObservation:
    defaults = dict(aircraft_id="abc-123", lat=0.5, lng=0.5, altitude_m_wgs_84=50.0)
    defaults.update(overrides)
    return TelemetryObservation(**defaults)


# --------------------------------------------------------------------------- #
# point_in_polygon geometry helper
# --------------------------------------------------------------------------- #


def test_point_in_polygon_inside():
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert point_in_polygon(0.5, 0.5, square) is True


def test_point_in_polygon_outside():
    square = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    assert point_in_polygon(5.0, 5.0, square) is False


def test_point_in_polygon_degenerate_returns_false():
    assert point_in_polygon(0.0, 0.0, [(0.0, 0.0), (1.0, 1.0)]) is False


# --------------------------------------------------------------------------- #
# Telemetry-based conformance (C2-C8)
# --------------------------------------------------------------------------- #


def test_conforming_case_returns_conformant():
    result = check_operation_conformant_via_telemetry(
        snapshot=_snapshot(),
        telemetry=_telemetry(),
        geofences=[],
    )
    assert result is ConformanceCheck.CONFORMANT


def test_c2_missing_operational_intent_reference():
    """C2: when USSP network enabled and op-intent reference is absent."""
    result = check_operation_conformant_via_telemetry(
        snapshot=_snapshot(operational_intent_reference_exists=False),
        telemetry=_telemetry(),
        geofences=[],
        ussp_network_enabled=True,
    )
    assert result is ConformanceCheck.C2


def test_c2_skipped_when_ussp_network_disabled():
    """With the USSP network disabled, a missing reference must NOT trip C2."""
    result = check_operation_conformant_via_telemetry(
        snapshot=_snapshot(operational_intent_reference_exists=False),
        telemetry=_telemetry(),
        geofences=[],
        ussp_network_enabled=False,
    )
    assert result is ConformanceCheck.CONFORMANT


def test_c3_aircraft_id_mismatch():
    result = check_operation_conformant_via_telemetry(
        snapshot=_snapshot(aircraft_id="abc-123"),
        telemetry=_telemetry(aircraft_id="other-999"),
        geofences=[],
    )
    assert result is ConformanceCheck.C3


def test_c4_invalid_state():
    """States 0, 5, 6, 7, 8 are invalid for an active operation (C4)."""
    for invalid_state in (0, 5, 6, 7, 8):
        result = check_operation_conformant_via_telemetry(
            snapshot=_snapshot(state=invalid_state),
            telemetry=_telemetry(),
            geofences=[],
        )
        assert result is ConformanceCheck.C4, f"state={invalid_state}"


def test_c5_not_activated_state():
    """State 1 (Accepted) is valid but not yet activated (C5)."""
    result = check_operation_conformant_via_telemetry(
        snapshot=_snapshot(state=1),
        telemetry=_telemetry(),
        geofences=[],
    )
    assert result is ConformanceCheck.C5


def test_c6_telemetry_outside_time_window():
    result = check_operation_conformant_via_telemetry(
        snapshot=_snapshot(
            start_datetime=_now() - timedelta(hours=3),
            end_datetime=_now() - timedelta(hours=2),
        ),
        telemetry=_telemetry(),
        geofences=[],
    )
    assert result is ConformanceCheck.C6


def test_c7a_outside_horizontal_volume():
    """Inside altitude band but outside the polygon footprint -> C7a."""
    result = check_operation_conformant_via_telemetry(
        snapshot=_snapshot(volumes=[_square_volume(altitude_lower=0.0, altitude_upper=120.0)]),
        telemetry=_telemetry(lat=5.0, lng=5.0, altitude_m_wgs_84=50.0),
        geofences=[],
    )
    assert result is ConformanceCheck.C7a


def test_c7b_outside_altitude_band():
    """Inside the footprint but above the altitude ceiling -> C7b."""
    result = check_operation_conformant_via_telemetry(
        snapshot=_snapshot(volumes=[_square_volume(altitude_lower=0.0, altitude_upper=120.0)]),
        telemetry=_telemetry(lat=0.5, lng=0.5, altitude_m_wgs_84=500.0),
        geofences=[],
    )
    assert result is ConformanceCheck.C7b


def test_c8_geofence_breach():
    """Aircraft inside the operating volume but also inside an active geofence -> C8."""
    breaching_fence = GeoFencePolygon(vertices=[(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)])
    result = check_operation_conformant_via_telemetry(
        snapshot=_snapshot(),
        telemetry=_telemetry(lat=0.5, lng=0.5),
        geofences=[breaching_fence],
    )
    assert result is ConformanceCheck.C8


def test_geofence_not_breached_is_conformant():
    far_fence = GeoFencePolygon(vertices=[(10.0, 10.0), (11.0, 10.0), (11.0, 11.0), (10.0, 11.0)])
    result = check_operation_conformant_via_telemetry(
        snapshot=_snapshot(),
        telemetry=_telemetry(lat=0.5, lng=0.5),
        geofences=[far_fence],
    )
    assert result is ConformanceCheck.CONFORMANT


# --------------------------------------------------------------------------- #
# Operational-intent-reference conformance (C9-C11)
# --------------------------------------------------------------------------- #


def test_op_intent_conformance_conforming():
    result = check_operational_intent_reference_conformance(
        state=2,
        latest_telemetry_datetime=_now(),
        operational_intent_reference_exists=True,
    )
    assert result is ConformanceCheck.CONFORMANT


def test_c11_missing_operational_intent_reference():
    result = check_operational_intent_reference_conformance(
        state=2,
        latest_telemetry_datetime=_now(),
        operational_intent_reference_exists=False,
        ussp_network_enabled=True,
    )
    assert result is ConformanceCheck.C11


def test_c11_skipped_when_ussp_disabled():
    result = check_operational_intent_reference_conformance(
        state=2,
        latest_telemetry_datetime=_now(),
        operational_intent_reference_exists=False,
        ussp_network_enabled=False,
    )
    assert result is ConformanceCheck.CONFORMANT


def test_c10_invalid_state():
    """Only states 2, 3, 4 are allowed for an active op-intent (C10)."""
    for invalid_state in (0, 1, 5, 6, 7, 8):
        result = check_operational_intent_reference_conformance(
            state=invalid_state,
            latest_telemetry_datetime=_now(),
            operational_intent_reference_exists=True,
        )
        assert result is ConformanceCheck.C10, f"state={invalid_state}"


def test_c9b_stale_telemetry():
    """Telemetry exists but is older than the 15-second liveness window -> C9b."""
    result = check_operational_intent_reference_conformance(
        state=2,
        latest_telemetry_datetime=_now() - timedelta(minutes=5),
        operational_intent_reference_exists=True,
    )
    assert result is ConformanceCheck.C9b


def test_c9a_absent_telemetry():
    """No telemetry at all -> C9a (contingent)."""
    result = check_operational_intent_reference_conformance(
        state=2,
        latest_telemetry_datetime=None,
        operational_intent_reference_exists=True,
    )
    assert result is ConformanceCheck.C9a
