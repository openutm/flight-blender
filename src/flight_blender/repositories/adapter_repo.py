from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from flight_blender.models.adapter_orm import AdapterSampleORM, AdapterSessionORM
from flight_blender.models.flight_declarations_orm import FlightDeclarationORM


class AdapterRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def store(self, owner: str, session: dict, samples: list[dict]) -> None:
        if session.get("schemaVersion") == 2:
            await self.store_archive(owner, session, samples)
            return
        session_id = UUID(session["id"])
        row = await self.db.scalar(select(AdapterSessionORM).where(AdapterSessionORM.id == session_id).with_for_update())
        if row is None:
            row = AdapterSessionORM(id=session_id, owner=owner, revision=session["revision"], record=session)
            self.db.add(row)
            await self.db.flush()
        elif row.owner != owner:
            raise PermissionError("session belongs to another principal")
        else:
            if row.record.get("schemaVersion") == 2:
                raise ValueError("session protocol cannot change")
            immutable = ("id", "assignment", "startedAt", "blenderURL", "aerobridgeURL")
            if any(row.record[key] != session[key] for key in immutable):
                raise ValueError("session assignment is immutable")
            if row.record["endedAt"] and session["endedAt"] and row.record["endedAt"] != session["endedAt"]:
                raise ValueError("session end is immutable")
            if session["revision"] >= row.revision:
                if row.record["endedAt"] and not session["endedAt"]:
                    raise ValueError("closed session cannot be reopened")
                row.revision = session["revision"]
                row.record = session
        for sample in samples:
            if row.record["endedAt"] and datetime.fromisoformat(sample["vehicle"]["gpsAt"].replace("Z", "+00:00")) > datetime.fromisoformat(
                row.record["endedAt"].replace("Z", "+00:00")
            ):
                raise ValueError("sample follows the stored session end")
            existing = await self.db.get(AdapterSampleORM, (session_id, sample["sequence"]))
            if existing and existing.record != sample:
                raise ValueError("sample sequence already contains different data")
            if existing is None:
                self.db.add(AdapterSampleORM(session_id=session_id, sequence=sample["sequence"], record=sample))
        await self.db.flush()

    async def read(self, session_id: UUID, owner: str, after: int, limit: int) -> dict | None:
        row = await self.db.get(AdapterSessionORM, session_id)
        if row is None or row.owner != owner:
            return None
        samples = await self.db.scalars(
            select(AdapterSampleORM)
            .where(AdapterSampleORM.session_id == session_id, AdapterSampleORM.sequence > after)
            .order_by(AdapterSampleORM.sequence)
            .limit(limit)
        )
        return {"session": row.record, "samples": [sample.record for sample in samples]}

    async def store_archive(self, owner: str, session: dict, samples: list[dict]) -> None:
        session_id = UUID(session["id"])
        row = await self.db.scalar(select(AdapterSessionORM).where(AdapterSessionORM.id == session_id).with_for_update())
        if row is not None and row.owner != owner:
            raise PermissionError("session belongs to another principal")
        if row is None:
            declaration = await self.db.scalar(
                select(func.count()).select_from(FlightDeclarationORM).where(FlightDeclarationORM.id == UUID(session["assignment"]["declarationID"]))
            )
            if not declaration:
                raise ValueError("declaration does not exist")
            row = AdapterSessionORM(id=session_id, owner=owner, revision=session["revision"], record=session)
            self.db.add(row)
            await self.db.flush()
        else:
            if row.record.get("schemaVersion") != 2:
                raise ValueError("session protocol cannot change")
            immutable = ("id", "schemaVersion", "source", "sourceData", "assignment", "startedAt")
            if any(row.record[key] != session[key] for key in immutable):
                raise ValueError("session assignment is immutable")
            if session["revision"] == row.revision and session != row.record:
                raise ValueError("revision already contains different metadata")
            if session["revision"] > row.revision:
                if row.record["endedAt"] is not None and session != row.record | {"revision": session["revision"]}:
                    raise ValueError("closed session metadata is immutable")
                row.revision = session["revision"]
                row.record = session
        # Validate both incoming and previously stored samples when closing. Never permit a
        # truncated final count or an end time preceding telemetry already acknowledged.
        stored = row.record
        candidates = samples
        if stored["endedAt"] is not None:
            previous = await self.db.scalars(select(AdapterSampleORM.record).where(AdapterSampleORM.session_id == session_id))
            candidates = [*previous, *samples]
        for sample in candidates:
            at = datetime.fromisoformat(sample["recordedAt"].replace("Z", "+00:00"))
            if at < datetime.fromisoformat(stored["startedAt"].replace("Z", "+00:00")):
                raise ValueError("sample precedes session")
            if stored["endedAt"] is not None and (
                at > datetime.fromisoformat(stored["endedAt"].replace("Z", "+00:00")) or sample["sequence"] >= stored["finalSampleCount"]
            ):
                raise ValueError("sample exceeds closed session bounds")
        for sample in samples:
            existing = await self.db.get(AdapterSampleORM, (session_id, sample["sequence"]))
            if existing is not None and existing.record != sample:
                raise ValueError("sample sequence already contains different data")
            if existing is None:
                self.db.add(AdapterSampleORM(session_id=session_id, sequence=sample["sequence"], record=sample))
        await self.db.flush()

    async def archive_status(self, session_id: UUID) -> dict:
        row = await self.db.get(AdapterSessionORM, session_id)
        count = await self.db.scalar(select(func.count()).select_from(AdapterSampleORM).where(AdapterSampleORM.session_id == session_id))
        if row is None:
            raise ValueError("session does not exist")
        final = row.record.get("finalSampleCount")
        return {"receivedSampleCount": count, "finalSampleCount": final, "complete": final is not None and count == final}
