"""Universal plugin loader for Flight Blender.

Provides a single function to import a class from a dotted-path string,
optionally validate it against a ``@runtime_checkable`` Protocol, and cache
the result so repeated lookups are free.

Usage::

    from flight_blender.plugins.loader import load_plugin

    EngineClass = load_plugin(
        "flight_blender.services.deconfliction_engine.DefaultDeconflictionEngine",
        expected_protocol=DeconflictionEngine,
    )
    engine = EngineClass()
    result = engine.check_deconfliction(request, db)
"""

from functools import lru_cache
from importlib import import_module
from typing import TypeVar, overload

T = TypeVar("T")


@overload
def load_plugin(
    dotted_path: str,
    *,
    expected_protocol: type[T],
) -> type[T]: ...


@overload
def load_plugin(
    dotted_path: str,
    *,
    expected_protocol: None = ...,
) -> type: ...


@lru_cache(maxsize=None)
def load_plugin(
    dotted_path: str,
    *,
    expected_protocol: T | None = None,
) -> T | type:
    """Import a class from a dotted module path string.

    Args:
        dotted_path: Fully qualified class path, e.g.
            ``'pkg.module.ClassName'``.
        expected_protocol: An optional ``@runtime_checkable`` Protocol.
            When provided, a ``TypeError`` is raised if the loaded class
            does not satisfy the protocol.

    Returns:
        The **class** object itself — not an instance of it.
        When *expected_protocol* is provided the return type is
        ``type[T]`` (i.e. a class whose instances satisfy *T*).

    Raises:
        ImportError: If the module cannot be imported.
        AttributeError: If the class is not found in the module.
        TypeError: If the class does not satisfy *expected_protocol*.
    """
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = import_module(module_path)
    cls = getattr(module, class_name)

    if expected_protocol is not None:
        instance = cls.__new__(cls)
        if not isinstance(instance, expected_protocol):  # type: ignore[arg-type]
            raise TypeError(f"Plugin class {dotted_path!r} does not satisfy the {expected_protocol.__name__} protocol.")  # type: ignore[arg-type]

    return cls
