"""
Tests for evennia.utils.bulk_tick.
"""

import tempfile
import threading
from copy import deepcopy
from unittest.mock import patch

from django.db import transaction
from django.test import override_settings

from evennia.objects.models import ObjectDB
from evennia.typeclasses.attributes import AttributeHandler
from evennia.typeclasses.jsonb_handler import (
    AttributeUpdateUnavailable,
    AttributeUpdateUsageError,
    JsonbAttributeBackend,
    StaleAttributeValueError,
    _spool_row_lock,
    _write_spool_payload,
)
from evennia.utils.bulk_tick import _NULL_CAT, BulkTickContext
from evennia.utils.test_resources import BaseEvenniaTest


class TestApplyUncached(BaseEvenniaTest):
    """Write-back for genuinely uncached objects (no idmapper entry).

    Must never instantiate ObjectDB from a partial row: partial instantiation
    of idmapper models is unsupported (construction reads deferred fields, and
    idmapper cannot refresh a deferred field).
    """

    def test_writes_back_without_instantiating(self):
        obj_id = self.obj1.id
        ObjectDB.__dbclass__.__instance_cache__.pop(obj_id, None)
        self.assertIsNone(ObjectDB.get_cached_instance(obj_id))

        ctx = BulkTickContext()
        ctx._uncached_ids.add(obj_id)
        written = ctx._apply_uncached({obj_id: {"bulk_tick_test_key": 42}})

        self.assertEqual(written, 1)
        # the write-back must not have pulled the object into the idmapper
        self.assertIsNone(ObjectDB.get_cached_instance(obj_id))
        attrs = ObjectDB.objects.filter(pk=obj_id).values_list("db_attrs", flat=True).first()
        self.assertEqual(attrs[_NULL_CAT]["_d"]["bulk_tick_test_key"], 42)

    def test_merges_into_existing_document(self):
        obj = self.obj1
        obj_id = obj.id
        obj.attributes.add("existing_key", "keepme")
        obj.attributes.backend.flush_dirty()
        ObjectDB.__dbclass__.__instance_cache__.pop(obj_id, None)

        ctx = BulkTickContext()
        ctx._uncached_ids.add(obj_id)
        written = ctx._apply_uncached({obj_id: {"bulk_tick_test_key": 7}})

        self.assertEqual(written, 1)
        attrs = ObjectDB.objects.filter(pk=obj_id).values_list("db_attrs", flat=True).first()
        self.assertEqual(attrs[_NULL_CAT]["_d"]["bulk_tick_test_key"], 7)
        self.assertIn("existing_key", attrs[_NULL_CAT]["_d"])

    def test_concurrent_database_change_is_not_overwritten(self):
        obj_id = self.obj1.id
        ObjectDB.__dbclass__.__instance_cache__.pop(obj_id, None)
        ctx = BulkTickContext()
        ctx._uncached_ids.add(obj_id)
        ctx._baseline[obj_id] = {}
        remote = deepcopy(self.obj1.db_attrs or {})
        remote.setdefault(_NULL_CAT, {}).setdefault("_d", {})["hp"] = 50
        ObjectDB.objects.filter(pk=obj_id).update(db_attrs=remote)

        written = ctx._apply_uncached({obj_id: {"hp": 110}})

        self.assertEqual(written, 0)
        self.assertEqual(ctx.conflicts, 1)
        attrs = ObjectDB.objects.filter(pk=obj_id).values_list("db_attrs", flat=True).get()
        self.assertEqual(attrs[_NULL_CAT]["_d"]["hp"], 50)

    def test_pending_spool_blocks_uncached_write(self):
        obj_id = self.obj1.id
        ObjectDB.__dbclass__.__instance_cache__.pop(obj_id, None)
        ctx = BulkTickContext()
        ctx._uncached_ids.add(obj_id)
        ctx._baseline[obj_id] = {}
        key = ("default", ObjectDB._meta.label_lower, obj_id)
        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                with _spool_row_lock(key):
                    _write_spool_payload(key, "DELTA", {}, {"queued": True})

                self.assertEqual(ctx._apply_uncached({obj_id: {"hp": 110}}), 0)
                self.assertEqual(ctx.conflicts, 1)


class TestBulkThreadGuard(BaseEvenniaTest):
    """Bulk gather/apply must keep explicit IO-thread enforcement under -O."""

    def test_uncached_gather_off_io_thread_is_rejected(self):
        errors = []

        def gather():
            try:
                BulkTickContext().gather_objectdb([], lambda obj_id, attrs: {"id": obj_id})
            except Exception as err:
                errors.append(err)

        thread = threading.Thread(target=gather)
        thread.start()
        thread.join()

        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], AttributeUpdateUnavailable)

    def test_gather_in_outer_atomic_is_rejected(self):
        with transaction.atomic():
            with self.assertRaises(AttributeUpdateUsageError):
                BulkTickContext().gather_objectdb(
                    [self.obj1.id], lambda obj_id, attrs: {"id": obj_id}
                )

    def test_cached_gather_reconciles_deferred_pending_state(self):
        backend = self.obj1.attributes.backend
        state = backend._row_state
        state.pending_checked = False
        key = state.key

        with tempfile.TemporaryDirectory() as spool:
            with override_settings(JSONB_WRITE_SPOOL_DIR=spool):
                with _spool_row_lock(key):
                    _write_spool_payload(key, "DELTA", {}, {"queued": True})

                with self.assertRaises(AttributeUpdateUnavailable):
                    BulkTickContext().gather_objectdb(
                        [self.obj1.id], lambda obj_id, attrs: {"id": obj_id}
                    )


