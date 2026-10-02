"""
Why the managed collection policy needs full collections: evicted instances are cyclic garbage.

An instance and its handlers (attributes, tags, ndb and the rest) refer to one another,
so dropping the idmapper's reference to an evicted instance does not free it. Only the
cycle collector does, and under the managed policy a young pass never looks at an
instance that has been cached for more than a few seconds.
"""

import gc
import weakref

from evennia.objects.objects import DefaultObject
from evennia.utils import create, gc_policy
from evennia.utils.test_resources import EvenniaTestCase


class TestEvictedInstances(EvenniaTestCase):
    def setUp(self):
        super().setUp()
        self._was_enabled = gc.isenabled()
        gc.collect()
        gc.disable()
        self.addCleanup(lambda: gc.enable() if self._was_enabled else None)

    def _evicted(self, count):
        """Create ``count`` objects, use them as the game does, then drop them from the idmapper."""
        refs = []
        for number in range(count):
            obj = create.create_object(DefaultObject, key=f"evictee-{number}")
            obj.db.notes = {"tags": list(range(20)), "text": "x" * 30}
            obj.tags.add("evictee", category="test")
            obj.ndb.cache = {"n": number}
            obj.attributes.get("notes")
            refs.append(weakref.ref(obj))
            type(obj).__dbclass__.__instance_cache__.pop(obj.pk, None)
        return refs

    def test_reference_counting_does_not_free_an_evicted_instance(self):
        refs = self._evicted(20)

        self.assertEqual(sum(ref() is not None for ref in refs), 20)

    def test_a_deep_clean_frees_them_all(self):
        refs = self._evicted(20)

        result = gc_policy.deep_clean("manual")

        self.assertEqual(sum(ref() is not None for ref in refs), 0)
        self.assertGreater(result.collected, 20, "dozens of containers per instance")
