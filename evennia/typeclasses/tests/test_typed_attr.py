"""
Tests for evennia.typeclasses.typed_attr.

Covers:
  - AttrField validation
  - AttributeBag: init, get/set, defaults, write-back, dict interface
  - TypedAttr blob/typed_col: get/set, defaults, autocreate, hooks,
    validation, filter_kwargs, __delete__
  - TypedAttr bag: whole-bag set, sub-key access, schema validation
  - Schema migration ops: RenameAttr, TransformAttr, DropAttr
  - apply_schema_migrations: ordering, version stamp, no-op when current,
    skips failed op without aborting subsequent ops
"""

from unittest.mock import MagicMock, patch

from evennia.typeclasses.typed_attr import (
    AttrField,
    AttributeBag,
    DropAttr,
    RenameAttr,
    TransformAttr,
    TypedAttr,
    _SCHEMA_VERSION_CATEGORY,
    _SCHEMA_VERSION_KEY,
    _UNSET,
    apply_schema_migrations,
)
from evennia.utils.test_resources import BaseEvenniaTest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _obj_with_attrs(test_case):
    """Return a plain in-memory object suitable for attribute tests."""
    return test_case.obj1


# ---------------------------------------------------------------------------
# AttrField
# ---------------------------------------------------------------------------


class TestAttrField(BaseEvenniaTest):
    def test_validate_passes_none_when_nullable(self):
        f = AttrField(int, default=0, nullable=True)
        f.validate("x", None)  # should not raise

    def test_validate_rejects_none_when_not_nullable(self):
        f = AttrField(int, default=0, nullable=False)
        with self.assertRaises(ValueError):
            f.validate("x", None)

    def test_validate_type_mismatch(self):
        f = AttrField(int)
        with self.assertRaises(TypeError):
            f.validate("x", "not-an-int")

    def test_validate_type_match(self):
        f = AttrField(int)
        f.validate("x", 5)  # no raise

    def test_validate_below_min(self):
        f = AttrField(int, min=1)
        with self.assertRaises(ValueError):
            f.validate("x", 0)

    def test_validate_above_max(self):
        f = AttrField(int, max=10)
        with self.assertRaises(ValueError):
            f.validate("x", 11)

    def test_validate_within_range(self):
        f = AttrField(int, min=1, max=10)
        f.validate("x", 5)  # no raise

    def test_validate_choices_rejected(self):
        f = AttrField(str, choices=("a", "b"))
        with self.assertRaises(ValueError):
            f.validate("x", "c")

    def test_validate_choices_accepted(self):
        f = AttrField(str, choices=("a", "b"))
        f.validate("x", "a")  # no raise

    def test_default_unset_by_default(self):
        f = AttrField(int)
        self.assertIs(f.default, _UNSET)

    def test_bare_value_still_works(self):
        # AttrField can be constructed with just a type and no default
        f = AttrField(str)
        self.assertIs(f.type_, str)


# ---------------------------------------------------------------------------
# AttributeBag
# ---------------------------------------------------------------------------


