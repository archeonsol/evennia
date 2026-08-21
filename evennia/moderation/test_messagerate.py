"""Tests for message-volume observation.

The property that matters most is a negative one: **this never refuses a
message.** A limit that blocks will eventually refuse a real player in the
middle of a fight round, an auction, or a long pose pasted in pieces, and the
cost of being wrong there is far higher than the cost of a flag nobody reads.

The second property is that it is off unless a game turns it on. "High" is a
property of the game, not of the engine, and a default that guesses would
either flag constantly or never.

"""

from django.test import TestCase, override_settings

from evennia.moderation.messagerate import MessageRateObserver, note_message
from evennia.server.models import ModerationFlag

ON = {
    "MODERATION_MESSAGE_RATE_ENABLED": True,
    "MODERATION_MESSAGE_RATE_LIMIT": 5,
    "MODERATION_MESSAGE_RATE_WINDOW": 60,
}


class TestDisabledByDefault(TestCase):
    """Nothing happens until a game opts in."""

    def setUp(self):
        self.observer = MessageRateObserver()

    def test_it_does_nothing_when_off(self):
        for index in range(500):
            self.observer.note(1, "player", now=100.0 + index)
        self.assertFalse(ModerationFlag.objects.exists())

    def test_the_module_level_helper_is_also_off(self):
        for _ in range(500):
            note_message(1, "player")
        self.assertFalse(ModerationFlag.objects.exists())


@override_settings(**ON)
class TestObservation(TestCase):
    """Counting, and the flag at the threshold."""

    def setUp(self):
        self.observer = MessageRateObserver()

    def _send(self, times, account_id=1, start=100.0):
        results = []
        for index in range(times):
            results.append(self.observer.note(account_id, "player", now=start + index * 0.01))
        return results

    def test_normal_volume_raises_nothing(self):
        self.assertEqual(self._send(5), [False] * 5)
        self.assertFalse(ModerationFlag.objects.exists())

    def test_crossing_the_threshold_raises_one_flag(self):
        self._send(6)
        flag = ModerationFlag.objects.get()
        self.assertEqual(flag.kind, ModerationFlag.KIND_MESSAGE_BURST)
        self.assertEqual(flag.account_id, 1)

    def test_the_flag_records_the_count_and_the_window(self):
        self._send(6)
        flag = ModerationFlag.objects.get()
        self.assertEqual(flag.evidence["count"], 6)
        self.assertEqual(flag.evidence["window_seconds"], 60)

    def test_the_flag_is_low_severity(self):
        # Volume is corroboration, not proof. Somebody can be loud and fine.
        self._send(6)
        self.assertEqual(ModerationFlag.objects.get().severity, 1)

    def test_a_sustained_flood_is_one_row_not_thousands(self):
        self._send(400)
        self.assertEqual(ModerationFlag.objects.count(), 1)

    def test_two_accounts_are_counted_separately(self):
        self._send(6, account_id=1)
        self._send(3, account_id=2)
        self.assertEqual(ModerationFlag.objects.count(), 1)

    def test_messages_outside_the_window_do_not_count(self):
        self._send(5, start=100.0)
        self.assertFalse(self.observer.note(1, "player", now=100.0 + 3600))
        self.assertFalse(ModerationFlag.objects.exists())

    def test_it_never_refuses(self):
        # The return value reports whether a flag was written. It is not a
        # refusal, and no caller may treat it as one.
        results = self._send(20)
        self.assertIn(True, results, "no flag was written at all")
        # Every call still returned; nothing raised and nothing was blocked.
        self.assertEqual(len(results), 20)

    def test_an_unknown_account_is_skipped(self):
        self.assertFalse(self.observer.note(None, "nobody", now=100.0))
        self.assertFalse(self.observer.note(0, "nobody", now=100.0))

    def test_a_broken_flag_write_does_not_propagate(self):
        from unittest.mock import patch

        with patch(
            "evennia.moderation.flags.raise_flag", side_effect=RuntimeError("database gone")
        ):
            self.assertEqual(self._send(6)[-1], False)

    @override_settings(MODERATION_MESSAGE_RATE_MAX_KEYS=3)
    def test_memory_is_bounded(self):
        for account_id in range(1, 12):
            self.observer.note(account_id, "player", now=100.0)
        self.assertLessEqual(self.observer.stats()["accounts"], 3)

    @override_settings(MODERATION_MESSAGE_RATE_LIMIT=0)
    def test_a_zero_limit_disables_it(self):
        self._send(50)
        self.assertFalse(ModerationFlag.objects.exists())


@override_settings(**ON)
class TestNoDatabaseOnTheMessagePath(TestCase):
    """The cost of counting a message must be nothing."""

    def setUp(self):
        self.observer = MessageRateObserver()

    def test_counting_under_the_threshold_touches_no_database(self):
        # Safe to call on every send only if this holds.
        with self.assertNumQueries(0):
            for index in range(5):
                self.observer.note(1, "player", now=100.0 + index * 0.01)
