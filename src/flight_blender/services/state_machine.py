"""ASTM F3548-21 flight operation state machine.

Ported from Django conformance_monitoring_operations/operation_state_helper.py.
Pure Python — no database or I/O dependencies.
"""

from __future__ import annotations


class _State:
    def on_event(self, event: str) -> "_State":
        return self

    def __repr__(self) -> str:
        return self.__class__.__name__


class _ProcessingNotSubmittedToDss(_State):
    def on_event(self, event: str) -> "_State":
        if event == "dss_accepts":
            return _AcceptedState()
        if event == "operator_withdraws":
            return _WithdrawnState()
        if event == "operator_cancels":
            return _CancelledState()
        return self


class _AcceptedState(_State):
    def on_event(self, event: str) -> "_State":
        if event == "operator_activates":
            return _ActivatedState()
        if event == "operator_confirms_ended":
            return _EndedState()
        if event == "ua_departs_early_late_outside_op_intent":
            return _NonconformingState()
        return self


class _ActivatedState(_State):
    def on_event(self, event: str) -> "_State":
        if event == "operator_confirms_ended":
            return _EndedState()
        if event == "ua_exits_coordinated_op_intent":
            return _NonconformingState()
        if event == "operator_initiates_contingent":
            return _ContingentState()
        return self


class _NonconformingState(_State):
    def on_event(self, event: str) -> "_State":
        if event == "operator_return_to_coordinated_op_intent":
            return _ActivatedState()
        if event == "operator_confirms_ended":
            return _EndedState()
        if event in ("timeout", "operator_confirms_contingent"):
            return _ContingentState()
        return self


class _ContingentState(_State):
    def on_event(self, event: str) -> "_State":
        if event == "operator_confirms_ended":
            return _EndedState()
        return self


class _EndedState(_State):
    pass


class _WithdrawnState(_State):
    pass


class _CancelledState(_State):
    pass


class _RejectedState(_State):
    pass


# int → class mapping (ASTM F3548-21 operational state codes)
_STATE_MAP: dict[int, type[_State]] = {
    0: _ProcessingNotSubmittedToDss,
    1: _AcceptedState,
    2: _ActivatedState,
    3: _NonconformingState,
    4: _ContingentState,
    5: _EndedState,
    6: _WithdrawnState,
    7: _CancelledState,
    8: _RejectedState,
}

_REVERSE_MAP: dict[type[_State], int] = {v: k for k, v in _STATE_MAP.items()}

# Terminal states — no further transitions allowed
TERMINAL_STATES: frozenset[int] = frozenset({5, 6, 7, 8})

# States considered "active" for deconfliction intersection checks
ACTIVE_OPERATIONAL_STATES: list[int] = [1, 2, 3, 4]


def _make_state(state_int: int) -> _State:
    cls = _STATE_MAP.get(state_int)
    if cls is None:
        raise ValueError(f"Unknown state int: {state_int}")
    return cls()


def _state_to_int(state: _State) -> int:
    return _REVERSE_MAP[type(state)]


def is_valid_transition(current_state: int, event: str) -> tuple[bool, int]:
    """Return (valid, new_state_int).

    If the event causes a state change, valid=True and new_state_int is the
    resulting state.  If the state does not change (event not applicable),
    valid=False and new_state_int equals current_state.
    """
    s = _make_state(current_state)
    next_s = s.on_event(event)
    new_int = _state_to_int(next_s)
    return new_int != current_state, new_int


def get_valid_transitions() -> dict[int, list[str]]:
    """Return mapping of state_int → list of events that cause a transition."""
    events = [
        "dss_accepts", "operator_withdraws", "operator_cancels",
        "operator_activates", "operator_confirms_ended",
        "ua_departs_early_late_outside_op_intent",
        "ua_exits_coordinated_op_intent", "operator_initiates_contingent",
        "operator_return_to_coordinated_op_intent", "timeout",
        "operator_confirms_contingent",
    ]
    result: dict[int, list[str]] = {}
    for state_int in range(9):
        valid = []
        for event in events:
            ok, new_int = is_valid_transition(state_int, event)
            if ok:
                valid.append(event)
        result[state_int] = valid
    return result
