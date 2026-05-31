"""
Redis client factory (migrated from auth_helper/common.py).
"""

import redis.asyncio as aioredis
import redis as sync_redis

from flight_blender.config import get_settings

settings = get_settings()

_async_pool: aioredis.Redis | None = None
_sync_client: sync_redis.Redis | None = None


def get_async_redis() -> aioredis.Redis:
    """Return a module-level async Redis client (connection pool reuse)."""
    global _async_pool
    if _async_pool is None:
        _async_pool = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            decode_responses=True,
        )
    return _async_pool


def get_redis() -> sync_redis.Redis:
    """Return a module-level synchronous Redis client (used by Celery tasks)."""
    global _sync_client
    if _sync_client is None:
        _sync_client = sync_redis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            password=settings.redis_password,
            decode_responses=True,
        )
    return _sync_client
