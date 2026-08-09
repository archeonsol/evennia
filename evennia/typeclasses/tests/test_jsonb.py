"""
Tests for the JSONB attribute backend (Workstream B).

Covers:
  - jsonb_util: to_jsonb/from_jsonb round-trips for all value types
  - JsonbAttributeBackend: create, get, update, delete, clear, batch_add
  - _Saver* write-back through JsonbAttribute.value setter
  - force_flush: no-op on old backend, flushes on JSONB backend
  - Document layout: null category stored as "~", named categories preserved
  - Lockstring and strvalue (nick) storage
  - _do_flush retry/failure behavior
  - query_all with attrtype sections
  - pending_count() return values
  - unique pk counter per backend instance
"""

import tempfile
from copy import deepcopy
from unittest.mock import patch

from django.test import override_settings

from evennia.typeclasses.jsonb_handler import (
    FlushResult,
    JsonbAttributeBackend,
    JsonbWriteConflict,
    _three_way_merge,
    force_flush,
    reclaim_spooled_writes,
    spool_pending_count,
)
from evennia.typeclasses.jsonb_util import _SENTINEL, from_jsonb, to_jsonb
from evennia.utils.test_resources import BaseEvenniaTest

# ---------------------------------------------------------------------------
# Serialization round-trips
# ---------------------------------------------------------------------------


class TestToJsonb(BaseEvenniaTest):
    def _roundtrip(self, value):
        encoded = to_jsonb(value)
        return from_jsonb(encoded)

    def test_int(self):
        self.assertEqual(self._roundtrip(42), 42)

    def test_float(self):
        self.assertAlmostEqual(self._roundtrip(3.14), 3.14)

    def test_bool_true(self):
        self.assertIs(self._roundtrip(True), True)

    def test_bool_false(self):
        self.assertIs(self._roundtrip(False), False)

    def test_none(self):
        self.assertIsNone(self._roundtrip(None))

    def test_string(self):
        self.assertEqual(self._roundtrip("hello"), "hello")

    def test_empty_string(self):
        self.assertEqual(self._roundtrip(""), "")

    def test_list(self):
        self.assertEqual(self._roundtrip([1, 2, 3]), [1, 2, 3])

    def test_nested_dict(self):
        val = {"a": {"b": 1}, "c": [1, 2]}
        self.assertEqual(self._roundtrip(val), val)

    def test_tuple_uses_sentinel(self):
        # tuples are not JSON-safe; should be encoded with sentinel
        encoded = to_jsonb((1, 2, 3))
        self.assertIsInstance(encoded, str)
        self.assertTrue(encoded.startswith(_SENTINEL))

    def test_set_uses_sentinel(self):
        encoded = to_jsonb({1, 2, 3})
        self.assertTrue(encoded.startswith(_SENTINEL))

    def test_tuple_roundtrip(self):
        self.assertEqual(self._roundtrip((1, 2, 3)), (1, 2, 3))

    def test_arbitrary_object_roundtrip(self):
        import datetime

        d = datetime.date(2026, 1, 1)
        self.assertEqual(self._roundtrip(d), d)

    def test_large_int_stored_directly(self):
        big = 10**25
        encoded = to_jsonb(big)
        # Python's json module handles arbitrary-precision ints exactly; no sentinel needed.
        self.assertEqual(encoded, big)
        self.assertEqual(self._roundtrip(big), big)


# ---------------------------------------------------------------------------
# Backend: basic CRUD
# ---------------------------------------------------------------------------


