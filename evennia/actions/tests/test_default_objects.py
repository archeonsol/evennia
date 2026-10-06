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

    def test_give_tells_the_receiver_with_a_capital(self):
        char, actor, _room = self._char()
        char.get_display_name = lambda looker=None, **kwargs: "a tall woman"
        _coin(char, char)
        bob = FakeObj(key="bob")
        char.search_map["bob"] = bob
        char.search_for = lambda *args, **kwargs: None
        self._trace(Give(mode="plain", item_spec="coin", target_spec="bob"), actor, [char])
        self.assertIn("A tall woman gives you coins.", bob.messages)

    def test_put_commits_off_the_loop(self):
        char, actor, _room = self._char()
        chest = _Container(key="chest")
        coin = _coin(char, chest)
        self._trace(Put(target=coin, container=chest), actor, [char, chest])
        coin.move_to_async.assert_awaited_once_with(chest, quiet=True)
        coin.move_to.assert_not_called()
        self.assertTrue(any("You put" in m for m in char.messages))

    def test_put_room_echo_excludes_the_actor(self):
        """The actor's direct line must not be doubled by the room broadcast."""
        char, actor, room = self._char()
        chest = _Container(key="chest")
        coin = _coin(char, chest)
        broadcasts = []
        room.msg_contents = lambda text=None, **kwargs: broadcasts.append((text, kwargs))
        self._trace(Put(target=coin, container=chest), actor, [char, chest])
        self.assertEqual([m for m in char.messages if "You put" in m], ["You put coins in chest."])
        self.assertEqual(len(broadcasts), 1)
        self.assertIs(broadcasts[0][1].get("exclude"), char)


class _ScopedChar(CharacterObjectRules, FakeChar):
    """A character whose search honours ``candidates`` and ``location`` as the real one does."""

    def search(self, name, candidates=None, location=None, **kwargs):
        pool = candidates if candidates is not None else getattr(location, "contents", ())
        return next((obj for obj in pool or () if obj.key == name), None)


class TestPutFindsItsContainer(unittest.TestCase):
    """``put`` finds a container you carry as well as one standing in the room."""

    def _scene(self):
        room = FakeObj(key="room")
        char = _ScopedChar()
        char.location = room
        coin = FakeObj(key="coin", location=char)
        char.contents = [coin]
        room.contents = [char]
        return char, make_actor(char), room, coin

    def test_a_carried_container(self):
        char, actor, _room, coin = self._scene()
        bag = FakeObj(key="bag", location=char)
        char.contents.append(bag)
        action = Put.parse("coin in bag", actor)
        self.assertIs(action.container, bag)
        self.assertIs(action.target, coin)
        self.assertFalse(action._unresolved)

    def test_a_container_in_the_room(self):
        char, actor, room, coin = self._scene()
        chest = FakeObj(key="chest", location=room)
        room.contents.append(chest)
        action = Put.parse("coin into chest", actor)
        self.assertIs(action.container, chest)
        self.assertIs(action.target, coin)

    def test_an_item_is_still_taken_from_the_hands(self):
        char, actor, room, _coin = self._scene()
        chest = FakeObj(key="chest", location=room)
        gem = FakeObj(key="gem", location=room)
        room.contents.extend([chest, gem])
        action = Put.parse("gem in chest", actor)
        self.assertIsNone(action.target)
        self.assertTrue(action._unresolved)


class _MessagingChar(CharacterObjectRules, FakeChar):
    """A character whose search reports a miss to itself, as the real one does."""

    def search(self, name, not_found=None, **kwargs):
        found = self.search_map.get(name)
        if found is None:
            self.msg(not_found or f"Could not find '{name}'.")
        return found

    def search_for(self, *args, **kwargs):
        return None


class TestCheckStopsAfterItsOwnMessage(unittest.TestCase):
    """A check that already told the player why stops dispatch without a second line."""

    def _char(self):
        room = FakeObj(key="room")
        char = _MessagingChar()
        char.location = room
        return char, make_actor(char), room

    def _dispatch_quietly(self, action, actor, providers):
        with mock.patch("evennia.utils.logger.log_warn") as warn:
            trace = _dispatch(action, actor, providers)
        warn.assert_not_called()
        return trace

    @staticmethod
    def _carry_out_fired(trace):
        return [entry for entry in trace.phases if entry.phase == "carry_out"]

    def test_dropping_what_you_do_not_carry_gives_one_message(self):
        char, actor, _room = self._char()

        trace = self._dispatch_quietly(Drop(mode="plain", obj_spec="coin"), actor, [char])

        self.assertEqual(char.messages, ["You aren't carrying coin."])
        self.assertEqual(trace.outcome, "blocked")
        self.assertEqual(self._carry_out_fired(trace), [])

    def test_a_vetoed_drop_stops_before_carry_out(self):
        char, actor, _room = self._char()
        coin = _coin(char, char)

        def refuse(caller):
            caller.msg("It is glued to your hand.")
            return False

        coin.at_pre_drop = refuse

        trace = self._dispatch_quietly(Drop(mode="plain", obj_spec="coin"), actor, [char])

        self.assertEqual(char.messages, ["It is glued to your hand."])
        self.assertEqual(self._carry_out_fired(trace), [])
        coin.move_to_async.assert_not_awaited()

    def test_giving_to_nobody_gives_one_message(self):
        char, actor, _room = self._char()
        _coin(char, char)

        trace = self._dispatch_quietly(
            Give(mode="plain", item_spec="coin", target_spec="bob"), actor, [char]
        )

        self.assertEqual(char.messages, ["Could not find 'bob'."])
        self.assertEqual(self._carry_out_fired(trace), [])

    def test_a_container_refusing_an_item_gives_one_message(self):
        char, actor, _room = self._char()
        chest = _Container(key="chest")
        coin = _coin(char, chest)

        def refuse(moved_obj, caller):
            caller.msg("The chest is locked.")
            return False

        chest.at_pre_arrive = refuse
        coin.move_to_async = mock.AsyncMock(return_value=False)

        trace = self._dispatch_quietly(Put(target=coin, container=chest), actor, [char, chest])

        self.assertEqual(char.messages, ["The chest is locked."])
        self.assertEqual(self._carry_out_fired(trace), [])
