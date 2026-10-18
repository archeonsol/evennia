"""
Unit tests for typeclass base system

"""

from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from mock import patch
from parameterized import parameterized

from evennia.objects.models import ObjectDB
from evennia.objects.objects import DefaultObject
from evennia.utils.idmapper.models import flush_cache
from evennia.utils.test_resources import BaseEvenniaTest, EvenniaTestCase

# ------------------------------------------------------------
# Manager tests
# ------------------------------------------------------------


class DictSubclass(dict):
    pass


class TestAttributes(BaseEvenniaTest):
    evennia_fixtures = {"obj1"}

    def test_attrhandler(self):
        key = "testattr"
        value = "test attr value "
        self.obj1.attributes.add(key, value)
        self.assertEqual(self.obj1.attributes.get(key), value)
        self.obj1.db.testattr = value
        self.assertEqual(self.obj1.db.testattr, value)

        # "plain" subclasses
        value = DictSubclass({"fo": "foo", "bar": "bar"})
        self.obj1.db.testattr = value
        self.assertEqual(self.obj1.db.testattr, value)

        self.obj1.db.testattr["fo"] = "foo2"
        value.update({"fo": "foo2"})
        self.assertEqual(self.obj1.db.testattr, value)
        self.assertEqual(self.obj1.attributes.get("testattr"), value)

        # nested subclasses
        value = DictSubclass({"nested": True, "deep": DictSubclass({"fo": "foo", "bar": "bar"})})
        self.obj1.db.testattr = value

        self.obj1.db.testattr["deep"]["fo"] = "nemo"
        value["deep"].update({"fo": "nemo"})
        self.assertEqual(self.obj1.db.testattr, value)
        self.assertEqual(self.obj1.attributes.get("testattr"), value)

    @override_settings(TYPECLASS_AGGRESSIVE_CACHE=False)
    def test_attrhandler_nocache(self):
        key = "testattr"
        value = "test attr value "
        self.obj1.attributes.add(key, value)
        self.assertFalse(self.obj1.attributes.backend._cache)

        self.assertEqual(self.obj1.attributes.get(key), value)
        self.obj1.db.testattr = value
        self.assertEqual(self.obj1.db.testattr, value)
        self.assertFalse(self.obj1.attributes.backend._cache)

        # "plain" subclasses
        value = DictSubclass({"fo": "foo", "bar": "bar"})
        self.obj1.db.testattr = value
        self.assertEqual(self.obj1.db.testattr, value)

        self.obj1.db.testattr["fo"] = "foo2"
        value.update({"fo": "foo2"})
        self.assertEqual(self.obj1.db.testattr, value)
        self.assertEqual(self.obj1.attributes.get("testattr"), value)
        self.assertFalse(self.obj1.attributes.backend._cache)

        # nested subclasses
        value = DictSubclass({"nested": True, "deep": DictSubclass({"fo": "foo", "bar": "bar"})})
        self.obj1.db.testattr = value

        self.obj1.db.testattr["deep"]["fo"] = "nemo"
        value["deep"].update({"fo": "nemo"})
        self.assertEqual(self.obj1.db.testattr, value)
        self.assertEqual(self.obj1.attributes.get("testattr"), value)
        self.assertFalse(self.obj1.attributes.backend._cache)

    def test_weird_text_save(self):
        "test 'weird' text type (different in py2 vs py3)"
        from django.utils.safestring import SafeText

        key = "test attr 2"
        value = SafeText("test attr value 2")
        self.obj1.attributes.add(key, value)
        self.assertEqual(self.obj1.attributes.get(key), value)

    def test_batch_add(self):
        attrs = [
            ("key1", "value1"),
            ("key2", "value2", "category2"),
            ("key3", "value3"),
            ("key4", "value4", "category4", "attrread:id(1)", False),
        ]
        with self.assertRaisesRegex(ValueError, "not executable"):
            self.obj1.attributes.batch_add(*attrs)
        for key in ("key1", "key2", "key3", "key4"):
            self.assertFalse(self.obj1.attributes.has(key))

    def test_value_vs_strvalue(self):
        self.obj1.attributes.add("test", "one")
        self.assertEqual(self.obj1.attributes.get("test"), "one")
        self.assertEqual(self.obj1.attributes.get("test", strattr=True), None)
        # switch to strattr
        self.obj1.attributes.add("test", "two", strattr=True)
        self.assertEqual(self.obj1.attributes.get("test"), None)
        self.assertEqual(self.obj1.attributes.get("test", strattr=True), "two")


