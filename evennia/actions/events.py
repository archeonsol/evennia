"""
Typed events + ``@subscribe`` (CM1): a reactive world with no subscription leak.

The ``before``/``report`` phases let a *single* dispatch's providers react to it.
Events are the cross-object, after-the-fact dual: a ``Move`` that has happened
emits a :class:`Departed` from the old room and an :class:`Arrived` in the new
one, and *any* object that cares — a guard that tracks who leaves, a follower who
tags along, a trap that arms — reacts via an ``@subscribe`` handler. This
replaces the imperative ``handle_escort_move`` / ``notify_departure`` /
``after_escort_departure`` wiring threaded through the old ``do_traverse``.

Design mirrors ``@rule``/``RuleRegistry`` exactly, deliberately:

* ``@subscribe(EventType)`` attaches an :class:`EventSpec` to a method, stacking
  like ``@rule`` (one method may subscribe to several event types).
* :class:`EventRegistry` walks a provider class's MRO, indexes specs by event
  type, and memoizes the per-class index — the same tiny per-class dict, not a
  cross-object cache.
* There is **no persistent pub/sub registry.** Subscribers are collected fresh
  from the event's provider scope on every :meth:`~evennia.actions.engine.RuleEngine.emit`
  — so a subscription can never go stale or leak a destroyed object, the same
  anti-cached-state philosophy as rule collection.

An :class:`Event` owns its own *scope*: :meth:`Event.providers` returns the
objects whose handlers should fire (a movement event returns the source room +
its contents, the destination room + its contents, and the mover — the
destination is not in the original dispatch context, which is exactly why scope
is computed from the event, not the dispatch). The engine just collects and
fires; it never hard-codes "rooms".
"""

from dataclasses import dataclass
from typing import Callable, Optional

__all__ = ["Event", "subscribe", "EventSpec", "EventRegistry", "event_registry"]


class Event:
    """Base for a typed event. Subclass (usually as a ``@dataclass``) to carry
    payload; override :meth:`providers` to declare which objects' ``@subscribe``
    handlers should fire."""

    def providers(self):
        """Return the objects whose ``@subscribe(type(self))`` handlers fire.

        Order is significant only as a stable tie-breaker among equal-priority
        handlers (the engine sorts by priority desc). ``None`` entries and
        duplicates are dropped by the emitter. The base returns nothing.
        """
        return ()


@dataclass(slots=True)
class EventSpec:
    """One registered subscription: a method bound to a single event type."""

    event_type: type
    func: Callable
    handler_name: str
    priority: int = 0


def subscribe(event_type, *, priority: int = 0):
    """Decorate a provider method as an event handler.

    Args:
        event_type (type | tuple[type, ...]): the :class:`Event` subclass(es) this
            handler reacts to. A handler registered for a base event type also
            fires for subclasses (the lookup walks the event's MRO).
        priority (int): higher fires first across the emit scope.

    The handler is called as ``handler(event)`` during :meth:`RuleEngine.emit`.
    Handlers are report-like: side effects allowed, no short-circuit, return
    value ignored. A handler may start its own :class:`~evennia.actions.process.Activity`.
    """
    event_types = event_type if isinstance(event_type, tuple) else (event_type,)

    def deco(fn):
        specs = getattr(fn, "__evennia_event_specs__", None)
        if specs is None:
            specs = []
            fn.__evennia_event_specs__ = specs
        for et in event_types:
            specs.append(
                EventSpec(
                    event_type=et,
                    func=fn,
                    handler_name=fn.__name__,
                    priority=priority,
                )
            )
        return fn

    return deco


class EventRegistry:
    """Collects and indexes ``@subscribe`` specs per provider class (memoized)."""

    def collect(self, cls):
        """Walk ``cls``'s MRO, gather subscription specs, cache the index on the
        class. Most-derived definition of a method wins (an override without
        ``@subscribe`` shadows the base method's subscriptions). Returns the
        ``event_type -> [EventSpec]`` index (also stored as ``cls.__evennia_events__``)."""
        index = {}
        seen_methods = set()
        for klass in cls.__mro__:
            for name, attr in vars(klass).items():
                if name in seen_methods:
                    continue
                seen_methods.add(name)
                specs = getattr(attr, "__evennia_event_specs__", None)
                if not specs:
                    continue
                for spec in specs:
                    index.setdefault(spec.event_type, []).append(spec)
        for bucket in index.values():
            bucket.sort(key=lambda s: -s.priority)
        cls.__evennia_events__ = index
        cls.__evennia_events_cache__ = {}
        return index

    def _ensure_collected(self, cls):
        if "__evennia_events__" not in cls.__dict__:
            self.collect(cls)

    def handlers_for(self, cls, event_type):
        """Return ``cls``'s handler specs for ``event_type`` — including handlers
        registered against any of ``event_type``'s base :class:`Event` types
        (MRO-walked) — priority-sorted. Memoized per class + event type."""
        self._ensure_collected(cls)
        cache = cls.__evennia_events_cache__
        hit = cache.get(event_type)
        if hit is not None:
            return hit
        index = cls.__evennia_events__
        out = []
        for et in event_type.__mro__:
            out.extend(index.get(et, ()))
        if len(out) > 1:
            out.sort(key=lambda s: -s.priority)
        cache[event_type] = out
        return out


#: import-time singleton
event_registry = EventRegistry()
