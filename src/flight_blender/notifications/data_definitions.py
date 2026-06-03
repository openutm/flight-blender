from dataclasses import dataclass
from enum import Enum
from typing import Literal


class NotificationLevel(Enum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    DEBUG = "debug"


@dataclass
class FlightDeclarationUpdateMessage:
    """This object will hold messages that will go to the operational Notifications"""

    body: str
    level: Literal[
        NotificationLevel.CRITICAL,
        NotificationLevel.ERROR,
        NotificationLevel.WARNING,
        NotificationLevel.INFO,
        NotificationLevel.DEBUG,
    ]
    timestamp: str
