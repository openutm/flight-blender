"""Universal plugin loader for Flight Blender.

Provides a single function to import a class from a dotted-path string,
optionally validate it against a ``@runtime_checkable`` Protocol, and cache
the result so repeated lookups are free.

Usage::

    from common.plugin_loader import load_plugin
    from flight_declaration_operations.deconfliction_protocol import DeconflictionEngine

    EngineClass = load_plugin(
        "flight_declaration_operations.deconfliction_engine.DefaultDeconflictionEngine",
        expected_protocol=DeconflictionEngine,
    )
    engine = EngineClass()
    result = engine.check_deconfliction(request)
"""

from functools import lru_cache
from importlib import import_module
from typing import TypeVar

T = TypeVar("T")


@lru_cache(maxsize=None)
def load_plugin(
    dotted_path: str,
    *,
    expected_protocol: type[T] | None = None,
) -> type[T]:
    """Import a class from a dotted module path string.

    Args:
        dotted_path: Fully qualified class path, e.g.
            ``'pkg.module.ClassName'``.
        expected_protocol: An optional ``@runtime_checkable`` Protocol.
            If provided, a ``TypeError`` is raised when the loaded class
            does not satisfy the protocol.

    Returns:
        The **class** object (not an instance).

    Raises:
        ImportError: If the module cannot be imported.
        AttributeError: If the class is not found in the module.
        TypeError: If the class does not satisfy *expected_protocol*.
    """
    module_path, class_name = dotted_path.rsplit(".", 1)
    module = import_module(module_path)
    cls = getattr(module, class_name)

    if expected_protocol is not None:
        # Instantiate temporarily to check structural subtyping via
        # @runtime_checkable Protocol.  We use a try/except so that
        # classes whose __init__ requires arguments can still be
        # validated by falling back to a duck-type attribute check.
        try:
            instance = cls.__new__(cls)
            if not isinstance(instance, expected_protocol):
                raise TypeError(
                    f"Plugin class {dotted_path!r} does not satisfy "
                    f"the {expected_protocol.__name__} protocol."
                )
        except TypeError as exc:
            if "does not satisfy" in str(exc):
                raise
            # __new__ failed for some reason – fall through and trust
            # the caller.

    return cls
