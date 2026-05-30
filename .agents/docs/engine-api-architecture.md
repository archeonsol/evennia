# Engine API architecture

Architectural target for the engine API. Companion to
[`engine-boundary-migration.md`](engine-boundary-migration.md):
migration is fork/engine boundary work, this doc is engine
architecture work.

This doc defines the **target** (where the engine is going) and the
**launch gate** (the subset that must ship before Underspire launch
freezes the public API) as orthogonal annotations. The target is the
full really-good engine. The launch gate is schedule discipline. They
are not the same thing and should not be conflated.

Items here are *where we are going*; we move toward them as early as
possible rather than building on patterns we already know we'll
replace.

## Thesis: two-layer engine

The engine collapses two layers today:

- **Bottom layer**: small, sharp, typed primitives. Display pipeline,
  lock objects, hook registry, result types, permission algebra,
  composable move, typed attributes, committed concurrency story. No
  friendliness magic, no implicit conversions, no string DSLs in hot
  paths. What serious games target.
- **Top layer**: friendly API. `caller.msg("hi")`, `at_say` defaults,
  `.db.foo = 5`. What tutorials and starter games use.

Today these are one layer, so friendly-API conveniences leak into
anything trying to be rigorous, and rigorous concerns (viewer-aware
rendering, schema, validation, concurrency) have no home that isn't
game-side reinvention. The architectural target is to separate them:
bottom layer becomes the substrate, top layer is reimplemented on top
as sugar.

## Three principles

**P1. No string DSLs for things the engine itself parses.** Locks,
permissions, cmdset merge types, tag categories. Python objects with
optional string sugar as a transitional convenience, not strings with
a Python parser underneath. Typos become errors at definition time,
composition is real composition, IDE help works. String sugar is a
deprecation path, not a permanent layer.

**P2. Render before deliver.** Every output path produces a structured
render node for a specific viewer first; delivery is a separate step.
Filters (language, sdesc, conjugation, psychosis, accessibility) plug
in as render transforms. No exceptions: if it produces user-visible
text, it goes through render.

**P3. One way to do each thing.** When the engine offers a primitive,
that primitive is the answer. Five attribute-storage mechanisms is
the anti-pattern. Two ways to move an object is the anti-pattern.
String-or-object DSLs that both work forever is the anti-pattern.
Sugar layers exist for ergonomics; they delegate to one canonical
substrate, they aren't parallel implementations.

## Items

Each item lists: the problem, the **target** (what "complete" means),
the **launch gate** (what subset must ship before Underspire),
churn, and dependencies. Per-item design happens when picked up.

Items are grouped by the principle they primarily serve.

### Serving P2: render before deliver

**R1. Display pipeline.** The keystone.

- Problem: `msg`, `at_say`, `return_appearance`, `get_display_name`,
  `get_display_things`, language and other filters all run together.
  No "render to a tree for viewer X" step separate from "send to
  viewer X." Game-side rendering reaches into sdesc, recog,
  conjugation, scene broadcast, and message-type tagging in one
  function because the engine offers no other shape.
- Target: every output path goes through `render(obj, viewer) ->
  RenderNode` then `deliver(node, viewer)`. Filters are render
  transforms. The existing `msg` / `at_say` reimplement on top and
  stay as sugar. No output path bypasses render. Web clients, AI
  consumers, accessibility filters, offline preview all plug into the
  same render output rather than reaching into the engine.
- Launch gate: **partial**. Seam exists (RenderNode type, separable
  render step, at least one output path migrated). Adding new
  output paths on the old shape is closed off; existing paths can
  finish migrating after launch.
- Churn: large.
- Dependencies: migration B1 (hook taxonomy documents the render-side
  hooks); benefits from C1 (actor as viewer) but doesn't require it.

### Serving P1: no string DSLs

**L1. Lock objects + permission algebra.** Subsumes migration D1.

- Problem: `"cmd:perm(Builder) and not perm(Quell)"` parsed at call
  time. Typos silent. No static analysis. The whole permission story
  is strings-in-strings; `check_permstring` is a footgun (ignores
  quell) because there's no scope-aware resolver.
- Target: `Permission.Builder`, `Permission.Quelled`, `Scope.Account`,
  `Scope.Character`, `Scope.Effective` as objects. `Lock.cmd(
  Permission.Builder)` composable, validated at definition time.
  `check(actor, Permission.Builder, scope=Scope.Effective)` resolves
  quell correctly. String form **deprecated** with a migration path
  and removal in a documented future release. Strings are a
  transitional sugar, not the endpoint.
- Launch gate: **partial**. Object form exists, all engine-internal
  use is on objects, string form is deprecated (warnings) but still
  parses. Removal after launch.
- Churn: medium to large.
- Dependencies: migration C1 (actor for `check` first arg); supersedes
  migration D1.

**S1. Settings as typed objects.**

- Problem: `MULTISESSION_MODE`, `IDLE_TIMEOUT`, `DEFAULT_HOME` read
  deep in the runtime as global magic constants. Not overridable
  per-account, not validated, not introspectable.
