import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.models.constraint_orm import CompositeConstraintORM, ConstraintDetailORM, ConstraintReferenceORM
from flight_blender.models.geo_fence_orm import GeoFenceORM


def _coerce_datetime(value):
    if isinstance(value, datetime):
        return value
    if hasattr(value, "datetime"):
        return value.datetime
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


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

    # ─── Phase 1B additions ───────────────────────────────────────────────────

    async def get_constraint_by_geofence_id(self, geofence_id: uuid.UUID) -> Optional[ConstraintDetailORM]:
        result = await self.db.execute(select(ConstraintDetailORM).where(ConstraintDetailORM.geofence_id == geofence_id))
        return result.scalar_one_or_none()

    async def get_geofence_by_constraint_reference_id(self, ref_id: uuid.UUID) -> Optional[GeoFenceORM]:
        ref = await self.db.get(ConstraintReferenceORM, ref_id)
        if ref is None or ref.geofence_id is None:
            return None
        return await self.db.get(GeoFenceORM, ref.geofence_id)

    async def write_constraint_details(self, constraint_id: uuid.UUID, details_json: str) -> None:
        existing = await self.db.get(ConstraintDetailORM, constraint_id)
        if existing:
            existing.volumes = details_json
        else:
            self.db.add(ConstraintDetailORM(id=constraint_id, volumes=details_json))
        await self.db.flush()

    async def write_constraint_reference_details(self, details_json: str) -> None:
        self.db.add(ConstraintReferenceORM(volumes=details_json if hasattr(ConstraintReferenceORM, "volumes") else None))
        await self.db.flush()

    async def create_or_update_constraint_detail(self, constraint, geofence_id: uuid.UUID) -> ConstraintDetailORM:
        import json  # noqa: PLC0415
        from dataclasses import asdict  # noqa: PLC0415

        details_json = json.dumps(asdict(constraint)) if hasattr(constraint, "__dataclass_fields__") else json.dumps(constraint)
        result = await self.db.execute(select(ConstraintDetailORM).where(ConstraintDetailORM.geofence_id == geofence_id))
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.volumes = details_json
            obj = existing
        else:
            obj = ConstraintDetailORM(geofence_id=geofence_id, volumes=details_json)
            self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def create_or_update_constraint_reference(
        self, constraint_reference, geofence_id: uuid.UUID, declaration_id: uuid.UUID
    ) -> ConstraintReferenceORM:
        ref_id = constraint_reference.id if hasattr(constraint_reference, "id") else uuid.UUID(str(constraint_reference))
        existing = await self.db.get(ConstraintReferenceORM, ref_id)
        if existing is not None:
            existing.ovn = constraint_reference.ovn
            existing.geofence_id = geofence_id
            existing.flight_declaration_id = declaration_id
            existing.uss_availability = getattr(constraint_reference, "uss_availability", existing.uss_availability)
            existing.uss_base_url = getattr(constraint_reference, "uss_base_url", existing.uss_base_url)
            existing.version = str(getattr(constraint_reference, "version", existing.version))
            existing.manager = getattr(constraint_reference, "manager", existing.manager)
            if hasattr(constraint_reference, "time_start"):
                existing.time_start = _coerce_datetime(
                    constraint_reference.time_start.value if hasattr(constraint_reference.time_start, "value") else constraint_reference.time_start
                )
            if hasattr(constraint_reference, "time_end"):
                existing.time_end = _coerce_datetime(
                    constraint_reference.time_end.value if hasattr(constraint_reference.time_end, "value") else constraint_reference.time_end
                )
            obj = existing
        else:
            obj = ConstraintReferenceORM(
                id=ref_id,
                geofence_id=geofence_id,
                flight_declaration_id=declaration_id,
                ovn=constraint_reference.ovn,
                uss_availability=getattr(constraint_reference, "uss_availability", ""),
                uss_base_url=getattr(constraint_reference, "uss_base_url", ""),
                version=str(getattr(constraint_reference, "version", "")),
                manager=getattr(constraint_reference, "manager", None),
                time_start=_coerce_datetime(
                    constraint_reference.time_start.value if hasattr(constraint_reference.time_start, "value") else constraint_reference.time_start
                ),
                time_end=_coerce_datetime(
                    constraint_reference.time_end.value if hasattr(constraint_reference.time_end, "value") else constraint_reference.time_end
                ),
            )
            self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def create_or_update_geofence(self, geofence_payload) -> GeoFenceORM:
        payload_id = getattr(geofence_payload, "id", None)
        existing = await self.db.get(GeoFenceORM, uuid.UUID(str(payload_id))) if payload_id is not None else None
        if existing is not None:
            existing.raw_geo_fence = getattr(geofence_payload, "raw_geo_fence", existing.raw_geo_fence)
            existing.geozone = getattr(geofence_payload, "geozone", existing.geozone)
            existing.upper_limit = getattr(geofence_payload, "upper_limit", existing.upper_limit)
            existing.lower_limit = getattr(geofence_payload, "lower_limit", existing.lower_limit)
            existing.altitude_ref = getattr(geofence_payload, "altitude_ref", existing.altitude_ref)
            existing.name = getattr(geofence_payload, "name", existing.name)
            existing.bounds = getattr(geofence_payload, "bounds", existing.bounds)
            existing.status = getattr(geofence_payload, "status", existing.status)
            existing.message = getattr(geofence_payload, "message", existing.message)
            existing.is_test_dataset = getattr(geofence_payload, "is_test_dataset", existing.is_test_dataset)
            if hasattr(geofence_payload, "start_datetime"):
                existing.start_datetime = _coerce_datetime(geofence_payload.start_datetime)
            if hasattr(geofence_payload, "end_datetime"):
                existing.end_datetime = _coerce_datetime(geofence_payload.end_datetime)
            obj = existing
        else:
            obj = GeoFenceORM(
                id=uuid.UUID(str(payload_id)) if payload_id is not None else uuid.uuid4(),
                raw_geo_fence=getattr(geofence_payload, "raw_geo_fence", None),
                geozone=getattr(geofence_payload, "geozone", None),
                upper_limit=getattr(geofence_payload, "upper_limit", 0),
                lower_limit=getattr(geofence_payload, "lower_limit", 0),
                altitude_ref=getattr(geofence_payload, "altitude_ref", 0),
                name=getattr(geofence_payload, "name", "constraint"),
                bounds=getattr(geofence_payload, "bounds", ""),
                status=getattr(geofence_payload, "status", 1),
                message=getattr(geofence_payload, "message", ""),
                is_test_dataset=getattr(geofence_payload, "is_test_dataset", False),
                start_datetime=_coerce_datetime(geofence_payload.start_datetime),
                end_datetime=_coerce_datetime(geofence_payload.end_datetime),
            )
            self.db.add(obj)
        await self.db.flush()
        await self.db.refresh(obj)
        return obj

    async def create_or_update_composite_constraint(self, payload) -> bool:
        ref_id = uuid.UUID(str(payload.constraint_reference_id))
        det_id = uuid.UUID(str(payload.constraint_detail_id))
        fd_id = uuid.UUID(str(payload.flight_declaration_id))
        result = await self.db.execute(select(CompositeConstraintORM).where(CompositeConstraintORM.constraint_reference_id == ref_id))
        existing = result.scalar_one_or_none()
        if existing is not None:
            existing.declaration_id = fd_id
            existing.bounds = str(payload.bounds)
            existing.start_datetime = _coerce_datetime(payload.start_datetime)
            existing.end_datetime = _coerce_datetime(payload.end_datetime)
            existing.alt_max = float(payload.alt_max)
            existing.alt_min = float(payload.alt_min)
            existing.constraint_detail_id = det_id
        else:
            self.db.add(
                CompositeConstraintORM(
                    declaration_id=fd_id,
                    bounds=str(payload.bounds),
                    start_datetime=_coerce_datetime(payload.start_datetime),
                    end_datetime=_coerce_datetime(payload.end_datetime),
                    alt_max=float(payload.alt_max),
                    alt_min=float(payload.alt_min),
                    constraint_reference_id=ref_id,
                    constraint_detail_id=det_id,
                )
            )
        await self.db.flush()
        return True


AsyncConstraintRepository = SQLAlchemyConstraintRepository
