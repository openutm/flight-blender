"""Tests for example_plugins: HelloWorldFuser, HelloWorldEngine, HelloWorldVolumeGenerator."""

import pytest
import arrow
from geojson import Feature, FeatureCollection

from flight_blender.plugins.examples.hello_world_engine import HelloWorldEngine
from flight_blender.plugins.examples.hello_world_fuser import HelloWorldFuser
from flight_blender.plugins.examples.hello_world_volume_generator import HelloWorldVolumeGenerator
from flight_blender.flight_declarations.data_definitions import DeconflictionRequest
from flight_blender.flight_declarations.models import FlightDeclaration
from flight_blender.flight_feed.data_definitions import SingleAirtrafficObservation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_obs(icao="AA1234", ts_offset=0, lat=51.5, lon=-0.1, alt=100.0):
    """Create a fresh SingleAirtrafficObservation with a non-stale timestamp."""
    now_ts = arrow.utcnow().int_timestamp
    return SingleAirtrafficObservation(
        icao_address=icao,
        traffic_source=1,
        source_type=0,
        lat_dd=lat,
        lon_dd=lon,
        altitude_mm=alt,
        timestamp=now_ts + ts_offset,
        metadata={},
    )


def _make_stale_obs(icao="BB5678"):
    stale_ts = arrow.utcnow().int_timestamp - 9999  # very old
    return SingleAirtrafficObservation(
        icao_address=icao,
        traffic_source=1,
        source_type=0,
        lat_dd=10.0,
        lon_dd=20.0,
        altitude_mm=50.0,
        timestamp=stale_ts,
        metadata={},
    )


# ---------------------------------------------------------------------------
# HelloWorldFuser
# ---------------------------------------------------------------------------


class TestHelloWorldFuser:
    def test_empty_observations_returns_empty(self):
        fuser = HelloWorldFuser(session_id="sess1", raw_observations=[])
        result = fuser.generate_track_messages()
        assert result == []

    def test_single_obs_returns_one_track(self):
        obs = _make_obs()
        fuser = HelloWorldFuser(session_id="sess1", raw_observations=[obs])
        result = fuser.generate_track_messages()
        assert len(result) == 1
        assert result[0].unique_aircraft_identifier == "AA1234"

    def test_stale_obs_filtered_out(self):
        stale = _make_stale_obs()
        fuser = HelloWorldFuser(session_id="sess1", raw_observations=[stale])
        result = fuser.generate_track_messages()
        assert result == []

    def test_deduplication_keeps_latest(self):
        """Two observations for the same ICAO: keep the one with the higher timestamp."""
        older = _make_obs(icao="CC0001", ts_offset=-10)
        newer = _make_obs(icao="CC0001", ts_offset=0)
        fuser = HelloWorldFuser(session_id="sess1", raw_observations=[older, newer])
        result = fuser.generate_track_messages()
        assert len(result) == 1

    def test_multiple_icao_returns_multiple_tracks(self):
        obs1 = _make_obs(icao="DD0001")
        obs2 = _make_obs(icao="DD0002")
        fuser = HelloWorldFuser(session_id="sess1", raw_observations=[obs1, obs2])
        result = fuser.generate_track_messages()
        assert len(result) == 2
        icaos = {t.unique_aircraft_identifier for t in result}
        assert icaos == {"DD0001", "DD0002"}

    def test_mix_stale_and_fresh(self):
        fresh = _make_obs(icao="EE0001")
        stale = _make_stale_obs(icao="EE0002")
        fuser = HelloWorldFuser(session_id="sess1", raw_observations=[fresh, stale])
        result = fuser.generate_track_messages()
        assert len(result) == 1
        assert result[0].unique_aircraft_identifier == "EE0001"

    def test_track_message_fields(self):
        obs = _make_obs(icao="FF0001", lat=48.0, lon=16.0, alt=200.0)
        fuser = HelloWorldFuser(session_id="sess1", raw_observations=[obs])
        result = fuser.generate_track_messages()
        msg = result[0]
        assert msg.state.position.lat == 48.0
        assert msg.state.position.lng == 16.0
        assert msg.state.position.alt == 200.0
        assert msg.source == "hello_world_fuser"
        assert msg.track_state == "active"