- Target: all engine settings are typed objects with validation,
  introspection, and scoping (global / per-account / per-puppet where
  it makes sense). Plain-constant access deprecated.
- Launch gate: **partial**. Framework exists, all high-traffic
  settings converted, deprecation warnings on the rest. Remaining
  conversions continue post-launch.
- Churn: medium.
- Dependencies: none structural.

### Serving rigor: composable primitives

**M1. Composable `move_to`.**

- Problem: one call does lock check, hook chain, location change,
  contents update, message broadcast, exit traversal. Suppressing any
  one piece means digging through kwargs.
- Target: `Move(actor, dest).skip_messages().with_reason("teleport")
  .execute()`. Old `move_to(...)` becomes thin sugar that delegates.
  All engine-internal callers use the builder form. Kwargs-as-API
  removed.
- Launch gate: **yes** (target). New shape exists, engine-internal
  callers migrated, old kwargs sugar still works but is documented as
  legacy.
- Churn: medium.
- Dependencies: migration B1 (move-related hook contracts).

**Q1. `search` Result type.**

- Problem: `caller.search` returns single / list / None depending on
  flags and matches, and bakes disambiguation prompts into the
  engine.
- Target: `Found | Ambiguous | NotFound` Result type. Pattern-match
  at call site. Disambiguation prompts become a top-layer convenience
  built on Result, not an engine concern.
- Launch gate: **yes** (target). Result type is the API; old return
  shapes available through a sugar method but deprecated.
- Churn: small.
- Dependencies: none.

**H1. Hook registry.** Executable form of migration B1.

- Problem: hook signatures inconsistent. Some return values matter,
  some don't. Some take `**kwargs`, some don't. Some fire on actor,
  some on room, some on both. No registry, no discovery, no signature
  contract. Game devs grep the engine.
- Target: every public hook registered with declared signature,
  return contract, call site. `engine.hooks.for_event("move")` lists
  what fires. Decorator-driven; introspection generates docs.
  Registry-not-decorated hooks rejected at startup.
- Launch gate: **yes** (target). All public hooks registered.
- Churn: medium.
- Dependencies: migration B1.

**C2. CmdSet introspector.** Survival tool for the current cmdset
model; superseded by CM1 when CM1 lands.

- Problem: priority + duplicates + merge type + key collision +
  cmdset stacks is hard to reason about. No built-in "if I type X
  right now, here is exactly which command fires and why."
- Target: `account.explain_cmd("look")` returns which cmdset, what
  priority, which collisions were suppressed, which locks gated it.
- Launch gate: **yes** (target). Small, additive.
- Churn: small.
- Dependencies: none.

**A1. Typed attribute descriptors.**

- Problem: `.db.foo` vs `.attributes.add/get` vs `.ndb.foo` vs
  `.tags` vs `.aliases` vs `.nattributes` vs raw Django fields vs
  `ServerConfig`. Performance and semantics differ, no schema, no
  migrations. Every serious game writes a typed wrapper.
- Target: typed descriptors are the canonical attribute API. `class
  Char: hp = IntAttr(default=100, persist=True, cache="memory")`.
  Engine internals migrate to descriptors. Other storage mechanisms
  remain as sugar but are deprecated for new code; documentation
  steers everyone to descriptors. One canonical answer (P3).
- Launch gate: **partial**. Descriptor framework exists, engine
  internals partially migrated, deprecation guidance documented.
  Full migration continues post-launch.
- Churn: large.
- Dependencies: none structural.

### Level 2 additions

These items were missing from the original architecture doc; without
them the engine is "shippable" but not "really good." Added so the
target is honest.

**CM1. CmdSet rethink.** Supersedes C2 as the endpoint.

- Problem: merge-time-and-cached cmdset model is genuinely hard to
  reason about. C2 makes it debuggable; that's a survival aid, not a
  fix. The model itself has too many footguns (priority, duplicates,
  merge type, key collisions, cache invalidation bugs like `at_sync`).
- Target: simpler model. Either query-time evaluation (no merge step,
  no cache, recompute on each command lookup) or merge-time with
  drastically fewer knobs and explicit contracts. Design open; pick
  during the work.
- Launch gate: **no**. Post-launch arc. C2 is the launch-gate proxy.
- Churn: large.
- Dependencies: migration B1 (hook contracts for cmdset assembly).

**AS1. Sync/async commitment.**

- Problem: Twisted reactor underneath, most game code is sync,
  `defer.inlineCallbacks` is supported-but-not-recommended.
  Long-running hooks stall the reactor. No declared story for I/O
  in hooks. "We'll see" is not a story.
- Target: pick a direction explicitly and ship the supporting
  utilities. Two viable shapes:
  1. **Sync by default, documented**. Commit to sync hooks. Ship
     helpers for the common I/O patterns (defer to thread pool, fire
     and forget, scheduled task). Document where blocking is
     acceptable and where it isn't.
  2. **Async first-class**. `async def` hooks supported, engine
     awaits them, concurrency model documented.
  Either is acceptable; "neither, ambiguously" is not.
  Recommendation: option 1 unless someone has a concrete case for
  option 2.
