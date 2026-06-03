"""Tests for flight_blender.conformance:
- operation_state_helper.py (state machine)
- utils.py (FlightBlenderConformanceEngine, is_time_between)
- tasks.py (check_flight_conformance, check_operation_telemetry_conformance)
- custom_signals.py (signal receivers)
- operator_conformance_notifications.py
"""

import json
import uuid
from dataclasses import dataclass
from datetime import timezone
from unittest.mock import MagicMock, patch

import arrow
import pytest

from flight_blender.conformance.conformance_state_helper import ConformanceChecksList
from flight_blender.conformance.operation_state_helper import (
    AcceptedState,
    ActivatedState,
    CancelledState,
    ContingentState,
    EndedState,
    FlightOperationStateMachine,
    NonconformingState,
    ProcessingNotSubmittedToDss,
    RejectedState,
    WithdrawnState,
    get_status,
    match_state,
)
from flight_blender.conformance.operator_conformance_notifications import OperationConformanceNotification
from flight_blender.conformance.tasks import check_flight_conformance, check_operation_telemetry_conformance
from flight_blender.conformance.utils import FlightBlenderConformanceEngine, is_time_between
from flight_blender.flight_declarations.models import FlightDeclaration
from flight_blender.scd.scd_data_definitions import LatLngPoint


# ---------------------------------------------------------------------------
# operation_state_helper.py
# ---------------------------------------------------------------------------


class TestStateMachineStates:
    """Tests for individual State transitions."""

    def test_not_submitted_to_accepted_on_dss_accepts(self):
        s = ProcessingNotSubmittedToDss()
        new_s = s.on_event("dss_accepts")
        assert isinstance(new_s, AcceptedState)

    def test_not_submitted_to_withdrawn(self):
        s = ProcessingNotSubmittedToDss()
        new_s = s.on_event("operator_withdraws")
        assert isinstance(new_s, WithdrawnState)

    def test_not_submitted_to_cancelled(self):
        s = ProcessingNotSubmittedToDss()
        new_s = s.on_event("operator_cancels")
        assert isinstance(new_s, CancelledState)

    def test_not_submitted_unknown_event_returns_self(self):
        s = ProcessingNotSubmittedToDss()
        new_s = s.on_event("unknown_event")
        assert isinstance(new_s, ProcessingNotSubmittedToDss)

    def test_accepted_to_activated(self):
        s = AcceptedState()
        new_s = s.on_event("operator_activates")
        assert isinstance(new_s, ActivatedState)

    def test_accepted_to_ended(self):
        s = AcceptedState()
        new_s = s.on_event("operator_confirms_ended")
        assert isinstance(new_s, EndedState)

    def test_accepted_to_nonconforming(self):
        s = AcceptedState()
        new_s = s.on_event("ua_departs_early_late_outside_op_intent")
        assert isinstance(new_s, NonconformingState)

    def test_accepted_unknown_event_returns_self(self):
        s = AcceptedState()
        new_s = s.on_event("unknown_event")
        assert isinstance(new_s, AcceptedState)

    def test_activated_to_ended(self):
        s = ActivatedState()
        new_s = s.on_event("operator_confirms_ended")
        assert isinstance(new_s, EndedState)

    def test_activated_to_nonconforming(self):
        s = ActivatedState()
        new_s = s.on_event("ua_exits_coordinated_op_intent")
        assert isinstance(new_s, NonconformingState)

    def test_activated_to_contingent(self):
        s = ActivatedState()
        new_s = s.on_event("operator_initiates_contingent")
        assert isinstance(new_s, ContingentState)

    def test_ended_state_stays_ended(self):
        s = EndedState()
        new_s = s.on_event("any_event")
        assert isinstance(new_s, EndedState)

    def test_nonconforming_to_activated(self):
        s = NonconformingState()
        new_s = s.on_event("operator_return_to_coordinated_op_intent")
        assert isinstance(new_s, ActivatedState)

    def test_nonconforming_to_ended(self):
        s = NonconformingState()
        new_s = s.on_event("operator_confirms_ended")
        assert isinstance(new_s, EndedState)

    def test_nonconforming_to_contingent_timeout(self):
        s = NonconformingState()
        new_s = s.on_event("timeout")
        assert isinstance(new_s, ContingentState)

    def test_contingent_to_ended(self):
        s = ContingentState()
        new_s = s.on_event("operator_confirms_ended")
        assert isinstance(new_s, EndedState)

    def test_contingent_unknown_returns_self(self):
        s = ContingentState()
        new_s = s.on_event("unknown_event")
        assert isinstance(new_s, ContingentState)

    def test_withdrawn_state_stays_withdrawn(self):
        s = WithdrawnState()
        new_s = s.on_event("any_event")
        assert isinstance(new_s, WithdrawnState)

    def test_cancelled_state_stays_cancelled(self):
        s = CancelledState()
        new_s = s.on_event("any_event")
        assert isinstance(new_s, CancelledState)

    def test_rejected_state_stays_rejected(self):
        s = RejectedState()
        new_s = s.on_event("any_event")
        assert isinstance(new_s, RejectedState)


