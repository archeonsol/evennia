"""

Admin commands

"""

import re
import time

from django.conf import settings

import evennia
from evennia.authorization.capabilities import capability_registry
from evennia.authorization.storage import (grant_capability, principal_refs,
                                           revoke_grant)
from evennia.server.models import AuthorizationGrant, ServerConfig
from evennia.utils import class_from_module, evtable, logger, search

COMMAND_DEFAULT_CLASS = class_from_module(settings.COMMAND_DEFAULT_CLASS)

# limit members for API inclusion
__all__ = (
    "CmdBoot",
    "CmdBan",
    "CmdUnban",
    "CmdEmit",
    "CmdNewPassword",
    "CmdGrant",
    "CmdWall",
    "CmdForce",
)


class CmdBoot(COMMAND_DEFAULT_CLASS):
    """
    kick an account from the server.

    Usage:
      @boot[/switches] <account obj> [: reason]

    Switches:
      quiet - Silently boot without informing account
      sid - boot by session id instead of name or dbref

    Boot an account object from the server. If a reason is
    supplied it will be echoed to the user unless /quiet is set.
    """

    key = "@boot"
    switch_options = ("quiet", "sid")
    authorization = "engine.moderation.manage"
    help_category = "Admin"

    def func(self):
        """Implementing the function"""
        caller = self.caller
        args = self.args

        if not args:
            caller.msg("Usage: @boot[/switches] <account> [:reason]")
            return

        if ":" in args:
            args, reason = [a.strip() for a in args.split(":", 1)]
        else:
            args, reason = args, ""

        boot_list = []

        if "sid" in self.switches:
            # Boot a particular session id.
            sessions = evennia.SESSION_HANDLER.get_sessions(True)
            for sess in sessions:
                # Find the session with the matching session id.
                if sess.sessid == int(args):
                    boot_list.append(sess)
                    break
        else:
            # Boot by account object
            pobj = search.account_search(args)
            if not pobj:
                caller.msg(f"Account {args} was not found.")
                return
            pobj = pobj[0]
            if not pobj.access(caller, "boot"):
                caller.msg(f"You don't have the permission to boot {pobj.key}.")
                return
            # we have a bootable object with a connected user
            matches = evennia.SESSION_HANDLER.sessions_from_account(pobj)
            for match in matches:
                boot_list.append(match)

        if not boot_list:
            caller.msg(
                "No matching sessions found. The Account does not seem to be online."
            )
            return

        # Carry out the booting of the sessions in the boot list.

        feedback = None
        if "quiet" not in self.switches:
            feedback = f"You have been disconnected by {caller.name}.\n"
            if reason:
                feedback += f"\nReason given: {reason}"

        for session in boot_list:
            session.msg(feedback)
            session.account.disconnect_session_from_account(session)

        if pobj and boot_list:
            logger.log_sec(
                f"Booted: {pobj} (Reason: {reason}, Caller: {caller}, IP: {self.session.address})."
            )


# regex matching IP addresses with wildcards, eg. 233.122.4.*
IPREGEX = re.compile(r"[0-9*]{1,3}\.[0-9*]{1,3}\.[0-9*]{1,3}\.[0-9*]{1,3}")


def list_bans(cmd, banlist):
    """
    Helper function to display a list of active bans. Input argument
    is the banlist read into the two commands ban and unban below.

    Args:
        cmd (Command): Instance of the Ban command.
        banlist (list): List of bans to list.
    """
    if not banlist:
        return "No active bans were found."

    table = cmd.styled_table("|wid", "|wname/ip", "|wdate", "|wreason")
    for inum, ban in enumerate(banlist):
        table.add_row(str(inum + 1), ban[0] and ban[0] or ban[1], ban[3], ban[4])
    return f"|wActive bans:|n\n{table}"


