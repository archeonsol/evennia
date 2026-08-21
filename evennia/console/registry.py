"""Panel registration and the worker/IO-owner call boundary.

The registry is deliberately shaped like ``SYSTEM_MODULES``: games list modules
in ``settings.CONSOLE_PANEL_MODULES``, each module exposes
``register_panels()``, and a module that fails to import or registers nothing is
a loud startup error.

The load-bearing part is not registration but the **two-decorator split**. A
panel method is either:

* **worker-side** (the default) -- receives a :class:`WorkerContext` carrying
  scalars only, may use plain ORM reads, and physically cannot reach a live
  typeclass instance because it is handed no object to reach one through; or
* **IO-owner-side** (marked :func:`io_action`) -- dispatched through
  ``run_on_io_thread``, may touch game objects, handlers, and attributes.

Both sides' return values are validated by ``freeze_plain`` and handed back as
plain data by ``thaw_plain``, so a panel cannot leak a model, queryset,
handler, or Attribute proxy to a frontend even by accident. That is the JSONB
IO-thread trap made unrepresentable rather than merely discouraged.

See ``docs/source/Components/Web-IO-Boundary.md`` for the underlying contract.

"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping

from django.conf import settings

from evennia.console.services import freeze_plain, thaw_plain
from evennia.web.utils.io import release_worker_db_connections, run_on_io_thread

#: Attribute stamped on a method by :func:`io_action`.
IO_ACTION_FLAG = "__console_io_action__"

#: Capability that admits a caller to the console at all.
CONSOLE_ACCESS = "engine.console.access"

#: Capability that admits a caller to the moderation panel and nothing else.
CONSOLE_MODERATION = "engine.console.moderation"

#: Capability to sanction a single address or a device token.
#:
#: Decision D1 kept the console to two capabilities, on the reasoning that
#: everybody admitted can already do everything. That reasoning covers reading.
#: It does not cover enforcement, and the console shipped without the gate the
#: game-side form had.
#:
#: The line is drawn where the game drew it, not wider. Banning a **network**
#: is ordinary staff work: the network is the ban unit, because a residential
#: address changes and a /24 does not. Banning a **single address** or a
#: **device token** means acting on a value that is masked until somebody
#: reveals it, and that is the narrower act this gates.
CONSOLE_MODERATION_ADDRESS = "engine.console.moderation.address"

#: Capability to issue a sanction that never expires.
#:
#: Without it an operator may still ask for one. The request becomes a proposal
#: that a holder approves or declines, so the work of investigating a case and
#: the authority to make it permanent can sit with different people.
CONSOLE_MODERATION_PERMANENT = "engine.console.moderation.permanent"

#: Sanction subjects whose value is masked until somebody reveals it. Acting
#: on one of these is what ``CONSOLE_MODERATION_ADDRESS`` gates.
#:
#: ``cidr`` and ``asn`` are deliberately absent. Staff without the capability
#: still see which network a session came from and may still ban it, which is
#: what most moderation work needs.
ADDRESS_SUBJECTS = frozenset({"ip", "device_token"})


class PanelError(Exception):
    """A panel was declared, registered, or dispatched incorrectly."""


def io_action(func: Callable) -> Callable:
    """Mark a panel method as running on the IO owner.

    A marked method is dispatched through ``run_on_io_thread`` and may touch
    game objects, handlers, and attributes. An unmarked method runs on the web
    worker and must not.

    Args:
        func: The panel method to mark.

    Returns:
        Callable: The same function, stamped for the dispatcher.
    """

    setattr(func, IO_ACTION_FLAG, True)
    return func


def is_io_action(func: Any) -> bool:
    """Return whether a callable was marked with :func:`io_action`."""

    return bool(getattr(func, IO_ACTION_FLAG, False))


@dataclass(frozen=True, slots=True)
class WorkerContext:
    """Scalars a worker-side panel method is allowed to see.

    Deliberately carries no request, no user model, no session, and no game
    object. A panel method holding one of these cannot reach live game state,
    which is the point: the boundary is enforced by what the method is given,
    not by what its author remembers.

    Attributes:
        actor_id: Primary key of the acting account.
        actor_name: Display name for audit text only.
        capabilities: Capabilities the actor holds, resolved at request time.
        params: Frozen request parameters.
    """

    actor_id: int
    actor_name: str = ""
    capabilities: frozenset[str] = field(default_factory=frozenset)
    params: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def has(self, capability: str) -> bool:
        """Return whether the actor holds one capability."""

        return capability in self.capabilities

    @property
    def moderation_only(self) -> bool:
        """Return whether this actor reaches the moderation panel only."""

        return CONSOLE_ACCESS not in self.capabilities


@dataclass(frozen=True, slots=True)
class IOContext:
    """Scalars an IO-owner panel method is allowed to see.

    Same shape as :class:`WorkerContext`, distinguished by type so a method
    signature documents which side it runs on and so tests can assert the
    dispatcher routed correctly.

    Attributes:
        actor_id: Primary key of the acting account.
        actor_name: Display name for audit text only.
        capabilities: Capabilities the actor holds, resolved at request time.
        params: Frozen request parameters.
    """

    actor_id: int
    actor_name: str = ""
    capabilities: frozenset[str] = field(default_factory=frozenset)
    params: Mapping[str, Any] = field(default_factory=lambda: MappingProxyType({}))

    def has(self, capability: str) -> bool:
        """Return whether the actor holds one capability."""

        return capability in self.capabilities


class Panel:
    """One console panel.

    Subclasses set :attr:`key` and :attr:`label` and implement whichever of
    :meth:`rows` and :meth:`detail` they support. Methods that mutate game
    state are marked with :func:`io_action`.

    Panels do not declare capabilities. Reaching any panel already requires
    ``engine.console.access``; the sole exception is
    :attr:`moderation_only`, which additionally admits holders of
    ``engine.console.moderation``.

    Attributes:
        key: Stable identifier used in URLs and the registry.
        label: Human-readable nav label, plain technical English.
        description: One-line summary for the nav.
        columns: Column keys the default table renders, in order.
        moderation_only: Whether ``engine.console.moderation`` also admits.
        needs_io: Whether the panel is unusable without the IO owner. Panels
            that read only plain models leave this ``False`` so they keep
            working in degraded mode when the game server is down.
    """

    key: str = ""
    label: str = ""
    description: str = ""
    columns: tuple[str, ...] = ()
    moderation_only: bool = False
    needs_io: bool = False

    def rows(self, ctx: WorkerContext):
        """Return the panel's list view as plain data.

        Args:
            ctx: Worker-side context.

        Raises:
            NotImplementedError: The panel has no list view.
        """

        raise NotImplementedError(f"panel {self.key!r} has no list view")

    def detail(self, ctx: WorkerContext, pk):
        """Return one record as plain data.

        Args:
            ctx: Worker-side context.
            pk: Identifier of the record to return.

        Raises:
            NotImplementedError: The panel has no detail view.
        """

        raise NotImplementedError(f"panel {self.key!r} has no detail view")

    def describe(self) -> dict:
        """Return the panel's nav entry as plain data."""

        return {
            "key": self.key,
            "label": self.label,
            "description": self.description,
            "columns": list(self.columns),
            "moderation_only": bool(self.moderation_only),
            "needs_io": bool(self.needs_io),
        }

    def admits(self, ctx) -> bool:
        """Return whether one context may reach this panel.

        Full console access reaches everything. The moderation capability
        reaches moderation panels only.

        Args:
            ctx: Worker or IO context to test.
        """

        if ctx.has(CONSOLE_ACCESS):
            return True
        return bool(self.moderation_only) and ctx.has(CONSOLE_MODERATION)


