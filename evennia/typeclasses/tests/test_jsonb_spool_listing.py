"""How often a flush asks the filesystem about the durable spool.

A row's queue is checked under the row's lock, because another process may have
queued an intent for it: that check cannot be shared between rows. What can be
saved is everything around it. A flush used to list the spool twice for each dirty
row (once under the lock, once more to decide whether to keep the row in memory),
the async flush listed it again for each row when it adopted the results, and every
lock probed for its directories first. These tests count those calls.
"""

import os
import shutil
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from evennia.typeclasses import jsonb_handler
from evennia.typeclasses.attributes import flush_all_dirty
from evennia.utils.create import create_object
from evennia.utils.test_resources import BaseEvenniaTest

_ROWS = 6


class _SpoolCalls:
    """Counts what the code asks the filesystem about the spool directory.

    ``listings`` are listings of the spool itself. ``probes`` are the checks that
    the directories exist: ``isdir`` and ``makedirs`` on the spool or below it.
    """

    def __init__(self, spool):
        self.spool = os.path.normcase(os.path.abspath(spool))
        self.listings = 0
        self.probes = 0
        self._patches = []

    def _is_spool(self, path):
        try:
            return os.path.normcase(os.path.abspath(os.fspath(path))) == self.spool
        except TypeError:
            return False

    def _under_spool(self, path):
        try:
            return os.path.normcase(os.path.abspath(os.fspath(path))).startswith(self.spool)
        except TypeError:
            return False

    def __enter__(self):
        real_listdir, real_isdir, real_makedirs = os.listdir, os.path.isdir, os.makedirs

        def listdir(path=".", *args, **kwargs):
            if self._is_spool(path):
                self.listings += 1
            return real_listdir(path, *args, **kwargs)

        def isdir(path):
            if self._under_spool(path):
                self.probes += 1
            return real_isdir(path)

        def makedirs(name, *args, **kwargs):
            if self._under_spool(name):
                self.probes += 1
            return real_makedirs(name, *args, **kwargs)

        for target, replacement in (
            ("os.listdir", listdir),
            ("os.path.isdir", isdir),
            ("os.makedirs", makedirs),
        ):
            patcher = patch(target, replacement)
            patcher.start()
            self._patches.append(patcher)
        return self

    def __exit__(self, *exc):
        for patcher in reversed(self._patches):
            patcher.stop()
        self._patches.clear()


class _SpoolCase(BaseEvenniaTest):
    """A spool of its own, and rows that are dirty and ready to flush."""

    def setUp(self):
        super().setUp()
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.spool = os.path.join(folder.name, "spool")
        override = override_settings(JSONB_WRITE_SPOOL_DIR=self.spool)
        override.enable()
        self.addCleanup(override.disable)
        # Whatever the fixtures left dirty goes first, so a flush below covers only these rows.
        flush_all_dirty()

    def _dirty_rows(self, count=_ROWS):
        """Objects whose attribute document has changed and not yet been written."""
        objs = []
        for number in range(count):
            obj = create_object("evennia.objects.objects.DefaultObject", key=f"spool row {number}")
            objs.append(obj)
        flush_all_dirty()
        for number, obj in enumerate(objs):
            obj.attributes.add("level", number)
        return objs

    def _state(self, obj):
        return obj.attributes.backend._row_state


class TestFlushListsTheSpoolOncePerRow(_SpoolCase):
    def test_a_flush_lists_the_spool_once_for_each_dirty_row(self):
        objs = self._dirty_rows()

        with _SpoolCalls(self.spool) as calls:
            stats = flush_all_dirty()

        self.assertEqual((stats["total"], stats["failed"]), (len(objs), 0))
        self.assertEqual(calls.listings, len(objs))
        for obj in objs:
            self.assertFalse(self._state(obj).dirty)

    def test_a_flushed_row_is_not_kept_in_memory(self):
        obj = self._dirty_rows(1)[0]
        state = self._state(obj)
        self.assertIn(state.key, jsonb_handler._STRONG_ROW_STATES)

        flush_all_dirty()

        self.assertNotIn(state.key, jsonb_handler._STRONG_ROW_STATES)

    def test_a_row_with_a_queued_intent_still_queues_behind_it(self):
        obj = self._dirty_rows(1)[0]
        state = self._state(obj)
        jsonb_handler._write_spool_payload(
            state.key, "DELTA", state.durable_document, state.durable_document
        )

        stats = flush_all_dirty()

        self.assertEqual((stats["spooled"], stats["failed"]), (1, 0))
        self.assertEqual(len(jsonb_handler._list_row_entries(state.key)), 2)
        self.assertIn(state.key, jsonb_handler._STRONG_ROW_STATES)