class TestTypedObjectManager(BaseEvenniaTest):
    evennia_fixtures = {"obj1", "obj2"}

    def _manager(self, methodname, *args, **kwargs):
        return list(getattr(self.obj1.__class__.objects, methodname)(*args, **kwargs))

    def test_get_by_tag_no_category(self):
        self.obj1.tags.add("tag1")
        self.obj1.tags.add("tag2")
        self.obj1.tags.add("tag2c")
        self.obj2.tags.add("tag2")
        self.obj2.tags.add("tag2a")
        self.obj2.tags.add("tag2b")
        self.obj2.tags.add("tag3 with spaces")
        self.obj2.tags.add("tag4")
        self.obj2.tags.add("tag2c")
        self.assertEqual(self._manager("get_by_tag", "tag1"), [self.obj1])
        self.assertEqual(set(self._manager("get_by_tag", "tag2")), set([self.obj1, self.obj2]))
        self.assertEqual(self._manager("get_by_tag", "tag2a"), [self.obj2])
        self.assertEqual(self._manager("get_by_tag", "tag3 with spaces"), [self.obj2])
        self.assertEqual(self._manager("get_by_tag", ["tag2a", "tag2b"]), [self.obj2])
        self.assertEqual(self._manager("get_by_tag", ["tag2a", "tag1"]), [])
        self.assertEqual(self._manager("get_by_tag", ["tag2a", "tag4", "tag2c"]), [self.obj2])

    def test_get_by_tag_and_category(self):
        self.obj1.tags.add("tag5", "category1")
        self.obj1.tags.add("tag6")
        self.obj1.tags.add("tag7", "category1")
        self.obj1.tags.add("tag6", "category3")
        self.obj1.tags.add("tag7", "category4")
        self.obj2.tags.add("tag5", "category1")
        self.obj2.tags.add("tag5", "category2")
        self.obj2.tags.add("tag6", "category3")
        self.obj2.tags.add("tag7", "category1")
        self.obj2.tags.add("tag7", "category5")
        self.obj1.tags.add("tag8", "category6")
        self.obj2.tags.add("tag9", "category6")

        self.assertEqual(self._manager("get_by_tag", "tag5", "category1"), [self.obj1, self.obj2])
        self.assertEqual(self._manager("get_by_tag", "tag6", "category1"), [])
        self.assertEqual(self._manager("get_by_tag", "tag6", "category3"), [self.obj1, self.obj2])
        self.assertEqual(
            self._manager("get_by_tag", ["tag5", "tag6"], ["category1", "category3"]),
            [self.obj1, self.obj2],
        )
        self.assertEqual(
            self._manager("get_by_tag", ["tag5", "tag7"], "category1"),
            [self.obj1, self.obj2],
        )
        self.assertEqual(self._manager("get_by_tag", category="category1"), [self.obj1, self.obj2])
        self.assertEqual(self._manager("get_by_tag", category="category2"), [self.obj2])
        self.assertEqual(
            self._manager("get_by_tag", category=["category1", "category3"]),
            [self.obj1, self.obj2],
        )
        self.assertEqual(
            self._manager("get_by_tag", category=["category1", "category2"]),
            [self.obj1, self.obj2],
        )
        self.assertEqual(self._manager("get_by_tag", category=["category5", "category4"]), [])
        self.assertEqual(self._manager("get_by_tag", category="category1"), [self.obj1, self.obj2])
        self.assertEqual(self._manager("get_by_tag", category="category6"), [self.obj1, self.obj2])

    def test_get_tag_with_all(self):
        self.obj1.tags.add("tagA", "categoryA")
        self.assertEqual(
            self._manager("get_by_tag", ["tagA", "tagB"], ["categoryA", "categoryB"], match="all"),
            [],
        )

    def test_get_tag_with_any(self):
        self.obj1.tags.add("tagA", "categoryA")
        self.assertEqual(
            self._manager("get_by_tag", ["tagA", "tagB"], ["categoryA", "categoryB"], match="any"),
            [self.obj1],
        )

    def test_get_tag_with_any_including_nones(self):
        self.obj1.tags.add("tagA", "categoryA")
        self.assertEqual(
            self._manager(
                "get_by_tag", ["tagA", "tagB"], ["categoryA", "categoryB", None], match="any"
            ),
            [self.obj1],
        )

    def test_get_tag_withnomatch(self):
        self.obj1.tags.add("tagC", "categoryC")
        self.assertEqual(
            self._manager("get_by_tag", ["tagA", "tagB"], ["categoryA", "categoryB"], match="any"),
            [],
        )

    def test_get_by_tag_with_integer_key_and_category(self):
        self.obj1.tags.add(123, 456)
        self.assertEqual(self._manager("get_by_tag", 123, 456), [self.obj1])
        self.assertEqual(self._manager("get_by_tag", "123", "456"), [self.obj1])

    def test_batch_add(self):
        tags = ["tag1", ("tag2", "category2"), "tag3", ("tag4", "category4", "data4")]
        self.obj1.tags.batch_add(*tags)
        self.assertEqual(self.obj1.tags.get("tag1"), "tag1")
        tagobj = self.obj1.tags.get("tag4", category="category4", return_tagobj=True)
        self.assertEqual(tagobj.db_key, "tag4")
        self.assertEqual(tagobj.db_category, "category4")
        self.assertEqual(tagobj.db_data, "data4")

    def test_batch_add_reuses_existing_tags(self):
        self.obj2.tags.add("tag1", "category1", data="old data")

        self.obj1.tags.batch_add(
            ("tag1", "category1", "new data"),
            ("tag2", "category1", "new data"),
        )

        self.assertEqual(
            self.obj1.tags.all(return_key_and_category=True),
            [("tag1", "category1"), ("tag2", "category1")],
        )
        self.assertEqual(self.obj2.tags.get("tag1", category="category1"), "tag1")

        tagobj = self.obj1.tags.get("tag1", category="category1", return_tagobj=True)
        self.assertEqual(tagobj.db_data, "new data")

    def test_batch_add_deduplicates_input(self):
        self.obj1.tags.batch_add("tag1", "tag1", ("tag1", None), ("tag2", "category2"))

        self.assertEqual(
            self.obj1.tags.all(return_key_and_category=True),
            [("tag1", None), ("tag2", "category2")],
        )


