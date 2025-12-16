from dotenv import find_dotenv, load_dotenv
from loguru import logger

load_dotenv(find_dotenv())


class State:
    """
    A object to hold state transitions as defined in the ASTM F3548-21 standard
    Source: https://dev.to/karn/building-a-simple-state-machine-in-python
    """

    def __init__(self):
        logger.info("Processing current state:%s" % str(self))

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


class FlightOperationStateMachine:
    def __init__(self, state: int = 1):
        s = match_state(state)
        self.state = s

    def on_event(self, event):
        self.state = self.state.on_event(event)


state_mapping = {
    0: ProcessingNotSubmittedToDss,
    1: AcceptedState,
    2: ActivatedState,
    3: NonconformingState,
    4: ContingentState,
    5: EndedState,
    6: WithdrawnState,
    7: CancelledState,
    8: RejectedState,
}


def match_state(status: int):
    return state_mapping.get(status, lambda: False)()


def get_status(state: State):
    reverse_mapping = {v: k for k, v in state_mapping.items()}
    return reverse_mapping.get(type(state), False)
