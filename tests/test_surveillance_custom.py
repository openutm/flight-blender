"""Tests for flight_blender.surveillance custom_utils, custom_signals, and utils."""

import uuid
from dataclasses import asdict
from unittest.mock import MagicMock, patch

import pytest

from flight_blender.flight_feed.data_definitions import SingleAirtrafficObservation
from flight_blender.surveillance.custom_signals import (
    process_sensor_status_change,
    surveillance_sensor_failure_signal,
)
from flight_blender.surveillance.custom_utils import SpecializedTrafficDataFuser
from flight_blender.surveillance.data_definitions import ActiveTrack
from flight_blender.surveillance.models import (
    SurveillanceSensor,
    SurveillanceSensorFailureNotification,
)
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
# custom_signals.process_sensor_status_change
# ===========================================================================


@pytest.mark.django_db
class TestProcessSensorStatusChange:
    def _create_sensor(self):
        return SurveillanceSensor.objects.create(
            sensor_identifier=f"signal-test-sensor-{uuid.uuid4().hex[:8]}",
            sensor_type=12,
            is_active=True,
        )

    def test_sensor_not_found_logs_and_returns(self):
        missing_id = str(uuid.uuid4())
        # Should not raise; just log and return
        process_sensor_status_change(
            sender=None,
            sensor_id=missing_id,
            previous_status="operational",
            new_status="degraded",
            recovery_type=None,
        )

    def test_failure_status_creates_notification(self):
        sensor = self._create_sensor()
        initial_count = SurveillanceSensorFailureNotification.objects.count()

        process_sensor_status_change(
            sender=None,
            sensor_id=str(sensor.id),
            previous_status="operational",
            new_status="degraded",
            recovery_type=None,
        )

        assert SurveillanceSensorFailureNotification.objects.count() == initial_count + 1

    def test_recovery_status_creates_notification_with_recovery_label(self):
        sensor = self._create_sensor()

        process_sensor_status_change(
            sender=None,
            sensor_id=str(sensor.id),
            previous_status="degraded",
            new_status="operational",
            recovery_type="automatic",
        )

        notif = SurveillanceSensorFailureNotification.objects.filter(sensor=sensor).latest("created_at")
        assert "automatic recovery" in notif.message

    def test_outage_status_creates_notification(self):
        sensor = self._create_sensor()

        process_sensor_status_change(
            sender=None,
            sensor_id=str(sensor.id),
            previous_status="operational",
            new_status="outage",
            recovery_type=None,
        )

        notif = SurveillanceSensorFailureNotification.objects.filter(sensor=sensor).latest("created_at")
        assert "outage" in notif.message

    def test_signal_fires_via_send(self):
        sensor = self._create_sensor()
        initial_count = SurveillanceSensorFailureNotification.objects.count()

        surveillance_sensor_failure_signal.send(
            sender="test",
            sensor_id=str(sensor.id),
            previous_status="operational",
            new_status="degraded",
            recovery_type=None,
        )

        assert SurveillanceSensorFailureNotification.objects.count() == initial_count + 1


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