class PanelRegistry:
    """Registry of console panels, keyed by :attr:`Panel.key`."""

    def __init__(self):
        """Initialize an empty registry."""

        self._panels: dict[str, Panel] = {}
        self._loaded_modules = False

    def register(self, panel) -> Panel:
        """Register one panel class or instance.

        Args:
            panel: A :class:`Panel` subclass or instance.

        Returns:
            Panel: The registered instance.

        Raises:
            PanelError: The panel is malformed or its key is taken.
        """

        instance = panel() if isinstance(panel, type) else panel
        if not isinstance(instance, Panel):
            raise PanelError(f"{panel!r} is not a Panel")
        key = str(instance.key or "").strip().lower()
        if not key or len(key) > 64:
            raise PanelError("panel key must contain 1-64 characters")
        if not instance.label:
            raise PanelError(f"panel {key!r} has no label")
        existing = self._panels.get(key)
        if existing is not None and type(existing) is not type(instance):
            raise PanelError(f"conflicting panel registration for {key!r}")
        object.__setattr__(instance, "key", key)
        self._panels[key] = instance
        return instance

    def get(self, key: str) -> Panel:
        """Return one registered panel or fail closed.

        Args:
            key: Panel key.

        Raises:
            PanelError: No panel is registered under that key.
        """

        try:
            return self._panels[str(key).strip().lower()]
        except KeyError as err:
            raise PanelError(f"unknown console panel {key!r}") from err

    def panels(self) -> tuple[Panel, ...]:
        """Return every registered panel in deterministic key order."""

        return tuple(self._panels[key] for key in sorted(self._panels))

    def visible(self, ctx) -> tuple[Panel, ...]:
        """Return the panels one context may reach, in key order."""

        return tuple(panel for panel in self.panels() if panel.admits(ctx))

    def load_modules(self) -> None:
        """Import ``settings.CONSOLE_PANEL_MODULES`` and register their panels.

        Each module must expose ``register_panels(registry)``. Import and
        registration errors deliberately abort startup rather than silently
        producing a console missing a panel.

        Raises:
            PanelError: A configured module has no ``register_panels()``.
        """

        if self._loaded_modules:
            return
        for path in getattr(settings, "CONSOLE_PANEL_MODULES", ()) or ():
            module = importlib.import_module(path)
            register_panels = getattr(module, "register_panels", None)
            if register_panels is None:
                raise PanelError(f"console panel module {path!r} has no register_panels()")
            register_panels(self)
        self._loaded_modules = True

    def _reset_for_tests(self) -> None:
        """Drop every registration. Test-support only."""

        self._panels.clear()
        self._loaded_modules = False


