# W1: engine console (Django-admin successor)

Status: built (branch `w1-console-phase1`, off `6.0.0+underspire.209`)

---

## Build status

Audited against this document on 2026-08-20 by reading the shipped code, then
rebuilt against that audit the same day. **Twenty-three panels are registered and
under test, and every cross-cutting promise in this document is implemented.**

Re-audited the same day against P5 specifically, after the TLS and negotiation
signals were wired in. That pass is the one worth reading: the first audit
checked that each panel existed and answered, and **three of the four defects
below survived it**, because a panel can exist, answer, and still be wrong.

### What the P5 re-audit found

**The flag dossier threw before it rendered.** `duration` and `reach` were
declared inside `editor()`, used only inside `flagDossier()`, and resolved in
neither — a `ReferenceError` on every click. The single path this document calls
"the one path that creates sanctions" did not open at all. Nothing caught it
because the panel's tests are service-side and the renderer had no test that ran
it. A scope sweep over every top-level function in `console.js` found no second
instance.

**An unrevealed signature reached the browser.** `tls_sig` and `telnet_sig` are
opaque identifiers with no sanction subject, and being unbannable was quietly
treated as a reason not to mask them. This document already answers that: an
unrevealed value must never reach the browser or devtools recovers it and the
audit trail lies. What a value can be *used for* does not decide whether it is
shown; that it identifies somebody does.

**Purged and never-recorded addresses read identically.** Both arrive as an
empty string. The retention promise in P5 — show retention state per row — was
not implemented, so a row whose address the ninety-day sweep cleared looked like
a row that never had one. The discriminator is `ip_hash`, not the row's age:
retention windows are settings that change, and a row purged under an old window
would be described wrongly by any calculation from today's.

**Alt correlation was never built.** P5 commits to it explicitly and the
migration step says anything the old surface does that the new one does not is a
bug. The account dossier is now `ModerationPanel.account()`. It costs two
queries per column rather than the game surface's one per *value*, which on an
account with a long history was eighty round trips to render one page.

One further defect, found while testing that dossier: the model's default
ordering joins a `DISTINCT`, so a limited shared-with lookup was distinct per
*session* rather than per pair, and thirty sessions from one account filled the
limit and hid everybody else on the key. `.order_by()` before `.distinct()`.

### Identity signals

`telnet_sig`, `tls_sig`, and `http_order_fp` are captured, indexed, masked,
revealable through the audited action, and correlated in the account dossier.
`tls_sig` is a correlation trigger; `http_order_fp` is deliberately not one, and
`evennia/moderation/README.md` carries the reasoning. The Moderation panel
reports per-signal coverage, because a signal that was configured and is
silently absent leaves the queue looking calm for the wrong reason, and no other
readout in the console would ever say so.

### Panels

Records, Attributes, Moderation, Authorization, Runtime, Logs, Errors, Objects,
Actions, Hooks, Prototypes, Jobs, Event bus, Live sessions, Server control,
REPL, SQL, Database, Saved views, Migrations, Settings, Audit, Health.

P15 Database was numbered here and never built. P20 Jobs and P21 Event bus were
committed in D6 and never numbered, so the commitment was unreadable from either
section alone and nobody built them. P22 Audit did not exist in any form: the
console wrote a full trail from phase 1 and could not show one row of it.

### Cross-cutting layer

| Promise | State |
| --- | --- |
| Degraded mode | Built, tested, enforced by a registration guard |
| English only | Held |
| Everything deep-linkable | Every panel's view state is in the address bar |
| Command palette, keyboard-first | Ctrl-K palette, matching name and description |
| Undo and state-as-of | Both built on the audit table; no new store |
| Saved views | `ConsoleSavedView`, shared and pinnable |
| Export (CSV/JSON, audited) | Built; recorded as a disclosure event |
| Bulk operations with dry run | Cascade counted per row before the operator commits |
| Multi-operator presence | Built in the cache; no database writes |

### Rules the rebuild had to hold

**Nothing may cost the running server.** Every listing is bounded and keyset
paged. No panel counts a table that grows per event. Presence is a cache
entry, not a heartbeat row: a test asserts its query count is zero. The
`db_attrs` size distribution is sampled rather than aggregated, because
`length(db_attrs::text)` across a table is the sequential scan the Database
panel exists to discourage.

**The write-behind cache has to be respected, not ignored.** Attribute writes
are drained on a cadence, so any SQL read of `db_attrs` answers from before the
write. Point queries run the read-your-writes barrier that
`objects/manager.py` already documents; the catalogue does not, and says so
instead, because a process-wide flush does not belong on a page an operator
leaves open.

**Operator-facing text is Simplified Technical English.** One meaning per word,
one instruction per sentence, active voice, the same word for the same thing.

### Undo, deliberately narrow

Records records an inverse for a change that succeeded, and for nothing else.
Reversing a create means deleting, and delete carries a cascade the service
preflights for a reason; attaching one to a control an operator reaches for
after a mistake is the wrong shape. A write that faulted did not necessarily
leave the row in the state the row records. Where undo is unavailable the panel
says which of the two applies, because an absent control explains nothing.

Undo never revises the row it reverses. It writes a new row pointing back at
it, so the trail holds the mistake and the correction as two facts.

### Defects the rebuild found

Five of these were the same bug: **guessing a registry's shape fails silently,
and the panel renders happily while telling the operator something untrue.**
The scheduler read reached for `_SYSTEMS`, `REGISTRY`, and `_REGISTRY`, none of
which is the name, and reported "the scheduler does not expose a readable
registry" about a scheduler that exposes one. `RuntimePanel` returned `systems`
and `tasks` and the renderer dropped both. `PanelActionView` refused every
action during an outage, including worker-side ones that never touch the IO
owner. The JSONB row-state caches survive a test rollback exactly as the
idmapper does. And the export read `columns` as records when it is a list of
names.

Every one is locked in by a test.

The implementation plan for the console half of **W1** (see
[`engine-architecture/committed.md`](../docs/engine-architecture/committed.md)).
W1 covers "web / client / protocol modernization" and names three surfaces: the
webclient protocol, the REST views, and the input/output handler stack. The
webclient protocol half is designed separately in
[`webclient-protocol.md`](../docs/engine-architecture/webclient-protocol.md) and
[`webclient-protocol-runtime.md`](../docs/engine-architecture/webclient-protocol-runtime.md).
This document covers the remaining half: the staff-facing web surface, currently
Django admin.

W1 was sequenced last because it depends on R1 (render output is what web
consumers receive) and H1 (the registry covers these surfaces). **Both shipped.**
W1 is unblocked.

Unlike most prompts in this folder, this one commits to a design rather than
inviting one cold. That is deliberate: the target is well-constrained by shipped
subsystems, and the analysis below is the review artifact. The executing agent
should still confirm the decisions in the last section before writing code; each
is a recommendation with its reasoning attached, not a settled constraint.

---

## The problem, stated as evidence

### Django admin covers eight models

Every `@admin.register` in the tree, and there are no others:

| Model | Module |
| --- | --- |
| `AccountDB` | `web/admin/accounts.py` |
| `Msg` | `web/admin/comms.py` |
| `ChannelDB` | `web/admin/comms.py` |
| `HelpEntry` | `web/admin/help.py` |
| `ObjectDB` | `web/admin/objects.py` |
| `ScriptDB` | `web/admin/scripts.py` |
| `ServerConfig` | `web/admin/server.py` |
| `Tag` | `web/admin/tags.py` |

### `server/models.py` alone defines twelve models

One is registered. Eleven have no web surface of any kind:

`GameEvent`, `AuthorizationGrant`, `AuthorizationScopeLabel`,
`AuthorizationPolicyOverride`, `AuthorizationPrincipalState`,
`AuthorizationAuditEvent`, `EngineJob`, `SessionRecord`, `Sanction`,
`SanctionHit`, `ModerationFlag`.

That set is the entire capability runtime (R3A-R3E, shipped), the durable job
queue, and the entire moderation substrate (`+underspire.209`). The moderation
package is ~2,440 lines of implementation plus ~1,820 lines of tests. It has a
tamper-evident sanction hash chain, an eight-way subject taxonomy, a five-level
severity ladder, address-provenance tracking, and a flag queue with a four-state
review workflow (`open` -> `acknowledged` -> `dismissed` | `actioned`) carrying
`resolved_by_id`, `resolution_note`, and a FK to the resulting `Sanction`.

### The engine's own substrate is administered from the game repo

The moderation substrate does have a web UI. It is downstream, in Underspire:

| Piece | Where | Lines |
| --- | --- | --- |
| Bounded IO-owned read/mutate services | `web/website/moderation_services.py` | 813 |
| Views (queue, dossier, flag, resolve, sanction, revoke) | `web/website/views/moderation.py` | 327 |
| Templates | `web/templates/website/moderation_*.html`, `partials/_moderation_sanction_form.html` | ~155 |
| Staff index row | `web/templates/website/staff_index.html` | — |

This is not an argument against the console. It is the strongest argument *for*
it, and it is the textbook "pull in" signal from
[`FUTURE-IDEAS.md`](../../FUTURE-IDEAS.md): *"Game has accidentally built
infrastructure while solving a game problem"* and *"A seam exists in the engine
but the implementation lives downstream."* The engine owns the models, the
capture path, the detection, the enforcement, the hash chain, and the retention
policy. The game owns the only way to look at any of it.

Two consequences shape this plan:

1. **The moderation panel is a promotion, not greenfield.** `moderation_services.py`
   is a working, bounded, IO-owned reference implementation with row caps and a
   thought-through capability split. The engine panel is that code moved up and
   re-namespaced, which makes it one of the lowest-risk panels rather than one
   of the highest.
2. **The game-side surface is removed in the same arc.** Confirmed as intent:
   moderation becomes a full engine thing. See "Moderation migration" below for
   what moves, what stays, and the ordering.

The game-side service splits its authority in two, and the reasoning is worth
carrying forward even though the mechanism is not:

