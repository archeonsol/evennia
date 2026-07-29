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
    """View, create, edit, or remove persistent attributes."""

    __primary_handler__ = DefaultCharacter


Set = SetAttribute


@action("@alias", "setobjalias")
class SetObjAlias(ArgAction):
    """View or mutate globally visible object aliases."""

    __primary_handler__ = DefaultCharacter


Alias = SetObjAlias


@action("@copy")
class Copy(ObjManipAction):
    """Copy one object and its persistent properties."""

    __primary_handler__ = DefaultCharacter


@action("@cpattr")
class CpAttr(ObjManipAction):
    """Copy or move attributes between objects."""

    __primary_handler__ = DefaultCharacter


@action("@link")
class Link(ArgAction):
    """Inspect or mutate an object's destination."""

    __primary_handler__ = DefaultCharacter


@action("@unlink")
class Unlink(ArgAction):
    """Clear an object's destination."""

    __primary_handler__ = DefaultCharacter


@action("@sethome")
class SetHome(ArgAction):
    """Inspect or mutate an object's home."""

    __primary_handler__ = DefaultCharacter


@action("@wipe")
class Wipe(ObjManipAction):
    """Remove selected or all persistent attributes from an object."""

    __primary_handler__ = DefaultCharacter


@action("@examine", "@exam", "@ex")
class Examine(ObjManipAction):
    """Inspect engine objects, accounts, scripts, and channels."""

    __primary_handler__ = DefaultCharacter