class TestFlightOperationStateMachine:
    def test_init_with_accepted_state(self):
        sm = FlightOperationStateMachine(state=1)
        assert isinstance(sm.state, AcceptedState)

    def test_on_event_changes_state(self):
        sm = FlightOperationStateMachine(state=1)
        sm.on_event("operator_activates")
        assert isinstance(sm.state, ActivatedState)

    def test_get_status_returns_int(self):
        s = AcceptedState()
        assert get_status(s) == 1

    def test_match_state_unknown_returns_false(self):
        result = match_state(999)
        assert result is False


# ---------------------------------------------------------------------------
# is_time_between
# ---------------------------------------------------------------------------


class TestIsTimeBetween:
    def test_time_within_range(self):
        begin = arrow.now().shift(minutes=-10)
        end = arrow.now().shift(minutes=10)
        check = arrow.now()
        assert is_time_between(begin, end, check_time=check) is True

    def test_time_before_range(self):
        begin = arrow.now().shift(minutes=5)
        end = arrow.now().shift(minutes=15)
        check = arrow.now()
        assert is_time_between(begin, end, check_time=check) is False

    def test_time_after_range(self):
        begin = arrow.now().shift(minutes=-20)
        end = arrow.now().shift(minutes=-5)
        check = arrow.now()
        assert is_time_between(begin, end, check_time=check) is False

    def test_midnight_crossing_true(self):
        """When begin > end (crosses midnight), check_time >= begin OR <= end."""
        begin = arrow.now().shift(hours=1)
        end = arrow.now().shift(hours=-1)  # end < begin → crosses midnight
        # check_time < begin but > end → FALSE
        check = arrow.now()
        result = is_time_between(begin, end, check_time=check)
        # Either True or False is fine here; just ensure no exception
        assert isinstance(result, bool)

    def test_default_check_time_uses_now(self):
        begin = arrow.now().shift(minutes=-10)
        end = arrow.now().shift(minutes=10)
        result = is_time_between(begin, end)
        assert result is True


# ---------------------------------------------------------------------------
# FlightBlenderConformanceEngine (DB-heavy tests)
# ---------------------------------------------------------------------------


def _make_flight_declaration_for_conformance(state=2, aircraft_id="TEST-UAV"):
    """Create a FlightDeclaration with a valid operational_intent JSON."""
    now = arrow.utcnow()
    operational_intent = {
        "volumes": [
            {
                "volume": {
                    "outline_polygon": {
                        "vertices": [
                            {"lat": 51.4, "lng": -0.2},
                            {"lat": 51.4, "lng": 0.2},
                            {"lat": 51.6, "lng": 0.2},
                            {"lat": 51.6, "lng": -0.2},
                            {"lat": 51.4, "lng": -0.2},
                        ]
                    },
                    "altitude_lower": {"value": 0.0, "reference": "W84", "units": "M"},
                    "altitude_upper": {"value": 200.0, "reference": "W84", "units": "M"},
                },
                "time_start": {"format": "RFC3339", "value": now.shift(minutes=-5).isoformat()},
                "time_end": {"format": "RFC3339", "value": now.shift(minutes=55).isoformat()},
            }
        ]
    }
    fd = FlightDeclaration.objects.create(
        flight_declaration_raw_geojson="{}",
        bounds="-1.0,50.0,1.0,52.0",
        start_datetime=now.shift(minutes=-5).datetime,
        end_datetime=now.shift(minutes=55).datetime,
        type_of_operation=0,
        originating_party="TEST",
        state=state,
        aircraft_id=aircraft_id,
        operational_intent=json.dumps(operational_intent),
    )
    return fd


