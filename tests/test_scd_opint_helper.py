"""
Unit tests for flight_blender.scd/opint_helper.py

Tests DSSOperationalIntentsCreator paths via mocked SCDOperations and DB helpers.
"""

import json
import uuid
from unittest.mock import MagicMock, patch

import arrow
import pytest

from flight_blender.scd.opint_helper import DSSOperationalIntentsCreator
from flight_blender.scd.scd_data_definitions import (
    OperationalIntentSubmissionStatus,
    OtherError,
)


# ---------------------------------------------------------------------------
# Shared fake data builders (centralised in tests/fakes.py but repeated here
# for self-contained clarity on the imports/data shapes needed).
# ---------------------------------------------------------------------------


def _fake_flight_declaration(state=0):
    fd = MagicMock()
    fd.state = state
    fd.start_datetime = arrow.utcnow().shift(minutes=10).datetime
    fd.end_datetime = arrow.utcnow().shift(hours=1).datetime
    fd.operational_intent = json.dumps(
        {
            "state": "Accepted",
            "volumes": [],
            "off_nominal_volumes": [],
            "priority": 0,
        }
    )
    fd.add_state_history_entry = MagicMock()
    return fd


def _fake_opint_ref():
    ref = MagicMock()
    ref.id = uuid.uuid4()
    return ref


def _fake_opint_detail():
    detail = MagicMock()
    detail.id = uuid.uuid4()
    return detail


def _submission_success():
    return OperationalIntentSubmissionStatus(
        status="success",
        status_code=201,
        message="Created",
        dss_response=MagicMock(operational_intent_reference=_fake_opint_ref()),
        operational_intent_id=str(uuid.uuid4()),
        constraints=[],
    )


def _submission_auth_error():
    return {"error": "auth_error"}


def _submission_conflict():
    return OperationalIntentSubmissionStatus(
        status="conflict_with_flight",
        status_code=500,
        message="conflict_with_flight",
        dss_response=OtherError(notes="conflict"),
        operational_intent_id=str(uuid.uuid4()),
    )


