"""Tests for common.database_operations – FlightBlenderDatabaseReader and FlightBlenderDatabaseWriter.

These tests exercise the CRUD wrapper methods directly against the test DB
(SQLite-in-memory via DATABASE_URL=sqlite://:memory:).
"""

import uuid
from datetime import datetime, timezone

import arrow
import pytest

from flight_blender.common.database_operations import FlightBlenderDatabaseReader, FlightBlenderDatabaseWriter
from flight_blender.flight_declarations.models import FlightDeclaration
from flight_blender.flight_feed.models import FlightObservation
from flight_blender.scd.scd_data_definitions import PartialCreateOperationalIntentReference
from flight_blender.surveillance.models import (
    SurveillanceSensor,
    SurveillanceSession,
    SurveillanceSensorHealth,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_flight_declaration(**kwargs):
    now = arrow.now()
    defaults = dict(
        operational_intent="{}",
        bounds="0.0,0.0,1.0,1.0",
        aircraft_id="UAV-TEST",
        state=0,
        originating_party="Test Party",
        start_datetime=now.shift(hours=1).datetime,
        end_datetime=now.shift(hours=2).datetime,
    )
    defaults.update(kwargs)
    fd = FlightDeclaration(**defaults)
    fd.save()
    return fd


def _make_flight_observation(session_id=None, traffic_source=1, **kwargs):
    defaults = dict(
        session_id=session_id or uuid.uuid4(),
        latitude_dd=51.5,
        longitude_dd=-0.1,
        altitude_mm=100.0,
        traffic_source=traffic_source,
        source_type=0,
        icao_address="AABBCC",
        metadata="{}",
    )
    defaults.update(kwargs)
    obs = FlightObservation(**defaults)
    obs.save()
    return obs


def _make_surveillance_sensor(identifier=None):
    sensor = SurveillanceSensor.objects.create(
        sensor_identifier=identifier or f"test-sensor-{uuid.uuid4().hex[:8]}",
        sensor_type=12,
        is_active=True,
    )
    return sensor


def _make_surveillance_session(valid_until=None):
    session = SurveillanceSession.objects.create(
        valid_until=valid_until or arrow.now().shift(hours=1).datetime,
    )
    return session


# ===========================================================================
# FlightBlenderDatabaseReader tests
# ===========================================================================


@pytest.mark.django_db
class TestDatabaseReaderFlightObservations:
    def test_get_flight_observations_returns_queryset(self):
        _make_flight_observation()
        reader = FlightBlenderDatabaseReader()
        one_min_ago = arrow.now().shift(minutes=-1)
        results = reader.get_flight_observations(after_datetime=one_min_ago)
        assert len(list(results)) >= 1

    def test_get_closest_flight_observation(self):
        _make_flight_observation()
        reader = FlightBlenderDatabaseReader()
        now = arrow.now()
        results = reader.get_closest_flight_observation_for_now(now=now)
        assert results is not None

    def test_get_flight_observation_objects(self):
        _make_flight_observation()
        reader = FlightBlenderDatabaseReader()
        results = reader.get_flight_observation_objects()
        assert len(list(results)) >= 1

    def test_get_temporal_flight_observations_by_session(self):
        sid = uuid.uuid4()
        _make_flight_observation(session_id=sid)
        reader = FlightBlenderDatabaseReader()
        results = reader.get_temporal_flight_observations_by_session(str(sid), arrow.now().shift(minutes=-1))
        assert len(list(results)) >= 1

    def test_get_flight_observations_by_session_excludes_source_11(self):
        sid = uuid.uuid4()
        _make_flight_observation(session_id=sid, traffic_source=11)
        _make_flight_observation(session_id=sid, traffic_source=1)
        reader = FlightBlenderDatabaseReader()
        results = list(reader.get_flight_observations_by_session(str(sid), arrow.now().shift(minutes=-1)))
        # Source 11 is excluded
        assert all(r["traffic_source"] != 11 for r in results)

    def test_get_all_flight_observations_in_window(self):
        _make_flight_observation()
        reader = FlightBlenderDatabaseReader()
        start = arrow.now().shift(minutes=-5).datetime
        end = arrow.now().shift(minutes=5).datetime
        results = reader.get_all_flight_observations_in_window(start, end)
        assert results.count() >= 1

    def test_get_latest_flight_observation_by_session_returns_none_when_empty(self):
        reader = FlightBlenderDatabaseReader()
        result = reader.get_latest_flight_observation_by_session(str(uuid.uuid4()))
        assert result is None

    def test_get_latest_flight_observation_by_session_returns_object(self):
        sid = uuid.uuid4()
        _make_flight_observation(session_id=sid)
        reader = FlightBlenderDatabaseReader()
        result = reader.get_latest_flight_observation_by_session(str(sid))
        assert result is not None

    def test_get_active_rid_observations_for_view(self):
        _make_flight_observation(traffic_source=11)
        reader = FlightBlenderDatabaseReader()
        start = arrow.now().shift(minutes=-5).datetime
        end = arrow.now().shift(minutes=5).datetime
        results = reader.get_active_rid_observations_for_view(start, end)
        assert results is not None

    def test_get_active_rid_observations_for_session(self):
        sid = uuid.uuid4()
        _make_flight_observation(session_id=sid, traffic_source=11)
        reader = FlightBlenderDatabaseReader()
        results = reader.get_active_rid_observations_for_session(str(sid))
        assert results is not None

    def test_get_active_rid_observations_for_session_between_interval(self):
        sid = uuid.uuid4()
        _make_flight_observation(session_id=sid, traffic_source=11)
        reader = FlightBlenderDatabaseReader()
        start = arrow.now().shift(minutes=-5).datetime
        end = arrow.now().shift(minutes=5).datetime
        results = reader.get_active_rid_observations_for_session_between_interval(start, end, str(sid))
        assert results is not None


@pytest.mark.django_db
class TestDatabaseReaderFlightDeclarations:
    def test_get_all_flight_declarations(self):
        _make_flight_declaration()
        reader = FlightBlenderDatabaseReader()
        result = reader.get_all_flight_declarations()
        assert len(list(result)) >= 1

    def test_check_flight_declaration_exists_true(self):
        fd = _make_flight_declaration()
        reader = FlightBlenderDatabaseReader()
        assert reader.check_flight_declaration_exists(str(fd.id)) is True

    def test_check_flight_declaration_exists_false(self):
        reader = FlightBlenderDatabaseReader()
        assert reader.check_flight_declaration_exists(str(uuid.uuid4())) is False

    def test_get_flight_declaration_by_id_found(self):
        fd = _make_flight_declaration()
        reader = FlightBlenderDatabaseReader()
        result = reader.get_flight_declaration_by_id(str(fd.id))
        assert result is not None
        assert result.id == fd.id

    def test_get_flight_declaration_by_id_not_found(self):
        reader = FlightBlenderDatabaseReader()
        result = reader.get_flight_declaration_by_id(str(uuid.uuid4()))
        assert result is None

    def test_check_active_activated_flights_exist(self):
        _make_flight_declaration(state=1)  # Accepted
        reader = FlightBlenderDatabaseReader()
        assert reader.check_active_activated_flights_exist() is True

    def test_check_active_activated_flights_exist_returns_false_when_none(self):
        reader = FlightBlenderDatabaseReader()
        assert reader.check_active_activated_flights_exist() is False

    def test_get_active_activated_flight_declarations(self):
        _make_flight_declaration(state=1)
        reader = FlightBlenderDatabaseReader()
        result = reader.get_active_activated_flight_declarations()
        assert len(list(result)) >= 1

    def test_get_current_flight_accepted_activated_declaration_ids(self):
        now = arrow.now()
        _make_flight_declaration(
            state=1,
            start_datetime=now.shift(minutes=1).datetime,
            end_datetime=now.shift(hours=2).datetime,
        )
        reader = FlightBlenderDatabaseReader()
        ids = reader.get_current_flight_accepted_activated_declaration_ids(now.isoformat())
        assert ids is not None

    def test_check_flight_declaration_active(self):
        now = arrow.now()
        fd = _make_flight_declaration(
            start_datetime=now.shift(minutes=-1).datetime,
            end_datetime=now.shift(hours=1).datetime,
        )
        reader = FlightBlenderDatabaseReader()
        assert reader.check_flight_declaration_active(str(fd.id), now.datetime) is True

    def test_check_flight_declaration_active_false(self):
        now = arrow.now()
        fd = _make_flight_declaration(
            start_datetime=now.shift(hours=-3).datetime,
            end_datetime=now.shift(hours=-1).datetime,
        )
        reader = FlightBlenderDatabaseReader()
        assert reader.check_flight_declaration_active(str(fd.id), now.datetime) is False

    def test_check_composite_operational_intent_exists_false(self):
        fd = _make_flight_declaration()
        reader = FlightBlenderDatabaseReader()
        assert reader.check_composite_operational_intent_exists(str(fd.id)) is False

    def test_get_composite_operational_intent_by_declaration_id_returns_none(self):
        fd = _make_flight_declaration()
        reader = FlightBlenderDatabaseReader()
        result = reader.get_composite_operational_intent_by_declaration_id(str(fd.id))
        assert result is None


@pytest.mark.django_db
class TestDatabaseReaderConstraints:
    def test_check_constraint_id_exists_false(self):
        reader = FlightBlenderDatabaseReader()
        assert reader.check_constraint_id_exists(str(uuid.uuid4())) is False

    def test_check_constraint_reference_id_exists_false(self):
        reader = FlightBlenderDatabaseReader()
        assert reader.check_constraint_reference_id_exists(str(uuid.uuid4())) is False

    def test_get_peer_operational_intent_details_returns_none(self):
        reader = FlightBlenderDatabaseReader()
        result = reader.get_peer_operational_intent_details_by_id(str(uuid.uuid4()))
        assert result is None

    def test_get_peer_operational_intent_reference_returns_none(self):
        reader = FlightBlenderDatabaseReader()
        result = reader.get_peer_operational_intent_reference_by_id(str(uuid.uuid4()))
        assert result is None


@pytest.mark.django_db
class TestDatabaseReaderSurveillance:
    def test_get_active_surveillance_sensors(self):
        _make_surveillance_sensor()
        reader = FlightBlenderDatabaseReader()
        result = reader.get_active_surveillance_sensors()
        assert result.count() >= 1

    def test_get_surveillance_sensor_by_id_not_found(self):
        reader = FlightBlenderDatabaseReader()
        result = reader.get_surveillance_sensor_by_id(uuid.uuid4())
        assert result is None

    def test_get_surveillance_sensor_by_id_found(self):
        sensor = _make_surveillance_sensor()
        reader = FlightBlenderDatabaseReader()
        result = reader.get_surveillance_sensor_by_id(sensor.id)
        assert result is not None
        assert result.id == sensor.id

    def test_get_surveillance_session_by_id_not_found(self):
        reader = FlightBlenderDatabaseReader()
        result = reader.get_surveillance_session_by_id(str(uuid.uuid4()))
        assert result is None

    def test_get_surveillance_session_by_id_found(self):
        session = _make_surveillance_session()
        reader = FlightBlenderDatabaseReader()
        result = reader.get_surveillance_session_by_id(str(session.id))
        assert result is not None
        assert result.id == session.id

    def test_get_all_active_surveillance_sessions(self):
        _make_surveillance_session()
        reader = FlightBlenderDatabaseReader()
        result = reader.get_all_active_surveillance_sessions()
        assert result.count() >= 1

    def test_get_sensor_health_record_returns_none_when_no_health(self):
        sensor = _make_surveillance_sensor()
        reader = FlightBlenderDatabaseReader()
        result = reader.get_sensor_health_record(str(sensor.id))
        assert result is None

    def test_get_sensor_status_before_time_returns_none_when_no_records(self):
        sensor = _make_surveillance_sensor()
        reader = FlightBlenderDatabaseReader()
        result = reader.get_sensor_status_before_time(str(sensor.id), arrow.now().datetime)
        assert result is None

    def test_get_active_user_notifications_between_interval(self):
        reader = FlightBlenderDatabaseReader()
        start = arrow.now().shift(minutes=-5).datetime
        end = arrow.now().shift(minutes=5).datetime
        result = reader.get_active_user_notifications_between_interval(start, end)
        assert result is not None

    def test_get_heartbeat_events_in_window_empty(self):
        reader = FlightBlenderDatabaseReader()
        start = arrow.now().shift(minutes=-5).datetime
        end = arrow.now().shift(minutes=5).datetime
        result = reader.get_heartbeat_events_in_window(start, end)
        assert result.count() == 0

    def test_get_track_events_for_session_empty(self):
        session = _make_surveillance_session()
        reader = FlightBlenderDatabaseReader()
        start = arrow.now().shift(minutes=-5).datetime
        end = arrow.now().shift(minutes=5).datetime
        result = reader.get_track_events_for_session(str(session.id), start, end)
        assert result.count() == 0

    def test_get_failure_notifications_for_sensor_empty(self):
        sensor = _make_surveillance_sensor()
        reader = FlightBlenderDatabaseReader()
        start = arrow.now().shift(minutes=-5).datetime
        end = arrow.now().shift(minutes=5).datetime
        result = reader.get_failure_notifications_for_sensor(str(sensor.id), start, end)
        assert result.count() == 0

    def test_get_surveillance_periodic_tasks_by_session_id(self):
        reader = FlightBlenderDatabaseReader()
        result = reader.get_surveillance_periodic_tasks_by_session_id(uuid.uuid4())
        assert result.count() == 0

    def test_get_surveillance_sessions_with_events_in_window_empty(self):
        reader = FlightBlenderDatabaseReader()
        start = arrow.now().shift(minutes=-5).datetime
        end = arrow.now().shift(minutes=5).datetime
        result = reader.get_surveillance_sessions_with_events_in_window(start, end)
        assert result.count() == 0

    def test_get_health_tracking_records_for_sensor_empty(self):
        sensor = _make_surveillance_sensor()
        reader = FlightBlenderDatabaseReader()
        start = arrow.now().shift(minutes=-5).datetime
        end = arrow.now().shift(minutes=5).datetime
        result = reader.get_health_tracking_records_for_sensor(str(sensor.id), start, end)
        assert result.count() == 0


@pytest.mark.django_db
class TestDatabaseReaderRIDSubscriptions:
    def test_check_rid_subscription_by_view_hash_false(self):
        reader = FlightBlenderDatabaseReader()
        assert reader.check_rid_subscription_record_by_view_hash_exists(99999) is False

    def test_check_rid_subscription_by_subscription_id_false(self):
        reader = FlightBlenderDatabaseReader()
        assert reader.check_rid_subscription_record_by_subscription_id_exists(str(uuid.uuid4())) is False

    def test_get_all_rid_simulated_subscription_records_empty(self):
        reader = FlightBlenderDatabaseReader()
        result = reader.get_all_rid_simulated_subscription_records()
        assert result.count() == 0

    def test_check_flight_details_exist_false(self):
        reader = FlightBlenderDatabaseReader()
        assert reader.check_flight_details_exist(str(uuid.uuid4())) is False

    def test_get_rid_monitoring_task_returns_none(self):
        reader = FlightBlenderDatabaseReader()
        result = reader.get_rid_monitoring_task(session_id=uuid.uuid4())
        assert result is None

    def test_get_active_geofences_returns_queryset(self):
        reader = FlightBlenderDatabaseReader()
        result = reader.get_active_geofences()
        assert result is not None


# ===========================================================================
# FlightBlenderDatabaseWriter tests
# ===========================================================================


@pytest.mark.django_db
class TestDatabaseWriterFlightOperations:
    def test_update_flight_operation_state(self):
        fd = _make_flight_declaration(state=0)
        writer = FlightBlenderDatabaseWriter()
        result = writer.update_flight_operation_state(str(fd.id), 1)
        assert result is True
        fd.refresh_from_db()
        assert fd.state == 1

    def test_update_flight_operation_state_not_found(self):
        writer = FlightBlenderDatabaseWriter()
        result = writer.update_flight_operation_state(str(uuid.uuid4()), 1)
        assert result is False

    def test_update_telemetry_timestamp(self):
        fd = _make_flight_declaration()
        writer = FlightBlenderDatabaseWriter()
        result = writer.update_telemetry_timestamp(str(fd.id))
        assert result is True

    def test_update_telemetry_timestamp_not_found(self):
        writer = FlightBlenderDatabaseWriter()
        result = writer.update_telemetry_timestamp(str(uuid.uuid4()))
        assert result is False

    def test_delete_all_flight_observations(self):
        _make_flight_observation()
        writer = FlightBlenderDatabaseWriter()
        result = writer.delete_all_flight_observations()
        assert result is True

    def test_delete_all_flight_details(self):
        writer = FlightBlenderDatabaseWriter()
        result = writer.delete_all_flight_details()
        assert result is True

    def test_create_flight_operational_intent_reference_from_declaration_obj(self):
        fd = _make_flight_declaration()
        writer = FlightBlenderDatabaseWriter()
        result = writer.create_flight_operational_intent_reference_from_flight_declaration_obj(fd)
        assert result is True

    def test_update_flight_operation_operational_intent(self):
        fd = _make_flight_declaration()
        writer = FlightBlenderDatabaseWriter()
        opint = PartialCreateOperationalIntentReference(
            volumes=[],
            priority=0,
            state="Accepted",
            off_nominal_volumes=[],
        )
        result = writer.update_flight_operation_operational_intent(str(fd.id), opint)
        assert result is True


@pytest.mark.django_db
class TestDatabaseWriterSurveillance:
    def test_create_surveillance_session(self):
        writer = FlightBlenderDatabaseWriter()
        sid = uuid.uuid4()
        valid_until = arrow.now().shift(hours=1).isoformat()
        result = writer.create_surveillance_session(sid, valid_until)
        assert result is True

    def test_create_surveillance_session_duplicate_returns_false(self):
        writer = FlightBlenderDatabaseWriter()
        sid = uuid.uuid4()
        valid_until = arrow.now().shift(hours=1).isoformat()
        writer.create_surveillance_session(sid, valid_until)
        result = writer.create_surveillance_session(sid, valid_until)
        assert result is False

    def test_delete_surveillance_session(self):
        session = _make_surveillance_session()
        writer = FlightBlenderDatabaseWriter()
        writer.delete_surveillance_session(session.id)
        assert SurveillanceSession.objects.filter(id=session.id).count() == 0

    def test_record_heartbeat_event(self):
        session = _make_surveillance_session()
        writer = FlightBlenderDatabaseWriter()
        expected_at = datetime.now(tz=timezone.utc)
        result = writer.record_heartbeat_event(str(session.id), expected_at, delivered_on_time=True)
        assert result is True

    def test_record_heartbeat_event_session_not_found(self):
        writer = FlightBlenderDatabaseWriter()
        expected_at = datetime.now(tz=timezone.utc)
        result = writer.record_heartbeat_event(str(uuid.uuid4()), expected_at, delivered_on_time=True)
        assert result is False

    def test_record_track_event(self):
        session = _make_surveillance_session()
        writer = FlightBlenderDatabaseWriter()
        expected_at = datetime.now(tz=timezone.utc)
        result = writer.record_track_event(str(session.id), expected_at, had_active_tracks=False)
        assert result is True

    def test_record_track_event_session_not_found(self):
        writer = FlightBlenderDatabaseWriter()
        expected_at = datetime.now(tz=timezone.utc)
        result = writer.record_track_event(str(uuid.uuid4()), expected_at, had_active_tracks=False)
        assert result is False

    def test_update_sensor_health_status_sensor_not_found(self):
        writer = FlightBlenderDatabaseWriter()
        result = writer.update_sensor_health_status(str(uuid.uuid4()), "operational")
        assert result is False

    def test_update_sensor_health_status_creates_health_record(self):
        sensor = _make_surveillance_sensor()
        writer = FlightBlenderDatabaseWriter()
        result = writer.update_sensor_health_status(str(sensor.id), "operational")
        assert result is True
        health = SurveillanceSensorHealth.objects.get(sensor=sensor)
        assert health.status == "operational"

    def test_update_sensor_health_status_no_op_when_same(self):
        sensor = _make_surveillance_sensor()
        writer = FlightBlenderDatabaseWriter()
        writer.update_sensor_health_status(str(sensor.id), "operational")
        result = writer.update_sensor_health_status(str(sensor.id), "operational")
        assert result is True


@pytest.mark.django_db
class TestDatabaseWriterRIDSubscriptions:
    def test_create_rid_subscription_record(self):
        writer = FlightBlenderDatabaseWriter()
        result = writer.create_rid_subscription_record(
            subscription_id=str(uuid.uuid4()),
            record_id=str(uuid.uuid4()),
            view="0,0,1,1",
            view_hash=12345,
            end_datetime=arrow.now().shift(hours=1).isoformat(),
            flights_dict="{}",
            is_simulated=True,
        )
        assert result is True

    def test_delete_all_simulated_rid_subscription_records(self):
        writer = FlightBlenderDatabaseWriter()
        writer.create_rid_subscription_record(
            subscription_id=str(uuid.uuid4()),
            record_id=str(uuid.uuid4()),
            view="0,0,1,1",
            view_hash=11111,
            end_datetime=arrow.now().shift(hours=1).isoformat(),
            flights_dict="{}",
            is_simulated=True,
        )
        result = writer.delete_all_simulated_rid_subscription_records()
        assert result is True


@pytest.mark.django_db
class TestNormalizeTimestamp:
    def test_none_returns_none(self):
        result = FlightBlenderDatabaseWriter._normalize_timestamp(None)
        assert result is None

    def test_zero_returns_none(self):
        result = FlightBlenderDatabaseWriter._normalize_timestamp(0)
        assert result is None

    def test_microsecond_timestamp(self):
        ts = 1_700_000_000_000_000  # microseconds
        result = FlightBlenderDatabaseWriter._normalize_timestamp(ts)
        assert result is not None
        assert result.tzinfo is not None

    def test_millisecond_timestamp(self):
        ts = 1_700_000_000_000  # milliseconds
        result = FlightBlenderDatabaseWriter._normalize_timestamp(ts)
        assert result is not None

    def test_seconds_timestamp(self):
        ts = 1_700_000_000  # seconds
        result = FlightBlenderDatabaseWriter._normalize_timestamp(ts)
        assert result is not None

    def test_invalid_string_returns_none(self):
        result = FlightBlenderDatabaseWriter._normalize_timestamp("not-a-number")
        assert result is None
