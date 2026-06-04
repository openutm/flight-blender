"""Repository implementations.

Import concrete repositories from their module directly so package import
does not eagerly pull Django-backed implementations into FastAPI startup.
"""

__all__: list[str] = []
