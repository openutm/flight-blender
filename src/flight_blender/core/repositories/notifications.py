from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from flight_blender.rid.data_definitions import OperatorRIDNotificationCreationPayload


@runtime_checkable
class NotificationsRepository(Protocol):
    def get_active_user_notifications_between_interval(self, start_time: datetime, end_time: datetime) -> Any | None: ...
    def create_operator_rid_notification(self, operator_rid_notification: OperatorRIDNotificationCreationPayload) -> bool: ...
