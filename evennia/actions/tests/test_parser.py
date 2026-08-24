"""Tests for the action parser (CM1 Phase 2).

The parser turns raw text into a typed :class:`Action`: verb resolution
(exact / unambiguous-prefix / fuzzy), per-action argument parsing by dataclass
field type, switch extraction, and the system-action fallbacks
(``NoInputAction`` / ``NoMatchAction``). To stay independent of the global
verb registry, these tests build an isolated :class:`ActionRegistry` and feed it
to a private :class:`ActionParser`.
"""

import unittest
from dataclasses import dataclass, field
from typing import Literal

from evennia.actions.action import Action, GameObject
from evennia.actions.exceptions import AmbiguousTarget, ParseError
from evennia.actions.parser import ActionParser, NoMatchAction, ParseResult
from evennia.actions.predicate import HasCapability
from evennia.actions.registry import ActionRegistry
from evennia.actions.result import CLAIM
from evennia.actions.rule import rule


# --- test action types ------------------------------------------------------
@dataclass
class Kick(Action):
    target: GameObject = None
    strength: Literal["soft", "hard"] = "soft"


@dataclass
class Look(Action):
    target: GameObject = None


@dataclass
class Punch(Action):
    target: GameObject = None


@dataclass
class Speak(Action):
    message: str = ""
    matched: str = field(default="", compare=False)

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        return cls(message=(raw_args or "").strip(), matched=verb or "")


@dataclass
class Emit(Action):
    text: str = ""

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        text = (raw_args or "").strip()
        if verb in (",", "p,"):
            text = "," + text  # `,` consumed as the verb; re-attach the marker
        return cls(text=text)


# --- actor stub -------------------------------------------------------------
class _Obj:
    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"<Obj {self.name}>"


class FakeActor:
    """Minimal actor: ``search`` returns a known object, ``None`` for a miss, or
    raises :class:`AmbiguousTarget` for a name flagged ambiguous. Carries the
    context-building surface (``effective``/``state_objects``/…) so the
    suggestion reachability filter can walk providers; the filter fails closed
    on anything less actor-shaped."""

    def __init__(self, objects=None, ambiguous=(), effective=None):
        self._objects = objects or {}
        self._ambiguous = set(ambiguous)
        self.effective = effective if effective is not None else self
        self.character = None
        self.account = None
        self.location = None
        self.state_objects = []
        self.equipped_items = []

    def search(self, name):
        if name in self._ambiguous:
            raise AmbiguousTarget(
                candidates=[_Obj(name + "1"), _Obj(name + "2")], original_raw=name
            )
        return self._objects.get(name)


def _registry():
    reg = ActionRegistry()
    for cls, verbs in ((Kick, ("kick",)), (Look, ("look",)), (Punch, ("punch",))):
        cls.__action_verbs__ = verbs
        reg.register(cls, verbs)
    return reg


def _parser():
    return ActionParser(registry=_registry())


def _actor():
    return FakeActor(objects={"ball": _Obj("ball")})


# --- verb resolution --------------------------------------------------------
class TestVerbResolution(unittest.TestCase):
    def test_exact_match_confidence_one(self):
        res = _parser().parse("kick ball", _actor())
        self.assertIsInstance(res, ParseResult)
        self.assertIsInstance(res.action, Kick)
        self.assertEqual(res.confidence, 1.0)
        self.assertEqual(res.raw_verb, "kick")
        self.assertEqual(res.raw_args, "ball")

    def test_prefix_match_confidence_below_one(self):
        res = _parser().parse("pun ball", _actor())
        self.assertIsInstance(res.action, Punch)
        self.assertLess(res.confidence, 1.0)
        self.assertAlmostEqual(res.confidence, 3 / 5)

    def test_unknown_verb_yields_nomatch(self):
        res = _parser().parse("frobnicate ball", _actor())
        self.assertIsInstance(res.action, NoMatchAction)
        self.assertEqual(res.confidence, 0.0)
        self.assertEqual(res.action.raw_string, "frobnicate ball")

    def test_empty_input_returns_none(self):
        self.assertIsNone(_parser().parse("", _actor()))
        self.assertIsNone(_parser().parse("   ", _actor()))
        self.assertIsNone(_parser().parse(None, _actor()))

    def test_fuzzy_suggestions_on_near_miss(self):
        res = _parser().parse("kik ball", _actor())
        self.assertIsInstance(res.action, NoMatchAction)
        self.assertIn("kick", res.action.suggestions)


