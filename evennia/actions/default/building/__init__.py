"""Native default building actions and their composed character rules."""

from .actions import (
    Alias,
    Copy,
    CpAttr,
    Examine,
    Link,
    ObjManipAction,
    Set,
    SetAttribute,
    SetHome,
    SetObjAlias,
    Unlink,
    Wipe,
)
from .rules import CharacterBuildingRules

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
    "CharacterBuildingRules",
]
