"""
The command template for the default MUX-style command set. There
is also an Account/OOC version that makes sure caller is an Account object.
"""

from evennia.commands.command import Command
from evennia.utils import utils

# limit symbol import for API
__all__ = ("MuxCommand", "MuxAccountCommand")


class MuxCommand(Command):
    """
    This sets up the basis for a MUX command. The idea
    is that most other Mux-related commands should just
    inherit from this and don't have to implement much
    parsing of their own unless they do something particularly
    advanced.

    Note that the class's __doc__ string (this text) is
    used by Evennia to create the automatic help entry for
    the command, so make sure to document consistently here.
    """

    def has_perm(self, srcobj):
        """
        This is called by the cmdhandler to determine
        if srcobj is allowed to execute this command.
        We just show it here for completeness - we
        are satisfied using the default check in Command.
        """
        return super().has_perm(srcobj)

    def at_post_cmd(self):
        """
        This hook is called after the command has finished executing
        (after self.func()).
        """
        pass

    def parse(self):
        """MuxCommand parse: delegate switch syntax to ``Command.parse``.

        Switch parsing was promoted to the base ``Command.parse`` in
        ``6.0.0+underspire.5``. This override now delegates to
        ``super().parse()`` and runs the legacy ``account_caller``
        normalisation block for third-party subclasses that still rely
        on it.

        The ``account_caller`` block is skipped when the engine's
        pre-parse normalisation already ran for this cmd
        (``account_command_caller = True``, shipped in
        ``6.0.0+underspire.3``). The engine path produces the same
        ``self.caller`` / ``self.account`` / ``self.character`` shape,
        so re-running here would only re-call ``get_puppet`` for no
        benefit. The flag-based short-circuit keeps third-party
        ``account_caller``-only subclasses on the legacy path during
        the deprecation window.

        See ``Command.parse`` for the full set of parsed attributes
        (``self.switches``, ``self.lhs`` / ``self.rhs``, etc.) and the
        ``switch_options`` / ``rhs_split`` opt-in class attributes.
        """
        super().parse()
        if not hasattr(self, "account_caller"):
            self.account_caller = False
        if self.account_caller and not getattr(self, "account_command_caller", False):
            if utils.inherits_from(self.caller, "evennia.objects.objects.DefaultObject"):
                # caller is an Object/Character
                self.character = self.caller
                self.caller = self.caller.account
            elif utils.inherits_from(self.caller, "evennia.accounts.accounts.DefaultAccount"):
                # caller was already an Account
                self.character = self.caller.get_puppet(self.session)
            else:
                self.character = None

    def get_command_info(self):
        """
        Update of parent class's get_command_info() for MuxCommand.
        """
        variables = "\n".join(
            " |w{}|n ({}): {}".format(key, type(val), val) for key, val in self.__dict__.items()
        )
        string = f"""
Command {self} has no defined `func()` - showing on-command variables: No child func() defined for {self} - available variables:
{variables}
        """
        self.msg(string)
        # a simple test command to show the available properties
        string = "-" * 50
        string += f"\n|w{self.key}|n - Command variables from evennia:\n"
        string += "-" * 50
        string += f"\nname of cmd (self.key): |w{self.key}|n\n"
        string += f"cmd aliases (self.aliases): |w{self.aliases}|n\n"
        string += f"cmd locks (self.locks): |w{self.locks}|n\n"
        string += f"help category (self.help_category): |w{self.help_category}|n\n"
        string += f"object calling (self.caller): |w{self.caller}|n\n"
        string += f"object storing cmdset (self.obj): |w{self.obj}|n\n"
        string += f"command string given (self.cmdstring): |w{self.cmdstring}|n\n"
        # show cmdset.key instead of cmdset to shorten output
        string += utils.fill(f"current cmdset (self.cmdset): |w{self.cmdset}|n\n")
        string += "\n" + "-" * 50
        string += "\nVariables from MuxCommand baseclass\n"
        string += "-" * 50
        string += f"\nraw argument (self.raw): |w{self.raw}|n \n"
        string += f"cmd args (self.args): |w{self.args}|n\n"
        string += f"cmd switches (self.switches): |w{self.switches}|n\n"
        string += f"cmd options (self.switch_options): |w{self.switch_options}|n\n"
        string += f"cmd parse left/right using (self.rhs_split): |w{self.rhs_split}|n\n"
        string += f"space-separated arg list (self.arglist): |w{self.arglist}|n\n"
        string += f"lhs, left-hand side of '=' (self.lhs): |w{self.lhs}|n\n"
        string += f"lhs, comma separated (self.lhslist): |w{self.lhslist}|n\n"
        string += f"rhs, right-hand side of '=' (self.rhs): |w{self.rhs}|n\n"
        string += f"rhs, comma separated (self.rhslist): |w{self.rhslist}|n\n"
        string += "-" * 50
        self.msg(string)

    def func(self):
        """
        This is the hook function that actually does all the work. It is called
         by the cmdhandler right after self.parser() finishes, and so has access
         to all the variables defined therein.
        """
        self.get_command_info()


class MuxAccountCommand(MuxCommand):
    """
    This is an on-Account version of the MuxCommand. Since these commands sit
    on Accounts rather than on Characters/Objects, we need to check
    this in the parser.

    Account commands are available also when puppeting a Character, it's
    just that they are applied with a lower priority and are always
    available, also when disconnected from a character (i.e. "ooc").

    This class makes sure that caller is always an Account object, while
    creating a new property "character" that is set only if a
    character is actually attached to this Account and Session.
    """

    account_caller = True  # Using MuxAccountCommand explicitly defaults the caller to an account
    # Opt into engine pre-parse normalisation
    # (``cmdhandler._normalize_account_command_caller``, shipped in
    # ``6.0.0+underspire.3``). With both flags set, the engine handles the
    # caller/account/character rewrite before any hook fires, and
    # ``MuxCommand.parse``'s legacy normalisation block short-circuits via
    # the ``not getattr(self, "account_command_caller", False)`` guard.
    # Downstream code can detect "this is an account command" uniformly via
    # ``getattr(cmd, "account_command_caller", False)`` — covering both
    # ``evennia.commands.command.AccountCommand`` subclasses and stock
    # ``MuxAccountCommand`` subclasses.
    account_command_caller = True
