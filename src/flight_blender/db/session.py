from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from flight_blender.config import settings

_async_url = (
    settings.DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
    .replace("postgresql://", "postgresql+asyncpg://", 1)
    .replace("sqlite://", "sqlite+aiosqlite://", 1)
)

async_engine = create_async_engine(_async_url)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def async_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
