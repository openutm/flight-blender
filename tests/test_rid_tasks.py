"""
Unit tests for flight_blender.rid/tasks.py

These tests mock Redis, Celery tasks, and DSS helper methods so that we can
exercise the business logic without any external dependencies.
"""

import json
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import arrow
import pytest

from flight_blender.repositories.flight_declarations_repo import SQLAlchemyFlightDeclarationRepository
from flight_blender.repositories.flight_feed_repo import SQLAlchemyFlightFeedRepository
from flight_blender.repositories.notifications_repo import SQLAlchemyNotificationsRepository
from flight_blender.services.rid_svc import FlightTelemetryRIDEngine
from flight_blender.tasks.rid_task import (
    _async_process_requested_flight,
    _parse_rid_timestamp_us,
    check_rid_stream_conformance,
    stream_rid_telemetry_data,
    write_operator_rid_notification,
)

# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------


def _make_telemetry_entry(lat=52.5, lng=13.4, alt=100.0, timestamp=None):
    if timestamp is None:
        timestamp = arrow.utcnow().isoformat()
    return {
        "timestamp": timestamp,
        "timestamp_accuracy": 0.01,
        "operational_status": "Airborne",
        "position": {
            "lat": lat,
            "lng": lng,
            "alt": alt,
            "accuracy_h": "HAUnknown",
            "accuracy_v": "VAUnknown",
        },
        "track": 90.0,
        "speed": 10.0,
        "speed_accuracy": "SAUnknown",
        "vertical_speed": 0.0,
    }


def _make_flight_detail_entry(injection_id="test-inj-001"):
    return {
        "effective_after": arrow.utcnow().isoformat(),
        "details": {
            "id": injection_id,
            "operation_description": "Test flight",
            "operator_id": "fin87astrdge12kh-abc",
            "uas_id": {
                "serial_number": "ABCD5EFGHJ",
                "registration_id": "",
                "utm_id": "",
                "specific_session_id": None,
            },
        },
    }


def _make_requested_flight(injection_id="test-inj-001"):
    return {
        "aircraft_type": "Multirotor",
        "injection_id": injection_id,
        "telemetry": [_make_telemetry_entry()],
        "details_responses": [_make_flight_detail_entry(injection_id)],
    }


VALID_OPERATION_ID = "00000000-0000-0000-0000-000000000001"
VALID_SESSION_ID = "00000000-0000-0000-0000-000000000002"


# ---------------------------------------------------------------------------
# _parse_rid_timestamp_us
# ---------------------------------------------------------------------------


class TestParseRidTimestampUs:
    def test_valid_timestamp(self):
        ts = arrow.utcnow().isoformat()
        result = _parse_rid_timestamp_us(ts, "test_ctx")
        assert isinstance(result, int)
        assert result > 0

    def test_none_returns_zero(self):
        assert _parse_rid_timestamp_us(None, "ctx") == 0

    def test_empty_string_returns_zero(self):
        assert _parse_rid_timestamp_us("", "ctx") == 0

    def test_invalid_string_returns_zero(self):
        assert _parse_rid_timestamp_us("not-a-date", "ctx") == 0

    def test_result_is_microseconds(self):
        now = arrow.utcnow()
        result = _parse_rid_timestamp_us(now.isoformat(), "ctx")
        expected_order = int(now.float_timestamp * 1_000_000)
        assert abs(result - expected_order) < 1_000_000  # within 1 second


# ---------------------------------------------------------------------------
# write_operator_rid_notification
# ---------------------------------------------------------------------------


class TestWriteOperatorRidNotification:
    def test_creates_notification_record(self):
        """write_operator_rid_notification should write to the database."""
        with patch.object(SQLAlchemyNotificationsRepository, "create_notification", new_callable=AsyncMock) as mock_create:
            write_operator_rid_notification("test message", VALID_SESSION_ID)
            mock_create.assert_awaited_once()


# ---------------------------------------------------------------------------
# process_requested_flight
# ---------------------------------------------------------------------------


