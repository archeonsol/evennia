"""
Lazy accessors for the optional English-inflection libraries.

`inflect` and `pyinflect` are only consulted when prose is actually rendered,
but importing them is expensive: `inflect` drags in `typeguard` (~5s) and
`pyinflect` imports `spacy` (~3s). Importing either at module scope therefore
put both on the critical path of `django.setup()`, so every management command,
migration check and test process paid ~8s for grammar helpers it might never
call.

These accessors defer the import to the first caller and cache the result,
including the failure case, so a missing optional dependency costs one failed
import rather than one per call.

"""

from functools import cache

__all__ = ("inflect_engine", "pyinflect_module", "warm")


@cache
def inflect_engine():
    """
    Return the process-wide `inflect` engine.

    Returns:
        inflect.engine or None: The shared engine, or `None` when `inflect` is
            not installed. Callers must handle `None` and fall back to their own
            approximation.

    """
    try:
        import inflect
    except ImportError:
        return None
    return inflect.engine()


@cache
def pyinflect_module():
    """
    Return the `pyinflect` module.

    Returns:
        module or None: The imported module, or `None` when `pyinflect` is not
            installed.

    Notes:
        `pyinflect` imports `spacy`, which is the single most expensive import
        in the engine. Nothing should call this at import time.

    """
    try:
        import pyinflect
    except ImportError:
        return None
    return pyinflect


def warm():
    """
    Pay both import costs now, deliberately.

    Laziness is right for a process that may never render prose: a migration, a
    management command, a test worker. It is wrong for a live server, which
    boots once and then serves players. Without this, the first command that
    rendered a numbered name or conjugated an emote paid ~5s (and another ~3s
    for `pyinflect`) on the IO thread, stalling whoever happened to act first
    after a reload.

    Called from the server's own start hook, so the cost lands on the boot
    path, which is where it was before these accessors became lazy.

    """
    inflect_engine()
    pyinflect_module()
