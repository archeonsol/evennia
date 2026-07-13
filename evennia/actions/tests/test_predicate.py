"""Tests for the capability-native predicate algebra."""

import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace

from evennia.actions.predicate import (ALWAYS, NEVER, And, HasCapability,
                                       Holds, Not, Or, Predicate,
                                       coerce_predicate)


class _Principal:
    def __init__(self, capabilities=(), contents=()):
        self.capabilities = set(capabilities)
        self.contents = list(contents)

    def has_capability(self, key, **kwargs):
        return key in self.capabilities


def _actor(*capabilities, contents=()):
    effective = _Principal(capabilities, contents)
    return SimpleNamespace(effective=effective, character=effective)


@dataclass(frozen=True, slots=True)
class _Spy(Predicate):
    name: str
    result: bool
    _cost: int
    calls: list = field(compare=False, hash=False, default_factory=list)

    @property
    def cost(self):
        return self._cost

    def __call__(self, action, actor):
        self.calls.append(self.name)
        return self.result

    def describe(self):
        return self.name


class PredicateTests(unittest.TestCase):
    def test_namespaced_string_compiles_to_capability(self):
        node = coerce_predicate("engine.world.build")
        self.assertEqual(node, HasCapability("engine.world.build"))

    def test_lock_expression_is_rejected(self):
        with self.assertRaises(ValueError):
            coerce_predicate("perm(Builder)")

    def test_capability_reads_structured_facade(self):
        node = HasCapability("engine.world.build")
        self.assertTrue(node(None, _actor("engine.world.build")))
        self.assertFalse(node(None, _actor()))

    def test_constants(self):
        self.assertTrue(ALWAYS(None, _actor()))
        self.assertFalse(NEVER(None, _actor()))
        self.assertIs(~ALWAYS, NEVER)

    def test_composition_and_interning(self):
        cap = HasCapability("engine.world.build")
        self.assertIsInstance(cap & Holds(), And)
        self.assertIsInstance(cap | Holds(), Or)
        self.assertIsInstance(~cap, Not)
        self.assertIs(cap & Holds(), cap & Holds())

    def test_composite_short_circuits_by_cost(self):
        calls = []
        cheap = _Spy("cheap", False, 0, calls)
        expensive = _Spy("expensive", True, 3, calls)
        self.assertFalse(And._build(expensive, cheap)(None, _actor()))
        self.assertEqual(calls, ["cheap"])

    def test_holds_remains_contextual(self):
        item = object()
        self.assertTrue(Holds()(SimpleNamespace(target=item), _actor(contents=(item,))))


if __name__ == "__main__":
    unittest.main()
