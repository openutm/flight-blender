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
    body: str
    level: Literal[
        NotificationLevel.CRITICAL,
        NotificationLevel.ERROR,
        NotificationLevel.WARNING,
        NotificationLevel.INFO,
        NotificationLevel.DEBUG,
    ]
    timestamp: str

# --- HTTP request/response schemas ---
import uuid

from pydantic import BaseModel


class CreateNotificationRequest(BaseModel):
    message: str
    session_id: uuid.UUID | None = None