- `underspire.moderation.act` — see the queue, read a dossier, issue and lift
  sanctions.
- `underspire.moderation.address` — see raw addresses, device tokens, and
  hashes. Held by few people.

A staffer with only the first still sees which network a session came from and
can still ban it, without ever reading the address. That is most moderation
work, and an appeal never requires staff to have seen the address.

The console keeps that property through auditing rather than through a second
capability — see the moderation panel and the capability decision under
"Transport and authentication". Moderation is also the one place where the
console's superuser framing genuinely strains, since moderators are the one
audience for this tool who are not necessarily server administrators. That is
what the single extra capability exists to solve, and nothing else.

### Three of the eight registered admins administer retired concepts

- **`web/admin/attributes.py` is a nine-line tombstone.** Its entire body is a
  docstring: *"The Attribute Django model was removed in Phase 2 of the JSONB
  migration."* Attribute browsing was a primary reason the admin existed. It was
  removed, not replaced. There is today no way to inspect an object's attributes
  from the web at all.
- **`web/admin/scripts.py` presents `ScriptDB` as a timer.** Per
  [`decisions.md`](../docs/engine-architecture/decisions.md) (AS2 tranche B,
  `.93`), **Script is storage-only**; the timer columns and `Script.interval`
  machinery are gone and recurring work is the System Scheduler. The scheduler
  has no web surface; its only view is the in-game `@systems` action.
- **`web/admin/objects.py` edits `db_lock_storage`.** Locks are the R3 *rollback
  oracle*. Capabilities are the live authority. The admin edits the deprecated
  field and cannot see the real one.

### The correctness half already exists

`web/admin/io.py` (1,123 lines) and `web/admin/mixins.py` (364 lines) are not
admin cosmetics. They are a bounded IO-owner mutation service:
`AdminMutationRequest` / `AdminDeleteRequest` dataclasses, recursive value
freezing with depth/item/byte budgets, FK resolution and validation, tag deltas,
`NestedObjects` cascade preview, superuser-authority guards, compensation on
partial account creation, and `LogEntry` audit. It carries 985 lines of tests
(`test_changelist_io.py`, `test_mutation_io.py`, `test_objects_io.py`).

That is an admin kernel with one hardcoded frontend. The frontend is the part
that is wrong.

---

## The bar: supersede, do not match

Parity with Django admin is not the target and would not be worth building.
Django admin is a generic CRUD scaffold for a schema-first Django app; this
engine is neither generic nor schema-first. The console must do **everything
Django admin does, better, plus the entire class of things ModelAdmin cannot
express.**

Every Django admin capability, and what supersedes it:

| Django admin | Console |
| --- | --- |
| `list_display` changelist | Virtualized table, any model, no registration step |
| `list_filter`, `search_fields`, `date_hierarchy` | Cross-field query builder, JSONB containment, saved and shareable named views |
| `list_editable` | Inline edit plus bulk edit with a dry-run diff before commit |
| Admin actions (bulk) | Bulk domain operations, each showing its cascade preview and per-row outcome |
| `raw_id_fields` / autocomplete widgets | Typeclass-aware object picker with live search across key, alias, and tag |
| `fieldsets`, `readonly_fields` | Schema-driven forms from `spec.py`, with read-only fields derived from the write spec |
| `inlines` | Related-object editing plus a navigable relationship graph |
| `get_deleted_objects` cascade confirm | Same `NestedObjects` preview, now on every mutation, not only delete |
| Object history (`LogEntry`) | Full audit timeline with before/after diffs, undo where derivable, and state-as-of-timestamp |
| Per-model add/change/delete permissions | Capability per panel, per operation, and per field, re-checked live |
| `AdminSite.each_context`, app ordering | Registry-driven nav, ordered by the registry, filtered by deployment settings |
| — | Attribute inspection (Django admin lost this entirely at JSONB) |
| — | Live push: metrics, logs, sessions, queue arrivals |
| — | REPL, SQL console, query profiler |
| — | Server control: reload, reset, shutdown, portal status |
| — | Live sessions: watch, kick, disconnect |
| — | Migration state, effective settings, database health |
| — | Error inbox with traceback grouping |
| — | Multi-operator presence and edit collision warnings |
| — | Degraded read-only mode when the game server is down |
| — | Capability decision prober with explanations |
| — | Moderation queue, sanctions, alt correlation |
| — | CSV / JSON export on any view |

The right mental model is not "a nicer Django admin." It is the operations
console for a running game server, of which database CRUD is one panel.

---

## What this is not

Read these before proposing scope changes.

**Not event sourcing.** [`horizon.md`](../docs/engine-architecture/horizon.md)
warns against it *with evidence*, and the warning stands even though its example
has moved on. "Replay" in this document means replaying `render.v1` nodes the
engine already produces, nothing more. No new event store, no new capture path.

> **The "dead/unconsumed" label on `evennia/jobs/` and `evennia/events/bus.py`
> is stale.** Both have live game-side consumers; see D6. `horizon.md` and
> [`ALPHA-jobs-eventbus-boundary.md`](ALPHA-jobs-eventbus-boundary.md) both
> need updating, and nothing should delete either subsystem on the strength of
> those labels.

**Not the plugin system.** [`FUTURE-IDEAS.md`](../../FUTURE-IDEAS.md) defers a
plugin mechanism for lack of a second consumer. The panel registry here is a
settings-listed module list with a `register_panels()` callable — deliberately
the same shape as `SYSTEM_MODULES`, same failure mode on bad import, nothing new
to learn and no new abstraction to maintain.

**Not a Django admin theme.** `django-unfold` and friends restyle ModelAdmin.
ModelAdmin is the layer that cannot express any of the above.

**Not a removal of Django admin.** See "Coexistence" below.

**Not a game-side staff tool.** Underspire's NPC builder, GM dashboard, faction
tools, bug queue, pending-request queue, character notes, and RRMS stay
downstream. RRMS in particular is an IC player surface and has nothing to do
with this. Moderation is the one exception, and it moves the other way: see
"Moderation migration".

**Not the mapper port.** An earlier draft shipped Underspire's mapper as the
built-in domain panel proving the registry API. **Deferred by decision.** The
console must first be a complete engine-facing development and operations
surface; a game-shaped panel riding along dilutes that and adds a port that
touches game authorization. The registry API still gets built and still gets
exercised — by the twenty engine panels below, which is a harder test than one
imported tool. Revisit after phase 4.

---

## Hard constraints

Each is a shipped decision, and each is a trap the obvious implementation falls
into. Any design that violates one is wrong regardless of how good it looks.

**C1. No `.only()` / `.defer()` on idmapper models, ever.** Per
[`decisions.md`](../docs/engine-architecture/decisions.md): partial loads cannot
build an uncached `SharedMemoryModel`, and idmapper can never refresh a deferred
field. List views use `values()` / `values_list()`. This forbids the single most
natural implementation of a fast changelist.

**C2. Worker ORM reads construct complete detached rows; mutation crosses the IO
bridge.** Identity, hooks, handlers, and persistence are IO-owner scoped. The web
worker never holds a live typeclass instance.

**C3. Nothing in a panel touches `.db`, `.ndb`, or any handler directly.**
Attribute reads off the IO thread crash on production PostgreSQL and are
invisible under SQLite in development. This is the failure mode that produced
admin 500s in the first place. The panel API must make it *unrepresentable*, not
merely discouraged — see K4 below.

**C4. Outcomes are a five-way taxonomy, not success-or-error.** Per
[`Web-Mutation-Bridge.md`](../../docs/source/Components/Web-Mutation-Bridge.md)
and [`Web-IO-Boundary.md`](../../docs/source/Components/Web-IO-Boundary.md):

| Outcome | Meaning | Retry |
| --- | --- | --- |
| Conflict | Deterministic rejection before any write | Caller may fix and resubmit |
| Success | Domain postcondition verified | n/a |
| Partial | A write or recorded PK exists, later fault | **No** |
| Recovery-required | As partial, needs operator action | **No** |
| Indeterminate | Callback started, outlived the worker wait | **No** |

Only a pre-start `IOThreadCallTimeout` is retryable. The console UI must render
all five distinctly, with the affected record linked for the three
non-retryable ones. Collapsing this into a red toast is the single easiest way
to make the console worse than Django admin at the thing Django admin does
badly.

**C4a. Audit is written after the domain result, in a separate transaction.**
Audit failure cannot roll back a completed mutation or turn it into a retryable
response; the operator receives an explicit audit warning instead. Delete logs
only IDs proven deleted, from detached pre-delete snapshots.

**C4b. `release_worker_db_connections()` after validation, immediately before
dispatch.** It rejects an active worker transaction and closes ordinary worker
connections. No worker transaction may be presented as atomic with the owner
connection.

**C5. The DTO rule.** An IO service accepts scalar IDs, bounded scalar input, and
immutable tokens; performs lookup, authorization, reads/writes, and serialization
in *one* callback; and returns primitives, safe scalars, collections of those, or
frozen DTOs. It never returns typeclasses, models, handlers, querysets, relation
managers, lazy wrappers, requests, or forms. Frontend code consumes DTO fields
and never dereferences `.db` or a live method. `freeze_plain` and `_CodecBudget`
in `web/admin/io.py` enforce the encoding half.

**C5a. A worker-side check cannot authorize a mutation.** Authorization and
mutable topology checks are repeated inside the same callback as the write. A
worker-side pre-check is for fast rejection only. This matters more, not less,
under the one-capability model: the single check is the only check.

**C5b. Detached worker instances cannot persist.** A worker may materialize a
complete `SharedMemoryModel` row for rendering, including `select_related` rows,
but gets a detached hookless instance outside the canonical cache. `save()`,
`delete()`, `QuerySet.delete()`, `bulk_create()`, and `bulk_update()` all fail
before persistence. This is the engine enforcing C3 for us; the panel API layers
on top of it rather than duplicating it.