class CmdBan(COMMAND_DEFAULT_CLASS):
    """
    ban an account from the server

    Usage:
      @ban [<name or ip> [: reason]]

    Without any arguments, shows numbered list of active bans.

    This command bans a user from accessing the game. Supply an optional
    reason to be able to later remember why the ban was put in place.

    It is often preferable to ban an account from the server than to
    delete an account with accounts/delete. If banned by name, that account
    account can no longer be logged into.

    IP (Internet Protocol) address banning allows blocking all access
    from a specific address or subnet. Use an asterisk (*) as a
    wildcard.

    Examples:
      @ban thomas             - ban account 'thomas'
      @ban/ip 134.233.2.111   - ban specific ip address
      @ban/ip 134.233.2.*     - ban all in a subnet
      @ban/ip 134.233.*.*     - even wider ban

    A single IP filter can be easy to circumvent by changing computers
    or requesting a new IP address. Setting a wide IP block filter with
    wildcards might be tempting, but remember that it may also
    accidentally block innocent users connecting from the same country
    or region.

    """

    key = "@ban"
    aliases = ["@bans"]
    authorization = "engine.runtime.manage"
    help_category = "Admin"

    def func(self):
        """
        Bans are stored in a serverconf db object as a list of
        dictionaries:
          [ (name, ip, ipregex, date, reason),
            (name, ip, ipregex, date, reason),...  ]
        where name and ip are set by the user and are shown in
        lists. ipregex is a converted form of ip where the * is
        replaced by an appropriate regex pattern for fast
        matching. date is the time stamp the ban was instigated and
        'reason' is any optional info given to the command. Unset
        values in each tuple is set to the empty string.
        """
        banlist = ServerConfig.objects.conf("server_bans")
        if not banlist:
            banlist = []

        if not self.args or (
            self.switches
            and not any(switch in ("ip", "name") for switch in self.switches)
        ):
            self.msg(list_bans(self, banlist))
            return

        now = time.ctime()
        reason = ""
        if ":" in self.args:
            ban, reason = self.args.rsplit(":", 1)
        else:
            ban = self.args
        ban = ban.lower()
        ipban = IPREGEX.findall(ban)
        if not ipban:
            # store as name
            typ = "Name"
            bantup = (ban, "", "", now, reason)
        else:
            # an ip address.
            typ = "IP"
            ban = ipban[0]
            # replace * with regex form and compile it
            ipregex = ban.replace(".", r"\.")
            ipregex = ipregex.replace("*", "[0-9]{1,3}")
            ipregex = re.compile(r"%s" % ipregex)
            bantup = ("", ban, ipregex, now, reason)

        ret = yield (f"Are you sure you want to {typ}-ban '|w{ban}|n' [Y]/N?")
        if str(ret).lower() in ("no", "n"):
            self.msg("Aborted.")
            return

        # save updated banlist
        banlist.append(bantup)
        ServerConfig.objects.conf("server_bans", banlist)
        self.msg(f"{typ}-ban '|w{ban}|n' was added. Use |w@unban|n to reinstate.")
        logger.log_sec(
            f"Banned {typ}: {ban.strip()} (Caller: {self.caller}, IP: {self.session.address})."
        )


class CmdUnban(COMMAND_DEFAULT_CLASS):
    """
    remove a ban from an account

    Usage:
      @unban <banid>

    This will clear an account name/ip ban previously set with the @ban
    command.  Use this command without an argument to view a numbered
    list of bans. Use the numbers in this list to select which one to
    unban.

    """

    key = "@unban"
    authorization = "engine.runtime.manage"
    help_category = "Admin"

    def func(self):
        """Implement unbanning"""

        banlist = ServerConfig.objects.conf("server_bans")

        if not self.args:
            self.msg(list_bans(self, banlist))
            return

        try:
            num = int(self.args)
        except Exception:
            self.msg("You must supply a valid ban id to clear.")
            return

        if not banlist:
            self.msg("There are no bans to clear.")
        elif not (0 < num < len(banlist) + 1):
            self.msg(f"Ban id |w{self.args}|n was not found.")
        else:
            # all is ok, ask, then clear ban
            ban = banlist[num - 1]
            value = (" ".join([s for s in ban[:2]])).strip()

            ret = yield (f"Are you sure you want to unban {num}: '|w{value}|n' [Y]/N?")
            if str(ret).lower() in ("n", "no"):
                self.msg("Aborted.")
                return

            del banlist[num - 1]
            ServerConfig.objects.conf("server_bans", banlist)
            self.msg(f"Cleared ban {num}: '{value}'")
            logger.log_sec(
                f"Unbanned: {value.strip()} (Caller: {self.caller}, IP: {self.session.address})."
            )


