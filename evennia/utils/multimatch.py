"""
Multimatch parsing, display labels, location scoping, and auto-pick.

Used by object search, command parsing, and at_search_result.
"""

from __future__ import annotations

import re
from typing import Any, Optional, Union

from django.conf import settings
from django.utils.translation import gettext as _

from evennia.utils.utils import str2int, variable_from_module

# Ordinal words -> 0-based index (first four via str2int; rest explicit)
_ORDINAL_WORDS = (
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
)
_SPECIAL_SELECTORS = frozenset(("last", "other"))

Selector = Union[int, str, None]

_DEFAULT_LOCATION_PREFIXES = {
    "my": "inventory",
    "mine": "inventory",
    "here": "location",
    "room": "location",
    "worn": "worn",
}


def _location_prefixes():
    return getattr(settings, "SEARCH_MULTIMATCH_LOCATION_PREFIXES", _DEFAULT_LOCATION_PREFIXES)


def _multimatch_regex():
    return re.compile(settings.SEARCH_MULTIMATCH_REGEX, re.I + re.U)


def _get_multimatch_input_handler():
    path = getattr(settings, "SEARCH_MULTIMATCH_INPUT", None)
    if path:
        return variable_from_module(*path.rsplit(".", 1))
    return parse_multimatch_input


def parse_location_scope(searchdata: str) -> tuple[Optional[str], str]:
    """
    Parse a location scope prefix (my/here/worn etc).

    Returns:
        (scope, remainder) where scope is inventory, location, worn, or None.
    """
    if not searchdata or not isinstance(searchdata, str):
        return None, searchdata
    text = searchdata.strip()
    prefixes = _location_prefixes()
    for prefix in sorted(prefixes.keys(), key=len, reverse=True):
        m = re.match(rf"^{re.escape(prefix)}\s+(.+)$", text, re.IGNORECASE)
        if m:
            scope = prefixes[prefix]
            return scope, m.group(1).strip()
    return None, text


def parse_multimatch_input(searchdata: str) -> tuple[Selector, str]:
    """
    Parse ordinal / last / other / numeric multimatch prefix.

    Returns:
        (selector, remainder). selector is 0-based int, "last", "other", or None.
    """
    if not searchdata or not isinstance(searchdata, str):
        return None, searchdata
    text = searchdata.strip()

    for word in _ORDINAL_WORDS:
        m = re.match(rf"^{re.escape(word)}\s+(.+)$", text, re.IGNORECASE)
        if m:
            try:
                return str2int(word) - 1, m.group(1).strip()
            except ValueError:
                pass

    for word in _SPECIAL_SELECTORS:
        m = re.match(rf"^{re.escape(word)}\s+(.+)$", text, re.IGNORECASE)
        if m:
            return word, m.group(1).strip()

    regex = _multimatch_regex()
    m = regex.match(text)
    if m:
        groups = m.groupdict()
        if groups.get("number") is not None:
            name = groups.get("name", "") or ""
            args = groups.get("args") or ""
            return int(groups["number"]) - 1, (name + args).strip()

    # optional: "2 sword" (digit + space)
    m = re.match(r"^(\d+)\s+(.+)$", text)
    if m:
        return int(m.group(1)) - 1, m.group(2).strip()

    return None, text


def parse_search_qualifiers(searchdata: str) -> dict[str, Any]:
    """
    Parse location scope then multimatch selector.

    Returns:
        dict with keys scope, selector, searchdata, had_qualifier.
    """
    scope, remainder = parse_location_scope(searchdata)
    selector, remainder = parse_multimatch_input(remainder)
    had = scope is not None or selector is not None
    return {
        "scope": scope,
        "selector": selector,
        "searchdata": remainder,
        "had_qualifier": had,
    }


def resolve_multimatch_index(selector: Selector, num_matches: int) -> Optional[int]:
    """
    Map selector to 0-based index, or None if invalid.
    """
    if selector is None or num_matches < 1:
        return None
    if selector == "last":
        return num_matches - 1 if num_matches >= 1 else None
    if selector == "other":
        return 1 if num_matches == 2 else None
    if isinstance(selector, int):
        if 0 <= selector < num_matches:
            return selector
        return None
    return None