def _submission_dss_error(code=400):
    return OperationalIntentSubmissionStatus(
        status="dss_error",
        status_code=code,
        message="DSS error",
        dss_response=OtherError(notes="error"),
        operational_intent_id=str(uuid.uuid4()),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDSSOperationalIntentsCreatorValidateTime:
    def test_valid_start_end_time(self):
        """Declarations starting within 2 h return True."""
        creator = DSSOperationalIntentsCreator(flight_declaration_id=str(uuid.uuid4()))
        fd = _fake_flight_declaration()
        fd.start_datetime = arrow.utcnow().shift(minutes=10).datetime
        fd.end_datetime = arrow.utcnow().shift(minutes=90).datetime
        with patch.object(creator.my_database_reader, "get_flight_declaration_by_id", return_value=fd):
            assert creator.validate_flight_declaration_start_end_time() is True

    def test_past_start_time_returns_false(self):
        """Declarations with start in the past return False."""
        creator = DSSOperationalIntentsCreator(flight_declaration_id=str(uuid.uuid4()))
        fd = _fake_flight_declaration()
        fd.start_datetime = arrow.utcnow().shift(hours=-1).datetime
        fd.end_datetime = arrow.utcnow().shift(hours=1).datetime
        with patch.object(creator.my_database_reader, "get_flight_declaration_by_id", return_value=fd):
            assert creator.validate_flight_declaration_start_end_time() is False

    def test_far_future_start_time_returns_false(self):
        """Declarations starting more than 2 h from now return False."""
        creator = DSSOperationalIntentsCreator(flight_declaration_id=str(uuid.uuid4()))
        fd = _fake_flight_declaration()
        fd.start_datetime = arrow.utcnow().shift(hours=3).datetime
        fd.end_datetime = arrow.utcnow().shift(hours=4).datetime
        with patch.object(creator.my_database_reader, "get_flight_declaration_by_id", return_value=fd):
            assert creator.validate_flight_declaration_start_end_time() is False


class TestDSSOperationalIntentsCreatorSubmit:
    def test_not_found_returns_declaration_not_found(self):
        creator = DSSOperationalIntentsCreator(flight_declaration_id=str(uuid.uuid4()))
        with patch.object(creator.my_database_reader, "get_flight_declaration_by_id", return_value=None):
            result = creator.submit_flight_declaration_to_dss()
        assert result.status == "declaration_not_found"
        assert result.status_code == 404

    def test_auth_error_returns_auth_server_error(self):
        creator = DSSOperationalIntentsCreator(flight_declaration_id=str(uuid.uuid4()))
        fd = _fake_flight_declaration()
        with patch.object(creator.my_database_reader, "get_flight_declaration_by_id", return_value=fd):
            with patch.object(creator.my_scd_dss_helper, "get_auth_token", return_value={"error": "conn_error"}):
                with patch.object(creator.my_database_writer, "update_flight_operation_state"):
                    result = creator.submit_flight_declaration_to_dss()
        assert result.status == "auth_server_error"
        assert result.status_code == 500

    def test_successful_submission_updates_state(self):
        creator = DSSOperationalIntentsCreator(flight_declaration_id=str(uuid.uuid4()))
        fd = _fake_flight_declaration()
        opint_ref = _fake_opint_ref()
        opint_detail = _fake_opint_detail()
        success = _submission_success()
        with patch.object(creator.my_database_reader, "get_flight_declaration_by_id", return_value=fd):
            with patch.object(creator.my_scd_dss_helper, "get_auth_token", return_value={"access_token": "tok"}):
                with patch.object(creator.my_scd_dss_helper, "create_and_submit_operational_intent_reference", return_value=success):
                    with patch.object(creator.my_database_writer, "create_flight_operational_intent_reference", return_value=opint_ref):
                        with patch.object(
                            creator.my_database_writer,
                            "create_flight_operational_intent_details_with_submitted_operational_intent",
                            return_value=opint_detail,
                        ):
                            with patch.object(
                                creator.my_operational_intent_reference_helper,
                                "generate_bounds_altitude_time_for_volumes",
                                return_value=MagicMock(
                                    bounds="0,0,1,1",
                                    start_datetime=arrow.utcnow().shift(minutes=5).datetime,
                                    end_datetime=arrow.utcnow().shift(hours=1).datetime,
                                    alt_max=100,
                                    alt_min=0,
                                ),
                            ):
                                with patch.object(creator.my_database_writer, "create_or_update_composite_operational_intent"):
                                    with patch.object(creator.my_database_writer, "update_flight_operation_state") as mock_state:
                                        result = creator.submit_flight_declaration_to_dss()
        assert result.status_code == 201
        mock_state.assert_called_once_with(flight_declaration_id=creator.flight_declaration_id, state=1)

    def test_dss_400_error_sets_rejected_state(self):
        creator = DSSOperationalIntentsCreator(flight_declaration_id=str(uuid.uuid4()))
        fd = _fake_flight_declaration()
        with patch.object(creator.my_database_reader, "get_flight_declaration_by_id", return_value=fd):
            with patch.object(creator.my_scd_dss_helper, "get_auth_token", return_value={"access_token": "tok"}):
                with patch.object(
                    creator.my_scd_dss_helper,
                    "create_and_submit_operational_intent_reference",
                    return_value=_submission_dss_error(400),
                ):
                    with patch.object(creator.my_database_writer, "update_flight_operation_state") as mock_state:
                        creator.submit_flight_declaration_to_dss()
        mock_state.assert_called_once_with(flight_declaration_id=creator.flight_declaration_id, state=8)

    def test_dss_409_error_sets_rejected_state(self):
        creator = DSSOperationalIntentsCreator(flight_declaration_id=str(uuid.uuid4()))
        fd = _fake_flight_declaration()
        with patch.object(creator.my_database_reader, "get_flight_declaration_by_id", return_value=fd):
            with patch.object(creator.my_scd_dss_helper, "get_auth_token", return_value={"access_token": "tok"}):
                with patch.object(
                    creator.my_scd_dss_helper,
                    "create_and_submit_operational_intent_reference",
                    return_value=_submission_dss_error(409),
                ):
                    with patch.object(creator.my_database_writer, "update_flight_operation_state") as mock_state:
                        creator.submit_flight_declaration_to_dss()
        mock_state.assert_called_once_with(flight_declaration_id=creator.flight_declaration_id, state=8)

    def test_conflict_with_flight_sets_rejected_state(self):
        creator = DSSOperationalIntentsCreator(flight_declaration_id=str(uuid.uuid4()))
        fd = _fake_flight_declaration()
        with patch.object(creator.my_database_reader, "get_flight_declaration_by_id", return_value=fd):
            with patch.object(creator.my_scd_dss_helper, "get_auth_token", return_value={"access_token": "tok"}):
                with patch.object(
                    creator.my_scd_dss_helper,
                    "create_and_submit_operational_intent_reference",
                    return_value=_submission_conflict(),
                ):
                    with patch.object(creator.my_database_writer, "update_flight_operation_state") as mock_state:
                        creator.submit_flight_declaration_to_dss()
        mock_state.assert_called_once_with(flight_declaration_id=creator.flight_declaration_id, state=8)
