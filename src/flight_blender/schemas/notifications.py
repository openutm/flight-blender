import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Literal

from pydantic import BaseModel


class NotificationLevel(Enum):
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


class CreateNotificationRequest(BaseModel):
    message: str
    session_id: uuid.UUID | None = None
