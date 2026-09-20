"""Parity + caching tests for the compiled ``Action.parse`` schema.

``Action.parse`` compiles each class's field schema once (annotation kind,
``Literal`` options, ``GameObject`` markers, defaults, greedy last field) and
reuses it. These tests hold the compiled path to the original per-call
implementation: identical values, identical ``ParseError`` messages, and the
same fallback when ``get_type_hints`` cannot resolve an annotation.
"""

import sys
import unittest
from dataclasses import dataclass
from dataclasses import fields as dataclass_fields
from typing import Literal, get_type_hints
from unittest import mock

from evennia.actions.action import (
    Action,
    GameObject,
    _has_default,
    _is_gameobject,
    _literal_options,
)
from evennia.actions.exceptions import AmbiguousTarget, ParseError

# The package re-exports the ``action`` decorator, which shadows the submodule
# attribute on the package; grab the module object from sys.modules.
action_mod = sys.modules["evennia.actions.action"]


def _reference_parse(cls, raw_args, actor, context=None, switches=(), verb=None):
    """The pre-schema implementation, verbatim, as the oracle."""
    tokens = raw_args.split() if raw_args else []
    switches = list(switches)
    try:
        hints = get_type_hints(cls)
    except Exception:
        hints = {}

    init_fields = [f for f in dataclass_fields(cls) if f.init and f.name != "block_reason"]
    values = {}
    idx = 0
    last_i = len(init_fields) - 1

    for i, f in enumerate(init_fields):
        annotation = hints.get(f.name, f.type)
        literal_opts = _literal_options(annotation)

        if literal_opts is not None:
            chosen = None
            for sw in list(switches):
                if sw in literal_opts:
                    chosen = sw
                    switches.remove(sw)
                    break
            if chosen is None and idx < len(tokens):
                tok = tokens[idx]
                idx += 1
                if tok not in literal_opts:
                    raise ParseError(
                        f"'{tok}' is not a valid option for {f.name} "
                        f"(choose from: {', '.join(map(str, literal_opts))})."
                    )
                chosen = tok
            if chosen is None:
                if _has_default(f):
                    continue
                raise ParseError(
                    f"Missing required {f.name} (one of: {', '.join(map(str, literal_opts))})."
                )
            values[f.name] = chosen
            continue

        if _is_gameobject(annotation):
            if idx >= len(tokens):
                if _has_default(f):
                    continue
                raise ParseError(f"{cls._verb_label()} what?")
            tok = tokens[idx]
            idx += 1
            found = actor.search(tok)
            if found is None:
                raise ParseError(f"You don't see '{tok}' here.")
            values[f.name] = found
            continue

        if annotation is int:
            if idx >= len(tokens):
                if _has_default(f):
                    continue
                raise ParseError(f"Missing a number for {f.name}.")
            tok = tokens[idx]
            idx += 1
            try:
                values[f.name] = int(tok)
            except ValueError:
                raise ParseError(f"'{tok}' is not a number.")
            continue

        if idx >= len(tokens):
            if _has_default(f):
                continue
            raise ParseError(f"Missing required {f.name}.")
        if i == last_i:
            values[f.name] = " ".join(tokens[idx:])
            idx = len(tokens)
        else:
            values[f.name] = tokens[idx]
            idx += 1

    return cls(**values)


class _Obj:
    def __init__(self, name):
        self.name = name


class _Actor:
    def __init__(self, objects=None):
        self._objects = objects or {}

    def search(self, name):
        return self._objects.get(name)


@dataclass
class _Kick(Action):
    __action_verbs__ = ("kick",)

    target: GameObject = None
    strength: Literal["soft", "hard"] = "soft"


@dataclass(kw_only=True)
class _Required(Action):
    __action_verbs__ = ("required",)

    target: GameObject
    count: int = 1


@dataclass
class _Multi(Action):
    __action_verbs__ = ("multi",)

    first: str = ""
    second: str = ""


