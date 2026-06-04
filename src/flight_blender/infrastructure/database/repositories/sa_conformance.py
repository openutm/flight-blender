from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.infrastructure.database.models.conformance import ConformanceRecordORM


class SQLAlchemyConformanceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_conformance_records_for_duration(self, start_time: datetime, end_time: datetime) -> list[ConformanceRecordORM]:
        result = await self.db.execute(
            select(ConformanceRecordORM)
            .where(
                ConformanceRecordORM.created_at >= start_time,
                ConformanceRecordORM.created_at <= end_time,
            )
            .order_by(ConformanceRecordORM.created_at.desc())
        )
        return list(result.scalars().all())
