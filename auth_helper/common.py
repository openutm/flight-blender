import logging
from os import environ as env

import redis
from dotenv import find_dotenv, load_dotenv
from walrus import Database as WalrusDatabase
from typing import Optional

load_dotenv(find_dotenv())
logger = logging.getLogger("django")

def get_redis() -> redis.Redis:
    """
    Get a Redis instance with the configured connection parameters.

    Returns:
        redis.Redis: A Redis instance.
    """
    redis_host: str = env.get("REDIS_HOST", "redis")
    redis_port: int = int(env.get("REDIS_PORT", 6379))
    redis_password: Optional[str] = env.get("REDIS_PASSWORD", None)

    if redis_password:
        return redis.Redis(
            host=redis_host,
            port=redis_port,
            password=redis_password,
            charset="utf-8",
            decode_responses=True,
        )
    else:
        return redis.Redis(
            host=redis_host,
            port=redis_port,
            charset="utf-8",
            decode_responses=True,
        )


def get_walrus_database() -> WalrusDatabase:
    """
    Get a Walrus Database instance with the configured connection parameters.

    Returns:
        Database: A Walrus Database instance.
    """
    redis_host: str = env.get("REDIS_HOST", "redis")
    redis_port: int = int(env.get("REDIS_PORT", 6379))
    redis_password: Optional[str] = env.get("REDIS_PASSWORD", None)

    if redis_password:
        walrus_db = WalrusDatabase(host=redis_host, port=redis_port, password=redis_password)
    else:
        walrus_db = WalrusDatabase(host=redis_host, port=redis_port)
    return walrus_db

class RedisHelper:
    def __init__(self):
        """
        Initialize RedisHelper with Redis connection parameters.
        """
        self.redis_host: str = env.get("REDIS_HOST", "redis")
        self.redis_port: int = int(env.get("REDIS_PORT", 6379))
        self.redis_password: Optional[str] = env.get("REDIS_PASSWORD", None)

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
                charset="utf-8",
                decode_responses=True,
            )
        else:
            return redis.Redis(
                host=self.redis_host,
                port=self.redis_port,
                charset="utf-8",
                decode_responses=True,
            )

    def flush_db(self) -> None:
        """
        Flush the entire Redis database.
        """
        r = self._get_redis_instance()
        r.flushdb()

    def delete_all_opints(self) -> None:
        """
        Delete all keys in Redis that match the pattern 'flight_opint.*'.
        """
        r = self._get_redis_instance()
        all_opints = r.keys(pattern="flight_opint.*")
        for opint in all_opints:
            r.delete(opint)
