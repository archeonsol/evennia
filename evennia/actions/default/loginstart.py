"""Default ``LoginStartAction`` handling (CM1 Phase 8)."""

from __future__ import annotations

from django.conf import settings

from evennia.utils import utils

from ..parser import LoginStartAction
from ..result import CLAIM
from ..rule import rule

__all__ = ["DefaultLoginStartRules", "loginstart_providers"]


class DefaultLoginStartRules:
    """Show the unlogged-in connection screen once per connect."""

    @rule(LoginStartAction, phase="carry_out", priority=0)
    def carry_out_loginstart(self, action, actor):
        del action
        session = actor.session or actor.effective
        if session is None:
            return CLAIM
        module = getattr(settings, "CONNECTION_SCREEN_MODULE", "server.conf.connection_screens")
        callables = utils.callables_from_module(module)
        if "connection_screen" in callables:
            connection_screen = callables["connection_screen"]()
        else:
            connection_screen = utils.random_string_from_module(module)
            if not connection_screen:
                connection_screen = "No connection screen found. Please contact an admin."
        session.msg(connection_screen)
        return CLAIM


loginstart_providers = (DefaultLoginStartRules(),)
