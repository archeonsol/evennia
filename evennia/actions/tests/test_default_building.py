"""Focused tests for native default building actions."""

import ast
import inspect
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from django.test import override_settings
from twisted.internet.defer import Deferred

from evennia.actions.default import python_console, roleplay, script_paging, system
from evennia.actions.default.building import (
    CharacterBuildingRules,
    Copy,
    CpAttr,
    Examine,
    Link,
    SetAttribute,
    SetHome,
    SetObjAlias,
    Unlink,
    Wipe,
)
from evennia.actions.default.building import actions as building_actions_module
from evennia.actions.default.building import attributes as attributes_module
from evennia.actions.default.building import examine as examine_module
from evennia.actions.default.building import rules as rules_module
from evennia.actions.tests.fakes import FakeAccount, FakeChar, FakeObj, dispatch, make_actor
from evennia.authorization.storage import grant_capabilities, principal_refs
from evennia.objects.character import DefaultCharacter
from evennia.objects.exit import DefaultExit
from evennia.objects.models import ObjectDB
from evennia.utils import create
from evennia.utils.ansi import strip_ansi
from evennia.utils.test_resources import EvenniaTest

engine_module = sys.modules["evennia.actions.engine"]


class CategoryAttributes:
    """Small category-aware attribute handler used by building tests."""

    def __init__(self, values=None):
        self.values = dict(values or {})

    def has(self, key, category=None):
        return (key, category) in self.values

    def get(
        self,
        key=None,
        default=None,
        category=None,
        raise_exception=False,
        return_obj=False,
        return_list=False,
    ):
        marker = (key, category)
        if return_obj and key is not None:
            if marker not in self.values:
                return default
            return SimpleNamespace(key=key, value=self.values[marker], strvalue=None)
        if return_obj:
            return [
                SimpleNamespace(key=attr_key) for attr_key, cat in self.values if cat == category
            ]
        if marker not in self.values:
            if raise_exception:
                raise AttributeError(key)
            return default
        return self.values[marker]

    def add(self, key, value, category=None, strattr=False):
        self.values[(key, category)] = value

    def remove(self, key, category=None):
        self.values.pop((key, category), None)

    def clear(self, category=None):
        if category is None:
            self.values.clear()
        else:
            self.values = {k: v for k, v in self.values.items() if k[1] != category}

    def all(self):
        return []


class Aliases:
    """Category-aware alias handler."""

    def __init__(self):
        self.values = []

    def all(self, return_key_and_category=False):
        return list(self.values) if return_key_and_category else [key for key, _ in self.values]

    def get(self, category=None, return_list=False):
        return [key for key, cat in self.values if cat == category]

    def add(self, aliases, category=None):
        for alias in aliases if isinstance(aliases, list) else [aliases]:
            if (alias, category) not in self.values:
                self.values.append((alias, category))

    def remove(self, key=None, category=None):
        self.values = [pair for pair in self.values if pair != (key, category)]

    def clear(self):
        self.values.clear()

    def __str__(self):
        return ", ".join(key for key, _ in self.values)


class Builder(CharacterBuildingRules, FakeChar):
    """Character provider with no implicit account authority."""


class Account(FakeAccount):
    """Account fixture whose capability result can model quelling."""

    def __init__(self, capabilities=(), quelled=False):
        super().__init__()
        self.capabilities = set(capabilities)
        self.quelled = quelled

    def has_capability(self, capability):
        return not self.quelled and capability in self.capabilities


def setup_builder(account_caps=("engine.world.build",), body_caps=(), quelled=False):
    """Build an actor/provider pair with separate account/body grants."""
    char = Builder(capabilities=body_caps)
    account = Account(account_caps, quelled=quelled)
    char.account = account
    actor = make_actor(char, account=account)
    return char, account, actor


class TestBuildingAuthorization(unittest.TestCase):
    """Account gates and resource checks compose independently."""

    def _set(self, char, actor, raw):
        return dispatch(SetAttribute.parse(raw, actor, verb="@set"), actor, [char])

    def test_all_building_actions_use_default_character_primary_handler(self):
        for action_type in (
            SetAttribute,
            SetObjAlias,
            Copy,
            CpAttr,
            Link,
            Unlink,
            SetHome,
            Wipe,
            Examine,
        ):
            self.assertIs(action_type.__primary_handler__, DefaultCharacter)

    def test_account_capability_allows_mutation(self):
        char, _, actor = setup_builder()
        obj = FakeObj("box")
        obj.attributes = CategoryAttributes()
        char.search_map["box"] = obj
        self._set(char, actor, "box/count = 3")
        self.assertEqual(obj.attributes.get("count"), 3)

    def test_body_only_capability_is_denied(self):
        char, _, actor = setup_builder(account_caps=(), body_caps=("engine.world.build",))
        obj = FakeObj("box")
        obj.attributes = CategoryAttributes()
        char.search_map["box"] = obj
        trace = self._set(char, actor, "box/count = 3")
        self.assertFalse(obj.attributes.has("count"))
        self.assertGreaterEqual(trace.carry_out_gated, 1)

    def test_quelled_account_capability_is_denied(self):
        char, _, actor = setup_builder(quelled=True)
        trace = self._set(char, actor, "box/count = 3")
        self.assertGreaterEqual(trace.carry_out_gated, 1)

    def test_object_level_edit_denial_blocks_mutation(self):
        char, _, actor = setup_builder()
        obj = FakeObj("box", access={"control": False, "edit": False})
        obj.attributes = CategoryAttributes()
        char.search_map["box"] = obj
        self._set(char, actor, "box/count = 3")
        self.assertFalse(obj.attributes.has("count"))
        self.assertIn("permission", "\n".join(map(str, char.messages)))

    def test_attribute_read_requires_object_examine_or_control(self):
        char, _, actor = setup_builder()
        obj = FakeObj("box", access={"examine": False, "control": False})
        obj.attributes = CategoryAttributes({("secret", None): "hidden"})
        char.search_map["box"] = obj
        self._set(char, actor, "box/secret")
        self.assertNotIn("hidden", "\n".join(map(str, char.messages)))


