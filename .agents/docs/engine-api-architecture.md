# Engine API architecture

Architectural target for the engine API. Companion to
[`engine-boundary-migration.md`](engine-boundary-migration.md):
migration is fork/engine boundary work, this doc is engine
architecture work.

## API stability and milestones

The real API-stability milestone is **Level 2 complete**, not
Underspire launch. Level 2 is the work catalogued in this doc.

Underspire-the-game launches partway through this arc. That launch is
a release milestone for the game, not an API freeze for the engine.
The engine API is in motion across the entire Level 2 arc; breaking
changes are expected between Underspire launch and Level 2
completion. Game devs using Evennia in that window know they're in
pre-stable territory.

This framing means item sequencing matters (dependencies + bandwidth)
but item *gating* mostly doesn't. We aren't trying to lock everything
down by a specific calendar date; we're trying to land Level 2 in a
sensible order. The "Sequencing and milestones" section at the bottom
spells out the order.

Items here are *where we are going*; we move toward them as early as
possible rather than building on patterns we already know we'll
replace.

## Thesis: two-layer engine

The engine collapses two layers today:

- **Bottom layer**: small, sharp, typed primitives. Display pipeline,
  lock objects, hook registry, result types, permission algebra,
  composable move, typed attributes, committed concurrency story,
  unified actor abstraction. No friendliness magic, no implicit
  conversions, no string DSLs in hot paths. What serious games
  target.
- **Top layer**: friendly API. `caller.msg("hi")`, `at_say` defaults,
  `.db.foo = 5`. What tutorials and starter games use.

Today these are one layer, so friendly-API conveniences leak into
anything trying to be rigorous, and rigorous concerns (viewer-aware
rendering, schema, validation, concurrency, identity) have no home
that isn't game-side reinvention. The architectural target is to
separate them: bottom layer becomes the substrate, top layer is
reimplemented on top as sugar.

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
Three identity concepts that overlap (session, account, puppet) is
the anti-pattern. Sugar layers exist for ergonomics; they delegate to
one canonical substrate, they aren't parallel implementations.

## Items

Each item lists: the problem, the **target** (what "complete" means),
churn, and dependencies. Per-item design happens when picked up.

Items are ordered approximately by dependency. Independent items come
first; items that consume substrate from earlier items come later.

### I1. Actor abstraction

Substrate primitive; precedes L1, R1, I2. Migrated here from the
boundary-migration doc (was Phase C1) because it's substrate, not
boundary.

- Problem: `self.caller` in a command can be a Session, Account, or
  Object depending on cmdset routing. The session-proxy /
  `AccountCommand` machinery papers over this but exposes the
  underlying ambiguity to game code. There's no single object
  answering "who is acting, on what, with what authority."
- Target: one object (working name deliberately ambiguous; pick
  during design) unifying session, account, puppet, and effective
  permissions. Once landed: `self.caller` ambiguity collapses,
  `AccountCommand` split retires or reframes as a routing hint, L1's
  `check` first arg has a place to live, R1's viewer arg has a place
  to live, I2's multi-puppet machinery has a place to live.
- Design questions to resolve when picked up: wrapper around the
  existing trio or a replacement, what does the migration story look
  like for existing game code, does it touch cmdset merge or just
  the command entry point.
- Churn: medium to large (substrate change; many call sites touch
  identity).
- Dependencies: none structural; benefits from B1 (hook signatures
  reference actor cleanly).

### Q1. `search` Result type

- Problem: `caller.search` returns single / list / None depending on
  flags and matches, and bakes disambiguation prompts into the
  engine.
- Target: `Found | Ambiguous | NotFound` Result type. Pattern-match
  at call site. Disambiguation prompts become a top-layer convenience
  built on Result, not an engine concern. Old return shapes available
  through a sugar method but deprecated.
- Churn: small.
- Dependencies: none.

### C2. CmdSet introspector

Survival tool for the current cmdset model; superseded by CM1 as the
endpoint.

- Problem: priority + duplicates + merge type + key collision +
  cmdset stacks is hard to reason about. No built-in "if I type X
  right now, here is exactly which command fires and why."
- Target: `account.explain_cmd("look")` returns which cmdset, what
  priority, which collisions were suppressed, which locks gated it.
- Churn: small.
- Dependencies: none.

### A1. Typed attribute descriptors