class CmdEmit(COMMAND_DEFAULT_CLASS):
    """
    admin command for emitting message to multiple objects

    Usage:
      @emit[/switches] [<obj>, <obj>, ... =] <message>
      @remit          [<obj>, <obj>, ... =] <message>
      @pemit          [<obj>, <obj>, ... =] <message>

    Switches:
      room     -  limit emits to rooms only (default)
      accounts -  limit emits to accounts only
      contents -  send to the contents of matched objects too

    Emits a message to the selected objects or to
    your immediate surroundings. If the object is a room,
    send to its contents. @remit and @pemit are just
    limited forms of @emit, for sending to rooms and
    to accounts respectively.
    """

    key = "@emit"
    aliases = ["@pemit", "@remit"]
    switch_options = ("room", "accounts", "contents")
    authorization = "engine.world.build"
    help_category = "Admin"

    def func(self):
        """Implement the command"""

        caller = self.caller
        args = self.args

        if not args:
            string = "Usage: "
            string += "\n@emit[/switches] [<obj>, <obj>, ... =] <message>"
            string += "\n@remit          [<obj>, <obj>, ... =] <message>"
            string += "\n@pemit          [<obj>, <obj>, ... =] <message>"
            caller.msg(string)
            return

        rooms_only = "rooms" in self.switches
        accounts_only = "accounts" in self.switches
        send_to_contents = "contents" in self.switches

        # we check which command was used to force the switches
        if self.cmdstring == "@remit":
            rooms_only = True
            send_to_contents = True
        elif self.cmdstring == "@pemit":
            accounts_only = True

        if not self.rhs:
            message = self.args
            objnames = [caller.location.key]
        else:
            message = self.rhs
            objnames = self.lhslist

        # send to all objects
        for objname in objnames:
            obj = caller.search(objname, global_search=True)
            if not obj:
                return
            if rooms_only and obj.location is not None:
                caller.msg(f"{objname} is not a room. Ignored.")
                continue
            if accounts_only and not obj.is_puppeted:
                caller.msg(f"{objname} has no active account. Ignored.")
                continue
            if obj.access(caller, "tell"):
                obj.msg(message)
                if send_to_contents and hasattr(obj, "msg_contents"):
                    obj.msg_contents(message)
                    caller.msg(f"Emitted to {objname} and contents:\n{message}")
                else:
                    caller.msg(f"Emitted to {objname}:\n{message}")
            else:
                caller.msg(f"You are not allowed to emit to {objname}.")


class CmdNewPassword(COMMAND_DEFAULT_CLASS):
    """
    change the password of an account

    Usage:
      @userpassword <user obj> = <new password>

    Set an account's password.
    """

    key = "@userpassword"
    authorization = "engine.moderation.manage"
    help_category = "Admin"

    def func(self):
        """Implement the function."""

        caller = self.caller

        if not self.rhs:
            self.msg("Usage: @userpassword <user obj> = <new password>")
            return

        # the account search also matches 'me' etc.
        account = caller.search_account(self.lhs)
        if not account:
            return

        newpass = self.rhs

        # Validate password
        validated, error = account.validate_password(newpass)
        if not validated:
            errors = [e for suberror in error.messages for e in error.messages]
            string = "\n".join(errors)
            caller.msg(string)
            return

        account.set_password(newpass)
        account.save()
        self.msg(f"{account.name} - new password set to '{newpass}'.")
        if account.character != caller:
            account.msg(f"{caller.name} has changed your password to '{newpass}'.")
        logger.log_sec(
            f"Password Changed: {account} (Caller: {caller}, IP: {self.session.address})."
        )


