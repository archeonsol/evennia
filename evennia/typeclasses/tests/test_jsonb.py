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

import gc
import os
import subprocess
import sys
import tempfile
import threading
import time
from copy import deepcopy
from unittest.mock import patch

from django.db import close_old_connections, transaction
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from twisted.internet.defer import Deferred

from evennia.typeclasses import jsonb_handler
from evennia.typeclasses.attributes import (
    AttributeHandler,
    discard_dirty_backends,
    flush_all_dirty,
)
from evennia.typeclasses.jsonb_handler import (
    AttributePostCommitError,
    AttributeUpdateConflict,
    AttributeUpdateIndeterminate,
    AttributeUpdateUnavailable,
    AttributeUpdateUsageError,
    FlushResult,
    JsonbAttributeBackend,
    JsonbWriteConflict,
    StaleAttributeValueError,
    _three_way_merge,
    force_flush,
    has_spooled_write,
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


class TestRowOwnedPersistence(BaseEvenniaTest):
    """Every handler for one model row shares one conflict-safe document."""

    def test_flush_all_dirty_merges_newer_database_edit(self):
        handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        handler.add("local", 1)
        remote = deepcopy(self.obj1.db_attrs or {})
        remote.setdefault("~", {}).setdefault("_d", {})["remote"] = 2
        type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)

        flush_all_dirty()

        document = (
            type(self.obj1).objects.filter(pk=self.obj1.pk).values_list("db_attrs", flat=True).get()
        )
        self.assertEqual(document["~"]["_d"], {"local": 1, "remote": 2})

    def test_attribute_and_nick_handlers_share_one_row_document(self):
        class NickHandler(AttributeHandler):
            _attrtype = "nick"

        attributes = AttributeHandler(self.obj1, JsonbAttributeBackend)
        nicks = NickHandler(self.obj1, JsonbAttributeBackend)
        attributes.add("color", "blue")
        nicks.add("l", "look")

        self.assertIs(attributes.backend._row_state, nicks.backend._row_state)
        flush_all_dirty()

        document = (
            type(self.obj1).objects.filter(pk=self.obj1.pk).values_list("db_attrs", flat=True).get()
        )
        self.assertEqual(document["~"]["_d"]["color"], "blue")
        self.assertEqual(document["_t_nick__~"]["_d"]["l"], "look")

    def test_dirty_row_survives_temporary_handler_collection(self):
        handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        state = handler.backend._row_state
        handler.add("durable", 7)
        del handler
        gc.collect()

        self.assertTrue(state.dirty)
        flush_all_dirty()
        document = (
            type(self.obj1).objects.filter(pk=self.obj1.pk).values_list("db_attrs", flat=True).get()
        )
        self.assertEqual(document["~"]["_d"]["durable"], 7)

    def test_conflicting_handlers_fail_closed_before_cleaning(self):
        first = AttributeHandler(self.obj1, JsonbAttributeBackend)
        first.add("value", 1)
        first.backend.flush_dirty()
        second = AttributeHandler(self.obj1, JsonbAttributeBackend)

        first.add("value", 2)
        remote = deepcopy(self.obj1.db_attrs)
        remote["~"]["_d"]["value"] = 3
        type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)

        result = first.backend.flush_dirty()

        self.assertFalse(result)
        self.assertIsInstance(result.error, JsonbWriteConflict)
        self.assertTrue(first.backend._dirty)
        self.assertTrue(second.backend._dirty)

    def test_same_path_proxy_from_second_handler_goes_stale(self):
        first = AttributeHandler(self.obj1, JsonbAttributeBackend)
        second = AttributeHandler(self.obj1, JsonbAttributeBackend)
        first.add("state", {"revision": 1})
        first.backend.flush_dirty()
        first_value = first.get("state")
        stale_value = second.get("state")

        first_value["revision"] = 2
        first.backend.flush_dirty()

        with self.assertRaises(StaleAttributeValueError):
            stale_value["revision"] = 99
        self.assertEqual(first.get("state")["revision"], 2)

    def test_unrelated_path_proxy_survives_ordinary_update(self):
        first = AttributeHandler(self.obj1, JsonbAttributeBackend)
        second = AttributeHandler(self.obj1, JsonbAttributeBackend)
        first.add("state", {"revision": 1})
        first.add("other", {"value": 1})
        first.backend.flush_dirty()
        other = second.get("other")

        first.get("state")["revision"] = 2
        other["value"] = 2
        first.backend.flush_dirty()

        self.assertEqual(first.get("other")["value"], 2)