class TestSetAndAlias(unittest.TestCase):
    """Important typed, nested, category, alias, and editor paths."""

    def setUp(self):
        self.char, _, self.actor = setup_builder()
        self.obj = FakeObj("box")
        self.obj.attributes = CategoryAttributes({("data", "meta"): {"items": [1]}})
        self.obj.aliases = Aliases()
        self.char.search_map["box"] = self.obj

    def _set(self, raw, switches=()):
        action = SetAttribute.parse(raw, self.actor, switches=switches, verb="@set")
        return dispatch(action, self.actor, [self.char])

    def test_category_and_nested_append(self):
        self._set("box/data['items'][+] : meta = 2")
        self.assertEqual(self.obj.attributes.get("data", category="meta"), {"items": [1, 2]})

    def _view_desc(self):
        desc = "|rA red coat|n " + "with long seams " * 10 + "fits $you()."
        self.obj.attributes.values[("desc", None)] = desc
        self._set("box/desc")
        return "\n".join(map(str, self.char.messages))

    @override_settings(FUNCPARSER_PARSE_OUTGOING_MESSAGES_ENABLED=False)
    def test_view_shows_whole_value_with_color_escaped(self):
        shown = self._view_desc()
        self.assertIn("||rA red coat||n " + "with long seams " * 10 + "fits $you().", shown)

    @override_settings(FUNCPARSER_PARSE_OUTGOING_MESSAGES_ENABLED=True)
    def test_view_escapes_func_calls_when_outgoing_text_is_parsed(self):
        self.assertIn(r"fits \$you().", self._view_desc())

    @override_settings(FUNCPARSER_PARSE_OUTGOING_MESSAGES_ENABLED=False)
    def test_set_confirmation_shows_markup_as_typed(self):
        self._set('box/desc = "A |rred|n coat"')
        self.assertIn("= A ||rred||n coat", "\n".join(map(str, self.char.messages)))

    def test_unparseable_literal_is_stored_as_text_not_an_error(self):
        self._set("box/x = {[1]: 2}")
        self.assertEqual(self.obj.attributes.get("x"), "{[1]: 2}")

    def test_list_index_out_of_range_is_reported(self):
        self.obj.attributes.add("items", [1, 2])
        self._set("box/items[5] = 3")
        self.assertEqual(self.obj.attributes.get("items"), [1, 2])
        self.assertIn("Could not set box/", "\n".join(map(str, self.char.messages)))

    def test_wipe_with_category_wipes_only_that_category(self):
        self.obj.attributes.add("hp", 5)
        self.obj.attributes.add("hp", 9, category="stats")
        self.obj.attributes.add("str", 3, category="stats")
        dispatch(Wipe.parse("box:stats", self.actor, verb="@wipe"), self.actor, [self.char])
        self.assertEqual(self.obj.attributes.get("hp"), 5)
        self.assertFalse(self.obj.attributes.has("str", category="stats"))

    def test_wipe_named_attribute_in_category(self):
        self.obj.attributes.add("hp", 5)
        self.obj.attributes.add("hp", 9, category="stats")
        dispatch(Wipe.parse("box/hp:stats", self.actor, verb="@wipe"), self.actor, [self.char])
        self.assertEqual(self.obj.attributes.get("hp"), 5)
        self.assertFalse(self.obj.attributes.has("hp", category="stats"))

    def test_wipe_reports_a_missing_attribute(self):
        dispatch(Wipe.parse("box/nothing", self.actor, verb="@wipe"), self.actor, [self.char])
        self.assertIn("has no attribute nothing", "\n".join(map(str, self.char.messages)))

    def test_alias_delete_without_name_is_refused(self):
        self.obj.aliases.add(["crate", "chest"])
        dispatch(
            SetObjAlias.parse("box", self.actor, switches=("delete",), verb="@alias"),
            self.actor,
            [self.char],
        )
        self.assertEqual(self.obj.aliases.all(), ["crate", "chest"])
        self.assertIn("Usage: alias/delete", "\n".join(map(str, self.char.messages)))

    def test_alias_delete_ignores_case(self):
        self.obj.aliases.add(["crate"])
        dispatch(
            SetObjAlias.parse("box = Crate", self.actor, switches=("delete",), verb="@alias"),
            self.actor,
            [self.char],
        )
        self.assertEqual(self.obj.aliases.all(), [])

    def test_dbref_search_value_requires_control(self):
        target = FakeObj("target", access={"control": False, "edit": False})
        with mock.patch.object(rules_module, "parse_attribute_value", return_value=target):
            self._set("box/ref = $dbref(#3)")
        self.assertFalse(self.obj.attributes.has("ref"))

    def test_alias_category_and_delete(self):
        action = SetObjAlias.parse(
            "box = crate, chest:building",
            self.actor,
            switches=("category",),
            verb="@alias",
        )
        dispatch(action, self.actor, [self.char])
        self.assertEqual(
            self.obj.aliases.get(category="building", return_list=True), ["crate", "chest"]
        )
        action = SetObjAlias.parse("box = crate", self.actor, switches=("delete",), verb="@alias")
        dispatch(action, self.actor, [self.char])
        self.assertEqual(self.obj.aliases.get(category="building", return_list=True), ["chest"])

    def test_non_string_edit_cancel_does_not_mutate(self):
        self.obj.attributes.add("count", 3)
        for reply in ("no", "cancel", "q"):
            with self.subTest(reply=reply):
                answer = Deferred()
                answer.callback(reply)
                with (
                    mock.patch.object(engine_module, "_get_input_future", return_value=answer),
                    mock.patch.object(rules_module, "EvEditor") as editor,
                ):
                    self._set("box/count", switches=("edit",))
                self.assertEqual(self.obj.attributes.get("count"), 3)
                editor.assert_not_called()

    def test_non_string_edit_yes_only_persists_on_editor_save(self):
        self.obj.attributes.add("count", 3)
        answer = Deferred()
        answer.callback("yes")
        with (
            mock.patch.object(engine_module, "_get_input_future", return_value=answer),
            mock.patch.object(rules_module, "EvEditor") as editor,
        ):
            self._set("box/count", switches=("edit",))
        self.assertEqual(self.obj.attributes.get("count"), 3)
        save = editor.call_args.kwargs["savefunc"]
        save(self.char, "changed")
        self.assertEqual(self.obj.attributes.get("count"), "changed")

    def test_non_string_edit_enter_takes_the_default_yes(self):
        self.obj.attributes.add("count", 3)
        answer = Deferred()
        answer.callback("")
        with (
            mock.patch.object(engine_module, "_get_input_future", return_value=answer),
            mock.patch.object(rules_module, "EvEditor") as editor,
        ):
            self._set("box/count", switches=("edit",))
        editor.assert_called_once()

    def test_editor_save_rechecks_account_and_object_authority(self):
        self.obj.attributes.add("note", "old")
        with mock.patch.object(rules_module, "EvEditor") as editor:
            self._set("box/note", switches=("edit",))
        save = editor.call_args.kwargs["savefunc"]
        self.char.account.quelled = True
        save(self.char, "new")
        self.assertEqual(self.obj.attributes.get("note"), "old")


