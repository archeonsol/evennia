# Engine long-horizon sketch

Speculative shape of where the engine could go after Level 2 (the
[`engine-api-architecture.md`](engine-api-architecture.md) target)
lands. This doc is **sketch-level, not committed**. It exists so
Level 2 decisions don't accidentally close off Level 3 paths, not as
a plan to execute.

If you find yourself wanting to put churn estimates, launch gates, or
dependencies in this doc, stop. Those belong in the architecture doc
once an item moves from speculative to scoped. This doc is for shape
only.

## What's in scope here

The items below are concrete enough to describe but require a major
version commitment to implement: breaking changes to every game ever
written on Evennia, migration tooling, a 2.0-scale arc. None of them
fit inside the Level 2 target and none of them should be attempted
before Underspire ships.

What's **not** here: reactive / event-sourced engine, out-of-process
services architecture, multi-game-server clustering. Those are
"what if Evennia were a different framework" thoughts. Not useful as
direction.

## Sketch items

### Typeclass decoupled from Django model

The headline item, and the only one we currently understand well
enough to describe in any detail.

Today: `DefaultObject` inherits from `ObjectDB` (a Django model) via
`TypedObject`. Typeclass *is* the model; you cannot have one without
the other. Every typeclass instance is a DB row; in-memory typeclass
instances aren't a thing; testing behavior without the ORM requires
DB setup or careful mocks.

Shape: composition instead of inheritance. `Character` has-a
`ObjectDB` rather than is-a. Persistence is a separable concern;
behavior lives on the typeclass; the model is what the typeclass
delegates to for storage. In-memory typeclass instances become valid;
test code can construct a Character without a DB; alternative
persistence backends become conceivable.

Cost: every game ever written on Evennia breaks. Migration tooling
required (scripts that walk source code and rewrite inheritance to
composition). Probably 6-12 months of focused work. Cannot ship
without a major version bump.

Payoff: testing tractable, lifecycle ambiguity resolved (an
in-memory typeclass instance isn't "persisted" until you persist it,
which makes the question well-formed), engine internals separable
from Django.

### Headless engine mode

Today: starting Evennia requires a Twisted reactor, a Django settings
module, a game directory, and a launcher. The engine cannot be
imported as a plain library and used in a REPL or test without all
of that scaffolding.

Shape: the engine should be runnable as a library. `import evennia;
evennia.standalone()` (or similar) gives you a working engine with
no reactor, no launcher, no game directory required. Useful for:
testing (real engine objects without integration overhead), embedding
(Evennia inside another application), REPL exploration (poke at the
engine to learn it), alternative drivers (something other than
Twisted on the network side).

Cost: most modules that assume "the reactor exists" or "settings is
configured globally" need to grow a "neither, fall back to defaults"
path. Probably falls out naturally from typeclass decoupling because
both touch the same coupling.

Payoff: the engine becomes a Python library that happens to ship with
a MUD driver, rather than a MUD driver that happens to be in Python.
Mental-model shift that pays off for everyone who's not running a
traditional always-on MUD server.

### Bottom layer extracted as a separate package

Today: the two-layer thesis (architecture doc) says the bottom layer
is a substrate for the top. They live in one package. Game devs who
want only the substrate can't get it without the sugar.

Shape: `evennia-core` ships the typed substrate (render pipeline,
lock objects, hook registry, result types, permission algebra,
composable move, typed attributes, identity model). `evennia` (the
current package) depends on it and adds the friendly sugar. Games
that don't want the sugar import `evennia-core` directly.

Cost: package split is mostly mechanical if the seams are clean.
Distribution and versioning gets more complex. Probably not worth
doing until there's demand from at least one consumer who wants the
substrate without the sugar.

Payoff: the substrate becomes a genuine Python library that's useful
outside the MUD context. Alternative high-level layers become
possible (e.g. a different friendly API for non-MUD multiplayer
text apps).

## Thought exercises (more speculative)

The items above are concrete enough to describe and have clear
motivations. The items below are real architectural directions
Evennia *could* go but we don't currently understand them well
enough to say whether they're good ideas, what they'd cost, or what
problems they'd actually solve. Captured here so the thought isn't
lost when it comes up later, not as direction.

If you're tempted to flesh any of these out, ask first: is the
motivation real today, or are we designing for hypothetical demand?
Real motivation moves an item up to the sketch section above.
Hypothetical demand keeps it here.

