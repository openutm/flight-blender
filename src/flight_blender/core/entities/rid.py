from dataclasses import dataclass
from enum import Enum
from typing import Any


class NestedDict(dict):
    def convert_value(self, obj):
        if isinstance(obj, Enum):
            return obj.value
        return obj

    def __init__(self, data):
        super().__init__(self.convert_value(x) for x in data if x[1] is not None)


@dataclass
class SignedUnSignedTelemetryObservations:
    current_states: list[Any]
    flight_details: Any
