"""Tests for the attribute lens.

The panel's whole reason to exist is that attribute keys are unbounded, so the
tests are about behaviour under that unboundedness: the catalogue must be
honest that it sampled, the sample must not be mistaken for a count, and the
document decoding must survive documents that are wrong.

"""

from unittest.mock import patch

from django.test import TestCase

from evennia.console.panels import attributes as attrs
from evennia.console.panels.attributes import (
    DATA,
    NULL_CATEGORY,
    AttributesPanel,
    attribute_models,
)
from evennia.console.registry import CONSOLE_ACCESS, WorkerContext
from evennia.objects.models import ObjectDB


def _ctx(**params):
    """Build a worker context carrying panel parameters."""
    return WorkerContext(
        actor_id=1,
        actor_name="tester",
        capabilities=frozenset({CONSOLE_ACCESS}),
        params=params,
    )


def _doc(**pairs):
    """Build a default-category attribute document."""
    return {NULL_CATEGORY: {DATA: dict(pairs)}}


def _object(key, document=None):
    """Create one bare ObjectDB row carrying an attribute document.

    Deliberately not ``create_object``: that runs the typeclass lifecycle and
    wants a default home to exist. This panel only ever reads columns, so a
    plain row is both sufficient and a more honest fixture for what it does.
    """
    return ObjectDB.objects.create(
        db_key=key,
        db_typeclass_path="evennia.objects.objects.DefaultObject",
        db_attrs=document or {},
    )


class TestAttributeModels(TestCase):
    """The panel discovers which models carry documents."""

    def test_typeclass_models_carry_attributes(self):
        labels = attribute_models()
        for label in ("objects.objectdb", "accounts.accountdb"):
            self.assertIn(label, labels)

    def test_plain_models_do_not(self):
        labels = attribute_models()
        for label in ("server.sanction", "console.consoleauditevent"):
            self.assertNotIn(label, labels)

    def test_unknown_model_fails_closed(self):
        with self.assertRaises(LookupError):
            AttributesPanel().rows(_ctx(model="server.sanction"))


class TestDocumentDecoding(TestCase):
    """The engine's document layout is decoded, not shown raw."""

    def setUp(self):
        self.panel = AttributesPanel()

    def test_default_and_named_categories(self):
        entries = list(
            attrs._document_entries(
                {
                    NULL_CATEGORY: {DATA: {"hp": 10}},
                    "static": {DATA: {"bio": "long"}},
                }
            )
        )
        self.assertIn((None, "hp", 10), entries)
        self.assertIn(("static", "bio", "long"), entries)

    def test_locks_and_strvalues_are_not_entries(self):
        # Only _d holds values; _l and _s are sparse siblings.
        entries = list(
            attrs._document_entries(
                {NULL_CATEGORY: {DATA: {"a": 1}, "_l": {"a": "x"}, "_s": {"a": "y"}}}
            )
        )
        self.assertEqual(entries, [(None, "a", 1)])

    def test_malformed_documents_do_not_raise(self):
        for document in (None, [], "text", {"~": "not a dict"}, {"~": {"_d": "not a dict"}}):
            self.assertEqual(list(attrs._document_entries(document)), [])

    def test_value_kinds(self):
        self.assertEqual(attrs._value_kind(None), "null")
        self.assertEqual(attrs._value_kind(True), "bool")
        self.assertEqual(attrs._value_kind(3), "number")
        self.assertEqual(attrs._value_kind("s"), "text")
        self.assertEqual(attrs._value_kind({}), "dict")
        self.assertEqual(attrs._value_kind([]), "list")

    def test_bool_is_not_reported_as_number(self):
        # bool is a subclass of int; reporting it as a number would make the
        # catalogue lie about what a key holds.
        self.assertEqual(attrs._value_kind(False), "bool")


class TestCatalogue(TestCase):
    """Keys are the unit of navigation, and the sample is declared."""

    def setUp(self):
        self.panel = AttributesPanel()
        self.a = _object("alpha", _doc(hp=10, faction="corp"))
        self.b = _object("beta", _doc(hp=20))

    def test_lists_keys_with_object_counts(self):
        result = self.panel.rows(_ctx(model="objects.objectdb"))
        found = {row["key"]: row for row in result["rows"]}
        self.assertEqual(found["hp"]["objects"], 2)
        self.assertEqual(found["faction"]["objects"], 1)

    def test_ordered_by_reach(self):
        rows = self.panel.rows(_ctx(model="objects.objectdb"))["rows"]
        counts = [row["objects"] for row in rows]
        self.assertEqual(counts, sorted(counts, reverse=True))

    def test_reports_value_kinds_and_an_example(self):
        found = {row["key"]: row for row in self.panel.rows(_ctx(model="objects.objectdb"))["rows"]}
        self.assertEqual(found["hp"]["kinds"], ["number"])
        self.assertEqual(found["faction"]["example"], "corp")

    def test_search_filters_keys(self):
        rows = self.panel.rows(_ctx(model="objects.objectdb", search="fact"))["rows"]
        self.assertEqual([row["key"] for row in rows], ["faction"])

    def test_category_filter(self):
        ObjectDB.objects.filter(pk=self.a.pk).update(
            db_attrs={NULL_CATEGORY: {DATA: {"hp": 1}}, "static": {DATA: {"bio": "x"}}}
        )
        rows = self.panel.rows(_ctx(model="objects.objectdb", category="static"))["rows"]
        self.assertEqual([row["key"] for row in rows], ["bio"])

    def test_sample_is_declared_complete_when_it_read_everything(self):
        sample = self.panel.rows(_ctx(model="objects.objectdb"))["sample"]
        self.assertTrue(sample["complete"])
        self.assertIn("whole table", sample["note"])

    def test_sample_admits_when_it_is_partial(self):
        # A catalogue that read a window must never present itself as a count.
        with patch.object(attrs, "SAMPLE_SIZE", 1):
            sample = self.panel.rows(_ctx(model="objects.objectdb"))["sample"]
        self.assertFalse(sample["complete"])
        self.assertIn("most recent", sample["note"])
        self.assertIn("exact count", sample["note"])

    def test_offers_the_models_that_carry_attributes(self):
        result = self.panel.rows(_ctx(model="objects.objectdb"))
        self.assertIn("accounts.accountdb", result["models"])


