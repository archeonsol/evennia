# Capability Authorization

The engine has one authorization authority: immutable typed policies evaluated
against explicit namespaced capability grants.

```text
principal grants + resource scopes + operation policy + runtime context
    -> AuthorizationDecision
```

## Capabilities and grants

Games register capabilities such as `mygame.world.author` in a capability module.
Capabilities are independent units, not ranks. Bundles are administrative
shortcuts that expand to grants; they do not exist during evaluation.

```python
from evennia.authorization.capabilities import CapabilityDefinition

def register_capabilities(registry):
    registry.register(CapabilityDefinition("mygame.world.author"))
    registry.register_bundle("world_team", ("mygame.world.author",))
```

Grants may apply to the world, a resource, or an authored label. They are durable,
audited, expirable, revocable, and generation-cached. Delegation cannot widen a
parent grant or outlive it.

```text
@grant *Ari = bundle:world_team
@grant *Ari = mygame.world.author/label/project:market
@grant/revoke *Ari = <grant-id>
```

## Policies

Resources expose stable operations through `resource.access(principal,
"operation")`. Code and builders author typed policy nodes; no expression string
is parsed at runtime.

```python
from evennia.authorization.policy import Always, RequiresCapability

class Workshop(DefaultRoom):
    authorization_policies = {
        **DefaultRoom.authorization_policies,
        "view": Always(),
        "edit": RequiresCapability("mygame.world.author"),
    }
```

Sparse per-resource overrides use `resource.policies` or `@policy`:

```text
@policy/set workshop/edit = mygame.world.author
@policy/set public gate/traverse = public
@policy/set sealed gate/traverse = disabled
@policy/del workshop/edit
```

Missing operations fail closed. `default=True` cannot manufacture authority.

## Scopes

`@scope/set resource = kind:key` assigns searchable labels. A label-scoped grant
matches any resource carrying that label. Creating an object does not grant
authority and there is no implicit creator ownership.

## Commands and actions

Commands declare `authorization = "public"`, `"disabled"`, or a capability.
Typed actions use `HasCapability("namespace.domain.verb")`. The command parser,
action engine, REST API, RenderNode affordances, channels, help, scripts, and
objects all consult the same grant/policy evaluator.

## Operations

- `@policy` / `@scope`: builder policy and scope authoring.
- `@grant`: grant/revoke explicit authority.
- `auth_finalize_capabilities`: one-time permission-tag materialization and lock
  storage clearing while the server is stopped.
- `auth_audit_capabilities`: deployment gate; refuses remaining executable legacy
  storage.
- `auth_import_lockstrings`: offline-only, bounded import for simple historical
  rows. Unsupported/custom semantics require explicit policy authoring.
- `auth_recover`: short-lived audited break-glass recovery.

`@quell` suppresses account grants for testing. It does not activate a lower tier.
The only emergency bypass is a short-lived, audited break-glass grant issued
offline; Django's `is_superuser` flag is not game authority.

## R3F cutover contract

The capability-only cutover is deliberately one-way:

1. **R3F.1 — runtime authority:** `access()` and action predicates evaluate only
   typed policies and registered grants.
2. **R3F.2 — authoring:** commands, prototypes, help, channels, REST resources,
   Attributes, and game actions author semantic capabilities instead of tiers.
3. **R3F.3 — administration:** `@policy`, `@scope`, and `@grant` replace dynamic
   lock and permission editing.
4. **R3F.4 — data finalization:** while stopped, run
   `auth_finalize_capabilities --apply --remove-authority-tags
   --clear-lock-storage` to materialize configured legacy authority.
5. **R3F.5 — deployment gate:** run `auth_audit_capabilities`; deployment fails
   while any executable lock storage or imported legacy shadow remains.
6. **R3F.6 — removal:** the lock runtime, permission hierarchy, command-access
   cache, rank aliases, and online compatibility evaluator are absent. The only
   importer is the explicitly invoked offline `auth_import_lockstrings` command.

The database columns that formerly stored locks remain inert for a compatibility
release so existing databases require no schema migration. They are cleared by
the finalizer and ignored by every runtime decision.
