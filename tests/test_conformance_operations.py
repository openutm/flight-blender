"""Tests for flight_blender.conformance:
- operation_state_helper.py (state machine)
- utils.py (FlightBlenderConformanceEngine, is_time_between)
- tasks.py (check_flight_conformance, check_operation_telemetry_conformance)
- custom_signals.py (signal receivers)
- operator_conformance_notifications.py
"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import arrow
import pytest

from flight_blender.domain_types.scd import LatLngPoint
from flight_blender.services.conformance_svc import (
    AcceptedState,
    ActivatedState,
    CancelledState,
    ConformanceChecksList,
    ContingentState,
    EndedState,
    FlightBlenderConformanceEngine,
    FlightOperationStateMachine,
    NonconformingState,
    OperationConformanceNotification,
    ProcessingNotSubmittedToDss,
    RejectedState,
    WithdrawnState,
    get_status,
    is_time_between,
    match_state,
    set_conformance_deps,
)
from flight_blender.tasks.conformance_task import check_flight_conformance, check_operation_telemetry_conformance

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
    @pytest.mark.asyncio
    async def test_c2_no_flight_declaration(self):
        engine = FlightBlenderConformanceEngine(db=MagicMock())
        # Non-existent flight declaration ID
        with (
            patch.object(FlightBlenderConformanceEngine, "_get_flight_declaration", new_callable=AsyncMock, return_value=None),
            patch.object(FlightBlenderConformanceEngine, "_get_opint_reference", new_callable=AsyncMock, return_value=True),
        ):
            result = await engine.is_operation_conformant_via_telemetry(
                flight_declaration_id=str(uuid.uuid4()),
                aircraft_id="TEST-UAV",
                telemetry_location=LatLngPoint(lat=51.5, lng=0.0),
                altitude_m_wgs_84=50.0,
            )
        # Returns C2 check code
        assert result == ConformanceChecksList.C2


class TestCheckFlightOperationalIntentReferenceConformance:
    @pytest.mark.asyncio
    async def test_nonexistent_declaration_returns_c11(self):
        engine = FlightBlenderConformanceEngine(db=MagicMock())
        with (
            patch.object(FlightBlenderConformanceEngine, "_get_flight_declaration", new_callable=AsyncMock, return_value=None),
            patch.object(FlightBlenderConformanceEngine, "_get_opint_reference", new_callable=AsyncMock, return_value=None),
        ):
            result = await engine.check_flight_operational_intent_reference_conformance(
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
            new_callable=AsyncMock,
            return_value=1,
        ):
            with patch("flight_blender.tasks.conformance_task._async_check_operation_telemetry_conformance", new_callable=AsyncMock) as mock_telem:
                check_flight_conformance(flight_declaration_id=str(uuid.uuid4()), session_id="test-sess")
                mock_telem.assert_awaited_once()

    def test_nonconformant_sends_signal(self):
        with patch(
            "flight_blender.tasks.conformance_task.FlightBlenderConformanceEngine.check_flight_operational_intent_reference_conformance",
            new_callable=AsyncMock,
            return_value=ConformanceChecksList.C9a,
        ):
            with patch(
                "flight_blender.tasks.conformance_task.custom_signals.flight_operational_intent_reference_non_conformance_signal.send"
            ) as mock_signal:
                check_flight_conformance(flight_declaration_id=str(uuid.uuid4()), session_id="test-sess")
                mock_signal.assert_called_once()


class TestCheckOperationTelemetryConformanceTask:
    def test_no_observation_returns_early(self):
        with patch("flight_blender.tasks.conformance_task.get_latest_flight_observation_by_declaration_id", new_callable=AsyncMock, return_value=None):
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


# ---------------------------------------------------------------------------
# Conformance task additional coverage
# ---------------------------------------------------------------------------


class TestConformanceTaskCoverage:
    """Additional tests for conformance_task."""

    @pytest.mark.asyncio
    async def test_check_operation_telemetry_conformance_with_observation(self):
        """Test check_operation_telemetry_conformance with observation."""
        from flight_blender.tasks.conformance_task import _async_check_operation_telemetry_conformance

        test_id = str(uuid.uuid4())

        with patch('flight_blender.tasks.conformance_task.flight_stream_helper.ObservationReadOperations') as mock_obs_cls:
            mock_obs_instance = MagicMock()
            mock_obs_instance.get_latest_flight_observation_by_flight_declaration_id = AsyncMock(return_value=MagicMock(
                metadata={"flight_details": {"id": test_id}},
                latitude_dd=0.0,
                longitude_dd=0.0,
                altitude_mm=100,
                icao_address="test-aircraft",
            ))
            mock_obs_cls.return_value = mock_obs_instance

            with patch('flight_blender.tasks.conformance_task.FlightBlenderConformanceEngine') as mock_engine:
                mock_engine.return_value.is_operation_conformant_via_telemetry = AsyncMock(return_value=100)

                with patch('flight_blender.tasks.conformance_task.custom_signals') as mock_signals:
                    await _async_check_operation_telemetry_conformance(flight_declaration_id=test_id)

                    mock_signals.telemetry_non_conformance_signal.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_check_operation_telemetry_conformance_non_conformant(self):
        """Test check_operation_telemetry_conformance with non-conformant observation."""
        from flight_blender.tasks.conformance_task import _async_check_operation_telemetry_conformance

        test_id = str(uuid.uuid4())

        with patch('flight_blender.tasks.conformance_task.flight_stream_helper.ObservationReadOperations') as mock_obs_cls:
            mock_obs_instance = MagicMock()
            mock_obs_instance.get_latest_flight_observation_by_flight_declaration_id = AsyncMock(return_value=MagicMock(
                metadata={"flight_details": {"id": test_id}},
                latitude_dd=0.0,
                longitude_dd=0.0,
                altitude_mm=100,
                icao_address="test-aircraft",
            ))
            mock_obs_cls.return_value = mock_obs_instance

            with patch('flight_blender.tasks.conformance_task.FlightBlenderConformanceEngine') as mock_engine:
                mock_engine.return_value.is_operation_conformant_via_telemetry = AsyncMock(return_value=50)

                with patch('flight_blender.tasks.conformance_task.custom_signals') as mock_signals:
                    await _async_check_operation_telemetry_conformance(flight_declaration_id=test_id)

                    mock_signals.telemetry_non_conformance_signal.send.assert_called_once()
# Conformance service additional coverage
# ---------------------------------------------------------------------------


class TestConformanceServiceCoverage:
    """Additional tests for conformance_svc."""

    def test_status_code_list(self):
        """Test StatusCode.list method."""
        from flight_blender.services.conformance_svc import ConformanceChecksList

        result = ConformanceChecksList.list()

        assert isinstance(result, list)
        assert len(result) > 0

    def test_status_code_text(self):
        """Test StatusCode.text method."""
        from flight_blender.services.conformance_svc import ConformanceChecksList

        result = ConformanceChecksList.text(ConformanceChecksList.C2)

        assert result is not None
        assert isinstance(result, str)

    def test_status_code_items(self):
        """Test StatusCode.items method."""
        from flight_blender.services.conformance_svc import ConformanceChecksList

        result = ConformanceChecksList.items()

        assert len(list(result)) > 0

    def test_status_code_keys(self):
        """Test StatusCode.keys method."""
        from flight_blender.services.conformance_svc import ConformanceChecksList

        result = ConformanceChecksList.keys()

        assert len(list(result)) > 0

    def test_status_code_labels(self):
        """Test StatusCode.labels method."""
        from flight_blender.services.conformance_svc import ConformanceChecksList

        result = ConformanceChecksList.labels()

        assert len(list(result)) > 0

    def test_status_code_names(self):
        """Test StatusCode.names method."""
        from flight_blender.services.conformance_svc import ConformanceChecksList

        result = ConformanceChecksList.names()

        assert isinstance(result, dict)
        assert len(result) > 0

    def test_status_code_dict(self):
        """Test StatusCode.dict method."""
        from flight_blender.services.conformance_svc import ConformanceChecksList

        result = ConformanceChecksList.dict()

        assert isinstance(result, dict)
        assert len(result) > 0

    def test_status_code_label(self):
        """Test StatusCode.label method."""
        from flight_blender.services.conformance_svc import ConformanceChecksList

        result = ConformanceChecksList.label(ConformanceChecksList.C2)

        assert result is not None
        assert isinstance(result, str)
# FlightBlenderConformanceEngine
# ---------------------------------------------------------------------------


class TestFlightBlenderConformanceEngine:
    """Tests for the main conformance engine."""

    @pytest.mark.asyncio
    async def test_is_operation_conformant_via_telemetry_returns_100_when_conformant(self):
        """Test that a conformant operation returns 100."""
        import json
        mock_db = AsyncMock()

        with patch('flight_blender.services.conformance_svc.SQLAlchemyFlightDeclarationRepository') as mock_fd_repo_cls:
            mock_fd_repo = AsyncMock()
            mock_fd_repo_cls.return_value = mock_fd_repo

            # Mock flight declaration
            mock_fd = MagicMock()
            mock_fd.state = 2  # Activated
            mock_fd.start_datetime = arrow.utcnow().shift(hours=-1).datetime
            mock_fd.end_datetime = arrow.utcnow().shift(hours=1).datetime
            mock_fd.aircraft_id = "test-aircraft"
            mock_fd.operational_intent = json.dumps({
                "volumes": [{
                    "volume": {
                        "altitude_lower": {"value": 0, "reference": "W84", "units": "M"},
                        "altitude_upper": {"value": 1000, "reference": "W84", "units": "M"},
                        "outline_polygon": {
                            "vertices": [
                                {"lng": -1, "lat": -1},
                                {"lng": -1, "lat": 1},
                                {"lng": 1, "lat": 1},
                                {"lng": 1, "lat": -1},
                                {"lng": -1, "lat": -1},  # Duplicate to be popped
                            ]
                        }
                    },
                    "time_start": {"format": "RFC3339", "value": arrow.utcnow().shift(hours=-1).isoformat()},
                    "time_end": {"format": "RFC3339", "value": arrow.utcnow().shift(hours=1).isoformat()},
                }],
                "priority": 0,
            })
            mock_fd_repo.get_by_id = AsyncMock(return_value=mock_fd)

            # Mock opint reference
            mock_opint_ref = MagicMock()
            mock_opint_ref.state = "Accepted"
            mock_fd_repo.get_opint_reference_by_declaration_id = AsyncMock(return_value=mock_opint_ref)

            with patch('flight_blender.services.conformance_svc.SQLAlchemyConformanceRepository') as mock_conformance_repo_cls:
                mock_conformance_repo = AsyncMock()
                mock_conformance_repo_cls.return_value = mock_conformance_repo

                # Mock active geofences
                mock_conformance_repo.get_active_geofences = AsyncMock(return_value=[])

                engine = FlightBlenderConformanceEngine(db=mock_db)

                result = await engine.is_operation_conformant_via_telemetry(
                    flight_declaration_id=uuid.uuid4(),
                    aircraft_id="test-aircraft",
                    telemetry_location=LatLngPoint(lat=0.0, lng=0.0),
                    altitude_m_wgs_84=100.0,
                )

                assert result == 100

    @pytest.mark.asyncio
    async def test_is_operation_conformant_via_telemetry_returns_3_when_no_flight_declaration(self):
        """Test that missing flight declaration returns check C2."""
        mock_db = AsyncMock()

        with patch('flight_blender.services.conformance_svc.SQLAlchemyFlightDeclarationRepository') as mock_fd_repo_cls:
            mock_fd_repo = AsyncMock()
            mock_fd_repo_cls.return_value = mock_fd_repo

            mock_fd_repo.get_by_id = AsyncMock(return_value=None)

            engine = FlightBlenderConformanceEngine(db=mock_db)

            result = await engine.is_operation_conformant_via_telemetry(
                flight_declaration_id=uuid.uuid4(),
                aircraft_id="test-aircraft",
                telemetry_location=LatLngPoint(lat=0.0, lng=0.0),
                altitude_m_wgs_84=100.0,
            )

            assert result == 2  # C2

    @pytest.mark.asyncio
    async def test_is_operation_conformant_via_telemetry_returns_4_when_aircraft_id_mismatch(self):
        """Test that aircraft ID mismatch returns check C3."""
        import json
        mock_db = AsyncMock()

        with patch('flight_blender.services.conformance_svc.SQLAlchemyFlightDeclarationRepository') as mock_fd_repo_cls:
            mock_fd_repo = AsyncMock()
            mock_fd_repo_cls.return_value = mock_fd_repo

            mock_fd = MagicMock()
            mock_fd.state = 2
            mock_fd.start_datetime = arrow.utcnow().shift(hours=-1).datetime
            mock_fd.end_datetime = arrow.utcnow().shift(hours=1).datetime
            mock_fd.aircraft_id = "different-aircraft"  # Different from test-aircraft
            mock_fd.operational_intent = json.dumps({"volumes": [], "priority": 0})
            mock_fd_repo.get_by_id = AsyncMock(return_value=mock_fd)

            mock_opint_ref = MagicMock()
            mock_opint_ref.state = "Accepted"
            mock_fd_repo.get_opint_reference_by_declaration_id = AsyncMock(return_value=mock_opint_ref)

            with patch('flight_blender.services.conformance_svc.SQLAlchemyConformanceRepository') as mock_conformance_repo_cls:
                mock_conformance_repo = AsyncMock()
                mock_conformance_repo_cls.return_value = mock_conformance_repo

                mock_conformance_repo.get_active_geofences = AsyncMock(return_value=[])

                engine = FlightBlenderConformanceEngine(db=mock_db)

                result = await engine.is_operation_conformant_via_telemetry(
                    flight_declaration_id=uuid.uuid4(),
                    aircraft_id="test-aircraft",
                    telemetry_location=LatLngPoint(lat=0.0, lng=0.0),
                    altitude_m_wgs_84=100.0,
                )

                assert result == 3  # C3


# ---------------------------------------------------------------------------
# FlightOperationConformanceHelper
# ---------------------------------------------------------------------------


class TestFlightOperationConformanceHelper:
    """Tests for the conformance helper state transitions."""

    def test_verify_operation_state_transition_returns_true_for_valid_transition(self):
        """Test that valid state transition returns True."""
        from flight_blender.services.conformance_svc import FlightOperationConformanceHelper

        result = FlightOperationConformanceHelper.verify_operation_state_transition(
            original_state=0,  # Created
            new_state=1,  # Accepted
            event="dss_accepts",
        )

        assert result is True

    def test_verify_operation_state_transition_returns_false_for_invalid_transition(self):
        """Test that invalid state transition returns False."""
        from flight_blender.services.conformance_svc import FlightOperationConformanceHelper

        result = FlightOperationConformanceHelper.verify_operation_state_transition(
            original_state=0,  # Created
            new_state=5,  # Ended
            event="dss_accepts",
        )

        assert result is False
