# Engine architecture

The engine's architectural **decisions** record: what we chose, why, and what's
confirmed in the tree. This replaces the earlier forward-looking "target" docs,
which drifted stale as the work shipped (a roadmap rots; a decisions log doesn't).

## How to read this directory

- **[decisions.md](decisions.md)** — decided and shipped. ADR-style: the choice,
  the rationale, and where it lives in the tree. This is the load-bearing part.
- **[committed.md](committed.md)** — decided in principle but not yet built (R1
  display pipeline, L1 lock objects, W1 web). Includes current partial state and
  the preservation constraints that bind them.
- **[horizon.md](horizon.md)** — speculative, **not committed**. Major-version
  shape only, no churn estimates, no gates. Exists so today's decisions don't
  close off these paths, not as a plan to execute.

If an item moves from speculative to scoped, it graduates from `horizon.md` up to
`committed.md`, and to `decisions.md` once shipped.

## Two-layer thesis

The engine should separate two layers it currently collapses:

- **Bottom layer** — small, sharp, typed primitives: display pipeline, lock
  objects, hook registry, result types, permission algebra, typed attributes,
  concurrency story, actor abstraction. No friendliness magic, no implicit
  conversions, no string DSLs in hot paths. What serious games target.
- **Top layer** — friendly API: `caller.msg("hi")`, `at_say` defaults,
  `.db.foo = 5`. What tutorials and starter games use, reimplemented as sugar
  over the bottom layer.

Collapsed today, friendly-API conveniences leak into anything trying to be
rigorous, and rigorous concerns (viewer-aware rendering, schema, validation,
identity) have no home but game-side reinvention. The target separates them: the
bottom becomes the substrate, the top becomes sugar on top of it.

## Three principles

**P1. No string DSLs for things the engine itself parses.** Locks, permissions,
merge types, tag categories: Python objects with optional string sugar as a
transitional convenience, not strings with a parser underneath. Typos become
definition-time errors; composition is real; IDE help works. String sugar is a
deprecation path, not a permanent layer.

**P2. Render before deliver.** Every output path produces a structured render
node for a specific viewer first; delivery is a separate step. Filters (language,
sdesc, conjugation, accessibility) plug in as render transforms. If it produces
user-visible text, it goes through render.

**P3. One way to do each thing.** When the engine offers a primitive, that
primitive is the answer. Five attribute-storage mechanisms, two ways to move an
object, string-or-object DSLs that both live forever, three overlapping identity
concepts: all the anti-pattern. Sugar layers delegate to one canonical substrate;
they aren't parallel implementations.

## Related

- [Core Beliefs](../core-beliefs.md) — the engine/game placement rule (framing
  test, agnostic-by-opt-out) that governs what belongs in the engine at all.
- [Alpha-promotion prompts](../../prompts/README.md) — the read-only audit
  burndown that hardens the surface these decisions live on.
