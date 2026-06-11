"""
Tests for evennia.utils.bulk_tick.
"""

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
