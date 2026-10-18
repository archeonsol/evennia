"""Attribute algorithms shared by native default building rules."""

from __future__ import annotations

import ast
import re

from django.conf import settings

from evennia.utils import funcparser, search, utils
from evennia.utils.ansi import raw as ansi_raw
from evennia.utils.utils import variable_from_module

__all__ = [
    "AttributeOperations",
    "as_typed",
    "convert_from_string",
    "pasteable",
    "search_typed",
    "stored_slot",
    "stored_value",
]

LIST_APPEND_CHAR = "+"
_NESTED_RE = re.compile(r"\[.*?\]")
_NOT_FOUND = object()
_ATTRFUNCPARSER = None
_OUTGOING_FUNCPARSER = None


def as_typed(value):
    """``value`` as text that shows its markup instead of rendering it.

    Color codes are escaped. ``$func()`` calls are escaped only when outgoing
    messages are parsed for them; otherwise the escape would show as a ``\\``.
    """
    global _OUTGOING_FUNCPARSER
    text = ansi_raw(utils.to_str(value))
    if not settings.FUNCPARSER_PARSE_OUTGOING_MESSAGES_ENABLED:
        return text
    if _OUTGOING_FUNCPARSER is None:
        _OUTGOING_FUNCPARSER = funcparser.FuncParser(settings.FUNCPARSER_OUTGOING_MESSAGES_MODULES)
    return _OUTGOING_FUNCPARSER.parse(text, escape=True)


def stored_slot(obj, key, category=None):
    """``(value, strattr)`` of an attribute that exists, as plain data.

    ``strattr`` is True for a string attribute, whose value is read from
    ``strvalue``. The value is a copy: writing the slot later does not change it.
    """
    stored = obj.attributes.get(key, category=category, return_obj=True)
    if stored.value is None and stored.strvalue is not None:
        return stored.strvalue, True
    return stored.value, False


def stored_value(obj, key, category=None):
    """The value of an attribute that exists, read from ``strvalue`` for a string attribute."""
    return stored_slot(obj, key, category)[0]


def pasteable(value):
    """``value`` as ``@set`` would need it typed to store it again unchanged.

    A string is quoted when its bare text would be read as something else: a
    literal (``123``, ``None``), a ``$dbref``/``$search`` call, or text that
    loses its edge spaces or newlines on the way in.
    """
    if isinstance(value, bytes):
        return repr(value)
    if not isinstance(value, str):
        return value
    if (
        not value
        or value != value.strip()
        or "\n" in value
        or "$dbref(" in value
        or "$search(" in value
    ):
        return repr(value)
    try:
        ast.literal_eval(value)
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        return value
    return repr(value)


def convert_from_string(caller, value):
    """Convert a wire string to a Python literal, falling back to text."""
    try:
        return ast.literal_eval(value)
    except (SyntaxError, ValueError, TypeError, MemoryError, RecursionError):
        value = utils.to_str(value)
        caller.msg(
            f'|RNote: "|n{as_typed(value)}|R" was stored as a string. Make sure this is acceptable.|n'
        )
        return value


def parse_attribute_value(caller, value):
    """Resolve ``$dbref``/``$search`` or convert a Python literal."""
    global _ATTRFUNCPARSER
    if _ATTRFUNCPARSER is None:
        _ATTRFUNCPARSER = funcparser.FuncParser(
            {
                "dbref": funcparser.funcparser_callable_search,
                "search": funcparser.funcparser_callable_search,
            }
        )
    parsed = _ATTRFUNCPARSER.parse(value, return_str=False, caller=caller)
    if hasattr(parsed, "access"):
        return parsed
    return convert_from_string(caller, value)


def search_typed(caller, query, switches):
    """Search the object table selected by building switches."""
    switches = set(switches)
    if query.startswith("*") or "account" in switches:
        return caller.search_account(query.lstrip("*"))
    if "script" in switches or "channel" in switches:
        kind = "script" if "script" in switches else "channel"
        result = getattr(search, f"search_{kind}")(query)
        at_search_result = variable_from_module(*settings.SEARCH_AT_RESULT.rsplit(".", 1))
        return at_search_result(result, caller, query=query)
    global_search = bool(switches & {"char", "character", "room", "exit"})
    typeclass = None
    if switches & {"char", "character"}:
        typeclass = settings.BASE_CHARACTER_TYPECLASS
    elif "room" in switches:
        typeclass = settings.BASE_ROOM_TYPECLASS
    elif "exit" in switches:
        typeclass = settings.BASE_EXIT_TYPECLASS
    return caller.search(query, global_search=global_search, typeclass=typeclass)


