"""
FastAPI router for constraint operations.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep
from flight_blender.database import get_db
from flight_blender.models.constraint import ConstraintDetail, ConstraintReference

router = APIRouter()


@router.get("/constraint_detail/{constraint_id}", dependencies=[ReadDep])
async def get_constraint_detail(constraint_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    obj = await db.get(ConstraintDetail, constraint_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Constraint detail not found")
    return {"id": str(obj.id), "volumes": obj.volumes, "type": obj._type}


@router.get("/constraint_reference/{reference_id}", dependencies=[ReadDep])
async def get_constraint_reference(reference_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    obj = await db.get(ConstraintReference, reference_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Constraint reference not found")
    return {
        "id": str(obj.id),
        "uss_availability": obj.uss_availability,
        "ovn": obj.ovn,
        "manager": obj.manager,
        "uss_base_url": obj.uss_base_url,
        "version": obj.version,
        "is_live": obj.is_live,
    }