class TestBlockingUpdate(BaseEvenniaTest):
    """Supported protected JSONB Attribute mutation surface."""

    def setUp(self):
        super().setUp()
        self.handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        self.handler.add("state", {"revision": 1, "items": []})
        self.handler.backend.flush_dirty()

    def _database_document(self):
        return (
            type(self.obj1).objects.filter(pk=self.obj1.pk).values_list("db_attrs", flat=True).get()
            or {}
        )

    def test_mutates_plain_working_value_and_returns_result(self):
        def mutate(attrs):
            first = attrs.get("state")
            second = attrs.get("STATE")
            self.assertIs(first, second)
            first["revision"] += 1
            first["items"].append("x")
            return first["revision"]

        result = self.handler.blocking_update(mutate)

        self.assertEqual(result, 2)
        self.assertEqual(self.handler.get("state"), {"revision": 2, "items": ["x"]})
        self.assertFalse(self.handler.backend._dirty)

    def test_locked_fields_are_current_and_read_only(self):
        type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_key="database-key")

        def mutate(attrs):
            self.assertEqual(attrs.row["db_key"], "database-key")
            with self.assertRaises(TypeError):
                attrs.row["db_key"] = "changed"

        self.handler.blocking_update(mutate, locked_fields=("db_key",))

    def test_mutable_locked_field_values_are_recursively_read_only(self):
        view = jsonb_handler.AttributeMutationView({}, {"mutable": {"nested": [1]}})

        self.assertEqual(view.row["mutable"]["nested"], (1,))
        with self.assertRaises(TypeError):
            view.row["mutable"]["nested"][0] = 2

    def test_clear_and_entries_normalize_category(self):
        def mutate(attrs):
            attrs.add("Flag", 1, category=" Combat ")
            self.assertEqual(
                [(entry.key, entry.category, entry.value) for entry in attrs.entries(" COMBAT ")],
                [("flag", "combat", 1)],
            )
            attrs.clear(" CoMbAt ")
            self.assertEqual(attrs.entries("combat"), ())

        self.handler.blocking_update(mutate)

    def test_callback_exception_rolls_back_but_preserves_prior_dirty_edit(self):
        self.handler.add("ordinary", 4)

        def mutate(attrs):
            attrs.add("protected", 9)
            raise ValueError("refuse")

        with self.assertRaisesRegex(ValueError, "refuse"):
            self.handler.blocking_update(mutate)

        self.assertEqual(self.handler.get("ordinary"), 4)
        self.assertIsNone(self.handler.get("protected"))
        self.assertTrue(self.handler.backend._dirty)

    def test_callback_rollback_rebases_prior_dirty_edit_over_remote_change(self):
        self.handler.add("ordinary", 4)
        remote = deepcopy(self.obj1.db_attrs)
        remote.setdefault("~", {}).setdefault("_d", {})["remote"] = 7
        type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)

        with self.assertRaisesRegex(ValueError, "refuse"):
            self.handler.blocking_update(lambda attrs: (_ for _ in ()).throw(ValueError("refuse")))

        self.assertEqual(self.handler.get("ordinary"), 4)
        self.assertEqual(self.handler.get("remote"), 7)
        self.assertTrue(self.handler.backend._dirty)

    def test_conflict_prevents_callback(self):
        self.handler.add("state", {"revision": 2})
        remote = deepcopy(self.obj1.db_attrs)
        remote["~"]["_d"]["state"] = {"revision": 3}
        type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)
        called = []

        with self.assertRaises(AttributeUpdateConflict):
            self.handler.blocking_update(lambda attrs: called.append(True))

        self.assertEqual(called, [])
        self.assertTrue(self.handler.backend._dirty)

    def test_pending_spool_refuses_before_callback(self):
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                self.handler.add("queued", True)
                self.handler.backend._flush_failures = self.handler.backend._FLUSH_FAIL_LIMIT - 1
                with patch.object(
                    jsonb_handler, "_write_locked_document", side_effect=Exception("db down")
                ):
                    self.handler.backend._do_flush()
                called = []

                with self.assertRaises(AttributeUpdateConflict):
                    self.handler.blocking_update(lambda attrs: called.append(True))

                self.assertEqual(called, [])
                self.assertTrue(has_spooled_write(self.obj1))

    def test_direct_live_write_rejected_without_partial_cache_change(self):
        before = deepcopy(self.handler.backend._l1)

        def mutate(attrs):
            with self.assertRaises(AttributeUpdateUsageError):
                self.handler.add("bypass", 1)
            attrs.add("safe", 2)

        self.handler.blocking_update(mutate)

        self.assertNotIn("bypass", self.handler.backend._l1.get("~", {}).get("_d", {}))
        self.assertEqual(before["~"]["_d"]["state"], {"revision": 1, "items": []})
        self.assertEqual(self.handler.get("safe"), 2)

    def test_captured_handler_read_is_rejected_in_favor_of_locked_view(self):
        remote = deepcopy(self._database_document())
        remote["~"]["_d"]["state"]["revision"] = 8
        type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)

        def mutate(attrs):
            with self.assertRaises(AttributeUpdateUsageError):
                self.handler.get("state")
            attrs.get("state")["revision"] += 1

        self.handler.blocking_update(mutate)

        self.assertEqual(self.handler.get("state")["revision"], 9)

    def test_direct_write_to_another_jsonb_row_is_rejected(self):
        other = AttributeHandler(self.obj2, JsonbAttributeBackend)

        def mutate(attrs):
            with self.assertRaises(AttributeUpdateUsageError):
                other.add("bypass", 1)
            attrs.add("safe", 2)

        self.handler.blocking_update(mutate)

        self.assertIsNone(other.get("bypass"))
        self.assertEqual(self.handler.get("safe"), 2)

    def test_direct_backend_flush_inside_callback_is_falsy_and_keeps_intent(self):
        other = AttributeHandler(self.obj2, JsonbAttributeBackend)
        other.add("waiting", 3)

        def mutate(attrs):
            result = other.backend.flush_dirty()
            self.assertFalse(result)
            self.assertIsInstance(result.error, AttributeUpdateUsageError)
            attrs.add("safe", 2)

        self.handler.blocking_update(mutate)

        self.assertTrue(other.backend._dirty)
        self.assertEqual(other.get("waiting"), 3)
        self.assertEqual(self.handler.get("safe"), 2)

    def test_first_handler_for_other_row_is_rejected_inside_callback(self):
        from evennia.objects.models import ObjectDB

        other = ObjectDB._base_manager.create(
            db_key="first-handler-callback",
            db_typeclass_path="evennia.objects.objects.DefaultObject",
            db_attrs={},
        )
        force_flush(other)
        key = jsonb_handler._row_key(other)
        jsonb_handler._ROW_STATES.pop(key, None)
        jsonb_handler._STRONG_ROW_STATES.pop(key, None)

        def mutate(attrs):
            with self.assertRaises(AttributeUpdateUsageError):
                AttributeHandler(other, JsonbAttributeBackend)
            attrs.add("safe", 2)

        self.handler.blocking_update(mutate)

        self.assertEqual(self.handler.get("safe"), 2)

    def test_first_handler_for_row_is_rejected_inside_outer_atomic(self):
        from evennia.objects.models import ObjectDB

        other = ObjectDB._base_manager.create(
            db_key="first-handler-atomic",
            db_typeclass_path="evennia.objects.objects.DefaultObject",
            db_attrs={},
        )
        force_flush(other)
        key = jsonb_handler._row_key(other)
        jsonb_handler._ROW_STATES.pop(key, None)
        jsonb_handler._STRONG_ROW_STATES.pop(key, None)

        with transaction.atomic():
            with self.assertRaises(AttributeUpdateUsageError):
                AttributeHandler(other, JsonbAttributeBackend)

    def test_model_save_hooks_may_initialize_handler_inside_outer_atomic(self):
        from evennia.objects.models import ObjectDB

        with transaction.atomic():
            other = ObjectDB._base_manager.create(
                db_key="handler-during-save",
                db_typeclass_path="evennia.objects.objects.DefaultObject",
                db_attrs={},
            )
            with self.assertRaises(AttributeUpdateUsageError):
                other.attributes.get("state")

        self.assertIsInstance(other.attributes.backend, JsonbAttributeBackend)

    def test_rolled_back_model_save_leaves_deferred_handler_unavailable(self):
        from evennia.objects.models import ObjectDB

        with self.assertRaisesRegex(RuntimeError, "rollback"):
            with transaction.atomic():
                other = ObjectDB._base_manager.create(
                    db_key="rolled-back-handler",
                    db_typeclass_path="evennia.objects.objects.DefaultObject",
                    db_attrs={},
                )
                raise RuntimeError("rollback")

        with self.assertRaises(AttributeUpdateUnavailable):
            other.attributes.get("state")

    def test_rolled_back_model_save_blocks_force_flush_and_reset(self):
        from evennia.objects.models import ObjectDB

        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                other = ObjectDB._base_manager.create(
                    db_key="rolled-back-coordinators",
                    db_typeclass_path="evennia.objects.objects.DefaultObject",
                    db_attrs={},
                )
                raise RuntimeError("rollback")

        result = force_flush(other)
        self.assertFalse(result)
        self.assertIsInstance(result.error, AttributeUpdateUnavailable)
        other.attributes.reset_cache()
        with self.assertRaises(AttributeUpdateUnavailable):
            other.attributes.get("state")

    def test_deferred_conflict_stays_quarantined_after_blocking_attempt(self):
        from evennia.objects.models import ObjectDB
        from evennia.typeclasses.attribute_context import model_save_context

        baseline = {"~": {"_d": {"same": 1}}}
        with transaction.atomic():
            other = ObjectDB._base_manager.create(
                db_key="deferred-conflict",
                db_typeclass_path="evennia.objects.objects.DefaultObject",
                db_attrs=baseline,
            )
            with model_save_context() as scope:
                other.attributes.add("same", 2)
            scope.commit()
            remote = {"~": {"_d": {"same": 3}}}
            ObjectDB._base_manager.filter(pk=other.pk).update(db_attrs=remote)

        with self.assertRaises(AttributeUpdateConflict):
            other.attributes.get("same")
        with self.assertRaises(AttributeUpdateUnavailable):
            other.attributes.blocking_update(lambda attrs: None)
        with self.assertRaises(AttributeUpdateUnavailable):
            other.attributes.get("same")

    def test_query_barrier_is_rejected_without_flushing_other_rows(self):
        other = AttributeHandler(self.obj2, JsonbAttributeBackend)
        self.handler.add("ordinary", 1)
        other.add("waiting", 3)

        def mutate(attrs):
            attrs.add("protected", 2)
            with self.assertRaises(AttributeUpdateUsageError):
                flush_all_dirty()

        self.handler.blocking_update(mutate)

        other_document = (
            type(self.obj2).objects.filter(pk=self.obj2.pk).values_list("db_attrs", flat=True).get()
        )
        self.assertNotIn("waiting", other_document.get("~", {}).get("_d", {}))
        self.assertTrue(other.backend._dirty)
        self.assertEqual(self.handler.get("ordinary"), 1)
        self.assertEqual(self.handler.get("protected"), 2)

    def test_nested_attempt_poisons_outer_even_when_caught(self):
        def mutate(attrs):
            with self.assertRaises(AttributeUpdateUsageError):
                self.handler.blocking_update(lambda inner: None)
            attrs.add("must_not_commit", True)

        with self.assertRaises(AttributeUpdateUsageError):
            self.handler.blocking_update(mutate)

        self.assertIsNone(self.handler.get("must_not_commit"))

    def test_outer_atomic_is_rejected(self):
        with transaction.atomic():
            with self.assertRaises(AttributeUpdateUsageError):
                self.handler.blocking_update(lambda attrs: None)

    def test_ordinary_flush_in_outer_atomic_keeps_volatile_intent(self):
        self.handler.add("ordinary", 4)

        with transaction.atomic():
            result = self.handler.backend.flush_dirty()

        self.assertFalse(result)
        self.assertIsInstance(result.error, AttributeUpdateUsageError)
        self.assertTrue(self.handler.backend._dirty)

    def test_force_flush_in_outer_atomic_keeps_volatile_intent(self):
        self.handler.add("ordinary", 4)

        with transaction.atomic():
            result = force_flush(self.obj1)

        self.assertFalse(result)
        self.assertIsInstance(result.error, AttributeUpdateUsageError)
        self.assertTrue(self.handler.backend._dirty)

    def test_cache_reset_in_outer_atomic_is_rejected(self):
        with transaction.atomic():
            with self.assertRaises(AttributeUpdateUsageError):
                self.handler.reset_cache()

    def test_spool_inspection_in_outer_atomic_is_rejected(self):
        with transaction.atomic():
            with self.assertRaises(AttributeUpdateUsageError):
                has_spooled_write(self.obj1)

    def test_global_spool_count_is_rejected_inside_callback(self):
        def mutate(attrs):
            with self.assertRaises(AttributeUpdateUsageError):
                spool_pending_count()
            attrs.add("safe", 2)

        self.handler.blocking_update(mutate)

        self.assertEqual(self.handler.get("safe"), 2)

    def test_lazy_return_is_rejected(self):
        with self.assertRaises(AttributeUpdateUsageError):
            self.handler.blocking_update(lambda attrs: Deferred())

    def test_queryset_return_is_rejected_before_it_can_escape(self):
        model = type(self.obj1)

        def mutate(attrs):
            attrs.add("must_not_commit", True)
            return model.objects.filter(pk=self.obj1.pk)

        with self.assertRaises(AttributeUpdateUsageError):
            self.handler.blocking_update(mutate)

        self.assertIsNone(self.handler.get("must_not_commit"))

    def test_async_after_commit_callback_is_rejected_before_commit(self):
        async def later():
            return None

        def mutate(attrs):
            attrs.add("must_not_commit", True)
            attrs.after_commit(later)

        with self.assertRaises(AttributeUpdateUsageError):
            self.handler.blocking_update(mutate)

        self.assertIsNone(self.handler.get("must_not_commit"))

    def test_generator_function_is_rejected_before_call(self):
        def mutate(attrs):
            yield attrs

        with self.assertRaises(AttributeUpdateUsageError):
            self.handler.blocking_update(mutate)

    def test_mutable_missing_default_does_not_create_attribute(self):
        def mutate(attrs):
            default = attrs.get("missing", default={})
            default["local"] = True

        self.handler.blocking_update(mutate)

        self.assertFalse(self.handler.has("missing"))

    def test_added_value_is_detached_and_mutated_through_get(self):
        original = {"revision": 1}

        def mutate(attrs):
            attrs.add("added", original)
            original["revision"] = 99
            attrs.get("added")["revision"] = 2

        self.handler.blocking_update(mutate)

        self.assertEqual(self.handler.get("added"), {"revision": 2})

    def test_direct_db_holder_write_is_rejected(self):
        def mutate(attrs):
            with self.assertRaises(AttributeUpdateUsageError):
                self.obj1.db.bypass = 1
            attrs.add("safe", 2)

        self.handler.blocking_update(mutate)

        self.assertIsNone(self.handler.get("bypass"))
        self.assertEqual(self.handler.get("safe"), 2)

    def test_mutator_database_reads_writes_saves_and_deletes_are_rejected(self):
        original_key = self.obj1.db_key
        model = type(self.obj1)
        operations = (
            lambda: model.objects.filter(pk=self.obj1.pk).exists(),
            lambda: model.objects.filter(pk=self.obj1.pk).update(db_key="bypass"),
            lambda: self.obj1.save(update_fields=("db_key",)),
            lambda: self.obj1.delete(),
        )
        for operation in operations:
            self.obj1.db_key = "captured-save"
            with self.assertRaises(AttributeUpdateUsageError):
                self.handler.blocking_update(lambda attrs, operation=operation: operation())
        self.obj1.db_key = original_key

        self.assertEqual(
            type(self.obj1).objects.values_list("db_key", flat=True).get(pk=self.obj1.pk),
            original_key,
        )
        self.assertIsNone(self.handler.get("safe"))

    def test_off_io_thread_is_rejected(self):
        errors = []

        def run():
            try:
                self.handler.blocking_update(lambda attrs: None)
            except Exception as err:
                errors.append(err)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], AttributeUpdateUnavailable)

    def test_prewarmed_handler_read_off_io_thread_is_rejected(self):
        self.assertEqual(self.handler.get("state")["revision"], 1)
        errors = []

        def run():
            try:
                self.handler.get("state")
            except Exception as err:
                errors.append(err)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], AttributeUpdateUnavailable)

    def test_cache_reset_off_io_thread_is_rejected(self):
        errors = []

        def run():
            try:
                self.handler.reset_cache()
            except Exception as err:
                errors.append(err)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], AttributeUpdateUnavailable)

    def test_spool_coordinators_off_io_thread_are_rejected(self):
        errors = []

        def run():
            for operation in (lambda: has_spooled_write(self.obj1), lambda: force_flush(self.obj1)):
                try:
                    operation()
                except Exception as err:
                    errors.append(err)

        thread = threading.Thread(target=run)
        thread.start()
        thread.join()

        self.assertEqual(len(errors), 2)
        self.assertTrue(all(isinstance(error, AttributeUpdateUnavailable) for error in errors))

    def test_after_commit_runs_after_cache_sync_and_aggregates_errors(self):
        observed = []

        def mutate(attrs):
            attrs.add("committed", 1)
            attrs.after_commit(lambda: observed.append(self.handler.get("committed")))
            attrs.after_commit(lambda: (_ for _ in ()).throw(RuntimeError("post")))
            attrs.after_commit(lambda: observed.append("continued"))

        with self.assertRaises(AttributePostCommitError) as caught:
            self.handler.blocking_update(mutate)

        self.assertTrue(caught.exception.committed)
        self.assertEqual(observed, [1, "continued"])

    def test_escaped_value_is_detached_after_commit(self):
        escaped = []

        def mutate(attrs):
            value = attrs.get("state")
            value["revision"] = 2
            escaped.append(value)

        self.handler.blocking_update(mutate)
        escaped[0]["revision"] = 99

        self.assertEqual(self.handler.get("state")["revision"], 2)

    def test_old_saver_proxy_cannot_overwrite_protected_result(self):
        stale = self.handler.get("state")
        self.handler.blocking_update(lambda attrs: attrs.get("state").update({"revision": 2}))

        with self.assertRaises(StaleAttributeValueError):
            stale["revision"] = 99

        self.assertEqual(self.handler.get("state")["revision"], 2)

    def test_noop_update_adopts_fresh_committed_document(self):
        remote = deepcopy(self.obj1.db_attrs)
        remote["~"]["_d"]["state"] = {"revision": 8, "items": []}
        type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)

        observed = self.handler.blocking_update(lambda attrs: attrs.get("state")["revision"])

        self.assertEqual(observed, 8)
        self.assertEqual(self.handler.get("state")["revision"], 8)
        self.assertFalse(self.handler.backend._dirty)

    def test_deleted_row_refuses_without_calling_mutator(self):
        type(self.obj1).objects.filter(pk=self.obj1.pk).delete()
        called = []

        with self.assertRaises(AttributeUpdateUnavailable):
            self.handler.blocking_update(lambda attrs: called.append(True))

        self.assertEqual(called, [])

    def test_idmapper_flush_retires_cached_object_after_external_row_delete(self):
        from evennia.objects.models import ObjectDB

        self.obj1.nattributes.add("keep-cached", True)
        self.obj1.attributes
        object_id = self.obj1.pk
        ObjectDB._base_manager.filter(pk=object_id).delete()
        self.obj1.pk = object_id
        ObjectDB.cache_instance(self.obj1)

        ObjectDB.flush_instance_cache()

        self.assertIsNone(ObjectDB.get_cached_instance(object_id))
        with self.assertRaises(AttributeUpdateUnavailable):
            self.handler.get("state")

    def test_idmapper_trim_is_zero_io_and_preserves_row_state(self):
        self.obj1.nattributes.add("keep-cached", True)
        proxy = self.handler.get("state")
        proxy["revision"] = 2
        state = self.handler.backend._row_state
        key = state.key
        before = {
            "documents": (
                deepcopy(state.committed_document),
                deepcopy(state.durable_document),
                deepcopy(state.visible_document),
                deepcopy(state.volatile_baseline),
            ),
            "flags": (
                state.dirty,
                state.pending_checked,
                state.pending_blocked,
                state.row_missing,
                state.sync_failed,
            ),
            "generations": (state.generation, deepcopy(state.path_generations)),
        }

        with (
            patch.object(
                jsonb_handler,
                "_spool_row_lock",
                side_effect=AssertionError("idmapper trim touched the spool lock"),
            ),
            patch.object(
                jsonb_handler,
                "_list_row_entries",
                side_effect=AssertionError("idmapper trim scanned the spool"),
            ),
            patch.object(
                jsonb_handler,
                "_locked_row_query",
                side_effect=AssertionError("idmapper trim locked the database row"),
            ),
            patch.object(
                jsonb_handler.transaction,
                "atomic",
                side_effect=AssertionError("idmapper trim opened a transaction"),
            ),
        ):
            self.assertFalse(self.obj1.at_idmapper_flush())

        self.assertEqual(
            (
                state.committed_document,
                state.durable_document,
                state.visible_document,
                state.volatile_baseline,
            ),
            before["documents"],
        )
        self.assertEqual(
            (
                state.dirty,
                state.pending_checked,
                state.pending_blocked,
                state.row_missing,
                state.sync_failed,
            ),
            before["flags"],
        )
        self.assertEqual(
            (state.generation, state.path_generations),
            before["generations"],
        )
        self.assertIs(jsonb_handler._STRONG_ROW_STATES[key], state)
        proxy["revision"] = 3
        self.assertEqual(self.handler.get("state")["revision"], 3)

    def test_idmapper_missing_row_without_handler_installs_tombstone(self):
        from evennia.objects.models import ObjectDB

        other = ObjectDB._base_manager.create(
            db_key="missing-without-handler",
            db_typeclass_path="evennia.objects.objects.DefaultObject",
            db_attrs={"~": {"_d": {"stale": 1}}},
        )
        other.nattributes.add("keep-cached", True)
        key = jsonb_handler._row_key(other)
        other.__dict__.pop("attributes", None)
        jsonb_handler._ROW_STATES.pop(key, None)
        jsonb_handler._STRONG_ROW_STATES.pop(key, None)
        self.assertNotIn("attributes", other.__dict__)
        object_id = other.pk
        ObjectDB._base_manager.filter(pk=object_id).delete()
        other.pk = object_id
        ObjectDB.cache_instance(other)

        ObjectDB.flush_instance_cache()

        self.assertIsNone(ObjectDB.get_cached_instance(object_id))
        with self.assertRaises(AttributeUpdateUnavailable):
            other.attributes.get("stale")

    def test_recreated_primary_key_gets_fresh_state_without_reviving_old_object(self):
        from evennia.objects.models import ObjectDB

        old = ObjectDB._base_manager.create(
            db_key="old-row",
            db_typeclass_path="evennia.objects.objects.DefaultObject",
            db_attrs={"~": {"_d": {"stale": 1}}},
        )
        old.nattributes.add("keep-cached", True)
        object_id = old.pk
        old.__dict__.pop("attributes", None)
        key = jsonb_handler._row_key(old)
        jsonb_handler._ROW_STATES.pop(key, None)
        jsonb_handler._STRONG_ROW_STATES.pop(key, None)
        ObjectDB._base_manager.filter(pk=object_id).delete()
        old.pk = object_id
        ObjectDB.__instance_cache__[object_id] = old
        ObjectDB.flush_instance_cache()

        replacement = ObjectDB(
            db_key="replacement-row",
            db_typeclass_path="evennia.objects.objects.DefaultObject",
            db_attrs={"~": {"_d": {"fresh": 2}}},
        )
        replacement.pk = object_id
        replacement.save(force_insert=True)

        self.assertEqual(replacement.attributes.get("fresh"), 2)
        with self.assertRaises(AttributeUpdateUnavailable):
            old.attributes.get("stale")

    def test_idmapper_flush_preserves_nattributes_on_nonmissing_unavailability(self):
        self.obj1.nattributes.add("keep-cached", True)
        public_handler = self.obj1.attributes

        with patch.object(
            public_handler,
            "idmapper_trim_cache",
            side_effect=AttributeUpdateUnavailable("temporary coordinator failure"),
        ):
            with self.assertRaises(AttributeUpdateUnavailable):
                self.obj1.at_idmapper_flush()

        self.assertTrue(self.obj1.nattributes.get("keep-cached"))

    def test_local_delete_retires_clean_row_state(self):
        from evennia.utils.idmapper.models import SharedMemoryModel

        state = self.handler.backend._row_state
        key = state.key

        SharedMemoryModel.delete(self.obj1)

        self.assertTrue(state.pending_blocked)
        self.assertNotIn(key, jsonb_handler._ROW_STATES)
        self.assertNotIn(key, jsonb_handler._STRONG_ROW_STATES)
        with self.assertRaises(AttributeUpdateUnavailable):
            self.handler.get("state")

    def test_local_delete_discards_dirty_row_state(self):
        from evennia.utils.idmapper.models import SharedMemoryModel

        state = self.handler.backend._row_state
        key = state.key
        self.handler.add("volatile", 3)
        self.assertIn(state, jsonb_handler.dirty_jsonb_row_states())

        SharedMemoryModel.delete(self.obj1)

        self.assertFalse(state.dirty)
        self.assertNotIn(state, jsonb_handler.dirty_jsonb_row_states())
        self.assertNotIn(key, jsonb_handler._STRONG_ROW_STATES)
        self.assertEqual(state.visible_document, {})

    def test_local_delete_in_outer_transaction_is_rejected(self):
        from evennia.utils.idmapper.models import SharedMemoryModel

        state = self.handler.backend._row_state
        with transaction.atomic():
            with self.assertRaises(AttributeUpdateUsageError):
                SharedMemoryModel.delete(self.obj1)

        self.assertFalse(state.pending_blocked)
        self.assertEqual(self.handler.get("state")["revision"], 1)

    def test_public_delete_rejects_outer_transaction_before_pre_delete_hook(self):
        with patch.object(self.obj1, "at_pre_delete") as pre_delete:
            with transaction.atomic():
                with self.assertRaises(AttributeUpdateUsageError):
                    self.obj1.delete()

        pre_delete.assert_not_called()

    def test_public_delete_rejects_pending_spool_before_pre_delete_hook(self):
        state = self.handler.backend._row_state
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                with jsonb_handler._spool_row_lock(state.key):
                    jsonb_handler._write_spool_payload(
                        state.key,
                        "DELTA",
                        state.durable_document,
                        state.visible_document,
                    )
                with patch.object(self.obj1, "at_pre_delete") as pre_delete:
                    with self.assertRaises(AttributeUpdateConflict):
                        self.obj1.delete()

                pre_delete.assert_not_called()

    def test_public_delete_rejects_off_io_thread_before_pre_delete_hook(self):
        errors = []
        with patch.object(self.obj1, "at_pre_delete") as pre_delete:

            def run():
                try:
                    self.obj1.delete()
                except Exception as err:
                    errors.append(err)

            thread = threading.Thread(target=run)
            thread.start()
            thread.join()

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], AttributeUpdateUnavailable)
        pre_delete.assert_not_called()

    def test_local_delete_rejects_pending_spool_without_live_state(self):
        from evennia.objects.models import ObjectDB
        from evennia.utils.idmapper.models import SharedMemoryModel

        other = ObjectDB._base_manager.create(
            db_key="pending-delete",
            db_typeclass_path="evennia.objects.objects.DefaultObject",
            db_attrs={},
        )
        force_flush(other)
        key = jsonb_handler._row_key(other)
        jsonb_handler._ROW_STATES.pop(key, None)
        jsonb_handler._STRONG_ROW_STATES.pop(key, None)

        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                with jsonb_handler._spool_row_lock(key):
                    jsonb_handler._write_spool_payload(
                        key, "PREPARED_BLOCKING", {}, {"protected": True}
                    )

                with self.assertRaises(AttributeUpdateConflict):
                    SharedMemoryModel.delete(other)

                self.assertTrue(ObjectDB._base_manager.filter(pk=other.pk).exists())

    def test_model_save_hook_attribute_edit_is_discarded_on_outer_rollback(self):
        from django.db.models.signals import post_save

        sender = self.obj1._meta.concrete_model
        dispatch_uid = "test-jsonb-model-save-rollback"

        def mutate_in_hook(sender, instance, **kwargs):
            if instance.pk == self.obj1.pk:
                instance.attributes.add("hook-local", 1)

        post_save.connect(mutate_in_hook, sender=sender, dispatch_uid=dispatch_uid)
        try:
            with transaction.atomic():
                self.obj1.db_key = "rolled-back-key"
                self.obj1.save(update_fields=("db_key",))
                transaction.set_rollback(True)
        finally:
            post_save.disconnect(sender=sender, dispatch_uid=dispatch_uid)

        self.assertIsNone(self.handler.get("hook-local"))
        self.assertFalse(self.handler.backend._dirty)

    def test_evennia_postsave_hook_attribute_edit_is_discarded_on_outer_rollback(self):
        def mutate_in_hook(instance, new):
            instance.attributes.add("evennia-hook-local", 1)

        model = type(self.obj1)
        with patch.object(model, "at_db_key_postsave", new=mutate_in_hook, create=True):
            with transaction.atomic():
                self.obj1.db_key = "rolled-back-evennia-hook"
                self.obj1.save(update_fields=("db_key",))
                transaction.set_rollback(True)

        self.assertIsNone(self.handler.get("evennia-hook-local"))
        self.assertFalse(self.handler.backend._dirty)

    def test_model_save_and_delete_reject_different_database_alias(self):
        from evennia.utils.idmapper.models import SharedMemoryModel

        with self.assertRaises(AttributeUpdateUsageError):
            self.obj1.save(using="other", update_fields=("db_key",))
        with self.assertRaises(AttributeUpdateUsageError):
            SharedMemoryModel.delete(self.obj1, using="other")
        with self.assertRaises(AttributeUpdateUsageError):
            self.obj1.save(False, False, "other", ("db_key",))
        with self.assertRaises(AttributeUpdateUsageError):
            SharedMemoryModel.delete(self.obj1, "other")

    def test_reset_cache_reads_current_committed_document(self):
        remote = deepcopy(self._database_document())
        remote["~"]["_d"]["remote-reset"] = 11
        type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)

        self.handler.reset_cache()

        self.assertEqual(self.handler.get("remote-reset"), 11)

    def test_ambiguous_commit_retains_nonreplayable_witness(self):
        real_atomic = transaction.atomic

        class AmbiguousCommit:
            def __init__(self, using):
                self.atomic = real_atomic(using=using)

            def __enter__(self):
                return self.atomic.__enter__()

            def __exit__(self, exc_type, exc_value, traceback):
                result = self.atomic.__exit__(exc_type, exc_value, traceback)
                if exc_type is None:
                    raise OSError("commit acknowledgement lost")
                return result

        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                with patch.object(
                    jsonb_handler.transaction,
                    "atomic",
                    side_effect=lambda using: AmbiguousCommit(using),
                ):
                    with self.assertRaises(AttributeUpdateIndeterminate):
                        self.handler.blocking_update(
                            lambda attrs: attrs.get("state").update({"revision": 2})
                        )

                self.assertEqual(spool_pending_count(), 1)
                self.assertEqual(self._database_document()["~"]["_d"]["state"]["revision"], 2)
                self.assertEqual(reclaim_spooled_writes(), 0)
                self.assertEqual(spool_pending_count(), 0)

    def test_full_model_save_cannot_overwrite_indeterminate_commit(self):
        real_atomic = transaction.atomic

        class AmbiguousCommit:
            def __init__(self, using):
                self.atomic = real_atomic(using=using)

            def __enter__(self):
                return self.atomic.__enter__()

            def __exit__(self, exc_type, exc_value, traceback):
                result = self.atomic.__exit__(exc_type, exc_value, traceback)
                if exc_type is None:
                    raise OSError("commit acknowledgement lost")
                return result

        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                with patch.object(
                    jsonb_handler.transaction,
                    "atomic",
                    side_effect=lambda using: AmbiguousCommit(using),
                ):
                    with self.assertRaises(AttributeUpdateIndeterminate):
                        self.handler.blocking_update(
                            lambda attrs: attrs.get("state").update({"revision": 2})
                        )

                self.obj1.db_key = "must-not-clobber"
                self.obj1.save()

                self.assertEqual(self._database_document()["~"]["_d"]["state"]["revision"], 2)

    def test_full_model_save_excludes_db_attrs_with_pending_delta(self):
        remote = deepcopy(self._database_document())
        remote["~"]["_d"]["remote"] = 7
        type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)
        state = self.handler.backend._row_state

        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                with jsonb_handler._spool_row_lock(state.key):
                    jsonb_handler._write_spool_payload(
                        state.key,
                        "DELTA",
                        state.durable_document,
                        state.visible_document,
                    )

                self.obj1.db_key = "safe-field-save"
                self.obj1.save()

                self.assertEqual(self._database_document()["~"]["_d"]["remote"], 7)

    def test_explicit_model_save_of_db_attrs_is_rejected(self):
        with self.assertRaises(AttributeUpdateUsageError):
            self.obj1.save(update_fields=("db_attrs",))

    def test_jsonb_backend_subclass_keeps_model_save_protection(self):
        class CustomJsonbBackend(JsonbAttributeBackend):
            pass

        remote = deepcopy(self._database_document())
        remote["~"]["_d"]["subclass-remote"] = 9
        type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)
        self.obj1.db_attrs = {}

        with override_settings(ATTRIBUTE_BACKEND_CLASS=CustomJsonbBackend):
            self.obj1.save()

        self.assertEqual(self._database_document()["~"]["_d"]["subclass-remote"], 9)

    def test_cache_sync_failure_blocks_reads_until_witness_reclaim(self):
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                with patch.object(
                    jsonb_handler, "_sync_live_backends", side_effect=RuntimeError("cache sync")
                ):
                    with self.assertRaises(AttributePostCommitError) as caught:
                        self.handler.blocking_update(
                            lambda attrs: attrs.get("state").update({"revision": 2})
                        )

                self.assertTrue(caught.exception.committed)
                self.assertTrue(self.handler.backend._row_state.pending_blocked)
                self.assertEqual(spool_pending_count(), 1)
                with self.assertRaises(AttributeUpdateUnavailable):
                    self.handler.get("state")

                self.assertEqual(reclaim_spooled_writes(), 0)
                self.assertEqual(self.handler.get("state")["revision"], 2)

    def test_noop_cache_sync_failure_recovers_without_witness(self):
        remote = deepcopy(self.obj1.db_attrs)
        remote["~"]["_d"]["state"] = {"revision": 8, "items": []}
        type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)

        with patch.object(
            jsonb_handler, "_sync_live_backends", side_effect=RuntimeError("cache sync")
        ):
            with self.assertRaises(AttributePostCommitError):
                self.handler.blocking_update(lambda attrs: attrs.get("state"))

        self.assertEqual(spool_pending_count(), 0)
        self.assertTrue(self.handler.backend._row_state.sync_failed)
        with self.assertRaises(AttributeUpdateUnavailable):
            self.handler.get("state")
        with patch.object(self.obj1, "at_pre_delete") as pre_delete:
            with self.assertRaises(AttributeUpdateUnavailable):
                self.obj1.delete()
        pre_delete.assert_not_called()

        self.handler.reset_cache()
        self.assertEqual(self.handler.get("state")["revision"], 8)

    def test_rollback_cache_sync_failure_recovers_staged_state(self):
        self.handler.add("ordinary", 4)

        with patch.object(
            jsonb_handler, "_sync_live_backends", side_effect=RuntimeError("cache sync")
        ):
            with self.assertRaises(AttributeUpdateUnavailable):
                self.handler.blocking_update(
                    lambda attrs: (_ for _ in ()).throw(ValueError("refuse"))
                )

        self.assertTrue(self.handler.backend._row_state.sync_failed)
        with self.assertRaises(AttributeUpdateUnavailable):
            self.handler.get("ordinary")

        self.handler.reset_cache()
        self.assertEqual(self.handler.get("ordinary"), 4)
        self.assertTrue(self.handler.backend._dirty)

    def test_deleted_row_tombstone_is_reused_by_new_handler(self):
        state = self.handler.backend._row_state
        obj_id = self.obj1.pk
        type(self.obj1).objects.filter(pk=self.obj1.pk).delete()

        with self.assertRaises(AttributeUpdateUnavailable):
            self.handler.blocking_update(lambda attrs: None)
        self.obj1.pk = obj_id
        self.obj1._state.adding = False
        with patch.object(self.obj1, "at_pre_delete") as pre_delete:
            with self.assertRaises(AttributeUpdateUnavailable):
                self.obj1.delete()
        pre_delete.assert_not_called()
        replacement = AttributeHandler(self.obj1, JsonbAttributeBackend)

        self.assertIs(replacement.backend._row_state, state)
        with self.assertRaises(AttributeUpdateUnavailable):
            replacement.get("state")
        with self.assertRaises(AttributeUpdateUnavailable):
            replacement.add("phantom", 1)

    def test_postcommit_callback_may_start_followup_update(self):
        def first(attrs):
            attrs.add(
                "first",
                1,
            )
            attrs.after_commit(
                lambda: self.handler.blocking_update(lambda followup: followup.add("second", 2))
            )

        self.handler.blocking_update(first)

        self.assertEqual(self.handler.get("first"), 1)
        self.assertEqual(self.handler.get("second"), 2)