class TestLinkAndExamine(unittest.TestCase):
    """Link resource checks and cmdset-free examine output."""

    def test_link_and_unlink_are_native_mutations(self):
        char, _, actor = setup_builder()
        exit_obj = FakeObj("door")
        exit_obj.dbref = "#2"
        exit_obj.destination = None
        room = FakeObj("room")
        char.search_map.update({"door": exit_obj, "room": room})
        dispatch(Link.parse("door = room", actor, verb="@link"), actor, [char])
        self.assertIs(exit_obj.destination, room)

    def test_sethome_requires_authority_over_destination(self):
        char, _, actor = setup_builder()
        obj = FakeObj("box")
        room = FakeObj("vault", access={"control": False, "edit": False})
        char.search_map.update({"box": obj, "vault": room})
        dispatch(SetHome.parse("box = vault", actor, verb="@sethome"), actor, [char])
        self.assertIsNone(obj.home)


class TestCpAttrMove(unittest.TestCase):
    """Moving attributes snapshots values and removes sources only after writes."""

    def test_move_to_multiple_targets_preserves_value_for_each(self):
        char, _, actor = setup_builder()
        source = FakeObj("source")
        first = FakeObj("first")
        second = FakeObj("second")
        source.attributes = CategoryAttributes({("value", None): 7})
        first.attributes = CategoryAttributes()
        second.attributes = CategoryAttributes()
        char.search_map.update({"source": source, "first": first, "second": second})
        action = CpAttr.parse(
            "source/value = first/value, second/value",
            actor,
            switches=("move",),
            verb="@cpattr",
        )
        dispatch(action, actor, [char])
        self.assertEqual(first.attributes.get("value"), 7)
        self.assertEqual(second.attributes.get("value"), 7)
        self.assertFalse(source.attributes.has("value"))

    def test_move_between_categories_removes_source_category(self):
        char, _, actor = setup_builder()
        obj = FakeObj("box")
        obj.attributes = CategoryAttributes({("value", "old"): 7})
        char.search_map["box"] = obj
        action = CpAttr.parse(
            "box/value:old = box/value:new",
            actor,
            switches=("move",),
            verb="@cpattr",
        )
        dispatch(action, actor, [char])
        self.assertFalse(obj.attributes.has("value", "old"))
        self.assertEqual(obj.attributes.get("value", category="new"), 7)

    def test_examine_requires_account_capability_and_has_no_cmdsets(self):
        char, account, actor = setup_builder(account_caps=("engine.object.examine",))
        obj = FakeObj("box")
        obj.dbref = "#4"
        obj.typeclass_path = "game.Box"
        obj.typename = "Box"
        obj.attributes = CategoryAttributes()
        obj.aliases = Aliases()
        obj.policies = SimpleNamespace(all=lambda: {})
        obj.tags = SimpleNamespace(all=lambda **kwargs: [])
        obj.scripts = SimpleNamespace(all=lambda: [])
        obj.cmdset = SimpleNamespace(all=lambda: ["must not inspect"])
        char.search_map["box"] = obj
        with mock.patch("evennia.actions.default.building.examine.load_grants") as grants:
            grants.return_value = SimpleNamespace(by_capability={})
            dispatch(Examine.parse("box", actor, verb="@examine"), actor, [char])
        output = "\n".join(str(message) for message in char.messages)
        self.assertIn("Name/key", output)
        self.assertNotIn("Cmdset", output)
        self.assertNotIn("Commands available", output)

    def test_new_native_modules_have_no_legacy_default_imports(self):
        modules = (
            building_actions_module,
            rules_module,
            attributes_module,
            examine_module,
            roleplay,
            system,
            python_console,
            script_paging,
        )
        for module in modules:
            tree = ast.parse(inspect.getsource(module))
            imports = [
                node.module
                for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module
            ]
            self.assertFalse(
                any(name.startswith("evennia.commands.default") for name in imports),
                module.__name__,
            )


