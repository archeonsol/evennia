"""
Default account-shell actions: engine-shipped OOC account verbs.

The action-engine analogue of ``evennia/commands/default/account.py``'s
``CmdOption``/``CmdPassword`` and ``admin.py``'s ``CmdNewPassword``:

* :class:`Option` — view/set client protocol options on the current session,
  with ``/save`` and ``/clear`` persistence on the account.
* :class:`Password` — change your own password (old = new), gated on the
  account-scope Player rank (the ``pperm(Player)`` analogue: quell does not
  lock you out of your own password).
* :class:`UserPassword` — staff verb to set another account's password
  (Admin gate).

The actions carry ``__primary_handler__ = DefaultAccount`` so the context
builder includes the account as a provider even while puppeted — the engine
analogue of these commands living in the Account cmdset. A game composes
:class:`DefaultAccountRules` into its Account typeclass; it inherits
:class:`~evennia.actions.default.general.NickRules` because the stock Account
cmdset also carried ``@nick``.
"""

from __future__ import annotations

from codecs import lookup as codecs_lookup
from dataclasses import dataclass

from evennia.accounts.accounts import DefaultAccount

from ..action import action
from ..muxargs import ArgAction
from ..permission import Scope, get_capability_enum
from ..predicate import NEVER
from ..predicate import Admin as AdminCap
from ..predicate import HasCapability
from ..result import CLAIM, SKIP
from ..rule import rule
from .general import NickRules

__all__ = [
    "Option",
    "Password",
    "UserPassword",
    "PlayerAccountCap",
    "DefaultAccountRules",
]

_PLAYER_CAP = getattr(get_capability_enum(), "PLAYER", None)
#: ``pperm(Player)`` analogue: the Player rank resolved at ACCOUNT scope.
PlayerAccountCap = (
    HasCapability(_PLAYER_CAP, scope=Scope.ACCOUNT) if _PLAYER_CAP is not None else NEVER
)


@action("@option", "@options")
@dataclass
class Option(ArgAction):
    """View or set client interface options (``@option[/save|/clear] [name = value]``)."""

    __primary_handler__ = DefaultAccount


@action("@password")
@dataclass
class Password(ArgAction):
    """Change your own password (``@password <old> = <new>``)."""

    __primary_handler__ = DefaultAccount


@action("@userpassword")
@dataclass
class UserPassword(ArgAction):
    """Set another account's password (``@userpassword <account> = <new password>``)."""

    __primary_handler__ = DefaultAccount


