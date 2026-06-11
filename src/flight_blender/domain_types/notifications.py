from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class NotificationLevel(StrEnum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    DEBUG = "debug"


@dataclass
class FlightDeclarationUpdateMessage:
    body: str
    level: Literal[
        NotificationLevel.CRITICAL,
        NotificationLevel.ERROR,
        NotificationLevel.WARNING,
        NotificationLevel.INFO,
        NotificationLevel.DEBUG,
    ]
    timestamp: str