class TestProcessRequestedFlight:
    async def test_basic_flight_processing(self, fakeredis_server):
        """_async_process_requested_flight returns a RIDTestInjection and positions/altitudes."""
        flight = _make_requested_flight(VALID_OPERATION_ID)
        rid_repo = MagicMock()
        rid_repo.create_or_update_flight_detail = AsyncMock()
        result, positions, altitudes = await _async_process_requested_flight(
            requested_flight=flight,
            flight_injection_sorted_set="test_ss",
            test_id=VALID_SESSION_ID,
            injection_id=VALID_OPERATION_ID,
            rid_repo=rid_repo,
        )
        assert result.aircraft_type == "Multirotor"
        assert len(positions) == 1
        assert len(altitudes) == 1
        assert altitudes[0] == 100.0

    async def test_missing_telemetry_fields_skips_entry(self, fakeredis_server):
        """Telemetry entries missing mandatory fields are skipped."""
        bad_telemetry = {"position": {"lat": 52.5, "lng": 13.4, "alt": 100.0}}
        flight = {
            "aircraft_type": "Multirotor",
            "injection_id": VALID_OPERATION_ID,
            "telemetry": [bad_telemetry],
            "details_responses": [_make_flight_detail_entry(VALID_OPERATION_ID)],
        }
        rid_repo = MagicMock()
        rid_repo.create_or_update_flight_detail = AsyncMock()
        with patch("flight_blender.tasks.rid_task.write_operator_rid_notification") as mock_notify:
            result, positions, altitudes = await _async_process_requested_flight(
                requested_flight=flight,
                flight_injection_sorted_set="test_ss2",
                test_id=VALID_SESSION_ID,
                injection_id=VALID_OPERATION_ID,
                rid_repo=rid_repo,
            )
        # No positions or altitudes accumulated since telemetry was skipped
        assert len(positions) == 0
        assert len(altitudes) == 0
        mock_notify.delay.assert_called_once()

    async def test_operator_location_parsed(self, fakeredis_server):
        """Flight details with operator_location are parsed correctly."""
        detail_with_loc = {
            "effective_after": arrow.utcnow().isoformat(),
            "details": {
                "id": VALID_OPERATION_ID,
                "operation_description": "Test flight with operator loc",
                "operator_id": "fin87astrdge12kh-abc",
                "operator_location": {"lat": 52.51, "lng": 13.41},
                "uas_id": {"serial_number": "ABCD5EFGHJ", "registration_id": "", "utm_id": "", "specific_session_id": None},
            },
        }
        flight = {
            "aircraft_type": "Multirotor",
            "injection_id": VALID_OPERATION_ID,
            "telemetry": [_make_telemetry_entry()],
            "details_responses": [detail_with_loc],
        }
        rid_repo = MagicMock()
        rid_repo.create_or_update_flight_detail = AsyncMock()
        result, positions, altitudes = await _async_process_requested_flight(
            requested_flight=flight,
            flight_injection_sorted_set="test_ss3",
            test_id=VALID_SESSION_ID,
            injection_id=VALID_OPERATION_ID,
            rid_repo=rid_repo,
        )
        assert len(positions) == 1

    async def test_auth_data_parsed(self, fakeredis_server):
        """Flight details with auth_data are parsed correctly."""
        detail_with_auth = {
            "effective_after": arrow.utcnow().isoformat(),
            "details": {
                "id": VALID_OPERATION_ID,
                "operation_description": "Test flight with auth",
                "operator_id": "fin87astrdge12kh-abc",
                "auth_data": {"format": 1, "data": "dGVzdA=="},
                "uas_id": {"serial_number": "ABCD5EFGHJ", "registration_id": "", "utm_id": "", "specific_session_id": None},
            },
        }
        flight = {
            "aircraft_type": "Multirotor",
            "injection_id": VALID_OPERATION_ID,
            "telemetry": [_make_telemetry_entry()],
            "details_responses": [detail_with_auth],
        }
        rid_repo = MagicMock()
        rid_repo.create_or_update_flight_detail = AsyncMock()
        result, positions, altitudes = await _async_process_requested_flight(
            requested_flight=flight,
            flight_injection_sorted_set="test_ss_auth",
            test_id=VALID_SESSION_ID,
            injection_id=VALID_OPERATION_ID,
            rid_repo=rid_repo,
        )
        assert len(positions) == 1

    async def test_height_field_parsed(self, fakeredis_server):
        """Telemetry entries with height field are handled correctly."""
        telemetry_with_height = _make_telemetry_entry()
        telemetry_with_height["height"] = {"distance": 50.0, "reference": "TakeoffLocation"}

        flight = {
            "aircraft_type": "Multirotor",
            "injection_id": VALID_OPERATION_ID,
            "telemetry": [telemetry_with_height],
            "details_responses": [_make_flight_detail_entry(VALID_OPERATION_ID)],
        }
        rid_repo = MagicMock()
        rid_repo.create_or_update_flight_detail = AsyncMock()
        result, positions, altitudes = await _async_process_requested_flight(
            requested_flight=flight,
            flight_injection_sorted_set="test_ss_height",
            test_id=VALID_SESSION_ID,
            injection_id=VALID_OPERATION_ID,
            rid_repo=rid_repo,
        )
        assert len(positions) == 1


# ---------------------------------------------------------------------------
# stream_rid_telemetry_data
# ---------------------------------------------------------------------------


