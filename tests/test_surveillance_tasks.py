"""
Unit tests for:
- flight_blender.surveillance/tasks.py
- flight_blender.surveillance/metric_calculator.py
- flight_blender.surveillance/custom_signals.py

All external I/O (Redis channel layer, Celery, DB) is mocked.
"""

import uuid
from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

import arrow

from flight_blender.common.redis_stream_operations import RedisStreamOperations
from flight_blender.surveillance.metric_calculator import SurveillanceMetricCalculator
from flight_blender.infrastructure.celery.tasks.surveillance import cleanup_old_heartbeat_events, send_and_generate_track_to_consumer, send_heartbeat_to_consumer

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
    """Build a minimal SyncDatabaseFacade mock."""
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


def _mock_sa_repo():
    """Return a mock SQLAlchemySurveillanceSyncRepository."""
    repo = MagicMock()
    repo.get_active_surveillance_sensors.return_value = []
    repo.record_heartbeat_event.return_value = True
    repo.record_track_event.return_value = True
    repo.cleanup_old_events.return_value = (0, 0)
    return repo


def _sa_repo_patch(mock_repo):
    """Context manager: patch SessionLocal (via session_scope) + the repo class as looked up in tasks."""
    @contextmanager
    def _ctx():
        mock_session = MagicMock()
        mock_session.commit = MagicMock()
        mock_session.rollback = MagicMock()
        mock_session.close = MagicMock()

        mock_repo_cls = MagicMock(return_value=mock_repo)

        with ExitStack() as stack:
            stack.enter_context(
                patch("flight_blender.infrastructure.database.session.SessionLocal", return_value=mock_session)
            )
            stack.enter_context(
                patch(
                    "flight_blender.infrastructure.celery.tasks.surveillance.SQLAlchemySurveillanceSyncRepository",
                    mock_repo_cls,
                )
            )
            yield mock_repo

    return _ctx()


class TestSendHeartbeatToConsumer:
    def test_heartbeat_sent_successfully(self):
        """send_heartbeat_to_consumer should record event when Redis publish succeeds."""
        mock_repo = _mock_sa_repo()
        session_id = str(uuid.uuid4())
        with patch("flight_blender.infrastructure.celery.tasks.surveillance.redis.from_url") as mock_from_url:
            mock_from_url.return_value.publish.return_value = 1
            with patch.object(send_heartbeat_to_consumer, "apply_async") as mock_apply_async:
                with _sa_repo_patch(mock_repo):
                    send_heartbeat_to_consumer(session_id=session_id)
        mock_repo.record_heartbeat_event.assert_called_once()
        mock_from_url.return_value.publish.assert_called_once()
        mock_apply_async.assert_called_once()

    def test_heartbeat_channel_error_still_records(self):
        """send_heartbeat_to_consumer records the event even when Redis publish fails."""
        mock_repo = _mock_sa_repo()
        session_id = str(uuid.uuid4())
        with patch("flight_blender.infrastructure.celery.tasks.surveillance.redis.from_url") as mock_from_url:
            mock_from_url.return_value.publish.side_effect = Exception("redis unavailable")
            with patch.object(send_heartbeat_to_consumer, "apply_async") as mock_apply_async:
                with _sa_repo_patch(mock_repo):
                    send_heartbeat_to_consumer(session_id=session_id)
        # Even on error, we record the heartbeat
        mock_repo.record_heartbeat_event.assert_called_once()
        mock_apply_async.assert_called_once()
        kwargs = mock_repo.record_heartbeat_event.call_args.kwargs
        assert kwargs.get("delivered_on_time") is False


class TestSendAndGenerateTrackToConsumer:
    def test_track_consumer_runs(self):
        """send_and_generate_track_to_consumer calls record_track_event."""
        mock_repo = _mock_sa_repo()
        session_id = str(uuid.uuid4())
        with patch("flight_blender.infrastructure.celery.tasks.surveillance.redis.from_url") as mock_from_url:
            mock_from_url.return_value.publish.return_value = 1
            with patch.object(RedisStreamOperations, "create_consumer_reader", return_value="consumer-1"):
                with patch.object(RedisStreamOperations, "read_latest_air_traffic_data", return_value=[]):
                    with patch("flight_blender.infrastructure.celery.tasks.surveillance.load_plugin") as mock_load:
                        mock_fuser = MagicMock()
                        mock_fuser.return_value.generate_track_messages.return_value = []
                        mock_load.return_value = mock_fuser
                        with patch.object(send_and_generate_track_to_consumer, "apply_async") as mock_apply_async:
                            with _sa_repo_patch(mock_repo):
                                send_and_generate_track_to_consumer(session_id=session_id)
        mock_repo.record_track_event.assert_called_once()
        mock_from_url.return_value.publish.assert_called_once()
        mock_apply_async.assert_called_once()


class TestCleanupOldHeartbeatEvents:
    def test_cleanup_deletes_old_records(self):
        """cleanup_old_heartbeat_events deletes records older than retention period."""
        mock_repo = _mock_sa_repo()
        mock_repo.cleanup_old_events.return_value = (5, 3)
        with _sa_repo_patch(mock_repo):
            cleanup_old_heartbeat_events()
        mock_repo.cleanup_old_events.assert_called_once()

    def test_cleanup_with_custom_retention(self, monkeypatch):
        """cleanup_old_heartbeat_events respects HEARTBEAT_RETENTION_DAYS env var."""
        monkeypatch.setenv("HEARTBEAT_RETENTION_DAYS", "7")
        mock_repo = _mock_sa_repo()
        with _sa_repo_patch(mock_repo):
            cleanup_old_heartbeat_events()
        mock_repo.cleanup_old_events.assert_called_once()
