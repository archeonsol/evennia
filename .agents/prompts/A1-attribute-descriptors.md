# A1: typed attribute descriptors

Status: todo

## Goal

Make typed descriptors (`hp = IntAttr(default=100, persist=True)`)
the canonical attribute API. Subsume the current swamp (`.db.foo`,
`.attributes.add/get`, `.ndb.foo`, `.tags`, `.aliases`,
`.nattributes`, raw Django fields, `ServerConfig`) by routing
everything through one substrate; sugar layers stay but stop being
the recommended path.

P3 (one canonical answer) applied to the largest API surface in the
engine.

## Background

Target item A1 in
[`engine-api-architecture.md`](../docs/engine-api-architecture.md).

Current attribute storage is the single biggest cognitive load for
serious games. Performance and semantics differ across mechanisms;
no schema, no migrations; every serious game writes a typed wrapper.
`AttributeProperty` exists as a partial solution but isn't the
canonical answer.

## Approach

This is a large item. Take the design pass seriously.

1. Read the architecture doc A1 item and study existing storage
   mechanisms thoroughly.
2. Read [AGENTS.md](../../AGENTS.md).
3. Propose a design covering the questions below. Expect the
   proposal to be substantial (1-2 pages); this is the kind of thing
   a small design doc helps with.
4. **Stop and get user review on your design proposal.** Expect
   multiple rounds.
5. After approval, implement.

## Design questions to resolve in your proposal

- Descriptor taxonomy. `IntAttr`, `StrAttr`, `BoolAttr`, `ListAttr`,
  `DictAttr`, `ObjectAttr` (references to other objects), `JSONAttr`?
  What else?
- Backend selection. Persistence options (db / ndb /
  AttributeProperty / ServerConfig)? Cache layers (memory / lazy)?
  Per-descriptor configuration?
- Relationship to existing `AttributeProperty`. Subsume? Coexist?
  Migrate AttributeProperty users to descriptors?
- Opt-in mechanics. How do `.db.foo` users migrate? Coexistence with
  descriptors on the same class during transition?
- Schema / migration story. If a descriptor changes (rename, type
  change, default change), how is existing data handled?
- Default values. `default=100` vs `default_factory=lambda: ...`?
  When are defaults evaluated?
- Validation. Type coercion or strict typing? At write time, read
  time, both?
- Performance. Descriptor access on hot paths: how does this compare
  to current `.db.foo`?
- Tags and aliases. Descriptor-able? Or stay as separate APIs?
- Engine-internal migration scope. Which engine internals migrate in
  A1 as worked examples? Which wait for follow-up?

## Scope boundary

- **In scope**: descriptor framework, type taxonomy, at least one
  engine-internal consumer (worked example), migration guidance for
  existing storage mechanisms.
- **Out of scope** (for A1 specifically; gradual follow-up): fully
  migrating all engine internals onto descriptors, deprecating the
  current `.db` / `.ndb` / `.attributes` API.

## Existing code to study

- `evennia/typeclasses/attributes.py` — current Attribute system and
  AttributeProperty.
- `evennia/typeclasses/typedobject.py` — `.db`, `.ndb` handlers.
- `evennia/typeclasses/tags.py` — Tags and aliases.
- `evennia/server/models.py` — ServerConfig.
- Game-side typed wrappers in the downstream fork (ask user) for
  prior art.

## Done means

- Descriptor framework implemented with tests covering the taxonomy.
- At least one engine-internal consumer migrated as worked example.
- Migration guidance for existing storage mechanisms documented.
- All existing tests pass; existing storage APIs still work.
- Architecture doc A1 entry updated.
- PR description summarizes design decisions.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Deprecating or removing any existing storage mechanism.
- Migrating engine internals at scale (out of scope for A1;
  follow-up).
- Breaking compatibility with existing `AttributeProperty` users
  without a clear migration path.
- Adding new dependencies for serialization, validation, etc.
