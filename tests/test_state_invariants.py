"""
Shared operational-intent state invariants.

Restores the load-bearing state groupings from the Django
``common/data_definitions.py`` (dropped in the FastAPI migration) so that
conformance / flight-declaration / SCD logic share one typed source of truth.
These tests pin the exact integer state sets from the Django original.
"""

from flight_blender.common.enums import (
    ACTIVE_OPERATIONAL_STATES,
    VALID_OPERATIONAL_INTENT_STATES,
    OperationState,
)


def test_valid_operational_intent_states():
    """Op-intent states valid for coordination (Django [1, 2, 3, 4])."""
    assert VALID_OPERATIONAL_INTENT_STATES == frozenset(
        {
            OperationState.ACCEPTED,
            OperationState.ACTIVATED,
            OperationState.NONCONFORMING,
            OperationState.CONTINGENT,
        }
    )


def test_active_operational_states():
    """States where the operation is actively airborne (Django [2, 3, 4])."""
    assert ACTIVE_OPERATIONAL_STATES == frozenset(
        {
            OperationState.ACTIVATED,
            OperationState.NONCONFORMING,
            OperationState.CONTINGENT,
        }
    )


def test_active_states_are_subset_of_valid_states():
    assert ACTIVE_OPERATIONAL_STATES <= VALID_OPERATIONAL_INTENT_STATES


def test_state_sets_use_integer_values():
    """The frozensets compare equal to the raw Django integer lists."""
    assert {int(s) for s in VALID_OPERATIONAL_INTENT_STATES} == {1, 2, 3, 4}
    assert {int(s) for s in ACTIVE_OPERATIONAL_STATES} == {2, 3, 4}
