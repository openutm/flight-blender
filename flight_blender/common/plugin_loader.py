"""Universal plugin loader for Flight Blender.

Provides a single function to import a class from a dotted-path string,
optionally validate it against a ``@runtime_checkable`` Protocol, and cache
the result so repeated lookups are free.
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
        try:
            instance = cls.__new__(cls)
            if not isinstance(instance, expected_protocol):
                raise TypeError(
                    f"Plugin class {dotted_path!r} does not satisfy the "
                    f"{expected_protocol.__name__} protocol."
                )
        except TypeError as exc:
            if "does not satisfy" in str(exc):
                raise

            # __new__ failed — fall back to duck-typing attribute check.
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
                raise TypeError(
                    f"Plugin class {dotted_path!r} is missing required "
                    f"protocol members: {missing_str}"
                ) from exc

    return cls
