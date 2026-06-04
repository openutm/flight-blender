import json
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.infrastructure.database.models.flight_declarations import FlightDeclarationORM, FlightOperationTrackingORM


class SQLAlchemyFlightDeclarationRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, **kwargs) -> FlightDeclarationORM:
        obj = FlightDeclarationORM(**kwargs)
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def get_by_id(self, declaration_id: uuid.UUID) -> FlightDeclarationORM | None:
        result = await self.db.execute(select(FlightDeclarationORM).where(FlightDeclarationORM.id == declaration_id))
        return result.scalar_one_or_none()

    async def list(
        self,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        states: list[int] | None = None,
    ) -> list[FlightDeclarationORM]:
        stmt = select(FlightDeclarationORM)
        if start_date is not None:
            stmt = stmt.where(FlightDeclarationORM.start_datetime >= start_date)
        if end_date is not None:
            stmt = stmt.where(FlightDeclarationORM.end_datetime <= end_date)
        if states:
            stmt = stmt.where(FlightDeclarationORM.state.in_(states))
        stmt = stmt.order_by(FlightDeclarationORM.created_at.desc())
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def update(self, declaration_id: uuid.UUID, **fields) -> FlightDeclarationORM | None:
        obj = await self.get_by_id(declaration_id)
        if obj is None:
            return None
        for key, value in fields.items():
            setattr(obj, key, value)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def add_state_history_entry(
        self,
        flight_declaration_id: uuid.UUID,
        original_state: int,
        new_state: int,
        notes: str = "",
    ) -> None:
        original = original_state or "start"
        entry = FlightOperationTrackingORM(
            flight_declaration_id=flight_declaration_id,
            notes=notes,
            deltas=json.dumps({"original_state": str(original), "new_state": str(new_state)}),
        )
        self.db.add(entry)
        await self.db.flush()

    async def delete(self, declaration_id: uuid.UUID) -> bool:
        obj = await self.get_by_id(declaration_id)
        if obj is None:
            return False
        await self.db.delete(obj)
        await self.db.flush()
        return True

    @staticmethod
    def serialize(obj: FlightDeclarationORM) -> dict:
        return {
            "id": str(obj.id),
            "operational_intent": json.loads(obj.operational_intent),
            "originating_party": obj.originating_party,
            "type_of_operation": obj.type_of_operation,
            "state": obj.state,
            "is_approved": obj.is_approved,
            "start_datetime": obj.start_datetime.isoformat() if obj.start_datetime else None,
            "end_datetime": obj.end_datetime.isoformat() if obj.end_datetime else None,
            "flight_declaration_geojson": json.loads(obj.flight_declaration_raw_geojson) if obj.flight_declaration_raw_geojson else None,
            "flight_declaration_raw_geojson": json.loads(obj.flight_declaration_raw_geojson) if obj.flight_declaration_raw_geojson else None,
            "bounds": obj.bounds,
            "approved_by": obj.approved_by,
            "submitted_by": obj.submitted_by,
        }