class TestJsonbBackendCRUD(BaseEvenniaTest):
    """Tests using the real JSONB backend wired to obj1 (an ObjectDB)."""

    def setUp(self):
        super().setUp()
        from evennia.typeclasses.attributes import AttributeHandler

        self.handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        self.backend = self.handler.backend

    def test_create_and_get_returns_value(self):
        self.handler.add("hp", 100)
        self.assertEqual(self.handler.get("hp"), 100)

    def test_get_missing_returns_default(self):
        self.assertIsNone(self.handler.get("nonexistent"))

    def test_get_missing_returns_supplied_default(self):
        self.assertEqual(self.handler.get("nonexistent", default=42), 42)

    def test_update_overwrites(self):
        self.handler.add("hp", 100)
        self.handler.add("hp", 50)
        self.assertEqual(self.handler.get("hp"), 50)

    def test_delete_removes(self):
        self.handler.add("hp", 100)
        self.handler.remove("hp")
        self.assertIsNone(self.handler.get("hp"))

    def test_has_present(self):
        self.handler.add("hp", 100)
        self.assertTrue(self.handler.has("hp"))

    def test_has_absent(self):
        self.assertFalse(self.handler.has("missing_key"))

    def test_all_returns_attrs(self):
        self.handler.add("hp", 100)
        self.handler.add("stamina", 50)
        keys = {a.key for a in self.handler.all()}
        self.assertIn("hp", keys)
        self.assertIn("stamina", keys)

    def test_clear_removes_all(self):
        self.handler.add("hp", 100)
        self.handler.add("stamina", 50)
        self.handler.clear()
        self.assertEqual(self.handler.all(), [])

    def test_clear_by_category(self):
        self.handler.add("hp", 100, category="combat")
        self.handler.add("name", "Alice")
        self.handler.clear(category="combat")
        self.assertIsNone(self.handler.get("hp", category="combat"))
        self.assertEqual(self.handler.get("name"), "Alice")

    def test_category_stored_separately(self):
        self.handler.add("val", 1)
        self.handler.add("val", 2, category="cat")
        self.assertEqual(self.handler.get("val"), 1)
        self.assertEqual(self.handler.get("val", category="cat"), 2)

    def test_null_category_stored_under_tilde(self):
        self.handler.add("x", 1)
        self.assertIn("~", self.backend._l1)

    def test_named_category_stored_under_its_key(self):
        self.handler.add("x", 1, category="traits")
        self.assertIn("traits", self.backend._l1)

    def test_lockstring_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not executable"):
            self.handler.add("secret", 42, lockstring="read:perm(Admin)")
        self.assertIsNone(self.handler.get("secret"))

    def test_marks_dirty_on_write(self):
        self.assertFalse(self.backend._dirty)
        self.handler.add("x", 1)
        self.assertTrue(self.backend._dirty)

    def test_flush_dirty_saves_and_clears_flag(self):
        self.handler.add("x", 99)
        self.backend.flush_dirty()
        self.assertFalse(self.backend._dirty)
        # Value was persisted to the model field.
        self.assertIn("~", self.obj1.db_attrs)

    def test_force_flush_saves_immediately(self):
        self.handler.add("x", 7)
        self.backend._do_flush()
        self.assertFalse(self.backend._dirty)
        self.assertIn("~", self.obj1.db_attrs)

    def test_batch_add(self):
        self.handler.batch_add(
            ("a", 1),
            ("b", 2),
            ("c", 3),
        )
        self.assertEqual(self.handler.get("a"), 1)
        self.assertEqual(self.handler.get("b"), 2)
        self.assertEqual(self.handler.get("c"), 3)


# ---------------------------------------------------------------------------
# _Saver* write-back
# ---------------------------------------------------------------------------