class TestAttributeBag(BaseEvenniaTest):
    def _bag(self, schema=None):
        return AttributeBag(self.obj1, "test_bag", schema=schema)

    def test_setattr_and_getattr(self):
        bag = self._bag()
        bag.foo = "bar"
        self.assertEqual(bag.foo, "bar")

    def test_getattr_missing_raises(self):
        bag = self._bag()
        with self.assertRaises(AttributeError):
            _ = bag.nonexistent

    def test_getattr_returns_schema_default_when_key_absent(self):
        bag = self._bag(schema={"hp": AttrField(int, default=100)})
        # hp not written yet; should return schema default
        self.assertEqual(bag.hp, 100)

    def test_setattr_validates_schema(self):
        bag = self._bag(schema={"hp": AttrField(int, min=0, max=200)})
        with self.assertRaises(ValueError):
            bag.hp = -1

    def test_setattr_schema_type_check(self):
        bag = self._bag(schema={"name": AttrField(str)})
        with self.assertRaises(TypeError):
            bag.name = 42

    def test_contains_true_after_set(self):
        bag = self._bag()
        bag.foo = "bar"
        self.assertIn("foo", bag)

    def test_contains_false_when_absent(self):
        bag = self._bag()
        self.assertNotIn("nonexistent", bag)

    def test_init_creates_attribute_with_defaults(self):
        bag = self._bag(schema={"mp": AttrField(int, default=50)})
        # Reading any key triggers lazy init
        _ = bag.mp
        raw = self.obj1.attributes.get("test_bag")
        self.assertIsNotNone(raw)

    def test_write_persists_across_new_bag_instance(self):
        bag1 = self._bag()
        bag1.score = 99
        # Create a fresh bag instance pointing to the same object/key
        bag2 = self._bag()
        self.assertEqual(bag2.score, 99)

    def test_delattr_removes_key(self):
        bag = self._bag()
        bag.foo = "bar"
        del bag.foo
        self.assertNotIn("foo", bag)

    def test_get_returns_default_when_absent(self):
        bag = self._bag()
        self.assertEqual(bag.get("missing", "default"), "default")

    def test_get_returns_value_when_present(self):
        bag = self._bag()
        bag.key = "val"
        self.assertEqual(bag.get("key"), "val")

    def test_keys_contains_set_keys(self):
        bag = self._bag()
        bag.a = 1
        bag.b = 2
        self.assertIn("a", bag.keys())
        self.assertIn("b", bag.keys())

    def test_items_contains_set_pairs(self):
        bag = self._bag()
        bag.x = 10
        self.assertIn(("x", 10), list(bag.items()))

    def test_update_sets_multiple_keys(self):
        bag = self._bag()
        bag.update({"foo": 1, "bar": 2})
        self.assertEqual(bag.foo, 1)
        self.assertEqual(bag.bar, 2)

    def test_update_validates_schema(self):
        bag = self._bag(schema={"hp": AttrField(int, min=0)})
        with self.assertRaises(ValueError):
            bag.update({"hp": -5})

    def test_repr_includes_key(self):
        bag = self._bag()
        self.assertIn("test_bag", repr(bag))

    def test_mutation_of_nested_dict_persists(self):
        """_SaverDict write-back must propagate nested mutations."""
        bag = self._bag()
        bag.nested = {"inner": 1}
        # Re-fetch the bag and mutate via the _SaverDict
        bag2 = self._bag()
        data = bag2._data()
        data["nested"]["inner"] = 99
        # Read again
        bag3 = self._bag()
        self.assertEqual(bag3.nested["inner"], 99)


# ---------------------------------------------------------------------------
# TypedAttr — constructor guards
# ---------------------------------------------------------------------------


class TestTypedAttrConstructor(BaseEvenniaTest):
    def test_invalid_backend_raises(self):
        with self.assertRaises(ValueError):
            TypedAttr(int, backend="invalid")

    def test_typed_col_non_primitive_raises(self):
        with self.assertRaises(TypeError):
            TypedAttr(list, backend="typed_col")

    def test_bag_with_type_raises(self):
        with self.assertRaises(TypeError):
            TypedAttr(int, backend="bag")

    def test_set_name_registers_in_typed_attrs(self):
        class FakeOwner:
            pass

        td = TypedAttr(int, default=0)
        td.__set_name__(FakeOwner, "my_attr")
        self.assertEqual(td.attr_key, "my_attr")
        self.assertIn("my_attr", FakeOwner._typed_attrs)
        self.assertIs(FakeOwner._typed_attrs["my_attr"], td)

    def test_keys_normalised_to_attrfield(self):
        td = TypedAttr(backend="bag", keys={"x": 5})
        self.assertIsInstance(td.keys["x"], AttrField)
        self.assertEqual(td.keys["x"].default, 5)


# ---------------------------------------------------------------------------
# TypedAttr blob / typed_col — get / set / default / validation
# ---------------------------------------------------------------------------