class TestSqliteProtectedSerialization(TransactionTestCase):
    """Exercise the SQLite write-lock fallback through a separate connection."""

    def setUp(self):
        from evennia.objects.models import ObjectDB

        self.obj = ObjectDB._base_manager.create(
            db_key="protected-lock",
            db_typeclass_path="evennia.objects.objects.DefaultObject",
            db_attrs={},
        )
        self.handler = AttributeHandler(self.obj, JsonbAttributeBackend)
        self.handler.add("state", {"revision": 1})
        self.handler.backend.flush_dirty()

    def tearDown(self):
        discard_dirty_backends()
        close_old_connections()
        super().tearDown()

    def test_external_update_waits_for_protected_commit(self):
        from evennia.objects.models import ObjectDB

        started = threading.Event()
        finished = threading.Event()
        errors = []

        def contender():
            close_old_connections()
            try:
                started.set()
                ObjectDB._base_manager.filter(pk=self.obj.pk).update(db_key="contender-finished")
            except Exception as err:
                errors.append(err)
            finally:
                finished.set()
                close_old_connections()

        thread = threading.Thread(target=contender)

        def mutate(attrs):
            thread.start()
            self.assertTrue(started.wait(1))
            if finished.wait(0.1):
                self.assertTrue(errors, "external write completed before protected commit")
            attrs.get("state")["revision"] = 2

        self.handler.blocking_update(mutate)
        thread.join(5)

        self.assertFalse(thread.is_alive())
        db_key = ObjectDB._base_manager.values_list("db_key", flat=True).get(pk=self.obj.pk)
        if errors:
            self.assertIn("locked", str(errors[0]).lower())
            self.assertEqual(db_key, "protected-lock")
        else:
            self.assertEqual(db_key, "contender-finished")
        self.assertEqual(self.handler.get("state")["revision"], 2)


