from uuid import UUID

from flight_blender.repositories.adapter_repo import AdapterRepository


class AdapterOperations:
    def __init__(self, repo: AdapterRepository):
        self.repo = repo

    async def store(self, owner: str, session: dict, samples: list[dict]) -> None:
        await self.repo.store(owner, session, samples)

    async def archive_status(self, session_id: UUID) -> dict:
        return await self.repo.archive_status(session_id)

    async def read(self, session_id: UUID, owner: str, after: int, limit: int) -> dict | None:
        return await self.repo.read(session_id, owner, after, limit)
