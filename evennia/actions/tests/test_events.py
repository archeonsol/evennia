"""Tests for typed events + ``@subscribe`` + ``engine.emit`` (CM1 movement additions).

Mirrors the rule-registry tests: ``@subscribe`` indexes per provider class
(MRO-walked, memoized), an :class:`Event` owns its provider scope, and
``engine.emit`` collects fresh subscribers from that scope, dedupes by provider,
fires in priority order, and never short-circuits.
"""

import unittest
from dataclasses import dataclass, field
from unittest import mock

from evennia.actions.engine import RuleEngine
from evennia.actions.events import Event, subscribe, event_registry


ENGINE = RuleEngine()


# --- test events ------------------------------------------------------------
@dataclass
class Departed(Event):
    mover: object = None
    scope: tuple = field(default_factory=tuple)

    def providers(self):
        return self.scope


@dataclass
class Arrived(Departed):
    """A subclass — handlers for ``Departed`` should also fire for it (MRO)."""


# --- subscriber providers ---------------------------------------------------
class Guard:
    def __init__(self, log, name, priority_marker=None):
        self.log = log
        self.key = name

    @subscribe(Departed, priority=1)
    def note_departure(self, event):
        self.log.append(("guard", self.key, event.mover))


class Loud:
    def __init__(self, log):
        self.log = log
        self.key = "loud"

    @subscribe(Departed, priority=10)
    def first(self, event):
        self.log.append(("loud-high",))

    @subscribe(Departed, priority=-5)
    def last(self, event):
        self.log.append(("loud-low",))


class AnyEventWatcher:
    """Subscribed to the base ``Event`` → fires for every event type."""

    def __init__(self, log):
        self.log = log
        self.key = "any"

    @subscribe(Event)
    def on_any(self, event):
        self.log.append(("any", type(event).__name__))


class Boom:
    key = "boom"

    @subscribe(Departed)
    def explode(self, event):
        raise RuntimeError("handler bug")


# --- emit -------------------------------------------------------------------
class TestEmit(unittest.TestCase):
    def test_fires_subscribers_in_scope(self):
        log = []
        g = Guard(log, "g1")
        event = Departed(mover="bob", scope=(g,))
        fired = ENGINE.emit(event)
        self.assertEqual(fired, 1)
        self.assertEqual(log, [("guard", "g1", "bob")])

    def test_dedupes_provider_in_scope_twice(self):
        log = []
        g = Guard(log, "g1")
        # same object appears twice in the scope → handler fires once
        ENGINE.emit(Departed(mover="bob", scope=(g, g)))
        self.assertEqual(len(log), 1)

    def test_priority_order_across_scope(self):
        log = []
        ENGINE.emit(Departed(mover="x", scope=(Loud(log),)))
        self.assertEqual(log, [("loud-high",), ("loud-low",)])

    def test_subclass_event_fires_base_handler(self):
        log = []
        g = Guard(log, "g1")
        ENGINE.emit(Arrived(mover="bob", scope=(g,)))  # Arrived(Departed)
        self.assertEqual(log, [("guard", "g1", "bob")])

    def test_base_event_subscription_catches_all(self):
        log = []
        w = AnyEventWatcher(log)
        ENGINE.emit(Departed(scope=(w,)))
        ENGINE.emit(Arrived(scope=(w,)))
        self.assertEqual(log, [("any", "Departed"), ("any", "Arrived")])

    def test_handler_exception_does_not_stop_emit(self):
        log = []
        g = Guard(log, "g1")
        with mock.patch("evennia.utils.logger.log_trace"):
            fired = ENGINE.emit(Departed(mover="bob", scope=(Boom(), g)))
        # Boom raised but Guard still fired; count reflects only successes
        self.assertEqual(log, [("guard", "g1", "bob")])
        self.assertEqual(fired, 1)

    def test_empty_scope_fires_nothing(self):
        self.assertEqual(ENGINE.emit(Departed(scope=())), 0)


# --- registry ---------------------------------------------------------------
class TestEventRegistry(unittest.TestCase):
    def test_handlers_for_is_memoized(self):
        first = event_registry.handlers_for(Loud, Departed)
        second = event_registry.handlers_for(Loud, Departed)
        self.assertIs(first, second)
        self.assertEqual(len(first), 2)

    def test_override_without_subscribe_shadows_base(self):
        class Base:
            @subscribe(Departed)
            def react(self, event):
                ...

        class Derived(Base):
            def react(self, event):  # overrides, no @subscribe → unsubscribed
                ...

        self.assertEqual(event_registry.handlers_for(Derived, Departed), [])
        self.assertEqual(len(event_registry.handlers_for(Base, Departed)), 1)


if __name__ == "__main__":
    unittest.main()
