import uuid

from pydantic import BaseModel


class CreateNotificationRequest(BaseModel):
    message: str
    session_id: uuid.UUID | None = None