# setting up testing typeclass with child- and parent class
class TestSearchManagerTypeclassParent(DefaultObject):
    pass


class TestSearchManagerTypeclass(TestSearchManagerTypeclassParent):
    pass


class TestSearchManagerTypeclassChild(TestSearchManagerTypeclass):
    pass


class TestSearchTypeclassFamily(EvenniaTestCase):
    """
    Test the manager method for searching for inheriting typeclasses.

    """

    def setUp(self):
        self.obj_parent, _ = TestSearchManagerTypeclassParent.create(key="obj_parent")
        self.obj1, _ = TestSearchManagerTypeclass.create(key="obj1")
        self.obj2, _ = TestSearchManagerTypeclass.create(key="obj2")
        self.obj_child, _ = TestSearchManagerTypeclassChild.create(key="obj_child")

    def test_typeclass_search__inputs(self):
        """Test basic functionality"""

        res1 = self.obj1.__class__.objects.typeclass_search(self.obj1.__class__)
        res2 = self.obj1.__class__.objects.typeclass_search(
            "evennia.typeclasses.tests.test_typeclasses.TestSearchManagerTypeclass"
        )
        self.assertEqual(set(res1), {self.obj1, self.obj2})
        self.assertEqual(set(res2), {self.obj1, self.obj2})

    def test_typeclass_search__children_and_parents(self):
        """Test getting parents/child classes"""

        # just the objects of this typeclass
        res1 = self.obj1.__class__.objects.typeclass_search(self.obj1.__class__)
        res2 = self.obj2.__class__.objects.typeclass_search(self.obj2.__class__)

        # these objects + children
        res3 = self.obj1.__class__.objects.typeclass_search(
            self.obj1.__class__, include_children=True
        )
        # these objects + parents
        res4 = self.obj1.__class__.objects.typeclass_search(
            self.obj1.__class__, include_parents=True
        )
        # these objects + parents + children
        res5 = self.obj1.__class__.objects.typeclass_search(
            self.obj1.__class__, include_children=True, include_parents=True
        )

        self.assertEqual(set(res1), {self.obj1, self.obj2})
        self.assertEqual(set(res2), {self.obj1, self.obj2})
        self.assertEqual(set(res3), {self.obj1, self.obj2, self.obj_child})
        self.assertEqual(set(res4), {self.obj1, self.obj2, self.obj_parent})
        self.assertEqual(set(res5), {self.obj1, self.obj2, self.obj_child, self.obj_parent})

    def test_typeclass_search__nested(self):
        """Test several levels deep searches"""
        # check all children of the parent
        res1 = self.obj1.__class__.objects.typeclass_search(
            self.obj_parent.__class__, include_children=True
        )
        # check all parents of the child
        res2 = self.obj1.__class__.objects.typeclass_search(
            self.obj_child.__class__, include_parents=True
        )

        self.assertEqual(set(res1), {self.obj_parent, self.obj1, self.obj2, self.obj_child})
        self.assertEqual(set(res2), {self.obj_parent, self.obj1, self.obj2, self.obj_child})


class TestTypeclassManagerGet(EvenniaTestCase):
    """``get(pk=...)`` on a typeclass manager reads the idmapper first."""

    def setUp(self):
        self.obj, _ = TestSearchManagerTypeclass.create(key="obj")
        self.child, _ = TestSearchManagerTypeclassChild.create(key="child")

    def test_cached_pk_costs_no_query(self):
        for kwargs in ({"pk": self.obj.pk}, {"id": self.obj.pk}, {"pk__exact": self.obj.pk}):
            with self.assertNumQueries(0):
                self.assertIs(TestSearchManagerTypeclass.objects.get(**kwargs), self.obj)

    def test_evicted_pk_is_queried(self):
        pk = self.obj.pk
        self.obj.flush_from_cache(force=True)
        reloaded = TestSearchManagerTypeclass.objects.get(pk=pk)
        self.assertIsNot(reloaded, self.obj)
        self.assertEqual(reloaded.pk, pk)

    def test_cached_pk_of_another_typeclass_is_not_found(self):
        with self.assertRaises(DefaultObject.DoesNotExist):
            TestSearchManagerTypeclass.objects.get(pk=self.child.pk)
        with self.assertRaises(DefaultObject.DoesNotExist):
            TestSearchManagerTypeclassChild.objects.get(pk=self.obj.pk)

    def test_related_manager_keeps_its_owner_filter(self):
        from django.db.models.fields.related_descriptors import (
            create_reverse_many_to_one_manager,
        )

        from evennia.objects.models import ObjectDB

        container, _ = TestSearchManagerTypeclass.create(key="container")
        inside, _ = TestSearchManagerTypeclass.create(key="inside", location=container)
        rel = ObjectDB._meta.get_field("db_location").remote_field
        manager_class = create_reverse_many_to_one_manager(
            type(TestSearchManagerTypeclass.objects), rel
        )
        contents = manager_class(container)
        contents.model = TestSearchManagerTypeclass

        self.assertEqual(contents.get(pk=inside.pk), inside)
        with self.assertRaises(DefaultObject.DoesNotExist):
            contents.get(pk=self.obj.pk)

    def test_other_lookups_use_sql(self):
        with self.assertNumQueries(1):
            self.assertIs(TestSearchManagerTypeclass.objects.get(db_key="obj"), self.obj)