**Reactive / event-sourced engine.** Every state change is an event;
world state is the fold of events. This is the "natural" answer to a
cluster of problems: multi-puppet (events are per-actor, no shared
mutable state), undo, replay, time-travel debugging, server-side
recording for AI training, audit logs. In tension with the current
"object has state, attributes mutate in place" model; would require
a fundamentally different mental model for game devs. Motivation
becomes real if AI training or replay debugging become first-class
concerns, or if the multi-puppet shape stays awkward despite the
Level 2 work.

**Out-of-process services architecture.** Long-running computation
(AI inference, complex simulation, procedural world generation)
shouldn't block the reactor. Today the only answer is "spin up a
deferred + thread pool" or "schedule a task." A real services story
would have an AI service, a simulation service, a chat service, each
independently scalable, each with its own deployment lifecycle.
Motivation becomes real when game devs hit the "this calculation
takes 5 seconds and freezes everyone" wall in production; the sync/
async commitment in Level 2 (AS1) addresses the symptom but not the
underlying single-process assumption.

**Multi-server / clustering.** Evennia is fundamentally single-process
today. Sharding by area, by account, or by region would require the
engine to think about distributed identity, cross-shard messaging,
consistent state under partition. Motivation becomes real if anyone
runs into the single-process ceiling (probably tens of thousands of
concurrent players, generously). For most games this never matters;
for a hypothetical Evennia-powered persistent shared world it would
be the gating constraint.

## What L2 should preserve to keep L3 reachable

This is the load-bearing section. The architectural decisions in
Level 2 should not accidentally close off Level 3 paths. Concretely:

**Avoid hard-coding `ObjectDB` as the parent class in new substrate
code.** Where Level 2 adds typed primitives (Lock objects,
RenderNode, Move builder, Result type, hook registry), make sure
those primitives don't inherit from Django models or assume Django
ORM access at construction time. They should be plain Python types
that *operate on* objects, not *be* model objects. This keeps the
typeclass-decoupling path open without committing to it.

**Treat the actor abstraction (migration C1) as the substrate's
identity primitive.** When Level 3 decouples typeclass from model,
the actor abstraction is what survives the transition. Permissions,
render viewers, hook signatures should all key off the actor concept,
not directly off `AccountDB` / `ObjectDB` instances. If actor is a
clean abstraction over the trio today, it stays a clean abstraction
over a decoupled trio tomorrow.

**Keep the hook registry (H1) free of typeclass-model assumptions.**
Hooks should register with their signatures, not with assumptions
about which class they live on. A hook that fires "when an object
moves" registers as that; whether the object is a Django-model-backed
typeclass or an in-memory typeclass is the registry's problem to
handle, not the hook author's. This costs a little discipline at
registration time and pays off when the typeclass-model coupling
loosens.

**Keep render output structured, not pre-formatted, all the way
through.** Level 2 says every output path goes through render. Level
3 wants headless mode where render output goes to a test harness, an
AI summarizer, an accessibility layer, or a web client. If render
output is structured (RenderNode trees) all the way to the delivery
boundary, headless mode is straightforward. If render output gets
flattened to strings in the middle of the pipeline somewhere, headless
mode is a partial rewrite.

**Don't let any new attribute primitive assume Django storage.** A1
in the architecture doc revisits the attribute storage model;
whatever it introduces (typed descriptors, a component bag, or
nothing) must keep storage backend-agnostic. Any primitive should
pick a backend at construction time, with Django-backed storage being
one option among several (in-memory, JSON file, custom). Even if no
other backend is implemented in Level 2, the interface should be
backend-agnostic so Level 3 (and an eventual ECS-style store) can add
others without a redesign.

**If a settings-layer rework is ever taken on, it should support
non-Django config sources.** S1 (typed settings objects) was dropped
as not worth the churn, but the principle still applies to any
future revisit: a headless mode that loads settings from a dict or a
TOML file should be possible without rewriting the settings layer.

The summary: Level 2 should design typed primitives that *operate on*
the existing typeclass+Django substrate without *being part of* it.
That single discipline keeps Level 3 reachable. Violating it is fine
where the cost would be too high; the doc just exists to make the
tradeoff visible at the time the decision is made, not after.

## Cross-references

- [`engine-api-architecture.md`](engine-api-architecture.md) defines
  Level 2 (the committed architectural target). This doc is the
  sketch of what could come after.
- [`engine-boundary-migration.md`](engine-boundary-migration.md)
  defines the migration items feeding into Level 2. Migration B1's
  lifecycle-hook documentation is the survivable tactical fix for
  typeclass-model coupling; the structural fix is the typeclass
  decoupling item above.
