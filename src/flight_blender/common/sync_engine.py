"""
Synchronous SQLAlchemy engine singleton for Celery tasks.

Celery workers run synchronous code, so they need a sync SQLAlchemy engine
rather than the async engine configured in :mod:`flight_blender.database`.
"""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@lru_cache(maxsize=1)
def get_sync_engine(database_url: str) -> Engine:
    """Return a cached synchronous SQLAlchemy engine for *database_url*.

    Translates the async database URL (``+aiosqlite`` / ``+asyncpg``) to the
    corresponding sync dialect (plain ``sqlite`` / ``+psycopg2``).
    """
    sync_url = database_url.replace("+aiosqlite", "").replace("+asyncpg", "+psycopg2")
    return create_engine(sync_url)
