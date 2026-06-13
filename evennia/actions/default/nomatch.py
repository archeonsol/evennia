"""
Default no-match handling (CM1): baseline feedback when nothing else claims.

Games layer pending-input handlers and custom fallbacks as higher-priority
``@rule(NoMatchAction, ...)`` providers on their typeclasses; this module ships
the engine fallback (parse error text, fuzzy verb suggestions, or a short
default message).
"""

from __future__ import annotations

from django.utils.translation import gettext as _

from evennia.utils import utils

from ..parser import NoMatchAction
from ..result import CLAIM
from ..rule import rule
from .emote_nomatch import DefaultEmoteNoMatchRules

__all__ = [
    "DefaultNoMatchRules",
    "DefaultEmoteNoMatchRules",
    "nomatch_providers",
]


class DefaultNoMatchRules:
    """Baseline ``NoMatchAction`` narration when no other rule claims."""

    @rule(NoMatchAction, phase="report", priority=-1000)
    def report_nomatch(self, action, actor):
        if action.error:
            actor.msg(action.error)
            return CLAIM
        if action.suggestions:
            msg = _("Command '{command}' is not available.").format(command=action.raw_string)
            msg += _(" Maybe you meant {command}?").format(
                command=utils.list_to_string(action.suggestions, endsep=_("or"), addquote=True)
            )
            actor.msg(msg)
            return CLAIM
        actor.msg(_("Huh?"))
        return CLAIM


#: Providers prepended for ``NoMatchAction`` dispatches (emote before baseline).
nomatch_providers = (DefaultEmoteNoMatchRules(), DefaultNoMatchRules())
