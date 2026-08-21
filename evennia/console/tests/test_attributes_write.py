"""Tests for the Attributes tree and write path.

Two things this locks in.

**JSON, not a guess.** An operator who types ``123`` means the number and one
who types ``"123"`` means the string. A panel that decides for them stores the
wrong type into a document nothing else validates, and nothing reports it.

**The handler, not the column.** A direct write to ``db_attrs`` would skip the
codec that decides what JSON can carry and what has to be packed, and skip the
invalidation that stops another reader serving the old value.

"""

from django.test import TestCase

from evennia.console.models import ConsoleAuditEvent
from evennia.console.panels.attributes import (
    MAX_TREE_CHILDREN,
    MAX_TREE_DEPTH,
    AttributesPanel,
    _tree,
)
from evennia.console.registry import CONSOLE_ACCESS, IOContext, WorkerContext
from evennia.objects.models import ObjectDB


def _ctx(**params):
    """Build a worker context."""

    return WorkerContext(
        actor_id=1, actor_name="op", capabilities=frozenset({CONSOLE_ACCESS}), params=params
    )


def _io():
    """Build an IO context."""

    return IOContext(actor_id=1, actor_name="op", capabilities=frozenset())


class TestTree(TestCase):
    """Walking a stored value."""

    def test_a_scalar_has_no_children(self):
        node = _tree(42)
        self.assertEqual(node["kind"], "number")
        self.assertEqual(node["children"], [])

    def test_a_dict_is_walked(self):
        node = _tree({"a": 1, "b": {"c": 2}})
        self.assertEqual(node["kind"], "dict")
        names = [child["name"] for child in node["children"]]
        self.assertEqual(sorted(names), ["a", "b"])

    def test_nesting_is_navigable(self):
        # The behaviour P3 asked for and _preview could not give: a nested
        # structure that opens rather than a truncated repr.
        node = _tree({"outer": {"inner": ["x"]}})
        outer = node["children"][0]
        inner = outer["children"][0]
        self.assertEqual(inner["name"], "inner")
        self.assertEqual(inner["kind"], "list")
        self.assertEqual(inner["children"][0]["summary"], "x")

    def test_a_list_names_children_by_index(self):
        node = _tree(["first", "second"])
        self.assertEqual([child["name"] for child in node["children"]], ["0", "1"])

    def test_depth_is_bounded(self):
        deep = current = {}
        for _ in range(MAX_TREE_DEPTH + 4):
            current["next"] = {}
            current = current["next"]
        node = _tree(deep)
        for _ in range(MAX_TREE_DEPTH):
            if not node["children"]:
                break
            node = node["children"][0]
        self.assertIn("not walked past depth", node["summary"])

    def test_breadth_is_bounded_and_the_remainder_is_counted(self):
        node = _tree({str(index): index for index in range(MAX_TREE_CHILDREN + 25)})
        self.assertEqual(len(node["children"]), MAX_TREE_CHILDREN)
        self.assertEqual(node["truncated"], 25)

    def test_a_packed_value_is_named_not_rendered(self):
        # The base64 body of a pickle teaches an operator nothing.
        node = _tree("__P:c29tZXRoaW5n")
        self.assertEqual(node["kind"], "packed")
        self.assertIn("packed", node["summary"])
        self.assertEqual(node["children"], [])


class TestDecode(TestCase):
    """Parsing a submitted value."""

    def setUp(self):
        self.panel = AttributesPanel()

    def test_a_number_stays_a_number(self):
        self.assertEqual(self.panel._decode("123"), 123)

    def test_a_quoted_number_stays_a_string(self):
        self.assertEqual(self.panel._decode('"123"'), "123")

    def test_structures_are_accepted(self):
        self.assertEqual(self.panel._decode('{"a": [1, 2]}'), {"a": [1, 2]})

    def test_null_is_a_value(self):
        self.assertIsNone(self.panel._decode("null"))

    def test_empty_input_is_refused(self):
        with self.assertRaises(ValueError):
            self.panel._decode("   ")

    def test_invalid_json_says_where(self):
        with self.assertRaises(ValueError) as caught:
            self.panel._decode("{not json")
        self.assertIn("position", str(caught.exception))


