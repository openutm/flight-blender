from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.models.flight_declarations_orm import (
    CompositeOperationalIntentORM,
    FlightDeclarationORM,
    FlightOperationalIntentDetailORM,
    FlightOperationalIntentReferenceORM,
    FlightOperationTrackingORM,
    PeerCompositeOperationalIntentORM,
    PeerOperationalIntentDetailORM,
    PeerOperationalIntentReferenceORM,
    SubscriberORM,
)


def _coerce_datetime(value):
    if isinstance(value, datetime):
        return value
    if hasattr(value, "datetime"):
        return value.datetime
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


class CompositeBundle:
    """Eagerly-loaded composite operational intent with related detail and reference rows."""

    def __init__(
        self,
        composite_id: uuid.UUID,
        details: Optional[FlightOperationalIntentDetailORM],
        reference: Optional[FlightOperationalIntentReferenceORM],
        bounds: str = "",
        start_datetime: Optional[datetime] = None,
        end_datetime: Optional[datetime] = None,
        alt_max: float = 0.0,
        alt_min: float = 0.0,
    ) -> None:
        self.id = composite_id
        self.operational_intent_details = details
        self.operational_intent_reference = reference
        self.bounds = bounds
        self.start_datetime = start_datetime
        self.end_datetime = end_datetime
        self.alt_max = alt_max
        self.alt_min = alt_min


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

    async def update_telemetry_timestamp(self, declaration_id: uuid.UUID) -> bool:
        obj = await self.get_by_id(declaration_id)
        if obj is None:
            return False
        obj.latest_telemetry_datetime = datetime.now(timezone.utc)
        await self.db.flush()
        return True

    # ─── FlightOperationalIntentReference ────────────────────────────────────

    async def get_opint_reference_by_declaration_id(self, declaration_id: uuid.UUID) -> FlightOperationalIntentReferenceORM | None:
        result = await self.db.execute(
            select(FlightOperationalIntentReferenceORM).where(FlightOperationalIntentReferenceORM.declaration_id == declaration_id)
        )
        return result.scalar_one_or_none()

    async def get_opint_reference_by_id(self, ref_id: uuid.UUID) -> FlightOperationalIntentReferenceORM | None:
        return await self.db.get(FlightOperationalIntentReferenceORM, ref_id)

    async def create_opint_reference(self, declaration_id: uuid.UUID, payload) -> FlightOperationalIntentReferenceORM:
        obj = FlightOperationalIntentReferenceORM(
            id=uuid.UUID(str(payload.id)),
            declaration_id=declaration_id,
            ovn=payload.ovn,
            state=payload.state,
            uss_availability=payload.uss_availability,
            uss_base_url=payload.uss_base_url,
            version=str(payload.version),
            manager=payload.manager,
            time_start=_coerce_datetime(payload.time_start.value),
            time_end=_coerce_datetime(payload.time_end.value),
            subscription_id=payload.subscription_id,
        )
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def update_opint_reference(self, ref_id: uuid.UUID, payload) -> bool:
        ref = await self.db.get(FlightOperationalIntentReferenceORM, ref_id)
        if ref is None:
            return False
        ref.ovn = payload.ovn
        ref.state = payload.state
        ref.uss_availability = payload.uss_availability
        ref.uss_base_url = payload.uss_base_url
        ref.version = str(payload.version)
        ref.time_start = _coerce_datetime(payload.time_start.value)
        ref.time_end = _coerce_datetime(payload.time_end.value)
        ref.subscription_id = payload.subscription_id
        ref.manager = payload.manager
        await self.db.flush()
        return True

    async def update_opint_reference_ovn(self, ref_id: uuid.UUID, ovn: str) -> bool:
        ref = await self.db.get(FlightOperationalIntentReferenceORM, ref_id)
        if ref is None:
            return False
        ref.ovn = ovn
        await self.db.flush()
        return True

    async def create_opint_reference_subscribers(self, declaration_id: uuid.UUID, subscribers) -> bool:
        ref = await self.get_opint_reference_by_declaration_id(declaration_id)
        if ref is None:
            return False
        for subscriber in subscribers:
            all_subscriptions = [asdict(s) for s in subscriber.subscriptions]
            self.db.add(
                SubscriberORM(
                    operational_intent_reference_id=ref.id,
                    uss_base_url=subscriber.uss_base_url,
                    subscriptions=json.dumps(all_subscriptions),
                )
            )
        await self.db.flush()
        return True

    async def get_subscribers_of_opint_reference(self, ref_id: uuid.UUID) -> list[SubscriberORM]:
        result = await self.db.execute(select(SubscriberORM).where(SubscriberORM.operational_intent_reference_id == ref_id))
        return list(result.scalars().all())

    # ─── FlightOperationalIntentDetail ───────────────────────────────────────

    async def get_opint_detail_by_declaration_id(self, declaration_id: uuid.UUID) -> FlightOperationalIntentDetailORM | None:
        result = await self.db.execute(
            select(FlightOperationalIntentDetailORM).where(FlightOperationalIntentDetailORM.declaration_id == declaration_id)
        )
        return result.scalar_one_or_none()

    async def create_opint_detail(self, declaration_id: uuid.UUID, payload) -> FlightOperationalIntentDetailORM:
        _payload = asdict(payload)
        obj = FlightOperationalIntentDetailORM(
            declaration_id=declaration_id,
            volumes=json.dumps(_payload["volumes"]),
            off_nominal_volumes=json.dumps(_payload["off_nominal_volumes"]),
            priority=payload.priority,
        )
        self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def update_opint_detail(self, detail_id: uuid.UUID, payload) -> bool:
        obj = await self.db.get(FlightOperationalIntentDetailORM, detail_id)
        if obj is None:
            return False
        _volumes = [asdict(v) for v in (payload.volumes or [])]
        _off_nominal = [asdict(v) for v in (payload.off_nominal_volumes or [])]
        obj.volumes = json.dumps(_volumes)
        obj.off_nominal_volumes = json.dumps(_off_nominal)
        obj.priority = payload.priority
        await self.db.flush()
        return True

    # ─── CompositeOperationalIntent ───────────────────────────────────────────

    async def get_composite_opint_by_declaration_id(self, declaration_id: uuid.UUID) -> CompositeBundle | None:
        result = await self.db.execute(select(CompositeOperationalIntentORM).where(CompositeOperationalIntentORM.declaration_id == declaration_id))
        composite = result.scalar_one_or_none()
        if composite is None:
            return None
        details = await self.db.get(FlightOperationalIntentDetailORM, composite.operational_intent_details_id)
        reference = await self.db.get(FlightOperationalIntentReferenceORM, composite.operational_intent_reference_id)
        return CompositeBundle(
            composite_id=composite.id,
            details=details,
            reference=reference,
            bounds=composite.bounds,
            start_datetime=composite.start_datetime,
            end_datetime=composite.end_datetime,
            alt_max=composite.alt_max,
            alt_min=composite.alt_min,
        )

    async def create_or_update_composite_opint(self, declaration_id: uuid.UUID, payload) -> bool:
        result = await self.db.execute(select(CompositeOperationalIntentORM).where(CompositeOperationalIntentORM.declaration_id == declaration_id))
        existing = result.scalar_one_or_none()
        if existing:
            existing.bounds = str(payload.bounds)
            existing.start_datetime = _coerce_datetime(payload.start_datetime)
            existing.end_datetime = _coerce_datetime(payload.end_datetime)
            existing.alt_max = float(payload.alt_max)
            existing.alt_min = float(payload.alt_min)
            if hasattr(payload, "operational_intent_reference_id") and payload.operational_intent_reference_id:
                existing.operational_intent_reference_id = uuid.UUID(str(payload.operational_intent_reference_id))
            if hasattr(payload, "operational_intent_details_id") and payload.operational_intent_details_id:
                existing.operational_intent_details_id = uuid.UUID(str(payload.operational_intent_details_id))
        else:
            ref_id = uuid.UUID(str(payload.operational_intent_reference_id)) if hasattr(payload, "operational_intent_reference_id") else uuid.uuid4()
            det_id = uuid.UUID(str(payload.operational_intent_details_id)) if hasattr(payload, "operational_intent_details_id") else uuid.uuid4()
            self.db.add(
                CompositeOperationalIntentORM(
                    declaration_id=declaration_id,
                    bounds=str(payload.bounds),
                    start_datetime=_coerce_datetime(payload.start_datetime),
                    end_datetime=_coerce_datetime(payload.end_datetime),
                    alt_max=float(payload.alt_max),
                    alt_min=float(payload.alt_min),
                    operational_intent_reference_id=ref_id,
                    operational_intent_details_id=det_id,
                )
            )
        await self.db.flush()
        return True

    # ─── Peer operational intent ──────────────────────────────────────────────

    async def create_or_update_peer_opint_detail(self, peer_id: uuid.UUID, payload) -> None:
        _payload = asdict(payload)
        existing = await self.db.get(PeerOperationalIntentDetailORM, peer_id)
        if existing:
            existing.volumes = json.dumps(_payload["volumes"])
            existing.off_nominal_volumes = json.dumps(_payload["off_nominal_volumes"])
            existing.priority = payload.priority
        else:
            self.db.add(
                PeerOperationalIntentDetailORM(
                    id=peer_id,
                    volumes=json.dumps(_payload["volumes"]),
                    off_nominal_volumes=json.dumps(_payload["off_nominal_volumes"]),
                    priority=payload.priority,
                )
            )
        await self.db.flush()

    async def create_or_update_peer_opint_reference(self, peer_id: uuid.UUID, payload) -> None:
        existing = await self.db.get(PeerOperationalIntentReferenceORM, peer_id)
        if existing:
            existing.uss_base_url = payload.uss_base_url
            existing.ovn = payload.ovn
            existing.state = payload.state
            existing.uss_availability = payload.uss_availability
            existing.version = str(payload.version)
            existing.time_start = _coerce_datetime(payload.time_start.value)
            existing.time_end = _coerce_datetime(payload.time_end.value)
            existing.subscription_id = payload.subscription_id
        else:
            self.db.add(
                PeerOperationalIntentReferenceORM(
                    id=peer_id,
                    uss_base_url=payload.uss_base_url,
                    ovn=payload.ovn,
                    state=payload.state,
                    uss_availability=payload.uss_availability,
                    version=str(payload.version),
                    manager=getattr(payload, "manager", ""),
                    time_start=_coerce_datetime(payload.time_start.value),
                    time_end=_coerce_datetime(payload.time_end.value),
                    subscription_id=payload.subscription_id,
                )
            )
        await self.db.flush()

    async def create_or_update_peer_composite_opint(self, operation_id: uuid.UUID, payload) -> bool:
        details = await self.db.get(PeerOperationalIntentDetailORM, operation_id)
        reference = await self.db.get(PeerOperationalIntentReferenceORM, operation_id)
        if details is None or reference is None:
            return False
        result = await self.db.execute(
            select(PeerCompositeOperationalIntentORM).where(PeerCompositeOperationalIntentORM.operational_intent_details_id == operation_id)
        )
        existing = result.scalar_one_or_none()
        if existing:
            existing.start_datetime = _coerce_datetime(payload.start_datetime)
            existing.end_datetime = _coerce_datetime(payload.end_datetime)
            existing.alt_max = float(payload.alt_max)
            existing.alt_min = float(payload.alt_min)
        else:
            self.db.add(
                PeerCompositeOperationalIntentORM(
                    start_datetime=_coerce_datetime(payload.start_datetime),
                    end_datetime=_coerce_datetime(payload.end_datetime),
                    alt_max=float(payload.alt_max),
                    alt_min=float(payload.alt_min),
                    bounds=str(getattr(payload, "bounds", "")),
                    operational_intent_details_id=details.id,
                    operational_intent_reference_id=reference.id,
                )
            )
        await self.db.flush()
        return True

    # ─── Serialization helper ─────────────────────────────────────────────────

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


AsyncFlightDeclarationRepository = SQLAlchemyFlightDeclarationRepository
