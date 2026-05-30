"""
FastAPI router for flight declaration operations.
"""

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.auth import ReadDep, WriteDep
from flight_blender.database import get_db
from flight_blender.models.flight_declaration import FlightDeclaration, FlightOperationTracking
from flight_blender.schemas.flight_declaration import (
    BulkFlightDeclarationCreateResponse,
    BulkFlightDeclarationResult,
    FlightDeclarationApproval,
    FlightDeclarationCreate,
    FlightDeclarationCreateResponse,
    FlightDeclarationListResponse,
    FlightDeclarationResponse,
    FlightDeclarationStateUpdate,
    FlightDeclarationUpdate,
    SubmitToDSSResponse,
)
from flight_blender.tasks.flight_declaration import submit_flight_declaration_to_dss_async

router = APIRouter()


async def _get_declaration_or_404(declaration_id: uuid.UUID, db: AsyncSession) -> FlightDeclaration:
    obj = await db.get(FlightDeclaration, declaration_id)
    if not obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Flight declaration not found")
    return obj


# ── CRUD ────────────────────────────────────────────────────────────────────────


@router.get("/flight_declaration", response_model=FlightDeclarationListResponse, dependencies=[ReadDep])
async def list_flight_declarations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    offset = (page - 1) * page_size
    count_result = await db.execute(select(func.count()).select_from(FlightDeclaration))
    total = count_result.scalar_one()
    result = await db.execute(select(FlightDeclaration).order_by(FlightDeclaration.created_at.desc()).offset(offset).limit(page_size))
    return FlightDeclarationListResponse(count=total, results=result.scalars().all())


@router.post("/flight_declaration", response_model=FlightDeclarationCreateResponse, status_code=status.HTTP_201_CREATED, dependencies=[WriteDep])
async def create_flight_declaration(payload: FlightDeclarationCreate, db: AsyncSession = Depends(get_db)):
    decl = FlightDeclaration(**payload.model_dump())
    db.add(decl)
    await db.flush()
    await db.refresh(decl)
    return FlightDeclarationCreateResponse(id=decl.id, message="Flight declaration created", is_approved=decl.is_approved, state=decl.state)


@router.get("/flight_declaration/{declaration_id}", response_model=FlightDeclarationResponse, dependencies=[ReadDep])
async def get_flight_declaration(declaration_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    return await _get_declaration_or_404(declaration_id, db)


@router.put("/flight_declaration/{declaration_id}", response_model=FlightDeclarationResponse, dependencies=[WriteDep])
async def update_flight_declaration(
    payload: FlightDeclarationUpdate,
    declaration_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    decl = await _get_declaration_or_404(declaration_id, db)
    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(decl, field, value)
    await db.flush()
    await db.refresh(decl)
    return decl


@router.delete("/flight_declaration/{declaration_id}/delete", status_code=status.HTTP_204_NO_CONTENT, dependencies=[WriteDep])
async def delete_flight_declaration(declaration_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    decl = await _get_declaration_or_404(declaration_id, db)
    await db.delete(decl)


# ── State management ────────────────────────────────────────────────────────────


@router.get("/flight_declaration_state/{declaration_id}", dependencies=[ReadDep])
async def get_declaration_state(declaration_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    decl = await _get_declaration_or_404(declaration_id, db)
    return {"id": str(decl.id), "state": decl.state}


@router.put("/flight_declaration_state/{declaration_id}", dependencies=[WriteDep])
async def update_declaration_state(
    payload: FlightDeclarationStateUpdate,
    declaration_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    decl = await _get_declaration_or_404(declaration_id, db)
    original_state = decl.state
    decl.state = payload.state
    # Record state transition
    tracking = FlightOperationTracking(
        flight_declaration_id=decl.id,
        deltas=json.dumps({"original_state": str(original_state), "new_state": str(payload.state)}),
    )
    db.add(tracking)
    await db.flush()
    return {"id": str(decl.id), "state": decl.state, "message": "State updated"}


# ── Approval ────────────────────────────────────────────────────────────────────


@router.get("/flight_declaration_review/{declaration_id}", dependencies=[ReadDep])
async def get_declaration_review(declaration_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    decl = await _get_declaration_or_404(declaration_id, db)
    return {"id": str(decl.id), "is_approved": decl.is_approved, "approved_by": decl.approved_by}


@router.post("/flight_declaration_review/{declaration_id}", dependencies=[WriteDep])
async def set_declaration_approval(
    payload: FlightDeclarationApproval,
    declaration_id: uuid.UUID = Path(...),
    db: AsyncSession = Depends(get_db),
):
    decl = await _get_declaration_or_404(declaration_id, db)
    decl.is_approved = payload.is_approved
    decl.approved_by = payload.approved_by
    await db.flush()
    return {"id": str(decl.id), "is_approved": decl.is_approved}


# ── DSS submission ──────────────────────────────────────────────────────────────


@router.post("/flight_declaration/{declaration_id}/submit_to_dss", response_model=SubmitToDSSResponse, dependencies=[WriteDep])
async def submit_to_dss(declaration_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    await _get_declaration_or_404(declaration_id, db)
    submit_flight_declaration_to_dss_async.delay(str(declaration_id))
    return SubmitToDSSResponse(message="DSS submission queued")


# ── Bulk creation ───────────────────────────────────────────────────────────────


@router.post("/set_flight_declarations_bulk", response_model=BulkFlightDeclarationCreateResponse, dependencies=[WriteDep])
async def bulk_create_flight_declarations(payloads: list[FlightDeclarationCreate], db: AsyncSession = Depends(get_db)):
    results: list[BulkFlightDeclarationResult] = []
    submitted = 0
    failed = 0

    for payload in payloads:
        try:
            decl = FlightDeclaration(**payload.model_dump())
            db.add(decl)
            await db.flush()
            results.append(BulkFlightDeclarationResult(id=decl.id, message="Created", success=True))
            submitted += 1
        except Exception as exc:
            logger.error("Bulk create error: %s", exc)
            results.append(BulkFlightDeclarationResult(id=None, message=str(exc), success=False))
            failed += 1

    return BulkFlightDeclarationCreateResponse(submitted=submitted, failed=failed, results=results)


# ── Network declarations ────────────────────────────────────────────────────────


@router.get("/flight_declaration/{declaration_id}/network_flight_declarations", dependencies=[ReadDep])
async def get_network_flight_declarations(declaration_id: uuid.UUID = Path(...), db: AsyncSession = Depends(get_db)):
    """Return peer operational intents associated with this declaration."""
    await _get_declaration_or_404(declaration_id, db)
    return {"declaration_id": str(declaration_id), "network_declarations": []}


@router.get("/network_flight_declarations_by_view", dependencies=[ReadDep])
async def get_network_declarations_by_view(
    view: str = Query(..., description="Bounding box: 'lat_lo,lng_lo,lat_hi,lng_hi'"),
    db: AsyncSession = Depends(get_db),
):
    return {"view": view, "network_declarations": []}