@patch("evennia.utils.bulk_tick._assert_io_thread", lambda where: None)
class TestCompareAndSet(BaseEvenniaTest):
    """apply() must not overwrite a value changed since gather (CAS)."""

    def _snapshot(self, obj_id, l1):
        return {"id": obj_id, "hp": l1.get(_NULL_CAT, {}).get("_d", {}).get("hp")}

    def _section(self, obj):
        return obj.attributes.backend._l1.setdefault(_NULL_CAT, {}).setdefault("_d", {})

    def test_unchanged_value_is_written(self):
        self.obj1.attributes.add("hp", 100)
        ctx = BulkTickContext()
        ctx.gather_objectdb([self.obj1.id], self._snapshot)
        # no concurrent write; heartbeat computes hp -> 110
        patched = ctx.apply([{"id": self.obj1.id, "hp": 110}])
        self.assertEqual(patched, 1)
        self.assertEqual(ctx.conflicts, 0)
        self.assertEqual(self._section(self.obj1)["hp"], 110)

    def test_concurrent_write_is_not_clobbered(self):
        self.obj1.attributes.add("hp", 100)
        ctx = BulkTickContext()
        ctx.gather_objectdb([self.obj1.id], self._snapshot)

        # a combat hit lands between gather and apply, dropping hp to 50
        self._section(self.obj1)["hp"] = 50

        # the stale heartbeat result (based on hp=100 -> 110) must be skipped
        patched = ctx.apply([{"id": self.obj1.id, "hp": 110}])
        self.assertEqual(patched, 0)
        self.assertEqual(ctx.conflicts, 1)
        self.assertEqual(self._section(self.obj1)["hp"], 50)

    def test_same_value_aba_write_is_rejected_by_epoch(self):
        self.obj1.attributes.add("hp", 100)
        ctx = BulkTickContext()
        ctx.gather_objectdb([self.obj1.id], self._snapshot)

        self.obj1.attributes.add("hp", 50)
        self.obj1.attributes.add("hp", 100)

        self.assertEqual(ctx.apply([{"id": self.obj1.id, "hp": 110}]), 0)
        self.assertEqual(ctx.conflicts, 1)
        self.assertEqual(self.obj1.attributes.get("hp"), 100)

    def test_dirty_live_state_survives_idmapper_eviction(self):
        handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        handler.add("hp", 100)
        ObjectDB.__dbclass__.__instance_cache__.pop(self.obj1.id, None)

        ctx = BulkTickContext()
        ctx.gather_objectdb([self.obj1.id], self._snapshot)
        self.assertEqual(ctx.apply([{"id": self.obj1.id, "hp": 110}]), 1)

        self.assertEqual(handler.get("hp"), 110)
        self.assertTrue(handler.backend._dirty)

    def test_bulk_apply_invalidates_old_same_path_proxy(self):
        handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        handler.add("state", {"revision": 1})
        stale = handler.get("state")
        ctx = BulkTickContext()
        ctx.gather_objectdb(
            [self.obj1.id],
            lambda obj_id, l1: {
                "id": obj_id,
                "state": l1[_NULL_CAT]["_d"]["state"],
            },
        )

        self.assertEqual(ctx.apply([{"id": self.obj1.id, "state": {"revision": 2}}]), 1)
        with self.assertRaises(StaleAttributeValueError):
            stale["revision"] = 99

    def test_bulk_apply_inside_protected_mutator_has_no_partial_effect(self):
        handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        handler.add("hp", 100)
        handler.backend.flush_dirty()
        ctx = BulkTickContext()
        ctx.gather_objectdb([self.obj1.id], self._snapshot)

        def mutate(attrs):
            with self.assertRaises(AttributeUpdateUsageError):
                ctx.apply([{"id": self.obj1.id, "hp": 110}])
            self.assertEqual(attrs.get("hp"), 100)

        handler.blocking_update(mutate)

        self.assertEqual(handler.get("hp"), 100)

    def test_bulk_apply_in_outer_atomic_has_no_partial_effect(self):
        handler = AttributeHandler(self.obj1, JsonbAttributeBackend)
        handler.add("hp", 100)
        handler.backend.flush_dirty()
        ctx = BulkTickContext()
        ctx.gather_objectdb([self.obj1.id], self._snapshot)

        with transaction.atomic():
            with self.assertRaises(AttributeUpdateUsageError):
                ctx.apply([{"id": self.obj1.id, "hp": 110}])

        self.assertEqual(handler.get("hp"), 100)
        self.assertFalse(handler.backend._dirty)
