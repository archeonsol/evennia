"""
Commands

Commands describe the input the account can do to the game.

"""

from evennia.commands.command import Command as BaseCommand

# from evennia import default_cmds


class Command(BaseCommand):
    """
    Base command (you may see this if a child command had no help text defined)

    Note that the class's `__doc__` string is used by Evennia to create the
    automatic help entry for the command, so make sure to document consistently
    here. Without setting one, the parent's docstring will show (like now).

    """

    # Each Command class implements the following methods, called in this order
    # (only func() is actually required):
    #
    #     - at_pre_parse(): Runs before parse(). Return truthy to abort.
    #     - parse(): Splits self.args into self.switches / self.lhs / self.rhs /
    #         self.lhslist / self.rhslist / self.arglist by default (MuxCommand-
    #         style syntax, opt out with `parse_mux_syntax = False`).
    #     - at_pre_cmd(): Runs after parse(), before func(). Override for
    #         input validation that needs parsed args.
    #     - func(): Performs the actual work.
    #     - at_post_cmd(): Extra actions, often things done after
    #         every command, like prompts.
    #
    pass


# -------------------------------------------------------------
#
# All default commands inherit directly from ``Command`` (or
# ``AccountCommand`` for account-context commands). MuxCommand and
# MuxAccountCommand were deleted in 6.0.0+underspire.6 — switch parsing
# and caller normalisation are now in the engine. Subclass
# ``evennia.commands.command.Command`` or ``AccountCommand`` directly.
#
# -------------------------------------------------------------
