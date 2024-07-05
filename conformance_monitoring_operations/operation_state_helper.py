class State(object):
    """
    A object to hold state transitions as defined in the ASTM F3548-21 standard
    Source: https://dev.to/karn/building-a-simple-state-machine-in-python
    """

    def __init__(self):
        print("Processing current state:%s" % str(self))

    def get_value(self):
        return self._value

    def on_event(self, event):
        pass

    def __repr__(self):
        return self.__str__()

    def __str__(self):
        return self.__class__.__name__


# Start states
class ProcessingNotSubmittedToDss(State):
    def on_event(self, event):
        if event == "dss_accepts":
            return AcceptedState()
        return self


# Start states
class AcceptedState(State):
    def on_event(self, event):
        if event == "operator_activates":
            return ActivatedState()
        elif event == "operator_confirms_ended":
            return EndedState()
        elif event == "ua_departs_early_late_outside_op_intent":
            return NonconformingState()

        return self


class ActivatedState(State):
    def on_event(self, event):
        if event == "operator_confirms_ended":
            return EndedState()
        elif event == "ua_exits_coordinated_op_intent":
            return NonconformingState()
        elif event == "operator_initiates_contingent":
            return ContingentState()

        return self


class EndedState(State):
    def on_event(self, event):
        return self


class NonconformingState(State):
    def on_event(self, event):
        if event == "operator_return_to_coordinated_op_intent":
            return ActivatedState()
        elif event == "operator_confirms_ended":
            return EndedState()
        elif event in ["timeout", "operator_confirms_contingent"]:
            return ContingentState()
        return self


class ContingentState(State):
    def on_event(self, event):
        if event == "operator_confirms_ended":
            return EndedState()

        return self


class WithdrawnState(State):
    def on_event(self, event):
        return self


class CancelledState(State):
    def on_event(self, event):
        return self


class RejectedState(State):
    def on_event(self, event):
        return self


# End states.


class FlightOperationStateMachine(object):
    def __init__(self, state: int = 1):
        s = match_state(state)
        self.state = s

    def on_event(self, event):
        self.state = self.state.on_event(event)


def match_state(status: int):
    if status == 0:
        return ProcessingNotSubmittedToDss()
    elif status == 1:
        return AcceptedState()
    elif status == 2:
        return ActivatedState()
    elif status == 3:
        return NonconformingState()
    elif status == 4:
        return ContingentState()
    elif status == 5:
        return EndedState()
    elif status == 6:
        return WithdrawnState()
    elif status == 7:
        return CancelledState()
    elif status == 8:
        return RejectedState()
    else:
        return False


def get_status(state: State):
    if isinstance(state, ProcessingNotSubmittedToDss):
        return 0
    if isinstance(state, AcceptedState):
        return 1
    elif isinstance(state, ActivatedState):
        return 2
    elif isinstance(state, NonconformingState):
        return 3
    elif isinstance(state, ContingentState):
        return 4
    elif isinstance(state, EndedState):
        return 5
    elif isinstance(state, WithdrawnState):
        return 6
    elif isinstance(state, CancelledState):
        return 7
    elif isinstance(state, RejectedState):
        return 8
    else:
        return False