class BuildingCharacter(CharacterBuildingRules, DefaultCharacter):
    """A database character carrying the default building rules."""


class BuildingDBTest(EvenniaTest):
    """Building actions run by Char, whose account holds the build grants."""

    character_typeclass = BuildingCharacter
    maxDiff = None

    def setUp(self):
        super().setUp()
        grant_capabilities(
            principal_refs(self.char1)[0],
            ["engine.object.edit", "engine.object.control", "engine.object.examine"],
            scope_kind="world",
            scope_key="*",
            provenance="test",
        )

    def run_action(self, action_cls, raw, switches=()):
        """Dispatch one building action and return what Char was sent, markup stripped."""
        actor = make_actor(self.char1, account=self.account)
        action = action_cls.parse(raw, actor, switches=switches, verb="@build")
        self.char1.msg = mock.Mock()
        try:
            dispatch(action, actor, [self.char1])
            shown = []
            for call in self.char1.msg.call_args_list:
                text = call.kwargs.get("text", call.args[0] if call.args else "")
                shown.append(strip_ansi(str(text[0] if isinstance(text, tuple) else text)))
        finally:
            del self.char1.msg
        return "\n".join(shown)


class TestExamineOnTheDatabase(BuildingDBTest):
    """``@examine`` targets, attribute views and the markers it adds."""

    def _examine(self, raw=""):
        return self.run_action(Examine, raw)

    def test_no_argument_examines_the_location(self):
        self.assertIn("Name/key: Room", self._examine())

    def test_object_account_and_exit(self):
        self.assertIn("Name/key: Obj", self._examine("Obj"))
        self.assertIn("Name/key: TestAccount", self._examine("*TestAccount"))
        self.assertIn("Name/key: out", self._examine("out"))

    def test_not_found(self):
        self.assertEqual(self._examine("NotFound"), "Could not find 'NotFound'.")

    def test_attribute_view(self):
        self.char1.db.test = "testval"
        self.assertEqual(
            self._examine("self/test"), "Attribute Char/test [category=None]:\n\ntestval"
        )

    @override_settings(FUNCPARSER_PARSE_OUTGOING_MESSAGES_ENABLED=True)
    def test_attribute_view_escapes_func_calls(self):
        self.char1.db.test2 = "this is a $random() value."
        self.assertEqual(
            self._examine("self/test2"),
            "Attribute Char/test2 [category=None]:\n\nthis is a \\$random() value.",
        )

    def test_attribute_view_marks_a_string_value(self):
        self.char1.attributes.add("strattr", "testval", strattr=True)
        self.assertEqual(
            self._examine("self/strattr"),
            "Attribute Char/strattr [category=None] [strvalue]:\n\ntestval",
        )

    def test_scripts_are_listed(self):
        self.room1.scripts.add(self.script.__class__)
        self.assertIn("Scripts: ", self._examine())
        self.account.scripts.add(self.script.__class__)
        shown = self._examine("*TestAccount")
        self.assertIn("Name/key: TestAccount", shown)
        self.assertIn("Scripts: ", shown)


class TestAliasOnTheDatabase(BuildingDBTest):
    """``@alias`` views, sets and clears stored aliases."""

    def _alias(self, raw):
        return self.run_action(SetObjAlias, raw)

    def test_usage_and_not_found(self):
        self.assertEqual(self._alias(""), "Usage: alias <obj> [= [alias[,alias ...]]]")
        self.assertEqual(self._alias("NotFound ="), "Could not find 'NotFound'.")

    def test_set_show_and_clear(self):
        self.assertEqual(self._alias("Obj = TestObj1b"), "Alias(es) for 'Obj' set to 'testobj1b'.")
        self.assertEqual(self._alias("Obj"), "Aliases for Obj: 'testobj1b'")
        self.assertEqual(self._alias("Obj ="), "Cleared aliases from Obj: testobj1b")
        self.assertEqual(self.obj1.aliases.all(), [])
        self.assertEqual(self._alias("Obj ="), "No aliases to clear.")

    def test_delete_removes_the_alias_from_every_category(self):
        self.obj1.aliases.add("testobj1b")
        self.obj1.aliases.add("testobj1b", category="other")
        self.run_action(SetObjAlias, "Obj = testobj1b", switches=("delete",))
        self.assertEqual(self.obj1.aliases.all(return_key_and_category=True), [])


