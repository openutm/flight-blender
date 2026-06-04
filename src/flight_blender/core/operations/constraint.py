import uuid

from flight_blender.core.repositories.constraint import AsyncConstraintRepository


class ConstraintOperations:
    def __init__(self, repo: AsyncConstraintRepository):
        self.repo = repo

    async def list_constraint_details(self) -> list[dict]:
        details = await self.repo.get_constraint_details()
        return [
            {
                "id": str(d.id),
                "volumes": d.volumes,
                "type": d._type,
                "geofence_id": str(d.geofence_id) if d.geofence_id else None,
                "created_at": d.created_at.isoformat() if d.created_at else None,
            }
            for d in details
        ]

    async def get_constraint_detail(self, constraint_id: uuid.UUID) -> dict | None:
        d = await self.repo.get_constraint_detail_by_id(constraint_id)
        if d is None:
            return None
        return {
            "id": str(d.id),
            "volumes": d.volumes,
            "type": d._type,
            "geofence_id": str(d.geofence_id) if d.geofence_id else None,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }

    async def list_constraint_references(self) -> list[dict]:
        refs = await self.repo.get_constraint_references()
        return [
            {
                "id": str(r.id),
                "uss_availability": r.uss_availability,
                "ovn": r.ovn,
                "manager": r.manager,
                "uss_base_url": r.uss_base_url,
                "version": r.version,
                "is_live": r.is_live,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in refs
        ]

    async def get_constraint_reference(self, ref_id: uuid.UUID) -> dict | None:
        r = await self.repo.get_constraint_reference_by_id(ref_id)
        if r is None:
            return None
        return {
            "id": str(r.id),
            "uss_availability": r.uss_availability,
            "ovn": r.ovn,
            "manager": r.manager,
            "uss_base_url": r.uss_base_url,
            "version": r.version,
            "is_live": r.is_live,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
