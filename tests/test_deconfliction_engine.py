"""
Unit tests for the strategic deconfliction engine.

Pure-logic tests: no DB, no network. They cover the geometric / temporal /
altitude overlap helpers, the full 4D deconfliction decision, the
DefaultDeconflictionEngine strategic + bbox checks, and the fail-closed
decision mapping (``resolve_decision``).
"""

from datetime import datetime, timedelta, timezone

from flight_blender.services.deconfliction import (
    DeconflictionRequest,
    DefaultDeconflictionEngine,
    check_altitude_overlap,
    check_polygon_intersection,
    check_time_overlap,
    deconflict_operational_intent,
    resolve_decision,
)

# A square around (-122.4, 37.7) .. (-122.3, 37.8)
_SQUARE_A = [[-122.4, 37.7], [-122.4, 37.8], [-122.3, 37.8], [-122.3, 37.7], [-122.4, 37.7]]
# Overlapping square (shifted but still overlapping A)
_SQUARE_A_OVERLAP = [[-122.35, 37.75], [-122.35, 37.85], [-122.25, 37.85], [-122.25, 37.75], [-122.35, 37.75]]
# Disjoint square far away
_SQUARE_FAR = [[-100.0, 30.0], [-100.0, 30.1], [-99.9, 30.1], [-99.9, 30.0], [-100.0, 30.0]]


def _now():
    return datetime.now(timezone.utc)


# ── Pure helpers ────────────────────────────────────────────────────────────


class TestPolygonIntersection:
    def test_overlapping_polygons(self):
        assert check_polygon_intersection(_SQUARE_A, _SQUARE_A_OVERLAP) is True

    def test_disjoint_polygons(self):
        assert check_polygon_intersection(_SQUARE_A, _SQUARE_FAR) is False

    def test_identical_polygons(self):
        assert check_polygon_intersection(_SQUARE_A, _SQUARE_A) is True

    def test_degenerate_polygon(self):
        assert check_polygon_intersection(_SQUARE_A, [[-122.4, 37.7], [-122.4, 37.8]]) is False


class TestTimeOverlap:
    def test_overlapping_time(self):
        t0 = _now()
        assert check_time_overlap(t0, t0 + timedelta(hours=2), t0 + timedelta(hours=1), t0 + timedelta(hours=3)) is True

    def test_disjoint_time(self):
        t0 = _now()
        assert check_time_overlap(t0, t0 + timedelta(hours=1), t0 + timedelta(hours=2), t0 + timedelta(hours=3)) is False

    def test_touching_time_is_overlap(self):
        t0 = _now()
        assert check_time_overlap(t0, t0 + timedelta(hours=1), t0 + timedelta(hours=1), t0 + timedelta(hours=2)) is True


class TestAltitudeOverlap:
    def test_overlapping_altitude(self):
        assert check_altitude_overlap(50, 120, 100, 200) is True

    def test_disjoint_altitude(self):
        assert check_altitude_overlap(50, 120, 130, 200) is False

    def test_touching_altitude_is_overlap(self):
        assert check_altitude_overlap(50, 120, 120, 200) is True


# ── Full 4D deconfliction decision ──────────────────────────────────────────


class TestDeconflict4D:
    def _candidate(self, coords=None, start=None, end=None, min_alt=50, max_alt=120):
        t0 = _now()
        return {
            "coordinates": coords or _SQUARE_A,
            "start": start or t0,
            "end": end or (t0 + timedelta(hours=2)),
            "min_alt": min_alt,
            "max_alt": max_alt,
        }

    def test_conflict_when_overlap_in_space_time_altitude(self):
        cand = self._candidate()
        existing = [self._candidate(coords=_SQUARE_A_OVERLAP)]
        # clear == False means there IS a conflict
        assert deconflict_operational_intent(cand, existing) is False

    def test_clear_when_disjoint_in_space(self):
        cand = self._candidate()
        existing = [self._candidate(coords=_SQUARE_FAR)]
        assert deconflict_operational_intent(cand, existing) is True

    def test_clear_when_disjoint_in_time(self):
        t0 = _now()
        cand = self._candidate(start=t0, end=t0 + timedelta(hours=1))
        existing = [self._candidate(coords=_SQUARE_A_OVERLAP, start=t0 + timedelta(hours=2), end=t0 + timedelta(hours=3))]
        assert deconflict_operational_intent(cand, existing) is True

    def test_clear_when_disjoint_in_altitude(self):
        cand = self._candidate(min_alt=50, max_alt=120)
        existing = [self._candidate(coords=_SQUARE_A_OVERLAP, min_alt=200, max_alt=300)]
        assert deconflict_operational_intent(cand, existing) is True

    def test_clear_with_no_existing(self):
        assert deconflict_operational_intent(self._candidate(), []) is True

    def test_iso_string_times_supported(self):
        t0 = _now()
        cand = {
            "coordinates": _SQUARE_A,
            "start": t0.isoformat(),
            "end": (t0 + timedelta(hours=2)).isoformat(),
            "min_alt": 50,
            "max_alt": 120,
        }
        existing = [
            {
                "coordinates": _SQUARE_A_OVERLAP,
                "start": (t0 + timedelta(hours=1)).isoformat(),
                "end": (t0 + timedelta(hours=3)).isoformat(),
                "min_alt": 50,
                "max_alt": 120,
            }
        ]
        assert deconflict_operational_intent(cand, existing) is False


