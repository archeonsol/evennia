"""The capability runtime, made visible.

R3 shipped grants, scopes, policies, principal state, and an append-only audit
trail, and Django admin registered none of it. Five tables carrying the engine's
entire authority model, with no way to look at any of them.

The prober is the part worth having. ``r3-authorization.md`` records that
decisions already explain their result -- the explanation was built, it was
simply never surfaced. Debugging "why can this account do that", or the far
worse "why *can't* it", has until now meant reading grant rows by hand and
simulating the resolver in your head.

Reads are plain queries over plain models, so this panel works with the game
server down. The prober asks the live runtime and therefore does not.

"""

from __future__ import annotations

from evennia.console import audit
from evennia.console.registry import Panel, io_action

#: Rows returned per view.
MAX_ROWS = 200

#: The views this panel offers, mapped to their model and default ordering.
VIEWS = {
    "grants": ("server.authorizationgrant", "-created_at"),
    "scopes": ("server.authorizationscopelabel", "-id"),
    "policies": ("server.authorizationpolicyoverride", "-id"),
    "principals": ("server.authorizationprincipalstate", "-id"),
    "audit": ("server.authorizationauditevent", "-created_at"),
}


def _stamp(value):
    """Render one timestamp as plain text."""

    return value.isoformat() if value else ""


