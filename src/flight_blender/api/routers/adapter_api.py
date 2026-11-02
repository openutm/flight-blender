import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.api.dependencies import require_scopes
from flight_blender.db.session import async_get_db
from flight_blender.domain_types.common import FLIGHTBLENDER_READ_SCOPE, FLIGHTBLENDER_WRITE_SCOPE
from flight_blender.repositories.adapter_repo import AdapterRepository
from flight_blender.schemas.adapter import ArchiveBatch, ArchivePage, ArchiveReceipt, SessionBatch, SessionReceipt
from flight_blender.services.adapter_svc import AdapterOperations

router = APIRouter(prefix="/adapter_ops", tags=["Adapter compliance records"])


async def _ops(db: AsyncSession = Depends(async_get_db, scope="function")) -> AdapterOperations:
    return AdapterOperations(AdapterRepository(db))


def _owner(auth: dict) -> str:
    if not auth.get("sub"):
        raise HTTPException(401, "A subject claim is required for session records")
    return json.dumps([auth["iss"], auth["sub"]], separators=(",", ":"))


@router.put("/sessions/{session_id}", response_model=ArchiveReceipt | SessionReceipt)
async def store_session(
    session_id: UUID,
    body: ArchiveBatch | SessionBatch,
    ops: AdapterOperations = Depends(_ops),
    auth: Any = Depends(require_scopes([FLIGHTBLENDER_WRITE_SCOPE])),
):
    if session_id != body.session.id:
        raise HTTPException(422, "Path and session IDs must match")
    data = body.model_dump(mode="json")
    try:
        await ops.store(_owner(auth), data["session"], data["samples"])
    except PermissionError:
        raise HTTPException(403, "Session belongs to another principal")
    except (ValueError, IntegrityError):
        raise HTTPException(409, "Conflicting session record; retry identical batches after concurrent requests")
    record = await ops.read(session_id, _owner(auth), -1, 1)
    if record is None:
        raise RuntimeError("Stored archive is unavailable in its transaction")
    result = {"id": str(session_id), "revision": record["session"]["revision"]}
    if isinstance(body, ArchiveBatch):
        result["archive"] = await ops.archive_status(session_id)
    return result


@router.get("/sessions/{session_id}", response_model=ArchivePage | SessionBatch)
async def read_session(
    session_id: UUID,
    after: int = Query(-1, ge=-1),
    limit: int = Query(200, ge=1, le=200),
    ops: AdapterOperations = Depends(_ops),
    auth: Any = Depends(require_scopes([FLIGHTBLENDER_READ_SCOPE])),
):
    record = await ops.read(session_id, _owner(auth), after, limit)
    if record is None:
        raise HTTPException(404, "Session not found")
    if record["session"].get("schemaVersion") == 2:
        record["archive"] = await ops.archive_status(session_id)
    return record