class TestAsyncFlushListsTheSpool(_SpoolCase):
    def _run_async(self):
        snapshots, failures = jsonb_handler._prepare_async_row_flushes()
        self.assertEqual(failures, 0)
        outcomes = jsonb_handler._persist_row_snapshots_worker(snapshots)
        return snapshots, outcomes

    def test_adopting_the_results_lists_the_spool_once_for_all_the_rows(self):
        objs = self._dirty_rows()

        snapshots, outcomes = self._run_async()
        with _SpoolCalls(self.spool) as calls:
            tallies = jsonb_handler._adopt_async_row_flushes(snapshots, outcomes)

        self.assertEqual(tallies, {"flushed": len(objs), "spooled": 0, "failed": 0})
        self.assertEqual(calls.listings, 1)

    def test_a_whole_async_flush_lists_once_for_each_row_and_once_to_adopt(self):
        objs = self._dirty_rows()

        with _SpoolCalls(self.spool) as calls:
            snapshots, outcomes = self._run_async()
            jsonb_handler._adopt_async_row_flushes(snapshots, outcomes)

        self.assertEqual(calls.listings, len(objs) + 1)

    def test_an_intent_queued_after_the_worker_keeps_the_row_in_memory(self):
        obj = self._dirty_rows(1)[0]
        state = self._state(obj)
        snapshots, outcomes = self._run_async()
        # Another process queues an intent for the row after the worker let go of its lock.
        jsonb_handler._write_spool_payload(
            state.key, "DELTA", state.durable_document, state.durable_document
        )

        jsonb_handler._adopt_async_row_flushes(snapshots, outcomes)

        self.assertIn(state.key, jsonb_handler._STRONG_ROW_STATES)

    def test_a_row_with_nothing_queued_is_let_go_when_adopted(self):
        obj = self._dirty_rows(1)[0]
        state = self._state(obj)
        snapshots, outcomes = self._run_async()

        jsonb_handler._adopt_async_row_flushes(snapshots, outcomes)

        self.assertNotIn(state.key, jsonb_handler._STRONG_ROW_STATES)

    def test_adopting_nothing_lists_nothing(self):
        with _SpoolCalls(self.spool) as calls:
            jsonb_handler._adopt_async_row_flushes((), ())

        self.assertEqual(calls.listings, 0)


class TestLoadingARowAsksLittle(_SpoolCase):
    def test_a_row_state_is_made_with_one_listing_and_no_directory_probe(self):
        obj = create_object("evennia.objects.objects.DefaultObject", key="spool load")
        # The first lock makes the directories; the rest find them.
        with jsonb_handler._spool_row_lock(self._state(obj).key):
            pass
        jsonb_handler._ROW_STATES.pop(self._state(obj).key, None)

        with _SpoolCalls(self.spool) as calls:
            state = jsonb_handler._get_row_state(obj)

        self.assertIsNotNone(state)
        self.assertEqual((calls.listings, calls.probes), (1, 0))


class TestRowLock(SimpleTestCase):
    key = ("default", "objects.objectdb", 1)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.spool = os.path.join(folder.name, "spool")
        override = override_settings(JSONB_WRITE_SPOOL_DIR=self.spool)
        override.enable()
        self.addCleanup(override.disable)

    def test_taking_a_lock_does_not_probe_for_its_directories(self):
        with jsonb_handler._spool_row_lock(self.key):
            pass

        with _SpoolCalls(self.spool) as calls:
            with jsonb_handler._spool_row_lock(self.key):
                pass

        self.assertEqual(calls.probes, 0)

    def test_the_first_lock_makes_the_directories(self):
        self.assertFalse(os.path.exists(self.spool))

        with jsonb_handler._spool_row_lock(self.key):
            self.assertTrue(os.path.isdir(os.path.join(self.spool, ".locks")))

    def test_a_spool_that_is_cleared_out_while_running_is_made_again(self):
        with jsonb_handler._spool_row_lock(self.key):
            pass
        shutil.rmtree(self.spool)

        with jsonb_handler._spool_row_lock(self.key):
            self.assertTrue(os.path.isdir(os.path.join(self.spool, ".locks")))

    def test_every_lock_of_a_row_uses_the_same_file(self):
        with jsonb_handler._spool_row_lock(self.key):
            pass
        locks = os.listdir(os.path.join(self.spool, ".locks"))
        with jsonb_handler._spool_row_lock(self.key):
            pass

        self.assertEqual(os.listdir(os.path.join(self.spool, ".locks")), locks)
        self.assertEqual(len(locks), 1)