class _TypedAttrBlob:
    """
    Shared test logic for blob and typed_col. Subclassed below with different
    backend values.
    """

    backend = "blob"

    def _make_descriptor(self, type_=None, **kwargs):
        kwargs.setdefault("backend", self.backend)
        td = TypedAttr(type_, **kwargs)
        td.__set_name__(type(self.obj1), "test_attr")
        return td

    def test_get_returns_default_before_set(self):
        td = self._make_descriptor(int, default=42, autocreate=False)
        val = td.__get__(self.obj1, type(self.obj1))
        self.assertEqual(val, 42)

    def test_set_and_get(self):
        td = self._make_descriptor(int, default=0)
        td.__set__(self.obj1, 7)
        val = td.__get__(self.obj1, type(self.obj1))
        self.assertEqual(val, 7)

    def test_set_validates_type(self):
        td = self._make_descriptor(int)
        with self.assertRaises(TypeError):
            td.__set__(self.obj1, "string")

    def test_set_validates_min(self):
        td = self._make_descriptor(int, min=0)
        with self.assertRaises(ValueError):
            td.__set__(self.obj1, -1)

    def test_set_validates_max(self):
        td = self._make_descriptor(int, max=100)
        with self.assertRaises(ValueError):
            td.__set__(self.obj1, 101)

    def test_set_validates_choices(self):
        td = self._make_descriptor(str, choices=("a", "b"))
        with self.assertRaises(ValueError):
            td.__set__(self.obj1, "c")

    def test_set_null_when_nullable(self):
        td = self._make_descriptor(int, nullable=True)
        td.__set__(self.obj1, None)
        val = td.__get__(self.obj1, type(self.obj1))
        self.assertIsNone(val)

    def test_set_null_rejected_when_not_nullable(self):
        td = self._make_descriptor(int, nullable=False)
        with self.assertRaises(ValueError):
            td.__set__(self.obj1, None)

    def test_delete_removes_attribute(self):
        td = self._make_descriptor(int, default=5)
        td.__set__(self.obj1, 5)
        td.__delete__(self.obj1)
        # After delete, get returns the default (not the stored value)
        val = td.__get__(self.obj1, type(self.obj1))
        self.assertEqual(val, 5)  # default still applies

    def test_class_level_access_returns_descriptor(self):
        td = self._make_descriptor(int)
        result = td.__get__(None, type(self.obj1))
        self.assertIs(result, td)

    def test_at_get_hook(self):
        td = self._make_descriptor(int, default=0)

        def doubled(value, obj):
            return value * 2 if value else 0

        td.at_get = doubled
        td.__set__(self.obj1, 5)
        val = td.__get__(self.obj1, type(self.obj1))
        self.assertEqual(val, 10)

    def test_at_set_hook(self):
        td = self._make_descriptor(int, default=0)

        def clamped(value, obj):
            return max(0, min(100, value))

        td.at_set = clamped
        td.__set__(self.obj1, 150)
        val = td.__get__(self.obj1, type(self.obj1))
        self.assertEqual(val, 100)

    def test_autocreate_false_does_not_create_row(self):
        td = self._make_descriptor(int, default=99, autocreate=False)
        # Just reading should NOT create an Attribute row
        _ = td.__get__(self.obj1, type(self.obj1))
        self.assertFalse(self.obj1.attributes.has("test_attr"))

    def test_persists_across_reads(self):
        td = self._make_descriptor(str)
        td.__set__(self.obj1, "hello")
        # Read twice
        v1 = td.__get__(self.obj1, type(self.obj1))
        v2 = td.__get__(self.obj1, type(self.obj1))
        self.assertEqual(v1, "hello")
        self.assertEqual(v2, "hello")


class TestTypedAttrBlob(_TypedAttrBlob, BaseEvenniaTest):
    backend = "blob"


class TestTypedAttrTypedCol(_TypedAttrBlob, BaseEvenniaTest):
    backend = "typed_col"

    def _make_descriptor(self, type_=int, **kwargs):
        kwargs.setdefault("backend", self.backend)
        td = TypedAttr(type_, **kwargs)
        td.__set_name__(type(self.obj1), "test_attr")
        return td


# ---------------------------------------------------------------------------
# TypedAttr bag
# ---------------------------------------------------------------------------


