import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.models.constraint_orm import CompositeConstraintORM, ConstraintDetailORM, ConstraintReferenceORM


class SQLAlchemyConstraintRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_constraint_detail_by_id(self, constraint_id: uuid.UUID) -> Optional[ConstraintDetailORM]:
        return await self.db.get(ConstraintDetailORM, constraint_id)

    async def get_constraint_details(self) -> list[ConstraintDetailORM]:
        result = await self.db.execute(select(ConstraintDetailORM).order_by(ConstraintDetailORM.created_at.desc()))
        return list(result.scalars().all())

    async def create_constraint_detail(self, **kwargs) -> ConstraintDetailORM:
        obj = ConstraintDetailORM(**kwargs)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def get_constraint_reference_by_id(self, ref_id: uuid.UUID) -> Optional[ConstraintReferenceORM]:
        return await self.db.get(ConstraintReferenceORM, ref_id)

    async def get_constraint_references(self) -> list[ConstraintReferenceORM]:
        result = await self.db.execute(select(ConstraintReferenceORM).order_by(ConstraintReferenceORM.created_at.desc()))
        return list(result.scalars().all())

    async def create_constraint_reference(self, **kwargs) -> ConstraintReferenceORM:
        obj = ConstraintReferenceORM(**kwargs)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def update_constraint_reference_ovn(self, ref_id: uuid.UUID, ovn: str) -> bool:
        ref = await self.get_constraint_reference_by_id(ref_id)
        if ref is None:
            return False
        ref.ovn = ovn
        await self.db.flush()
        return True

    async def create_composite_constraint(self, **kwargs) -> CompositeConstraintORM:
        obj = CompositeConstraintORM(**kwargs)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

AsyncConstraintRepository = SQLAlchemyConstraintRepository
