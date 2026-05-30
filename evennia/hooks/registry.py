"""Hook registry: ``@hook`` decorator and lookup functions.

The registry is descriptive metadata, not a dispatcher. The engine
continues to call ``obj.at_pre_move(...)`` directly. Decorated hooks
carry a ``__evennia_hook__`` attribute (a ``HookSpec``) and are
indexed in a module-level dict keyed by qualified name.

Public surface re-exported from ``evennia.hooks``:

- ``@hook(...)`` decorator
- ``describe(method)``: return the spec for a method, or ``None``
- ``for_event(event)``: list specs filtered by event family
- ``list_all()``: every registered spec
"""

from .specs import HookSpec

_REGISTRY: dict[str, HookSpec] = {}


def _reset_registry_for_tests():
    """Clear the registry. Test-only; do not call from production code."""
    _REGISTRY.clear()


def hook(**fields):
    """Register a function as an engine hook.

    Keyword arguments are forwarded to ``HookSpec``. The decorator
    attaches the resulting spec to the function as ``__evennia_hook__``
    and records it in the module-level registry keyed by
    ``func.__qualname__``.

    Args:
        **fields: ``HookSpec`` field values. See ``HookSpec`` for the
            full list and their validation rules.

    Returns:
        Callable: The same function, with ``__evennia_hook__`` set.

    Raises:
        ValueError: If a hook with the same ``__qualname__`` is
            already registered (duplicate decoration).
    """

    def _wrap(func_or_descriptor):
        # Unwrap classmethod/staticmethod so the spec lives on the underlying
        # function. The returned object is still the original descriptor, so
        # @classmethod / @staticmethod can appear above OR below @hook.
        real_func = func_or_descriptor
        if isinstance(real_func, (classmethod, staticmethod)):
            real_func = real_func.__func__
        spec = HookSpec(**fields)
        qualname = real_func.__qualname__
        if qualname in _REGISTRY:
            raise ValueError(f"hook already registered: {qualname}")
        _REGISTRY[qualname] = spec
        real_func.__evennia_hook__ = spec
        return func_or_descriptor

    return _wrap


def describe(method):
    """Return the ``HookSpec`` for a method, or ``None`` if unregistered."""
    return getattr(method, "__evennia_hook__", None)


def for_event(event):
    """Return all registered specs whose ``event`` matches."""
    return [spec for spec in _REGISTRY.values() if spec.event == event]


def list_all():
    """Return every registered spec."""
    return list(_REGISTRY.values())


def lint():
    """Return all lint findings against the engine class set. See ``hooks.lint``."""
    from .lint import lint as _lint

    return _lint()