class TestCopyOnTheDatabase(BuildingDBTest):
    """``@copy`` makes new stored objects."""

    def test_copy_to_named_targets_with_aliases(self):
        shown = self.run_action(Copy, "Obj = TestObj2;TestObj2b, TestObj3;TestObj3b")
        self.assertEqual(
            shown,
            "Copied Obj to 'TestObj2' (aliases: ['TestObj2b']).\n"
            "Copied Obj to 'TestObj3' (aliases: ['TestObj3b']).",
        )
        for key, alias in (("TestObj2", "testobj2b"), ("TestObj3", "testobj3b")):
            (copy,) = ObjectDB.objects.filter(db_key=key)
            self.assertEqual([a.lower() for a in copy.aliases.all()], [alias])
            self.assertEqual(copy.location, self.room1)

    def test_identical_copy(self):
        self.assertEqual(
            self.run_action(Copy, "Obj"), "Identical copy of Obj, named 'Obj_copy' was created."
        )
        self.assertTrue(ObjectDB.objects.filter(db_key="Obj_copy").exists())

    def test_usage_and_not_found(self):
        self.assertEqual(
            self.run_action(Copy, ""),
            "Usage: copy <obj> [=<new_name>[;alias;alias..]][:<new_location>] [, <new_name2>...]",
        )
        self.assertEqual(self.run_action(Copy, "NotFound = Foo"), "Could not find 'NotFound'.")
        self.assertFalse(ObjectDB.objects.filter(db_key="Foo").exists())


class TestWipeOnTheDatabase(BuildingDBTest):
    """``@wipe`` removes named or all stored attributes."""

    def test_usage(self):
        self.assertEqual(self.run_action(Wipe, ""), "Usage: wipe <object>[/<attr>/<attr>...]")

    def test_wipe_named_then_all(self):
        for key in ("test1", "test2", "test3"):
            self.obj2.attributes.add(key, key)
        self.assertEqual(
            self.run_action(Wipe, "Obj2/test2/test3"), "Wiped attributes test2,test3 on Obj2."
        )
        self.assertEqual([attr.key for attr in self.obj2.attributes.all()], ["test1"])
        self.assertEqual(self.run_action(Wipe, "Obj2"), "Wiped all attributes on Obj2.")
        self.assertEqual(self.obj2.attributes.all(), [])