class TestJsonbWriteBack(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        from evennia.typeclasses.attributes import AttributeHandler

        self.handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        self.backend = self.handler.backend

    def test_dict_mutation_writes_back_to_l1(self):
        self.handler.add("stats", {"hp": 100, "mp": 50})
        stats = self.handler.get("stats")
        # Mutate in-place — _SaverDict should propagate to L1.
        stats["hp"] = 999
        encoded = self.backend._l1["~"]["_d"]["stats"]
        # Re-decode and check the mutation landed.
        plain = from_jsonb(encoded)
        self.assertEqual(plain["hp"], 999)

    def test_nested_dict_mutation_writes_back(self):
        self.handler.add("data", {"outer": {"inner": 1}})
        data = self.handler.get("data")
        data["outer"]["inner"] = 42
        encoded = self.backend._l1["~"]["_d"]["data"]
        plain = from_jsonb(encoded)
        self.assertEqual(plain["outer"]["inner"], 42)

    def test_list_mutation_writes_back(self):
        self.handler.add("items", [1, 2, 3])
        items = self.handler.get("items")
        items.append(4)
        encoded = self.backend._l1["~"]["_d"]["items"]
        plain = from_jsonb(encoded)
        self.assertIn(4, plain)


# ---------------------------------------------------------------------------
# Document persistence round-trip
# ---------------------------------------------------------------------------


class TestJsonbPersistence(BaseEvenniaTest):
    """Reload the L1 dict from the saved db_attrs and confirm values survive."""

    def setUp(self):
        super().setUp()
        from evennia.typeclasses.attributes import AttributeHandler

        self.handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        self.backend = self.handler.backend

    def _reload_backend(self):
        """Flush, then construct a fresh backend reading from db_attrs."""
        self.backend.flush_dirty()
        from evennia.typeclasses.attributes import AttributeHandler

        fresh = AttributeHandler(self.obj1, JsonbAttributeBackend)
        return fresh

    def test_int_persists(self):
        self.handler.add("level", 5)
        h2 = self._reload_backend()
        self.assertEqual(h2.get("level"), 5)

    def test_string_persists(self):
        self.handler.add("name", "Alice")
        h2 = self._reload_backend()
        self.assertEqual(h2.get("name"), "Alice")

    def test_dict_persists(self):
        self.handler.add("stats", {"hp": 100})
        h2 = self._reload_backend()
        self.assertEqual(h2.get("stats"), {"hp": 100})

    def test_category_persists(self):
        self.handler.add("power", 9000, category="traits")
        h2 = self._reload_backend()
        self.assertEqual(h2.get("power", category="traits"), 9000)

    def test_none_persists(self):
        self.handler.add("empty", None)
        h2 = self._reload_backend()
        self.assertIsNone(h2.get("empty", default="MISSING"))
        # Confirm the key exists (get returned None, not the default).
        self.assertTrue(h2.has("empty"))

    def test_tuple_persists(self):
        self.handler.add("pair", (1, 2))
        h2 = self._reload_backend()
        self.assertEqual(h2.get("pair"), (1, 2))


# ---------------------------------------------------------------------------
# force_flush no-op on old backend
# ---------------------------------------------------------------------------


class TestForceFlushNoop(BaseEvenniaTest):
    def test_force_flush_noop_on_jsonb_backend(self):
        self.obj1.attributes.add("x", 1)
        force_flush(self.obj1)  # must not raise


# ---------------------------------------------------------------------------
# _do_flush retry and give-up behavior
# ---------------------------------------------------------------------------


class TestFlushRetry(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        from evennia.typeclasses.attributes import AttributeHandler

        self.handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        self.backend = self.handler.backend

    def test_retry_increments_failure_counter(self):
        self.handler.add("x", 1)
        with patch.object(self.obj1, "save", side_effect=Exception("db down")):
            result = self.backend._do_flush()
        self.assertEqual(self.backend._flush_failures, 1)
        self.assertTrue(self.backend._dirty)
        # not durable yet: still only in the volatile L1 dict
        self.assertFalse(result)
        self.assertFalse(result.ok)
        self.assertFalse(result.spooled)

    def test_at_limit_diverts_to_durable_spool(self):
        # At the retry limit the write must NOT be dropped: it is diverted to
        # the durable on-disk spool and only then marked clean.
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                self.handler.add("x", 1)
                self.backend._flush_failures = self.backend._FLUSH_FAIL_LIMIT - 1
                with patch.object(self.obj1, "save", side_effect=Exception("db down")):
                    result = self.backend._do_flush()
                # dirty cleared (durable now), and the write is on disk
                self.assertFalse(self.backend._dirty)
                self.assertTrue(result.spooled)
                self.assertTrue(result)  # durable
                self.assertEqual(spool_pending_count(), 1)

    def test_spool_failure_keeps_dirty(self):
        # If even the spool write fails, the backend stays dirty (never lose).
        self.handler.add("x", 1)
        self.backend._flush_failures = self.backend._FLUSH_FAIL_LIMIT - 1
        with patch.object(self.obj1, "save", side_effect=Exception("db down")):
            with patch("evennia.typeclasses.jsonb_handler._spool_write", return_value=False):
                result = self.backend._do_flush()
        self.assertTrue(self.backend._dirty)
        self.assertFalse(result)

    def test_reclaim_replays_spooled_write(self):
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                self.handler.add("x", 42)
                self.backend._flush_failures = self.backend._FLUSH_FAIL_LIMIT - 1
                with patch.object(self.obj1, "save", side_effect=Exception("db down")):
                    self.backend._do_flush()
                self.assertEqual(spool_pending_count(), 1)
                # reclamation writes the doc to the DB and clears the spool
                reclaimed = reclaim_spooled_writes()
                self.assertEqual(reclaimed, 1)
                self.assertEqual(spool_pending_count(), 0)
                self.obj1.refresh_from_db()
                self.assertEqual(self.obj1.db_attrs["~"]["_d"]["x"], 42)

    def test_reclaim_merges_nonconflicting_newer_database_write(self):
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                self.handler.add("x", 42)
                self.backend._flush_failures = self.backend._FLUSH_FAIL_LIMIT - 1
                with patch.object(self.obj1, "save", side_effect=Exception("db down")):
                    self.backend._do_flush()
                remote = deepcopy(self.obj1.db_attrs or {})
                remote.setdefault("~", {}).setdefault("_d", {})["y"] = 7
                type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)

                self.assertEqual(reclaim_spooled_writes(), 1)
                document = (
                    type(self.obj1)
                    .objects.filter(pk=self.obj1.pk)
                    .values_list("db_attrs", flat=True)
                    .get()
                )
                self.assertEqual(document["~"]["_d"], {"x": 42, "y": 7})

    def test_reclaim_leaves_conflicting_spool_for_recovery(self):
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                self.handler.add("x", 42)
                self.backend._flush_failures = self.backend._FLUSH_FAIL_LIMIT - 1
                with patch.object(self.obj1, "save", side_effect=Exception("db down")):
                    self.backend._do_flush()
                remote = deepcopy(self.obj1.db_attrs or {})
                remote.setdefault("~", {}).setdefault("_d", {})["x"] = 99
                type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)

                self.assertEqual(reclaim_spooled_writes(), 0)
                self.assertEqual(spool_pending_count(), 1)
                document = (
                    type(self.obj1)
                    .objects.filter(pk=self.obj1.pk)
                    .values_list("db_attrs", flat=True)
                    .get()
                )
                self.assertEqual(document["~"]["_d"]["x"], 99)

    def test_success_resets_failure_counter(self):
        self.handler.add("x", 1)
        self.backend._flush_failures = 3
        result = self.backend.flush_dirty()
        self.assertEqual(self.backend._flush_failures, 0)
        self.assertFalse(self.backend._dirty)
        self.assertTrue(result.ok)

    def test_force_flush_returns_durable_result(self):
        self.handler.add("x", 1)
        result = force_flush(self.obj1)
        self.assertIsInstance(result, FlushResult)
        self.assertTrue(result)
        # nothing dirty now -> still a durable no-op result
        self.assertTrue(force_flush(self.obj1))


