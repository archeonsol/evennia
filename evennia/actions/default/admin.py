"""
Default admin actions: engine-shipped staff verbs and their rule providers.

The action-engine analogue of the engine's administrative command surface:

* :class:`Emit` — ``@emit`` / ``@remit`` / ``@pemit`` broadcast to objects,
  rooms, or accounts (the alias forces the room/account restriction, exactly
  as the stock command keyed off ``cmdstring``).
* :class:`Wall` — announce to every connected session.
* :class:`Force` — make another object execute a command line.
* :class:`Grant` — grant/revoke registered capabilities with explicit scopes.
* :class:`Policy` — inspect and author typed resource policies.
* :class:`Scope` — inspect and author resource scope labels.
* :class:`Access` — explain the caller's effective capability grants.

Administrative gates are namespaced capability predicates and remain
quell-aware. There is no tier hierarchy or dynamic permission-name fallback.

A game composes :class:`CharacterAdminRules` into its Character typeclass (the
same pattern as :class:`~evennia.actions.default.objects.CharacterObjectRules`;
mixing into the engine's ``DefaultCharacter`` directly would cycle imports with
the action modules that use it as ``__primary_handler__``).
"""

from __future__ import annotations

from dataclasses import dataclass

import evennia
from evennia.objects.character import DefaultCharacter

from ..action import action
from ..muxargs import ArgAction
from ..predicate import HasCapability
from ..result import CLAIM, SKIP
from ..rule import rule

