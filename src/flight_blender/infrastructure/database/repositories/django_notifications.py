from datetime import datetime

from django.db.utils import IntegrityError

from flight_blender.notifications.models import OperatorRIDNotification
from flight_blender.rid.data_definitions import OperatorRIDNotificationCreationPayload


class DjangoNotificationsRepository:
    def get_active_user_notifications_between_interval(self, start_time: datetime, end_time: datetime):
        try:
            return OperatorRIDNotification.objects.filter(created_at__gte=start_time, created_at__lte=end_time, is_active=True)
        except OperatorRIDNotification.DoesNotExist:
            return None

    def create_operator_rid_notification(self, operator_rid_notification: OperatorRIDNotificationCreationPayload) -> bool:
        try:
            obj = OperatorRIDNotification(
                message=operator_rid_notification.message,
                session_id=operator_rid_notification.session_id,
            )
            obj.save()
            return True
        except IntegrityError:
            return False