class TestDurableIntentOrdering(BaseEvenniaTest):
    """Replayable DELTAs and non-replayable protected witnesses stay distinct."""

    def setUp(self):
        super().setUp()
        self.handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        self.handler.add("state", {"revision": 1})
        self.handler.backend.flush_dirty()

    def _database_document(self):
        return (
            type(self.obj1).objects.filter(pk=self.obj1.pk).values_list("db_attrs", flat=True).get()
            or {}
        )

    def test_prepared_blocking_witness_never_replays_after_semantic_change(self):
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                state = self.handler.backend._row_state
                baseline = self._database_document()
                final = deepcopy(baseline)
                final["~"]["_d"]["state"] = {"revision": 2}
                with jsonb_handler._spool_row_lock(state.key):
                    jsonb_handler._write_spool_payload(
                        state.key, "PREPARED_BLOCKING", baseline, final
                    )
                changed = deepcopy(baseline)
                changed["~"]["_d"]["state"] = {"revision": 99}
                type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=changed)

                self.assertEqual(reclaim_spooled_writes(), 0)
                self.assertEqual(spool_pending_count(), 1)
                self.assertEqual(self._database_document()["~"]["_d"]["state"], {"revision": 99})

    def test_missing_row_retains_prepared_blocking_witness(self):
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                state = self.handler.backend._row_state
                baseline = self._database_document()
                final = deepcopy(baseline)
                final["~"]["_d"]["state"] = {"revision": 2}
                with jsonb_handler._spool_row_lock(state.key):
                    jsonb_handler._write_spool_payload(
                        state.key, "PREPARED_BLOCKING", baseline, final
                    )
                type(self.obj1).objects.filter(pk=self.obj1.pk).delete()

                self.assertEqual(reclaim_spooled_writes(), 0)
                self.assertEqual(spool_pending_count(), 1)

    def test_new_row_state_blocks_until_pending_delta_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                self.handler.add("queued", 4)
                self.handler.backend._flush_failures = self.handler.backend._FLUSH_FAIL_LIMIT - 1
                with patch.object(
                    jsonb_handler, "_write_locked_document", side_effect=Exception("db down")
                ):
                    self.handler.backend._do_flush()
                jsonb_handler.discard_jsonb_row_states()
                restarted = AttributeHandler(self.obj1, JsonbAttributeBackend)

                with self.assertRaises(AttributeUpdateUnavailable):
                    restarted.get("queued")
                with self.assertRaises(AttributeUpdateUnavailable):
                    restarted.add("later", 5)

                self.assertEqual(reclaim_spooled_writes(), 1)
                self.assertEqual(restarted.get("queued"), 4)

    def test_two_deltas_replay_fifo_and_keep_newer_visible_head(self):
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                self.handler.add("first", 1)
                self.handler.backend._flush_failures = self.handler.backend._FLUSH_FAIL_LIMIT - 1
                with patch.object(
                    jsonb_handler, "_write_locked_document", side_effect=Exception("db down")
                ):
                    first = force_flush(self.obj1)
                self.assertTrue(first.spooled)

                self.handler.add("second", 2)
                second = force_flush(self.obj1)
                self.assertTrue(second.spooled)
                self.assertEqual(spool_pending_count(), 2)

                remote = self._database_document()
                remote.setdefault("~", {}).setdefault("_d", {})["remote"] = 3
                type(self.obj1).objects.filter(pk=self.obj1.pk).update(db_attrs=remote)

                self.assertEqual(reclaim_spooled_writes(), 2)
                self.assertEqual(spool_pending_count(), 0)
                self.assertEqual(
                    self._database_document()["~"]["_d"],
                    {
                        "state": {"revision": 1},
                        "first": 1,
                        "second": 2,
                        "remote": 3,
                    },
                )
                self.assertEqual(self.handler.get("second"), 2)

    def test_spool_identity_includes_database_alias(self):
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                state = self.handler.backend._row_state
                default_key = state.key
                replica_key = ("replica", *state.key[1:])
                baseline = self._database_document()
                with jsonb_handler._spool_row_lock(default_key):
                    default_path = jsonb_handler._write_spool_payload(
                        default_key, "DELTA", baseline, baseline
                    )
                with jsonb_handler._spool_row_lock(replica_key):
                    replica_path = jsonb_handler._write_spool_payload(
                        replica_key, "DELTA", baseline, baseline
                    )

                self.assertNotEqual(default_path, replica_path)
                self.assertEqual(len(jsonb_handler._list_row_entries(default_key)), 1)
                self.assertEqual(len(jsonb_handler._list_row_entries(replica_key)), 1)

    def test_reclaim_in_outer_atomic_retains_delta(self):
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                self.handler.add("queued", 4)
                self.handler.backend._flush_failures = self.handler.backend._FLUSH_FAIL_LIMIT - 1
                with patch.object(
                    jsonb_handler, "_write_locked_document", side_effect=Exception("db down")
                ):
                    self.handler.backend._do_flush()

                with transaction.atomic():
                    with self.assertRaises(AttributeUpdateUsageError):
                        reclaim_spooled_writes()

                self.assertEqual(spool_pending_count(), 1)


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
        with patch.object(
            jsonb_handler, "_write_locked_document", side_effect=Exception("db down")
        ):
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
                with patch.object(
                    jsonb_handler, "_write_locked_document", side_effect=Exception("db down")
                ):
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
        with patch.object(
            jsonb_handler, "_write_locked_document", side_effect=Exception("db down")
        ):
            with patch.object(
                jsonb_handler, "_write_spool_payload", side_effect=Exception("disk down")
            ):
                result = self.backend._do_flush()
        self.assertTrue(self.backend._dirty)
        self.assertFalse(result)

    def test_reclaim_replays_spooled_write(self):
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                self.handler.add("x", 42)
                self.backend._flush_failures = self.backend._FLUSH_FAIL_LIMIT - 1
                with patch.object(
                    jsonb_handler, "_write_locked_document", side_effect=Exception("db down")
                ):
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
                with patch.object(
                    jsonb_handler, "_write_locked_document", side_effect=Exception("db down")
                ):
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
                with patch.object(
                    jsonb_handler, "_write_locked_document", side_effect=Exception("db down")
                ):
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

    def test_committed_flush_cache_sync_failure_blocks_until_reset(self):
        self.handler.add("x", 1)
        with patch.object(
            jsonb_handler, "_sync_live_backends", side_effect=RuntimeError("cache sync")
        ):
            result = self.backend.flush_dirty()

        self.assertTrue(result.ok)
        self.assertIsInstance(result.error, AttributePostCommitError)
        self.assertTrue(self.backend._row_state.sync_failed)
        with self.assertRaises(AttributeUpdateUnavailable):
            self.handler.get("x")

        self.handler.reset_cache()
        self.assertEqual(self.handler.get("x"), 1)
        self.assertFalse(self.backend._dirty)

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

    def test_protected_update_is_regular_attributes_only(self):
        with self.assertRaises(AttributeUpdateUnavailable):
            self.handler.blocking_update(lambda attrs: None)


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