class AuthorizationPanel(Panel):
    """Grants, scopes, policies, audit, and a decision prober."""

    key = "authorization"
    label = "Authorization"
    description = "Grants and policies, and why one decision came out as it did."
    columns = ("principal_ref", "capability", "scope_kind", "expires_at")
    needs_io = False

    def rows(self, ctx):
        """Return one authorization view.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``view``,
                ``principal``, and ``capability``.

        Returns:
            dict: The rows, the views available, and the capability vocabulary.
        """

        from django.apps import apps

        params = ctx.params
        view = str(params.get("view") or "grants").strip().lower()
        if view not in VIEWS:
            raise LookupError(f"{view!r} is not an authorization view")
        label, ordering = VIEWS[view]
        model = apps.get_model(label)

        queryset = model._base_manager.all()
        principal = str(params.get("principal") or "").strip()
        if principal and any(f.name == "principal_ref" for f in model._meta.concrete_fields):
            queryset = queryset.filter(principal_ref=principal)
        capability = str(params.get("capability") or "").strip()
        if capability and any(f.name == "capability" for f in model._meta.concrete_fields):
            queryset = queryset.filter(capability=capability)

        fields = [field.name for field in model._meta.concrete_fields]
        rows = [
            {name: self._plain(value) for name, value in row.items()}
            for row in queryset.order_by(ordering).values(*fields)[:MAX_ROWS]
        ]

        return {
            "view": view,
            "views": sorted(VIEWS),
            "model": label,
            "fields": fields,
            "rows": rows,
            "row_count": len(rows),
            "capabilities": self._capabilities(),
            "bundles": self._bundles(),
            "cache_note": (
                "Grant and resource caches invalidate independently, and cross-process "
                "changes propagate through a generation counter with a two-second local "
                "TTL. A change that has not appeared yet is usually that interval."
            ),
        }

    def _plain(self, value):
        """Render one column value as JSON-safe plain data."""

        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if isinstance(value, dict):
            return value
        return str(value)

    def _capabilities(self):
        """Return every registered capability the engine knows."""

        try:
            from evennia.authorization.capabilities import capability_registry

            return [
                {
                    "key": definition.key,
                    "delegable": definition.delegable,
                    "sensitive": definition.sensitive,
                    "description": definition.description,
                }
                for definition in capability_registry.definitions()
            ]
        except Exception:  # noqa: BLE001 - a listing must not break the page
            return []

    def _bundles(self):
        """Return the registered bundles and what each expands to."""

        try:
            from evennia.authorization.capabilities import capability_registry

            return sorted(
                {
                    name: sorted(capability_registry.expand_bundle(name))
                    for name in getattr(capability_registry, "_bundles", {})
                }.items()
            )
        except Exception:  # noqa: BLE001
            return []

    @io_action
    def probe(self, ctx, principal_id=None, capability=None, resource_id=None):
        """Ask the live runtime whether one principal holds one capability.

        The decision already explains itself; nothing surfaced the explanation.
        This is the difference between reading grant rows and simulating the
        resolver in your head, and asking the resolver.

        Runs on the IO owner because it consults live authority state rather
        than the stored rows.

        Args:
            ctx: IO context.
            principal_id: Account to ask about.
            capability: Capability to test.
            resource_id: Optional object the capability is tested against.

        Returns:
            dict: The verdict, with whatever the runtime said about it.

        Raises:
            LookupError: The principal or capability is unknown.
        """

        from evennia.accounts.models import AccountDB
        from evennia.authorization.capabilities import InvalidCapability, capability_registry
        from evennia.authorization.service import has_capability
        from evennia.authorization.storage import load_grants, principal_refs

        principal = AccountDB.objects.filter(pk=principal_id).first()
        if principal is None:
            raise LookupError(f"no account with id {principal_id!r}")
        try:
            definition = capability_registry.require(str(capability or ""))
        except InvalidCapability as err:
            raise LookupError(str(err)) from err

        resource = principal
        if resource_id:
            from evennia.objects.models import ObjectDB

            resource = ObjectDB.objects.filter(pk=resource_id).first() or principal

        verdict = has_capability(principal, definition.key, resource=resource)
        snapshot = load_grants(principal)
        held = self._held(snapshot, definition.key)

        result = {
            "principal": {"id": principal.pk, "name": principal.username},
            "capability": definition.key,
            "sensitive": definition.sensitive,
            "delegable": definition.delegable,
            "resource": getattr(resource, "pk", None),
            "allowed": bool(verdict),
            "refs": list(principal_refs(principal)),
            "matching_grants": held,
            "explanation": self._explain(bool(verdict), held, definition),
        }
        audit.record(
            panel=self.key,
            operation="probe",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"accounts.accountdb#{principal.pk}",
            after={"capability": definition.key, "allowed": bool(verdict)},
        )
        return result

    def _held(self, snapshot, capability):
        """Return the scopes in a snapshot that carry one capability.

        ``GrantSnapshot.by_capability`` maps a capability to a frozenset of
        ``GrantScope`` records. Read their real fields rather than treating
        them as tuples: an explanation reporting "no grant found" while a grant
        plainly exists is worse than offering no explanation at all, and that is
        exactly what a silent shape mismatch here produces.
        """

        rows = []
        try:
            for scope in snapshot.by_capability.get(capability, ()) or ():
                rows.append(
                    {
                        "origin": getattr(scope, "origin", ""),
                        "scope_kind": getattr(scope, "kind", ""),
                        "scope_key": getattr(scope, "key", ""),
                        "constraints": list(getattr(scope, "constraints", ()) or ()),
                    }
                )
        except Exception:  # noqa: BLE001 - the snapshot shape belongs to R3
            return []
        return sorted(rows, key=lambda row: (row["scope_kind"], row["scope_key"]))

    def _explain(self, allowed, held, definition):
        """State the verdict in a sentence an operator can act on."""

        if allowed and held:
            return f"Allowed: {len(held)} grant(s) carry {definition.key}."
        if allowed:
            return (
                f"Allowed, but no direct grant for {definition.key} was found on this "
                "principal. A bundle, a delegation, or a break-glass grant is carrying it."
            )
        if held:
            return (
                f"Denied even though {len(held)} grant(s) name {definition.key}. The "
                "principal is suspended, quelled, or the grant's scope does not cover "
                "this resource."
            )
        return f"Denied: no grant on this principal carries {definition.key}."

    @io_action
    def break_glass(self, ctx, principal_id=None, reason="", ttl_seconds=900):
        """Issue a temporary bypass grant, permanently audited.

        Matches the ``auth_recover_grant`` contract: a reason and a lifetime
        are both required, and every use is durably recorded. It bypasses
        authorization policy, never action or domain invariants.

        Raises:
            LookupError: The principal is unknown.
            ValueError: No reason was given, or the lifetime is out of range.
        """

        from datetime import timedelta

        from django.utils import timezone

        from evennia.accounts.models import AccountDB
        from evennia.authorization.storage import grant_capability, principal_refs

        principal = AccountDB.objects.filter(pk=principal_id).first()
        if principal is None:
            raise LookupError(f"no account with id {principal_id!r}")
        if not str(reason or "").strip():
            raise ValueError("a break-glass grant requires a reason")
        ttl = int(ttl_seconds or 0)
        if not 60 <= ttl <= 86400:
            raise ValueError("a break-glass grant lasts between 60 and 86400 seconds")

        expires_at = timezone.now() + timedelta(seconds=ttl)
        grant_capability(
            principal_refs(principal)[0],
            "engine.authorization.break_glass",
            scope_kind="world",
            scope_key="*",
            expires_at=expires_at,
            provenance="console",
            actor_ref=str(ctx.actor_id),
            reason=str(reason)[:255],
        )
        audit.record(
            panel=self.key,
            operation="break_glass_issue",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"accounts.accountdb#{principal.pk}",
            after={"expires_at": _stamp(expires_at), "ttl_seconds": ttl},
            message=str(reason)[:500],
            retention="permanent",
        )
        return {
            "principal": principal.pk,
            "expires_at": _stamp(expires_at),
            "ttl_seconds": ttl,
            "note": "Bypasses authorization policy only. Domain invariants still hold.",
        }
