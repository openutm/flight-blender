from collections.abc import AsyncGenerator, Generator, Iterator
from contextlib import asynccontextmanager, contextmanager

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import NullPool

from flight_blender.config import settings

_sync_url = settings.DATABASE_URL.replace("postgres://", "postgresql://", 1)
_async_url = (
    settings.DATABASE_URL.replace("postgres://", "postgresql+asyncpg://", 1)
    .replace("postgresql://", "postgresql+asyncpg://", 1)
    .replace("sqlite://", "sqlite+aiosqlite://", 1)
)

engine = create_engine(_sync_url)
async_engine = create_async_engine(_async_url)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
AsyncSessionLocal = async_sessionmaker(async_engine, expire_on_commit=False)

# NullPool avoids event-loop binding issues when called via asyncio.run() in Celery tasks.
_task_async_engine = create_async_engine(_async_url, poolclass=NullPool)
_TaskAsyncSessionLocal = async_sessionmaker(_task_async_engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


@contextmanager
def session_scope() -> Iterator[Session]:
    """Sync session scope for Celery tasks: commit on success, rollback on error, always close."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db() -> Generator[Session, None, None]:
    """Sync session for Celery tasks."""
    with session_scope() as db:
        yield db


async def async_get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise


@asynccontextmanager
async def async_session_scope() -> AsyncGenerator[AsyncSession, None]:
    """Async session scope for Celery tasks via asyncio.run(): commit on success, rollback on error."""
    async with _TaskAsyncSessionLocal() as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
