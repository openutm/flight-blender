"""Tests for flight_blender.surveillance custom_utils, custom_signals, and utils."""

from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from flight_blender.flight_feed.data_definitions import SingleAirtrafficObservation
from flight_blender.surveillance.custom_utils import SpecializedTrafficDataFuser
from flight_blender.surveillance.data_definitions import ActiveTrack
from flight_blender.surveillance.utils import TrafficDataFuser


# ===========================================================================
# custom_utils.SpecializedTrafficDataFuser
# ===========================================================================


class TestSpecializedTrafficDataFuser:
    def test_instantiation(self):
        fuser = SpecializedTrafficDataFuser(raw_observations=[])
        assert fuser.raw_observations == []

    def test_instantiation_with_observations(self):
        obs = MagicMock()
        fuser = SpecializedTrafficDataFuser(raw_observations=[obs])
        assert len(fuser.raw_observations) == 1

    def test_fuse_raw_observations_raises_not_implemented(self):
        fuser = SpecializedTrafficDataFuser(raw_observations=[])
        with pytest.raises(NotImplementedError):
            fuser.fuse_raw_observations()

    def test_generate_track_messages_raises_not_implemented(self):
        fuser = SpecializedTrafficDataFuser(raw_observations=[])
        with pytest.raises(NotImplementedError):
            fuser.generate_track_messages(fused_observations=[])


# ===========================================================================
# utils.TrafficDataFuser
# ===========================================================================


class TestTrafficDataFuserInstantiation:
    def test_instantiation(self):
        with patch("flight_blender.surveillance.utils.RedisStreamOperations"):
            fuser = TrafficDataFuser(session_id="test-session", raw_observations=[])
            assert fuser.session_id == "test-session"
            assert fuser.raw_observations == []
            assert fuser.SDSP_IDENTIFIER == "SDSP123"

    def test_fuse_raw_observations_returns_same_list(self):
        obs = MagicMock()
        with patch("flight_blender.surveillance.utils.RedisStreamOperations"):
            fuser = TrafficDataFuser(session_id="test-session", raw_observations=[obs])
            result = fuser._fuse_raw_observations()
            assert result == [obs]

    def test_generate_active_tracks_new_track(self):
        obs = SingleAirtrafficObservation(
            icao_address="AABBCC",
            traffic_source=1,
            source_type=0,
            lat_dd=51.5,
            lon_dd=-0.1,
            altitude_mm=100.0,
            timestamp=0,
            metadata={},
        )

        mock_redis = MagicMock()
        mock_redis.check_active_track_exists.return_value = False

        with patch("flight_blender.surveillance.utils.RedisStreamOperations", return_value=mock_redis):
            fuser = TrafficDataFuser(session_id="test-session", raw_observations=[obs])
            # Should not raise
            fuser._generate_active_tracks([obs])
            mock_redis.add_active_track_to_session.assert_called_once()

    def test_generate_active_tracks_existing_track(self):
        obs = SingleAirtrafficObservation(
            icao_address="AABBCC",
            traffic_source=1,
            source_type=0,
            lat_dd=51.5,
            lon_dd=-0.1,
            altitude_mm=100.0,
            timestamp=0,
            metadata={},
        )

        existing_track = ActiveTrack(
            session_id="test-session",
            unique_aircraft_identifier="AABBCC",
            last_updated_timestamp="2026-01-01T00:00:00Z",
            observations=[asdict(obs)],
        )
        mock_redis = MagicMock()
        mock_redis.check_active_track_exists.return_value = True
        mock_redis.get_active_track.return_value = existing_track

        with patch("flight_blender.surveillance.utils.RedisStreamOperations", return_value=mock_redis):
            fuser = TrafficDataFuser(session_id="test-session", raw_observations=[obs])
            fuser._generate_active_tracks([obs])
            mock_redis.update_active_track.assert_called_once()