class TestListingARowsEntries(SimpleTestCase):
    key = ("default", "objects.objectdb", 7)

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = folder.name
        self.spool = os.path.join(folder.name, "spool")
        override = override_settings(JSONB_WRITE_SPOOL_DIR=self.spool)
        override.enable()
        self.addCleanup(override.disable)

    def test_no_spool_means_no_entries(self):
        self.assertEqual(jsonb_handler._list_row_entries(self.key), [])

    def test_a_spool_that_is_not_a_directory_means_no_entries(self):
        with open(self.spool, "w", encoding="utf-8") as handle:
            handle.write("not a directory")

        self.assertEqual(jsonb_handler._list_row_entries(self.key), [])
        self.assertEqual(jsonb_handler._list_row_entries(self.key, strict=False), [])

    def test_an_empty_spool_means_no_entries_and_reads_nothing(self):
        os.makedirs(self.spool)

        with patch.object(jsonb_handler, "_read_spool_payload") as read:
            self.assertEqual(jsonb_handler._list_row_entries(self.key), [])

        read.assert_not_called()

    def test_only_this_rows_files_are_read(self):
        os.makedirs(self.spool)
        other = ("default", "objects.objectdb", 8)
        jsonb_handler._write_spool_payload(other, "DELTA", {}, {"a": 1})
        mine = jsonb_handler._write_spool_payload(self.key, "DELTA", {}, {"b": 2})

        with patch.object(
            jsonb_handler, "_read_spool_payload", wraps=jsonb_handler._read_spool_payload
        ) as read:
            entries = jsonb_handler._list_row_entries(self.key)

        self.assertEqual([path for _sequence, path, _payload in entries], [mine])
        read.assert_called_once()

    def test_a_file_named_the_old_way_still_belongs_to_its_row(self):
        os.makedirs(self.spool)
        legacy = os.path.join(self.spool, f"{self.key[1]}.{self.key[2]}.json")
        with open(legacy, "w", encoding="utf-8") as handle:
            handle.write(
                '{"model": "objects.objectdb", "pk": 7, "baseline": {}, "db_attrs": {"c": 3}}'
            )

        entries = jsonb_handler._list_row_entries(self.key)

        self.assertEqual([path for _sequence, path, _payload in entries], [legacy])

    def test_a_listing_that_fails_for_another_reason_still_raises_when_strict(self):
        os.makedirs(self.spool)

        with patch("os.listdir", side_effect=PermissionError("denied")):
            with self.assertRaises(PermissionError):
                jsonb_handler._list_row_entries(self.key)
            self.assertEqual(jsonb_handler._list_row_entries(self.key, strict=False), [])


class TestMarkClean(_SpoolCase):
    def test_a_known_answer_saves_the_listing(self):
        state = self._state(self._dirty_rows(1)[0])

        with patch.object(jsonb_handler, "_row_has_pending_entries") as looked:
            state.mark_clean(pending=False)

        looked.assert_not_called()
        self.assertNotIn(state.key, jsonb_handler._STRONG_ROW_STATES)

    def test_a_pending_intent_keeps_the_row(self):
        state = self._state(self._dirty_rows(1)[0])

        with patch.object(jsonb_handler, "_row_has_pending_entries") as looked:
            state.mark_clean(pending=True)

        looked.assert_not_called()
        self.assertIn(state.key, jsonb_handler._STRONG_ROW_STATES)

    def test_without_an_answer_it_looks(self):
        state = self._state(self._dirty_rows(1)[0])

        with patch.object(jsonb_handler, "_row_has_pending_entries", return_value=False) as looked:
            state.mark_clean()

        looked.assert_called_once()
        self.assertNotIn(state.key, jsonb_handler._STRONG_ROW_STATES)
        self.assertFalse(state.dirty)