class TestWrites(TestCase):
    """Set and unset, through the handler."""

    def setUp(self):
        self.panel = AttributesPanel()
        self.obj = ObjectDB.objects.create(db_key="subject")

    def test_setting_creates_the_attribute(self):
        result = self.panel.set(
            _io(), model="objects.ObjectDB", pk=self.obj.pk, key="hp", value="10"
        )
        self.assertTrue(result["created"])
        self.assertEqual(self.obj.attributes.get("hp"), 10)

    def test_setting_again_reports_a_change_and_the_previous_value(self):
        self.panel.set(_io(), model="objects.ObjectDB", pk=self.obj.pk, key="hp", value="10")
        result = self.panel.set(
            _io(), model="objects.ObjectDB", pk=self.obj.pk, key="hp", value="20"
        )
        self.assertFalse(result["created"])
        self.assertEqual(result["before"], "10")
        self.assertEqual(result["after"], "20")

    def test_a_category_is_honoured(self):
        self.panel.set(
            _io(),
            model="objects.ObjectDB",
            pk=self.obj.pk,
            key="note",
            category="staff",
            value='"kept"',
        )
        self.assertEqual(self.obj.attributes.get("note", category="staff"), "kept")
        self.assertIsNone(self.obj.attributes.get("note"))

    def test_a_structure_round_trips(self):
        self.panel.set(
            _io(),
            model="objects.ObjectDB",
            pk=self.obj.pk,
            key="loadout",
            value='{"weapon": "knife", "charges": [1, 2]}',
        )
        self.assertEqual(self.obj.attributes.get("loadout"), {"weapon": "knife", "charges": [1, 2]})

    def test_it_refuses_an_empty_key(self):
        with self.assertRaises(ValueError):
            self.panel.set(_io(), model="objects.ObjectDB", pk=self.obj.pk, key=" ", value="1")

    def test_it_refuses_an_unknown_row(self):
        with self.assertRaises(LookupError):
            self.panel.set(_io(), model="objects.ObjectDB", pk=999999, key="hp", value="1")

    def test_unsetting_removes_the_attribute(self):
        self.panel.set(_io(), model="objects.ObjectDB", pk=self.obj.pk, key="hp", value="10")
        self.panel.unset(_io(), model="objects.ObjectDB", pk=self.obj.pk, key="hp")
        self.assertIsNone(self.obj.attributes.get("hp"))

    def test_unsetting_something_absent_is_reported(self):
        # The handler removes quietly when nothing matches, so an operator who
        # mistyped a key would otherwise be told the removal worked.
        with self.assertRaises(LookupError):
            self.panel.unset(_io(), model="objects.ObjectDB", pk=self.obj.pk, key="never-set")

    def test_the_document_size_is_reported_back(self):
        result = self.panel.set(
            _io(), model="objects.ObjectDB", pk=self.obj.pk, key="bio", value='"x"'
        )
        self.assertGreater(result["size_bytes"], 0)
        self.assertIn("fat", result)


class TestWriteAudit(TestCase):
    """Every write is recorded with both sides."""

    def setUp(self):
        self.panel = AttributesPanel()
        self.obj = ObjectDB.objects.create(db_key="subject")

    def test_a_create_is_recorded(self):
        self.panel.set(_io(), model="objects.ObjectDB", pk=self.obj.pk, key="hp", value="10")
        row = ConsoleAuditEvent.objects.get(panel="attributes")
        self.assertEqual(row.operation, "add")
        self.assertEqual(row.after["value"], "10")
        self.assertFalse(row.before["existed"])

    def test_a_change_records_the_previous_value(self):
        self.panel.set(_io(), model="objects.ObjectDB", pk=self.obj.pk, key="hp", value="10")
        self.panel.set(_io(), model="objects.ObjectDB", pk=self.obj.pk, key="hp", value="20")
        row = ConsoleAuditEvent.objects.filter(operation="change").first()
        self.assertEqual(row.before["value"], "10")
        self.assertEqual(row.after["value"], "20")

    def test_a_removal_records_what_was_there(self):
        self.panel.set(_io(), model="objects.ObjectDB", pk=self.obj.pk, key="hp", value="10")
        self.panel.unset(_io(), model="objects.ObjectDB", pk=self.obj.pk, key="hp")
        row = ConsoleAuditEvent.objects.filter(operation="delete").first()
        self.assertEqual(row.before["value"], "10")

    def test_the_target_names_the_key(self):
        self.panel.set(_io(), model="objects.ObjectDB", pk=self.obj.pk, key="hp", value="10")
        row = ConsoleAuditEvent.objects.get(panel="attributes")
        self.assertTrue(row.target_ref.endswith(":hp"))