@dataclass
class _Empty(Action):
    __action_verbs__ = ("empty",)


@dataclass
class _Quoted(Action):
    __action_verbs__ = ("quoted",)

    target: "GameObject" = None


@dataclass
class _Broken(Action):
    __action_verbs__ = ("broken",)

    text: "NoSuchTypeAnywhere" = ""


_CASES = [
    (_Kick, "ball hard", ()),
    (_Kick, "ball", ()),
    (_Kick, "ball", ("hard",)),
    (_Kick, "ball sideways", ()),
    (_Kick, "ghost", ()),
    (_Kick, "", ()),
    (_Kick, "ball", ("unknown",)),
    (_Required, "ball", ()),
    (_Required, "ball 7", ()),
    (_Required, "ball x", ()),
    (_Required, "", ()),
    (_Multi, "a b c", ()),
    (_Multi, "a", ()),
    (_Multi, "", ()),
    (_Empty, "", ()),
    (_Empty, "ignored", ()),
    (_Quoted, "ball", ()),
    (_Broken, "hello world", ()),
    (_Broken, "", ()),
]


class TestParseSchemaParity(unittest.TestCase):
    def _actor(self):
        return _Actor({"ball": _Obj("ball")})

    def _run(self, fn, cls, raw_args, actor, switches):
        try:
            result = fn(cls, raw_args, actor, switches)
        except ParseError as err:
            return ("error", str(err))
        return ("ok", result.__dict__)

    def test_values_and_errors_match_reference(self):
        for cls, raw_args, switches in _CASES:
            with self.subTest(cls=cls.__name__, raw=raw_args, switches=switches):
                actor = self._actor()
                expected = self._run(
                    lambda c, raw, act, sw: _reference_parse(c, raw, act, switches=sw),
                    cls,
                    raw_args,
                    actor,
                    switches,
                )
                got = self._run(
                    lambda c, raw, act, sw: c.parse(raw, act, switches=sw),
                    cls,
                    raw_args,
                    actor,
                    switches,
                )
                self.assertEqual(got, expected)

    def test_ambiguous_target_still_propagates(self):
        class _AmbiguousActor(_Actor):
            def search(self, name):
                raise AmbiguousTarget([_Obj("a"), _Obj("b")], name)

        with self.assertRaises(AmbiguousTarget):
            _Kick.parse("ball", _AmbiguousActor(), None)


class TestParseSchemaCaching(unittest.TestCase):
    def test_schema_compiled_once_per_class(self):
        @dataclass
        class _Local(Action):
            target: GameObject = None
            strength: Literal["soft", "hard"] = "soft"

        actor = _Actor({"ball": _Obj("ball")})
        calls = {"n": 0}
        real = action_mod.get_type_hints

        def counting(cls):
            calls["n"] += 1
            return real(cls)

        with mock.patch.object(action_mod, "get_type_hints", side_effect=counting):
            first = _Local.parse("ball hard", actor, None)
            second = _Local.parse("ball soft", actor, None)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(first.strength, "hard")
        self.assertEqual(second.strength, "soft")

    def test_unresolvable_hints_not_cached(self):
        @dataclass
        class _LocalBroken(Action):
            text: "DefinitelyMissingType" = ""

        actor = _Actor()
        calls = {"n": 0}
        real = action_mod.get_type_hints

        def counting(cls):
            calls["n"] += 1
            return real(cls)

        with mock.patch.object(action_mod, "get_type_hints", side_effect=counting):
            first = _LocalBroken.parse("hello world", actor, None)
            second = _LocalBroken.parse("again", actor, None)
        # The fallback path runs per call (never cached) and still parses the
        # string-typed field as the historical implementation did.
        self.assertEqual(calls["n"], 2)
        self.assertEqual(first.text, "hello world")
        self.assertEqual(second.text, "again")


if __name__ == "__main__":
    unittest.main()
