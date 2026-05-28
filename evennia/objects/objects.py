"""
Backward-compatibility re-export shim.

The classes that previously all lived in this single file have been split into
focused modules:

- `evennia.objects.object`    — `ObjectSessionHandler`, `DefaultObject`
- `evennia.objects.character` — `DefaultCharacter`
- `evennia.objects.room`      — `DefaultRoom`
- `evennia.objects.exit`      — `ExitCommand`, `DefaultExit`

All names are re-exported here so that existing code using::

    from evennia.objects.objects import DefaultObject  # (or any other name)

continues to work without modification.

"""

from evennia.objects.character import DefaultCharacter  # noqa: F401
from evennia.objects.exit import DefaultExit, ExitCommand  # noqa: F401
from evennia.objects.object import (DefaultObject,  # noqa: F401
                                    ObjectSessionHandler)
from evennia.objects.room import DefaultRoom  # noqa: F401

__all__ = [
    "ObjectSessionHandler",
    "DefaultObject",
    "DefaultCharacter",
    "DefaultRoom",
    "ExitCommand",
    "DefaultExit",
]
