import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.models.conformance_orm import ConformanceRecordORM
from flight_blender.models.geo_fence_orm import GeoFenceORM


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

    # ─── Phase 1D additions ───────────────────────────────────────────────────

    async def create_conformance_record(
        self,
        declaration_id: uuid.UUID,
        state: int,
        event_type: str,
        description: str,
        geofence_breach: bool,
        resolved: bool,
    ) -> bool:
        self.db.add(
            ConformanceRecordORM(
                flight_declaration_id=declaration_id,
                conformance_state=state,
                event_type=event_type,
                description=description,
                geofence_breach=geofence_breach,
                resolved=resolved,
            )
        )
        await self.db.flush()
        return True

    async def get_active_geofences(self) -> list[GeoFenceORM]:
        result = await self.db.execute(select(GeoFenceORM).where(GeoFenceORM.status == 0))
        return list(result.scalars().all())


AsyncConformanceRepository = SQLAlchemyConformanceRepository
