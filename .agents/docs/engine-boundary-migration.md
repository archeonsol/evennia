# Engine/game boundary migration

Plan for pushing fork-owned infrastructure upstream into Evennia core,
trimming engine code that belongs game-side, and getting the engine
API into a consistent, stable shape. Items are ordered by execution
sequence; per-item design happens when the item is picked up, not
here.

Framing test (see [`FUTURE-IDEAS.md`](../../FUTURE-IDEAS.md)): if a
hypothetical second consumer could not reasonably re-implement this
from scratch, it belongs in the engine. Reference precedent:
`display_name_cache` moved game-side in `underspire.36`; the
`get_display_name` seam it sat on stayed engine-side.

Shipped bundles, rejected items, deferred work, and plugin-future
inhabitants live in
[`engine-boundary-migration-archive.md`](engine-boundary-migration-archive.md).

Engine-wide architectural target (two-layer engine, render/deliver,
lock objects, hook registry, etc.) lives in
[`engine-api-architecture.md`](engine-api-architecture.md). That doc
is gated on Underspire launch as the API-freeze deadline; this doc is
boundary work that feeds into it. Cross-references called out per
item where they apply.

## Language-agnostic position (post-Bundle 2)

Engine stays language-agnostic by *declining to ship defaults*, not by
abstracting language as a strategy protocol. Empty-default hooks
engine-side, opinion downstream. Opinionated language content lives in
a future language plugin. Consequences: drop the `Conjugator` strategy
item; drop pose/emote upstreaming (former Bundle 3 content goes to
plugin-future); `get_numbered_name`'s English pluralization is no
longer "not a violation," it's plugin-future cleanup.

## Execution order

Items are grouped by phase. Within a phase, sub-items can land in
parallel unless a dependency is named. Each item gets full design when
picked up, not here.

### Phase A: near-term polish (no structural dependencies)

Shipped as `+underspire.43`. See the archive entry for what landed.

### Phase B: foundation (gates Phase C)

Pure documentation work, but load-bearing. The act of writing the
contract will surface misshapen hooks early and give the structural
phases a fixed target to honor. Phase C does not start until Phase B
ships.

**B1. Typeclass hooks taxonomy and contract doc.** Write the
typeclass-side equivalent of [`command-system.md`](command-system.md).
Design predecessor to **H1** (hook registry) in
[`engine-api-architecture.md`](engine-api-architecture.md): this doc
defines the contract, H1 makes it executable. Schema choices here
inform H1's decorator surface. At minimum the doc must define:

- Naming taxonomy. Today's `at_*` covers vetoes (`at_pre_move`),
  notifications (`at_post_move`), one-shot mutators
  (`at_object_creation`), and renderers (`return_appearance`) with no
  way to tell which is which from the name. `get_*` covers content
  providers but is also used for state queries. Define which prefix
  means what; flag hooks that violate.
- Calling order. For each lifecycle event (creation, move, puppet,
  unpuppet, delete, appearance, say, etc.) document the exact hook
  sequence, who fires whom, and which hooks can short-circuit the
  chain.
- Return-value contracts. Which hooks veto by returning False, which
  return content, which are pure side-effect.
- Override discipline. Which hooks are designed for game-side
  override; which are engine-internal and overriding them is
  unsupported.
- Object-state-at-firing. For each lifecycle hook
  (`at_init`, `at_object_creation`, `at_first_save`, etc.) document
  whether the object is freshly constructed, loaded from cache,
  persisted, or mid-transaction. This is the load-bearing part of
  resolving typeclass/model coupling ambiguity; the rest of that
  coupling is either an Evennia feature or a test-tooling problem,
  not a boundary issue.
- Misshapen hooks list. As a side effect of writing the doc, the
  hooks that don't fit the taxonomy get flagged for Phase C or
  earlier cleanup.

### Phase C: keystone structural change

Single item, but the largest move in the plan. Everything in Phase D
is downstream of this and should not start until it lands.

**C1. Unified actor/context abstraction.** Working name deliberately
ambiguous; pick during design. Goal: one object answering "who is
acting, on what, with what authority," unifying session, account,
puppet, and effective permissions. Substrate for **L1** (actor
argument to `check`) and **R1** (viewer argument to `render`) in
[`engine-api-architecture.md`](engine-api-architecture.md); both can
start scoping before C1 lands but depend on its shape. Once landed:

- `self.caller` ambiguity in commands collapses (the object always
  exposes session/account/puppet explicitly).
- `AccountCommand` split can be retired or reframed as a routing
  hint.
- Permission scope (Phase D) has a place to live.
- Multi-puppet shape (Phase D) has a place to live.

Design questions to resolve when picked up: is this a wrapper around
the existing trio or a replacement, what does the migration story
look like for existing game code, does it touch the cmdset merge or
just the command entry point.

### Phase D: downstream of identity

Both items presuppose Phase C. Old Bundle 3 and Bundle 4 items are
absorbed and reframed here; their original scope is preserved in the
archive.

**D1. Permission scope declaration.** Supersedes old Bundle 3 (quell-
aware permstring helper + `check_permstring` scope resolver). Perms
declare account-scoped, character-scoped, or both at definition time;
quell behavior falls out automatically from the scope declaration
rather than being computed per callsite. Old Bundle 3's helper and
resolver become migration tactics on the way to this, not the
endpoint. Superseded in turn by **L1** in
[`engine-api-architecture.md`](engine-api-architecture.md) (lock
objects + permission algebra); when picking up D1, build it as the
migration path toward L1 rather than a separate intermediate.

**D2. Multi-puppet first-class shape.** Supersedes old Bundle 4's
multi-puppet relay item. Slot primitives (P1/P2/P3 in the fork) and
session relay become engine concepts rather than game-side
workarounds. The fork's relay reads only puppet markers set at slot
assignment, so it survives this cleanly. Death/incapacitation gates
stay game-side behind try/import; the engine ships the policy
*shape*, not the policy *content*.

### Phase E: independent of identity, can land any time after Phase B

Both items have no Phase C dependency. They can run in parallel with
Phase C/D once the hooks contract (B1) is written.

**E1. Follow / escort / shadow commands.** Move upstream as default
commands. Blocked on shipped move primitives (Bundle 1).
Mover-side invariant: mover is in destination before followers are
scheduled. No cross-room ordering needed.

**E2. Scene / IC broadcast helpers.** Blocked on shipped appearance
primitives (Bundle 2) and a new `room_ic_viewers` typeclass hook.
Batch the hook with Bundle 1-style work if possible.

## Sequencing summary

| Release | Phase | Items |
|---|---|---|
| `.40` | shipped | Bundle 1 (items 1, 2, 3, 4) |
| `.41` | shipped | Bundle 1.5 universal veto/transform rule |
| `.42` | shipped | Bundle 2 (items 5, 6, 7) |
| `.43` | shipped | Phase A (A1 language polish + A2 flat API hygiene + A3 `bump_*_generation` doc + A4 `at_sync` reattach hooks) |
| `.44+` | B | B1 hooks taxonomy + contract doc (gates C) |
| `.45+` | C | C1 identity model (gates D) |
| later | D | D1 permission scope declaration |
| later | D | D2 multi-puppet first-class shape |
| later | E | E1 follow / escort / shadow |
| later | E | E2 scene / IC broadcast helpers |

Hardest push from the downstream side: B1 (it forces the contract to
be written down), C1 (largest structural move), and D1/D2 (touch the
most fork code, closest analogs to the `display_name_cache` move).
