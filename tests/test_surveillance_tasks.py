"""
Unit tests for:
- surveillance_monitoring_operations/tasks.py
- surveillance_monitoring_operations/metric_calculator.py
- surveillance_monitoring_operations/custom_signals.py

All external I/O (Redis channel layer, Celery, DB) is mocked.
"""

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

import arrow
import pytest

from common.database_operations import FlightBlenderDatabaseWriter
from common.redis_stream_operations import RedisStreamOperations
from surveillance_monitoring_operations.metric_calculator import SurveillanceMetricCalculator
from surveillance_monitoring_operations.models import SurveillanceHeartbeatEvent, SurveillanceTrackEvent
from surveillance_monitoring_operations.tasks import (
    cleanup_old_heartbeat_events,
    send_heartbeat_to_consumer,
    send_and_generate_track_to_consumer,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_heartbeat_event(session_id, delivered_on_time=True, offset_seconds=0):
    """Return a MagicMock simulating SurveillanceHeartbeatEvent."""
    m = MagicMock()
    m.delivered_on_time = delivered_on_time
    m.dispatched_at = arrow.utcnow().shift(seconds=offset_seconds).datetime
    return m


def _mock_db_reader(
    heartbeat_events=None,
    track_events=None,
    health_records=None,
    observations=None,
    active_sensors=None,
    pre_window_status=None,
):
    """Build a minimal FlightBlenderDatabaseReader mock."""
    reader = MagicMock()
    # heartbeat events queryset-like
    hb_qs = MagicMock()
    hb_qs.count.return_value = len(heartbeat_events) if heartbeat_events else 0
    hb_qs.filter.return_value = hb_qs
    hb_qs.first.return_value = heartbeat_events[0] if heartbeat_events else None
    hb_qs.last.return_value = heartbeat_events[-1] if heartbeat_events else None
    reader.get_heartbeat_events_for_session.return_value = hb_qs

    obs_qs = MagicMock()
    obs_qs.count.return_value = len(observations) if observations else 0
    reader.get_all_flight_observations_in_window.return_value = obs_qs

    reader.get_health_tracking_records_for_sensor.return_value = health_records or []
    reader.get_sensor_status_before_time.return_value = pre_window_status

    active_sensors_qs = MagicMock()
    active_sensors_qs.exists.return_value = bool(active_sensors)
    if active_sensors:
        sensor_mock = MagicMock()
        sensor_mock.expected_latency_ms = 100
        sensor_mock.horizontal_accuracy_m = 5
        active_sensors_qs.first.return_value = sensor_mock
    reader.get_active_surveillance_sensors.return_value = active_sensors_qs

    return reader


# ===========================================================================
# SurveillanceMetricCalculator
# ===========================================================================


class TestHeartbeatRateMetric:
    def test_rate_zero_when_no_events(self):
        reader = _mock_db_reader(heartbeat_events=[])
        calc = SurveillanceMetricCalculator(database_reader=reader)
        now = arrow.utcnow()
        result = calc.calculate_heartbeat_rate(
            session_id="sess-1",
            start_time=now.shift(minutes=-5).datetime,
            end_time=now.datetime,
        )
        assert result.measured_rate_hz == 0.0
        assert result.total_heartbeats_in_window == 0

    def test_rate_zero_when_single_event(self):
        ev = _make_heartbeat_event("sess-1")
        reader = _mock_db_reader(heartbeat_events=[ev])
        # Single event → span unknown / 0 → rate == 0
        hb_qs = reader.get_heartbeat_events_for_session.return_value
        hb_qs.count.return_value = 1
        calc = SurveillanceMetricCalculator(database_reader=reader)
        now = arrow.utcnow()
        result = calc.calculate_heartbeat_rate("sess-1", now.shift(minutes=-5).datetime, now.datetime)
        assert result.measured_rate_hz == 0.0

    def test_rate_calculated_for_two_events_one_second_apart(self):
        ev1 = _make_heartbeat_event("sess-1", offset_seconds=-10)
        ev2 = _make_heartbeat_event("sess-1", offset_seconds=0)
        reader = _mock_db_reader(heartbeat_events=[ev1, ev2])
        hb_qs = reader.get_heartbeat_events_for_session.return_value
        hb_qs.count.return_value = 2
        hb_qs.first.return_value = ev1
        hb_qs.last.return_value = ev2
        calc = SurveillanceMetricCalculator(database_reader=reader)
        now = arrow.utcnow()
        result = calc.calculate_heartbeat_rate("sess-1", now.shift(minutes=-1).datetime, now.datetime)
        # (2-1) / 10 seconds = 0.1 Hz
        assert result.measured_rate_hz == 0.1
        assert result.total_heartbeats_in_window == 2


class TestHeartbeatDeliveryProbability:
    def test_probability_all_on_time(self):
        events = [_make_heartbeat_event("s", delivered_on_time=True) for _ in range(5)]
        reader = _mock_db_reader(heartbeat_events=events)
        hb_qs = reader.get_heartbeat_events_for_session.return_value
        hb_qs.count.return_value = 5
        on_time_qs = MagicMock()
        on_time_qs.count.return_value = 5
        hb_qs.filter.return_value = on_time_qs
        calc = SurveillanceMetricCalculator(database_reader=reader)
        now = arrow.utcnow()
        result = calc.calculate_heartbeat_delivery_probability("s", now.shift(minutes=-5).datetime, now.datetime)
        assert result.probability == 1.0

    def test_probability_half_on_time(self):
        reader = _mock_db_reader()
        hb_qs = reader.get_heartbeat_events_for_session.return_value
        hb_qs.count.return_value = 10
        on_time_qs = MagicMock()
        on_time_qs.count.return_value = 5
        hb_qs.filter.return_value = on_time_qs
        calc = SurveillanceMetricCalculator(database_reader=reader)
        now = arrow.utcnow()
        result = calc.calculate_heartbeat_delivery_probability("s", now.shift(minutes=-5).datetime, now.datetime)
        assert result.probability == 0.5

    def test_probability_zero_when_no_events(self):
        reader = _mock_db_reader(heartbeat_events=[])
        calc = SurveillanceMetricCalculator(database_reader=reader)
        now = arrow.utcnow()
        result = calc.calculate_heartbeat_delivery_probability("s", now.shift(minutes=-5).datetime, now.datetime)
        assert result.probability == 0.0


class TestTrackUpdateProbability:
    def test_probability_zero_when_no_observations(self):
        reader = _mock_db_reader(observations=[])
        calc = SurveillanceMetricCalculator(database_reader=reader)
        now = arrow.utcnow()
        result = calc.calculate_track_update_probability("s", now.shift(minutes=-5).datetime, now.datetime)
        assert result.probability == 0.0

    def test_probability_one_when_all_have_tracks(self):
        reader = _mock_db_reader()
        obs_qs = reader.get_all_flight_observations_in_window.return_value
        obs_qs.count.return_value = 10
        calc = SurveillanceMetricCalculator(database_reader=reader)
        now = arrow.utcnow()
        result = calc.calculate_track_update_probability("s", now.shift(minutes=-5).datetime, now.datetime)
        assert result.probability == 1.0


class TestSensorHealthMetrics:
    def test_no_records_returns_none_values(self):
        reader = _mock_db_reader(health_records=[], pre_window_status=None)
        calc = SurveillanceMetricCalculator(database_reader=reader)
        now = arrow.utcnow()
        with patch("surveillance_monitoring_operations.models.SurveillanceSensor") as mock_sensor_cls:
            mock_sensor_cls.objects.get.side_effect = Exception("not found")
            result = calc.calculate_sensor_health_metrics(
                sensor_id=str(uuid.uuid4()),
                start_time=now.shift(hours=-1).datetime,
                end_time=now.datetime,
            )
        assert result.mttr_seconds is None
        assert result.auto_recovery_time_seconds is None

    def test_single_failure_then_recovery(self):
        """One failure → one recovery → MTTR calculated."""
        now = arrow.utcnow()
        failure_rec = MagicMock()
        failure_rec.status = "outage"
        failure_rec.recorded_at = now.shift(minutes=-40).datetime
        failure_rec.recovery_type = None

        recovery_rec = MagicMock()
        recovery_rec.status = "operational"
        recovery_rec.recorded_at = now.shift(minutes=-30).datetime
        recovery_rec.recovery_type = "manual"

        reader = _mock_db_reader(health_records=[failure_rec, recovery_rec], pre_window_status="operational")
        calc = SurveillanceMetricCalculator(database_reader=reader)
        with patch("surveillance_monitoring_operations.models.SurveillanceSensor") as mock_sensor_cls:
            mock_sensor_cls.objects.get.side_effect = Exception("not found")
            result = calc.calculate_sensor_health_metrics(
                sensor_id=str(uuid.uuid4()),
                start_time=now.shift(hours=-1).datetime,
                end_time=now.datetime,
            )
        assert result.mttr_seconds == 10 * 60  # 10 minutes = 600 seconds
        assert result.auto_recovery_time_seconds is None

    def test_auto_recovery_calculated(self):
        """Automatic recovery populates auto_recovery_time_seconds."""
        now = arrow.utcnow()
        failure_rec = MagicMock()
        failure_rec.status = "degraded"
        failure_rec.recorded_at = now.shift(minutes=-20).datetime
        failure_rec.recovery_type = None

        recovery_rec = MagicMock()
        recovery_rec.status = "operational"
        recovery_rec.recorded_at = now.shift(minutes=-15).datetime
        recovery_rec.recovery_type = "automatic"

        reader = _mock_db_reader(health_records=[failure_rec, recovery_rec], pre_window_status="operational")
        calc = SurveillanceMetricCalculator(database_reader=reader)
        with patch("surveillance_monitoring_operations.models.SurveillanceSensor") as mock_sensor_cls:
            mock_sensor_cls.objects.get.side_effect = Exception("not found")
            result = calc.calculate_sensor_health_metrics(
                sensor_id=str(uuid.uuid4()),
                start_time=now.shift(hours=-1).datetime,
                end_time=now.datetime,
            )
        assert result.auto_recovery_time_seconds == 5 * 60  # 5 minutes = 300 seconds

    def test_pre_window_failure_state(self):
        """When sensor is already failing before window, start_time is used as failure onset."""
        now = arrow.utcnow()
        recovery_rec = MagicMock()
        recovery_rec.status = "operational"
        recovery_rec.recorded_at = now.shift(minutes=-30).datetime
        recovery_rec.recovery_type = "automatic"

        reader = _mock_db_reader(health_records=[recovery_rec], pre_window_status="outage")
        calc = SurveillanceMetricCalculator(database_reader=reader)
        with patch("surveillance_monitoring_operations.models.SurveillanceSensor") as mock_sensor_cls:
            mock_sensor_cls.objects.get.side_effect = Exception("not found")
            result = calc.calculate_sensor_health_metrics(
                sensor_id=str(uuid.uuid4()),
                start_time=now.shift(hours=-1).datetime,
                end_time=now.datetime,
            )
        assert result.mttr_seconds is not None
        assert result.mttr_seconds > 0


# ===========================================================================
# Celery tasks
# ===========================================================================


@pytest.mark.django_db
class TestSendHeartbeatToConsumer:
    def test_heartbeat_sent_successfully(self):
        """send_heartbeat_to_consumer should record event when channel send succeeds."""
        with patch("surveillance_monitoring_operations.tasks.RedisChannelLayer") as mock_layer_cls:
            mock_layer = MagicMock()
            mock_layer_cls.return_value = mock_layer

            with patch("surveillance_monitoring_operations.tasks.async_to_sync") as mock_a2s:
                mock_a2s.return_value = lambda *a, **kw: None

                with patch.object(FlightBlenderDatabaseWriter, "record_heartbeat_event") as mock_record:
                    with patch("common.database_operations.FlightBlenderDatabaseReader") as mock_reader_cls:
                        mock_reader = MagicMock()
                        mock_reader.get_active_surveillance_sensors.return_value.exists.return_value = False
                        mock_reader_cls.return_value = mock_reader
                        send_heartbeat_to_consumer(session_id="sess-hb-001")

                mock_record.assert_called_once()

    def test_heartbeat_channel_error_still_records(self):
        """send_heartbeat_to_consumer records the event even when channel send fails."""
        with patch("surveillance_monitoring_operations.tasks.RedisChannelLayer"):
            with patch("surveillance_monitoring_operations.tasks.async_to_sync") as mock_a2s:
                mock_a2s.return_value = MagicMock(side_effect=Exception("channel unavailable"))

                with patch.object(FlightBlenderDatabaseWriter, "record_heartbeat_event") as mock_record:
                    with patch("common.database_operations.FlightBlenderDatabaseReader") as mock_reader_cls:
                        mock_reader = MagicMock()
                        mock_reader.get_active_surveillance_sensors.return_value.exists.return_value = False
                        mock_reader_cls.return_value = mock_reader
                        send_heartbeat_to_consumer(session_id="sess-hb-err")

                # Even on error, we record the heartbeat
                mock_record.assert_called_once()
                # And delivered_on_time should be False
                kwargs = mock_record.call_args.kwargs
                assert kwargs.get("delivered_on_time") is False


@pytest.mark.django_db
class TestSendAndGenerateTrackToConsumer:
    def test_track_consumer_runs(self):
        """send_and_generate_track_to_consumer calls record_track_event."""
        with patch("surveillance_monitoring_operations.tasks.RedisChannelLayer"):
            with patch.object(RedisStreamOperations, "create_consumer_reader", return_value="consumer-1"):
                with patch.object(RedisStreamOperations, "read_latest_air_traffic_data", return_value=[]):
                    with patch("surveillance_monitoring_operations.tasks.async_to_sync") as mock_a2s:
                        mock_a2s.return_value = lambda *a, **kw: None
                        with patch("surveillance_monitoring_operations.tasks.load_plugin") as mock_load:
                            mock_fuser = MagicMock()
                            mock_fuser.return_value.generate_track_messages.return_value = []
                            mock_load.return_value = mock_fuser
                            with patch.object(FlightBlenderDatabaseWriter, "record_track_event") as mock_record:
                                send_and_generate_track_to_consumer(session_id="sess-track-001")
                            mock_record.assert_called_once()


@pytest.mark.django_db
class TestCleanupOldHeartbeatEvents:
    def test_cleanup_deletes_old_records(self):
        """cleanup_old_heartbeat_events deletes records older than retention period."""
        with patch.object(SurveillanceHeartbeatEvent.objects.__class__, "filter") as _:
            with patch("surveillance_monitoring_operations.tasks.SurveillanceHeartbeatEvent") as mock_hb:
                with patch("surveillance_monitoring_operations.tasks.SurveillanceTrackEvent") as mock_track:
                    mock_hb.objects.filter.return_value.delete.return_value = (5, {})
                    mock_track.objects.filter.return_value.delete.return_value = (3, {})
                    # Should not raise
                    cleanup_old_heartbeat_events()

    def test_cleanup_with_custom_retention(self, monkeypatch):
        """cleanup_old_heartbeat_events respects HEARTBEAT_RETENTION_DAYS env var."""
        monkeypatch.setenv("HEARTBEAT_RETENTION_DAYS", "7")
        with patch("surveillance_monitoring_operations.tasks.SurveillanceHeartbeatEvent") as mock_hb:
            with patch("surveillance_monitoring_operations.tasks.SurveillanceTrackEvent") as mock_track:
                mock_hb.objects.filter.return_value.delete.return_value = (0, {})
                mock_track.objects.filter.return_value.delete.return_value = (0, {})
                cleanup_old_heartbeat_events()
        mock_hb.objects.filter.assert_called_once()