class TestBoundary(TestCase):
    """The writes must cross to the IO owner; the reads must not need it."""

    def test_writes_are_marked_io(self):
        # A db_attrs write off the IO thread crashes on PostgreSQL and appears
        # to work on SQLite, so this marking is the difference between a panel
        # that works and one that works until it is deployed.
        from evennia.console.registry import is_io_action

        self.assertTrue(is_io_action(AttributesPanel.set))
        self.assertTrue(is_io_action(AttributesPanel.unset))

    def test_the_panel_still_reads_without_the_io_owner(self):
        self.assertFalse(AttributesPanel.needs_io)


class TestCarriers(TestCase):
    """Which objects hold one key, which is how the catalogue reaches a row."""

    def setUp(self):
        self.panel = AttributesPanel()

    def test_it_lists_the_objects_holding_a_key(self):
        held = ObjectDB.objects.create(db_key="holder")
        ObjectDB.objects.create(db_key="empty")
        held.attributes.add("faction", "corp")
        result = self.panel.carriers(_ctx(), model="objects.ObjectDB", key="faction")
        self.assertEqual([row["id"] for row in result["rows"]], [held.pk])

    def test_it_reports_the_value_each_one_holds(self):
        held = ObjectDB.objects.create(db_key="holder")
        held.attributes.add("faction", "corp")
        row = self.panel.carriers(_ctx(), model="objects.ObjectDB", key="faction")["rows"][0]
        self.assertEqual(row["value"], "corp")
        self.assertEqual(row["kind"], "text")

    def test_a_category_separates_two_keys_of_the_same_name(self):
        held = ObjectDB.objects.create(db_key="holder")
        held.attributes.add("note", "public")
        held.attributes.add("note", "private", category="staff")
        default = self.panel.carriers(_ctx(), model="objects.ObjectDB", key="note")
        staffed = self.panel.carriers(
            _ctx(), model="objects.ObjectDB", key="note", category="staff"
        )
        self.assertEqual(default["rows"][0]["value"], "public")
        self.assertEqual(staffed["rows"][0]["value"], "private")

    def test_nothing_holding_the_key_is_an_empty_list(self):
        result = self.panel.carriers(_ctx(), model="objects.ObjectDB", key="never-set")
        self.assertEqual(result["rows"], [])

    def test_it_refuses_an_empty_key(self):
        with self.assertRaises(LookupError):
            self.panel.carriers(_ctx(), model="objects.ObjectDB", key="  ")

    def test_it_says_how_the_rows_were_found(self):
        # A sampled answer and an indexed answer are not the same claim, and
        # the panel must not let them look alike.
        result = self.panel.carriers(_ctx(), model="objects.ObjectDB", key="faction")
        self.assertTrue(result["note"])
        self.assertIn("complete", result)

    def test_it_does_not_need_the_io_owner(self):
        from evennia.console.registry import is_io_action

        self.assertFalse(is_io_action(AttributesPanel.carriers))


class TestWriteBehindBarrier(TestCase):
    """Where the process-wide flush is paid, and where it deliberately is not."""

    def setUp(self):
        self.panel = AttributesPanel()

    def test_a_point_query_sees_a_write_that_has_not_flushed(self):
        # Attribute writes are write-behind. Without the barrier this query
        # answers from before the write, and an operator who just set a key
        # and cannot find it concludes the write failed.
        held = ObjectDB.objects.create(db_key="holder")
        held.attributes.add("fresh", "value")
        rows = self.panel.carriers(_ctx(), model="objects.ObjectDB", key="fresh")["rows"]
        self.assertEqual([row["id"] for row in rows], [held.pk])

    def test_a_point_query_reports_that_it_flushed(self):
        result = self.panel.carriers(_ctx(), model="objects.ObjectDB", key="anything")
        self.assertTrue(result["barrier"]["flushed"])

    def test_the_catalogue_does_not_flush_and_says_so(self):
        # performance.md treats an attribute search as hostile to game logic
        # because it forces a process-wide flush. A navigation aid an operator
        # leaves open must not carry that on every render.
        barrier = self.panel.rows(_ctx(model="objects.ObjectDB"))["sample"]["barrier"]
        self.assertFalse(barrier["flushed"])
        self.assertIn("recent changes", barrier["reason"])