class TestObjectDocument(TestCase):
    """One object's document, grouped and measured."""

    def setUp(self):
        self.panel = AttributesPanel()
        self.obj = _object("subject")

    def test_groups_by_category(self):
        ObjectDB.objects.filter(pk=self.obj.pk).update(
            db_attrs={NULL_CATEGORY: {DATA: {"hp": 5}}, "static": {DATA: {"bio": "text"}}}
        )
        result = self.panel.detail(_ctx(model="objects.objectdb"), self.obj.pk)
        names = [group["category"] for group in result["categories"]]
        self.assertEqual(names, ["", "static"])
        self.assertEqual(result["entry_count"], 2)

    def test_reports_document_size(self):
        ObjectDB.objects.filter(pk=self.obj.pk).update(db_attrs=_doc(hp=1))
        result = self.panel.detail(_ctx(model="objects.objectdb"), self.obj.pk)
        self.assertGreater(result["size_bytes"], 0)
        self.assertFalse(result["fat"])

    def test_flags_a_fat_document(self):
        # The operational point of the panel: one large value taxes every
        # unrelated write on the same object.
        ObjectDB.objects.filter(pk=self.obj.pk).update(db_attrs=_doc(bio="x" * 8000))
        result = self.panel.detail(_ctx(model="objects.objectdb"), self.obj.pk)
        self.assertTrue(result["fat"])
        self.assertIn("re-serializes", result["size_note"])

    def test_values_are_truncated_for_transport(self):
        ObjectDB.objects.filter(pk=self.obj.pk).update(db_attrs=_doc(bio="y" * 5000))
        result = self.panel.detail(_ctx(model="objects.objectdb"), self.obj.pk)
        entry = result["categories"][0]["entries"][0]
        self.assertLess(len(entry["value"]), 500)

    def test_missing_object_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.detail(_ctx(model="objects.objectdb"), 999999)

    def test_bad_identifier_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.detail(_ctx(model="objects.objectdb"), "not-a-pk")

    def test_object_with_no_attributes(self):
        ObjectDB.objects.filter(pk=self.obj.pk).update(db_attrs={})
        result = self.panel.detail(_ctx(model="objects.objectdb"), self.obj.pk)
        self.assertEqual(result["entry_count"], 0)
        self.assertEqual(result["categories"], [])


class TestSizes(TestCase):
    """The fat-document report."""

    def setUp(self):
        self.panel = AttributesPanel()

    def test_orders_by_size_and_counts_the_fat_ones(self):
        small = _object("small", _doc(a=1))
        large = _object("large", _doc(a="z" * 9000))
        result = self.panel.sizes(_ctx(), model="objects.objectdb")
        self.assertEqual(result["rows"][0]["id"], large.pk)
        self.assertTrue(result["rows"][0]["fat"])
        self.assertEqual(result["fat_count"], 1)

    def test_declares_its_threshold_and_sample(self):
        result = self.panel.sizes(_ctx(), model="objects.objectdb")
        self.assertEqual(result["threshold_bytes"], attrs.FAT_DOCUMENT_BYTES)
        self.assertIn("documents_read", result["sample"])


class TestBackendDependentQueries(TestCase):
    """PostgreSQL-only features degrade with a stated reason, never silently."""

    def setUp(self):
        self.panel = AttributesPanel()

    def test_key_stats_needs_a_key(self):
        with self.assertRaises(LookupError):
            self.panel.key_stats(_ctx(), model="objects.objectdb")

    def test_find_needs_a_key(self):
        with self.assertRaises(LookupError):
            self.panel.find(_ctx(), model="objects.objectdb")

    def test_key_stats_explains_itself_without_postgres(self):
        with patch.object(attrs, "_is_postgres", return_value=False):
            result = self.panel.key_stats(_ctx(), model="objects.objectdb", key="hp")
        self.assertFalse(result["exact"])
        self.assertIn("PostgreSQL", result["reason"])

    def test_find_explains_itself_without_postgres(self):
        with patch.object(attrs, "_is_postgres", return_value=False):
            result = self.panel.find(_ctx(), model="objects.objectdb", key="hp", value=1)
        self.assertFalse(result["supported"])
        self.assertEqual(result["rows"], [])
        self.assertIn("PostgreSQL", result["reason"])

    def test_find_warns_that_it_scans(self):
        # Needs the real backend: the warning belongs to the branch that
        # actually issues the containment query, and SQLite cannot run it.
        if not attrs._is_postgres():
            self.skipTest("containment queries need PostgreSQL")
        result = self.panel.find(_ctx(), model="objects.objectdb", key="hp", value=1)
        self.assertIn("whole table", result["note"])


class TestDegradedMode(TestCase):
    """The panel reads a plain column, so it survives the game server."""

    def test_panel_does_not_need_the_io_owner(self):
        self.assertFalse(AttributesPanel.needs_io)
