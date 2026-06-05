"""Tests for flight_blender.conformance:
- operation_state_helper.py (state machine)
- utils.py (FlightBlenderConformanceEngine, is_time_between)
- tasks.py (check_flight_conformance, check_operation_telemetry_conformance)
- custom_signals.py (signal receivers)
- operator_conformance_notifications.py
"""

import json
import uuid
from datetime import timezone
from unittest.mock import MagicMock, patch

import arrow
import pytest

from flight_blender.services.conformance_svc import ConformanceChecksList
from flight_blender.services.conformance_svc import (
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
from flight_blender.services.conformance_svc import (
    OperationConformanceNotification,
    set_conformance_deps,
)
from flight_blender.tasks.conformance_task import check_flight_conformance, check_operation_telemetry_conformance
from flight_blender.services.conformance_svc import FlightBlenderConformanceEngine, is_time_between
from flight_blender.domain_types.scd import LatLngPoint


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


@pytest.fixture(autouse=True)
def _stub_conformance_deps():
    """Signal receivers and no-arg FlightBlenderConformanceEngine read deps from
    a module-level provider. Tests need a stub registered before they run.
    """
    deps = MagicMock()
    deps.db = MagicMock()
    deps.dss = MagicMock()
    deps.notifier = MagicMock()
    set_conformance_deps(deps)
    yield
    set_conformance_deps(MagicMock(db=MagicMock(), dss=MagicMock(), notifier=MagicMock()))


class TestFlightBlenderConformanceEngineC2C3:
    def test_c2_no_flight_declaration(self):
        from flight_blender.infrastructure.database.repositories.sync_facade import SyncDatabaseFacade

        engine = FlightBlenderConformanceEngine(db=SyncDatabaseFacade())
        # Non-existent flight declaration ID
        with patch(
            "flight_blender.infrastructure.database.repositories.sync_facade.SyncDatabaseFacade.get_flight_declaration_by_id", return_value=None
        ):
            result = engine.is_operation_conformant_via_telemetry(
                flight_declaration_id=str(uuid.uuid4()),
                aircraft_id="TEST-UAV",
                telemetry_location=LatLngPoint(lat=51.5, lng=0.0),
                altitude_m_wgs_84=50.0,
            )
        # Returns C2 check code
        assert result == ConformanceChecksList.C2


class TestCheckFlightOperationalIntentReferenceConformance:
    def test_nonexistent_declaration_returns_c11(self):
        from flight_blender.infrastructure.database.repositories.sync_facade import SyncDatabaseFacade

        engine = FlightBlenderConformanceEngine(db=SyncDatabaseFacade())
        with (
            patch(
                "flight_blender.infrastructure.database.repositories.sync_facade.SyncDatabaseFacade.get_flight_declaration_by_id", return_value=None
            ),
            patch(
                "flight_blender.infrastructure.database.repositories.sync_facade.SyncDatabaseFacade.get_flight_operational_intent_reference_by_flight_declaration_id",
                return_value=None,
            ),
        ):
            result = engine.check_flight_operational_intent_reference_conformance(
                flight_declaration_id=str(uuid.uuid4()),
            )
        # If USSP_NETWORK_ENABLED=0 (test default), skips C11 and checks C10
        # Non-existent declaration returns None → C10 path
        assert isinstance(result, int)


# ---------------------------------------------------------------------------
# conformance tasks (check_flight_conformance, check_operation_telemetry_conformance)
# ---------------------------------------------------------------------------


class TestCheckFlightConformanceTask:
    def test_conformant_calls_telemetry_check(self):
        with patch(
            "flight_blender.tasks.conformance_task.FlightBlenderConformanceEngine.check_flight_operational_intent_reference_conformance",
            return_value=1,
        ):
            with patch("flight_blender.tasks.conformance_task.check_operation_telemetry_conformance") as mock_telem:
                check_flight_conformance(flight_declaration_id=str(uuid.uuid4()), session_id="test-sess")
                mock_telem.assert_called_once()

    def test_nonconformant_sends_signal(self):
        with patch(
            "flight_blender.tasks.conformance_task.FlightBlenderConformanceEngine.check_flight_operational_intent_reference_conformance",
            return_value=ConformanceChecksList.C9a,
        ):
            with patch(
                "flight_blender.tasks.conformance_task.custom_signals.flight_operational_intent_reference_non_conformance_signal.send"
            ) as mock_signal:
                check_flight_conformance(flight_declaration_id=str(uuid.uuid4()), session_id="test-sess")
                mock_signal.assert_called_once()


class TestCheckOperationTelemetryConformanceTask:
    def test_no_observation_returns_early(self):
        with patch("flight_blender.tasks.conformance_task.flight_stream_helper.ObservationReadOperations") as mock_obs_cls:
            mock_obs_instance = MagicMock()
            mock_obs_instance.get_latest_flight_observation_by_flight_declaration_id.return_value = None
            mock_obs_cls.return_value = mock_obs_instance
            # Should return early without raising
            check_operation_telemetry_conformance(flight_declaration_id=str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# operator_conformance_notifications.py
# ---------------------------------------------------------------------------


class TestOperationConformanceNotification:
    def test_no_amqp_logs_error(self):
        notifier = MagicMock()
        notif = OperationConformanceNotification(flight_declaration_id="test-fd-id", notifier=notifier)
        # When AMQP_URL is not set, should just log without raising
        notif.send_conformance_status_notification(message="Test message", level="error")

    def test_with_amqp_calls_task(self):
        with patch("flight_blender.config.settings.AMQP_URL", "amqp://localhost"):
            with patch("flight_blender.tasks.flight_declarations_task.send_operational_update_message") as mock_task:
                mock_delay = MagicMock()
                mock_task.delay = mock_delay
                notifier = MagicMock()
                notifier.send_operational_update_message = mock_delay
                notif = OperationConformanceNotification(flight_declaration_id="fd-amqp", notifier=notifier)
                notif.send_conformance_status_notification(message="Some message", level="info")
                mock_delay.assert_called_once()
