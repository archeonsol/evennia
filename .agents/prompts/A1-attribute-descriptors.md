# A1: attribute storage model

Status: todo (exploratory — direction not yet committed)

## Goal

Decide what the attribute storage layer actually needs, then build
that. The previous framing of this item ("make typed descriptors the
canonical API") prejudged the answer. This rewrite reopens it: typed
descriptors are **one candidate**, not the foregone conclusion.

The honest starting position: the proliferation of storage mechanisms
(`.db.foo`, `.attributes.add/get`, `.ndb.foo`, `AttributeProperty`,
`.tags`, `.aliases`, raw Django fields, `ServerConfig`, plus every
game's own handler wrappers) is **not** accidental sprawl to be tidied
into one canonical API. Each mechanism solves a real problem that
"everything is an Attribute" handles badly. Adding a typed-descriptor
front door without understanding why the others exist just adds a
ninth mechanism.

So the deliverable is a **design recommendation grounded in measured
cost**, and "document the taxonomy and change little" is a legitimate
outcome.

## Background

Target item A1 in
[`engine-api-architecture.md`](../docs/engine-api-architecture.md).
The architecture-doc entry still states the old "descriptors are
canonical (P3)" framing; treat that as the *prior* proposal this
prompt is re-examining, not as settled.

The driving problem is **performance and queryability, not just
ergonomics.** "Everything is an Attribute" pins almost all state into
one shape with two costs:

1. **Row count / query volume.** Each `.db.foo` is one row in the
   Attribute table. Touch many fields across many objects (a room loop,
   a combat tick, an NPC AI pass) and that's a large number of rows in
   play. The cost is in granularity: one logical unit of state can be
   one row or dozens.
2. **Un-queryable values.** Attribute values are pickled blobs. You
   cannot filter by them in SQL ("find all characters with strength >
   5" is a full Python scan, not a query). The only escape is a real
   Django column, which means leaving the typeclass attribute model
   entirely.

Read the existing mechanisms as a **taxonomy of performance profiles**
along those axes, plus persistence (persistent / transient) and scope
(per-object / global):

- `.db` / `.attributes` / `AttributeProperty`: one row per value,
  per-object, persistent. Same backend, three syntaxes. Flexible,
  un-queryable, expensive at scale.
- `.ndb` / `NAttributeProperty`: transient, zero DB cost.
- `ServerConfig` / module vars / `Script`: global singleton state, one
  row (or zero) instead of per-object attributes.
- **Batched-blob handler pattern** (prior art — see below): N related
  values collapsed into a single Attribute holding a dict, so a whole
  subsystem is one row, one query, one write.
- Raw Django fields / models: real columns, SQL-queryable, indexable,
  bulk-operable. The only profile that solves cost #2.

### Prior art in the downstream fork (study this first)

The downstream consumer (newmoo) already solved the query-cost problem
twice, independently, with the **batched-blob handler pattern**:

- Its trait system stores *all* traits under one Attribute
  (`db_attribute_key="traits"`), a single dict; the handler holds a
  live reference so mutations sync back through that one row.
- Its buff system does the same: all buffs in one keyed Attribute
  holding a dict.

These are not "alternative storage APIs." They are the proven move for
hot, grouped per-object state: trade per-field granularity for one
row. Any A1 design should treat this pattern as a serious candidate for
a first-class engine primitive, and should explain why descriptors
(which are *more* granular — one row per field) would or would not make
the row-count problem worse.

### The ECS horizon (name it, probably don't build it)

The ambitious end-state is an ECS-style component store: state lives in
typed components, stored column-wise / contiguously, queryable and
iterable in bulk. That would address both costs at once and is the
natural home for game logic that iterates "all entities with component
X." It is also a **massive** change — effectively a second storage
substrate beside the typeclass+Attribute model — and almost certainly
too large for this item. Name it as the long horizon so the A1 design
does not foreclose it (keep any new primitive backend-agnostic per the
discipline in [`engine-long-horizon.md`](../docs/engine-long-horizon.md)),
but do not commit to building it here without explicit user direction.

## Approach

**Measure before designing. Map before prescribing.** This is
exploratory; the first job is to understand the cost, not to ship a
framework.

1. Read the architecture doc A1 item (the old proposal) and the
   long-horizon note on backend-agnostic descriptors.
2. Study the existing storage mechanisms and the downstream
   batched-blob prior art (ask the user to point at the trait/buff
   handlers).
3. Read [AGENTS.md](../../AGENTS.md) and the downstream
   `docs/performance.md` storage-primitive guidance (ask the user) —
   it already encodes which primitive to use when, and for what
   reason.
4. **Characterize the actual cost.** Where does query volume
   concentrate? Cold-load fan-out, attribute-by-value lookups, or
   write amplification? Each has a different fix and only one is a
   storage-shape change. The fork already carries a redis attr cache
   and a write-behind cache — establish what they already mitigate
   before proposing anything new.
5. **Write a recommendation**, not a foregone design. Cover the
   questions below. It is legitimate to recommend "document the
   taxonomy + promote the batched-blob pattern, build no new
   substrate" if that's what the cost analysis supports.
6. **Stop and get user review.** Expect multiple rounds. The
   direction itself is up for review, not just the details.
7. After approval, implement whatever the recommendation lands on.

## Design questions to resolve in your proposal

- **What is the actual cost?** Query volume by category (fan-out /
  value-lookup / write churn), measured or traced, not assumed. What
  do the existing fork caches already solve?
- **What does each mechanism actually solve?** Map the taxonomy above
  to the problem each profile addresses. Which are genuinely distinct,
  which are redundant syntax for the same backend?
- **Is the problem ergonomics, query volume, queryability, or all
  three?** The right intervention differs sharply per answer:
  consolidating *syntax* (descriptors) does nothing for query volume;
  the *batched-blob* pattern cuts query volume but not queryability;
  *real columns* fix queryability but leave the attribute model.
- **Batched-blob as an engine primitive?** Should the trait/buff
  handler pattern become a first-class, documented engine offering (a
  typed "component bag" backed by one Attribute)? What would its API
  be?
- **The queryable escape hatch.** Is the missing profile a clean,
  ergonomic way to back selected typed state with real indexed
  columns without dropping out of the typeclass model? What would that
  look like?
- **Typed descriptors — still worth it, and for what?** If recommended,
  what do they buy that `AttributeProperty` doesn't (schema, defaults,
  validation, a migration story)? Be explicit that they don't help
  query volume, and where they'd make it worse.
- **Schema / migration story.** This is the one thing nothing in the
  current stack provides: renamed/retyped keys leave stale persisted
  data silently. Is a versioning/migration helper for *declared* state
  the highest-value piece, independent of whatever front door wins?
- **Backend-agnosticism.** Whatever primitive (if any) is introduced
  must pick its backend explicitly at declaration so the cost is
  visible at the call site, and must not assume Django storage (keeps
  the ECS / headless horizon reachable).
- **Migration scope.** What, if anything, migrates as a worked example?
  What stays as-is? Mass migration is explicitly out of scope.

## Scope boundary

- **In scope**: the cost analysis, the taxonomy map, and a design
  recommendation. If the recommendation lands on a concrete primitive,
  a minimal worked example of it with tests.
- **Out of scope**: mass-migrating existing `.db` consumers; deprecating
  any existing mechanism; a full ECS rewrite; removing
  `AttributeProperty`.
- **Legitimate outcome**: "no new substrate; document the taxonomy and
  promote an existing pattern." Don't invent a framework to have
  shipped one.

## Existing code to study

- `evennia/typeclasses/attributes.py` — Attribute system,
  `AttributeProperty`, `NAttributeProperty`.
- `evennia/typeclasses/typedobject.py` — `.db`, `.ndb` handlers.
- `evennia/typeclasses/tags.py` — Tags and aliases (likely a separate
  concern; confirm they belong in scope at all).
- `evennia/server/models.py` — ServerConfig.
- The fork's redis attr cache and write-behind cache — what query/write
  cost they already absorb.
- Downstream fork (ask user): the trait and buff handlers
  (batched-blob prior art) and `docs/performance.md` storage guidance.

## Done means

- A written recommendation grounded in a real cost characterization,
  not an assumed one.
- The taxonomy of existing mechanisms mapped to the problems they
  solve.
- A clear direction the user has signed off on (which may be "build X",
  "promote pattern Y + document", or "do little").
- If a primitive is built: minimal worked example, tests, all existing
  tests pass, existing storage APIs still work.
- Architecture doc A1 entry updated to reflect the chosen direction
  (it currently states the old descriptors-are-canonical framing).
- PR / proposal summarizes the reasoning, not just the change.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Committing to *any* single mechanism (descriptors included) before
  the cost analysis justifies it.
- Starting an ECS-style substrate (long horizon; needs explicit
  direction).
- Deprecating or removing any existing storage mechanism.
- Migrating engine internals or game code at scale.
- Adding new dependencies for serialization, validation, etc.
