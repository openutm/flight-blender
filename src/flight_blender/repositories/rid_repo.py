import json
import uuid
from dataclasses import asdict
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.models.notifications_orm import OperatorRIDNotificationORM
from flight_blender.models.rid_orm import ISASubscriptionORM, RIDFlightDetailORM


class SQLAlchemyRIDRepository:
    """Async repo — used by FastAPI endpoints."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def check_subscription_exists_by_subscription_id(self, subscription_id: str) -> bool:
        result = await self.db.execute(select(ISASubscriptionORM).where(ISASubscriptionORM.subscription_id == uuid.UUID(subscription_id)))
        return result.scalar_one_or_none() is not None

    async def check_subscription_exists_by_view_hash(self, view_hash: int) -> bool:
        result = await self.db.execute(select(ISASubscriptionORM).where(ISASubscriptionORM.view_hash == view_hash))
        return result.scalar_one_or_none() is not None

    async def get_subscription_by_subscription_id(self, subscription_id: str) -> Optional[ISASubscriptionORM]:
        result = await self.db.execute(select(ISASubscriptionORM).where(ISASubscriptionORM.subscription_id == uuid.UUID(subscription_id)))
        return result.scalar_one_or_none()

    async def get_subscription_by_id(self, record_id: str) -> Optional[ISASubscriptionORM]:
        return await self.db.get(ISASubscriptionORM, uuid.UUID(record_id))

    async def update_subscription_flight_details(self, subscription: ISASubscriptionORM, flights_dict: str) -> None:
        subscription.flight_details = flights_dict
        await self.db.flush()

    async def check_flight_detail_exists(self, flight_detail_id: str) -> bool:
        result = await self.db.execute(select(RIDFlightDetailORM).where(RIDFlightDetailORM.id == uuid.UUID(flight_detail_id)))
        return result.scalar_one_or_none() is not None

    async def get_flight_detail_by_id(self, flight_detail_id: str) -> Optional[RIDFlightDetailORM]:
        return await self.db.get(RIDFlightDetailORM, uuid.UUID(flight_detail_id))

    async def get_active_subscriptions_for_view(self, now: datetime) -> list[ISASubscriptionORM]:
        result = await self.db.execute(
            select(ISASubscriptionORM).where(
                ISASubscriptionORM.is_simulated.is_(True), ISASubscriptionORM.end_datetime >= now, ISASubscriptionORM.created_at <= now
            )
        )
        return list(result.scalars().all())

    async def delete_simulated_subscriptions(self) -> None:
        await self.db.execute(delete(ISASubscriptionORM).where(ISASubscriptionORM.is_simulated == True))  # noqa: E712
        await self.db.flush()

    async def delete_all_flight_details(self) -> None:
        await self.db.execute(delete(RIDFlightDetailORM))
        await self.db.flush()

    async def create_subscription(
        self,
        record_id: str,
        subscription_id: str,
        view: str,
        view_hash: int,
        end_datetime: str,
        flights_dict: str,
        is_simulated: bool,
    ) -> bool:
        obj = ISASubscriptionORM(
            id=uuid.UUID(record_id),
            subscription_id=uuid.UUID(subscription_id),
            view=view,
            view_hash=view_hash,
            end_datetime=end_datetime,
            flight_details=flights_dict,
            is_simulated=is_simulated,
        )
        self.db.add(obj)
        try:
            await self.db.flush()
            return True
        except Exception:
            return False

    async def create_or_update_flight_detail(self, rid_flight_details_payload) -> Optional[RIDFlightDetailORM]:
        operator_location = _serialize_dataclass(rid_flight_details_payload.operator_location)
        auth_data = _serialize_dataclass(rid_flight_details_payload.auth_data)
        eu_classification = _serialize_dataclass(rid_flight_details_payload.eu_classification)
        uas_id = _serialize_dataclass(rid_flight_details_payload.uas_id)
        detail_id = uuid.UUID(str(rid_flight_details_payload.id))
        existing = await self.db.get(RIDFlightDetailORM, detail_id)
        if existing:
            existing.operation_description = rid_flight_details_payload.operation_description
            existing.operator_location = operator_location
            existing.operator_id = rid_flight_details_payload.operator_id
            existing.auth_data = auth_data
            existing.uas_id = uas_id
            existing.eu_classification = eu_classification
            await self.db.flush()
            return existing
        obj = RIDFlightDetailORM(
            id=detail_id,
            operation_description=rid_flight_details_payload.operation_description,
            operator_location=operator_location,
            operator_id=rid_flight_details_payload.operator_id,
            auth_data=auth_data,
            uas_id=uas_id,
            eu_classification=eu_classification,
        )
        self.db.add(obj)
        await self.db.flush()
        return obj

    async def get_active_notifications_between(self, start_time: datetime, end_time: datetime):
        result = await self.db.execute(
            select(OperatorRIDNotificationORM).where(
                OperatorRIDNotificationORM.is_active == True,  # noqa: E712
                OperatorRIDNotificationORM.created_at >= start_time,
                OperatorRIDNotificationORM.created_at <= end_time,
            )
        )
        return list(result.scalars().all())


def _serialize_dataclass(obj) -> str:
    if obj is None:
        return json.dumps({})
    return json.dumps(asdict(obj))
