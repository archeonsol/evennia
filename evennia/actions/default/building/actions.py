"""Typed requests for the native default building action family."""

from __future__ import annotations

import re

from evennia.objects.character import DefaultCharacter

from ...action import action
from ...muxargs import ArgAction

__all__ = [
    "ObjManipAction",
    "SetAttribute",
    "Set",
    "SetObjAlias",
    "Alias",
    "Copy",
    "CpAttr",
    "Link",
    "Unlink",
    "SetHome",
    "Wipe",
    "Examine",
]

_KEY_RE = re.compile(r"(?P<attr>[^\[]*)(?P<key>(\[[^\]]*\]\ *)+)?$")


def _objattrs(items):
    """Parse mux object definitions into attribute-aware definitions."""
    parsed = []
    for objdef in items:
        category = None
        if ":" in objdef:
            objdef, category = (part.strip() for part in objdef.rsplit(":", 1))
        if ";" in objdef:
            objdef = objdef.split(";", 1)[0].strip()
        attrs = []
        if "/" in objdef:
            objdef, attrspec = (part.strip() for part in objdef.split("/", 1))
            for part in map(str.strip, attrspec.split("/")):
                match = _KEY_RE.match(part)
                if not part or not match:
                    continue
                attr = match.group("attr").lower()
                if match.group("key"):
                    attr += match.group("key")
                attrs.append(attr)
        parsed.append({"name": objdef, "attrs": attrs, "category": category})
    return tuple(parsed)


class ObjManipAction(ArgAction):
    """Argument shape shared by building actions that address attributes."""

    @property
    def lhs_objattr(self):
        """Attribute-aware definitions from the left side."""
        return _objattrs(self.lhslist)

    @property
    def rhs_objattr(self):
        """Attribute-aware definitions from the right side."""
        return _objattrs(self.rhslist)


@action("@set")
class SetAttribute(ObjManipAction):
    """View, set, edit, or remove persistent attributes.

    |w@set <object>[/<attribute>/...][:<category>]|n /
    |w@set <object>/<attribute>[:<category>] = <value>|n /
    |w@set/edit <object>/<attribute>[:<category>]|n. Requires
    |wengine.world.build|n; viewing requires examine access, while assigning,
    clearing with an empty value, or editing requires edit or control access.
    |w/edit|n opens the line editor and converts a non-string value after confirmation.
    """

    __primary_handler__ = DefaultCharacter


Set = SetAttribute


@action("@alias", "setobjalias")
class SetObjAlias(ArgAction):
    """View, add, clear, or remove object aliases.

    |w@alias <object>|n / |w@alias <object> = <alias>, ...|n /
    |w@alias <object> =|n / |w@alias/delete <object> = <alias>|n /
    |w@alias/category <object> = <alias>, ... : <category>|n. Requires
    |wengine.world.build|n; mutations require edit or control access. An empty
    right side clears every alias, and |w/delete|n removes matching aliases.
    """

    __primary_handler__ = DefaultCharacter


Alias = SetObjAlias


@action("@copy")
class Copy(ObjManipAction):
    """Copy an editable object and its persistent properties.

    |w@copy <object>|n / |w@copy <object> = <new name>[;<alias>;...]
    [:<location>][, <new name>...]|n. Requires |wengine.world.build|n and
    edit or control access to the source. Without a destination it creates a
    |w_copy|n-suffixed duplicate; each supplied destination creates a new object.
    """

    __primary_handler__ = DefaultCharacter


@action("@cpattr")
class CpAttr(ObjManipAction):
    """Copy or move attributes between objects.

    |w@cpattr <source>/<attribute>[:<category>] = <target>[/<attribute>]
    [:<category>][, <target>...]|n / |w@cpattr/move ...|n. Requires
    |wengine.world.build|n; copying requires examine or control access to the
    source, moving requires edit or control access, and targets must be editable.
    |w/move|n removes each copied source attribute after a successful transfer.
    """

    __primary_handler__ = DefaultCharacter


@action("@link")
class Link(ArgAction):
    """Inspect or set an object's destination.

    |w@link <object>|n / |w@link <object> = <target>|n /
    |w@link/twoway <exit> = <return exit>|n. Requires |wengine.world.build|n;
    setting a link requires edit or control access to the source, and |w/twoway|n
    requires it for both exits and sets each destination to the other's location.
    """

    __primary_handler__ = DefaultCharacter


@action("@unlink")
class Unlink(ArgAction):
    """Clear an object's destination.

    |w@unlink <object>|n. Requires |wengine.world.build|n and edit or control
    access to the object; this removes its current destination.
    """

    __primary_handler__ = DefaultCharacter


@action("@sethome")
class SetHome(ArgAction):
    """Inspect or set an object's home location.

    |w@sethome <object>|n / |w@sethome <object> = <home location>|n. Requires
    |wengine.world.build|n; setting requires edit or control access to both the
    object and the new home.
    """

    __primary_handler__ = DefaultCharacter


@action("@wipe")
class Wipe(ObjManipAction):
    """Remove selected or all persistent attributes from an object.

    |w@wipe <object>[/<attribute>/<attribute>/...]|n. Requires
    |wengine.world.build|n and edit or control access. Omitting attributes
    permanently clears every attribute on the object.
    """

    __primary_handler__ = DefaultCharacter


@action("@examine", "@exam", "@ex")
class Examine(ObjManipAction):
    """Inspect an object, account, script, channel, or selected attributes.

    |w@examine [<target>[/<attribute>/...]]|n / |w@examine/account <account>|n
    / |w@examine/script <script>|n / |w@examine/channel <channel>|n. |w@exam|n
    and |w@ex|n are aliases; a leading |w*|n selects an account. Requires
    |wengine.object.examine|n. Without a target, it examines the current location.
    """

    __primary_handler__ = DefaultCharacter
