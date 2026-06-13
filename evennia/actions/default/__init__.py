"""
Default actions shipped with the engine.

The action-engine equivalent of ``evennia/commands/default/``: generic, ready-to-use
actions and their baseline rule providers that a game gets out of the box and
extends with its own ``@rule`` providers. Keeping these here keeps the core
mechanism modules (``action``/``rule``/``engine``/``process``/``events``) free of
any object-model (room/exit/``move_to``) dependency.
"""

from .account import DefaultAccountRules, Option, Password, UserPassword
from .admin import Access, CharacterAdminRules, Emit, Force, Perm, Wall
from .emote_nomatch import DefaultEmoteNoMatchRules
from .events import Arrived, Departed, Moved
from .general import CharacterGeneralRules, Help, Home, Nick, NickRules, SetHelp
from .movement import (
    CharacterMovementRules,
    ExitTraversalRules,
    Locomotion,
    Move,
    exit_resolver,
    register_exit_resolver,
)
from .nomatch import DefaultNoMatchRules, nomatch_providers
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
from .system import CharacterSystemRules, Py, PyRules, Systems, Tasks
from .unloggedin import Connect, Create, Encoding, Info, Screenreader, SessionLoginRules

__all__ = [
    "DefaultNoMatchRules",
    "DefaultEmoteNoMatchRules",
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
    # admin verbs
    "Emit",
    "Wall",
    "Force",
    "Perm",
    "Access",
    "CharacterAdminRules",
    # general verbs
    "Nick",
    "Home",
    "Help",
    "SetHelp",
    "NickRules",
    "CharacterGeneralRules",
    # account-shell verbs
    "Option",
    "Password",
    "UserPassword",
    "DefaultAccountRules",
    # system verbs
    "Systems",
    "Tasks",
    "Py",
    "PyRules",
    "CharacterSystemRules",
    # unlogged-in verbs
    "Connect",
    "Create",
    "Info",
    "Encoding",
    "Screenreader",
    "SessionLoginRules",
]


def __getattr__(name):
    """Lazy import roleplay actions so games with custom pose/emote avoid verb conflicts."""
    if name in ("Pose", "Emote", "DefaultRoleplayRules"):
        from . import roleplay

        return getattr(roleplay, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