class TestSetOnTheDatabase(BuildingDBTest):
    """``@set`` messages and nested index, key and tuple paths on stored attributes."""

    def test_editing_a_string_attribute_keeps_it_a_string_attribute(self):
        self.obj1.attributes.add("s", "old", strattr=True)
        with mock.patch.object(rules_module, "EvEditor") as editor:
            self.run_action(SetAttribute, "Obj/s", switches=("edit",))
        editor.call_args.kwargs["savefunc"](self.char1, "new")
        self.assertEqual(self.obj1.attributes.get("s", strattr=True), "new")

    def test_an_infinity_pastes_back_as_one(self):
        for value in (float("inf"), [float("-inf"), {"a": (float("inf"),)}, {1.5}]):
            with self.subTest(value=value):
                self.obj1.attributes.add("f", value)
                shown = self.run_action(SetAttribute, "Obj/f")
                typed = shown.split(" = ", 1)[1]
                self.run_action(SetAttribute, f"Obj/f = {typed}")
                self.assertEqual(self.obj1.attributes.get("f"), value)

    def test_bytes_are_shown_as_typed(self):
        self.obj1.attributes.add("b", b"x")
        self.assertEqual(
            self.run_action(SetAttribute, "Obj/b"), "Attribute Obj/b [category:None] = b'x'"
        )

    def _steps(self, steps):
        for raw, expected in steps:
            with self.subTest(raw=raw):
                self.assertEqual(self.run_action(SetAttribute, raw), expected)

    def test_usage(self):
        self.assertEqual(
            self.run_action(SetAttribute, ""),
            "Usage: set obj/attr[:category] = value. Use empty value to clear.",
        )

    def test_create_view_delete_and_missing(self):
        self._steps(
            [
                ('Obj/test1="value1"', "Created attribute Obj/test1 [category:None] = value1"),
                ("Obj/test1", "Attribute Obj/test1 [category:None] = value1"),
                ("Obj/NotFound", "Attribute Obj/notfound [category:None] does not exist."),
                ("Obj/test1 = ", "Deleted attribute Obj/test1 [category:None]."),
                ("Obj2/test4:Foo = 'Bar'", "Created attribute Obj2/test4 [category:foo] = Bar"),
            ]
        )
        self.assertFalse(self.obj1.attributes.has("test1"))
        self.assertEqual(self.obj2.attributes.get("test4", category="Foo"), "Bar")

    def test_a_string_that_reads_as_something_else_is_shown_quoted(self):
        for typed, shown in (
            ('"123"', "'123'"),
            ('"[1, 2]"', "'[1, 2]'"),
            ('"None"', "'None'"),
            ('"$dbref(#1)"', "'$dbref(#1)'"),
            ("plain words", "plain words"),
        ):
            with self.subTest(typed=typed):
                self.run_action(SetAttribute, f"Obj/test1 = {typed}")
                self.assertEqual(
                    self.run_action(SetAttribute, "Obj/test1"),
                    f"Attribute Obj/test1 [category:None] = {shown}",
                )

    def test_a_string_attribute_is_shown(self):
        self.obj1.attributes.add("test1", "kept text", strattr=True)
        self.assertEqual(
            self.run_action(SetAttribute, "Obj/test1"),
            "Attribute Obj/test1 [category:None] = kept text",
        )

    def test_category_view(self):
        self.obj1.attributes.add("test", "value", category="cat")
        self.obj1.attributes.add("testdict", {"key": "value"}, category="cat")
        self._steps(
            [
                ("Obj/test:cat", "Attribute Obj/test [category:cat] = value"),
                ("Obj/testdict['key']:cat", "Attribute Obj/testdict['key'] [category:cat] = value"),
            ]
        )

    def test_nested_list(self):
        self._steps(
            [
                ("Obj/test1=[1,2]", "Created attribute Obj/test1 [category:None] = [1, 2]"),
                ("Obj/test1", "Attribute Obj/test1 [category:None] = [1, 2]"),
                ("Obj/test1[0]", "Attribute Obj/test1[0] [category:None] = 1"),
                ("Obj/test1[1]", "Attribute Obj/test1[1] [category:None] = 2"),
                ("Obj/test1[0] = 99", "Modified attribute Obj/test1[0] [category:None] = [99, 2]"),
                ("Obj/test1[0]", "Attribute Obj/test1[0] [category:None] = 99"),
                ("Obj/test1[0] =", "Deleted attribute Obj/test1[0] [category:None]."),
                ("Obj/test1[0]", "Attribute Obj/test1[0] [category:None] = 2"),
                (
                    "Obj/test1[1]",
                    "Attribute Obj/test1[1] [category:None] does not exist. "
                    "(Nested lookups attempted)",
                ),
                (
                    "Obj/test1[5] =",
                    "No attribute Obj/test1[5] [category: None] was found to delete. "
                    "(Nested lookups attempted)",
                ),
                ("Obj/test1[+] = 42", "Modified attribute Obj/test1[+] [category:None] = [2, 42]"),
                (
                    "Obj/test1[+0] = -1",
                    "Modified attribute Obj/test1[+0] [category:None] = [-1, 2, 42]",
                ),
            ]
        )
        self.assertEqual(ObjectDB.objects.get(id=self.obj1.id).db.test1, [-1, 2, 42])

    def test_nested_dict(self):
        missing = "does not exist. (Nested lookups attempted)"
        self._steps(
            [
                (
                    "Obj/test2={ 'one': 1, 'two': 2 }",
                    "Created attribute Obj/test2 [category:None] = {'one': 1, 'two': 2}",
                ),
                ("Obj/test2", "Attribute Obj/test2 [category:None] = {'one': 1, 'two': 2}"),
                ("Obj/test2['one']", "Attribute Obj/test2['one'] [category:None] = 1"),
                ("Obj/test2['one]", "Attribute Obj/test2['one] [category:None] = 1"),
                (
                    "Obj/test2['one']=99",
                    "Modified attribute Obj/test2['one'] [category:None] = {'one': 99, 'two': 2}",
                ),
                ("Obj/test2['one']", "Attribute Obj/test2['one'] [category:None] = 99"),
                ("Obj/test2['two']", "Attribute Obj/test2['two'] [category:None] = 2"),
                ("Obj/test2[+'three']", f"Attribute Obj/test2[+'three'] [category:None] {missing}"),
                (
                    "Obj/test2[+'three'] = 3",
                    "Modified attribute Obj/test2[+'three'] [category:None] = "
                    "{'one': 99, 'two': 2, \"+'three'\": 3}",
                ),
                ("Obj/test2[+'three'] =", "Deleted attribute Obj/test2[+'three'] [category:None]."),
                (
                    "Obj/test2['three']=3",
                    "Modified attribute Obj/test2['three'] [category:None] = "
                    "{'one': 99, 'two': 2, 'three': 3}",
                ),
                ("Obj/test2['two'] =", "Deleted attribute Obj/test2['two'] [category:None]."),
                ("Obj/test2['two']", f"Attribute Obj/test2['two'] [category:None] {missing}"),
                ("Obj/test2", "Attribute Obj/test2 [category:None] = {'one': 99, 'three': 3}"),
                ("Obj/test2[0]", f"Attribute Obj/test2[0] [category:None] {missing}"),
                (
                    "Obj/test2['five'] =",
                    "No attribute Obj/test2['five'] [category: None] was found to delete. "
                    "(Nested lookups attempted)",
                ),
                (
                    "Obj/test2[+]=42",
                    "Modified attribute Obj/test2[+] [category:None] = "
                    "{'one': 99, 'three': 3, '+': 42}",
                ),
                (
                    "Obj/test2[+1]=33",
                    "Modified attribute Obj/test2[+1] [category:None] = "
                    "{'one': 99, 'three': 3, '+': 42, '+1': 33}",
                ),
            ]
        )
        self.assertEqual(
            ObjectDB.objects.get(id=self.obj1.id).db.test2,
            {"one": 99, "three": 3, "+": 42, "+1": 33},
        )

    def test_nested_dict_keys_keep_their_case(self):
        self._steps(
            [
                (
                    "Obj/test_case = {'FooBar': 1}",
                    "Created attribute Obj/test_case [category:None] = {'FooBar': 1}",
                ),
                (
                    "Obj/test_case['FooBar'] = 2",
                    "Modified attribute Obj/test_case['FooBar'] [category:None] = {'FooBar': 2}",
                ),
                ("Obj/test_case", "Attribute Obj/test_case [category:None] = {'FooBar': 2}"),
                (
                    "Obj/test_case['FooBar'] = {'BarBaz': 1}",
                    "Modified attribute Obj/test_case['FooBar'] [category:None] = {'FooBar': {'BarBaz': 1}}",
                ),
                (
                    "Obj/test_case['FooBar']['BarBaz'] = 2",
                    "Modified attribute Obj/test_case['FooBar']['BarBaz'] [category:None] = {'FooBar': {'BarBaz': 2}}",
                ),
                (
                    "Obj/test_case",
                    "Attribute Obj/test_case [category:None] = {'FooBar': {'BarBaz': 2}}",
                ),
            ]
        )

    def test_nested_tuple_is_not_changed(self):
        refused = "'tuple' object does not support item assignment."
        self._steps(
            [
                ("Obj/tup = (1,2)", "Created attribute Obj/tup [category:None] = (1, 2)"),
                ("Obj/tup[1] = 99", f"Could not set Obj/tup[1]: {refused}"),
                ("Obj/tup[+] = 99", f"Could not set Obj/tup[+]: {refused}"),
                ("Obj/tup[+1] = 99", f"Could not set Obj/tup[+1]: {refused}"),
                (
                    "Obj/tup[1] = ",
                    "No attribute Obj/tup[1] [category: None] was found to delete. "
                    "(Nested lookups attempted)",
                ),
            ]
        )
        self.assertEqual(self.obj1.db.tup, (1, 2))

    def test_deeper_nesting(self):
        self._steps(
            [
                (
                    "Obj/test3=[{'one': 1}]",
                    "Created attribute Obj/test3 [category:None] = [{'one': 1}]",
                ),
                ("Obj/test3[0]['one']", "Attribute Obj/test3[0]['one'] [category:None] = 1"),
                ("Obj/test3[0]", "Attribute Obj/test3[0] [category:None] = {'one': 1}"),
                ("Obj/test3[0]['one'] =", "Deleted attribute Obj/test3[0]['one'] [category:None]."),
                ("Obj/test3[0]", "Attribute Obj/test3[0] [category:None] = {}"),
                ("Obj/test3", "Attribute Obj/test3 [category:None] = [{}]"),
            ]
        )
        self.assertEqual(ObjectDB.objects.get(id=self.obj1.id).db.test3, [{}])

    def test_bracketed_key_and_nested_value_coexist(self):
        self._steps(
            [
                ("Obj/test4[0]='foo'", "Created attribute Obj/test4[0] [category:None] = foo"),
                ("Obj/test4[0]", "Attribute Obj/test4[0] [category:None] = foo"),
                (
                    "Obj/test4=[{'one': 1}]",
                    "Created attribute Obj/test4 [category:None] = [{'one': 1}]",
                ),
                ("Obj/test4[0]['one']", "Attribute Obj/test4[0]['one'] [category:None] = 1"),
                ("Obj/test4[0]", "Attribute Obj/test4[0] [category:None] = {'one': 1}"),
            ]
        )
        self.assertEqual(self.run_action(Wipe, "Obj/test4"), "Wiped attributes test4 on Obj.")
        self._steps(
            [
                ("Obj/test4[0]", "Attribute Obj/test4[0] [category:None] = foo"),
                (
                    "Obj/test4[0]['one']",
                    "Attribute Obj/test4[0]['one'] [category:None] does not exist. "
                    "(Nested lookups attempted)",
                ),
            ]
        )