class TestTags(BaseEvenniaTest):
    evennia_fixtures = {"obj1"}

    def test_has_tag_key_only(self):
        self.obj1.tags.add("tagC")
        self.assertTrue(self.obj1.tags.has("tagC"))

    def test_has_tag_key_with_category(self):
        self.obj1.tags.add("tagC", "categoryC")
        self.assertTrue(self.obj1.tags.has("tagC", "categoryC"))

    def test_does_not_have_tag_key_only(self):
        self.obj1.tags.add("tagC")
        self.assertFalse(self.obj1.tags.has("tagD"))

    def test_does_not_have_tag_key_with_category(self):
        self.obj1.tags.add("tagC", "categoryC")
        self.assertFalse(self.obj1.tags.has("tagD", "categoryD"))

    def test_has_tag_category_only(self):
        self.obj1.tags.add("tagC", "categoryC")
        self.assertTrue(self.obj1.tags.has(category="categoryC"))

    def test_does_not_have_tag_category_only(self):
        self.obj1.tags.add("tagC", "categoryC")
        self.assertFalse(self.obj1.tags.has(category="categoryD"))

    def test_integer_tag(self):
        self.obj1.tags.add(1)
        self.assertTrue(self.obj1.tags.has(1))
        self.assertTrue(self.obj1.tags.get(1))
        self.assertTrue(self.obj1.tags.has("1"))
        self.assertTrue(self.obj1.tags.get("1"))
        self.obj1.tags.remove(1)
        self.assertFalse(self.obj1.tags.has(1))
        self.assertFalse(self.obj1.tags.get(1))
        self.assertFalse(self.obj1.tags.has("1"))
        self.assertFalse(self.obj1.tags.get("1"))

    def test_tag_add_no_category__issue_2688(self):
        """
        Adding a tag without a category should create a new tag:None tag
        rather than trying to update an existing tag:category tag.
        """
        # adding tag+category, creates tag+category entry
        self.obj1.tags.add("testing", category="testing_category")
        self.assertEqual(
            self.obj1.tags.all(return_key_and_category=True), [("testing", "testing_category")]
        )
        # adding a new tag with no category should create a tag+None entry
        self.obj1.tags.add("testing")
        self.assertEqual(
            self.obj1.tags.all(return_key_and_category=True),
            [("testing", "testing_category"), ("testing", None)],
        )


class TestAliasOrder(BaseEvenniaTest):
    """Aliases keep insertion order; the first alias is the display alias."""

    evennia_fixtures = {"obj1"}

    def test_all_returns_insertion_order(self):
        self.obj1.aliases.add(["nw", "northwest"])
        self.assertEqual(self.obj1.aliases.all(), ["nw", "northwest"])
        self.assertEqual(str(self.obj1.aliases), "nw,northwest")

    def test_order_survives_a_cold_reload(self):
        self.obj1.aliases.add(["nw", "northwest"])
        flush_cache()
        obj = self.obj1.__class__.objects.get(id=self.obj1.id)
        self.assertEqual(obj.aliases.all(), ["nw", "northwest"])

    def test_order_survives_a_primed_cache(self):
        self.obj1.aliases.add(["nw", "northwest"])
        flush_cache()
        objs = list(self.obj1.__class__.objects.filter(id=self.obj1.id))
        self.obj1.__class__.objects.prime_tag_caches(objs, tagtype="alias")
        self.assertEqual(objs[0].aliases.all(), ["nw", "northwest"])

    def test_readd_moves_the_alias_to_the_end(self):
        self.obj1.aliases.add(["nw", "northwest"])
        self.obj1.aliases.remove("nw")
        self.obj1.aliases.add("nw")
        self.assertEqual(self.obj1.aliases.all(), ["northwest", "nw"])

    def test_tags_handler_stays_sorted(self):
        self.obj1.tags.add(["zz_tag", "aa_tag"])
        self.assertEqual(self.obj1.tags.all(), ["aa_tag", "zz_tag"])