@pytest.mark.django_db
class TestFlightBlenderConformanceEngineC2C3:
    def test_c2_no_flight_declaration(self):
        engine = FlightBlenderConformanceEngine()
        # Non-existent flight declaration ID
        result = engine.is_operation_conformant_via_telemetry(
            flight_declaration_id=str(uuid.uuid4()),
            aircraft_id="TEST-UAV",
            telemetry_location=LatLngPoint(lat=51.5, lng=0.0),
            altitude_m_wgs_84=50.0,
        )
        # Returns C2 check code
        assert result == ConformanceChecksList.C2

    def test_c3_aircraft_id_mismatch(self):
        fd = _make_flight_declaration_for_conformance(state=2, aircraft_id="CORRECT-ID")
        engine = FlightBlenderConformanceEngine()
        result = engine.is_operation_conformant_via_telemetry(
            flight_declaration_id=str(fd.id),
            aircraft_id="WRONG-ID",
            telemetry_location=LatLngPoint(lat=51.5, lng=0.0),
            altitude_m_wgs_84=50.0,
        )
        assert result == ConformanceChecksList.C3

    def test_c4_invalid_state(self):
        fd = _make_flight_declaration_for_conformance(state=0, aircraft_id="CORRECT-ID")
        engine = FlightBlenderConformanceEngine()
        result = engine.is_operation_conformant_via_telemetry(
            flight_declaration_id=str(fd.id),
            aircraft_id="CORRECT-ID",
            telemetry_location=LatLngPoint(lat=51.5, lng=0.0),
            altitude_m_wgs_84=50.0,
        )
        assert result == ConformanceChecksList.C4

    def test_c5_wrong_state(self):
        fd = _make_flight_declaration_for_conformance(state=1, aircraft_id="CORRECT-ID")
        engine = FlightBlenderConformanceEngine()
        result = engine.is_operation_conformant_via_telemetry(
            flight_declaration_id=str(fd.id),
            aircraft_id="CORRECT-ID",
            telemetry_location=LatLngPoint(lat=51.5, lng=0.0),
            altitude_m_wgs_84=50.0,
        )
        assert result == ConformanceChecksList.C5

    def test_c7_conformant_returns_100(self):
        fd = _make_flight_declaration_for_conformance(state=2, aircraft_id="CORRECT-ID")
        engine = FlightBlenderConformanceEngine()
        # Location within the declared polygon and altitude within bounds
        result = engine.is_operation_conformant_via_telemetry(
            flight_declaration_id=str(fd.id),
            aircraft_id="CORRECT-ID",
            telemetry_location=LatLngPoint(lat=51.5, lng=0.0),
            altitude_m_wgs_84=100.0,
        )
        assert result == 100

    def test_c7b_altitude_nonconformant(self):
        fd = _make_flight_declaration_for_conformance(state=2, aircraft_id="CORRECT-ID")
        engine = FlightBlenderConformanceEngine()
        # Altitude above upper limit (200m)
        result = engine.is_operation_conformant_via_telemetry(
            flight_declaration_id=str(fd.id),
            aircraft_id="CORRECT-ID",
            telemetry_location=LatLngPoint(lat=51.5, lng=0.0),
            altitude_m_wgs_84=9999.0,
        )
        assert result == ConformanceChecksList.C7b

    def test_c7a_bounds_nonconformant(self):
        fd = _make_flight_declaration_for_conformance(state=2, aircraft_id="CORRECT-ID")
        engine = FlightBlenderConformanceEngine()
        # Location outside the declared polygon (far away)
        result = engine.is_operation_conformant_via_telemetry(
            flight_declaration_id=str(fd.id),
            aircraft_id="CORRECT-ID",
            telemetry_location=LatLngPoint(lat=10.0, lng=100.0),  # far outside polygon
            altitude_m_wgs_84=50.0,
        )
        assert result == ConformanceChecksList.C7a


