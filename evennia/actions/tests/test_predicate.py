"""Tests for the Predicate algebra + lock-string transpiler (CM1 Phase 0f/0f-bis)."""

import unittest
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest import mock

from evennia.actions.permission import Capability, DefaultCapability
from evennia.actions import predicate as P
from evennia.actions.predicate import (
    Predicate,
    HasCapability,
    Holds,
    HasTag,
    HasAttr,
    IsSelf,
    IsObject,
    And,
    Or,
    Not,
    Builder,
    Admin,
    ALWAYS,
    NEVER,
    coerce_predicate,
    from_lockstring,
    LegacyLock,
)


class _Perms:
    """Mimics obj.permissions.all() for capability resolution."""

    def __init__(self, perms):
        self._perms = list(perms)

    def all(self):
        return list(self._perms)


def _actor(perms=(), character=None, effective=None, location=None, account=None):
    """Build a stand-in actor.

    Capabilities resolve *fresh* off the effective object's permission strings
    (there is no precomputed mask), so ``perms`` are permission-name strings.
    """
    eff = effective if effective is not None else character
    if eff is None:
        eff = SimpleNamespace()
    try:
        eff.permissions = _Perms(perms)
        eff.account = account
    except (AttributeError, TypeError):
        pass  # bare object() targets don't need capability resolution
    return SimpleNamespace(character=character, effective=eff, location=location)


# A leaf with a per-instance cost, for ordering/short-circuit tests.
@dataclass(frozen=True, slots=True)
class _Spy(Predicate):
    name: str
    ret: bool
    _cost: int
    log: list = field(compare=False, hash=False, default_factory=list)

    @property
    def cost(self):
        return self._cost

    def __call__(self, action, actor):
        self.log.append(self.name)
        return self.ret

    def describe(self):
        return self.name


class TestComposition(unittest.TestCase):
    def test_and_or_not_types(self):
        self.assertIsInstance(Builder & Holds(), And)
        self.assertIsInstance(Builder | Admin, Or)
        self.assertIsInstance(~Builder, Not)

    def test_double_negation_simplifies(self):
        self.assertIs(~~Builder, Builder)

    def test_invert_const(self):
        self.assertIs(~ALWAYS, NEVER)
        self.assertIs(~NEVER, ALWAYS)

    def test_flatten_nested_and(self):
        node = (Builder & Holds()) & HasTag("vip")
        self.assertIsInstance(node, And)
        # flattened: not an And-of-And
        self.assertEqual(len(node.parts), 3)
        self.assertFalse(any(isinstance(p, And) for p in node.parts))

    def test_cost_ordering(self):
        node = HasTag("vip") & Holds() & Builder  # costs 3, 2, 1
        costs = [p.cost for p in node.parts]
        self.assertEqual(costs, sorted(costs))
        self.assertIs(node.parts[0], Builder)  # cheapest first


class TestInterning(unittest.TestCase):
    def test_composite_interns(self):
        self.assertIs(Builder | Admin, Builder | Admin)
        self.assertIs(Builder & Holds(), Builder & Holds())

    def test_capability_coercion_interns_to_singleton(self):
        self.assertIs(coerce_predicate(DefaultCapability.BUILDER), Builder)


class TestEvaluation(unittest.TestCase):
    def test_has_capability(self):
        actor = _actor(perms=["Builder"])  # implies Player, not Admin
        self.assertTrue(Builder(None, actor))
        self.assertFalse(Admin(None, actor))

    def test_const(self):
        self.assertTrue(ALWAYS(None, _actor()))
        self.assertFalse(NEVER(None, _actor()))

    def test_and_short_circuits_cheap_first(self):
        log = []
        cheap_fail = _Spy("cheap", False, 0, log)
        expensive = _Spy("expensive", True, 3, log)
        node = And._build(expensive, cheap_fail)
        self.assertFalse(node(None, _actor()))
        self.assertEqual(log, ["cheap"])  # expensive never evaluated

    def test_or_short_circuits_cheap_first(self):
        log = []
        cheap_pass = _Spy("cheap", True, 0, log)
        expensive = _Spy("expensive", False, 3, log)
        node = Or._build(expensive, cheap_pass)
        self.assertTrue(node(None, _actor()))
        self.assertEqual(log, ["cheap"])

    def test_holds(self):
        target = object()
        char = SimpleNamespace(contents=[target])
        actor = _actor(character=char)
        action = SimpleNamespace(target=target)
        self.assertTrue(Holds()(action, actor))
        action2 = SimpleNamespace(target=object())
        self.assertFalse(Holds()(action2, actor))

    def test_is_self(self):
        char = object()
        actor = _actor(character=char)
        self.assertTrue(IsSelf()(SimpleNamespace(target=char), actor))
        self.assertFalse(IsSelf()(SimpleNamespace(target=object()), actor))

    def test_is_object_dbref(self):
        eff = SimpleNamespace(id=34)
        actor = _actor(effective=eff)
        self.assertTrue(IsObject(34)(None, actor))
        self.assertFalse(IsObject(99)(None, actor))


