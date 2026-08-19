"""Engine console: the staff-facing web surface for a running game server.

The console is the Django-admin successor described in the W1 arc. It reflects
the *runtime* -- typeclasses, capabilities, systems, metrics -- rather than the
Django schema, and it reaches game state only through the bounded IO-owner
services in :mod:`evennia.console.services`.

Public surface::

    from evennia.console import Panel, io_action, register

    class ThingPanel(Panel):
        key = "things"
        label = "Things"

        def rows(self, ctx):
            '''Worker side. Plain ORM, plain data out.'''

        @io_action
        def rename(self, ctx, pk, name):
            '''IO-owner side. May touch game objects.'''

Console access is a single capability, ``engine.console.access``. Panels do not
declare their own; see ``.agents/prompts/W1-console-implementation-plan.md``
(decision D1) for why a finer grid would be theatre next to a REPL.

**Names here resolve lazily.** This package is a Django app, so Django imports
it while the app registry is still loading; eagerly importing the registry (and
through it the services layer and ``dbserialize``) raises
``AppRegistryNotReady``. PEP 562 keeps ``from evennia.console import Panel``
working without paying that cost at app-load time.

"""

_LAZY_EXPORTS = {
    "IOContext": "evennia.console.registry",
    "Panel": "evennia.console.registry",
    "PanelError": "evennia.console.registry",
    "PanelRegistry": "evennia.console.registry",
    "WorkerContext": "evennia.console.registry",
    "dispatch": "evennia.console.registry",
    "io_action": "evennia.console.registry",
    "panel_registry": "evennia.console.registry",
    "register": "evennia.console.registry",
}

__all__ = tuple(sorted(_LAZY_EXPORTS))


def __getattr__(name):
    """Resolve a public console name on first access.

    Args:
        name: Attribute being looked up.

    Returns:
        The requested object.

    Raises:
        AttributeError: The name is not part of the public surface.
    """

    try:
        module_path = _LAZY_EXPORTS[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib

    return getattr(importlib.import_module(module_path), name)


def __dir__():
    """Return the public surface for tab completion."""

    return list(__all__)
