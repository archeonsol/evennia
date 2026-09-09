"""Tests for the engine-shipped dynamic exit resolver."""

import unittest
from types import SimpleNamespace
from unittest import mock

from evennia.actions.default import movement
from evennia.actions.exceptions import AmbiguousTarget


class _Aliases:
    """Small alias-handler stand-in for exit matching."""

    def __init__(self, *aliases):
        self._aliases = aliases

    def all(self):
        """Return configured aliases."""

        return list(self._aliases)


class _Exit:
    """Exit-shaped candidate used without a database."""

    def __init__(self, key, destination, *aliases):
        self.key = key
        self.destination = destination
        self.aliases = _Aliases(*aliases)


class TestExitResolver(unittest.TestCase):
    """Exit ambiguity carries enough information to finish the chosen move."""

    def test_ambiguous_choice_resolver_builds_move_for_selected_exit(self):
        actor = SimpleNamespace(location=object())
        first = _Exit("in", object())
        second = _Exit("in", object())

        with mock.patch.object(movement, "_exits_in", return_value=[first, second]):
            with self.assertRaises(AmbiguousTarget) as caught:
                movement.exit_resolver("in", actor)
            action = caught.exception.choice_resolver(second)

        self.assertIs(action.exit, second)
        self.assertEqual(action.direction, "in")

    def test_ambiguous_choice_expires_when_exit_is_no_longer_local(self):
        actor = SimpleNamespace(location=object())
        first = _Exit("out", object())
        second = _Exit("out", object())

        with mock.patch.object(movement, "_exits_in", return_value=[first, second]):
            with self.assertRaises(AmbiguousTarget) as caught:
                movement.exit_resolver("out", actor)

        with mock.patch.object(movement, "_exits_in", return_value=[first]):
            self.assertIsNone(caught.exception.choice_resolver(second))


if __name__ == "__main__":
    unittest.main()
