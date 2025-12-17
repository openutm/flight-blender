from os import environ as env

import redis
from dotenv import find_dotenv, load_dotenv
from loguru import logger

load_dotenv(find_dotenv())


def get_redis() -> redis.Redis:
    """
    Get a Redis instance with the configured connection parameters.

    Returns:
        redis.Redis: A Redis instance.
    """
    redis_host: str = env.get("REDIS_HOST", "redis")
    redis_port: int = int(env.get("REDIS_PORT", 6379))
    redis_password: str | None = env.get("REDIS_PASSWORD", None)

    if redis_password:
        return redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            decode_responses=True,
        )
    else:
        return redis.Redis(
            host=redis_host,
            port=redis_port,
            decode_responses=True,
        )


class RedisHelper:
    def __init__(self):
        """
        Initialize RedisHelper with Redis connection parameters.
        """
        self.redis_host: str = env.get("REDIS_HOST", "redis")
        self.redis_port: int = int(env.get("REDIS_PORT", 6379))
        self.redis_password: str | None = env.get("REDIS_PASSWORD", None)

    def _get_redis_instance(self) -> redis.Redis:
        """
        Get a Redis instance with the configured connection parameters.

        Returns:
            redis.Redis: A Redis instance.
        """
        if self.redis_password:
            return redis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                password=self.redis_password,
                decode_responses=True,
            )
        else:
            return redis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                decode_responses=True,
            )

    def flush_db(self) -> None:
        """
        Flush the entire Redis database.
        """
        r = self._get_redis_instance()
        logger.info("Flushing Redis db..")
        r.flushdb()