- Problem: `.db.foo` vs `.attributes.add/get` vs `.ndb.foo` vs
  `.tags` vs `.aliases` vs `.nattributes` vs raw Django fields vs
  `ServerConfig`. Performance and semantics differ, no schema, no
  migrations. Every serious game writes a typed wrapper.
- Target: typed descriptors are the canonical attribute API. `class
  Char: hp = IntAttr(default=100, persist=True, cache="memory")`.
  Engine internals migrate to descriptors. Other storage mechanisms
  remain as sugar but are deprecated for new code; documentation
  steers everyone to descriptors. One canonical answer (P3).
- Churn: large.
- Dependencies: none structural.

### S1. Settings as typed objects

- Problem: `MULTISESSION_MODE`, `IDLE_TIMEOUT`, `DEFAULT_HOME` read
  deep in the runtime as global magic constants. Not overridable
  per-account, not validated, not introspectable.
- Target: all engine settings are typed objects with validation,
  introspection, and scoping (global / per-account / per-puppet where
  it makes sense). Plain-constant access deprecated.
- Churn: medium.
- Dependencies: none structural.

### AS1. Sync/async commitment

- Problem: Twisted reactor underneath, most game code is sync,
  `defer.inlineCallbacks` is supported-but-not-recommended.
  Long-running hooks stall the reactor. No declared story for I/O in
  hooks. "We'll see" is not a story.
- Target: pick a direction explicitly and ship the supporting
  utilities. Two viable shapes:
  1. **Sync by default, documented**. Commit to sync hooks. Ship
     helpers for the common I/O patterns (defer to thread pool, fire
     and forget, scheduled task). Document where blocking is
     acceptable and where it isn't.
  2. **Async first-class**. `async def` hooks supported, engine
     awaits them, concurrency model documented.
  Either is acceptable; "neither, ambiguously" is not. Recommendation:
  option 1 unless someone has a concrete case for option 2.
- Churn: medium (option 1); large (option 2).
- Dependencies: none structural.

### H1. Hook registry

Executable form of migration B1. Design locked after B1 shipped; this
entry is the spec future-us implements against.

- Problem: hook signatures inconsistent. Some return values matter,
  some don't. Some take `**kwargs`, some don't. Some fire on actor,
  some on room, some on both. No registry, no discovery, no signature
  contract. Game devs grep the engine.
- Target: every public engine hook declared with `@hook(...)`. Startup
  walks `evennia/*` typeclasses, lints that every `at_*` / `get_*` /
  `return_*` method on registered base classes has the decorator (or
  inherits a registered one), and rejects unregistered ones. Warn-only
  during rollout, hard-fail once complete.
- Churn: medium (~108 decorators + registry/lint plumbing + doc
  generator).
- Dependencies: B1 (shipped) for the taxonomy that informs the
  schema.

**Enforcement scope.** Engine-only. Game-side overrides inherit
registration silently. Game-side novel hooks (e.g. game-specific
`at_buff_applied`) are not required to register. Lookup mirrors
enforcement: `describe(method)` reads the `__evennia_hook__` attribute
directly. Game-side overrides that redefine the method without `@hook`
return `None`; the inherited spec is recoverable by inspecting the
parent class explicitly.

**Dispatch mode.** Direct. The engine still calls
`obj.at_pre_move(...)` etc. The registry is descriptive + validating
metadata, not a dispatcher. Indirect `hooks.fire(...)` is explicitly
rejected; that would be a much bigger rewrite.

**Schema.** The `@hook` decorator carries:

```python
@hook(
    event="puppet",            # canonical event family
    phase="pre",               # pre | post | composite | failed
    actor="target",            # self | mover | source | destination | target | ...
    returns="veto",            # veto | transform | content | ignored | conditional
    discipline="public",       # public | internal | mixed
    fires_from=("DefaultAccount.puppet_object",
                "ServerSession.at_sync"),
    # structured state-at-firing fields, all optional:
    state_pk=True,
    state_db_row=True,
    state_init_done=True,
    state_location_set=None,
    state_cache_state="rehydrated",  # fresh | rehydrated | n/a
    state_mid_transaction=False,
    notes="Reattach path fires with reattach=True kwarg.",
)
def at_pre_puppet(self, account, session=None, **kwargs): ...
```

`fires_from` uses `Class.method` strings (cheap, resolvable at import).
Lint verifies each string resolves to a real callable.

