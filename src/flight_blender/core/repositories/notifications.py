from datetime import datetime
from typing import Any, Protocol, runtime_checkable
from uuid import UUID


@runtime_checkable
class AsyncNotificationsRepository(Protocol):
    async def get_active_notifications_between(self, start_time: datetime, end_time: datetime) -> Any: ...
    async def create_notification(self, message: str, session_id: UUID | None = None) -> Any: ...


@runtime_checkable
class NotificationsRepository(Protocol):
    def get_active_user_notifications_between_interval(self, start_time: datetime, end_time: datetime) -> Any | None: ...
    def create_operator_rid_notification(self, operator_rid_notification: Any) -> bool: ...