# --- per-action argument parsing --------------------------------------------
class TestActionParse(unittest.TestCase):
    def test_positional_object_and_literal(self):
        ball = _Obj("ball")
        actor = FakeActor(objects={"ball": ball})
        kick = Kick.parse("ball hard", actor, None)
        self.assertIs(kick.target, ball)
        self.assertEqual(kick.strength, "hard")

    def test_literal_defaults_when_omitted(self):
        ball = _Obj("ball")
        kick = Kick.parse("ball", FakeActor(objects={"ball": ball}), None)
        self.assertIs(kick.target, ball)
        self.assertEqual(kick.strength, "soft")

    def test_invalid_literal_raises_parse_error(self):
        actor = FakeActor(objects={"ball": _Obj("ball")})
        with self.assertRaises(ParseError):
            Kick.parse("ball sideways", actor, None)

    def test_missing_object_raises_parse_error(self):
        # search returns None → "you don't see it" ParseError.
        with self.assertRaises(ParseError):
            Kick.parse("ghost", FakeActor(objects={}), None)

    def test_ambiguous_target_propagates(self):
        actor = FakeActor(ambiguous={"ball"})
        with self.assertRaises(AmbiguousTarget):
            Kick.parse("ball", actor, None)


# --- switches ---------------------------------------------------------------
class TestSwitches(unittest.TestCase):
    def test_switch_fills_literal_field(self):
        ball = _Obj("ball")
        actor = FakeActor(objects={"ball": ball})
        res = _parser().parse("kick/hard ball", actor)
        self.assertIsInstance(res.action, Kick)
        self.assertIs(res.action.target, ball)
        self.assertEqual(res.action.strength, "hard")
        self.assertEqual(res.raw_verb, "kick")


# --- parser error handling --------------------------------------------------
class TestParserErrorHandling(unittest.TestCase):
    def test_parse_error_becomes_nomatch_with_message(self):
        actor = FakeActor(objects={})
        res = _parser().parse("kick ghost", actor)
        self.assertIsInstance(res.action, NoMatchAction)
        self.assertTrue(res.action.error)
        self.assertIn("ghost", res.action.error)

    def test_ambiguous_target_not_swallowed_by_parser(self):
        actor = FakeActor(ambiguous={"ball"})
        with self.assertRaises(AmbiguousTarget):
            _parser().parse("kick ball", actor)

    def test_raw_string_recorded_on_action(self):
        res = _parser().parse("kick ball", _actor())
        self.assertEqual(res.action._raw_string, "kick ball")


# --- no-space symbol-prefix verbs (arg_regex=None analogue) -----------------
def _symbol_registry():
    reg = ActionRegistry()
    for cls, verbs in ((Speak, ("say", '"', "'")), (Emit, (".", ","))):
        cls.__action_verbs__ = verbs
        reg.register(cls, verbs)
    return reg


