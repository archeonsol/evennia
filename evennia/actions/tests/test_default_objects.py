"""get/drop/give/put carry_out rules commit through move_to_async, off the loop."""

import asyncio
import unittest
from unittest import mock

from evennia.actions.context import ActionContext
from evennia.actions.default.objects import (
    CharacterObjectRules,
    ContainerPutRules,
    Drop,
    Get,
    Give,
    Put,
)
from evennia.actions.engine import RuleEngine
from evennia.actions.tests.fakes import FakeChar, FakeObj, make_actor


class _Actor(CharacterObjectRules, FakeChar):
    """Character carrying the shipped get/drop/give rule provider."""


class _Container(ContainerPutRules, FakeObj):
    """Container carrying the shipped put rule provider."""


def _dispatch(action, actor, providers):
    # asyncio.run is load-bearing: the engine swallows rule exceptions, so the
    # fakes._sync helper would turn a mis-driven async rule into a silent pass.
    context = ActionContext(providers=list(providers), actor=actor, raw_string="")
    return asyncio.run(RuleEngine().dispatch(action, actor, context))


def _coin(location, mover):
    """A carried or room-visible coin with no-op transfer hooks."""
    coin = FakeObj(key="coin")
    coin.location = location
    coin.at_pre_get = lambda caller: None
    coin.at_pre_drop = lambda caller: None
    coin.at_pre_give = lambda caller, target: None
    coin.at_post_get = mock.Mock()
    coin.at_post_drop = mock.Mock()
    coin.at_post_give = mock.Mock()
    coin.get_numbered_name = lambda n, looker, return_string=False: (
        "coins" if return_string else ("a coin", "coins")
    )
    mover.search_map["coin"] = coin
    coin.move_to = mock.Mock(side_effect=AssertionError("in-loop commit"))
    coin.move_to_async = mock.AsyncMock(return_value=True)
    return coin


class TestAsyncCommit(unittest.TestCase):
    """Each location-committing carry_out awaits move_to_async."""

    def _char(self):
        room = FakeObj(key="room")
        char = _Actor()
        char.location = room
        return char, make_actor(char), room

    def _trace(self, action, actor, providers):
        return _dispatch(action, actor, providers)

    def test_get_commits_off_the_loop(self):
        char, actor, _room = self._char()
        coin = _coin(char.location, char)
        self._trace(Get(mode="plain", obj_spec="coin"), actor, [char])
        coin.move_to_async.assert_awaited_once_with(char, quiet=True, move_type="get")
        coin.move_to.assert_not_called()
        coin.at_post_get.assert_called_once_with(char)
        # fakes capture msg_contents text before macro expansion
        self.assertTrue(any("(pick) up" in m for m in _room.contents_messages))

    def test_drop_commits_off_the_loop(self):
        char, actor, room = self._char()
        coin = _coin(char, char)
        char.search_for = lambda *args, **kwargs: None
        self._trace(Drop(mode="plain", obj_spec="coin"), actor, [char])
        coin.move_to_async.assert_awaited_once_with(room, quiet=True, move_type="drop")
        coin.move_to.assert_not_called()
        coin.at_post_drop.assert_called_once_with(char)
        self.assertTrue(any("drop" in m for m in room.contents_messages))

    def test_give_commits_off_the_loop(self):
        char, actor, _room = self._char()
        coin = _coin(char, char)
        bob = FakeObj(key="bob")
        char.search_map["bob"] = bob
        char.search_for = lambda *args, **kwargs: None
        self._trace(Give(mode="plain", item_spec="coin", target_spec="bob"), actor, [char])
        coin.move_to_async.assert_awaited_once_with(bob, quiet=True, move_type="give")
        coin.move_to.assert_not_called()
        coin.at_post_give.assert_called_once_with(char, bob)
        self.assertTrue(any("You give" in m for m in char.messages))
        self.assertTrue(any("gives you" in m for m in bob.messages))

    def test_put_commits_off_the_loop(self):
        char, actor, _room = self._char()
        chest = _Container(key="chest")
        coin = _coin(char, chest)
        self._trace(Put(target=coin, container=chest), actor, [char, chest])
        coin.move_to_async.assert_awaited_once_with(chest, quiet=True)
        coin.move_to.assert_not_called()
        self.assertTrue(any("You put" in m for m in char.messages))
