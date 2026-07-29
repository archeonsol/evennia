"""Focused tests for native default building actions."""

import ast
import inspect
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

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
from evennia.objects.character import DefaultCharacter

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

    def clear(self):
        self.values.clear()

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


if __name__ == "__main__":
    unittest.main()
