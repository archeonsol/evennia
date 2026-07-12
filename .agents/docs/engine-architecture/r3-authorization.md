# R3 capability authorization

Status: **R3A-R3E shipped; R3F removal deliberately deferred.**

R3 replaces lockstring authoring and tier authority with a generic capability
runtime. During this pass `LockHandler` remains the rollback oracle. No creator or
owner relationship is part of engine authorization.

## Contract

```text
principal grants (slow-changing cache)
  + resource policy/scope labels (resource-generation cache)
  + ActionContext (one-dispatch memo)
  -> AuthorizationDecision
```

- Capabilities are validated namespaced identifiers. Bundles expand to explicit
  capabilities and never imply ranks.
- Grants are positive-only, scoped, expiring, provenance-bearing, and optionally
  delegated within a bounded subset. Suspension is separate principal state.
- Resource policies are immutable `auth.policy.v1` trees. JSON is storage only;
  builders and code use typed nodes and registered providers.
- Resources are generic references with extensible adapters. Authored labels are
  materialized; evaluation never walks location/topology graphs.
- `created_by` is audit provenance only. Games may implement property ownership as
  domain policy, but object creation grants no authority.
- Decisions explain their result, feed typed actions, and expose only allowed
  affordances to RenderNodes/clients.

## Cache and lifecycle rules

Principal grant/suspension caches and resource scope/policy caches are separate.
Movement invalidates only the moved resource. Permission-tag changes invalidate
the temporary legacy grant bridge. Expiring grants and suspensions carry their
own cache deadline, so time alone invalidates them.
Cross-process mutations publish generation counters through the shared Django
cache; active processes poll touched keys under a two-second local TTL.

Grant mutation serializes on a per-principal state row. PostgreSQL row locks are
defense in depth; SQLite relies on the single-threaded engine mutation contract.
Revocation cascades through bounded delegation descendants.

## Recovery

There is no immortal genesis account. Deployment operators may run:

```text
evennia auth_recover_grant <account-id> --reason "..." --ttl 900
```

The resulting `engine.authorization.break_glass` grant is temporary and every
use is durably audited. It bypasses authorization policies, not action/domain
invariants.

## Migration and freeze

`auth_migrate_locks` compiles known lock semantics into policies and synthesized
resource grants. Boolean grouping is preserved. Core contextual functions become
registered structured providers; game functions require an explicit compiler.
Unknown semantics fail the resource rather than silently weakening it.

`auth_diff_locks` replays a bounded principal/resource/operation matrix offline.
After parity, adding a kind to `AUTHORIZATION_FROZEN_RESOURCE_KINDS` makes
`locks.add/remove/clear` write only structured policy state. The old field is
retained unchanged as rollback evidence. Runtime authority is selected per kind
with `AUTHORIZATION_RESOURCE_POLICIES` (`legacy`, `diagnostic`, `live`).

R3F is the next destructive pass: promote validated kinds, remove hierarchy and
the compatibility permission bridge, quarantine import support, then remove
`LockHandler`, the parser, and `@lock` from core.
