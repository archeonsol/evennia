"""Parity + invalidation tests for ``ActionRegistry.suggest_verbs``.

The registry serves suggestions through a SymSpell-style deletion index. These
tests hold that index to the exact behavior of the original linear scan: same
candidate set, same ``(distance, verb)`` ordering, same ``reachable`` semantics,
same system-verb exclusion — across several ``max_dist`` values (including the
linear fallback beyond the index cap) and seeded random vocabularies.
"""

import random
import sys
import unittest
from dataclasses import dataclass
from unittest import mock

from evennia.actions.action import Action
from evennia.actions.registry import ActionRegistry

registry_mod = sys.modules["evennia.actions.registry"]


@dataclass
class _Verb(Action):
    """Registry payload for the synthetic verbs."""


def _reference_levenshtein(a, b):
    """Two-row edit distance, copied from the original implementation."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(
                    prev[j] + 1,
                    cur[j - 1] + 1,
                    prev[j - 1] + (ca != cb),
                )
            )
        prev = cur
    return prev[-1]


def _reference_suggest(reg, token, max_dist=2, limit=3, reachable=None):
    """The pre-index behavior, verbatim, as the oracle."""
    token = token.lower()
    scored = []
    for verb in reg._by_verb:
        if verb.startswith("__") and verb.endswith("__"):
            continue
        dist = _reference_levenshtein(token, verb)
        if dist <= max_dist:
            scored.append((dist, verb))
    scored.sort(key=lambda pair: (pair[0], pair[1]))
    if reachable is None:
        return [verb for _, verb in scored[:limit]]
    out = []
    for _, verb in scored:
        if len(out) >= limit:
            break
        if reachable(verb, reg._by_verb[verb]):
            out.append(verb)
    return out


def _parity_filter(verb, _cls):
    """Deterministic reachable filter for parity runs."""
    return sum(ord(ch) for ch in verb) % 3 != 0


def _build_registry():
    rng = random.Random(20260720)
    alphabet = "abcde"
    verbs = {"look", "say", "lookat", "grab", "go", "drop", "looknorth"}
    while len(verbs) < 70:
        length = rng.randint(1, 6)
        verbs.add("".join(rng.choice(alphabet) for _ in range(length)))
    verbs.update({"__noinput__", "__nomatch__"})
    reg = ActionRegistry()
    reg.register(_Verb, tuple(sorted(verbs)))
    return reg


def _mutations(rng, word):
    """Near-miss variants of ``word``: deletes, inserts, substitutes, swaps."""
    out = {word}
    if word:
        out.add(word[1:])
        out.add(word[:-1])
        for i in range(len(word)):
            out.add(word[:i] + word[i + 1 :])
        for i in range(len(word)):
            for ch in "abc":
                out.add(word[:i] + ch + word[i + 1 :])
        out.add(word + rng.choice("abc"))
        out.add(rng.choice("abc") + word)
        if len(word) > 1:
            i = rng.randrange(len(word) - 1)
            out.add(word[:i] + word[i + 1] + word[i] + word[i + 2 :])
    return out


def _tokens(reg):
    rng = random.Random(97531)
    tokens = set()
    for word in list(reg._by_verb)[:12]:
        tokens |= _mutations(rng, word)
    for _ in range(20):
        length = rng.randint(0, 7)
        tokens.add("".join(rng.choice("abcde") for _ in range(length)))
    return sorted(tokens)[:120]


class TestSuggestVerbsParity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reg = _build_registry()
        cls.tokens = _tokens(cls.reg)

    def test_parity_across_distances_limits_and_filters(self):
        for max_dist in (0, 1, 2, 3, 4):
            for limit in (1, 3, 5):
                for reachable in (None, _parity_filter):
                    for token in self.tokens:
                        expected = _reference_suggest(self.reg, token, max_dist, limit, reachable)
                        got = self.reg.suggest_verbs(
                            token, max_dist=max_dist, limit=limit, reachable=reachable
                        )
                        self.assertEqual(
                            got,
                            expected,
                            (token, max_dist, limit, reachable is not None),
                        )

    def test_system_verbs_never_suggested(self):
        out = self.reg.suggest_verbs("__noinput__")
        self.assertNotIn("__noinput__", out)
        self.assertNotIn("__nomatch__", out)

    def test_empty_token_matches_reference(self):
        for max_dist in (0, 1, 2, 3, 4):
            self.assertEqual(
                self.reg.suggest_verbs("", max_dist=max_dist),
                _reference_suggest(self.reg, "", max_dist),
            )

    def test_limit_zero_and_negative_match_reference(self):
        for limit in (0, -1):
            self.assertEqual(
                self.reg.suggest_verbs("lok", limit=limit),
                _reference_suggest(self.reg, "lok", limit=limit),
            )

    def test_index_invalidated_on_register(self):
        reg = ActionRegistry()
        reg.register(_Verb, ("zzzz",))
        self.assertEqual(reg.suggest_verbs("cast"), [])
        reg.register(_Verb, ("cash",))
        self.assertEqual(reg.suggest_verbs("cast"), ["cash"])

    def test_unicode_token_and_verbs(self):
        reg = ActionRegistry()
        reg.register(_Verb, ("grüß", "gruss", "groß"))
        for token in ("grü", "gruss", "gross", "groß"):
            self.assertEqual(
                reg.suggest_verbs(token),
                _reference_suggest(reg, token),
                token,
            )


class TestSuggestScaling(unittest.TestCase):
    """Deterministic complexity gates: the index must turn a typo into a
    candidate-limited lookup instead of a scan over the whole vocabulary."""

    def test_candidate_verification_is_sublinear(self):
        reg = ActionRegistry()
        reg.register(_Verb, tuple(f"verb{i:04d}" for i in range(500)))
        calls = []
        real = registry_mod._banded_levenshtein

        def counting(a, b, max_dist):
            calls.append(b)
            return real(a, b, max_dist)

        with mock.patch.object(registry_mod, "_banded_levenshtein", side_effect=counting):
            out = reg.suggest_verbs("zzzz", max_dist=2)
        self.assertEqual(out, [])
        # A full scan would verify 500 candidates; the index verifies a handful.
        self.assertLess(len(calls), 100)

    def test_index_cached_across_calls(self):
        reg = ActionRegistry()
        reg.register(_Verb, ("look", "lookat"))
        reg.suggest_verbs("lookk")  # build
        index = reg._suggest_indexes[2]
        reg.suggest_verbs("lookk")
        self.assertIs(reg._suggest_indexes[2], index)


if __name__ == "__main__":
    unittest.main()