class TestStreamRidTelemetryData:
    def _make_observation_payload(self, operation_id=VALID_OPERATION_ID):
        return [
            {
                "flight_details": {
                    "id": operation_id,
                    "uas_id": {"serial_number": "ABCD5EFGHJ"},
                },
                "current_states": [
                    {
                        "timestamp": {"value": arrow.utcnow().isoformat(), "format": "RFC3339"},
                        "timestamp_accuracy": 0.01,
                        "operational_status": "Airborne",
                        "position": {
                            "lat": 52.5,
                            "lng": 13.4,
                            "alt": 100.0,
                            "accuracy_h": "HAUnknown",
                            "accuracy_v": "VAUnknown",
                            "extrapolated": False,
                            "pressure_altitude": 0.0,
                        },
                        "track": 90.0,
                        "speed": 10.0,
                        "speed_accuracy": "SAUnknown",
                        "vertical_speed": 0.0,
                        "height": None,
                    }
                ],
            }
        ]

    def test_stream_enqueues_observations(self):
        """stream_rid_telemetry_data should enqueue write tasks for each state."""
        payload = json.dumps(self._make_observation_payload())
        with patch.object(SQLAlchemyFlightDeclarationRepository, "update_telemetry_timestamp", new_callable=AsyncMock):
            with patch("flight_blender.tasks.rid_task.write_incoming_air_traffic_data") as mock_write:
                with patch("flight_blender.tasks.rid_task.wgs84_to_barometric", return_value=(100.0, 100.0)):
                    stream_rid_telemetry_data(payload)
        mock_write.delay.assert_called_once()

    def test_stream_multiple_states(self):
        """stream_rid_telemetry_data handles multiple current_states per observation."""
        obs = self._make_observation_payload()
        obs[0]["current_states"].append(obs[0]["current_states"][0].copy())
        payload = json.dumps(obs)
        with patch.object(SQLAlchemyFlightDeclarationRepository, "update_telemetry_timestamp", new_callable=AsyncMock):
            with patch("flight_blender.tasks.rid_task.write_incoming_air_traffic_data") as mock_write:
                with patch("flight_blender.tasks.rid_task.wgs84_to_barometric", return_value=(100.0, 100.0)):
                    stream_rid_telemetry_data(payload)
        assert mock_write.delay.call_count == 2


# ---------------------------------------------------------------------------
# check_rid_stream_conformance
# ---------------------------------------------------------------------------


class TestCheckRidStreamConformance:
    def test_conformant_stream(self):
        """check_rid_stream_conformance with a conformant stream logs OK."""
        with patch.object(
            SQLAlchemyFlightFeedRepository, "get_active_rid_observations_for_session_between_interval", new_callable=AsyncMock, return_value=[]
        ):
            check_rid_stream_conformance(session_id=VALID_SESSION_ID)

    def test_non_conformant_stream_writes_notifications(self):
        """check_rid_stream_conformance with errors writes notifications."""
        now = arrow.utcnow().datetime
        observations = [
            MagicMock(created_at=now),
            MagicMock(created_at=(arrow.get(now).shift(seconds=3).datetime)),
        ]
        with patch.object(
            SQLAlchemyFlightFeedRepository,
            "get_active_rid_observations_for_session_between_interval",
            new_callable=AsyncMock,
            return_value=observations,
        ):
            with patch.object(SQLAlchemyNotificationsRepository, "create_notification", new_callable=AsyncMock) as mock_create:
                check_rid_stream_conformance(session_id=VALID_SESSION_ID)
        mock_create.assert_awaited_once()


# ---------------------------------------------------------------------------
# FlightTelemetryRIDEngine (rid_telemetry_monitoring.py)
# ---------------------------------------------------------------------------


class TestFlightTelemetryRIDEngine:
    @pytest.mark.asyncio
    async def test_check_rid_stream_ok_no_observations(self):
        """When there are no recent observations the stream is considered OK."""
        repo = MagicMock()
        repo.get_active_rid_observations_for_session_between_interval = AsyncMock(return_value=[])
        engine = FlightTelemetryRIDEngine(session_id="sess-empty", db_reader=repo)
        ok, errors = await engine.check_rid_stream_ok()
        assert ok is True
        assert errors == []

    @pytest.mark.asyncio
    async def test_check_rid_stream_ok_single_observation(self):
        """A single observation has no gaps so the stream is OK."""
        now = arrow.utcnow()
        obs = MagicMock()
        obs.created_at = now.datetime
        repo = MagicMock()
        repo.get_active_rid_observations_for_session_between_interval = AsyncMock(return_value=[obs])
        engine = FlightTelemetryRIDEngine(session_id="sess-single", db_reader=repo)
        ok, errors = await engine.check_rid_stream_ok()
        assert ok is True

    @pytest.mark.asyncio
    async def test_check_rid_stream_ok_with_gap(self):
        """Observations with a non-1-second gap produce an error."""
        now = arrow.utcnow()
        obs1 = MagicMock()
        obs1.created_at = now.datetime
        obs2 = MagicMock()
        obs2.created_at = (now + timedelta(seconds=3)).datetime
        repo = MagicMock()
        repo.get_active_rid_observations_for_session_between_interval = AsyncMock(return_value=[obs1, obs2])
        engine = FlightTelemetryRIDEngine(session_id="sess-gap", db_reader=repo)
        ok, errors = await engine.check_rid_stream_ok()
        assert ok is False
        assert len(errors) == 1
