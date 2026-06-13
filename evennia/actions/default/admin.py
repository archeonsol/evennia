"""
Default admin actions: engine-shipped staff verbs and their rule providers.

The action-engine analogue of ``evennia/commands/default/admin.py``'s
``CmdEmit``/``CmdWall``/``CmdForce``/``CmdPerm`` plus ``general.py``'s
``CmdAccess``:

* :class:`Emit` — ``@emit`` / ``@remit`` / ``@pemit`` broadcast to objects,
  rooms, or accounts (the alias forces the room/account restriction, exactly
  as the stock command keyed off ``cmdstring``).
* :class:`Wall` — announce to every connected session.
* :class:`Force` — make another object execute a command line.
* :class:`Perm` — view / grant / revoke permission strings on objects or
  accounts, with the same anti-escalation check, security logging, access-cache
  invalidation, and ``permissions_changed`` signal as the stock command.
* :class:`Access` — show the permission hierarchy and the caller's own perms.

Permission gates are ``requires=`` capability predicates (quell-aware,
re-resolved fresh per dispatch): Builder for emit/force, Admin for wall,
Developer for perm. The stock locks' per-command ``perm(emit)``-style named
grants are **not** reproduced — a game wanting them overrides the rule with a
``requires=from_lockstring(...)`` gate.

A game composes :class:`CharacterAdminRules` into its Character typeclass (the
same pattern as :class:`~evennia.actions.default.objects.CharacterObjectRules`;
mixing into the engine's ``DefaultCharacter`` directly would cycle imports with
the action modules that use it as ``__primary_handler__``).
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings

import evennia
from evennia.objects.character import DefaultCharacter

from ..action import action
from ..muxargs import ArgAction
from ..predicate import Admin as AdminCap
from ..predicate import Builder as BuilderCap
from ..predicate import Developer as DeveloperCap
from ..result import CLAIM, SKIP
from ..rule import rule

__all__ = [
    "Emit",
    "Wall",
    "Force",
    "Perm",
    "Access",
    "CharacterAdminRules",
]


@action("@emit", "@remit", "@pemit")
@dataclass
class Emit(ArgAction):
    """Emit a message to objects, rooms, or accounts.

    ``@emit[/room|/accounts|/contents] [<obj>, <obj>, ... =] <message>``. The
    ``@remit`` alias forces rooms-only + send-to-contents; ``@pemit`` forces
    accounts-only.
    """

    __primary_handler__ = DefaultCharacter


@action("@wall")
@dataclass
class Wall(ArgAction):
    """Announce a message to all connected sessions (``@wall <message>``)."""

    __primary_handler__ = DefaultCharacter


@action("@force")
@dataclass
class Force(ArgAction):
    """Force an object to execute a command (``@force <object> = <command>``)."""

    __primary_handler__ = DefaultCharacter


@action("@perm", "@setperm")
@dataclass
class Perm(ArgAction):
    """View or change permission strings on an object or account.

    ``@perm[/del|/account] <object|*account> [= <perm>[,<perm>,...]]``.
    """

    __primary_handler__ = DefaultCharacter


@action("@access", "@groups", "@hierarchy")
@dataclass
class Access(ArgAction):
    """Show the permission hierarchy and the caller's own memberships."""

    __primary_handler__ = DefaultCharacter