class TestTypedAttrBag(BaseEvenniaTest):
    def _descriptor(self, **kwargs):
        td = TypedAttr(backend="bag", **kwargs)
        td.__set_name__(type(self.obj1), "stats")
        return td

    def test_get_returns_attribute_bag(self):
        td = self._descriptor()
        result = td.__get__(self.obj1, type(self.obj1))
        self.assertIsInstance(result, AttributeBag)

    def test_sub_key_set_and_get(self):
        td = self._descriptor()
        bag = td.__get__(self.obj1, type(self.obj1))
        bag.strength = 15
        bag2 = td.__get__(self.obj1, type(self.obj1))
        self.assertEqual(bag2.strength, 15)

    def test_schema_default_returned_before_set(self):
        td = self._descriptor(keys={"hp": AttrField(int, default=100)})
        bag = td.__get__(self.obj1, type(self.obj1))
        self.assertEqual(bag.hp, 100)

    def test_schema_validates_on_sub_key_set(self):
        td = self._descriptor(keys={"hp": AttrField(int, min=0, max=200)})
        bag = td.__get__(self.obj1, type(self.obj1))
        with self.assertRaises(ValueError):
            bag.hp = 999

    def test_whole_bag_set_via_dict(self):
        td = self._descriptor()
        td.__set__(self.obj1, {"a": 1, "b": 2})
        bag = td.__get__(self.obj1, type(self.obj1))
        self.assertEqual(bag.a, 1)
        self.assertEqual(bag.b, 2)

    def test_whole_bag_set_validates_schema(self):
        td = self._descriptor(keys={"hp": AttrField(int, max=100)})
        with self.assertRaises(ValueError):
            td.__set__(self.obj1, {"hp": 999})

    def test_whole_bag_set_rejects_non_dict(self):
        td = self._descriptor()
        with self.assertRaises(TypeError):
            td.__set__(self.obj1, "not-a-dict")

    def test_class_level_access_returns_descriptor(self):
        td = self._descriptor()
        self.assertIs(td.__get__(None, type(self.obj1)), td)

    def test_category_forwarded_to_bag(self):
        td = TypedAttr(backend="bag", category="traits")
        td.__set_name__(type(self.obj1), "stats")
        bag = td.__get__(self.obj1, type(self.obj1))
        # Internal category is forwarded
        self.assertEqual(object.__getattribute__(bag, "_cat"), "traits")


# ---------------------------------------------------------------------------
# Migration ops — apply()
# ---------------------------------------------------------------------------


class TestMigrationOps(BaseEvenniaTest):
    def test_rename_attr_moves_value(self):
        self.obj1.attributes.add("old_key", 42)
        op = RenameAttr("old_key", "new_key")
        op.apply(self.obj1)
        self.assertIsNone(self.obj1.attributes.get("old_key"))
        self.assertEqual(self.obj1.attributes.get("new_key"), 42)

    def test_rename_attr_no_op_when_source_absent(self):
        op = RenameAttr("nonexistent", "dest")
        op.apply(self.obj1)  # should not raise
        self.assertIsNone(self.obj1.attributes.get("dest"))

    def test_rename_attr_respects_category(self):
        self.obj1.attributes.add("old", "val", category="cat")
        op = RenameAttr("old", "new", category="cat")
        op.apply(self.obj1)
        self.assertIsNone(self.obj1.attributes.get("old", category="cat"))
        self.assertEqual(self.obj1.attributes.get("new", category="cat"), "val")

    def test_transform_attr_applies_fn(self):
        self.obj1.attributes.add("score", 10)
        op = TransformAttr("score", lambda v: v * 2)
        op.apply(self.obj1)
        self.assertEqual(self.obj1.attributes.get("score"), 20)

    def test_transform_attr_no_op_when_absent(self):
        op = TransformAttr("missing", lambda v: v * 2)
        op.apply(self.obj1)  # should not raise

    def test_drop_attr_removes_key(self):
        self.obj1.attributes.add("stale", "val")
        op = DropAttr("stale")
        op.apply(self.obj1)
        self.assertIsNone(self.obj1.attributes.get("stale"))

    def test_drop_attr_no_op_when_absent(self):
        op = DropAttr("nonexistent")
        op.apply(self.obj1)  # should not raise

    def test_repr_rename(self):
        self.assertIn("old", repr(RenameAttr("old", "new")))

    def test_repr_transform(self):
        self.assertIn("score", repr(TransformAttr("score", lambda v: v)))

    def test_repr_drop(self):
        self.assertIn("stale", repr(DropAttr("stale")))