**C6. Fail closed.** Per [`core-beliefs.md`](../docs/core-beliefs.md).
`admin_spec()` raises on any model without an explicit `_SPECS` entry, and the
bridge documentation states the position outright: *"It is not a generic
model-save API."* Its extension rule — *"Custom admins do not inherit this
behavior automatically. Register a bounded adapter or define a dedicated IO
service"* — is the rule K2 below is designed around, not a rule K2 relaxes.

**C7. `Tag` is a known exception and stays read-only.** Per the bridge doc's
extension rule, standalone Tag editing is outside the bridge because one shared
Tag row may be cached by many owners; it needs a global invalidation design or a
read-only policy. The console adopts **read-only** and does not invent the
invalidation design as a side quest. Tags remain editable through their owning
object's handler, which is where the existing inline path already works.

**C8. Backend-agnostic storage primitives.** Per the W1 preservation constraints:
don't hard-code `ObjectDB` as a parent in new substrate; key off the actor, not
raw `AccountDB` / `ObjectDB`; keep new primitives able to pick a backend.

**C9. Engine ships built frontend assets.** A game must never need `npm` to run
the console. Build artifacts are committed, same as the existing webclient's.

---

## Architecture

```
evennia/console/
    __init__.py         # flat-API exports: register, Panel, capability names
    spec.py             # runtime introspection surface
    services.py         # IO-owner services (promoted from web/admin/io.py)
    registry.py         # panel registration
    audit.py            # engine-owned audit log (LogEntry successor)
    feed.py             # server-push channel
    panels/             # built-in panels, one module each
        records.py
        objects.py
        attributes.py
        authorization.py
        moderation.py
        runtime.py
        logs.py
        repl.py
        sql.py
        actions.py
        hooks.py
        migrations.py
        settings.py
        prototypes.py
        server.py
        sessions.py
        errors.py
        database.py
evennia/web/console/
    urls.py
    views.py            # DRF viewsets + the websocket entry
    static/console/     # committed build output
    templates/console/index.html
```

Mounted at `/console/`, guarded by `settings.CONSOLE_ENABLED` (default on) and
`settings.CONSOLE_PANEL_MODULES`.

### K1. `spec.py` — the introspection surface

Django admin generates its UI from `Model._meta`. That reflects the storage
layer, which in this engine is no longer where the interesting structure lives.
`spec.py` reflects the runtime:

- **Typeclasses.** The registry, as a tree, with `db_typeclass_path` as the
  indexed key. Must handle the dual-spelling hazard documented in
  `performance.md` §10 (`typeclasses.characters.Character` vs
  `typeclasses.characters.base.Character`) so no UI filter silently selects
  nothing.
- **Models.** Every installed model, with a `plain` / `idmapper` classification
  that drives C1 and K2.
- **Hooks.** H1's `@hook` metadata: event, phase, actor, returns, discipline,
  plus which overrides a game has installed and which base hooks fail the
  startup lint.
- **Actions.** Registered actions, rules, predicates, and required capabilities.
- **Capabilities.** The validated namespaced identifier set and bundle
  expansions.
- **Systems.** Scheduler registrations with cadence, scope, and last-run.
- **Metrics.** The 22 Prometheus metric names in `server/prometheus_metrics.py`
  with their types and labels.

`spec.py` is read-only, pure, and cacheable per server generation. It is the
single most reusable piece of this work — the REST API, doc generator, and any
future tooling all want it.

### K2. `services.py` — the mutation kernel

Promoted from `web/admin/io.py`, minus every `django.contrib.admin` import. The
existing dataclasses and codec survive largely as-is; the change is who calls
them and what the audit target is.

**The read/write asymmetry is the key design decision here.**

- **Reads are generic.** `values()` / `values_list()` over any model is safe for
  both plain and idmapper models. Nothing needs an allowlist to be *listed*.
- **Writes to idmapper models require an explicit `_SPECS` entry**, exactly as
  today. `ObjectDB`, `AccountDB`, `ScriptDB`, `ChannelDB`, `Msg`, `HelpEntry`,
  `Tag`, `ServerConfig` keep hand-written specs. Adding a new typeclass model
  without a spec means it is readable and not writable, which is the correct
  fail-closed default.
- **Writes to plain models may use an auto-derived spec**, opted into per model.
  The eleven unadministered `server/models.py` models are all plain
  `models.Model` subclasses — no idmapper, no handlers, no `at_post_load`, no
  partial-load hazard. Deriving field types from `_meta` for these is safe in a
  way it is not for typeclass models. The opt-in is a declaration on the panel,
  not a global default.

Four model classes get **no generic write path at all** and are mutated only
through their own domain services:

- `Tag` — per C7, and per the bridge doc's own extension rule.

- `Sanction` / `SanctionHit` — the hash chain must be written by
  `moderation/sanctions.py` or tamper-evidence is worthless.
- `AuthorizationGrant` and siblings — grants serialize on a per-principal state
  row and cascade revocation through bounded delegation descendants. Editing the
  table directly corrupts the cache-generation contract.
- `AuthorizationAuditEvent` — append-only by definition.

The console surfaces these as **read + domain actions**, never as editable rows.
Attempting a generic write must be refused by the kernel, not merely hidden by
the UI.

### K3. `audit.py`

`django.contrib.admin.models.LogEntry` dies with Django admin, and `mixins.py`
currently writes to it. Replace with an engine-owned audit model before anything
else depends on the old one.

