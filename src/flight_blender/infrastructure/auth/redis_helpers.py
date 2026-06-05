import redis
import redis.asyncio as aioredis
from loguru import logger

from flight_blender.config import settings


def get_redis() -> redis.Redis:
    """Get a Redis instance with the configured connection parameters."""
    if settings.REDIS_PASSWORD:
        return redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
        )
    return redis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        decode_responses=True,
    )


def get_async_redis() -> aioredis.Redis:
    """Return an async Redis client with configured connection parameters."""
    if settings.REDIS_PASSWORD:
        return aioredis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            password=settings.REDIS_PASSWORD,
            decode_responses=True,
        )
    return aioredis.Redis(
        host=settings.REDIS_HOST,
        port=settings.REDIS_PORT,
        decode_responses=True,
    )


class RedisHelper:
    def __init__(self):
        self.redis_host: str = settings.REDIS_HOST
        self.redis_port: int = settings.REDIS_PORT
        self.redis_password: str | None = settings.REDIS_PASSWORD

    def _get_redis_instance(self) -> redis.Redis:
        """Get a Redis instance with the configured connection parameters."""
        if self.redis_password:
            return redis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                password=self.redis_password,
                decode_responses=True,
            )
        return redis.Redis(
            host=self.redis_host,
            port=self.redis_port,
            decode_responses=True,
        )

    def flush_db(self) -> None:
        """Flush the entire Redis database."""
        r = self._get_redis_instance()
        logger.info("Flushing Redis db..")
        r.flushdb()