Override registration is silent inheritance by default. Overrides may
optionally use `@hook(extends="LifecycleMixin.at_pre_puppet",
notes="...")` to declare a deltas-only registration when the override
has behavior worth recording (e.g. reattach suppression on
`Character.at_post_puppet`).

**API surface** (flat-API export at `evennia.hooks`):

- `hooks.for_event(event_name) -> list[HookSpec]`
- `hooks.describe(class_or_method) -> HookSpec | None`
- `hooks.list_all() -> list[HookSpec]`
- `hooks.lint() -> list[LintFinding]`
- `hooks.generate_docs(format="markdown") -> str`

**Sub-phases.**

- **H1a.** Schema + decorator infrastructure. New `evennia/hooks/`
  package: `__init__.py`, `registry.py`, `specs.py`. `HookSpec`
  dataclass. `@hook(...)` decorator attaching `__evennia_hook__` and
  registering in a module-level dict. Flat-API export. Apply to one
  hook end-to-end (e.g. `LifecycleMixin.at_pre_puppet`) to validate
  the schema. Tests for registration, lookup, invariants.
- **H1b.** Lint module + startup hook. `hooks.lint()` walks every
  subclass of registered base classes, finds `at_*` / `get_*` /
  `return_*` methods, checks each has `__evennia_hook__` directly or
  inherited. Validates `fires_from` paths resolve, `phase` matches
  name pattern, default body return type matches declared `returns`.
  Wired into server startup, warn-only during H1c rollout.
- **H1c.** Register every engine hook, class by class. One commit per
  typeclass (or small cluster). Order: simplest first
  (`DefaultScript`, `DefaultChannel`), then `DefaultObject` +
  `AppearanceMixin`, then `DefaultAccount`, finally `ServerSession`.
  Source of truth: B1 enumeration in `Typeclass-Hooks.md`.
- **H1d.** Doc generator. `evennia.hooks.generate_docs()` emits
  markdown for the §3 return-contract tables, §4 override-discipline
  tables, §5 state-at-firing tables, and event-grouped calling-order
  summaries. Published docs (`Typeclass-Hooks.md`,
  `Typeclass-Hooks-Reference.md`) get `<!-- generated:start -->` /
  `<!-- generated:end -->` markers around those tables. §6 misshapen
  list becomes lint output instead of hand-curated. §1 taxonomy, §2
  prose, §7 triage history stay hand-written.
- **H1e.** H-bucket forced decisions (§7 of `Typeclass-Hooks.md`).
  Resolved as side effects of H1c registration:
  - `at_desc`: register as `event="look", phase="composite",
    actor="target"`. Misleading name stays; registry tells you when
    it fires.
  - `return_appearance`: register as `event="look",
    phase="composite", returns="content"`. The `return_*` prefix
    loses meaning once registry is the source of truth.
  - Post-creation asymmetry: `DefaultAccount` / `DefaultChannel` /
    `DefaultScript` get stub `at_*_post_creation` methods so
    registration is symmetric. Three empty methods.
  - `at_look` overload: register as different events.
    `Object.at_look` is `event="look"`. `Account.at_look` becomes
    `event="ooc_look"`. Forces the distinction.
  - `get_return_exit` (D-bucket reclassified to H): register as
    `event="exit_pair", phase="composite", returns="content",
    discipline="public"`. It has tests; killing it later is a
    separate decision.

**Out of scope for H1.**

- `appearance_template` slot ↔ provider coupling. Separate
  config-registry problem, deferred (see `FUTURE-IDEAS.md`).
- Cmdset hooks. Already documented in `command-system.md`; stay
  there.
- Web / protocol hooks. W1, post-launch.

### M1. Composable `move_to`

- Problem: one call does lock check, hook chain, location change,
  contents update, message broadcast, exit traversal. Suppressing
  any one piece means digging through kwargs.
- Target: `Move(actor, dest).skip_messages().with_reason("teleport")
  .execute()`. Old `move_to(...)` becomes thin sugar that delegates.
  All engine-internal callers use the builder form. Kwargs-as-API
  removed.
- Churn: medium.
- Dependencies: migration B1 (move-related hook contracts).

### L1. Lock objects + permission algebra

Subsumes migration D1 (which has been folded into this item).

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
- Churn: medium to large.
- Dependencies: **I1** (actor for `check` first arg).

### I2. Multi-puppet first-class shape

