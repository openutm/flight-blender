"""Central repository of all fake objects, stub factories, and mock helpers.

All mocks, stubs, and fake factories used across every test suite live here.
Refactoring an external integration (DSS, auth server, …) requires changes in
exactly **one** place: this file.

Usage
-----
Import the factory functions directly, or request fixtures from conftest.py
that are built on top of these factories.  Test files should never call
``unittest.mock.patch`` themselves; instead they declare the relevant
conftest fixture as a parameter.
"""

import uuid

import arrow

# ---------------------------------------------------------------------------
# Auth-token fakes
# ---------------------------------------------------------------------------

FAKE_ACCESS_TOKEN = "fake-dss-access-token-for-testing"


def fake_auth_token_success() -> dict:
    """Valid auth-token response (no ``error`` key)."""
    return {"access_token": FAKE_ACCESS_TOKEN, "token_type": "Bearer", "expires_in": 3600}


def fake_auth_token_error() -> dict:
    """Auth-token error response (has ``error`` key)."""
    return {"error": "unable_to_connect", "error_description": "Cannot reach auth server"}


# ---------------------------------------------------------------------------
# SCD / Operational-intent DSS response fakes
# ---------------------------------------------------------------------------


def _fake_opint_reference(state: str = "Accepted", opint_id: str | None = None):
    """Build a minimal :class:`OperationalIntentReferenceDSSResponse`."""
    from scd_operations.scd_data_definitions import (
        OperationalIntentReferenceDSSResponse,
        Time,
    )

    now = arrow.now()
    return OperationalIntentReferenceDSSResponse(
        id=opint_id or str(uuid.uuid4()),
        manager="test-manager",
        uss_availability="Unknown",
        version=1,
        state=state,
        ovn="test-ovn-" + str(uuid.uuid4())[:8],
        time_start=Time(format="RFC3339", value=now.isoformat()),
        time_end=Time(format="RFC3339", value=now.shift(hours=1).isoformat()),
        uss_base_url="http://flight-blender:8000",
        subscription_id=str(uuid.uuid4()),
    )


def fake_submission_success(
    state: str = "Accepted",
    operational_intent_id: str | None = None,
):
    """Return a successful :class:`OperationalIntentSubmissionStatus`."""
    from scd_operations.scd_data_definitions import (
        OperationalIntentSubmissionStatus,
        OperationalIntentSubmissionSuccess,
    )

    opint_id = operational_intent_id or str(uuid.uuid4())
    reference = _fake_opint_reference(state=state, opint_id=opint_id)
    dss_response = OperationalIntentSubmissionSuccess(
        subscribers=[],
        operational_intent_reference=reference,
    )
    return OperationalIntentSubmissionStatus(
        dss_response=dss_response,
        status="success",
        status_code=200,
        message="Operational intent created successfully",
        operational_intent_id=opint_id,
    )


def fake_submission_conflict():
    """Return a conflict-with-flight :class:`OperationalIntentSubmissionStatus`."""
    from scd_operations.scd_data_definitions import OtherError, OperationalIntentSubmissionStatus

    return OperationalIntentSubmissionStatus(
        dss_response=OtherError(notes="Conflict with existing flight in DSS"),
        status="conflict_with_flight",
        status_code=409,
        message="Conflict with existing flight",
        operational_intent_id=str(uuid.uuid4()),
    )


def fake_submission_failure(status_code: int = 500):
    """Return a failed :class:`OperationalIntentSubmissionStatus`."""
    from scd_operations.scd_data_definitions import OtherError, OperationalIntentSubmissionStatus

    return OperationalIntentSubmissionStatus(
        dss_response=OtherError(notes="DSS returned an error"),
        status="failure",
        status_code=status_code,
        message="DSS submission failed",
        operational_intent_id=str(uuid.uuid4()),
    )


def fake_submission_timeout():
    """Return a timeout :class:`OperationalIntentSubmissionStatus` (408)."""
    return fake_submission_failure(status_code=408)


def fake_delete_success():
    """Return a successful DSS operational-intent deletion response."""
    from scd_operations.scd_data_definitions import (
        CommonDSS2xxResponse,
        DeleteOperationalIntentResponse,
        DeleteOperationalIntentResponseSuccess,
    )

    reference = _fake_opint_reference()
    dss_response = DeleteOperationalIntentResponseSuccess(
        subscribers=[],
        operational_intent_reference=reference,
    )
    return DeleteOperationalIntentResponse(
        dss_response=dss_response,
        status=200,
        message=CommonDSS2xxResponse(message="Operational intent deleted successfully"),
    )


def fake_delete_failure():
    """Return a failed DSS operational-intent deletion response."""
    from scd_operations.scd_data_definitions import CommonDSS4xxResponse, DeleteOperationalIntentResponse

    return DeleteOperationalIntentResponse(
        dss_response=CommonDSS4xxResponse(message="Operational intent not found in DSS"),
        status=404,
        message=CommonDSS4xxResponse(message="Operational intent not found"),
    )


# ---------------------------------------------------------------------------
# Nearby-operational-intents fakes
# ---------------------------------------------------------------------------


def fake_empty_nearby_operational_intents():
    """Return an empty list – no conflicts in the airspace."""
    return []


def fake_noop(*args, **kwargs):
    """A no-op callable used to silence fire-and-forget side-effects."""
    return None


# ---------------------------------------------------------------------------
# Valid UAV identifiers (for passing validation gates in views)
# ---------------------------------------------------------------------------

# ANSI/CTA-2063-A compliant serial number:
#   manufacturer_code="ABCD" (no O/I), length_code="5", body="EFGHJ" (5 chars)
VALID_UAS_SERIAL_NUMBER = "ABCD5EFGHJ"

# EN4709-02 compliant operator registration number.
# Computed checksum for base_id="87astrdge12k" + suffix="abc" is "h".
# Full format: {country(3)}{base_id(12)}{checksum(1)}-{suffix(3)}
VALID_OPERATOR_ID = "fin87astrdge12kh-abc"