class CharacterAdminRules:
    """Baseline admin-verb rules, composed into the Character typeclass.

    Every rule guards ``self is actor.character`` so bystander characters in
    the room (also providers of this class) abstain. Gates are ``requires=``
    capability predicates, so a fully gated dispatch falls into the bridge's
    fail-closed no-match path for unprivileged actors.
    """

    def _is_actor(self, actor) -> bool:
        return self is getattr(actor, "character", None)

    # --- @emit / @remit / @pemit ---------------------------------------------

    @rule(Emit, phase="carry_out", requires=BuilderCap)
    def carry_out_emit(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        caller = self
        if not action.args:
            usage = (
                "Usage: "
                "\n@emit[/switches] [<obj>, <obj>, ... =] <message>"
                "\n@remit          [<obj>, <obj>, ... =] <message>"
                "\n@pemit          [<obj>, <obj>, ... =] <message>"
            )
            caller.msg(usage)
            return CLAIM

        # the stock command declared the switch as "room" but tested "rooms";
        # accept both spellings.
        rooms_only = "rooms" in action.switches or "room" in action.switches
        accounts_only = "accounts" in action.switches
        send_to_contents = "contents" in action.switches

        if action.verb == "@remit":
            rooms_only = True
            send_to_contents = True
        elif action.verb == "@pemit":
            accounts_only = True

        if not action.rhs:
            message = action.args
            objnames = [caller.location.key]
        else:
            message = action.rhs
            objnames = list(action.lhslist)

        for objname in objnames:
            obj = caller.search(objname, global_search=True)
            if not obj:
                return CLAIM
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
        return CLAIM

    # --- @wall -----------------------------------------------------------------

    @rule(Wall, phase="carry_out", requires=AdminCap)
    def carry_out_wall(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        caller = self
        if not action.args:
            caller.msg("Usage: @wall <message>")
            return CLAIM
        message = f'{caller.name} shouts "{action.args}"'
        caller.msg("Announcing to all connected sessions ...")
        evennia.SESSION_HANDLER.announce_all(message)
        return CLAIM

    # --- @force ----------------------------------------------------------------

    @rule(Force, phase="carry_out", requires=BuilderCap)
    def carry_out_force(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        caller = self
        if not action.lhs or not action.rhs:
            caller.msg("You must provide a target and a command string to execute.")
            return CLAIM
        targ = caller.search(action.lhs)
        if not targ:
            return CLAIM
        if not targ.access(caller, "edit"):
            caller.msg(f"You don't have permission to force {targ} to execute commands.")
            return CLAIM
        targ.execute_cmd(action.rhs)
        caller.msg(f"You have forced {targ} to: {action.rhs}")
        return CLAIM

    # --- @perm -----------------------------------------------------------------

    @rule(Perm, phase="carry_out", requires=DeveloperCap)
    def carry_out_perm(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        from evennia.commands.cmd_access_cache import invalidate_caller_access
        from evennia.commands.signals import permissions_changed
        from evennia.utils import logger

        caller = self
        switches = action.switches
        lhs, rhs = action.lhs, action.rhs

        if not action.args:
            caller.msg("Usage: @perm[/switch] object [ = permission, permission, ...]")
            return CLAIM

        accountmode = "account" in switches or lhs.startswith("*")
        lhs = lhs.lstrip("*")

        if accountmode:
            obj = caller.search_account(lhs)
        else:
            obj = caller.search(lhs, global_search=True)
        if not obj:
            return CLAIM

        if not rhs:
            if not obj.access(caller, "examine"):
                caller.msg("You are not allowed to examine this object.")
                return CLAIM

            string = f"Permissions on |w{obj.key}|n: "
            if not obj.permissions.all():
                string += "<None>"
            else:
                string += ", ".join(obj.permissions.all())
                if (
                    hasattr(obj, "account")
                    and hasattr(obj.account, "is_superuser")
                    and obj.account.is_superuser
                ):
                    string += "\n(... but this object is currently controlled by a SUPERUSER! "
                    string += "All access checks are passed automatically.)"
            caller.msg(string)
            return CLAIM

        locktype = "edit" if accountmode else "control"
        if not obj.access(caller, locktype):
            accountstr = "account" if accountmode else "object"
            caller.msg(f"You are not allowed to edit this {accountstr}'s permissions.")
            return CLAIM

        address = getattr(getattr(actor, "session", None), "address", "unknown")
        hierarchy = [p.lower() for p in settings.PERMISSION_HIERARCHY]
        caller_result = []
        target_result = []
        added = []
        removed = []
        if "del" in switches:
            for perm in action.rhslist:
                obj.permissions.remove(perm)
                if obj.permissions.get(perm):
                    caller_result.append(
                        f"\nPermissions {perm} could not be removed from {obj.name}."
                    )
                else:
                    caller_result.append(
                        f"\nPermission {perm} removed from {obj.name} (if they existed)."
                    )
                    target_result.append(
                        f"\n{caller.name} revokes the permission(s) {perm} from you."
                    )
                    removed.append(perm)
                    logger.log_sec(
                        f"Permissions Deleted: {perm}, {obj} (Caller: {caller}, IP: {address})."
                    )
        else:
            # permissions.all() returns lowercased strings; compare
            # case-insensitively so `Builder` matches a stored `builder`.
            permissions_lower = {p.lower() for p in obj.permissions.all()}

            for perm in action.rhslist:
                # block assigning a permission higher in the hierarchy than the
                # caller's own (self/peer escalation)
                if perm.lower() in hierarchy and not obj.locks.check_lockstring(
                    caller, f"dummy:perm({perm})"
                ):
                    caller.msg(
                        "You cannot assign a permission higher than the one you have yourself."
                    )
                    return CLAIM

                if perm.lower() in permissions_lower:
                    caller_result.append(f"\nPermission '{perm}' is already defined on {obj.name}.")
                else:
                    obj.permissions.add(perm)
                    added.append(perm)
                    plystring = "the Account" if accountmode else "the Object/Character"
                    caller_result.append(
                        f"\nPermission '{perm}' given to {obj.name} ({plystring})."
                    )
                    target_result.append(
                        f"\n{caller.name} gives you ({obj.name}, {plystring}) the permission '{perm}'."
                    )
                    logger.log_sec(
                        f"Permissions Added: {perm}, {obj} (Caller: {caller}, IP: {address})."
                    )

        # Flush the engine's per-caller access caches before any signal
        # subscriber observes the new permission state.
        if added or removed:
            invalidate_caller_access(obj)
            permissions_changed.send_robust(
                sender=type(action),
                target=obj,
                added=tuple(added),
                removed=tuple(removed),
                actor=caller,
                account_mode=accountmode,
            )

        caller.msg("".join(caller_result).strip())
        if target_result:
            obj.msg("".join(target_result).strip())
        return CLAIM

    # --- @access ----------------------------------------------------------------

    @rule(Access, phase="carry_out")
    def carry_out_access(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        from evennia.objects.object import DefaultObject
        from evennia.utils import utils

        caller = self
        hierarchy_full = settings.PERMISSION_HIERARCHY
        string = "\n|wPermission Hierarchy|n (climbing):\n %s" % ", ".join(hierarchy_full)

        if caller.account and caller.account.is_superuser:
            cperms = "<Superuser>"
            pperms = "<Superuser>"
        else:
            cperms = ", ".join(caller.permissions.all())
            if caller.account:
                pperms = ", ".join(caller.account.permissions.all())
            else:
                pperms = "<No account>"

        string += "\n|wYour access|n:"
        string += f"\nCharacter |c{caller.key}|n: {cperms}"
        if utils.inherits_from(caller, DefaultObject) and caller.account:
            string += f"\nAccount |c{caller.account.key}|n: {pperms}"
        caller.msg(string)
        return CLAIM