__all__ = [
    "Emit",
    "Wall",
    "Force",
    "Grant",
    "Policy",
    "Scope",
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


@action("@grant", "@grants")
@dataclass
class Grant(ArgAction):
    """View or change explicit capability grants on an object or account.

    ``@perm[/del|/account] <object|*account> [= <perm>[,<perm>,...]]``.
    """

    __primary_handler__ = DefaultCharacter


@action("@policy", "@policies")
@dataclass
class Policy(ArgAction):
    """Inspect or author typed authorization policies on a resource."""

    __primary_handler__ = DefaultCharacter


@action("@scope", "@scopes")
@dataclass
class Scope(ArgAction):
    """Inspect or author searchable authorization scope labels."""

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

    @rule(Emit, phase="carry_out", requires=HasCapability("engine.world.build"))
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

    @rule(Wall, phase="carry_out", requires=HasCapability("engine.moderation.manage"))
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

    @rule(Force, phase="carry_out", requires=HasCapability("engine.world.build"))
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
            caller.msg(
                f"You don't have permission to force {targ} to execute commands."
            )
            return CLAIM
        targ.execute_cmd(action.rhs)
        caller.msg(f"You have forced {targ} to: {action.rhs}")
        return CLAIM

    # --- @grant ----------------------------------------------------------------

    @rule(Grant, phase="carry_out", requires=HasCapability("engine.runtime.manage"))
    def carry_out_grant(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        from evennia.authorization.capabilities import capability_registry
        from evennia.authorization.storage import (grant_capability,
                                                   principal_refs,
                                                   revoke_grant)
        from evennia.server.models import AuthorizationGrant

        caller = self
        switches = action.switches
        lhs, rhs = action.lhs, action.rhs

        if not action.args:
            caller.msg("Usage: @grant[/revoke] object [ = capability]")
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

            refs = principal_refs(obj)
            grants = AuthorizationGrant.objects.filter(
                principal_ref__in=refs, revoked_at__isnull=True
            ).order_by("capability")
            caller.msg(
                "\n".join(
                    f"{grant.grant_id} {grant.capability} "
                    f"[{grant.scope_kind}:{grant.scope_key}]"
                    for grant in grants
                )
                or "<No active capability grants>"
            )
            return CLAIM

        locktype = "edit" if accountmode else "control"
        if not obj.access(caller, locktype):
            accountstr = "account" if accountmode else "object"
            caller.msg(f"You are not allowed to edit this {accountstr}'s permissions.")
            return CLAIM

        refs = principal_refs(obj)
        prefix = "account:" if accountmode else "object:"
        principal_ref = next((ref for ref in refs if ref.startswith(prefix)), refs[0])
        actor_ref = principal_refs(caller)[0]
        if "del" in switches:
            grant = AuthorizationGrant.objects.filter(
                grant_id=rhs.strip(), principal_ref__in=refs
            ).first()
            changed = bool(
                grant
                and revoke_grant(
                    grant.grant_id,
                    actor_ref=actor_ref,
                    reason="action @grant/revoke",
                )
            )
            caller.msg(
                "Grant revoked." if changed else "No active matching grant was found."
            )
        else:
            declaration = rhs.strip().lower()
            if declaration.startswith("bundle:"):
                capabilities = capability_registry.expand_bundle(
                    declaration.split(":", 1)[1]
                )
            else:
                capabilities = (capability_registry.require(declaration).key,)
            for capability in sorted(capabilities):
                grant_capability(
                    principal_ref,
                    capability,
                    scope_kind="world",
                    scope_key="*",
                    provenance="action_command",
                    actor_ref=actor_ref,
                    reason="action @grant",
                )
            caller.msg(f"Granted {len(capabilities)} capability grant(s) to {obj}.")
        return CLAIM

    # --- @policy ---------------------------------------------------------------

    @rule(Policy, phase="carry_out", requires=HasCapability("engine.world.build"))
    def carry_out_policy(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        from evennia.authorization.policy import (Always, Never,
                                                  RequiresCapability)

        caller = self
        if not action.args:
            caller.msg("Usage: @policy[/set|del] <object>/<operation> [= policy]")
            return CLAIM

        lhs = action.lhs.strip()
        resource_name, separator, operation = lhs.rpartition("/")
        if not separator:
            resource_name, operation = lhs, ""
        if resource_name.startswith("*"):
            resource = caller.search_account(resource_name.lstrip("*"))
        else:
            resource = caller.search(resource_name, global_search=True)
        if not resource:
            return CLAIM
        if not (resource.access(caller, "control") or resource.access(caller, "edit")):
            caller.msg("You are not allowed to author policies on that resource.")
            return CLAIM

        if "set" in action.switches:
            if not operation or not action.rhs:
                caller.msg(
                    "Usage: @policy/set <object>/<operation> = "
                    "public|disabled|<capability>"
                )
                return CLAIM
            declaration = action.rhs.strip().lower()
            if declaration == "public":
                policy = Always()
            elif declaration == "disabled":
                policy = Never()
            else:
                try:
                    policy = RequiresCapability(declaration)
                except (TypeError, ValueError) as err:
                    caller.msg(f"Policy not changed: {err}")
                    return CLAIM
            try:
                resource.policies.set(operation, policy)
            except (TypeError, ValueError) as err:
                caller.msg(f"Policy not changed: {err}")
                return CLAIM
            caller.msg(f"Set {resource}/{operation} to {policy.to_data()!r}.")
            return CLAIM

        if "del" in action.switches:
            if not operation:
                caller.msg("Usage: @policy/del <object>/<operation>")
                return CLAIM
            removed = resource.policies.remove(operation)
            caller.msg(
                f"{'Removed' if removed else 'No override for'} {resource}/{operation}."
            )
            return CLAIM

        if operation:
            policy = resource.policies.get(operation)
            caller.msg(
                f"{resource}/{operation}: "
                f"{policy.to_data() if policy else '<class default>'}"
            )
            return CLAIM
        policies = resource.policies.all()
        if not policies:
            caller.msg(f"{resource} has no instance policy overrides.")
            return CLAIM
        caller.msg(
            "\n".join(
                f"{key}: {policy.to_data()!r}" for key, policy in policies.items()
            )
        )
        return CLAIM

    # --- @scope ----------------------------------------------------------------

    @rule(Scope, phase="carry_out", requires=HasCapability("engine.world.build"))
    def carry_out_scope(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        from evennia.authorization.storage import (resource_ref,
                                                   set_scope_labels)
        from evennia.server.models import AuthorizationScopeLabel

        caller = self
        if not action.args:
            caller.msg("Usage: @scope[/set|clear] <resource> [= <kind:key>, ...]")
            return CLAIM
        resource_name = action.lhs.strip()
        if resource_name.startswith("*"):
            resource = caller.search_account(resource_name.lstrip("*"))
        else:
            resource = caller.search(resource_name, global_search=True)
        if not resource:
            return CLAIM
        if not (resource.access(caller, "control") or resource.access(caller, "edit")):
            caller.msg("You are not allowed to author scopes on that resource.")
            return CLAIM
        if "set" in action.switches:
            labels = {part.strip().lower() for part in action.rhslist if part.strip()}
            if not labels or any(":" not in label for label in labels):
                caller.msg("Every scope label must use <kind>:<key> syntax.")
                return CLAIM
            set_scope_labels(resource, labels)
            caller.msg(
                f"Set authored scopes on {resource}: {', '.join(sorted(labels))}"
            )
            return CLAIM
        if "clear" in action.switches:
            set_scope_labels(resource, ())
            caller.msg(f"Cleared authored scopes on {resource}.")
            return CLAIM
        labels = AuthorizationScopeLabel.objects.filter(
            resource_ref=resource_ref(resource)
        ).order_by("label")
        caller.msg(
            "\n".join(f"{row.label} ({row.source})" for row in labels)
            or f"{resource} has no authorization scope labels."
        )
        return CLAIM

    # --- @access ----------------------------------------------------------------

    @rule(Access, phase="carry_out")
    def carry_out_access(self, action, actor):
        if not self._is_actor(actor):
            return SKIP
        from evennia.objects.object import DefaultObject
        from evennia.utils import utils

        caller = self
        from evennia.authorization.storage import load_grants

        grants = load_grants(caller)
        string = "\n|wYour capability grants|n:"
        for capability, scopes in sorted(grants.by_capability.items()):
            string += f"\n{capability}: {', '.join(f'{scope.kind}:{scope.key}' for scope in scopes)}"
        if not grants.by_capability:
            string += " <None>"
        caller.msg(string)
        return CLAIM