class TestTagMissCache(BaseEvenniaTest):
    """A known-absent tag must not re-query until a write invalidates it.

    ``tags.has`` is on the room-contents and broadcast hot paths, and a
    negative hit is what the common case asks for (an untagged room, an object
    without a permission). Each miss used to be one SQL query.
    """

    evennia_fixtures = {"obj1"}

    def _has_queries(self, key, category=None):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            for _ in range(5):
                self.obj1.tags.has(key, category=category)
        return len(ctx)

    def test_miss_is_remembered_and_free(self):
        flush_cache()
        self.obj1.tags.has("absent_tag", category="absent_cat")
        self.assertEqual(self._has_queries("absent_tag", "absent_cat"), 0)

    def test_add_after_miss_invalidates(self):
        flush_cache()
        self.assertFalse(self.obj1.tags.has("later_tag", category="cat"))
        self.obj1.tags.add("later_tag", category="cat")
        self.assertTrue(self.obj1.tags.has("later_tag", category="cat"))

    def test_remove_after_hit_invalidates(self):
        flush_cache()
        self.obj1.tags.add("gone_tag", category="cat")
        self.assertTrue(self.obj1.tags.has("gone_tag", category="cat"))
        self.obj1.tags.remove("gone_tag", category="cat")
        self.assertFalse(self.obj1.tags.has("gone_tag", category="cat"))

    def test_clear_invalidates(self):
        flush_cache()
        self.assertFalse(self.obj1.tags.has("cleared_tag", category="cat"))
        self.obj1.tags.add("other_tag", category="cat")
        self.obj1.tags.clear()
        self.assertFalse(self.obj1.tags.has("other_tag", category="cat"))
        self.obj1.tags.add("other_tag", category="cat")
        self.assertTrue(self.obj1.tags.has("other_tag", category="cat"))

    def test_reset_cache_invalidates(self):
        flush_cache()
        self.assertFalse(self.obj1.tags.has("reset_tag", category="cat"))
        self.obj1.tags.add("reset_tag", category="cat")
        self.obj1.tags.reset_cache()
        self.assertTrue(self.obj1.tags.has("reset_tag", category="cat"))

    def test_prefetch_then_miss_then_add(self):
        flush_cache()
        obj = (
            self.obj1.__class__.objects.filter(id=self.obj1.id).prefetch_related("db_tags").first()
        )
        self.assertFalse(obj.tags.has("prefetched_miss", category="cat"))
        obj.tags.add("prefetched_miss", category="cat")
        self.assertTrue(obj.tags.has("prefetched_miss", category="cat"))

    def test_miss_cache_ignored_when_aggressive_cache_off(self):
        flush_cache()
        with override_settings(TYPECLASS_AGGRESSIVE_CACHE=False):
            self.assertFalse(self.obj1.tags.has("no_cache_tag", category="cat"))
            self.obj1.tags.add("no_cache_tag", category="cat")
            self.assertTrue(self.obj1.tags.has("no_cache_tag", category="cat"))

    def test_misscache_is_bounded(self):
        """Misses keyed by player-typed text must not grow the dict forever."""

        from evennia.typeclasses import tags as tags_module

        flush_cache()
        with patch.object(tags_module, "_TAG_MISSCACHE_LIMIT", 8):
            for index in range(20):
                self.assertFalse(self.obj1.tags.has(f"typed{index}", category="chat"))
        self.assertLessEqual(len(self.obj1.tags._misscache), 8)
        # a dropped entry costs a re-query, never a wrong answer
        self.assertFalse(self.obj1.tags.has("typed19", category="chat"))