#: Process-wide registry. Panels register onto this.
panel_registry = PanelRegistry()


def register(panel):
    """Register one panel onto the process-wide registry.

    Usable as a decorator on a :class:`Panel` subclass.

    Args:
        panel: A :class:`Panel` subclass or instance.

    Returns:
        The argument, so decorator use keeps the class bound to its name.
    """

    panel_registry.register(panel)
    return panel


def _bounded(value):
    """Validate a panel result at the boundary and return it as plain data.

    Freezing rejects anything that must not cross -- models, querysets,
    handlers, lazy wrappers, cycles, oversized payloads -- and detaches what
    remains. Thawing converts the codec's frozen containers back to ordinary
    dicts and lists for the caller.

    Args:
        value: Whatever the panel method returned.

    Returns:
        The same data as plain, detached, JSON-shaped values.
    """

    return thaw_plain(freeze_plain(value))


def dispatch(panel, method_name: str, ctx: WorkerContext, *args, **kwargs):
    """Call one panel method on the correct thread and freeze its result.

    Worker-side methods run inline on the calling thread. Methods marked with
    :func:`io_action` are handed to the IO owner, with worker database
    connections released first per the mutation-bridge contract.

    Every return value passes through ``freeze_plain`` and back out through
    ``thaw_plain``. The freeze is the check: a panel that accidentally returns
    a model, queryset, handler, or Attribute proxy fails *here*, rather than at
    the serializer, in a template, or in production on a different database
    backend. The thaw is what the caller actually wants -- ordinary dicts and
    lists a serializer can render -- so panels never see the codec's internal
    container type.

    Args:
        panel: The panel instance.
        method_name: Name of the method to call.
        ctx: Worker-side context for the request.
        *args: Positional arguments for the method.
        **kwargs: Keyword arguments for the method.

    Returns:
        The method's frozen return value.

    Raises:
        PanelError: The method is missing, private, or not callable.
    """

    if not isinstance(ctx, WorkerContext):
        raise PanelError("dispatch requires a WorkerContext")
    if not panel.admits(ctx):
        raise PanelError(f"panel {panel.key!r} is not available to this caller")
    name = str(method_name)
    if name.startswith("_"):
        raise PanelError("console methods may not be private")
    method = getattr(panel, name, None)
    if method is None or not callable(method):
        raise PanelError(f"panel {panel.key!r} has no method {name!r}")
    if not is_io_action(method):
        return _bounded(method(ctx, *args, **kwargs))

    io_ctx = IOContext(
        actor_id=ctx.actor_id,
        actor_name=ctx.actor_name,
        capabilities=ctx.capabilities,
        params=ctx.params,
    )
    release_worker_db_connections()
    return _bounded(run_on_io_thread(method, io_ctx, *args, **kwargs))
