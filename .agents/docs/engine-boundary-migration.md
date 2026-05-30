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

### Phase B: foundation (typeclass hooks taxonomy)

Pure documentation work, but load-bearing. The act of writing the
contract will surface misshapen hooks early and give later items
(both in this doc and in the architecture doc) a fixed target to
honor.

**B1. Typeclass hooks taxonomy and contract doc.** Shipped. See
[`Typeclass-Hooks.md`](../../docs/source/Components/Typeclass-Hooks.md) for taxonomy, calling
order per lifecycle event, and the misshapen-hooks list;
[`Typeclass-Hooks-Reference.md`](../../docs/source/Components/Typeclass-Hooks-Reference.md) for
return-value contracts, override discipline, and
object-state-at-firing tables. Design predecessor to **H1** (hook
registry) in
[`engine-api-architecture.md`](engine-api-architecture.md): the
typeclass doc defines the contract, H1 makes it executable. The
misshapen-hooks list in §6 of the typeclass doc is the Phase C / H1
cleanup input.

Original scope (preserved for archive):

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
  hooks that don't fit the taxonomy get flagged for cleanup before
  they get registered via H1 (the architecture doc's hook registry).

### Phase C: upstream consumer-facing items

Boundary items that take fork-owned commands and helpers and move
them upstream as default engine surface. Both consume substrate from
the architecture doc; coordinate timing with whatever's ahead of them
there.

**C1. Follow / escort / shadow commands.** Move upstream as default
commands. Blocked on shipped move primitives (Bundle 1, shipped).
Mover-side invariant: mover is in destination before followers are
scheduled. No cross-room ordering needed. Coordinate with **M1**
(composable move) in the architecture doc; ideally these commands
target the new Move builder rather than the legacy `move_to`
signature.

**C2. Scene / IC broadcast helpers.** Blocked on shipped appearance
primitives (Bundle 2, shipped) and a new `room_ic_viewers` typeclass
hook. Coordinate with **R1** (display pipeline) in the architecture
doc; ideally these helpers consume RenderNode rather than reaching
into pre-formatted text.

## Sequencing summary

| Release | Phase | Items |
|---|---|---|
| `.40` | shipped | Bundle 1 (items 1, 2, 3, 4) |
| `.41` | shipped | Bundle 1.5 universal veto/transform rule |
| `.42` | shipped | Bundle 2 (items 5, 6, 7) |
| `.43` | shipped | Phase A (A1 language polish + A2 flat API hygiene + A3 `bump_*_generation` doc + A4 `at_sync` reattach hooks) |
| `.44+` | shipped | B1 hooks taxonomy + contract doc |
| later | C | C1 follow / escort / shadow |
| later | C | C2 scene / IC broadcast helpers |

Items previously listed here as Phase C (identity model), Phase D1
(permission scope), and Phase D2 (multi-puppet shape) have been
moved to [`engine-api-architecture.md`](engine-api-architecture.md)
as **I1**, **L1** (which subsumed D1), and **I2** respectively. They
are substrate work, not boundary work. Original scope preserved in
the archive.
