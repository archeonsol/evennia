"""Watching one session, and what gets recorded about it.

The properties under test are the ones that make this an operations tool
rather than something worse: the record is written when surveillance happens
and not before, captured content never reaches the database, and one
operator's capture never reaches another operator.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings

from evennia.console import watch
from evennia.console.models import ConsoleAuditEvent


def _session(sessid=1, username="player", pk=7):
    """Return a session stand-in carrying an account."""

    return SimpleNamespace(sessid=sessid, account=SimpleNamespace(pk=pk, username=username))


class WatchTestCase(TestCase):
    """Every test starts with nothing being watched."""

    def setUp(self):
        self.clock_patcher = patch("evennia.utils.clock.call_later")
        self.call_later = self.clock_patcher.start()
        watch.WATCHES.clear()

    def tearDown(self):
        watch.WATCHES.clear()
        self.clock_patcher.stop()

    def _start(self, sessid=1, watcher_id=42):
        return watch.start(sessid, _session(sessid), watcher_id, "staffer", "a report")


class TestCostWhenNobodyWatches(WatchTestCase):
    """The taps sit on every message a server sends. They must cost nothing."""

    def test_the_registry_is_empty_so_callers_can_skip_the_tap(self):
        self.assertFalse(watch.WATCHES)

    def test_tapping_an_unwatched_session_keeps_nothing(self):
        watch.tap(_session(9), "out", {"text": (("hello",), {})})
        self.assertFalse(watch.WATCHES)


class TestCapture(WatchTestCase):
    """What a watch collects, and how it reads."""

    def test_output_is_captured(self):
        entry = self._start()
        watch.tap(_session(1), "out", {"text": (("you see a door",), {})})
        self.assertEqual(entry.frames[0]["line"], "you see a door")
        self.assertEqual(entry.frames[0]["dir"], "out")

    def test_output_is_readable_without_evennia_color_markup(self):
        entry = self._start()
        watch.tap(_session(1), "out", {"text": (("|rred warning|n",), {})})
        self.assertEqual(entry.frames[0]["line"], "red warning")

    def test_input_is_captured_unredacted(self):
        # The operator chose to see both directions in full. The safety is that
        # this never leaves memory, not that it is filtered.
        entry = self._start()
        watch.tap(_session(1), "in", {"text": (("say something private",), {})})
        self.assertEqual(entry.frames[0]["line"], "say something private")
        self.assertEqual(entry.frames[0]["dir"], "in")

    def test_input_keeps_text_that_looks_like_evennia_markup(self):
        entry = self._start()
        watch.tap(_session(1), "in", {"text": (("say |r is literal",), {})})
        self.assertEqual(entry.frames[0]["line"], "say |r is literal")

    def test_a_structured_payload_is_named_but_not_dumped(self):
        # A patch carrying a JSON document is noise on a transcript. That one
        # went past is not.
        entry = self._start()
        watch.tap(_session(1), "out", {"patch": ((), {"ops": []})})
        self.assertEqual(entry.frames[0]["line"], "[patch]")

    def test_a_watch_on_another_session_captures_nothing(self):
        entry = self._start(sessid=1)
        watch.tap(_session(2), "out", {"text": (("elsewhere",), {})})
        self.assertEqual(len(entry.frames), 0)

    @override_settings(CONSOLE_WATCH_FRAMES=3)
    def test_the_buffer_is_bounded(self):
        entry = self._start()
        for index in range(10):
            watch.tap(_session(1), "out", {"text": ((f"line {index}",), {})})
        self.assertEqual(len(entry.frames), 3)
        self.assertEqual(entry.frames[-1]["line"], "line 9")

    def test_a_broken_frame_cannot_break_the_players_traffic(self):
        # A watch is an observer. It does not get to break what it observes.
        self._start()
        watch.tap(_session(1), "out", {"text": object()})


class TestScoping(WatchTestCase):
    """One operator's capture is not another operator's."""

    def test_two_operators_watching_one_session_each_get_their_own_copy(self):
        first = self._start(watcher_id=1)
        second = self._start(watcher_id=2)
        watch.tap(_session(1), "out", {"text": (("shared",), {})})
        self.assertEqual(len(first.frames), 1)
        self.assertEqual(len(second.frames), 1)

    def test_draining_returns_only_this_operators_frames(self):
        self._start(sessid=1, watcher_id=1)
        self._start(sessid=2, watcher_id=2)
        watch.tap(_session(1), "out", {"text": (("for one",), {})})
        watch.tap(_session(2), "out", {"text": (("for two",), {})})
        drained = watch.drain(1)
        self.assertEqual([frame["line"] for frame in drained], ["for one"])

    def test_drained_frames_name_the_exact_watch_instance(self):
        entry = self._start(watcher_id=1)
        watch.tap(_session(1), "out", {"text": (("one",), {})})
        drained = watch.drain(1)
        self.assertEqual(drained[0]["watch_id"], entry.watch_id)

    def test_rewatching_a_session_gets_a_new_instance_id(self):
        first = self._start(watcher_id=1)
        watch.stop(1, 1)
        second = self._start(watcher_id=1)
        self.assertNotEqual(first.watch_id, second.watch_id)

    def test_draining_twice_does_not_repeat(self):
        self._start(watcher_id=1)
        watch.tap(_session(1), "out", {"text": (("once",), {})})
        self.assertEqual(len(watch.drain(1)), 1)
        self.assertEqual(len(watch.drain(1)), 0)

    def test_one_operator_cannot_watch_one_session_twice(self):
        self._start(watcher_id=1)
        with self.assertRaises(ValueError):
            self._start(watcher_id=1)

    @override_settings(CONSOLE_WATCH_LIMIT=1)
    def test_the_concurrent_limit_is_enforced(self):
        self._start(sessid=1, watcher_id=1)
        with self.assertRaises(ValueError):
            self._start(sessid=2, watcher_id=2)


class TestAudit(WatchTestCase):
    """The record is written when surveillance happens, and not before."""

    def test_starting_a_watch_records_nothing(self):
        # The earlier version recorded the intent and never carried it out,
        # which made the permanent log assert something that had not happened.
        self._start()
        self.assertFalse(ConsoleAuditEvent.objects.exists())

    def test_a_watch_that_sees_nothing_records_nothing(self):
        self._start()
        watch.stop(1, 42)
        self.assertFalse(ConsoleAuditEvent.objects.exists())

    def test_the_first_delivered_frame_writes_a_permanent_row(self):
        self._start()
        watch.tap(_session(1), "out", {"text": (("seen",), {})})
        watch.drain(42)
        row = ConsoleAuditEvent.objects.get(operation="watch.began")
        self.assertEqual(row.retention, ConsoleAuditEvent.RETENTION_PERMANENT)
        self.assertEqual(row.after["watched_account"], "player")
        self.assertEqual(row.target_ref, "accounts.accountdb#7")
        self.assertEqual(row.message, "a report")

    def test_the_began_row_is_written_once(self):
        self._start()
        for index in range(3):
            watch.tap(_session(1), "out", {"text": ((f"line {index}",), {})})
            watch.drain(42)
        self.assertEqual(ConsoleAuditEvent.objects.filter(operation="watch.began").count(), 1)

    def test_stopping_records_how_much_was_seen(self):
        self._start()
        watch.tap(_session(1), "out", {"text": (("seen",), {})})
        watch.drain(42)
        watch.stop(1, 42, why="the operator stopped it")
        row = ConsoleAuditEvent.objects.get(operation="watch.ended")
        self.assertEqual(row.retention, ConsoleAuditEvent.RETENTION_PERMANENT)
        self.assertEqual(row.after["frames"], 1)
        self.assertEqual(row.after["ended_because"], "the operator stopped it")

    def test_no_captured_content_reaches_the_database(self):
        # Input is relayed unredacted, so it can carry a password typed at a
        # login prompt. That is a live view held in memory for minutes; it must
        # not become a permanent row that outlives the watch.
        self._start()
        watch.tap(_session(1), "in", {"text": (("connect sol hunter2",), {})})
        watch.drain(42)
        watch.stop(1, 42)
        for row in ConsoleAuditEvent.objects.all():
            self.assertNotIn("hunter2", repr(row.after))
            self.assertNotIn("hunter2", str(row.message))


class TestEnding(WatchTestCase):
    """A watch ends on its own, three different ways."""

    def test_a_disconnect_ends_every_watch_on_that_session(self):
        self._start(watcher_id=1)
        self._start(watcher_id=2)
        watch.stop_session(1)
        self.assertFalse(watch.WATCHES)

    def test_an_expired_watch_is_swept(self):
        entry = self._start()
        entry.expires = 0
        self.assertEqual(len(watch.sweep()), 1)
        self.assertFalse(watch.WATCHES)

    def test_expiry_is_scheduled_even_if_the_operator_closes_the_console(self):
        entry = self._start()
        seconds, callback, watch_id = self.call_later.call_args.args
        self.assertGreater(seconds, 0)
        self.assertEqual(watch_id, entry.watch_id)

        callback(watch_id)

        self.assertFalse(watch.WATCHES)

    def test_stopping_early_cancels_the_scheduled_expiry(self):
        self._start()
        handle = self.call_later.return_value
        watch.stop(1, 42)
        handle.cancel.assert_called_once_with()

    def test_expiry_records_why_it_ended(self):
        entry = self._start()
        watch.tap(_session(1), "out", {"text": (("seen",), {})})
        watch.drain(42)
        entry.expires = 0
        watch.sweep()
        row = ConsoleAuditEvent.objects.get(operation="watch.ended")
        self.assertEqual(row.after["ended_because"], "the watch expired")

    def test_stopping_a_watch_nobody_started_returns_nothing(self):
        self.assertIsNone(watch.stop(99, 42))

    @override_settings(CONSOLE_WATCH_SECONDS=60)
    def test_the_deadline_comes_from_settings(self):
        import time

        entry = self._start()
        self.assertLessEqual(entry.expires - time.monotonic(), 60)


class TestPanel(WatchTestCase):
    """The console actions on top of the registry."""

    def _ctx(self):
        return SimpleNamespace(actor_id=42, actor_name="staffer", capabilities=frozenset())

    def _panel(self):
        from evennia.console.panels.dangerous import SessionsPanel

        return SessionsPanel()

    def test_watching_requires_a_reason(self):
        with self.assertRaises(ValueError):
            self._panel().watch(self._ctx(), sessid=1, reason="")

    def test_watching_an_absent_session_is_a_lookup_error(self):
        with patch("evennia.server.sessionhandler.SESSIONS") as sessions:
            sessions.session_from_sessid.return_value = None
            with self.assertRaises(LookupError):
                self._panel().watch(self._ctx(), sessid=4242, reason="investigating")

    def test_starting_a_watch_says_it_stops_on_its_own(self):
        with patch("evennia.server.sessionhandler.SESSIONS") as sessions:
            sessions.session_from_sessid.return_value = _session(3)
            result = self._panel().watch(self._ctx(), sessid=3, reason="a report")
        self.assertTrue(result["watching"])
        self.assertEqual(result["watched_account"], "player")
        self.assertGreater(result["seconds"], 0)

    def test_the_running_watches_are_visible_to_every_operator(self):
        # The feed is private to each operator. That a watch is running is not:
        # staff watching players is what other staff should be able to see.
        self._start(watcher_id=1)
        rows = self._panel().watches(self._ctx())["rows"]
        self.assertEqual(rows[0]["watcher"], "staffer")
        self.assertEqual(rows[0]["account"], "player")
        self.assertFalse(rows[0]["mine"])

    def test_the_current_operators_watch_is_marked_as_theirs(self):
        self._start(watcher_id=42)
        rows = self._panel().watches(self._ctx())["rows"]
        self.assertTrue(rows[0]["mine"])

    def test_unwatching_something_you_do_not_watch_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self._panel().unwatch(self._ctx(), sessid=1)