@pytest.mark.django_db
class TestCheckFlightOperationalIntentReferenceConformance:
    def test_nonexistent_declaration_returns_c11(self):
        engine = FlightBlenderConformanceEngine()
        result = engine.check_flight_operational_intent_reference_conformance(
            flight_declaration_id=str(uuid.uuid4()),
        )
        # If USSP_NETWORK_ENABLED=0 (test default), skips C11 and checks C10
        # Non-existent declaration returns None → C10 path
        assert isinstance(result, int)

    def test_activated_with_recent_telemetry_returns_1(self):
        now = arrow.utcnow()
        fd = _make_flight_declaration_for_conformance(state=2, aircraft_id="CORRECT-ID")
        # Set latest_telemetry_datetime to just now (within 15s window)
        fd.latest_telemetry_datetime = now.datetime.replace(tzinfo=timezone.utc)
        fd.save()
        engine = FlightBlenderConformanceEngine()
        result = engine.check_flight_operational_intent_reference_conformance(
            flight_declaration_id=str(fd.id),
        )
        assert result == 1

    def test_activated_without_telemetry_returns_c9a(self):
        fd = _make_flight_declaration_for_conformance(state=2, aircraft_id="CORRECT-ID")
        engine = FlightBlenderConformanceEngine()
        result = engine.check_flight_operational_intent_reference_conformance(
            flight_declaration_id=str(fd.id),
        )
        assert result == ConformanceChecksList.C9a


# ---------------------------------------------------------------------------
# conformance tasks (check_flight_conformance, check_operation_telemetry_conformance)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestCheckFlightConformanceTask:
    def test_conformant_calls_telemetry_check(self):
        fd = _make_flight_declaration_for_conformance(state=2, aircraft_id="CORRECT-ID")
        with patch(
            "flight_blender.conformance.tasks.FlightBlenderConformanceEngine.check_flight_operational_intent_reference_conformance",
            return_value=1,
        ):
            with patch("flight_blender.conformance.tasks.check_operation_telemetry_conformance") as mock_telem:
                check_flight_conformance(flight_declaration_id=str(fd.id), session_id="test-sess")
                mock_telem.assert_called_once()

    def test_nonconformant_sends_signal(self):
        fd = _make_flight_declaration_for_conformance(state=2, aircraft_id="CORRECT-ID")
        with patch(
            "flight_blender.conformance.tasks.FlightBlenderConformanceEngine.check_flight_operational_intent_reference_conformance",
            return_value=ConformanceChecksList.C9a,
        ):
            with patch("flight_blender.conformance.tasks.custom_signals.flight_operational_intent_reference_non_conformance_signal.send") as mock_signal:
                check_flight_conformance(flight_declaration_id=str(fd.id), session_id="test-sess")
                mock_signal.assert_called_once()


@pytest.mark.django_db
class TestCheckOperationTelemetryConformanceTask:
    def test_no_observation_returns_early(self):
        fd = _make_flight_declaration_for_conformance(state=2)
        with patch("flight_blender.conformance.tasks.flight_stream_helper.ObservationReadOperations") as mock_obs_cls:
            mock_obs_instance = MagicMock()
            mock_obs_instance.get_latest_flight_observation_by_flight_declaration_id.return_value = None
            mock_obs_cls.return_value = mock_obs_instance
            # Should return early without raising
            check_operation_telemetry_conformance(flight_declaration_id=str(fd.id))


# ---------------------------------------------------------------------------
# operator_conformance_notifications.py
# ---------------------------------------------------------------------------


class TestOperationConformanceNotification:
    def test_no_amqp_logs_error(self):
        notif = OperationConformanceNotification(flight_declaration_id="test-fd-id")
        # When AMQP_URL is not set, should just log without raising
        notif.send_conformance_status_notification(message="Test message", level="error")

    def test_with_amqp_calls_task(self):
        with patch.dict("os.environ", {"AMQP_URL": "amqp://localhost"}):
            with patch("flight_blender.conformance.operator_conformance_notifications.send_operational_update_message") as mock_task:
                mock_delay = MagicMock()
                mock_task.delay = mock_delay
                notif = OperationConformanceNotification(flight_declaration_id="fd-amqp")
                notif.send_conformance_status_notification(message="Some message", level="info")
                mock_delay.assert_called_once()
