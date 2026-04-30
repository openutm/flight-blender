"""Tests for example_plugins.hello_world_engine."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from django.test import TestCase

from example_plugins.hello_world_engine import (
    HelloWorldEngine,
    _ACTIVE_STATES,
    _STATE_ACCEPTED,
    _STATE_NOT_SUBMITTED,
    _STATE_REJECTED,
)
from flight_declaration_operations.data_definitions import DeconflictionRequest


def _make_request(ussp_network_enabled=0, declaration_id=None):
    now = datetime.now(tz=timezone.utc)
    return DeconflictionRequest(
        start_datetime=now,
        end_datetime=now + timedelta(hours=1),
        view_box=[0.0, 0.0, 1.0, 1.0],
        ussp_network_enabled=ussp_network_enabled,
        declaration_id=declaration_id,
    )


class TestStateConstants(TestCase):
    """Verify state constants match common/data_definitions.py OPERATION_STATES."""

    def test_not_submitted_is_zero(self):
        self.assertEqual(_STATE_NOT_SUBMITTED, 0)

    def test_accepted_is_one(self):
        self.assertEqual(_STATE_ACCEPTED, 1)

    def test_rejected_is_eight(self):
        self.assertEqual(_STATE_REJECTED, 8)

    def test_active_states_match_spec(self):
        # Accepted, Activated, Nonconforming, Contingent
        self.assertEqual(sorted(_ACTIVE_STATES), [1, 2, 3, 4])


class TestHelloWorldEngineNoConflicts(TestCase):
    """No overlapping declarations → approval."""

    @patch("example_plugins.hello_world_engine.FlightDeclaration.objects")
    def test_approved_ussp_disabled_returns_accepted(self, mock_objects):
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.exclude.return_value = qs
        qs.values_list.return_value.__getitem__ = MagicMock(return_value=[])
        qs.values_list.return_value = []
        mock_objects.filter.return_value = qs

        engine = HelloWorldEngine()
        result = engine.check_deconfliction(_make_request(ussp_network_enabled=0))

        self.assertTrue(result.is_approved)
        self.assertEqual(result.declaration_state, _STATE_ACCEPTED)

    @patch("example_plugins.hello_world_engine.FlightDeclaration.objects")
    def test_approved_ussp_enabled_returns_not_submitted(self, mock_objects):
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.values_list.return_value = []
        mock_objects.filter.return_value = qs

        engine = HelloWorldEngine()
        result = engine.check_deconfliction(_make_request(ussp_network_enabled=1))

        self.assertTrue(result.is_approved)
        self.assertEqual(result.declaration_state, _STATE_NOT_SUBMITTED)


class TestHelloWorldEngineWithConflicts(TestCase):
    """Overlapping declarations → rejection."""

    @patch("example_plugins.hello_world_engine.FlightDeclaration.objects")
    def test_rejected_when_conflicts_exist(self, mock_objects):
        conflict_id = "conflict-uuid-1"
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.exclude.return_value = qs
        qs.values_list.return_value.__getitem__ = MagicMock(return_value=[conflict_id])
        # Simulate one conflicting declaration
        qs.values_list.return_value = [conflict_id]
        mock_objects.filter.return_value = qs

        engine = HelloWorldEngine()
        result = engine.check_deconfliction(_make_request(declaration_id="test-uuid"))

        self.assertFalse(result.is_approved)
        self.assertEqual(result.declaration_state, _STATE_REJECTED)

    @patch("example_plugins.hello_world_engine.FlightDeclaration.objects")
    def test_conflicting_ids_returned_in_result(self, mock_objects):
        conflict_ids = ["id-1", "id-2"]
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.exclude.return_value = qs
        qs.values_list.return_value = conflict_ids
        mock_objects.filter.return_value = qs

        engine = HelloWorldEngine()
        result = engine.check_deconfliction(_make_request(declaration_id="test-uuid"))

        self.assertEqual(result.all_relevant_declarations, conflict_ids)


class TestHelloWorldEngineFilter(TestCase):
    """Verify the ORM filter uses _ACTIVE_STATES and the correct time window."""

    @patch("example_plugins.hello_world_engine.FlightDeclaration.objects")
    def test_filter_uses_active_states(self, mock_objects):
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.values_list.return_value = []
        mock_objects.filter.return_value = qs

        request = _make_request()
        engine = HelloWorldEngine()
        engine.check_deconfliction(request)

        mock_objects.filter.assert_called_once_with(
            start_datetime__lt=request.end_datetime,
            end_datetime__gt=request.start_datetime,
            state__in=_ACTIVE_STATES,
        )

    @patch("example_plugins.hello_world_engine.FlightDeclaration.objects")
    def test_self_excluded_when_declaration_id_provided(self, mock_objects):
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.exclude.return_value = qs
        qs.values_list.return_value = []
        mock_objects.filter.return_value = qs

        engine = HelloWorldEngine()
        engine.check_deconfliction(_make_request(declaration_id="self-id"))

        qs.exclude.assert_called_once_with(pk="self-id")

    @patch("example_plugins.hello_world_engine.FlightDeclaration.objects")
    def test_no_exclude_when_no_declaration_id(self, mock_objects):
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.values_list.return_value = []
        mock_objects.filter.return_value = qs

        engine = HelloWorldEngine()
        engine.check_deconfliction(_make_request(declaration_id=None))

        qs.exclude.assert_not_called()
