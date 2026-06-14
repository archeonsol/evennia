"""Default ``LoginStartAction`` handling (CM1 Phase 8)."""

from __future__ import annotations

from django.conf import settings

from evennia.utils import utils

from ..parser import LoginStartAction
from ..result import CLAIM
from ..rule import rule

__all__ = ["DefaultLoginStartRules", "loginstart_providers", "render_connection_screen"]


def render_connection_screen(session):
    """Send the configured connection screen to ``session``.

    The reusable engine operation behind the connect-time screen: a game binds
    its own ``look`` verb to this, and the engine fires it on connect via
    :class:`~evennia.actions.parser.LoginStartAction`. Reads
    ``CONNECTION_SCREEN_MODULE`` (a ``connection_screen`` callable wins, else a
    random module-level string, else a fallback notice).

    Args:
        session (ServerSession): the session to render the screen to.
    """
    module = getattr(settings, "CONNECTION_SCREEN_MODULE", "server.conf.connection_screens")
    callables = utils.callables_from_module(module)
    if "connection_screen" in callables:
        connection_screen = callables["connection_screen"]()
    else:
        connection_screen = utils.random_string_from_module(module)
        if not connection_screen:
            connection_screen = "No connection screen found. Please contact an admin."
    session.msg(connection_screen)


class DefaultLoginStartRules:
    """Show the unlogged-in connection screen once per connect."""

    @rule(LoginStartAction, phase="carry_out", priority=0)
    def carry_out_loginstart(self, action, actor):
        del action
        session = actor.session or actor.effective
        if session is None:
            return CLAIM
        render_connection_screen(session)
        return CLAIM


loginstart_providers = (DefaultLoginStartRules(),)