class CmdGrant(COMMAND_DEFAULT_CLASS):
    """Manage explicit capability grants.

    Usage:
      @grant <object or *account>
      @grant <object or *account> = <capability>[/scope-kind/scope-key]
      @grant/revoke <object or *account> = <grant-id>

    Bundles are accepted as ``bundle:<key>`` and expand to explicit grants.
    """

    key = "@grant"
    aliases = ["@grants"]
    switch_options = ("revoke", "account")
    authorization = "engine.runtime.manage"
    help_category = "Admin"

    def _target(self):
        """Resolve the grant principal selected by command syntax."""

        name = self.lhs.strip()
        if "account" in self.switches or name.startswith("*"):
            return self.caller.search_account(name.lstrip("*"))
        return self.caller.search(name, global_search=True)

    def func(self):
        """List, create, or revoke durable positive grants."""

        if not self.args:
            self.msg("Usage: @grant[/revoke] <object or *account> [= capability]")
            return
        target = self._target()
        if not target:
            return
        refs = principal_refs(target)
        if not refs:
            self.msg("That target has no authorization principal reference.")
            return
        account_mode = "account" in self.switches or self.lhs.strip().startswith("*")
        prefix = "account:" if account_mode else "object:"
        principal_ref = next((ref for ref in refs if ref.startswith(prefix)), refs[0])
        if not self.rhs:
            grants = AuthorizationGrant.objects.filter(
                principal_ref__in=refs, revoked_at__isnull=True
            ).order_by("capability", "scope_kind", "scope_key")
            if not grants:
                self.msg(f"{target} has no active capability grants.")
                return
            self.msg(
                "\n".join(
                    f"{grant.grant_id} {grant.capability} "
                    f"[{grant.scope_kind}:{grant.scope_key}]"
                    for grant in grants
                )
            )
            return
        if "revoke" in self.switches:
            grant_id = self.rhs.strip()
            grant = AuthorizationGrant.objects.filter(
                grant_id=grant_id, principal_ref__in=refs
            ).first()
            if grant is None or not revoke_grant(
                grant_id,
                actor_ref=principal_refs(self.caller)[0],
                reason="staff @grant/revoke",
            ):
                self.msg("No active matching grant was found.")
                return
            self.msg(f"Revoked grant {grant_id} from {target}.")
            return

        declaration, *scope_parts = [part.strip() for part in self.rhs.split("/")]
        scope_kind, scope_key = "world", "*"
        if scope_parts:
            if len(scope_parts) != 2:
                self.msg("Scope must be /<scope-kind>/<scope-key>.")
                return
            scope_kind, scope_key = scope_parts
        capabilities = (
            capability_registry.expand_bundle(declaration.split(":", 1)[1])
            if declaration.lower().startswith("bundle:")
            else (capability_registry.require(declaration).key,)
        )
        for capability in sorted(capabilities):
            grant_capability(
                principal_ref,
                capability,
                scope_kind=scope_kind,
                scope_key=scope_key,
                provenance="staff_command",
                actor_ref=principal_refs(self.caller)[0],
                reason="staff @grant",
            )
        self.msg(f"Granted {', '.join(sorted(capabilities))} to {target}.")


class CmdWall(COMMAND_DEFAULT_CLASS):
    """
    make an announcement to all

    Usage:
      @wall <message>

    Announces a message to all connected sessions
    including all currently unlogged in.
    """

    key = "@wall"
    authorization = "engine.moderation.manage"
    help_category = "Admin"

    def func(self):
        """Implements command"""
        if not self.args:
            self.msg("Usage: @wall <message>")
            return
        message = f'{self.caller.name} shouts "{self.args}"'
        self.msg("Announcing to all connected sessions ...")
        evennia.SESSION_HANDLER.announce_all(message)


class CmdForce(COMMAND_DEFAULT_CLASS):
    """
    forces an object to execute a command

    Usage:
        @force <object>=<command string>

    Example:
        @force bob=get stick
    """

    key = "@force"
    authorization = "engine.world.build"
    help_category = "Building"
    perm_used = "edit"

    def func(self):
        """Implements the force command"""
        if not self.lhs or not self.rhs:
            self.msg("You must provide a target and a command string to execute.")
            return
        targ = self.caller.search(self.lhs)
        if not targ:
            return
        if not targ.access(self.caller, self.perm_used):
            self.msg(f"You don't have permission to force {targ} to execute commands.")
            return
        targ.execute_cmd(self.rhs)
        self.msg(f"You have forced {targ} to: {self.rhs}")
