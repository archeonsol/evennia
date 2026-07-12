"""
Tests for evennia.utils.bulk_tick.
"""

from unittest.mock import patch

from evennia.objects.models import ObjectDB
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