class TestTagBulkPrefetch(BaseEvenniaTest):
    """
    Efficiency of bulk tag reads: prefetch-aware handler + manager bulk API.

    The handler must answer per-category reads from cache when the object was
    loaded with ``prefetch_related('db_tags')`` or primed via the manager,
    instead of firing one query per (object, category).
    """

    evennia_fixtures = {"obj1", "obj2"}

    CATS = ["cat_a", "cat_b", "cat_c", "cat_d"]

    def setUp(self):
        super().setUp()
        for obj in (self.obj1, self.obj2):
            for cat in self.CATS:
                obj.tags.add("t_%s" % cat, category=cat)
        self.ids = [self.obj1.id, self.obj2.id]
        # use the typeclass manager (proxy model), not base ObjectDB.objects:
        # the through-table FK is named for the dbclass, so a typeclass manager
        # is the path that exposes a wrong field-name derivation
        self.mgr = self.obj1.__class__.objects

    def _read_all_categories(self, objs):
        for obj in objs:
            for cat in self.CATS:
                obj.tags.get(category=cat, return_list=True)

    def test_baseline_is_n_plus_one(self):
        # documents the per-(object, category) query cost without prefetch
        flush_cache()
        objs = list(self.mgr.filter(id__in=self.ids))
        with CaptureQueriesContext(connection) as ctx:
            self._read_all_categories(objs)
        self.assertEqual(len(ctx), len(objs) * len(self.CATS))

    def test_prefetch_makes_reads_free(self):
        flush_cache()
        objs = list(self.mgr.filter(id__in=self.ids).prefetch_related("db_tags"))
        with CaptureQueriesContext(connection) as ctx:
            self._read_all_categories(objs)
        self.assertEqual(len(ctx), 0)
        # and the values are correct
        for obj in objs:
            for cat in self.CATS:
                self.assertEqual(obj.tags.get(category=cat, return_list=True), ["t_%s" % cat])

    def test_prime_tag_caches_one_query_then_free(self):
        flush_cache()
        objs = list(self.mgr.filter(id__in=self.ids))
        with CaptureQueriesContext(connection) as prime_ctx:
            self.mgr.prime_tag_caches(objs)
        self.assertEqual(len(prime_ctx), 1)
        with CaptureQueriesContext(connection) as read_ctx:
            self._read_all_categories(objs)
            for obj in objs:
                obj.tags.all()
        self.assertEqual(len(read_ctx), 0)

    def test_get_tags_for_objects_shape_and_one_query(self):
        flush_cache()
        objs = list(self.mgr.filter(id__in=self.ids))
        with CaptureQueriesContext(connection) as ctx:
            mapping = self.mgr.get_tags_for_objects(objs)
        self.assertEqual(len(ctx), 1)
        self.assertEqual(set(mapping), set(self.ids))
        self.assertEqual(set(mapping[self.obj1.id]), set(self.CATS))
        self.assertEqual([tag.db_key for tag in mapping[self.obj1.id]["cat_a"]], ["t_cat_a"])

    def test_tagtype_partitioning_under_prefetch(self):
        # tags, aliases and permissions share the db_tags m2m; prefetch must
        # not bleed one tagtype into another's handler cache
        self.obj1.aliases.add("an_alias")
        self.obj1.permissions.add("Builder")
        flush_cache()
        obj = self.mgr.filter(id=self.obj1.id).prefetch_related("db_tags").first()
        self.assertEqual(obj.tags.get(category="cat_a", return_list=True), ["t_cat_a"])
        self.assertIn("an_alias", obj.aliases.all())
        self.assertNotIn("t_cat_a", obj.aliases.all())
        self.assertIn("builder", obj.permissions.all())
        self.assertNotIn("an_alias", obj.tags.all())

    def test_category_none_from_primed_cache(self):
        self.obj2.tags.add("uncategorized")
        flush_cache()
        objs = list(self.mgr.filter(id__in=self.ids))
        self.mgr.prime_tag_caches(objs)
        obj2 = next(o for o in objs if o.id == self.obj2.id)
        with CaptureQueriesContext(connection) as ctx:
            self.assertIn("uncategorized", obj2.tags.get(category=None, return_list=True))
        self.assertEqual(len(ctx), 0)

    def test_mutation_coherence_after_prefetch(self):
        flush_cache()
        obj = self.mgr.filter(id=self.obj1.id).prefetch_related("db_tags").first()
        # seed from prefetch
        obj.tags.get(category="cat_a", return_list=True)
        obj.tags.add("added", category="cat_a")
        self.assertIn("added", obj.tags.get(category="cat_a", return_list=True))
        obj.tags.remove("added", category="cat_a")
        self.assertNotIn("added", obj.tags.get(category="cat_a", return_list=True))

    def test_get_tag_with_obj_through_typeclass_manager(self):
        # the through-table FK is named for the dbclass ("objectdb"), not the
        # typeclass proxy; deriving it from self.model.__name__ raises FieldError
        # on a typeclass manager. Covers both the global_search and non-global
        # branches of get_tag, plus get_alias/get_permission.
        self.obj1.aliases.add("an_alias")
        self.obj1.permissions.add("Builder")
        flush_cache()

        keys = [tag.db_key for tag in self.mgr.get_tag(obj=self.obj1)]
        self.assertEqual(set(keys), {"t_%s" % cat for cat in self.CATS})
        # the global_search branch takes the other code path; it defaults to the
        # None category, so query a specific category to exercise the obj filter
        gkeys = [
            tag.db_key
            for tag in self.mgr.get_tag(obj=self.obj1, category="cat_a", global_search=True)
        ]
        self.assertEqual(gkeys, ["t_cat_a"])

        aliases = [tag.db_key for tag in self.mgr.get_alias(obj=self.obj1)]
        self.assertIn("an_alias", aliases)
        perms = [tag.db_key for tag in self.mgr.get_permission(obj=self.obj1)]
        self.assertIn("builder", perms)

    @override_settings(TYPECLASS_AGGRESSIVE_CACHE=False)
    def test_prime_noop_without_aggressive_cache(self):
        flush_cache()
        objs = list(self.mgr.filter(id__in=self.ids))
        # priming must not populate a cache that is meant to be disabled, but
        # reads must still return correct values (uncached)
        self.mgr.prime_tag_caches(objs)
        for obj in objs:
            self.assertFalse(obj.tags._cache_complete)
            for cat in self.CATS:
                self.assertEqual(obj.tags.get(category=cat, return_list=True), ["t_%s" % cat])