class TestSymbolPrefix(unittest.TestCase):
    def _p(self):
        return ActionParser(registry=_symbol_registry())

    def test_no_space_quote_routes(self):
        res = self._p().parse('"hello there', _actor())
        self.assertIsInstance(res.action, Speak)
        self.assertEqual(res.action.message, "hello there")
        self.assertEqual(res.raw_verb, '"')
        self.assertEqual(res.confidence, 1.0)

    def test_no_space_apostrophe_routes(self):
        res = self._p().parse("'hi", _actor())
        self.assertIsInstance(res.action, Speak)
        self.assertEqual(res.action.message, "hi")

    def test_space_form_still_routes(self):
        res = self._p().parse('" hi', _actor())
        self.assertIsInstance(res.action, Speak)
        self.assertEqual(res.action.message, "hi")

    def test_dot_prefix_routes(self):
        res = self._p().parse(".wave a hand", _actor())
        self.assertIsInstance(res.action, Emit)
        self.assertEqual(res.action.text, "wave a hand")

    def test_matched_verb_threaded_into_parse(self):
        res = self._p().parse('"hi', _actor())
        self.assertEqual(res.action.matched, '"')

    def test_comma_marker_reattached(self):
        res = self._p().parse(",The stage is calm", _actor())
        self.assertIsInstance(res.action, Emit)
        self.assertEqual(res.action.text, ",The stage is calm")

    def test_bare_symbol_verb_routes(self):
        res = self._p().parse('"', _actor())
        self.assertIsInstance(res.action, Speak)
        self.assertEqual(res.action.message, "")

    def test_word_verb_is_never_glued(self):
        # "sayhello" must NOT match the word verb "say"; only punctuation verbs
        # are eligible for no-space prefixing.
        res = self._p().parse("sayhello", _actor())
        self.assertIsInstance(res.action, NoMatchAction)

    def test_unregistered_symbol_is_nomatch(self):
        res = self._p().parse("@foo", _actor())
        self.assertIsInstance(res.action, NoMatchAction)


class TestExplicitGluedPrefix(unittest.TestCase):
    """Word-bearing aliases may opt into the no-space prefix surface."""

    def _p(self):
        reg = ActionRegistry()
        Emit.__action_glued_verbs__ = ("p.", "p,")
        reg.register(Emit, ("ppose", "p.", "p,"))
        return ActionParser(registry=reg)

    def test_glued_word_dot_prefix_routes(self):
        res = self._p().parse("p.wave a hand", _actor())
        self.assertIsInstance(res.action, Emit)
        self.assertEqual(res.action.text, "wave a hand")
        self.assertEqual(res.raw_verb, "p.")

    def test_glued_word_comma_prefix_preserves_marker(self):
        res = self._p().parse("p,The stage is calm", _actor())
        self.assertIsInstance(res.action, Emit)
        self.assertEqual(res.action.text, ",The stage is calm")

    def test_unmarked_word_alias_is_not_glued(self):
        res = self._p().parse("pposewaves", _actor())
        self.assertIsInstance(res.action, NoMatchAction)


# --- dynamic verb resolvers (exit names, channels, nicks) -------------------
@dataclass
class Move(Action):
    direction: str = ""


class TestDynamicResolver(unittest.TestCase):
    """A resolver is consulted only after trie + symbol misses, and before
    no-match; it claims the whole stripped line and may raise AmbiguousTarget."""

    def _resolver(self, *, known=("north",), ambiguous=()):
        def resolve(stripped, actor):
            token = stripped.lower()
            if token in ambiguous:
                raise AmbiguousTarget(candidates=[_Obj("a"), _Obj("b")], original_raw=stripped)
            if token in known:
                return Move(direction=token)
            return None

        return resolve

    def test_resolver_claims_unmatched_verb(self):
        p = _parser()
        p.add_resolver(self._resolver())
        res = p.parse("north", _actor())
        self.assertIsInstance(res.action, Move)
        self.assertEqual(res.action.direction, "north")
        self.assertEqual(res.confidence, 1.0)
        self.assertEqual(res.action._raw_string, "north")

    def test_registered_verb_still_wins(self):
        p = _parser()
        p.add_resolver(self._resolver(known=("kick",)))  # would shadow if consulted
        res = p.parse("kick ball", _actor())
        self.assertIsInstance(res.action, Kick)  # trie hit, resolver never tried

    def test_resolver_decline_falls_through_to_nomatch(self):
        p = _parser()
        p.add_resolver(self._resolver(known=("north",)))
        res = p.parse("frobnicate", _actor())
        self.assertIsInstance(res.action, NoMatchAction)

    def test_resolver_ambiguity_propagates(self):
        p = _parser()
        p.add_resolver(self._resolver(ambiguous={"door"}))
        with self.assertRaises(AmbiguousTarget):
            p.parse("door", _actor())

    def test_resolvers_tried_in_order(self):
        p = _parser()
        calls = []

        def first(stripped, actor):
            calls.append("first")
            return None

        def second(stripped, actor):
            calls.append("second")
            return Move(direction=stripped)

        p.add_resolver(first)
        p.add_resolver(second)
        p.parse("shard", _actor())
        self.assertEqual(calls, ["first", "second"])

    def test_add_resolver_is_idempotent(self):
        p = _parser()
        r = self._resolver()
        p.add_resolver(r)
        p.add_resolver(r)
        self.assertEqual(len(p._resolvers), 1)