def multimatch_label(index: int, num_matches: int) -> str:
    """Display label for a multimatch row (0-based index)."""
    if num_matches == 2 and index == 1:
        return "other"
    if num_matches >= 2 and index == num_matches - 1:
        return "last"
    if index < len(_ORDINAL_WORDS):
        return _ORDINAL_WORDS[index]
    return f"{index + 1}th"


def partition_matches(matches, caller) -> tuple[list, list, list]:
    """
    Split matches into inventory, room, and other buckets.
    """
    inv, room, other = [], [], []
    loc = getattr(caller, "location", None)
    for obj in matches:
        if getattr(obj, "location", None) == caller:
            inv.append(obj)
        elif loc and getattr(obj, "location", None) == loc:
            room.append(obj)
        else:
            other.append(obj)
    return inv, room, other


def try_autopick(matches, caller):
    """
    Return a single match if auto-pick rules apply, else None.
    """
    if not getattr(settings, "SEARCH_MULTIMATCH_AUTOPICK", True):
        return None
    if not matches or len(matches) <= 1:
        return matches[0] if len(matches) == 1 else None
    inv, room, other = partition_matches(list(matches), caller)
    if other:
        return None
    if len(inv) == 1 and len(room) == 0:
        return inv[0]
    if len(room) == 1 and len(inv) == 0:
        return room[0]
    return None


def _default_worn_filter(obj, caller) -> bool:
    if obj.tags.get("worn"):
        return True
    worn = getattr(obj, "db", None) and obj.db.worn
    return bool(worn)


def _get_worn_filter():
    path = getattr(settings, "SEARCH_MULTIMATCH_WORN_FILTER", None)
    if path:
        return variable_from_module(*path.rsplit(".", 1))
    return _default_worn_filter


def narrow_candidates(caller, scope: str) -> Optional[list]:
    """
    Build a candidate list for a location scope.
    """
    if scope == "inventory":
        return list(caller.contents)
    if scope == "location":
        loc = getattr(caller, "location", None)
        if not loc:
            return []
        return list(loc.contents)
    if scope == "worn":
        worn_filter = _get_worn_filter()
        return [obj for obj in caller.contents if worn_filter(obj, caller)]
    return None


def location_hint(obj, caller) -> str:
    """
    Extra info for multimatch listings (leading space).
    """
    if not caller:
        return ""
    if getattr(obj, "location", None) == caller:
        return _(" (yours, in your pack)")
    loc = getattr(caller, "location", None)
    if loc and getattr(obj, "location", None) == loc:
        return _(" (here, on the floor)")
    if hasattr(obj, "get_extra_info"):
        return obj.get_extra_info(caller) or ""
    return ""


def format_multimatch_footer(name: str, labels: list[str], scope_hint: bool = False) -> str:
    """
    Build a 'Type ...' hint line from display labels.
    """
    parts = [f"{label} {name}" for label in labels]
    if not parts:
        return ""
    if len(parts) == 1:
        hint = _("Type '{example}' to pick.").format(example=parts[0])
    else:
        hint = _("Type {examples}, or '{last}' to pick.").format(
            examples=", ".join(f"'{p}'" for p in parts[:-1]),
            last=parts[-1],
        )
    if scope_hint:
        hint += " " + _("You can also try 'my {name}', 'here {name}', or 'worn {name}'.").format(
            name=name
        )
    return hint


def apply_multimatch_template(
    template: str,
    *,
    label: str,
    name: str,
    aliases: str = "",
    info: str = "",
    number: Optional[int] = None,
) -> str:
    """
    Format SEARCH_MULTIMATCH_TEMPLATE with label; fall back to number for old templates.
    """
    try:
        return template.format(label=label, name=name, aliases=aliases, info=info)
    except KeyError:
        if number is not None:
            return template.format(number=number, name=name, aliases=aliases, info=info)
        raise


def invalid_other_message(query: str) -> str:
    return _("You can only use 'other {query}' when there are exactly two matches.").format(
        query=query.split(None, 1)[-1] if " " in query else query
    )