class AttributeOperations:
    """Mature nested persistent-attribute read/write/remove operations."""

    @staticmethod
    def split_nested_attr(attr):
        """Yield deepest-first compatible base-attribute/nested-key pairs."""
        quotes = "\"'"

        def clean_key(value):
            value = value.strip("[]")
            if value and value[0] in quotes:
                return value.strip(quotes)
            if value and value[0] == LIST_APPEND_CHAR:
                return value
            try:
                return int(value)
            except ValueError:
                return value

        parts = _NESTED_RE.findall(attr)
        base_attr = attr[: attr.find(parts[0])] if parts else ""
        for index, part in enumerate(parts):
            yield base_attr, [clean_key(item) for item in parts[index:]]
            base_attr += part
        yield attr, []

    @staticmethod
    def nested_lookup(value, *keys):
        """Look through mapping/sequence keys, returning a private miss token."""
        result = value
        for key in keys:
            try:
                result = result[key]
            except (IndexError, KeyError, TypeError):
                return _NOT_FOUND
        return result

    def view_attr(self, obj, attr, category):
        """Return player-facing output for an attribute or nested value.

        Markup in the value is escaped and a string is quoted where it must be,
        so the shown value can be pasted back into ``@set``.
        """
        nested = False
        for key, nested_keys in self.split_nested_attr(attr):
            nested = nested or bool(nested_keys)
            if not obj.attributes.has(key, category):
                continue
            value = stored_value(obj, key, category)
            if nested_keys:
                deep = self.nested_lookup(value, *nested_keys[:-1])
                if deep is _NOT_FOUND:
                    continue
                try:
                    value = deep[nested_keys[-1]]
                except (IndexError, KeyError, TypeError):
                    continue
            shown = as_typed(pasteable(value))
            return f"\nAttribute {obj.name}/|w{attr}|n [category:{category}] = {shown}"
        suffix = " (Nested lookups attempted)" if nested else ""
        return f"\nAttribute {obj.name}/|w{attr}|n [category:{category}] does not exist.{suffix}"

    def remove_attr(self, obj, attr, category):
        """Remove a top-level or nested attribute value and report it."""
        nested = False
        for key, nested_keys in self.split_nested_attr(attr):
            nested = nested or bool(nested_keys)
            if not obj.attributes.has(key, category):
                continue
            if nested_keys:
                value = obj.attributes.get(key, category=category)
                deep = self.nested_lookup(value, *nested_keys[:-1])
                if deep is _NOT_FOUND:
                    continue
                try:
                    del deep[nested_keys[-1]]
                except (IndexError, KeyError, TypeError):
                    continue
            else:
                obj.attributes.remove(key, category=category)
            return f"\nDeleted attribute {obj.name}/|w{attr}|n [category:{category}]."
        suffix = " (Nested lookups attempted)" if nested else ""
        return (
            f"\nNo attribute {obj.name}/|w{attr}|n [category: {category}] was found to delete."
            f"{suffix}"
        )

    def set_attr(self, obj, attr, value, category):
        """Create or mutate a top-level or nested attribute."""
        original_attr = attr
        done = False
        for key, nested_keys in self.split_nested_attr(attr):
            if not nested_keys or not obj.attributes.has(key, category):
                continue
            access_key = nested_keys[-1]
            stored = obj.attributes.get(key, category=category)
            deep = self.nested_lookup(stored, *nested_keys[:-1])
            if deep is _NOT_FOUND:
                continue
            if isinstance(access_key, str) and access_key.startswith(LIST_APPEND_CHAR):
                try:
                    if len(access_key) > 1:
                        deep.insert(int(access_key[1:]), value)
                    else:
                        deep.append(value)
                except (ValueError, AttributeError):
                    pass
                else:
                    value, attr, done = stored, key, True
                    break
            try:
                deep[access_key] = value
            except (TypeError, IndexError) as err:
                return f"\nCould not set {obj.name}/|w{original_attr}|n: {err}."
            value, attr, done = stored, key, True
            break
        verb = "Modified" if obj.attributes.has(attr, category) else "Created"
        if not done:
            obj.attributes.add(attr, value, category=category)
        return (
            f"\n{verb} attribute {obj.name}/|w{original_attr if done else attr}|n "
            f"[category:{category}] = {as_typed(value)}"
        )