class TestThreeWayMerge(BaseEvenniaTest):
    def test_local_and_remote_deletions_preserve_missing_keys(self):
        baseline = {"a": 1, "b": 2}
        self.assertEqual(_three_way_merge(baseline, {"b": 2}, baseline), {"b": 2})
        self.assertEqual(_three_way_merge(baseline, baseline, {"a": 1}), {"a": 1})

    def test_conflicting_delete_refuses(self):
        with self.assertRaises(JsonbWriteConflict):
            _three_way_merge({"a": 1}, {}, {"a": 2})


# ---------------------------------------------------------------------------
# query_all with attrtype sections
# ---------------------------------------------------------------------------


class TestAttrtypeQueryAll(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        from evennia.typeclasses.attributes import AttributeHandler

        class NickHandler(AttributeHandler):
            _attrtype = "nick"

        self.handler = NickHandler(self.obj1, JsonbAttributeBackend)
        self.backend = self.handler.backend

    def test_attrtype_section_returned_by_query_all(self):
        self.handler.add("greet", "hi")
        attrs = self.backend.query_all()
        keys = {a.db_key for a in attrs}
        self.assertIn("greet", keys)

    def test_regular_section_excluded_from_attrtype_backend(self):
        # Plant a regular (non-nick) attr directly in _l1.
        self.backend._l1.setdefault("~", {}).setdefault("_d", {})["intruder"] = "val"
        attrs = self.backend.query_all()
        keys = {a.db_key for a in attrs}
        self.assertNotIn("intruder", keys)

    def test_attrtype_category_preserved(self):
        self.handler.add("greet", "hi", category="en")
        attrs = self.backend.query_all()
        cats = {a.db_category for a in attrs}
        self.assertIn("en", cats)


# ---------------------------------------------------------------------------
# pending_count()
# ---------------------------------------------------------------------------


class TestPendingCount(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        from evennia.typeclasses.attributes import AttributeHandler

        self.handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        self.backend = self.handler.backend

    def test_zero_when_clean(self):
        self.assertEqual(self.backend.pending_count(), 0)

    def test_one_when_dirty(self):
        self.handler.add("x", 1)
        self.assertEqual(self.backend.pending_count(), 1)

    def test_zero_after_flush(self):
        self.handler.add("x", 1)
        self.backend.flush_dirty()
        self.assertEqual(self.backend.pending_count(), 0)


# ---------------------------------------------------------------------------
# pk counter uniqueness
# ---------------------------------------------------------------------------


class TestPkCounter(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        from evennia.typeclasses.attributes import AttributeHandler

        self.handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        self.backend = self.handler.backend

    def test_pk_unique_per_attr(self):
        self.handler.add("a", 1)
        self.handler.add("b", 2)
        attrs = self.backend.query_all()
        pks = [a.pk for a in attrs]
        self.assertEqual(len(pks), len(set(pks)), "duplicate pks across attrs")

    def test_pk_not_hash_collision_prone(self):
        # hash() can collide for (key=None, cat=None) vs (key=None, cat="")
        # Counter avoids this entirely.
        for i in range(50):
            self.handler.add(f"attr_{i}", i)
        attrs = self.backend.query_all()
        pks = [a.pk for a in attrs]
        self.assertEqual(len(pks), len(set(pks)))