Migrated here from the boundary-migration doc (was Phase D2) because
it's substrate, not boundary.

- Problem: engine treats multi-puppet as an edge case of
  single-puppet. Session relay, P1/P2/P3 slots, broadcast-to-all-
  my-puppets all live game-side. No engine concept of "this account
  drives several puppets simultaneously" as a first-class shape.
- Target: slot primitives and session relay become engine concepts,
  not game-side workarounds. The fork's relay reads only puppet
  markers set at slot assignment, so it survives this cleanly.
  Death/incapacitation gates stay game-side behind try/import; engine
  ships the policy *shape*, not the policy *content*.
- Churn: medium to large.
- Dependencies: **I1** (multi-puppet machinery hangs off actor).

### R1. Display pipeline

The keystone.

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
- Churn: large.
- Dependencies: migration B1 (hook taxonomy documents the render-side
  hooks); **I1** (actor-as-viewer for the `viewer` argument).

### CM1. CmdSet rethink

Supersedes C2 as the endpoint.

- Problem: merge-time-and-cached cmdset model is genuinely hard to
  reason about. C2 makes it debuggable; that's a survival aid, not
  a fix. The model itself has too many footguns (priority,
  duplicates, merge type, key collisions, cache invalidation bugs
  like `at_sync`).
- Target: simpler model. Either query-time evaluation (no merge step,
  no cache, recompute on each command lookup) or merge-time with
  drastically fewer knobs and explicit contracts. Design open; pick
  during the work.
- Churn: large.
- Dependencies: migration B1 (hook contracts for cmdset assembly).

### W1. Web / client / protocol modernization

- Problem: webclient protocol, REST views, input/output handler stack
  are real public API and untouched by everything else here. Each has
  its own conventions that predate the principles in this doc. Some
  string DSLs, some custom hook shapes, some direct-coupling-to-msg
  rather than render/deliver.
- Target: each surface follows the same principles. Typed,
  render/deliver-aware (web client receives RenderNodes, not
  pre-formatted text), hook registry covers their hooks, no string
  DSLs in their interfaces.
- Churn: medium to large.
- Dependencies: R1 (render output is what web/AI consumers receive);
  H1 (hook registry covers these surfaces).

## What's actually deferred

Items that are not part of this architectural target and won't be
addressed in the Level 2 arc. Sketched in
[`engine-long-horizon.md`](engine-long-horizon.md); summary here so
the tradeoff is named at the right time.

**Typeclass = Django model coupling.** Evennia's signature design
choice; addressing it is a 2.0-scale rewrite. Out of scope for Level
2 and probably out of scope for the 1.x line entirely. Documented at
migration B1 (lifecycle-hook object-state-at-firing) as the
survivable tactical fix; the structural fix is in the long-horizon
sketch.

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

## Sequencing and milestones

Sequence reflects dependency order and start-as-early-as-possible
principle. Items in the same row can run in parallel.

| Order | Items | Notes |
|---|---|---|
| Start now (alongside migration A shipped) | I1, Q1, C2, A1, S1, AS1 | No dependencies; start when capacity allows |
| After migration B1 (hooks taxonomy doc) | H1, M1 | Need hook contract |
| After I1 | L1, I2 | Need actor substrate |
| After B1 + I1 | R1 | Needs both hook contract and actor-as-viewer |
| After B1 | CM1 | Large; sequence after substrate items where possible |
| After R1 + H1 | W1 | Consumes render output and hook registry |

Underspire-the-game launches partway through this arc. For
Underspire's own polish we'd prefer the substrate items (I1, the R1
seam, L1 objects, M1, Q1, basic H1) done by then. That's a
preference, not a freeze; missing items don't block the game's launch
and don't lock in engine API shape.

The real API-stability milestone is the entire arc complete (Level 2
done). Items not done by Underspire launch ship between then and
Level 2 done; breaking changes in that window are expected.

## Cross-references with migration plan

- Migration **B1** (hooks taxonomy doc) is the design predecessor to
  **H1** (hook registry). Doc first, executable form second.
- Migration **E1** / **E2** (follow/escort, scene broadcast) become
  consumers of R1 once it exists; coordinate timing if both land in
  the same window.

Former migration items **C1**, **D1**, **D2** were not actually
boundary work; they were substrate. Moved here as **I1** (actor),
**L1** (which subsumed D1), and **I2** (multi-puppet).
