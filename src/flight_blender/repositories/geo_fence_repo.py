import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.models.geo_fence_orm import GeoFenceORM


class SQLAlchemyGeoFenceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_geofences_overlapping_time_window(
        self,
        start: datetime,
        end: datetime,
    ) -> list[GeoFenceORM]:
        stmt = select(GeoFenceORM).where(
            GeoFenceORM.start_datetime <= start,
            GeoFenceORM.end_datetime >= end,
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_geofences_by_date_range(
        self,
        start: datetime,
        end: datetime,
        is_test: bool = False,
    ) -> list[GeoFenceORM]:
        stmt = select(GeoFenceORM).where(
            GeoFenceORM.start_datetime <= end,
            GeoFenceORM.end_datetime >= start,
            GeoFenceORM.is_test_dataset == is_test,
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, geofence_id: uuid.UUID) -> GeoFenceORM | None:
        result = await self.db.get(GeoFenceORM, geofence_id)
        return result

    async def create(self, **kwargs: Any) -> GeoFenceORM:
        fence = GeoFenceORM(**kwargs)
        self.db.add(fence)
        await self.db.flush()
        await self.db.refresh(fence)
        return fence

    async def delete(self, geofence_id: uuid.UUID) -> bool:
        fence = await self.get_by_id(geofence_id)
        if fence is None:
            return False
        await self.db.delete(fence)
        await self.db.flush()
        return True

    async def get_test_geofences(self) -> list[GeoFenceORM]:
        stmt = select(GeoFenceORM).where(GeoFenceORM.is_test_dataset == True)  # noqa: E712
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def delete_test_geofences(self) -> int:
        fences = await self.get_test_geofences()
        for fence in fences:
            await self.db.delete(fence)
        await self.db.flush()
        return len(fences)

    async def get_geospatial_data_sources(self, start: datetime, end: datetime) -> list[GeoFenceORM]:
        stmt = (
            select(GeoFenceORM)
            .where(
                GeoFenceORM.start_datetime >= start,
                GeoFenceORM.end_datetime <= end,
                GeoFenceORM.is_test_dataset == False,  # noqa: E712
            )
            .order_by(GeoFenceORM.created_at)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