- Launch gate: **partial**. Direction declared in docs, basic helpers
  for option 1 shipped. Full ergonomics post-launch.
- Churn: medium (option 1); large (option 2).
- Dependencies: none structural.

**W1. Web / client / protocol modernization.**

- Problem: webclient protocol, REST views, input/output handler stack
  are real public API and untouched by everything else here. Each has
  its own conventions that predate the principles in this doc. Some
  string DSLs, some custom hook shapes, some direct-coupling-to-msg
  rather than render/deliver.
- Target: each surface follows the same principles. Typed,
  render/deliver-aware (web client receives RenderNodes, not
  pre-formatted text), hook registry covers their hooks, no string
  DSLs in their interfaces.
- Launch gate: **no**. Post-launch arc; the surfaces work today and
  changing them is bounded modernization, not blocking.
- Churn: medium to large.
- Dependencies: R1 (render output is what web/AI consumers receive);
  H1 (hook registry covers these surfaces).

## Underspire-launch gate

The launch gate is **schedule discipline**, not the architectural
endpoint. It's the subset that must ship before Underspire freezes
the public API. Items can be in the target without being on the
gate; that means they ship after launch as continued polish on top
of a stable substrate.

Must ship by launch (subset of target as noted per item):

- R1 partial (display pipeline seam)
- L1 partial (object form + engine-internal use + string deprecation)
- M1 (composable move with internal migration)
- Q1 (search Result type)
- H1 (hook registry)
- C2 (cmdset introspector)
- A1 partial (descriptor framework + engine consumers + deprecation
  guidance)
- S1 partial (framework + high-traffic settings)
- AS1 partial (direction declared + basic helpers)

Continues after launch toward target:

- R1 full (every output path on render/deliver)
- L1 full (string form removed)
- A1 full (engine internals fully migrated, other mechanisms removed)
- S1 full (all settings converted)
- CM1 (cmdset rethink)
- AS1 full (ergonomics for chosen direction)
- W1 (web/client/protocol modernization)

Post-launch items aren't optional; they're scheduled out. The launch
gate is about what *must* be locked down before the API freeze, not
about what's important.

## What's actually deferred

Items that are not part of this architectural target and won't be
addressed in the Level 2 arc. Sketched in
[`engine-long-horizon.md`](engine-long-horizon.md); summary here so
the tradeoff is named at the right time.

**Typeclass = Django model coupling.** Evennia's signature design
choice; addressing it is a 2.0-scale rewrite. Out of scope for the
pre-Underspire arc and probably out of scope for the 1.x line
entirely. Documented at migration B1 (lifecycle-hook
object-state-at-firing) as the survivable tactical fix; the
structural fix is in the long-horizon sketch.

**Headless engine mode.** Engine usable as a plain library without
reactor / launcher / game directory. Falls out naturally from
typeclass decoupling but worth naming separately. See long-horizon
doc.

**Bottom-layer extraction as a separate package.** The two-layer
thesis says the bottom is a substrate for the top. We're shaping
modules so the bottom is extractable in principle; we aren't
splitting the package. Long-horizon if/when there's demand from a
substrate-only consumer.

Level 2 should not accidentally close off any of these. The
long-horizon doc's "What L2 should preserve" section is the
load-bearing guidance for staying compatible with these directions
without committing to them.

## Sequencing summary

Sequence reflects dependency order and start-as-early-as-possible
principle. Items in the same row can run in parallel.

| Phase | Items | Notes |
|---|---|---|
| Now (alongside migration A) | C2, Q1 | Pure additions, no deps |
| After migration B1 | R1, H1, M1, AS1 (direction) | Need hook contract or independent |
| Alongside migration C1 | L1 | Needs actor abstraction for `check` |
| Alongside migration D | A1, S1 | Independent; start when capacity allows |
| Underspire launch | API freeze | Launch-gate subset must be in |
| Post-launch | R1 full, L1 full, A1 full, S1 full, CM1, AS1 full, W1 | Continued polish toward Level 2 target |

## Cross-references with migration plan

- Migration **B1** (hooks taxonomy doc) is the design predecessor to
  **H1** (hook registry). Doc first, executable form second.
- Migration **C1** (identity model) is the substrate for **L1**'s
  actor argument and **R1**'s viewer argument. Both can start
  scoping before C1 lands but depend on its shape.
- Migration **D1** (permission scope declaration) is superseded by
  **L1**. When picking up D1, build it as the migration path toward
  L1, not a separate intermediate.
- Migration **D2** (multi-puppet shape) is upstream of **R1**'s
  viewer argument when the viewer is a multi-puppet actor.
- Migration **E1** / **E2** (follow/escort, scene broadcast) become
  consumers of R1 once it exists; coordinate timing if both land in
  the same window.
