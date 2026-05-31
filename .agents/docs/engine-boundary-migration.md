# Engine/game boundary migration

Plan for pushing fork-owned commands and helpers upstream into Evennia
core. Boundary work distinct from substrate work (which lives in
[`engine-api-architecture.md`](engine-api-architecture.md)).

Framing test (see [`FUTURE-IDEAS.md`](../../FUTURE-IDEAS.md)): if a
hypothetical second consumer could not reasonably re-implement this
from scratch, it belongs in the engine. Reference precedent:
`display_name_cache` moved game-side in `underspire.36`; the
`get_display_name` seam it sat on stayed engine-side.

Settled policy: the engine stays language-agnostic by *declining to
ship defaults*, not by abstracting language as a strategy protocol.
Empty-default hooks engine-side; opinion downstream. English-specific
content (`get_numbered_name` pluralization, hardcoded strings) is
opportunistic cleanup as it's touched, not a separate audit.

History (shipped bundles, rejected items, deferred work, plugin-future
inhabitants) lives in
[`engine-boundary-migration-archive.md`](engine-boundary-migration-archive.md).

## Remaining items

Both items are downstream of architecture-doc substrate. They can
ship before that substrate by targeting the legacy API, but it's
better to wait so they consume the new shape directly and don't need
a follow-up migration.

**C1. Follow / escort / shadow commands.** Move upstream as default
commands. Mover-side invariant: mover is in destination before
followers are scheduled. No cross-room ordering needed. **Depends on
M1** (composable move) in the architecture doc; these commands
should target the new `Move(...)` builder rather than the legacy
`move_to` signature.

**C2. Scene / IC broadcast helpers.** Plus a new `room_ic_viewers`
typeclass hook. **Depends on R1** (display pipeline) in the
architecture doc; these helpers should consume `RenderNode` rather
than reaching into pre-formatted text.

## Shipped

- **Bundle 1** (`+underspire.40`) — items 1-4.
- **Bundle 1.5** (`+underspire.41`) — universal veto/transform rule
  for pre-hooks.
- **Bundle 2** (`+underspire.42`) — items 5-7 (empty-template
  `at_say`, content-group label hook, cmdset merge warmup).
- **Phase A** (`+underspire.43`) — PA1 language polish, PA2 flat API
  hygiene, PA3 `bump_*_generation` invalidation contract, PA4
  `at_sync` reattach hooks.
- **Phase B / B1** (shipped alongside `+underspire.44`) — typeclass
  hooks taxonomy and contract doc. See
  [`Typeclass-Hooks.md`](../../docs/source/Components/Typeclass-Hooks.md)
  and
  [`Typeclass-Hooks-Reference.md`](../../docs/source/Components/Typeclass-Hooks-Reference.md).
  Design predecessor to H1 in the architecture doc.

Original scope for each phase is preserved in the archive.

## Items reclassified

Originally on this plan as Phase C (identity model), Phase D1
(permission scope), and Phase D2 (multi-puppet shape). Reclassified
as substrate (not boundary) work; moved to
[`engine-api-architecture.md`](engine-api-architecture.md) as **I1**
(actor abstraction), **L1** (lock objects + permission algebra,
which subsumed D1), and **I2** (multi-puppet first-class shape).
Original scope preserved in the archive.