# ---------------------------------------------------------------------------
# HelloWorldEngine (requires DB)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestHelloWorldEngine:
    def _make_declaration(self, start_offset_s=0, end_offset_s=3600, state=1):
        now = arrow.utcnow()
        fd = FlightDeclaration.objects.create(
            flight_declaration_raw_geojson="{}",
            bounds="-1.0,-1.0,1.0,1.0",
            start_datetime=now.shift(seconds=start_offset_s).datetime,
            end_datetime=now.shift(seconds=end_offset_s).datetime,
            type_of_operation=0,
            originating_party="TEST",
            state=state,
        )
        return fd

    def test_no_conflict_returns_approved(self):
        engine = HelloWorldEngine()
        now = arrow.utcnow()
        req = DeconflictionRequest(
            start_datetime=now.shift(hours=10).datetime,
            end_datetime=now.shift(hours=11).datetime,
            view_box=[-1.0, -1.0, 1.0, 1.0],
            declaration_id=None,
            ussp_network_enabled=False,
        )
        result = engine.check_deconfliction(req)
        assert result.is_approved is True

    def test_time_conflict_returns_rejected(self):
        now = arrow.utcnow()
        self._make_declaration(start_offset_s=-600, end_offset_s=3600, state=1)

        engine = HelloWorldEngine()
        req = DeconflictionRequest(
            start_datetime=now.shift(seconds=-300).datetime,
            end_datetime=now.shift(seconds=1800).datetime,
            view_box=[-1.0, -1.0, 1.0, 1.0],
            declaration_id=None,
            ussp_network_enabled=False,
        )
        result = engine.check_deconfliction(req)
        assert result.is_approved is False
        assert result.all_relevant_declarations != []

    def test_exclude_self_declaration(self):
        now = arrow.utcnow()
        fd = self._make_declaration(start_offset_s=-600, end_offset_s=3600, state=1)

        engine = HelloWorldEngine()
        req = DeconflictionRequest(
            start_datetime=now.shift(seconds=-300).datetime,
            end_datetime=now.shift(seconds=1800).datetime,
            view_box=[-1.0, -1.0, 1.0, 1.0],
            declaration_id=str(fd.id),
            ussp_network_enabled=False,
        )
        # The declaration being checked is excluded → no conflict
        result = engine.check_deconfliction(req)
        assert result.is_approved is True

    def test_inactive_state_not_conflicting(self):
        """Declarations in state 0 (Not Submitted) should not conflict."""
        now = arrow.utcnow()
        self._make_declaration(start_offset_s=-600, end_offset_s=3600, state=0)

        engine = HelloWorldEngine()
        req = DeconflictionRequest(
            start_datetime=now.shift(seconds=-300).datetime,
            end_datetime=now.shift(seconds=1800).datetime,
            view_box=[-1.0, -1.0, 1.0, 1.0],
            declaration_id=None,
            ussp_network_enabled=False,
        )
        result = engine.check_deconfliction(req)
        assert result.is_approved is True

    def test_ussp_network_enabled_sets_not_submitted(self):
        now = arrow.utcnow()
        req = DeconflictionRequest(
            start_datetime=now.shift(hours=10).datetime,
            end_datetime=now.shift(hours=11).datetime,
            view_box=[-1.0, -1.0, 1.0, 1.0],
            declaration_id=None,
            ussp_network_enabled=True,
        )
        engine = HelloWorldEngine()
        result = engine.check_deconfliction(req)
        # With USSP network enabled and no conflict, state should be NOT_SUBMITTED (0)
        assert result.is_approved is True
        assert result.declaration_state == 0


# ---------------------------------------------------------------------------
# HelloWorldVolumeGenerator
# ---------------------------------------------------------------------------