# ── Engine strategic (volume) check ─────────────────────────────────────────


class TestEngineStrategicCheck:
    def _vol(self, coords, min_alt=50, max_alt=120, hours_offset=0, duration=2):
        t0 = _now()
        start = t0 + timedelta(hours=hours_offset)
        return {
            "coordinates": coords,
            "min_alt": min_alt,
            "max_alt": max_alt,
            "start": start.isoformat(),
            "end": (start + timedelta(hours=duration)).isoformat(),
        }

    def _request(self, candidate_volume, existing_volumes, ussp=0):
        return DeconflictionRequest(
            candidate_volume=candidate_volume,
            prefetched_volumes=existing_volumes,
            ussp_network_enabled=ussp,
        )

    def test_conflict_rejects(self):
        engine = DefaultDeconflictionEngine()
        result = engine.check_deconfliction(self._request(self._vol(_SQUARE_A), [self._vol(_SQUARE_A_OVERLAP)]))
        assert result.is_approved is False
        assert result.declaration_state == 8  # Rejected

    def test_clear_accepts_when_no_network(self):
        engine = DefaultDeconflictionEngine()
        result = engine.check_deconfliction(self._request(self._vol(_SQUARE_A), [self._vol(_SQUARE_FAR)], ussp=0))
        assert result.is_approved is True
        assert result.declaration_state == 1  # Accepted (no network)

    def test_clear_pending_when_network_enabled(self):
        engine = DefaultDeconflictionEngine()
        result = engine.check_deconfliction(self._request(self._vol(_SQUARE_A), [self._vol(_SQUARE_FAR)], ussp=1))
        assert result.is_approved is True
        assert result.declaration_state == 0  # NotSubmitted until DSS responds


class TestEngineBboxMode:
    """When no candidate_volume is supplied, the engine runs the legacy Django
    bbox check against prefetched declarations/fences."""

    def test_bbox_conflict_rejected(self):
        engine = DefaultDeconflictionEngine()
        req = DeconflictionRequest(
            view_box=[-122.4, 37.7, -122.3, 37.8],
            prefetched_declarations=[{"id": "d1", "bounds": '{"minx": -122.35, "miny": 37.75, "maxx": -122.25, "maxy": 37.85}'}],
            ussp_network_enabled=0,
        )
        result = engine.check_deconfliction(req)
        assert result.is_approved is False
        assert result.declaration_state == 8

    def test_bbox_disjoint_accepted(self):
        engine = DefaultDeconflictionEngine()
        req = DeconflictionRequest(
            view_box=[-122.4, 37.7, -122.3, 37.8],
            prefetched_declarations=[{"id": "d1", "bounds": '{"minx": -100.0, "miny": 30.0, "maxx": -99.9, "maxy": 30.1}'}],
            ussp_network_enabled=0,
        )
        result = engine.check_deconfliction(req)
        assert result.is_approved is True
        assert result.declaration_state == 1

    def test_bbox_no_view_box_accepts(self):
        engine = DefaultDeconflictionEngine()
        result = engine.check_deconfliction(DeconflictionRequest(view_box=[], ussp_network_enabled=0))
        assert result.is_approved is True
        assert result.declaration_state == 1


# ── Fail-closed decision logic ──────────────────────────────────────────────


class TestFailClosedDecision:
    def test_engine_error_is_not_approved(self):
        decision = resolve_decision(engine_error=True, is_clear=True, ussp_network_enabled=0)
        assert decision.is_approved is False
        assert decision.declaration_state == 8  # Rejected, never accepted on error

    def test_engine_error_not_approved_even_with_network(self):
        decision = resolve_decision(engine_error=True, is_clear=True, ussp_network_enabled=1)
        assert decision.is_approved is False
        assert decision.declaration_state == 8

    def test_conflict_is_rejected(self):
        decision = resolve_decision(engine_error=False, is_clear=False, ussp_network_enabled=0)
        assert decision.is_approved is False
        assert decision.declaration_state == 8

    def test_clear_accepted_without_network(self):
        decision = resolve_decision(engine_error=False, is_clear=True, ussp_network_enabled=0)
        assert decision.is_approved is True
        assert decision.declaration_state == 1

    def test_clear_pending_with_network(self):
        decision = resolve_decision(engine_error=False, is_clear=True, ussp_network_enabled=1)
        assert decision.is_approved is True
        assert decision.declaration_state == 0