class DefaultAccountRules(NickRules):
    """Baseline account-shell rules, composed into the Account typeclass.

    Every rule guards ``self is actor.account`` so the account answers both
    while puppeted (inserted by the context builder via the primary handler)
    and OOC (as the actor's effective object).
    """

    def _is_account(self, actor) -> bool:
        return self is getattr(actor, "account", None) or self is getattr(actor, "effective", None)

    # --- @option -----------------------------------------------------------------

    @rule(Option, phase="carry_out")
    def carry_out_option(self, action, actor):
        if not self._is_account(actor):
            return SKIP
        from evennia.utils import evtable, utils

        caller = self
        session = getattr(actor, "session", None)
        if session is None:
            return CLAIM

        def msg(text):
            caller.msg(text, session=session)

        flags = session.protocol_flags

        # display current options
        if not action.args:
            if "save" in action.switches:
                caller.db._saved_protocol_flags = flags
                msg("|gSaved all options. Use option/clear to remove.|n")
            if "clear" in action.switches:
                caller.db._saved_protocol_flags = {}
                msg("|gCleared all saved options.")

            options = dict(flags)
            saved_options = dict(caller.attributes.get("_saved_protocol_flags", default={}))

            if "SCREENWIDTH" in options:
                if len(options["SCREENWIDTH"]) == 1:
                    options["SCREENWIDTH"] = options["SCREENWIDTH"][0]
                else:
                    options["SCREENWIDTH"] = "  \n".join(
                        "%s : %s" % (screenid, size)
                        for screenid, size in options["SCREENWIDTH"].items()
                    )
            if "SCREENHEIGHT" in options:
                if len(options["SCREENHEIGHT"]) == 1:
                    options["SCREENHEIGHT"] = options["SCREENHEIGHT"][0]
                else:
                    options["SCREENHEIGHT"] = "  \n".join(
                        "%s : %s" % (screenid, size)
                        for screenid, size in options["SCREENHEIGHT"].items()
                    )
            options.pop("TTYPE", None)

            header = ("Name", "Value", "Saved") if saved_options else ("Name", "Value")
            table = evtable.EvTable(*header)
            for key in sorted(options):
                row = [key, options[key]]
                if saved_options:
                    saved = " |YYes|n" if key in saved_options else ""
                    changed = (
                        "|y*|n" if key in saved_options and flags[key] != saved_options[key] else ""
                    )
                    row.append("%s%s" % (saved, changed))
                table.add_row(*row)
            msg(f"|wClient settings ({session.protocol_key}):|n\n{table}|n")
            return CLAIM

        if not action.rhs:
            msg("Usage: @option [name = [value]]")
            return CLAIM

        # assign a new value
        name = action.lhs.upper()
        val = action.rhs.strip()

        def validate_encoding(new_encoding):
            try:
                codecs_lookup(new_encoding)
            except LookupError:
                raise RuntimeError(f"The encoding '|w{new_encoding}|n' is invalid. ")
            return new_encoding

        def validate_size(new_size):
            return {0: int(new_size)}

        def validate_bool(new_bool):
            return True if new_bool.lower() in ("true", "on", "1") else False

        def update(new_name, new_val, validator):
            try:
                old_val = flags.get(new_name, False)
                new_val = validator(new_val)
                if old_val == new_val:
                    msg(f"Option |w{new_name}|n was kept as '|w{old_val}|n'.")
                else:
                    flags[new_name] = new_val
                    # a manually assigned display size turns off auto-resizing
                    if new_name in ("SCREENWIDTH", "SCREENHEIGHT"):
                        flags["AUTORESIZE"] = False
                    msg(
                        f"Option |w{new_name}|n was changed from '|w{old_val}|n' to"
                        f" '|w{new_val}|n'."
                    )
                return {new_name: new_val}
            except Exception as err:
                msg(f"|rCould not set option |w{new_name}|r:|n {err}")
                return False

        validators = {
            "ANSI": validate_bool,
            "CLIENTNAME": utils.to_str,
            "ENCODING": validate_encoding,
            "MCCP": validate_bool,
            "NOGOAHEAD": validate_bool,
            "NOPROMPTGOAHEAD": validate_bool,
            "MXP": validate_bool,
            "NOCOLOR": validate_bool,
            "NOPKEEPALIVE": validate_bool,
            "OOB": validate_bool,
            "RAW": validate_bool,
            "SCREENHEIGHT": validate_size,
            "SCREENWIDTH": validate_size,
            "AUTORESIZE": validate_bool,
            "SCREENREADER": validate_bool,
            "TERM": utils.to_str,
            "UTF-8": validate_bool,
            "XTERM256": validate_bool,
            "INPUTDEBUG": validate_bool,
            "FORCEDENDLINE": validate_bool,
            "LOCALECHO": validate_bool,
            "TRUECOLOR": validate_bool,
        }

        optiondict = False
        if val and name in validators:
            optiondict = update(name, val, validators[name])
        else:
            msg("|rNo option named '|w%s|r'." % name)
        if optiondict:
            if "save" in action.switches:
                saved_options = caller.attributes.get("_saved_protocol_flags", default={})
                saved_options.update(optiondict)
                caller.attributes.add("_saved_protocol_flags", saved_options)
                for key in optiondict:
                    msg(f"|gSaved option {key}.|n")
            if "clear" in action.switches:
                for key in optiondict:
                    caller.attributes.get("_saved_protocol_flags", {}).pop(key, None)
                    msg(f"|gCleared saved {key}.")
            session.update_flags(**optiondict)
        return CLAIM

    # --- @password ----------------------------------------------------------------

    @rule(Password, phase="carry_out", requires=PlayerAccountCap)
    def carry_out_password(self, action, actor):
        if not self._is_account(actor):
            return SKIP
        from evennia.utils import logger

        account = self
        session = getattr(actor, "session", None)

        def msg(text):
            account.msg(text, session=session)

        if not action.rhs:
            msg("Usage: @password <oldpass> = <newpass>")
            return CLAIM
        oldpass = action.lhslist[0]
        newpass = action.rhslist[0]

        validated, error = account.validate_password(newpass)

        if not account.check_password(oldpass):
            msg("The specified old password isn't correct.")
        elif not validated:
            errors = [e for suberror in error.messages for e in error.messages]
            msg("\n".join(errors))
        else:
            account.set_password(newpass)
            account.save()
            msg("Password changed.")
            address = getattr(session, "address", "unknown")
            logger.log_sec(f"Password Changed: {account} (Caller: {account}, IP: {address}).")
        return CLAIM

    # --- @userpassword ---------------------------------------------------------------

    @rule(UserPassword, phase="carry_out", requires=AdminCap)
    def carry_out_userpassword(self, action, actor):
        if not self._is_account(actor):
            return SKIP
        from evennia.utils import logger

        caller = self
        session = getattr(actor, "session", None)

        def msg(text):
            caller.msg(text, session=session)

        if not action.rhs:
            msg("Usage: @userpassword <user obj> = <new password>")
            return CLAIM

        account = caller.search_account(action.lhs)
        if not account:
            return CLAIM

        newpass = action.rhs

        validated, error = account.validate_password(newpass)
        if not validated:
            errors = [e for suberror in error.messages for e in error.messages]
            msg("\n".join(errors))
            return CLAIM

        account.set_password(newpass)
        account.save()
        msg(f"{account.name} - new password set to '{newpass}'.")
        if account.character != caller:
            account.msg(f"{caller.name} has changed your password to '{newpass}'.")
        address = getattr(session, "address", "unknown")
        logger.log_sec(f"Password Changed: {account} (Caller: {caller}, IP: {address}).")
        return CLAIM