# ---------------------------------------------------------------------------
# apply_schema_migrations
# ---------------------------------------------------------------------------


class TestApplySchemaMigrations(BaseEvenniaTest):
    def _stamp(self, obj, version):
        obj.attributes.add(_SCHEMA_VERSION_KEY, version, category=_SCHEMA_VERSION_CATEGORY)

    def _stored_version(self, obj):
        return obj.attributes.get(_SCHEMA_VERSION_KEY, category=_SCHEMA_VERSION_CATEGORY)

    def test_no_op_when_no_schema_version_declared(self):
        """Objects whose typeclass declares no _attr_schema_version are skipped."""
        apply_schema_migrations(self.obj1)
        self.assertIsNone(self._stored_version(self.obj1))

    def test_no_op_when_already_current(self):
        obj = self.obj1
        obj.__class__._attr_schema_version = 3
        obj.__class__._attr_migrations = {}
        self._stamp(obj, 3)
        apply_schema_migrations(obj)
        self.assertEqual(self._stored_version(obj), 3)
        del obj.__class__._attr_schema_version
        del obj.__class__._attr_migrations

    def test_applies_single_migration(self):
        obj = self.obj1
        obj.attributes.add("old_hp", 80)
        obj.__class__._attr_schema_version = 2
        obj.__class__._attr_migrations = {2: [RenameAttr("old_hp", "hp")]}
        apply_schema_migrations(obj)
        self.assertEqual(obj.attributes.get("hp"), 80)
        self.assertIsNone(obj.attributes.get("old_hp"))
        self.assertEqual(self._stored_version(obj), 2)
        del obj.__class__._attr_schema_version
        del obj.__class__._attr_migrations

    def test_applies_multiple_versions_in_order(self):
        obj = self.obj1
        obj.attributes.add("v1_key", 1)
        obj.__class__._attr_schema_version = 3
        obj.__class__._attr_migrations = {
            2: [RenameAttr("v1_key", "v2_key")],
            3: [RenameAttr("v2_key", "v3_key")],
        }
        apply_schema_migrations(obj)
        self.assertIsNone(obj.attributes.get("v1_key"))
        self.assertIsNone(obj.attributes.get("v2_key"))
        self.assertEqual(obj.attributes.get("v3_key"), 1)
        self.assertEqual(self._stored_version(obj), 3)
        del obj.__class__._attr_schema_version
        del obj.__class__._attr_migrations

    def test_skips_already_applied_versions(self):
        obj = self.obj1
        obj.attributes.add("key", "original")
        obj.__class__._attr_schema_version = 3
        obj.__class__._attr_migrations = {
            2: [TransformAttr("key", lambda v: v + "_v2")],
            3: [TransformAttr("key", lambda v: v + "_v3")],
        }
        self._stamp(obj, 2)  # pretend v2 already ran
        apply_schema_migrations(obj)
        # Only v3 should have run
        self.assertEqual(obj.attributes.get("key"), "original_v3")
        del obj.__class__._attr_schema_version
        del obj.__class__._attr_migrations

    def test_failed_op_does_not_abort_subsequent_ops(self):
        obj = self.obj1
        obj.attributes.add("good_key", "good_val")

        class BrokenOp:
            def apply(self, o):
                raise RuntimeError("intentional failure")

            def __repr__(self):
                return "BrokenOp()"

        obj.__class__._attr_schema_version = 2
        obj.__class__._attr_migrations = {
            2: [BrokenOp(), RenameAttr("good_key", "renamed_key")],
        }
        # Should not raise; broken op is logged and skipped
        apply_schema_migrations(obj)
        # The second op (RenameAttr) should still have run
        self.assertEqual(obj.attributes.get("renamed_key"), "good_val")
        # Version stamp must still be advanced
        self.assertEqual(self._stored_version(obj), 2)
        del obj.__class__._attr_schema_version
        del obj.__class__._attr_migrations

    def test_version_stamp_written_after_migration(self):
        obj = self.obj1
        obj.__class__._attr_schema_version = 5
        obj.__class__._attr_migrations = {}
        apply_schema_migrations(obj)
        self.assertEqual(self._stored_version(obj), 5)
        del obj.__class__._attr_schema_version
        del obj.__class__._attr_migrations
