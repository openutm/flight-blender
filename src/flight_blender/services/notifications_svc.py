import uuid
from datetime import datetime

import arrow

from flight_blender.repositories.notifications_repo import AsyncNotificationsRepository


class NotificationsOperations:
    def __init__(self, repo: AsyncNotificationsRepository):
        self.repo = repo

    @staticmethod
    def parse_date_range_with_lookback(
        start_date: str | None, end_date: str | None, default_lookback_hours: int = 24
    ) -> tuple[tuple[datetime, datetime] | None, str | None]:
        try:
            start = arrow.get(start_date).datetime if start_date else arrow.utcnow().shift(hours=-default_lookback_hours).datetime
            end = arrow.get(end_date).datetime if end_date else arrow.utcnow().datetime
        except arrow.parser.ParserError:
            return None, "Invalid date format. Use ISO8601 format."
        return (start, end), None

    async def get_active_notifications(self, start_time: datetime, end_time: datetime) -> list[dict]:
        notifications = await self.repo.get_active_notifications_between(start_time, end_time)
        return [
            {
                "id": str(n.id),
                "session_id": str(n.session_id) if n.session_id else None,
                "message": n.message,
                "is_active": n.is_active,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notifications
        ]

    async def create_notification(self, message: str, session_id: uuid.UUID | None = None) -> dict:
        obj = await self.repo.create_notification(message=message, session_id=session_id)
        return {
            "id": str(obj.id),
            "session_id": str(obj.session_id) if obj.session_id else None,
            "message": obj.message,
            "is_active": obj.is_active,
        }