Requirements: actor ref (not FK — must outlive the account, matching the
moderation substrate's existing convention), panel, operation, target ref,
frozen before/after payloads under `_CodecBudget`, the five-way outcome from C4,
correlation ID, timestamp.

Ordering follows C4a exactly, because the existing bridge already got this right
and diverging would be a regression: domain result first, audit second, in a
separate transaction, and an audit failure surfaces as a warning rather than
converting a completed mutation into a retryable error. Deletes log only IDs
proven deleted, from detached pre-delete snapshots.

**Append-only at the service layer**, with no console write path, for the reason
in Risks: under one capability the audit trail is the only internal control, and
an audit table a console operator can edit is not one. It appears in the Records
lens read-only, alongside `Sanction` and the authorization tables.

**Account flows are not reimplemented.** `evennia.web.utils.auth` already owns
credential verification, password rehash and change, reset tokens, usable and
unusable password modes, and `last_login`. Account creation goes through
`create_with_provenance()`, whose `AccountCreationOutcome` is IO-local, contains
live models, and **must never cross the bridge**. The console calls these
services; it does not grow its own.

**Inverse where derivable.** A field-value change on a plain model is trivially
invertible; a cascade delete is not. Record an `inverse` payload when the service
can build one and expose undo only for those. Do not fake it.

Migration: write to the new model and `LogEntry` simultaneously during the
coexistence window, then drop the `LogEntry` write when Django admin is retired.

### K4. `registry.py` — the panel API

Deliberately shaped like `SYSTEM_MODULES`:

```python
# settings.py
CONSOLE_PANEL_MODULES = ["world.console_panels"]

# world/console_panels.py
from evennia.console import Panel, register

def register_panels():
    register(FactionPanel)

class FactionPanel(Panel):
    key = "factions"
    label = "Factions"                       # plain technical English
    columns = (...)

    def rows(self, ctx):
        """Runs on a worker. Plain ORM only, returns plain data."""

    def detail(self, ctx, pk):
        """Runs on a worker."""

    @io_action
    def enlist(self, ctx, pk, payload):
        """Runs on the IO thread. May touch game objects."""
```

Note the absence of a `capability` field. Console access is the capability (see
"Transport and authentication"); panels do not carve it up. A game panel that
wants to be visible to a narrower audience than "whoever administers this
server" is describing a game staff tool, and belongs in the game's own web app
rather than here.

The two-decorator split is C3 made structural. A method not marked `@io_action`
executes on the worker and is handed a context object with no game-object access
— no `.db`, no handlers, no live instances, because they are not reachable from
what it is given. A method marked `@io_action` is dispatched through
`run_on_io_thread` by the framework, and its return value passes through
`freeze_plain` before it leaves. Panel authors cannot make the JSONB IO-thread
mistake because the affordance does not exist in the object they hold.

That is the actual engine feature. Not the console. The API where the mistake is
unrepresentable.

A module that fails to import or registers nothing is a loud startup error, same
as `SYSTEM_MODULES`.

### K5. `feed.py` — server push

The console is live, not a snapshot. Reuse the Azaban envelope shape from
[`webclient-protocol-runtime.md`](../docs/engine-architecture/webclient-protocol-runtime.md)
rather than inventing a second wire format: typed envelope, handshake ordering,
resume with a per-tab token, batching, inbound limits. Different endpoint,
different capability, same protocol discipline and ideally the same encoder.

Feed topics: metric samples, log lines, session lifecycle, flag-queue arrivals,
audit entries, job-queue depth. A moderation-only subscriber receives moderation
topics and nothing else; every topic is rate-limited independently. A console
with no subscribers must cost
approximately nothing — no polling loop, no unconditional writes, per §7.4 of
`performance.md`.

---

## Panels

Nineteen panels. Ordered by phase (see below), not by importance.

| # | Panel | Phase | Needs IO thread |
| --- | --- | --- | --- |
| P1 | Records | 1 | writes only |
| P2 | Objects | 4 | writes only |
| P3 | Attributes | 2 | yes |
| P4 | Authorization | 2 | yes |
| P5 | Moderation | 2 | yes |
| P6 | Runtime | 3 | yes |
| P7 | Logs | 3 | no |
| P8 | REPL | 3 | yes |
| P9 | SQL | 3 | no |
| P10 | Actions | 4 | yes |
| P11 | Hooks | 4 | no |
| P12 | Server control | 3 | yes |
| P13 | Live sessions | 3 | yes |
| P14 | Errors | 3 | no |
| P15 | Database | 2 | no |
| P16 | Migrations | 1 | no |
| P17 | Settings | 1 | no |
| P18 | Prototypes | 4 | no |
| P19 | Health | 1 | partial |

The "needs IO thread" column is what determines degraded-mode availability, so it
is a design constraint, not documentation.

### P1. Records

The generic table lens. Every installed model including game models. List with
`values()`, filter, sort, paginate, detail, edit, delete with cascade preview.

Strictly wider than `/admin/` is today: it covers the eleven unadministered
`server/models.py` models and every model any game ever adds, with no
registration step.

Write access follows K2's asymmetry. Read-only models display a banner naming
the domain action that mutates them instead, with a link to that panel.

**This is the raw database access, and it is the panel that must not be
compromised for elegance.** Being able to look at and edit the row is the
baseline capability that everything else is measured against.

### P2. Objects

Typeclass-tree navigation rather than a flat `ObjectDB` table, since the tree is
what people actually think in and `db_typeclass_path` is the indexed column that
makes it cheap.

- Tag reads via `prime_tag_caches` (one query), never bare
  `prefetch_related("db_tags")` — `performance.md` §3 measures the latter at 119
  queries for 30 rooms because the idmapper returns a shared instance the
  prefetch cache never attached to.
- `select_related("db_location")` for the location column.
- Contents, exits, and location as navigable links.
- Puppeting state from the `ControlBinding` focus stack, not the retired
  puid/puppet pointer pair.

### P3. Attributes

The capability vanilla admin lost entirely.

- **Document view.** The `db_attrs` JSONB document rendered as a tree, with the
  storage shape decoded rather than shown raw: `"~"` is the default category,
  named categories are lowercased names, `"_d"` holds the value dict.
- **Size column with a threshold warning.** Every flush serializes the whole
  document (`performance.md` §8), so a 50 KB biography inflates every `hp` write.
  Nothing today shows which objects are fat. Flag documents over ~4 KB and
  surface the largest keys.
- **Containment query builder** over the GIN index:

  ```python
  ObjectDB.objects.filter(db_attrs__contains={"~": {"_d": {"faction": "corp"}}})
  ```

  `performance.md` §3 explicitly blesses this shape for admin and analytics
  queries. It is currently something you type by hand into `@py`. The builder
  must label it as a full table scan in the UI and refuse to offer it as a saved
  hot-path query.

  Note this is PostgreSQL-only; SQLite development installs get the builder
  disabled with an explicit reason, not a confusing empty result.
- **Edits go through the IO thread**, one `@io_action`, with a before/after diff
  in the audit record.

### P4. Authorization

The R3 runtime, currently invisible.

- Grants, scope labels, policy overrides, principal state, audit events — read
  views with the right indexes driving the default sorts.
- **Decision prober.** Pick principal, resource, operation; get the
  `AuthorizationDecision` with its explanation. Per
  [`r3-authorization.md`](../docs/engine-architecture/r3-authorization.md),
  decisions already explain their result; nothing surfaces it. `auth_diff_locks`
  already replays a bounded principal/resource/operation matrix offline — this is
  that machinery with a UI and a single-cell input.
- **Policy tree viewer.** `auth.policy.v1` trees rendered as typed nodes.
  Reminder from the R3 doc: JSON is storage only; the viewer renders typed
  nodes, never raw JSON as the primary view.
- **Break-glass issuance**, matching the `auth_recover_grant` contract: requires
  a reason and a TTL, durably audited, visibly time-boxed in the UI with a
  countdown. No separate grant: reaching this panel already means holding
  console access, and a break-glass button is less power than the REPL two
  panels over.
- **Cache generation display**, since cross-process mutations publish generation
  counters through the shared Django cache with a two-second local TTL. When a
  grant edit does not appear to take effect, the operator should be able to see
  that it is the poll interval and not a bug.

### P5. Moderation

Not a gap — a **promotion**. ~2,440 lines of engine substrate whose only
interface lives in the game repo (see the evidence section). The engine panel is
`web/website/moderation_services.py` moved up, re-namespaced, and given a real
frontend; the downstream surface is then deleted.

Read that service before designing this panel. It already solves the parts that
are easy to get wrong: every query row-capped (`QUEUE_FLAGS = 40`,
`QUEUE_SANCTIONS = 30`, `QUEUE_SESSIONS = 25`) because the tables grow by one row
per connection and a staff page must not be the thing that discovers how large
they got; every function inside one IO callback returning frozen scalars, because
capability checks resolve through the account typeclass even though the
substrate itself is plain ORM.

- **Flag queue.** The primary view: open and acknowledged flags, by severity and
  recency, deduped via `dedupe_key` with `seen_count`. Acknowledge, dismiss with
  a note, or escalate. The four states already exist on the model; the queue is
  the missing half.
- **Escalation flow.** Flag -> sanction, carrying evidence forward into
  `Sanction.evidence`. This is the one path that creates sanctions, matching the
  package's stated rule that nothing in the engine sanctions automatically.
  Level and subject type come from the model's own choice lists.
- **Sanction browser.** Active and historical, by subject type across all eight
  subjects, with revoke (reason required) and expiry. Indefinite sanctions
  (`expires_at` null) must be chosen deliberately in the UI, not be the default.
- **Hash-chain verification.** A visible chain-integrity check over `prev_hash`
  / `row_hash`. Tamper evidence that nobody can check is decoration.
- **Session records** with address provenance made legible. The model already
  exposes `address_is_trustworthy`; when `xff_present` is set and `xff_applied`
  is not, the recorded address is the reverse proxy's and *every address-based
  signal on that row is void*. That must be a loud row-level banner, not a
  column of two booleans, or staff will ban a proxy.
- **Alt correlation** over the existing indexes (`device_token`, `client_fp`,
  `cidr`, `ip_hash`, `csessid`) — exact matches only, matching the package's
  hard-signals-only stance. No scoring, no inference, no similarity metric. The
  README's justification is that any conclusion must be showable to the player it
  is used against; a UI that quietly adds a heuristic breaks that. **Built** as
  `ModerationPanel.account()`; `telnet_sig` and `tls_sig` join the column list,
  marked as keys that can be compared but never banned, since each identifies a
  piece of software rather than a person.
- **Retention.** `ip` is purged on schedule while `ip_hash` and `cidr` outlive
  it. Show retention state per row so staff understand why an older row has no
  address. **Built** as `address_state`: `held`, `purged`, or `absent`, keyed off
  `ip_hash` rather than the row's age.
- **Address reveal is audited, not gated.** The game-side service splits
  `act` from `address` as two capabilities. That split does not survive the
  console's two-capability decision, but the *value* behind it must, because the
  value was never really access control. It is appeal defensibility: an appeal
  never requires that staff have seen the address, so staff should be able to do
  the work without seeing it, and there should be a record when someone did.

  So: addresses, device tokens, and hashes are **masked by default in the
  response body**, and revealing one is an explicit per-record action that
  writes an audit event naming the operator, the record, and the reason. Nobody
  is prevented; everybody is recorded. That is strictly stronger than the
  capability split for the purpose the capability split served, and it costs one
  fewer capability.

  Masking happens in the service, not the template. An unrevealed address must
  never reach the browser, or devtools recovers it and the audit trail lies.

#### Moderation migration

Ordered, and each step leaves the tree working:

1. Engine panel ships behind `engine.console.access` and
   `engine.console.moderation` while the game-side views remain. Both read the
   same tables; there is no data migration.
2. Grants are issued from existing holders: `underspire.moderation.act` holders
   receive `engine.console.moderation`; anyone who also administers the server
   receives `engine.console.access` instead, which subsumes it.
   `underspire.moderation.address` holders receive nothing extra — reveal is an
   audited action now, available to any moderator. Flag that widening
   explicitly in the release notes; it is a deliberate trade of prevention for
   accountability, and staff should hear it from a changelog rather than
   discover it.
3. Staff verify the console panel against the old surface on live data for one
   release. Anything the old surface does that the new one does not is a bug to
   fix, not a feature to drop.
4. Game-side removal: `web/website/views/moderation.py`,
   `web/website/moderation_services.py`, `moderation_account.html`,
   `moderation_flag.html`, `moderation_queue.html`,
   `partials/_moderation_sanction_form.html`, the URL entries, and the
   `underspire.moderation.*` capability definitions.
5. The staff index keeps a moderation row, now a link into the console rather
   than a local page, with the open-flag count still rendered inline. Staff
   should not have to learn a new entry point; they should follow the same key
   and land somewhere better.

**What stays downstream:** the bug queue, pending requests, and character notes.
Those are game workflows over game models, they share nothing with the engine
substrate but a page layout, and the staff index continues to own them.
`views/device.py` also stays: it issues the device-token cookie to *players*, so
it is part of the game's public web surface, not the staff surface, even though
the token it mints is a moderation signal the console reads.

### P6. Runtime

- The 22 Prometheus metrics, live, over the feed. Attribute flush duration and
  dirty-pending gauge; the three cache hit/miss pairs; render delivery count and
  duration; authorization decisions and duration; runtime task roots, DB scope
  closes, and unmanaged-ORM-access counter; idmapper flush duration, batches,
  objects, row queries, failures.
- **A cache panel keyed to the invalidation contracts.** `core-beliefs.md`
  requires every cache to document what fills it, what invalidates it, and the
  stale-read bound, and notes "seven caches added, zero removed." Render hit rate
  next to each cache's contract. This is how the eighth cache gets refused, or
  the second one gets deleted.
- Scheduler table: cadence, scope, last-run, overlap-guard state — `@systems`
  as a page.
- Supervised task roots by `task_kind`, with the unmanaged-access counter
  prominent since it is a correctness alarm, not a performance number.
- Job queue: depth by status, dead-letter inspection, requeue, and per-`job_type`
  rates across the six live handlers in `world/engine_jobs.py`. Ungated: D6
  confirms the subsystem is in use.

### P7. Logs

- **Live tail** of `SERVER_LOG_FILE`, `PORTAL_LOG_FILE`, `HTTP_LOG_FILE`,
  `LOCKWARNING_LOG_FILE` over the feed. `tail_log_file` already exists in
  `evennia/utils/logger.py` and is used by `@channel` history and the launcher;
  the file-reading half is not new work. It is blocking I/O and therefore runs
  via `defer.in_thread`, never inline on the reactor.
- Level and regex filtering server-side, so a noisy `http_requests.log` does not
  flood the socket. Bounded backlog on subscribe.
- **Rotated backups browsable** per `LOG_ROTATED_PRUNE_NAMES`, within
  `LOG_ROTATED_RETENTION_DAYS` (14) and `LOG_ROTATED_MAX_BACKUPS` (30).
- Traceback frames link to source at the right line.
- **Correlation-ID join.** The differentiator. R1 nodes carry correlation IDs;
  bounded semantic timelines and read-only sinks already exist. From a log line,
  reach the render node, the viewer, and what that player actually saw. A bug
  report becomes a link instead of a paragraph of recollection.

  Scope discipline: this reads existing bounded timelines. It does not add
  retention, does not add an event store, and must degrade cleanly to "timeline
  expired" rather than growing one.

### P8. REPL

`actions/default/python_console.py` already implements `EvenniaPythonConsole`,
`run_code_snippet`, and `evennia_local_vars`, gated on
`engine.runtime.manage`. The web console gets the same console object over the
feed:

- Same locals (`self`, `me`, `here`, `evennia`, `ev`, `inherits_from`). Access
  re-checked live per submission rather than at session start, and gated by
  `CONSOLE_REPL_ENABLED` at the deployment level.
- Executes on the IO thread. Output captured, including the recursion guard the
  existing implementation already carries for redirected `print`.
- Persistent history per operator, multi-line editing, timing mode.
- **Every submission audited**, source text included. A REPL without an audit
  trail is not shippable in a moderation-bearing system.

Django admin has no equivalent, and for this engine it is the highest-value
panel per line of code written.

### P9. SQL

Read-only, parameterized, statement timeout, row cap, `EXPLAIN ANALYZE`, audited,
behind `CONSOLE_SQL_ENABLED`. Runs on a worker via `defer.in_thread` with
`close_old_connections` hygiene, never on the reactor.

Plus a **slow-query toggle**: `performance.md` §6 currently instructs the reader
to paste a `LOGGING` block into settings and restart to see queries. That should
be a switch with a bounded ring buffer and an off-by-default state.

### P10. Actions

Cmdsets are retired; `try_action_dispatch` is the sole player-input path.

- Registered actions, their rules by phase, predicates, and required
  capabilities.
- **Dry-run dispatcher.** Given an actor and an input string, show what it
  resolves to and which predicate rejected it if it does not. The existing
  `Found` / `Ambiguous` / `NotFound` typed search results make this presentable
  without inventing a diagnostic format.

### P11. Hooks

H1's registry is descriptive validating metadata with a doc generator. Render the
contract table live, show game overrides, and flag base-class methods failing the
startup lint. Near-free given the registry exists; high value for anyone learning
the engine.

### P12. Server control

Django admin cannot restart anything. This is the panel that makes the console an
operations surface rather than a database viewer.

- Portal and Server status as separate processes, since they are separate
  concerns and one survives the other's reload.
- **Reload, reset, shutdown**, with a typed confirmation naming the game and the
  connected-player count, and a broadcast-warning option. Every invocation
  audited with the actor.
- Redis stream health for the session/admin IPC bus, and AMP launcher-link
  status.
- Uptime, process identity, `evennia.__version__` including the git rev the
  running process was started from — which is the single most common question
  during an incident and currently requires shell access.

The relationship to `evennia_launcher` needs settling: the console runs inside
the Server process, so it can request a reload but cannot start a process that is
not running. Scope it to what the running Server can do, and say so plainly in
the UI rather than offering a dead button.

### P13. Live sessions

`SessionRecord` is history. This is the present.

- Connected sessions from the portal: protocol, account, puppet, idle time,
  command rate, negotiated capabilities, screen size.
- `ControlBinding` focus stack per session, since that is now the authority on
  session-to-puppet.
- **Watch**, subscribing to a session's render output through the existing
  read-only sink mechanism. ENGINE.md already lists bounded semantic timelines
  and pluggable read-only sinks supporting cameras, recordings, and headless
  consumers. This is that, aimed at staff.
- **Disconnect** and **kick** with a reason, audited, and a one-click path into
  the moderation panel prefilled with the session's identifiers.

Watch is surveillance and must be treated as such: always audited, and visible
in the audit timeline of the account watched. It has no separate grant, which
means the audit trail is the entire control and has to be reliable. The phase 3
security review covers this specifically.

### P14. Errors

A self-hosted traceback inbox. `core-beliefs.md` says the framework should avoid
requiring external services for core functionality; error tracking is core
functionality for anyone running a game, and today the answer is grep.

- Group tracebacks by signature, with count, first seen, last seen.
- Full frames with local context, linked to source.
- Correlation ID join into the render node and session, same as the log panel.
- Acknowledge and mute-until-changed, so a known issue stops drowning a new one.
- Bounded ring buffer plus a small persisted table for signatures; no unbounded
  growth and no new retention policy.

This reads the log stream rather than adding a new capture path. If a signature
store earns a model, it earns it under the "queryable indexed state" pattern from
`core-beliefs.md`, named explicitly in the migration.

### P15. Database

The panel the JSONB migration made necessary.

- Table sizes and row counts, with the `db_attrs` document-size distribution
  called out. `performance.md` §8 is a rule nobody can currently verify against
  their own data.
- Index usage from `pg_stat_user_indexes`, so the GIN index on `db_attrs` and the
  moderation substrate's many indexes can be shown to be earning their write
  cost.
- Connection pool state and long-running query list.
- Migration-adjacent: index bloat and last vacuum/analyze.

PostgreSQL-only. On SQLite it shows a clear explanation, not an empty table.

### P16. Migrations

- Applied and unapplied migrations per app, with the plan for what `migrate`
  would do next. `migrate` runs automatically on `@reload`, so "what is about to
  run" is a question worth being able to answer before pressing reload.
- A `makemigrations --check --dry-run` equivalent, flagging models whose
  migration was never generated. That is a real recurring failure mode and it is
  silent until deploy.
- Read-only. The console does not run migrations; it tells you what is pending.

Coordinate with [`ALPHA-migration-squash.md`](ALPHA-migration-squash.md): the
squash work would benefit from this panel existing first, and the panel must not
assume the current migration graph shape.

### P17. Settings

`settings_default.py` is ~1,850 lines. A game overrides some fraction of it in
`server/conf/settings.py`, and today the only way to know the effective value of
anything is to import Django settings in `@py`.

- Effective value, default value, and which file supplied it, with overrides
  highlighted.
- Grouped by subsystem, searchable, deep-linkable.
- **Secret masking** by name pattern and an explicit denylist, applied in the
  service. `SECRET_KEY`, database passwords, Redis URLs, API tokens, and the
  moderation hash-chain salt if one exists must never reach the browser. The
  masking list is engine-owned and extensible by setting, and the panel shows
  "masked" rather than omitting the row, so an operator can tell the difference
  between "not set" and "not shown".
- Read-only. Runtime-editable configuration is `ServerConfig`, which lives in the
  Records lens.

### P18. Prototypes

- Browse `PROTOTYPE_MODULES` with inheritance resolved.
- Spawn preview: what this prototype would produce, without producing it.
- Diff a spawned object against the prototype it came from, which is how you find
  out that a room was hand-edited three years ago.

`performance.md` §8 requires prototypes to live in version-controlled Python
modules, so this panel is deliberately read-only. Editing prototypes from the web
would reintroduce exactly the unversioned-data problem that rule exists to
prevent.

### P19. Health

One page that answers "is the server healthy" without interpretation.

Database reachable, Redis reachable, portal link up, attribute flush backlog
within bound, unmanaged-ORM-access counter at zero, reactor stall watchdog quiet,
scheduler systems all fired within their cadence, job queue not growing, dead
letters at zero, open flags count.

Green or not green per line, with the underlying number. Also served as a plain
JSON endpoint so an external uptime check can consume the same judgement rather
than inventing its own.

### P20. Jobs

Committed by D6 and never numbered until the 2026-08-20 audit found it missing.

- Queue depth by status and by `job_type`, across the six live handlers in
  `world/engine_jobs.py`.
- Dead-letter inspection with the failing payload and the traceback that put it
  there, and a requeue action.
- Rates per `job_type`, so a handler that has quietly stopped draining is
  visible before its queue is the thing that reports it.

Ungated: D6 confirms the subsystem is in use.

### P21. Event bus

The other half of D6. Labelled **Event bus**, never a bare "Events", so it stays
distinguishable from `evennia.actions.events` after the `evennia.events`
deprecation shim is dropped.

- `GameEvent` rows by subject, with the payload rendered rather than shown raw.
- Subject prefixes as a filter, since the subjects are namespaced by producer.
- Read-only. The bus is a stream; nothing here replays or injects onto it.

### P22. Audit

The console's own record, which it currently writes and cannot show.

- `ConsoleAuditEvent` rows by actor, panel, operation, outcome, and target,
  with the frozen before/after payloads rendered as a diff.
- The five-way outcome vocabulary shown as itself. A `partial` and a
  `recovery_required` must not render alike; the distinction is the reason the
  taxonomy exists.
- Undo where the row carries an inverse, per the cross-cutting promise, gated on
  `can_undo()` so a row without one offers nothing rather than a control that
  fails.
- Retention class per row, so an operator can see what is about to age out
  before it does.

This panel is what makes the audit trail a feature instead of a liability: an
unreadable record satisfies an auditor on paper and helps nobody at 03:00.

---

## Cross-cutting behaviour

Not panels. Properties every panel inherits from the shell, which is what makes
the console feel like one product rather than twenty pages.

**Degraded mode.** The web worker and the Server process fail independently. When
`run_on_io_thread` raises `IOThreadCallUnavailable` — game server down, web
server up — the console must not become a 500 page. It drops to read-only:
Records, Migrations, Settings, Database, Logs, and Errors all still work, because
none of them need the IO thread. Every `@io_action` is visibly disabled with the
reason. This is a capability Django admin does not have and an operator needs
precisely when things are worst.

**Saved views.** Any filter combination is nameable, shareable by URL, and
pinnable to the nav. A moderation queue filtered to one flag kind, or a
`SessionRecord` query for one CIDR, is a thing staff rebuild by hand daily.

**Export.** CSV and JSON on any list view, bounded by the same row caps as the
view, and audited. An export of moderation data is a disclosure event and is
recorded as one; revealed addresses inside an export are recorded per address,
same as an on-screen reveal.

**Bulk operations with dry run.** Select rows, choose an operation, see the
per-row outcome and the cascade preview, then commit or discard. Django admin's
bulk actions commit immediately and report afterwards.

**Undo and state-as-of.** The audit record carries before/after payloads; where
the service can build an inverse, offer undo. Independently, reconstruct an
object's field state as of a timestamp by folding audit rows backward. This is
not event sourcing and adds no store — it reads the audit table the console
already writes, and it degrades honestly to "no audit coverage before <date>".

**Multi-operator presence.** Show who else has the console open and what record
they have open. Warn before two people edit the same row, and reject the second
write with a conflict rather than silently overwriting. Two staff working the
same flag queue is the normal case, not the edge case.

**Command palette and keyboard-first navigation.** The audience is developers and
staff who will use this daily. Every action reachable without a mouse.

**Everything deep-linkable.** Every row, filter, and panel state has a URL, so a
bug report is a link.

**English only.** Per the unresolved [engine i18n
policy](engine-i18n-policy.md), staff tooling is developer-facing and should be
declared English-only rather than half-wrapped in `gettext`. Say so explicitly so
the question does not get relitigated per panel.

---

## Transport and authentication

**Request/response:** DRF, extending the existing `web/api/` app rather than
starting a parallel one. `EvenniaPermission` already maps DRF actions to
capability checks via `has_capability`, and the API already publishes an OpenAPI
schema with redoc. Console endpoints live under `api/console/` with their own
capability namespace.

**Push:** one websocket at `/console/feed`, Azaban envelope shape.

**Capabilities: two, not a tier system.** Decided. The console is the server
backend, not a staff tool. Holding console access means holding a REPL on the
running process, which is superuser access by every definition that matters — a
finely-sliced permission grid over it would be theatre, implying a containment
the REPL destroys in one line.

| Capability | Grants |
| --- | --- |
| `engine.console.access` | The entire console. Every panel, every operation. |
| `engine.console.moderation` | Console entry showing **only** the moderation panel. |

`engine.console.access` ships as a bundle (`engine.console.full`) expanding to
itself, so the R3 rule that bundles expand to explicit capabilities and never
imply ranks is respected without inventing ranks to imply.

The second capability exists for one reason, and it is not a permission tier: a
deployment with non-superuser moderators must not have to grant them a REPL. It
is the one panel with an audience that is not "whoever administers this server."
A deployment with no such audience never issues it and never thinks about it.

**Panels do not declare capabilities.** `Panel.capability` collapses from a
per-panel field to a single `moderation_only` boolean on the moderation panel.
Nineteen panels do not need nineteen grants; a panel that needed its own grant
would be evidence it belongs somewhere other than the console.

**What replaces the fine-grained grid:**

- **Deployment settings, not capabilities**, for the dangerous panels.
  `CONSOLE_REPL_ENABLED`, `CONSOLE_SQL_ENABLED`, `CONSOLE_SERVER_CONTROL_ENABLED`
  default off in production-shaped configurations. This is policy about what the
  deployment permits at all, which is a different question from who may do it,
  and it is a question a capability cannot express.
- **Audit, not authorization**, for the sensitive reads. Revealing a raw address
  is not a permission check — see the moderation panel below.

**Existing names to reconcile, not stack.** `engine.runtime.manage` gates `@py`
today and is not reused: the web REPL is reachable only through
`engine.console.access`, which is strictly narrower in audience and wider in
power. `engine.moderation.manage` gates `@wall`, `@force`, and channel
administration and is untouched by this work — it governs in-game staff actions,
not console entry.

**Session binding.** Django's session cookie authenticates. The console must not
accept a bearer token that survives forwarding — Underspire's
`STAFF_TOOLS_REQUIRE_OWNER_SESSION` pattern (session plus a scoped custom header,
where the header is the CSRF defence because it is unreadable cross-origin and
preflight-gated) is the shape to promote, since a leaked console URL must
authorize nobody.

**Live re-check.** Capability is re-checked per request and per feed message, not
cached at connect. A demotion or suspension revokes access immediately. The
authorization cache generation counters make this cheap.

---

## Frontend

Svelte 5 + Vite, matching the stack already proven in `underspire/rrms-client`.
Build output committed to `evennia/web/console/static/console/` (C9).

- Single shell, left nav from the registry, panels lazily loaded. Nav filtering
  has exactly two shapes: everything, or moderation only.
- Virtualized tables — the Records lens must handle a million-row `SessionRecord`
  table without the browser deciding otherwise.
- Keyboard-first: command palette, `/` to filter, `g` then a key to jump.
- Dark and light, following the viewer's system setting.
- **No external asset hosts.** No CDN, no webfont host, no analytics. The console
  must work on an air-gapped deployment and must not leak the existence of a
  staff session to a third party.
- A panel's data layer is generated from `spec.py`, not hand-written per panel.
  Nineteen bespoke table implementations is how this project fails.

---

## Coexistence and retirement

Mount `/console/` alongside `/admin/`. Change nothing about the latter at first.
Note that `evennia/web/urls.py` already has the admin include **commented out**
and games mount it themselves — the engine is not currently forcing it on anyone.

Then run the playbook R3 used on locks, because it worked and the vocabulary
already exists in this repo:

1. New authority ships and both run in parallel.
2. Parity is diffed per model kind — same mechanic as `auth_diff_locks`, over an
   admin operation matrix.
3. Kinds are frozen one at a time via a setting, so the old path stops being
   authoritative for that kind while remaining as rollback evidence.
4. Removal is a later, deliberate, destructive pass — the equivalent of R3F,
   explicitly out of scope here.

Nothing in phases 1-4 below removes a Django admin capability.

---

## Engine/game placement

Per [`core-beliefs.md`](../docs/core-beliefs.md), the framing test is whether a
second consumer could reasonably re-implement it from scratch. **Every panel in
this plan is lane 1, pure engine infrastructure.** None of it can be
re-implemented downstream because none of it is reachable from game code: the
mutation kernel, the metrics, the authorization internals, the migration graph,
the settings resolution, the log files, the process control, and the moderation
substrate all live in `evennia/`.

That uniformity is the point of deferring the mapper. A console that is entirely
lane 1 has no placement argument to have, and no game-shaped dependency to carry
into a second consumer's install.

The belief that settles it: *"The framework should be complete — Evennia includes
its own web server, webclient, admin interface, and REST API."* The admin
interface is engine-owned by doctrine. It is simply four years out of date with
respect to the engine underneath it.

Moderation is the direction signal firing the other way. The engine owns the
models, capture, detection, enforcement, hash chain, and retention; the game grew
the UI because the engine did not offer one. Per `FUTURE-IDEAS.md`, that is a
pull-in, and this plan pulls it in.

Underspire's NPC builder, GM dashboard, faction tools, bug queue, pending
requests, and character notes are lane 3 and stay downstream. RRMS is an IC
player surface and is not a console concern at all.

---

## Phasing

Mapped to the fork's release convention (`6.0.0+underspire.N`). Each phase is
independently shippable and leaves the tree working.

### Phase 1 — kernel and Records

The one that pays immediately, and mostly moves code that already has tests.

1. `evennia/console/` package skeleton; `CONSOLE_ENABLED`,
   `CONSOLE_PANEL_MODULES` settings.
2. `services.py`: move `web/admin/io.py`, drop the `django.contrib.admin`
   imports, keep the codec and budgets intact. `web/admin/mixins.py` imports from
   the new location — Django admin keeps working unchanged, which is what makes
   the existing 985 lines of tests a live regression net for the move.
3. `audit.py` plus migration; dual-write with `LogEntry`.
4. `spec.py`: models and typeclasses only.
5. `registry.py` with the `Panel` / `@io_action` split.
6. DRF endpoints under `api/console/`.
7. Frontend shell plus the Records panel.
8. Auto-derived specs for the eleven plain `server/models.py` models, with
   `Sanction`, `SanctionHit`, and the authorization tables marked read-only.

Also in phase 1, because they are cheap and immediately useful and need no new
infrastructure: **Migrations**, **Settings**, and **Health**. All three are
read-only, worker-side, and work in degraded mode. Health in particular should
exist before anything risky ships.

**Exit criterion:** every model Django admin can edit, the console can edit, plus
eleven it never could — and the operator can see what is pending, what is
configured, and whether the server is well.

### Phase 2 — the subsystems the admin never caught up with

Attributes, Authorization, Moderation, Database.

Highest value density in the plan. `spec.py` grows capabilities. The moderation
panel is the promotion described above, with the escalation flow wired to
`moderation/sanctions.py` rather than to the generic writer, and steps 1-3 of the
moderation migration land here. Step 4 (game-side removal) is a downstream commit
after one release of parallel running.

### Phase 3 — the live layer

`feed.py` first; it is the largest single piece of new infrastructure in the plan
and everything else in this phase depends on it. Then Runtime, Logs, Errors, Live
sessions, Server control.

Then REPL and SQL, gated on the dedicated security review named in Risks. Those
two plus Server control and session Watch should not ship until that review has
happened.

The correlation-ID join is the last item and may slip to phase 4 without blocking
anything.

Resolve the jobs/event-bus doc-rot question before the job-queue view.

### Phase 4 — introspection

Objects, Actions, Hooks, Prototypes. The panels that make the engine legible to
someone learning it, and the ones that most exercise `spec.py`.

The registry API is now used by twenty-three panels, which is the real proof
that it is a public API — a stronger test than importing one external tool
would have been.

Phase 4 itself landed with eighteen, not nineteen: P15 Database was not built
until phase 5.

### Phase 5 — the layer that makes it one product

Named by the 2026-08-20 audit and built the same day. Ordered by what an
operator reaches for first during an incident.

1. **P22 Audit**, with undo and state-as-of.
2. **The Runtime frontend gap** — `systems` and `tasks` were computed, tested,
   and dropped by the renderer.
3. **P3 Attributes: the tree, and the write path.**
4. **Deep linking for every panel.**
5. **A command palette**, replacing a digit shortcut the panel count outgrew.
6. **P15 Database**, **P20 Jobs**, **P21 Event bus**.
7. Export, saved views, bulk dry run, multi-operator presence.

All landed. What remains is not console work: the moderation migration steps
below, and the release that drops the `evennia.events` shim.

### Deferred

- **Mapper**, and any other game-shaped panel. Revisit after phase 4.
- Removing Django admin.
- R3F-style destructive cleanup of anything.
- A plugin system.
- Any new event store or retention surface.

---

## Testing

- **Move-first.** Phase 1 step 2 must be a pure move: the existing
  `test_mutation_io.py` / `test_changelist_io.py` / `test_objects_io.py` suites
  pass unchanged against the relocated services before any new caller exists.
- **Thread-boundary tests.** Assert that a worker-context panel method cannot
  reach a live typeclass instance, and that `@io_action` returns are frozen. This
  is C3's enforcement and deserves its own test module.
- **Outcome taxonomy.** Assert all five C4 outcomes render distinctly and that
  only pre-start `IOThreadCallTimeout` offers a retry. Assert an audit-write
  failure produces a warning beside a completed mutation, never a retryable
  error.
- **SQLite parity.** CI runs SQLite; the JSONB containment queries are
  PostgreSQL-only. Every PostgreSQL-only feature must be explicitly disabled with
  a stated reason on SQLite and covered by a test asserting the disable, not left
  to fail at runtime in a way only production sees.
- **Route enumeration under `engine.console.moderation`.** Every registered
  console route, asserted unreachable except the moderation panel's own. This is
  the only internal boundary in the design, so it gets tested by enumeration
  rather than by example, and a newly added panel must fail this test until it
  is explicitly considered.
- **Live revocation.** Access removed mid-session terminates the feed and the
  next request, without waiting for a cache TTL.
- **No `.only()` / `.defer()` lint.** A test that greps the console package for
  both, since C1 is silent when violated on a cached instance and loud only in
  production.
- **Audit completeness.** Every mutating path writes an audit row; assert by
  enumerating `@io_action` methods rather than by listing them by hand.
- **Degraded mode.** Run the full panel registry with the IO bridge stubbed to
  raise `IOThreadCallUnavailable`; assert no panel 500s and every `@io_action`
  reports disabled with a reason.
- **Masking.** Assert that an unrevealed address, device token, or hash never
  appears in any response body, including error messages and export output, and
  that every reveal writes an audit row. Test the service, not the template.
- **Secret masking.** Same shape for the Settings panel: assert `SECRET_KEY` and
  database credentials are unreachable through every endpoint that touches
  settings.

---

## Risks

**Scope.** Nineteen panels is a product, not a feature, and this is the risk that
actually kills the work. Phase 1 alone is justifiable on its own merits and
everything after it is optional. If phases 2-4 never happen, the engine still
gained a generic admin covering eleven more models than the old one, plus
migration, settings, and health visibility it never had. Treat that as the
commitment and everything after as earned.

The counter-pressure is real too: an operations console that stops halfway is a
worse product than either endpoint, because staff learn two tools instead of one.
The phase boundaries are drawn so each one is a coherent stopping point.

**One consumer.** `FUTURE-IDEAS.md` warns specifically against designing for
hypothetical demand, and cites `jobs/` and `events/bus.py` as built-and-unused.
The mitigation: every panel must serve Underspire on the day it ships. Any panel
that cannot be justified by a real Underspire need is deferred regardless of how
clean the abstraction is.

**Frontend maintenance.** A committed build artifact rots and a Svelte major
version will eventually force work. Accepted, but it should be a conscious
acceptance: the alternative is server-rendered HTML with far less capability,
and the engine already ships a webclient with the same class of obligation.

**The feed is real infrastructure.** Websocket auth, resume, backpressure, and
fan-out are where this could go badly. Reusing Azaban's runtime contract instead
of inventing one is the mitigation, and it is worth failing phase 3 over rather
than writing a second protocol.

**Security surface, under the one-capability decision.** A REPL, a SQL console,
live server control, session watching, moderation data with raw addresses, and
break-glass grant issuance on one web page is by a wide margin the most dangerous
surface the engine has ever shipped. Collapsing to one capability does not make
that better or worse; it makes it *honest*, and it moves where the defence has to
live.

The threat model is now explicit: **`engine.console.access` is equivalent to
shell access on the game server.** Anyone holding it can read every secret,
mutate every row, and execute arbitrary Python in-process. Granting it is a
trust decision of the same magnitude as adding an SSH key, and the documentation
must say exactly that where an operator will read it before granting.

Consequences, all of which grow in importance rather than shrink:

- **Session integrity is the whole defence.** With no internal permission
  boundaries, a stolen console session is total compromise. Session binding,
  the scoped-header CSRF defence, short idle timeouts, and re-authentication
  before the REPL and server control are no longer defence in depth. They are
  the defence.
- **Audit is the only internal control left.** Every action, including reads
  that reveal masked values, and including every REPL submission with its source
  text. An audit trail that a console operator can edit is not one — the audit
  table must be append-only at the service layer with no console write path,
  which means it appears in the Records lens as read-only like the sanction
  chain.
- **Deployment settings carry the load that capabilities were carrying.**
  `CONSOLE_REPL_ENABLED`, `CONSOLE_SQL_ENABLED`,
  `CONSOLE_SERVER_CONTROL_ENABLED`, and `CONSOLE_ENABLED` itself are the only
  way to make a panel unreachable. They need to be prominent, defaulted safely,
  and impossible to flip from inside the console.
- **The moderation-only path is a real security boundary**, not a convenience.
  A holder of `engine.console.moderation` must be unable to reach any other
  panel's endpoints, not merely unable to see them in the nav. Test it by
  enumerating every registered route against that grant.

**Dedicated security review remains a gate on phase 3.** It should cover: session
theft as total compromise, re-authentication before dangerous panels, session
watching as surveillance, address masking service-side rather than
template-side, export as a disclosure event, the moderation-only route
enumeration, and whether server control belongs on the same origin as the player
website at all.

**Moderation migration.** Removing the game-side surface (step 4) while staff
depend on it daily is the one step in this plan with a real operational failure
mode. It happens after a full release of parallel running, and the rollback is
"revert the deletion commit" — which only stays true if the deletion is a single
commit that touches nothing else.

**Degraded mode is easy to break and hard to notice.** It works on the day it
ships and silently rots the first time a panel adds an unconditional IO call.
Cover it with a test that runs the whole panel registry with the IO bridge
stubbed to raise `IOThreadCallUnavailable`.

---

## Decisions

Every question this plan opened, now answered. Recommendations, not commitments:
disagree and the answer changes, but the executing agent starts from here rather
than from a blank page.

### D1. Capabilities — two, not a grid

`engine.console.access` (everything) and `engine.console.moderation` (moderation
panel only). Console access is equivalent to shell access, so a fine-grained grid
would imply a containment the REPL removes in one line. Dangerous panels are
governed by deployment settings; sensitive reads by audit.
`engine.runtime.manage` is not reused. Details under "Transport and
authentication".

### D2. Audit model placement — a new `evennia.console` Django app

Not `server`. That app already carries eight migrations and owns authorization,
moderation, and jobs; it is also a squash target in
[`ALPHA-migration-squash.md`](ALPHA-migration-squash.md). A new app gets one
clean initial migration with zero squash interaction, and `evennia/console/`
already exists as a package, so the cost is an `apps.py` and one
`INSTALLED_APPS` line in `settings_default.py` — free for games, which never
edit that list.

It also matches the existing pattern: each subsystem owns its own audit trail
(`AuthorizationAuditEvent` for R3, the `GameEvent` bus for domain events). And it
is deletable as a unit if the console is ever dropped.

**The console audit does not replace the event bus, and does not duplicate it.**
The console emits a `console.*` subject onto `evennia.eventbus` for the
notification and export path, and separately writes its own structured row. The
bus payload is a JSON `TextField`; it cannot carry indexed before/after
snapshots, an inverse for undo, the five-way outcome, and a queryable target ref.
The bus is the stream; the audit table is the record.

### D3. Auto-derived specs — neither option. The question dissolves.

Inspect what the eleven models actually are and **none of them wants a generic
write path**:

| Model | Mutation path |
| --- | --- |
| `SessionRecord`, `SanctionHit`, `AuthorizationAuditEvent`, `GameEvent` | none — records, read-only by nature |
| `Sanction` | `moderation.sanctions.issue_sanction` / `revoke_sanction` |
| `ModerationFlag` | `moderation.flags.resolve_flag` |
| `AuthorizationGrant`, `AuthorizationScopeLabel`, `AuthorizationPolicyOverride`, `AuthorizationPrincipalState` | the authorization service |
| `EngineJob` | `jobs.queue.enqueue_job`, plus requeue and dead-letter operations |

So: **generic read for all eleven, zero auto-derived write specs, domain actions
wherever mutation is wanted.** That is fail-closed by construction (C6), honours
the bridge doc's extension rule verbatim (C7), and costs zero days rather than
the one the question estimated.

Auto-derivation stays unbuilt until a model actually asks for it, per the
hypothetical-demand warning in [`FUTURE-IDEAS.md`](../../FUTURE-IDEAS.md).

### D4. `spec.py` vs the H1 doc generator — neither supersedes; both are consumers

`evennia.hooks` already exposes `list_all()`, `for_event()`, `describe()`, and
`lint()` over `_REGISTRY`. `evennia/hooks/docs.py` is a markdown emitter with
`--check` / `--write`, writing between marker pairs in the published docs.

`spec.py` becomes a **third consumer of the same registry**: it calls
`hooks.list_all()` and `hooks.lint()` and never touches `_REGISTRY` directly.
Divergence is then impossible by construction rather than by discipline.

Nit spotted while confirming this: `evennia/hooks/__init__.py` points its
docstring at `.agents/docs/engine-api-architecture.md`, which does not exist —
the path is `.agents/docs/engine-architecture/`. Outside `clean_rot`'s scope
because it lives in engine code rather than under `.agents/`.

### D5. Feed transport — share the contract, not the code

**Separate implementation. Decided, and not close.**

`AzabanFormat` lives in `evennia/server/portal/wire_formats/azaban.py` and
subclasses `WireFormat`. It is a **Portal** concern, coupled to session protocol
flags and client capability negotiation. The console is a **Server**-side Django
surface. Sharing the implementation would route staff traffic through the player
protocol stack and violate "Portal and Server are separate concerns" from
[`core-beliefs.md`](../docs/core-beliefs.md).

The clincher is a requirement that runs backwards: Azaban implements
`_contains_raw_identity()` and **rejects raw identity in public payloads**,
substituting viewer-scoped expiring handles for database IDs. The console needs
dbrefs on every row. Sharing the encoder means fighting a load-bearing security
check on every frame, which is how such a check eventually gets weakened for the
wrong reason.

So: copy the **discipline** documented in
[`webclient-protocol-runtime.md`](../docs/engine-architecture/webclient-protocol-runtime.md)
— typed `t` discriminant, monotonic `s` stamping, replay buffer across
reconnect, batch coalescing, structural byte limits enforced before parse — and
implement it Server-side in `evennia/console/feed.py`.

The session-watch panel consumes a Server-side read-only render sink, not the
Portal stream, which keeps the boundary clean there too.

### D6. `GameEvent` and `EngineJob` — both live. Panels ship. The prompt is stale.

Step 1 of [`ALPHA-jobs-eventbus-boundary.md`](ALPHA-jobs-eventbus-boundary.md)
is "read the game repo to see whether it already has usage." Done, and the
answer is yes for **both** subsystems. That prompt's "zero in-repo consumers,
verified by grep" predates the game-side wiring.

**`evennia.jobs` — wired.** Enqueue sites in
`server/conf/at_server_startstop.py` (four), `world/account_signup.py`,
`world/channels/discord_bridge.py`, `world/channels/staff_discord.py`,
`world/mail.py`, and `world/systems.py`. Drained by `world/systems.py` through
`process_pending_jobs`. Handlers live in `world/engine_jobs.py`: help, search,
active-entity, and channel-subscriber index rebuilds; Discord webhooks; email
send; moderation network-list refresh; signup screening; game-event export.

**`evennia.eventbus` — wired.** Consumed through `world/audit/emit.py`
(`audit_economy_transfer`, `audit_moderation_action`,
`audit_staff_pending_resolve`, `audit_perm_change`), called from
`world/rpg/economy.py`, `world/staff_pending.py`, and `world/tickets/core.py`.
`job_game_event_export` reads `GameEvent` rows out to JSONL under `GAMELOG_DIR`.

Verdict: **wire, both — already wired.** The Jobs panel and a `GameEvent` view
ship as planned, and the ALPHA prompt should be rewritten from "wire or cut" to
"confirm the contract and fix the collision."

They are **P20** and **P21**. This decision originally named no panel number,
and the panel list ran P1 to P19 without them, so the commitment was invisible
to anyone reading either section alone and neither panel was built. Numbering
them is the fix.

**The name collision is resolved.** The bus moved to `evennia.eventbus` on
2026-08-19, so it no longer shadows `evennia.actions.events` (the live
in-process registry), and a console panel can safely be labelled for either.
`evennia.events` survives as a deprecation shim for one release. Panel naming:
use **Jobs** and **Event bus** as labels, never a bare "Events", so the two
systems stay distinguishable in the UI even after the shim is gone.

### D7. Django admin deprecation — no timeline, by design

Confirmed as intent. Keep it indefinitely:

- It is the R3-style rollback oracle, and the coexistence section depends on it.
- It costs the engine nothing: `evennia/web/urls.py` already has its include
  commented out, and games mount it themselves.
- Its 985 lines of tests are the regression net for the phase 1 kernel move.

Set a **review trigger rather than a date**: when the console has run one full
release with no operator falling back to `/admin/`, open removal as its own
prompt — the R3F analogue, a deliberate destructive pass, not a cleanup.

### D8. Moderation capability reconciliation — settled by D1

`engine.moderation.manage` is untouched and keeps governing in-game staff
actions. `underspire.moderation.act` maps to `engine.console.moderation`.
`underspire.moderation.address` is retired in favour of audited reveal; both
Underspire capabilities are deleted at migration step 4.

One judgement to confirm rather than a question: this widens who can see a raw
address, from a small held set to every moderator, in exchange for a record of
each viewing. If that trade is wrong for Underspire, the fix is a third
capability, and this plan should say so before phase 2 rather than after staff
notice.

### D9. Server control origin — same origin, setting defaults off, re-auth required

**Split the panel.** Status, uptime, version, and process health are read-only
and belong on the same origin as everything else. Reload, reset, and shutdown sit
behind `CONSOLE_SERVER_CONTROL_ENABLED`, **default off**, and when enabled
require re-authentication within a short window before each invocation.

Do not build a separate origin. It is real deployment complexity — second vhost,
second cookie domain, second TLS config — bought against a threat that mostly is
not there: anyone holding `engine.console.access` already has a REPL, and an
operator who cannot be trusted with reload cannot be trusted with the REPL two
panels over. The blast-radius concern is real, but the answer is the default-off
setting plus re-auth, not a second deployment surface.

If a deployment genuinely wants process control isolated, `CONSOLE_ENABLED=False`
on the public web node plus a console-only internal node is the shape — a
deployment topology, not an engine feature.

### D10. The staff index — the engine does not own it

Confirmed. Bugs, pending requests, and character notes are game workflows over
game models; the engine has no generic "queues staff should look at" concept and
should not grow one for a single consumer.

The one integration point: the console's health endpoint (P19) already serves
plain JSON, so the game's staff index keeps rendering its open-flag count inline
by reading that, without importing console internals or querying moderation
models directly.

### D11. Audit retention — a setting, matching the existing convention

Precedent is established and consistent: `MODERATION_IP_RETENTION_DAYS = 90`,
`MODERATION_SESSION_RETENTION_DAYS = 365`, `LOG_ROTATED_RETENTION_DAYS = 14`.
Follow it.

- `CONSOLE_AUDIT_RETENTION_DAYS = 365`
- `CONSOLE_AUDIT_REPL_RETENTION_DAYS = 90` — REPL source text is the bulkiest
  audit content and the least often needed long-term, so it gets its own knob.

Pruned by a `calendar(daily)` System Scheduler registration, never a new timer,
per AS2.

**Two classes are never pruned**, regardless of setting: rows whose target is a
moderation decision, because appeal evidence must outlive any retention window;
and rows recording a break-glass grant, because those are the ones an
investigation will want and the ones an attacker would most want gone.

---

## References

- [`engine-architecture/committed.md`](../docs/engine-architecture/committed.md)
  — W1, the parent item, and its preservation constraints.
- [`engine-architecture/decisions.md`](../docs/engine-architecture/decisions.md)
  — JSONB attributes, partial-load rule, AS2, H1, ControlBinding, R1, CM1.
- [`engine-architecture/horizon.md`](../docs/engine-architecture/horizon.md)
  — the event-sourcing caution and its evidence.
- [`engine-architecture/r3-authorization.md`](../docs/engine-architecture/r3-authorization.md)
  — capability contract, cache lifecycle, break-glass, `auth_diff_locks`.
- [`engine-architecture/webclient-protocol-runtime.md`](../docs/engine-architecture/webclient-protocol-runtime.md)
  — Azaban's runtime contract; the feed's model.
- [`core-beliefs.md`](../docs/core-beliefs.md) — engine/game placement, fail
  closed, cache discipline, framework completeness.
- [`FUTURE-IDEAS.md`](../../FUTURE-IDEAS.md) — three-lane breakdown, plugin
  deferral, the hypothetical-demand warning.
- `evennia/moderation/README.md` — hard-signals-only stance, capture write path,
  address provenance.
- `underspire/web/website/moderation_services.py` — the reference implementation
  the moderation panel promotes: row caps, IO-callback discipline, the act /
  address capability split.
- `underspire/web/website/views/moderation.py`,
  `underspire/web/templates/website/moderation_*.html` — the game-side surface
  this work removes.
- `underspire/docs/performance.md` — §1 db/ndb, §3 query optimization and
  `prime_tag_caches`, §6 profiling, §8 document size, §9 off-reactor I/O,
  §10 indexed tags.
- [`Web-IO-Boundary.md`](../../docs/source/Components/Web-IO-Boundary.md) —
  **canonical.** Bridge contract, timeout ownership, ORM read/write rules, the
  DTO rule, stock website services. Read before writing any service.
- [`Web-Mutation-Bridge.md`](../../docs/source/Components/Web-Mutation-Bridge.md)
  — **canonical.** The closed admin registry, owner operation, the five-way
  outcome taxonomy, audit ordering, the Tag exception, and the extension rule
  that governs every new adapter.
- `evennia/web/utils/io.py` — `run_on_io_thread` and the three timeout
  exceptions.
- `evennia/server/prometheus_metrics.py` — the 22 metrics.