# --- multi-word phrase verbs ------------------------------------------------
@dataclass
class Go(Action):
    tokens: list = field(default_factory=list)

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        out = [s.strip().lower() for s in switches if s and s.strip()]
        rest = (raw_args or "").strip()
        if rest:
            out.extend(rest.split())
        return cls(tokens=out)


@dataclass
class GoShard(Action):
    switches: tuple = ()

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        return cls(switches=tuple(switches or ()))


@dataclass
class Grapple(Action):
    target: str = ""

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        return cls(target=(raw_args or "").strip())


@dataclass
class GrappleThrow(Action):
    direction: str = ""

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        return cls(direction=(raw_args or "").strip().lower())


@dataclass
class JackIn(Action):
    pass


@dataclass
class JackOut(Action):
    pass


@dataclass
class EnterPod(Action):
    pass


def _phrase_registry():
    reg = ActionRegistry()
    specs = (
        (Kick, ("kick",)),
        (Look, ("look",)),
        (Punch, ("punch",)),
        (Go, ("go",)),
        (GoShard, ("go shard",)),
        (Grapple, ("grapple",)),
        (GrappleThrow, ("grapple throw", "toss")),
        (JackIn, ("jack in", "jackin")),
        (JackOut, ("jack out", "jackout")),
        (EnterPod, ("enter pod", "enter splinter pod")),
    )
    for cls, verbs in specs:
        cls.__action_verbs__ = verbs
        reg.register(cls, verbs)
    return reg


def _phrase_parser():
    return ActionParser(registry=_phrase_registry())


class TestPhraseVerbs(unittest.TestCase):
    def test_longest_phrase_beats_shorter_verb(self):
        res = _phrase_parser().parse("go shard", _actor())
        self.assertIsInstance(res.action, GoShard)
        self.assertEqual(res.confidence, 1.0)
        self.assertEqual(res.raw_verb, "go shard")
        self.assertEqual(res.raw_args, "")

    def test_switch_on_second_word_of_phrase(self):
        res = _phrase_parser().parse("go shard/all north/east", _actor())
        self.assertIsInstance(res.action, GoShard)
        self.assertEqual(res.raw_verb, "go shard")
        self.assertEqual(res.raw_args, "north/east")
        self.assertEqual(res.action.switches, ("all",))

    def test_shorter_verb_when_longer_phrase_missing(self):
        res = _phrase_parser().parse("go north", _actor())
        self.assertIsInstance(res.action, Go)
        self.assertEqual(res.raw_verb, "go")
        self.assertEqual(res.raw_args, "north")
        self.assertEqual(res.action.tokens, ["north"])

    def test_grapple_throw_beats_grapple(self):
        res = _phrase_parser().parse("grapple throw north", _actor())
        self.assertIsInstance(res.action, GrappleThrow)
        self.assertEqual(res.raw_verb, "grapple throw")
        self.assertEqual(res.action.direction, "north")

    def test_grapple_single_word(self):
        res = _phrase_parser().parse("grapple bob", _actor())
        self.assertIsInstance(res.action, Grapple)
        self.assertEqual(res.raw_verb, "grapple")
        self.assertEqual(res.action.target, "bob")

    def test_three_word_phrase_alias(self):
        res = _phrase_parser().parse("enter splinter pod", _actor())
        self.assertIsInstance(res.action, EnterPod)
        self.assertEqual(res.raw_verb, "enter splinter pod")

    def test_two_word_phrase_alias(self):
        res = _phrase_parser().parse("enter pod", _actor())
        self.assertIsInstance(res.action, EnterPod)
        self.assertEqual(res.raw_verb, "enter pod")

    def test_jack_in_and_out(self):
        p = _phrase_parser()
        res_in = p.parse("jack in", _actor())
        self.assertIsInstance(res_in.action, JackIn)
        res_out = p.parse("jack out", _actor())
        self.assertIsInstance(res_out.action, JackOut)

    def test_shared_stem_without_parent_is_nomatch(self):
        res = _phrase_parser().parse("jack", _actor())
        self.assertIsInstance(res.action, NoMatchAction)

    def test_registered_phrase_beats_dynamic_resolver(self):
        p = _phrase_parser()

        def resolve(stripped, actor):
            if stripped.lower() == "go shard":
                return Move(direction="shard")
            return None

        p.add_resolver(resolve)
        res = p.parse("go shard", _actor())
        self.assertIsInstance(res.action, GoShard)

    def test_prefix_abbrev_still_works(self):
        res = _phrase_parser().parse("pun ball", _actor())
        self.assertIsInstance(res.action, Punch)
        self.assertLess(res.confidence, 1.0)