def _make_linestring_fc(start="2026-01-01T10:00:00Z", end="2026-01-01T10:30:00Z"):
    return FeatureCollection(
        features=[
            Feature(
                geometry={"type": "LineString", "coordinates": [[0.0, 0.0], [0.001, 0.001], [0.002, 0.0]]},
                properties={
                    "id": 1,
                    "max_altitude": {"meters": 100.0},
                    "min_altitude": {"meters": 0.0},
                },
            )
        ]
    )


def _make_polygon_fc():
    return FeatureCollection(
        features=[
            Feature(
                geometry={
                    "type": "Polygon",
                    "coordinates": [[[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]]],
                },
                properties={
                    "max_altitude": {"meters": 120.0},
                    "min_altitude": {"meters": 20.0},
                },
            )
        ]
    )


class TestHelloWorldVolumeGenerator:
    def test_linestring_returns_v4d_list(self):
        gen = HelloWorldVolumeGenerator(
            default_uav_speed_m_per_s=5.5,
            default_uav_climb_rate_m_per_s=1.5,
            default_uav_descent_rate_m_per_s=1.5,
        )
        fc = _make_linestring_fc()
        result = gen.build_v4d_from_geojson(fc, "2026-01-01T10:00:00Z", "2026-01-01T10:30:00Z")
        assert len(result) >= 1
        # Check v4d structure
        v4d = result[0]
        assert v4d.volume.altitude_lower.value == 0.0
        assert v4d.volume.altitude_upper.value == 100.0

    def test_polygon_feature_returns_v4d(self):
        gen = HelloWorldVolumeGenerator(
            default_uav_speed_m_per_s=5.5,
            default_uav_climb_rate_m_per_s=1.5,
            default_uav_descent_rate_m_per_s=1.5,
        )
        fc = _make_polygon_fc()
        result = gen.build_v4d_from_geojson(fc, "2026-01-01T10:00:00Z", "2026-01-01T10:30:00Z")
        assert len(result) == 1

    def test_multiple_features_proportional_time(self):
        fc = FeatureCollection(
            features=[
                Feature(
                    geometry={"type": "LineString", "coordinates": [[0.0, 0.0], [0.01, 0.0]]},
                    properties={"max_altitude": {"meters": 80.0}, "min_altitude": {"meters": 10.0}},
                ),
                Feature(
                    geometry={"type": "LineString", "coordinates": [[0.01, 0.0], [0.02, 0.0]]},
                    properties={"max_altitude": {"meters": 80.0}, "min_altitude": {"meters": 10.0}},
                ),
            ]
        )
        gen = HelloWorldVolumeGenerator(
            default_uav_speed_m_per_s=5.5,
            default_uav_climb_rate_m_per_s=1.5,
            default_uav_descent_rate_m_per_s=1.5,
        )
        result = gen.build_v4d_from_geojson(fc, "2026-01-01T10:00:00Z", "2026-01-01T11:00:00Z")
        assert len(result) == 2
        # Last segment ends at or before end_datetime
        end_dt = arrow.get("2026-01-01T11:00:00Z")
        assert arrow.get(result[-1].time_end.value) <= end_dt

    def test_empty_feature_collection(self):
        gen = HelloWorldVolumeGenerator(
            default_uav_speed_m_per_s=5.5,
            default_uav_climb_rate_m_per_s=1.5,
            default_uav_descent_rate_m_per_s=1.5,
        )
        fc = FeatureCollection(features=[])
        result = gen.build_v4d_from_geojson(fc, "2026-01-01T10:00:00Z", "2026-01-01T10:30:00Z")
        assert result == []

    def test_feature_length_m_for_linestring(self):
        gen = HelloWorldVolumeGenerator(5.5, 1.5, 1.5)
        feat = Feature(
            geometry={"type": "LineString", "coordinates": [[0.0, 0.0], [0.01, 0.0]]},
            properties={},
        )
        length = gen._feature_length_m(feat)
        assert length > 0
