"""
Default actions shipped with the engine.

The action-engine equivalent of ``evennia/commands/default/``: generic, ready-to-use
actions and their baseline rule providers that a game gets out of the box and
extends with its own ``@rule`` providers. Keeping these here keeps the core
mechanism modules (``action``/``rule``/``engine``/``process``/``events``) free of
any object-model (room/exit/``move_to``) dependency.
"""

from .events import Arrived, Departed, Moved
from .emote_nomatch import DefaultEmoteNoMatchRules
from .nomatch import DefaultNoMatchRules, nomatch_provider, nomatch_providers
from .movement import (
    CharacterMovementRules,
    ExitTraversalRules,
    Locomotion,
    Move,
    exit_resolver,
    register_exit_resolver,
)
from .objects import (
    CharacterObjectRules,
    ContainerPutRules,
    Drop,
    Enter,
    Enterable,
    EnterableObjectRules,
    Get,
    Give,
    Put,
)

__all__ = [
    "DefaultNoMatchRules",
    "DefaultEmoteNoMatchRules",
    "nomatch_provider",
    "nomatch_providers",
    "Pose",
    "Emote",
    "DefaultRoleplayRules",
    "Moved",
    "Departed",
    "Arrived",
    "Move",
    "Locomotion",
    "ExitTraversalRules",
    "CharacterMovementRules",
    "exit_resolver",
    "register_exit_resolver",
    "Enterable",
    "Get",
    "Drop",
    "Give",
    "Put",
    "Enter",
    "CharacterObjectRules",
    "ContainerPutRules",
    "EnterableObjectRules",
]


def __getattr__(name):
    """Lazy import roleplay actions so games with custom pose/emote avoid verb conflicts."""
    if name in ("Pose", "Emote", "DefaultRoleplayRules"):
        from . import roleplay

        return getattr(roleplay, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
