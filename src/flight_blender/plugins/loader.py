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
                raise TypeError(f"Plugin class {dotted_path!r} does not satisfy the {expected_protocol.__name__} protocol.")
        except TypeError as exc:
            if "does not satisfy" in str(exc):
                # This is an explicit protocol validation failure; re-raise.
                raise

            # __new__ failed for some other reason (e.g., custom metaclass or
            # unusual __new__ signature). Fall back to a simple duck-typing
            # check based on the public members of the expected protocol.
            missing_members: list[str] = []
            for name in dir(expected_protocol):
                if name.startswith("_"):
                    continue
                proto_attr = getattr(expected_protocol, name, None)
                if callable(proto_attr) or isinstance(proto_attr, property):
                    if not hasattr(cls, name):
                        missing_members.append(name)

            if missing_members:
                missing_str = ", ".join(sorted(missing_members))
                raise TypeError(f"Plugin class {dotted_path!r} is missing required protocol members: {missing_str}") from exc

    return cls
