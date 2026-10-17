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
from evennia.objects.models import ObjectDB
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
        if return_obj:
            return [
                SimpleNamespace(key=attr_key) for attr_key, cat in self.values if cat == category
            ]
        marker = (key, category)
        if marker not in self.values:
            if raise_exception:
                raise AttributeError(key)
            return default
        return self.values[marker]

    def add(self, key, value, category=None):
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

    def test_cpattr_move_swap_keeps_both_values(self):
        self.obj.attributes.add("x", "X")
        self.obj.attributes.add("y", "Y")
        action = CpAttr.parse("box/x/y = box/y/x", self.actor, switches=("move",), verb="@cpattr")
        dispatch(action, self.actor, [self.char])
        self.assertEqual(self.obj.attributes.get("x"), "Y")
        self.assertEqual(self.obj.attributes.get("y"), "X")

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
        answers = iter(["no"])

        def input_future(actor, prompt):
            deferred = Deferred()
            deferred.callback(next(answers))
            return deferred

        with (
            mock.patch.object(engine_module, "_get_input_future", side_effect=input_future),
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
        self.assertIn("Name/key: TestAccount", self._examine("*TestAccount"))


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
                ("Obj2/test4:Foo = 'Bar'", "Created attribute Obj2/test4 [category:Foo] = Bar"),
            ]
        )
        self.assertFalse(self.obj1.attributes.has("test1"))
        self.assertEqual(self.obj2.attributes.get("test4", category="Foo"), "Bar")

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


if __name__ == "__main__":
    unittest.main()