# --- gated-verb suggestion filtering (fail-closed suggestions) ---------------
@dataclass
class Dig(Action):
    """A staff verb whose only carry_out path is capability-gated."""


@dataclass
class Wave(Action):
    """An ungated verb whose carry_out the actor's character carries."""


@dataclass
class Ping(Action):
    """A registered verb no provider carries any rule for."""


class _Perms:
    def __init__(self, perms):
        self._perms = list(perms)

    def all(self):
        return list(self._perms)


class _ProviderChar:
    """Character-like provider with one gated and one public carry-out."""

    def __init__(self, perms=("Player",)):
        self.permissions = _Perms(perms)
        self.account = None
        self.location = None
        self.has_capability = lambda key, **kwargs: key in set(perms)

    @rule(Dig, phase="carry_out", requires=HasCapability("engine.world.build"))
    def carry_out_dig(self, action, actor):
        return CLAIM

    @rule(Wave, phase="carry_out")
    def carry_out_wave(self, action, actor):
        return CLAIM


def _gated_parser():
    reg = ActionRegistry()
    for cls, verbs in ((Dig, ("@dig",)), (Wave, ("@wig",)), (Ping, ("@pig",))):
        cls.__action_verbs__ = verbs
        reg.register(cls, verbs)
    return ActionParser(registry=reg)


def _gated_actor(perms=("Player",)):
    char = _ProviderChar(perms=perms)
    actor = FakeActor(effective=char)
    actor.character = char
    return actor


class TestGatedSuggestions(unittest.TestCase):
    """A typo must not surface a verb the actor has no non-gated path to —
    mirroring the .91 fail-closed dispatch boundary: a fully gated verb is
    indistinguishable from one that does not exist."""

    def test_gated_verb_hidden_from_ungated_actor(self):
        res = _gated_parser().parse("@dgi", _gated_actor(perms=("Player",)))
        self.assertIsInstance(res.action, NoMatchAction)
        self.assertNotIn("@dig", res.action.suggestions)

    def test_gated_verb_suggested_when_gate_passes(self):
        res = _gated_parser().parse("@dgi", _gated_actor(perms=("engine.world.build",)))
        self.assertIsInstance(res.action, NoMatchAction)
        self.assertIn("@dig", res.action.suggestions)

    def test_ungated_verb_still_suggested(self):
        res = _gated_parser().parse("@wgi", _gated_actor(perms=("Player",)))
        self.assertIn("@wig", res.action.suggestions)

    def test_ruleless_verb_still_suggested(self):
        # No provider carries a rule for Ping: inapplicable, not secret (the
        # dispatch path messages it honestly, so suggestions may name it too).
        res = _gated_parser().parse("@pgi", _gated_actor(perms=("Player",)))
        self.assertIn("@pig", res.action.suggestions)

    def test_unshaped_actor_fails_closed(self):
        # An actor without the context surface gets no suggestions rather than
        # a leak (production actors always carry it).
        res = _gated_parser().parse("@dgi", _Obj("husk"))
        self.assertIsInstance(res.action, NoMatchAction)
        self.assertEqual(res.action.suggestions, [])


if __name__ == "__main__":
    unittest.main()
