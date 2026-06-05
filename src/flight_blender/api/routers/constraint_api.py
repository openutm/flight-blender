import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.api.dependencies import require_scopes
from flight_blender.db.session import async_get_db
from flight_blender.repositories.constraint_repo import SQLAlchemyConstraintRepository
from flight_blender.services.constraint_svc import ConstraintOperations

CONSTRAINT_SCOPE = "utm.constraint_processing"

router = APIRouter(prefix="/constraint_ops")


async def _ops(db: AsyncSession = Depends(async_get_db)) -> ConstraintOperations:
    return ConstraintOperations(repo=SQLAlchemyConstraintRepository(db))


@router.get("/constraint_details")
async def list_constraint_details(
    ops: ConstraintOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([CONSTRAINT_SCOPE])),
):
    return {"constraint_details": await ops.list_constraint_details()}


@router.get("/constraint_details/{constraint_id}")
async def get_constraint_detail(
    constraint_id: uuid.UUID,
    ops: ConstraintOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([CONSTRAINT_SCOPE])),
):
    detail = await ops.get_constraint_detail(constraint_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Not found")
    return detail


@router.get("/constraint_references")
async def list_constraint_references(
    ops: ConstraintOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([CONSTRAINT_SCOPE])),
):
    return {"constraint_references": await ops.list_constraint_references()}


@router.get("/constraint_references/{constraint_reference_id}")
async def get_constraint_reference(
    constraint_reference_id: uuid.UUID,
    ops: ConstraintOperations = Depends(_ops),
    _auth: Any = Depends(require_scopes([CONSTRAINT_SCOPE])),
):
    ref = await ops.get_constraint_reference(constraint_reference_id)
    if ref is None:
        raise HTTPException(status_code=404, detail="Not found")
    return ref
