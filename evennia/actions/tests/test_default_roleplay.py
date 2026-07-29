"""Focused tests for the native default roleplay actions."""

import unittest

from evennia.actions.context import ActionContext
from evennia.actions.default.roleplay import (
    DefaultRoleplayRules,
    Emote,
    Pose,
    Say,
    Whisper,
)
from evennia.actions.engine import RuleEngine
from evennia.actions.tests.fakes import FakeChar, FakeObj, _sync, dispatch, make_actor


class Roleplayer(DefaultRoleplayRules, FakeChar):
    """Character fixture recording the stable speech hook contract."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pre_say = []
        self.said = []

    def at_pre_say(self, speech, **kwargs):
        self.pre_say.append((speech, kwargs))
        return None

    def at_say(self, speech, **kwargs):
        self.said.append((speech, kwargs))


class TestRoleplay(unittest.TestCase):
    """Exercise parsing, pure checks, and delivery hook calls."""

    def setUp(self):
        self.room = FakeObj("room")
        self.char = Roleplayer(location=self.room)
        self.actor = make_actor(self.char)

    def test_say_preserves_transform_hooks(self):
        action = Say.parse("hello", self.actor, verb="say")
        dispatch(action, self.actor, [self.char])
        self.assertEqual(self.char.pre_say, [("hello", {})])
        self.assertEqual(self.char.said, [("hello", {"msg_self": True})])

    def test_whisper_resolves_deduplicates_and_exposes_targets(self):
        bob = FakeObj("Bob", location=self.room)
        sue = FakeObj("Sue", location=self.room)
        self.char.search_map.update({"bob": bob, "sue": sue})
        action = Whisper.parse("bob, sue, bob = hush", self.actor, verb="whisper")
        self.assertEqual(action.receivers, (bob, sue))
        self.assertEqual(action.targets, [bob, sue])
        dispatch(action, self.actor, [self.char])
        self.assertEqual(
            self.char.pre_say,
            [("hush", {"whisper": True, "receivers": [bob, sue]})],
        )
        self.assertEqual(
            self.char.said,
            [("hush", {"msg_self": True, "receivers": [bob, sue], "whisper": True})],
        )

    def test_self_whisper_suppresses_separate_echo(self):
        self.char.search_map["me"] = self.char
        action = Whisper.parse("me = secret", self.actor, verb="whisper")
        dispatch(action, self.actor, [self.char])
        self.assertIsNone(self.char.said[0][1]["msg_self"])

    def test_empty_pose_check_blocks_without_direct_message(self):
        action = Pose.parse("", self.actor, verb="pose")
        result = self.char.check_pose(action, self.actor)
        self.assertTrue(result.blocks)
        self.assertFalse(self.char.messages)

    def test_missing_location_check_blocks_without_direct_message(self):
        self.char.location = None
        action = Emote.parse("waves", self.actor, verb="emote")
        result = self.char.check_emote(action, self.actor)
        self.assertTrue(result.blocks)
        self.assertFalse(self.char.messages)

    def test_explain_emits_no_messages(self):
        action = Say.parse("", self.actor, verb="say")
        context = ActionContext(providers=[self.char], actor=self.actor)
        _sync(RuleEngine().explain(action, self.actor, context))
        self.assertFalse(self.char.messages)


if __name__ == "__main__":
    unittest.main()
