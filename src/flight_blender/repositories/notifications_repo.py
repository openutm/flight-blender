import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.models.notifications_orm import OperatorRIDNotificationORM


class SQLAlchemyNotificationsRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_active_notifications_between(self, start_time: datetime, end_time: datetime) -> list[OperatorRIDNotificationORM]:
        result = await self.db.execute(
            select(OperatorRIDNotificationORM).where(
                OperatorRIDNotificationORM.created_at >= start_time,
                OperatorRIDNotificationORM.created_at <= end_time,
                OperatorRIDNotificationORM.is_active == True,  # noqa: E712
            )
        )
        return list(result.scalars().all())

    async def create_notification(self, message: str, session_id: Optional[uuid.UUID] = None) -> OperatorRIDNotificationORM:
        obj = OperatorRIDNotificationORM(message=message, session_id=session_id)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

AsyncNotificationsRepository = SQLAlchemyNotificationsRepository