class TestNickHandler(BaseEvenniaTest):
    """
    Test the nick handler replacement.

    """

    evennia_fixtures = {"char1"}

    @parameterized.expand(
        [
            # shell syntax
            (
                "gr $1 $2 at $3",
                "emote with a $1 smile, $2 grins at $3.",
                False,
                "gr happy Foo at Bar",
                "emote with a happy smile, Foo grins at Bar.",
            ),
            # regex syntax
            (
                "gr (?P<arg1>.+?) (?P<arg2>.+?) at (?P<arg3>.+?)",
                "emote with a $1 smile, $2 grins at $3.",
                True,
                "gr happy Foo at Bar",
                "emote with a happy smile, Foo grins at Bar.",
            ),
            # channel-style syntax
            (
                "groo $1",
                "channel groo = $1",
                False,
                "groo Hello world",
                "channel groo = Hello world",
            ),
            (
                r"groo\s*?|groo\s+?(?P<arg1>.+?)",
                "channel groo = $1",
                True,
                "groo Hello world",
                "channel groo = Hello world",
            ),
            (
                r"groo\s*?|groo\s+?(?P<arg1>.+?)",
                "channel groo = $1",
                True,
                "groo ",
                "channel groo = ",
            ),
            (
                r"groo\s*?|groo\s*?(?P<arg1>.+?)",
                "channel groo = $1",
                True,
                "groo",
                "channel groo = ",
            ),
            (
                r"groo\s*?|groo\s+?(?P<arg1>.+?)",
                "channel groo = $1",
                True,
                "grooHello world",
                "grooHello world",
            ),  # not matched - this is correct!
            # optional, space-separated arguments
            (
                r"groo\s*?|groo\s+?(?P<arg1>.+?)(?:\s+?(?P<arg2>.+?)){0,1}",
                "channel groo = $1 and $2",
                True,
                "groo Hello World",
                "channel groo = Hello and World",
            ),
            (
                r"groo\s*?|groo\s+?(?P<arg1>.+?)(?:\s+?(?P<arg2>.+?)){0,1}",
                "channel groo = $1 and $2",
                True,
                "groo Hello",
                "channel groo = Hello and ",
            ),
            (
                r"groo\s*?|groo\s+?(?P<arg1>.+?)(?:\s+?(?P<arg2>.+?)){0,1}",
                "channel groo = $1 and $2",
                True,
                "groo",
                "channel groo =  and ",
            ),  # $1/$2 replaced by ''
        ]
    )
    def test_nick_parsing(
        self, pattern, replacement, pattern_is_regex, inp_string, expected_replaced
    ):
        """
        Setting up nick patterns and make sure they replace as expected.

        """
        # from evennia import set_trace;set_trace()
        self.char1.nicks.add(
            pattern, replacement, category="inputline", pattern_is_regex=pattern_is_regex
        )
        actual_replaced = self.char1.nicks.nickreplace(inp_string)

        self.assertEqual(expected_replaced, actual_replaced)
        self.char1.nicks.clear()

    def test_nick_with_parenthesis(self):
        """
        Test case where input has a special character

        """
        import re

        from evennia.typeclasses.attributes import initialize_nick_templates

        nick_regex, replacement_string = initialize_nick_templates(
            re.escape("OOC["), "ooc", pattern_is_regex=True
        )
        re.compile(nick_regex, re.I + re.DOTALL + re.U)