# Held by a foreign process so the parent has to contend for the row lock. Uses
# the raw OS primitives rather than importing Evennia, so no Django setup is
# needed in the child.
_FOREIGN_LOCK_HOLDER = """
import os, sys, time

path, ready_path, hold_seconds = sys.argv[1], sys.argv[2], float(sys.argv[3])
with open(path, "a+b") as fh:
    fh.seek(0)
    if not fh.read(1):
        fh.write(b"0")
        fh.flush()
    fh.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    with open(ready_path, "w") as marker:
        marker.write("locked")
    time.sleep(hold_seconds)
"""


class TestSpoolRowLockContention(SimpleTestCase):
    """The row lock is a cross-process lock; a held lock must block, not raise."""

    def test_acquire_waits_for_lock_held_by_another_process(self):
        key = ("default", "objects.objectdb", 1)
        hold_seconds = 2.0
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                lock_dir = os.path.join(spool, ".locks")
                os.makedirs(lock_dir, exist_ok=True)
                digest = jsonb_handler.hashlib.sha256(repr(key).encode()).hexdigest()
                lock_path = os.path.join(lock_dir, f"{digest}.lock")
                ready_path = os.path.join(spool, "holder-ready")

                holder = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        _FOREIGN_LOCK_HOLDER,
                        lock_path,
                        ready_path,
                        str(hold_seconds),
                    ]
                )
                try:
                    deadline = time.monotonic() + 30
                    while not os.path.exists(ready_path):
                        if time.monotonic() > deadline:
                            self.fail("lock holder subprocess never acquired the lock")
                        time.sleep(0.05)

                    start = time.monotonic()
                    with jsonb_handler._spool_row_lock(key):
                        waited = time.monotonic() - start
                finally:
                    holder.wait(timeout=30)

                self.assertGreater(
                    waited,
                    0.5,
                    "acquire returned without waiting for the foreign lock to release",
                )
