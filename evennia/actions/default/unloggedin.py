"""
Default unlogged-in actions: the connect-screen verbs, session-side.

The action-engine analogue of ``evennia/commands/default/unloggedin.py``'s
``CmdUnconnectedConnect``/``Create``/``Info``/``Encoding``/``Screenreader``/
``Help``. The login-start screen itself (``CMD_LOGINSTART``) is already
handled by :mod:`evennia.actions.default.loginstart`, which the dispatch
bridge injects; the verbs here are what an unlogged player *types*.

For an unlogged actor the session is the effective object, so these rules
live on the ServerSession class: a game composes :class:`SessionLoginRules`
into its session typeclass (``settings.SERVER_SESSION_CLASS``)::

    class ServerSession(SessionLoginRules, BaseServerSession): ...

Every rule guards "this session, not logged in", so the same class is inert
once an account is attached. The ``create`` confirmation is a generator rule —
the engine's generator driver interprets ``yield prompt`` as ask-and-suspend,
matching the stock command's ``@interactive`` flow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.conf import settings

import evennia
from evennia.utils import class_from_module

from ..action import action
from ..muxargs import ArgAction
from ..result import CLAIM, SKIP
from ..rule import rule
from .general import Help

__all__ = [
    "Connect",
    "Create",
    "Info",
    "Encoding",
    "Screenreader",
    "Help",
    "SessionLoginRules",
]


@dataclass
class _RawArgAction(ArgAction):
    """Arg action that skips the mux lhs/rhs split (credentials may hold ``=``)."""

    @classmethod
    def parse(cls, raw_args, actor, context=None, switches=(), verb=None):
        return cls(
            args=(raw_args or "").strip(),
            switches=tuple(switches or ()),
            verb=(verb or ""),
        )


@action("connect", "conn", "con", "co")
@dataclass
class Connect(_RawArgAction):
    """Log in to an existing account (``connect <name> <password>``).

    Quoted names/passwords support spaces; ``connect guest`` requests a guest
    account when enabled.
    """


@action("create", "cre", "cr")
@dataclass
class Create(_RawArgAction):
    """Create a new account (``create <name> <password>``), with confirmation."""


@action("info")
@dataclass
class Info(_RawArgAction):
    """MUDINFO 1.1 output for crawlers (``info``)."""


@action("encoding", "encode")
@dataclass
class Encoding(_RawArgAction):
    """View or set the text encoding before login (``encoding [<encoding>]``)."""


@action("screenreader")
@dataclass
class Screenreader(_RawArgAction):
    """Toggle screenreader mode before login (``screenreader``)."""


def _split_credentials(args):
    """Split ``name password`` honoring double-quoted parts with spaces."""
    parts = [part.strip() for part in re.split(r"\"", args) if part.strip()]
    if len(parts) == 1:
        parts = parts[0].split(None, 1)
    return parts


class SessionLoginRules:
    """Connect-screen rules, composed into the ServerSession typeclass.

    Each rule guards "self is the actor's session and no account is logged
    in", so a logged-in actor never triggers them (their session is not the
    effective object then anyway).
    """

    def _is_unlogged(self, actor) -> bool:
        return getattr(actor, "account", None) is None and self is getattr(actor, "session", None)

    # --- connect -----------------------------------------------------------------

    @rule(Connect, phase="carry_out")
    def carry_out_connect(self, action, actor):
        if not self._is_unlogged(actor):
            return SKIP
        session = self
        address = session.address

        parts = _split_credentials(action.args)
        if len(parts) == 1 and parts and parts[0].lower() == "guest":
            Guest = class_from_module(settings.BASE_GUEST_TYPECLASS)
            account, errors = Guest.authenticate(ip=address)
            if account:
                session.sessionhandler.login(session, account)
            else:
                session.msg("|R%s|n" % "\n".join(errors))
            return CLAIM

        if len(parts) != 2:
            session.msg("\n\r Usage (without <>): connect <name> <password>")
            return CLAIM

        Account = class_from_module(settings.BASE_ACCOUNT_TYPECLASS)
        name, password = parts
        account, errors = Account.authenticate(
            username=name, password=password, ip=address, session=session
        )
        if account:
            session.sessionhandler.login(session, account)
        else:
            session.msg("|R%s|n" % "\n".join(errors))
        return CLAIM

    # --- create ------------------------------------------------------------------

    @rule(Create, phase="carry_out")
    def carry_out_create(self, action, actor):
        if not self._is_unlogged(actor):
            return SKIP
        session = self
        if not settings.NEW_ACCOUNT_REGISTRATION_ENABLED:
            session.msg("Registration is currently disabled.")
            return CLAIM

        parts = _split_credentials(action.args)
        if len(parts) != 2:
            session.msg(
                "\n Usage (without <>): create <name> <password>"
                "\nIf <name> or <password> contains spaces, enclose it in double quotes."
            )
            return CLAIM

        Account = class_from_module(settings.BASE_ACCOUNT_TYPECLASS)
        username, password = parts

        # pre-normalize the username so the user knows what they get
        non_normalized_username = username
        username = Account.normalize_username(username)
        if non_normalized_username != username:
            session.msg(
                "Note: your username was normalized to strip spaces and remove characters "
                "that could be visually confusing."
            )

        return self._create_confirm_flow(session, Account, username, password)

    @staticmethod
    def _create_confirm_flow(session, Account, username, password):
        """Confirm and create the account (generator: the yield suspends on input)."""
        answer = yield (
            f"You want to create an account '{username}' with password '{password}'."
            "\nIs this what you intended? [Y]/N?"
        )
        if (answer or "").lower() in ("n", "no"):
            session.msg("Aborted. If your user name contains spaces, surround it by quotes.")
            return CLAIM

        account, errors = Account.create(
            username=username, password=password, ip=session.address, session=session
        )
        if account:
            string = "A new account '%s' was created. Welcome!"
            if " " in username:
                string += (
                    "\n\nYou can now log in with the command 'connect \"%s\" <your password>'."
                )
            else:
                string += "\n\nYou can now log with the command 'connect %s <your password>'."
            session.msg(string % (username, username))
        else:
            session.msg("|R%s|n" % "\n".join(errors))
        return CLAIM

    # --- info --------------------------------------------------------------------

    @rule(Info, phase="carry_out")
    def carry_out_info(self, action, actor):
        if not self._is_unlogged(actor):
            return SKIP
        import datetime

        from evennia.utils import gametime, utils

        self.msg(
            "## BEGIN INFO 1.1\nName: %s\nUptime: %s\nConnected: %d\nVersion: Evennia %s\n## END"
            " INFO"
            % (
                settings.SERVERNAME,
                datetime.datetime.fromtimestamp(gametime.SERVER_START_TIME).ctime(),
                evennia.SESSION_HANDLER.account_count(),
                utils.get_evennia_version(),
            )
        )
        return CLAIM

    # --- encoding ----------------------------------------------------------------

    @rule(Encoding, phase="carry_out")
    def carry_out_encoding(self, action, actor):
        if not self._is_unlogged(actor):
            return SKIP
        from codecs import lookup as codecs_lookup

        session = self
        sync = False
        if "clear" in action.switches:
            old_encoding = session.protocol_flags.get("ENCODING", None)
            if old_encoding:
                string = "Your custom text encoding ('%s') was cleared." % old_encoding
            else:
                string = "No custom encoding was set."
            session.protocol_flags["ENCODING"] = "utf-8"
            sync = True
        elif not action.args:
            pencoding = session.protocol_flags.get("ENCODING", None)
            string = ""
            if pencoding:
                string += (
                    "Default encoding: |g%s|n (change with |wencoding <encoding>|n)" % pencoding
                )
            encodings = settings.ENCODINGS
            if encodings:
                string += (
                    "\nServer's alternative encodings (tested in this order):\n   |g%s|n"
                    % ", ".join(encodings)
                )
            if not string:
                string = "No encodings found."
        else:
            old_encoding = session.protocol_flags.get("ENCODING", None)
            encoding = action.args
            try:
                codecs_lookup(encoding)
            except LookupError:
                string = (
                    "|rThe encoding '|w%s|r' is invalid. Keeping the previous encoding '|w%s|r'.|n"
                    % (encoding, old_encoding)
                )
            else:
                session.protocol_flags["ENCODING"] = encoding
                string = "Your custom text encoding was changed from '|w%s|n' to '|w%s|n'." % (
                    old_encoding,
                    encoding,
                )
                sync = True
        if sync:
            session.sessionhandler.session_portal_sync(session)
        session.msg(string.strip())
        return CLAIM

    # --- screenreader ------------------------------------------------------------

    @rule(Screenreader, phase="carry_out")
    def carry_out_screenreader(self, action, actor):
        if not self._is_unlogged(actor):
            return SKIP
        session = self
        new_setting = not session.protocol_flags.get("SCREENREADER", False)
        session.protocol_flags["SCREENREADER"] = new_setting
        session.msg("Screenreader mode turned |w%s|n." % ("on" if new_setting else "off"))
        session.sessionhandler.session_portal_sync(session)
        return CLAIM

    # --- help --------------------------------------------------------------------

    @rule(Help, phase="carry_out", priority=100)
    def carry_out_unlogged_help(self, action, actor):
        if not self._is_unlogged(actor):
            return SKIP
        string = """
You are not yet logged into the game. Commands available at this point:

  |wcreate|n - create a new account
  |wconnect|n - connect with an existing account
  |wlook|n - re-show the connection screen
  |whelp|n - show this help
  |wencoding|n - change the text encoding to match your client
  |wscreenreader|n - make the server more suitable for use with screen readers
  |wquit|n - abort the connection

First create an account e.g. with |wcreate Anna c67jHL8p|n
(If you have spaces in your name, use double quotes: |wcreate "Anna the Barbarian" c67jHL8p|n
Next you can connect to the game: |wconnect Anna c67jHL8p|n

You can use the |wlook|n command if you want to see the connect screen again.

"""
        if settings.STAFF_CONTACT_EMAIL:
            string += "For support, please contact: %s" % settings.STAFF_CONTACT_EMAIL
        self.msg(string)
        return CLAIM