class TestGetByTagPlainQuery(BaseEvenniaTest):
    """`get_by_tag` answers the usual searches with a plain semi-join, and agrees with the counting join.

    A sweep that asks every few seconds for the objects carrying one tag used to pay for a join over
    every tagged object, grouped and counted, whatever it found. The usual search (one key and one
    category, a list of distinct keys, one category alone) is now a plain queryset; `match="any"`,
    several categories with no key and a repeated key still take the counting join.
    """

    evennia_fixtures = {"obj1", "obj2", "char1", "room1", "script"}

    def setUp(self):
        super().setUp()
        # Not `DefaultObject.objects`: a typeclass manager keeps to its own typeclass.
        self.manager = ObjectDB.objects
        self.obj1.tags.add("red", "color")
        self.obj1.tags.add("big", "size")
        self.obj1.tags.add("plain")
        self.obj2.tags.add("red", "color")
        self.obj2.tags.add("small", "size")
        self.obj2.tags.add("plain")
        self.obj2.tags.add("shiny", "look")
        self.char1.tags.add("blue", "color")
        self.char1.tags.add("big", "size")
        self.char1.tags.add("plain", "look")
        self.room1.tags.add("shiny", "look")
        self.room1.tags.add("red")
        self.char1.aliases.add("hero")
        self.obj1.permissions.add("wizard")

    def _both(self, *args, **kwargs):
        """The plain answer and the counting join's answer to the same search, or the error each raised."""
        answers = []
        for method in (self.manager.get_by_tag, self.manager._get_by_tag_counted):
            try:
                answers.append([obj.id for obj in method(*args, **kwargs)])
            except Exception as err:
                answers.append(type(err))
        return answers

    def test_the_plain_query_agrees_with_the_counting_join(self):
        keys = (
            None,
            "red",
            "big",
            "plain",
            "shiny",
            "nothere",
            "RED ",
            ["red", "big"],
            ["red", "plain"],
            ["red", "small"],
            ["big", "plain", "red"],
            ["red", "red"],
        )
        categories = (
            None,
            "color",
            "size",
            "look",
            "nowhere",
            "COLOR",
            ["color", "size"],
            ["color", "look"],
            ["color", "size", "look"],
            ["color", "size", "look", "nowhere"],
        )
        checked = 0
        for key in keys:
            for category in categories:
                if key is None and category is None:
                    continue
                with self.subTest(key=key, category=category):
                    plain, counted = self._both(key, category)
                    self.assertEqual(plain, counted)
                    checked += 1
        self.assertGreater(checked, 100)

    def test_it_finds_what_the_old_tests_expect_in_each_shape(self):
        self.assertEqual(self._both("red", "color")[0], sorted([self.obj1.id, self.obj2.id]))
        self.assertEqual(self._both("red")[0], [self.room1.id])
        self.assertEqual(self._both(["red", "big"], "color")[0], [])
        self.assertEqual(self._both(["big", "red"], ["size", "color"])[0], [self.obj1.id])
        self.assertEqual(
            self._both(category="look")[0], sorted([self.obj2.id, self.char1.id, self.room1.id])
        )

    def test_keys_and_categories_are_matched_in_lower_case(self):
        self.assertEqual(
            [obj.id for obj in self.manager.get_by_tag(" RED ", "Color")],
            [obj.id for obj in self.manager.get_by_tag("red", "color")],
        )
        self.assertTrue(self.manager.get_by_tag(" RED ", "Color").exists())
        self.assertEqual(list(self.manager.get_by_tag("hero", tagtype="ALIAS")), [self.char1])

    def test_the_plain_query_has_no_distinct_no_group_by_and_no_count(self):
        for search in (
            self.manager.get_by_tag("red", "color"),
            self.manager.get_by_tag(["red", "big"], ["color", "size"]),
            self.manager.get_by_tag(category="look"),
        ):
            sql = str(search.query).upper()
            self.assertNotIn("DISTINCT", sql)
            self.assertNotIn("GROUP BY", sql)
            self.assertNotIn("COUNT(", sql)
            self.assertIn("ORDER BY", sql)

    def test_the_counting_join_still_has_them(self):
        sql = str(self.manager.get_by_tag("red", "color", match="any").query).upper()
        self.assertIn("GROUP BY", sql)

    def test_one_query_whatever_is_tagged(self):
        with self.assertNumQueries(1):
            list(self.manager.get_by_tag("red", "color"))
        with self.assertNumQueries(1):
            list(self.manager.get_by_tag("nothere", "nowhere"))
        with self.assertNumQueries(1):
            list(self.manager.get_by_tag(["red", "big", "plain"], ["color", "size", None]))

    def test_the_result_is_a_queryset_that_chains(self):
        search = self.manager.get_by_tag("red", "color")
        self.assertEqual(search.count(), 2)
        self.assertEqual(
            [obj.id for obj in search.order_by("-id")],
            sorted([self.obj1.id, self.obj2.id], reverse=True),
        )
        self.assertEqual(list(search.filter(id=self.obj1.id)), [self.obj1])
        self.assertEqual(
            list(search.values_list("id", flat=True)), sorted([self.obj1.id, self.obj2.id])
        )
        self.assertTrue(search.exists())
        self.assertFalse(self.manager.get_by_tag("nothere", "nowhere").exists())

    def test_it_is_ordered_by_id(self):
        found = [obj.id for obj in self.manager.get_by_tag(category="look")]
        self.assertEqual(found, sorted(found))

    def test_an_object_with_two_tags_of_a_category_comes_once(self):
        self.obj1.tags.add("heavy", "size")
        found = [obj.id for obj in self.manager.get_by_tag(category="size")]
        self.assertEqual(found.count(self.obj1.id), 1)

    def test_a_typeclass_manager_keeps_to_its_own_typeclass(self):
        from evennia.objects.objects import DefaultCharacter

        self.assertEqual(list(DefaultCharacter.objects.get_by_tag("big", "size")), [self.char1])
        self.assertEqual(list(DefaultObject.objects.get_by_tag("big", "size")), [self.obj1])
        self.assertEqual(set(ObjectDB.objects.get_by_tag("big", "size")), {self.obj1, self.char1})
        self.assertEqual(
            list(DefaultCharacter.objects.get_by_tag("big", "size")),
            list(DefaultCharacter.objects._get_by_tag_counted("big", "size")),
        )

    def test_it_serves_the_other_typeclassed_models(self):
        from evennia.scripts.models import ScriptDB

        self.script.tags.add("red", "color")
        self.assertEqual(list(ScriptDB.objects.get_by_tag("red", "color")), [self.script])
        self.assertNotIn(self.obj1, list(ScriptDB.objects.get_by_tag("red", "color")))

    def test_a_normal_search_leaves_aliases_and_permissions_alone(self):
        self.assertEqual(list(self.manager.get_by_tag("hero")), [])
        self.assertEqual(list(self.manager.get_by_tag("wizard")), [])
        self.assertEqual(list(self.manager.get_by_alias("hero")), [self.char1])
        self.assertEqual(list(self.manager.get_by_permission("wizard")), [self.obj1])

    def test_a_search_with_no_key_and_no_category_finds_nothing(self):
        self.assertEqual(self.manager.get_by_tag(), [])

    def test_a_list_of_keys_with_too_few_categories_still_raises(self):
        with self.assertRaises(IndexError):
            self.manager.get_by_tag(["red", "big", "plain"], ["color", "size"])