class TestCpAttrOnTheDatabase(BuildingDBTest):
    """``@cpattr`` copies stored attributes between objects."""

    def test_usage(self):
        self.assertEqual(
            self.run_action(CpAttr, ""),
            "Usage: cpattr[/move] <obj>/<attr>[:category] = <obj>/<attr>",
        )

    def test_copy_keeps_the_source(self):
        self.obj1.db.test1 = "value1"
        self.assertEqual(
            self.run_action(CpAttr, "Obj/test1 = Obj2/test3"),
            "\nCopied Obj.test1 -> Obj2.test3. (value: 'value1')",
        )
        self.assertEqual(self.obj1.db.test1, "value1")
        self.assertEqual(self.obj2.db.test3, "value1")

    def test_move_to_the_same_slot_in_another_case_keeps_the_value(self):
        self.obj1.attributes.add("a", "keep", category="cat")
        self.run_action(CpAttr, "Obj/a:Cat = Obj/a:cat", switches=("move",))
        self.assertEqual(self.obj1.attributes.get("a", category="cat"), "keep")

    def test_a_string_attribute_is_copied(self):
        self.obj1.attributes.add("s", "kept text", strattr=True)
        self.run_action(CpAttr, "Obj/s = Obj2/s")
        self.assertEqual(self.obj2.attributes.get("s", strattr=True), "kept text")

    def test_move_swap_keeps_both_values(self):
        self.obj1.attributes.add("x", "X")
        self.obj1.attributes.add("y", "Y")
        self.run_action(CpAttr, "Obj/x/y = Obj/y/x", switches=("move",))
        self.assertEqual((self.obj1.db.x, self.obj1.db.y), ("Y", "X"))

    def test_copy_swap_keeps_both_values(self):
        self.obj1.attributes.add("x", "X")
        self.obj1.attributes.add("y", "Y")
        self.run_action(CpAttr, "Obj/x/y = Obj/y/x")
        self.assertEqual((self.obj1.db.x, self.obj1.db.y), ("Y", "X"))

    def test_a_chain_copies_each_value_as_it_was(self):
        self.obj1.attributes.add("a", "A")
        self.obj1.attributes.add("b", "B")
        self.run_action(CpAttr, "Obj/a/b = Obj/b/c")
        self.assertEqual((self.obj1.db.b, self.obj1.db.c), ("A", "B"))

    def test_unknown_switch_changes_nothing(self):
        self.obj2.db.test2 = "value2"
        shown = self.run_action(CpAttr, "Obj2/test2 = Obj2/test3", switches=("mvoe",))
        self.assertEqual(shown, "Unknown switch /mvoe. The only switch is /move.")
        self.assertEqual(self.obj2.db.test2, "value2")
        self.assertIsNone(self.obj2.db.test3)


