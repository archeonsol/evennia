"""Rule providers for the native default building actions."""

from __future__ import annotations

from evennia.objects.models import ObjectDB
from evennia.utils.eveditor import EvEditor

from ...predicate import HasCapability
from ...result import CLAIM, SKIP
from ...rule import rule
from .actions import Copy, CpAttr, Examine, Link, SetAttribute, SetHome, SetObjAlias, Unlink, Wipe
from .attributes import AttributeOperations, parse_attribute_value, search_typed
from .examine import ExamineFormatter, search_examine_target

__all__ = ["CharacterBuildingRules"]

_BUILD = HasCapability("engine.world.build", principal_scope="account")
_EXAMINE = HasCapability("engine.object.examine", principal_scope="account")


def _can_edit(caller, obj):
    """Require the target's stable object-level control or edit policy."""
    return bool(obj and (obj.access(caller, "control") or obj.access(caller, "edit")))


class CharacterBuildingRules(AttributeOperations):
    """Composable character-side rules for the native default building verbs."""

    def _is_actor(self, actor):
        return self is getattr(actor, "character", None)

    @rule(SetObjAlias, phase="carry_out", requires=_BUILD)
    def carry_out_alias(self, action, actor):
        """View or mutate aliases with category and delete support."""
        if not self._is_actor(actor):
            return SKIP
        caller = self
        if not action.lhs:
            caller.msg("Usage: alias <obj> [= [alias[,alias ...]]]")
            return CLAIM
        obj = caller.search(action.lhs)
        if not obj:
            return CLAIM
        if action.rhs is None and "delete" not in action.switches:
            aliases = obj.aliases.all(return_key_and_category=True)
            if aliases:
                rendered = ", ".join(
                    f"'{key}'" + (f"[category:'{category}']" if category is not None else "")
                    for key, category in aliases
                )
                caller.msg(f"Aliases for {obj.get_display_name(caller)}: {rendered}")
            else:
                caller.msg(f"No aliases exist for '{obj.get_display_name(caller)}'.")
            return CLAIM
        if not _can_edit(caller, obj):
            caller.msg("You don't have permission to do that.")
            return CLAIM
        if not action.rhs:
            old_aliases = obj.aliases.all()
            if old_aliases:
                caller.msg(
                    f"Cleared aliases from {obj.get_display_name(caller)}: "
                    + ", ".join(old_aliases)
                )
                obj.aliases.clear()
            else:
                caller.msg("No aliases to clear.")
            return CLAIM
        if "delete" in action.switches:
            existed = False
            for key, category in obj.aliases.all(return_key_and_category=True):
                if key == action.rhs:
                    obj.aliases.remove(key=action.rhs, category=category)
                    existed = True
            caller.msg(
                f"Alias '{action.rhs}' deleted from {obj.get_display_name(caller)}."
                if existed
                else f"{obj.get_display_name(caller)} has no alias '{action.rhs}'."
            )
            return CLAIM
        category = None
        rhs = action.rhs
        if "category" in action.switches:
            if ":" not in rhs:
                caller.msg(
                    "If specifying the /category switch, the category must be given "
                    "as :category at the end."
                )
                return CLAIM
            rhs, category = (part.strip() for part in rhs.rsplit(":", 1))
        aliases = list(obj.aliases.get(category=category, return_list=True))
        aliases.extend(alias.strip().lower() for alias in rhs.split(",") if alias.strip())
        aliases = list(dict.fromkeys(aliases))
        obj.aliases.add(aliases, category=category)
        caller.msg(
            f"Alias(es) for '{obj.get_display_name(caller)}' set to '{obj.aliases}'"
            + (f" (category: '{category}')." if category else ".")
        )
        return CLAIM

    @rule(Copy, phase="carry_out", requires=_BUILD)
    def carry_out_copy(self, action, actor):
        """Copy an object once or to a list of named/location targets."""
        if not self._is_actor(actor):
            return SKIP
        caller = self
        if not action.args:
            caller.msg(
                "Usage: copy <obj> [=<new_name>[;alias;alias..]]"
                "[:<new_location>] [, <new_name2>...]"
            )
            return CLAIM
        source_name = action.lhs_objs[0]["name"] if action.rhs is not None else action.args
        source = caller.search(source_name)
        if not source:
            return CLAIM
        if not _can_edit(caller, source):
            caller.msg(f"You don't have permission to copy {source.key}.")
            return CLAIM
        if not action.rhs:
            new_name = f"{source_name}_copy"
            aliases = [
                (f"{alias}_copy", category)
                for alias, category in source.aliases.all(return_key_and_category=True)
            ]
            copied = ObjectDB.objects.copy_object(source, new_key=new_name, new_aliases=aliases)
            caller.msg(
                f"Identical copy of {source_name}, named '{new_name}' was created."
                if copied
                else f"There was an error copying {source_name}."
            )
            return CLAIM
        messages = []
        for objdef in action.rhs_objs:
            location = None
            if objdef["option"]:
                location = caller.search(objdef["option"], global_search=True)
                if not location:
                    continue
            copied = ObjectDB.objects.copy_object(
                source,
                new_key=objdef["name"],
                new_location=location,
                new_aliases=objdef["aliases"],
            )
            messages.append(
                f"Copied {source_name} to '{objdef['name']}' (aliases: {objdef['aliases']})."
                if copied
                else f"There was an error copying {source_name} to '{objdef['name']}'."
            )
        caller.msg("\n".join(messages))
        return CLAIM

    @rule(CpAttr, phase="carry_out", requires=_BUILD)
    def carry_out_cpattr(self, action, actor):
        """Copy or move one or more attributes to one or more objects."""
        if not self._is_actor(actor):
            return SKIP
        caller = self
        if not action.rhs:
            caller.msg("Usage: cpattr[/move] <obj>/<attr>[:category] = <obj>/<attr>")
            return CLAIM
        source_def = action.lhs_objattr[0]
        source_name = source_def["name"]
        source_attrs = source_def["attrs"]
        source_category = source_def["category"]
        source = caller.search(source_name) if source_attrs else caller
        if not source_attrs:
            source_attrs = [source_name]
        if not source or not action.rhs_objattr:
            caller.msg("You have to supply both source object and target(s).")
            return CLAIM
        moving = "move" in action.switches
        source_access = "edit" if moving else "examine"
        if not (source.access(caller, source_access) or source.access(caller, "control")):
            caller.msg(f"You don't have permission to read {source.key}.")
            return CLAIM
        if moving and len(source_attrs) != len(set(source_attrs)):
            caller.msg("|RCannot have duplicate source names when moving!")
            return CLAIM
        for attr in source_attrs:
            if not source.attributes.has(attr, category=source_category):
                caller.msg(
                    f"{source.name} doesn't have an attribute {attr}"
                    f"{f'[{source_category}]' if source_category else ''}."
                )
                return CLAIM
        output = []
        for target_def in action.rhs_objattr:
            target = caller.search(target_def["name"])
            if not target:
                output.append(f"\nCould not find object '{target_def['name']}'")
                continue
            if not _can_edit(caller, target):
                output.append(f"\nYou don't have permission to edit {target.key}.")
                continue
            target_attrs = target_def["attrs"]
            target_category = target_def["category"]
            for index, source_attr in enumerate(source_attrs):
                target_attr = target_attrs[index] if index < len(target_attrs) else source_attr
                value = source.attributes.get(source_attr, category=source_category)
                target.attributes.add(target_attr, value, category=target_category)
                source_suffix = f"[{source_category}]" if source_category else ""
                target_suffix = f"[{target_category}]" if target_category else ""
                if moving and not (source is target and source_attr == target_attr):
                    source.attributes.remove(source_attr, category=source_category)
                    verb = "Moved"
                else:
                    verb = "Copied"
                output.append(
                    f"\n{verb} {source.name}.{source_attr}{source_suffix} -> "
                    f"{target.name}.{target_attr}{target_suffix}. (value: {value!r})"
                )
        caller.msg("".join(output))
        return CLAIM

    def _set_flow(self, action, actor):
        """Generator implementing ``@set``, including safe editor conversion."""
        caller = self
        if not action.args or not action.lhs_objattr:
            caller.msg("Usage: set obj/attr[:category] = value. Use empty value to clear.")
            return CLAIM
        objdef = action.lhs_objattr[0]
        obj = search_typed(caller, objdef["name"], action.switches)
        if not obj:
            return CLAIM
        attrs = list(objdef["attrs"])
        category = objdef["category"]
        if "edit" in action.switches:
            if not _can_edit(caller, obj):
                caller.msg(f"You don't have permission to edit {obj.key}.")
                return CLAIM
            if len(attrs) > 1:
                caller.msg("The Line editor can only be applied to one attribute at a time.")
                return CLAIM
            if not attrs:
                caller.msg("Use `set/edit <objname>/<attr>` to define the Attribute to edit.")
                return CLAIM
            attr = attrs[0]
            try:
                old_value = obj.attributes.get(attr, category=category, raise_exception=True)
            except AttributeError:
                old_value = ""
            if not isinstance(old_value, str):
                answer = yield (
                    f"|rWarning: Attribute |w{attr}|r is of type "
                    f"|w{type(old_value).__name__}|r.\nTo continue editing, it must be "
                    "converted to (and saved as) a string. Continue? [Y]/N?"
                )
                if (answer or "").lower() not in ("y", "yes"):
                    caller.msg("Aborted edit.")
                    return CLAIM

            def load(editor_caller):
                try:
                    return str(obj.attributes.get(attr, category=category, raise_exception=True))
                except AttributeError:
                    return ""

            def save(editor_caller, buffer):
                obj.attributes.add(attr, buffer, category=category)
                editor_caller.msg(f"Saved Attribute {attr}.")

            EvEditor(caller, loadfunc=load, savefunc=save, key=f"{obj}/{attr}")
            return CLAIM
        result = []
        if not action.rhs:
            if action.rhs is None:
                if not attrs:
                    attrs = [
                        attr.key
                        for attr in obj.attributes.get(
                            category=category, return_obj=True, return_list=True
                        )
                    ]
                result.extend(self.view_attr(obj, attr, category) for attr in attrs)
            else:
                if not _can_edit(caller, obj):
                    caller.msg(f"You don't have permission to edit {obj.key}.")
                    return CLAIM
                result.extend(self.remove_attr(obj, attr, category) for attr in attrs)
        else:
            if not _can_edit(caller, obj):
                caller.msg(f"You don't have permission to edit {obj.key}.")
                return CLAIM
            for attr in attrs:
                value = parse_attribute_value(caller, action.rhs)
                if hasattr(value, "access") and not _can_edit(caller, value):
                    caller.msg(
                        f"You don't have permission to set object with identifier '{action.rhs}'."
                    )
                    continue
                result.append(self.set_attr(obj, attr, value, category))
        caller.msg(
            "".join(result).strip("\n")
            if result
            else "No valid attributes were found. Usage: set obj/attr[:category] = value. "
            "Use empty value to clear."
        )
        return CLAIM

    @rule(SetAttribute, phase="carry_out", requires=_BUILD)
    def carry_out_set(self, action, actor):
        """Dispatch the potentially interactive native ``@set`` flow."""
        if not self._is_actor(actor):
            return SKIP
        return self._set_flow(action, actor)

    def _find_link_object(self, caller, query):
        """Prefer local rich search results, then fall back to global search."""
        search_for = getattr(caller, "search_for", None)
        if search_for:
            from evennia.objects.search_result import Ambiguous, Found

            local = search_for(query)
            if isinstance(local, Found):
                return local.obj
            if isinstance(local, Ambiguous):
                caller.msg(
                    "Multiple matches: "
                    + ", ".join(str(candidate) for candidate in local.candidates)
                )
                return None
        return caller.search(query, global_search=True)

    def _link(self, action, clear=False):
        caller = self
        if not action.args:
            caller.msg("Usage: link[/twoway] <object> = <target>")
            return CLAIM
        obj = self._find_link_object(caller, action.lhs)
        if not obj:
            return CLAIM
        rhs = "" if clear else action.rhs
        if rhs:
            target = caller.search(rhs, global_search=True)
            if not target:
                return CLAIM
            if target is obj:
                caller.msg("Cannot link an object to itself.")
                return CLAIM
            if not _can_edit(caller, obj) or (
                "twoway" in action.switches and not _can_edit(caller, target)
            ):
                caller.msg("You don't have permission to edit that link.")
                return CLAIM
            notes = []
            if not getattr(obj, "destination", None):
                notes.append(
                    f"Note: {obj.name}({obj.dbref}) did not have a destination set before. "
                    "Make sure you linked the right thing."
                )
            if "twoway" in action.switches:
                if not (getattr(obj, "location", None) and getattr(target, "location", None)):
                    caller.msg(
                        f"To create a two-way link, {obj} and {target} must both have a location "
                        "(i.e. they cannot be rooms, but should be exits)."
                    )
                    return CLAIM
                if not getattr(target, "destination", None):
                    notes.append(
                        f"Note: {target.name}({target.dbref}) did not have a destination set before. "
                        "Make sure you linked the right thing."
                    )
                obj.destination, target.destination = target.location, obj.location
                notes.append(
                    f"Link created {obj.name} (in {obj.location}) <-> {target.name} "
                    f"(in {target.location}) (two-way)."
                )
            else:
                obj.destination = target
                notes.append(f"Link created {obj.name} -> {target} (one way).")
            caller.msg("\n".join(notes))
            return CLAIM
        if rhs is None:
            destination = getattr(obj, "destination", None)
            caller.msg(
                f"{obj.name} is an exit to {destination.name}."
                if destination
                else f"{obj.name} is not an exit. Its home location is {obj.home}."
            )
            return CLAIM
        if not _can_edit(caller, obj):
            caller.msg("You don't have permission to edit that link.")
            return CLAIM
        if getattr(obj, "destination", None):
            obj.destination = None
            caller.msg(f"Former exit {obj.name} no longer links anywhere.")
        else:
            caller.msg(f"{obj.name} had no destination to unlink.")
        return CLAIM

    @rule(Link, phase="carry_out", requires=_BUILD)
    def carry_out_link(self, action, actor):
        """Inspect, set, clear, or two-way-link destinations."""
        return self._link(action) if self._is_actor(actor) else SKIP

    @rule(Unlink, phase="carry_out", requires=_BUILD)
    def carry_out_unlink(self, action, actor):
        """Clear a destination through the same native link algorithm."""
        return self._link(action, clear=True) if self._is_actor(actor) else SKIP

    @rule(SetHome, phase="carry_out", requires=_BUILD)
    def carry_out_sethome(self, action, actor):
        """Inspect or mutate an object's home with object-level edit checks."""
        if not self._is_actor(actor):
            return SKIP
        caller = self
        if not action.args:
            caller.msg("Usage: sethome <obj> [= <home_location>]")
            return CLAIM
        obj = caller.search(action.lhs, global_search=True)
        if not obj:
            return CLAIM
        if not action.rhs:
            home = getattr(obj, "home", None)
            caller.msg(
                f"{obj}'s current home is {home}({home.dbref})."
                if home
                else "This object has no home location set!"
            )
            return CLAIM
        if not _can_edit(caller, obj):
            caller.msg(f"You don't have permission to edit {obj.key}.")
            return CLAIM
        new_home = caller.search(action.rhs, global_search=True)
        if not new_home:
            return CLAIM
        old_home = getattr(obj, "home", None)
        obj.home = new_home
        caller.msg(
            f"Home location of {obj} was changed from {old_home}({old_home.dbref}) to "
            f"{new_home}({new_home.dbref})."
            if old_home
            else f"Home location of {obj} was set to {new_home}({new_home.dbref})."
        )
        return CLAIM

    @rule(Wipe, phase="carry_out", requires=_BUILD)
    def carry_out_wipe(self, action, actor):
        """Remove selected or all attributes after object-level authorization."""
        if not self._is_actor(actor):
            return SKIP
        caller = self
        if not action.args or not action.lhs_objattr:
            caller.msg("Usage: wipe <object>[/<attr>/<attr>...]")
            return CLAIM
        objdef = action.lhs_objattr[0]
        obj = caller.search(objdef["name"])
        if not obj:
            return CLAIM
        if not _can_edit(caller, obj):
            caller.msg("You are not allowed to do that.")
            return CLAIM
        attrs = objdef["attrs"]
        if not attrs:
            obj.attributes.clear()
            caller.msg(f"Wiped all attributes on {obj.name}.")
        else:
            for attr in attrs:
                obj.attributes.remove(attr)
            caller.msg(f"Wiped attributes {','.join(attrs)} on {obj.name}.")
        return CLAIM

    @rule(Examine, phase="carry_out", requires=_EXAMINE)
    def carry_out_examine(self, action, actor):
        """Inspect typed targets without stored/merged CmdSet material."""
        if not self._is_actor(actor):
            return SKIP
        caller = self
        definitions = action.lhs_objattr if action.args else ()
        if not definitions:
            location = getattr(caller, "location", None)
            if not location:
                caller.msg("You need to supply a target to examine.")
                return CLAIM
            definitions = ({"name": None, "attrs": (), "category": None},)
        formatter = ExamineFormatter()
        for definition in definitions:
            query = definition["name"]
            object_type = "object"
            if "account" in action.switches or (query and query.startswith("*")):
                object_type = "account"
            elif "script" in action.switches:
                object_type = "script"
            elif "channel" in action.switches:
                object_type = "channel"
            obj = (
                caller.location
                if query is None
                else search_examine_target(caller, query, object_type, action.switches)
            )
            if not obj:
                continue
            if not obj.access(caller, "examine"):
                caller.msg(caller.at_look(obj))
                continue
            attrs = definition["attrs"]
            if attrs:
                found = [attr for attr in obj.attributes.all() if attr.db_key in attrs]
                if not found:
                    caller.msg(f"No attributes found on {obj.name}.")
                else:
                    caller.msg(
                        "\n".join(
                            formatter.format_single_attribute(attr, detail=True, obj=obj)
                            for attr in found
                        )
                    )
                continue
            width = (
                getattr(getattr(actor, "session", None), "protocol_flags", {})
                .get("SCREENWIDTH", {})
                .get(0, None)
            )
            caller.msg(text=(formatter.render(obj, object_type, width=width), {"type": "examine"}))
        return CLAIM