class TestIntrospection(unittest.TestCase):
    def test_describe(self):
        self.assertEqual(Builder.describe(), "requires Builder")
        self.assertEqual(Holds().describe(), "must be holding it")
        self.assertEqual((Builder & Holds()).describe(), "requires Builder and must be holding it")
        self.assertEqual((Builder | Admin).describe(), "requires Builder or requires Admin")
        self.assertEqual((~Builder).describe(), "not (requires Builder)")

    def test_unmet_returns_first_failing_leaf(self):
        actor = _actor(perms=[], character=SimpleNamespace(contents=[]))
        action = SimpleNamespace(target=object())
        node = Builder & Holds()
        failing = node.unmet(action, actor)
        self.assertIs(failing, Builder)  # cheapest, evaluated first, fails

    def test_unmet_none_when_satisfied(self):
        actor = _actor(perms=["Builder"])
        self.assertIsNone(Builder.unmet(None, actor))


class TestCoercion(unittest.TestCase):
    def test_predicate_passthrough(self):
        self.assertIs(coerce_predicate(Builder), Builder)

    def test_capability_coerced(self):
        self.assertIsInstance(coerce_predicate(DefaultCapability.ADMIN), HasCapability)

    def test_callable_coerced_cost3(self):
        pred = coerce_predicate(lambda a, actor: True)
        self.assertEqual(pred.cost, 3)
        self.assertTrue(pred(None, _actor()))

    def test_lockstring_rejected(self):
        with self.assertRaises(TypeError):
            coerce_predicate("perm(Builder)")

    def test_nonsense_rejected(self):
        with self.assertRaises(TypeError):
            coerce_predicate(42)


class TestFromLockstring(unittest.TestCase):
    def test_perm_to_capability(self):
        pred = from_lockstring("perm(Builder)", "cmd")
        self.assertIs(pred, Builder)

    def test_with_access_type_prefix(self):
        pred = from_lockstring("cmd:perm(Admin)", "cmd")
        self.assertIs(pred, Admin)

    def test_selects_correct_access_type(self):
        ls = "cmd:perm(Builder);view:perm(Player)"
        self.assertIs(from_lockstring(ls, "cmd"), Builder)

    def test_no_matching_access_type_is_never(self):
        self.assertIs(from_lockstring("view:perm(Builder)", "cmd"), NEVER)

    def test_holds(self):
        self.assertIsInstance(from_lockstring("holds()", "cmd"), Holds)

    def test_true_false(self):
        self.assertIs(from_lockstring("true()", "cmd"), ALWAYS)
        self.assertIs(from_lockstring("false()", "cmd"), NEVER)
        self.assertIs(from_lockstring("all()", "cmd"), ALWAYS)
        self.assertIs(from_lockstring("none()", "cmd"), NEVER)

    def test_and(self):
        pred = from_lockstring("perm(Builder) AND holds()", "cmd")
        self.assertIsInstance(pred, And)
        self.assertEqual(len(pred.parts), 2)

    def test_or(self):
        pred = from_lockstring("perm(Builder) OR perm(Admin)", "cmd")
        self.assertIsInstance(pred, Or)

    def test_precedence_and_binds_tighter_than_or(self):
        # Builder OR (Admin AND Holds)
        pred = from_lockstring("perm(Builder) OR perm(Admin) AND holds()", "cmd")
        self.assertIsInstance(pred, Or)
        self.assertTrue(any(isinstance(p, And) for p in pred.parts))

    def test_not(self):
        pred = from_lockstring("not perm(Builder)", "cmd")
        self.assertIsInstance(pred, Not)
        self.assertIs(pred.inner, Builder)

    def test_id_dbref(self):
        self.assertEqual(from_lockstring("id(34)", "cmd"), IsObject(34))
        self.assertEqual(from_lockstring("dbref(7)", "cmd"), IsObject(7))

    def test_tag_with_category(self):
        pred = from_lockstring("tag(vip, members)", "cmd")
        self.assertEqual(pred, HasTag("vip", "members"))

    def test_attr(self):
        pred = from_lockstring("attr(level, 5)", "cmd")
        self.assertEqual(pred, HasAttr("level", "5"))

    def test_unknown_lockfunc_becomes_legacylock(self):
        pred = from_lockstring("frobnicate()", "cmd")
        self.assertIsInstance(pred, LegacyLock)
        self.assertEqual(pred.access_type, "cmd")

    def test_perm_with_unknown_role_falls_back(self):
        pred = from_lockstring("perm(Janitor)", "cmd")
        self.assertIsInstance(pred, LegacyLock)

    def test_non_string_rejected(self):
        with self.assertRaises(TypeError):
            from_lockstring(123, "cmd")


class TestLegacyLock(unittest.TestCase):
    def setUp(self):
        P._LEGACY_WARNED.clear()

    def test_fallback_calls_evaluator_and_warns_once(self):
        actor = _actor(effective=SimpleNamespace(id=1))
        lock = LegacyLock("cmd:frobnicate()", "cmd")
        with mock.patch(
            "evennia.locks.lockhandler.check_lockstring", return_value=True
        ) as chk, mock.patch("evennia.utils.logger.log_warn") as warn:
            self.assertTrue(lock(None, actor))
            self.assertTrue(lock(None, actor))
            self.assertEqual(chk.call_count, 2)
            self.assertEqual(warn.call_count, 1)  # warned once per lockstring


if __name__ == "__main__":
    unittest.main()