class TestNestedAttributeHelpers(unittest.TestCase):
    """The split and lookup helpers behind nested ``@set`` paths."""

    def test_split_nested_attr(self):
        cases = {
            "test1": [("test1", [])],
            'test2["dict"]': [("test2", ["dict"]), ('test2["dict"]', [])],
            "test3[dict]": [("test3", ["dict"]), ("test3[dict]", [])],
            'test4["dict]': [("test4", ["dict"]), ('test4["dict]', [])],
            "test5[0][0]": [("test5", [0, 0]), ("test5[0]", [0]), ("test5[0][0]", [])],
            'test6["0"][0]': [("test6", ["0", 0]), ('test6["0"]', [0]), ('test6["0"][0]', [])],
            "test7[dict": [("test7[dict", [])],
        }
        for attr, expected in cases.items():
            with self.subTest(attr=attr):
                self.assertEqual(
                    list(attributes_module.AttributeOperations.split_nested_attr(attr)), expected
                )

    def test_nested_lookup(self):
        lookup = attributes_module.AttributeOperations.nested_lookup
        missing = attributes_module._NOT_FOUND
        cases = [
            ([], ("test1",), missing),
            ([1], ("test2",), missing),
            ([], (0,), missing),
            ([], ("0",), missing),
            ([1], (2,), missing),
            ([1], (0,), 1),
            ([1], ("0",), missing),
            ({}, ("test3",), missing),
            ({}, (0,), missing),
            ({"foo": "bar"}, ("foo",), "bar"),
            ({"one": [1, 2, 3]}, ("one", 0), 1),
            ([{}, {"two": 2}, 3], (1, "two"), 2),
        ]
        for value, keys, expected in cases:
            with self.subTest(value=value, keys=keys):
                self.assertEqual(lookup(value, *keys), expected)


class TestLinkOnTheDatabase(BuildingDBTest):
    """``@link`` and ``@unlink`` against stored exits and rooms."""

    def setUp(self):
        super().setUp()
        self.exit1 = create.create_object(
            DefaultExit, key="TestExit1", location=self.room1, destination=self.room2
        )

    def test_one_way_link_show_and_unlink(self):
        self.assertEqual(
            self.run_action(Link, "TestExit1=Room"), "Link created TestExit1 -> Room (one way)."
        )
        self.assertEqual(self.exit1.destination, self.room1)
        self.assertEqual(self.run_action(Link, "TestExit"), "TestExit1 is an exit to Room.")
        self.assertEqual(
            self.run_action(Link, "Obj"), "Obj is not an exit. Its home location is Room."
        )
        self.assertEqual(
            self.run_action(Unlink, "TestExit1"), "Former exit TestExit1 no longer links anywhere."
        )
        self.assertIsNone(self.exit1.destination)

    def test_usage_and_not_found(self):
        self.assertEqual(self.run_action(Unlink, ""), "Usage: unlink <object>")
        self.assertEqual(self.run_action(Link, ""), "Usage: link[/twoway] <object> = <target>")
        self.assertEqual(self.run_action(Link, "NotFound"), "Could not find 'NotFound'.")

    def test_twoway_self_link_and_global_match(self):
        self.char1.location = self.room2
        exit2 = create.create_object(
            DefaultExit, key="TestExit2", location=self.room2, destination=self.room1
        )
        self.assertEqual(
            self.run_action(Link, "TestExit=Room2"), "Link created TestExit2 -> Room2 (one way)."
        )
        self.assertEqual(exit2.destination, self.room2)
        self.assertEqual(
            self.run_action(Link, f"TestExit={self.exit.dbref}", switches=("twoway",)),
            "Link created TestExit2 (in Room2) <-> out (in Room) (two-way).",
        )
        self.assertEqual(exit2.destination, self.room1)
        self.assertEqual(self.exit.destination, self.room2)
        self.assertEqual(
            self.run_action(Link, f"TestExit={self.room1.dbref}", switches=("twoway",)),
            "To create a two-way link, TestExit2 and Room must both have a location "
            "(i.e. they cannot be rooms, but should be exits).",
        )
        self.assertEqual(
            self.run_action(Link, f"{self.exit.dbref}={self.exit.dbref}", switches=("twoway",)),
            "Cannot link an object to itself.",
        )
        self.exit1.destination = None
        self.assertEqual(
            self.run_action(Link, "TestExit1=Room2"),
            f"Note: TestExit1({self.exit1.dbref}) did not have a destination set before. "
            "Make sure you linked the right thing.\n"
            "Link created TestExit1 -> Room2 (one way).",
        )
        self.assertEqual(self.exit1.destination, self.room2)
        self.assertEqual(
            self.run_action(Link, "TestExit1="), "Former exit TestExit1 no longer links anywhere."
        )
        self.assertIsNone(self.exit1.destination)


class TestSetHomeOnTheDatabase(BuildingDBTest):
    """``@sethome`` shows and changes stored homes."""

    def test_usage(self):
        self.assertEqual(self.run_action(SetHome, ""), "Usage: sethome <obj> [= <home_location>]")

    def test_change_show_and_set(self):
        self.assertEqual(
            self.run_action(SetHome, "Obj = Room2"),
            f"Home location of Obj was changed from Room({self.room1.dbref}) "
            f"to Room2({self.room2.dbref}).",
        )
        self.assertEqual(self.obj1.home, self.room2)
        self.assertEqual(
            self.run_action(SetHome, "self"), f"Char's current home is Room({self.room1.dbref})."
        )
        self.assertEqual(
            self.run_action(SetHome, "Obj"), f"Obj's current home is Room2({self.room2.dbref})."
        )
        self.obj1.home = None
        self.assertEqual(
            self.run_action(SetHome, "Obj = Room2"),
            f"Home location of Obj was set to Room2({self.room2.dbref}).",
        )
        self.assertEqual(self.obj1.home, self.room2)


if __name__ == "__main__":
    unittest.main()
