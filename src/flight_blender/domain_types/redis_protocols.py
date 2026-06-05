"""Redis client Protocols for core operations.

Core operations modules receive Redis client capabilities through these
``typing.Protocol`` interfaces. Concrete ``redis.Redis`` / ``redis.asyncio.Redis``
instances are injected from ``api/routers/<domain>.py`` or constructed
explicitly in Celery tasks.

Only the surface area used by core is declared here. Extend these Protocols
when a new core method needs additional Redis capabilities.
"""

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SyncRedisClient(Protocol):
    """Synchronous Redis client surface used by core operations."""

    def exists(self, *names: Any) -> Any: ...
    def get(self, name: Any) -> Any: ...
    def set(self, name: Any, value: Any) -> Any: ...
    def expire(self, name: Any, time: Any) -> Any: ...


@runtime_checkable
class AsyncRedisClient(Protocol):
    """Asynchronous Redis client surface used by core operations."""

    async def exists(self, *names: Any) -> Any: ...
    async def get(self, name: Any) -> Any: ...
    async def set(self, name: Any, value: Any) -> Any: ...
    async def expire(self, name: Any, time: Any) -> Any: ...
