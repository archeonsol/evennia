# Engine metrics / observability surface — re-decide

Status: todo (discussion-first; may be dropped)

## Context

A parked question from the now-retired boundary-migration plan: **should the
engine expose an observability/metrics surface at all?** It was deferred as an
open question. Since then, concrete metrics code has crept into the tree without a
deliberate decision:

- `evennia/typeclasses/attributes.py` calls `record_attribute_flush_stats(...)`
  (wrapped in a bare `except: pass` — see the
  [shim/except cleanup prompt](ALPHA-shim-except-cleanup.md)).
- An `attribute_metrics` module backs it.

So the engine already has a half-formed, swallowed metrics path. That's the worst
of both: a surface exists but nobody decided to ship it, and its errors are
hidden.

## The decision to make

Pick one, deliberately:

1. **Drop it.** Metrics are game/ops concern; remove `attribute_metrics` and the
   `record_attribute_flush_stats` call rather than carry an undecided surface.
2. **Commit to a minimal surface.** Decide what the engine measures (attribute
   flushes? cache hit/miss? dispatch latency?), expose it through one documented
   primitive, and stop swallowing its errors. Apply the framing test
   ([`core-beliefs.md`](../docs/core-beliefs.md)): would a second consumer need
   the engine to provide this, or can a game add it?

"An accidental metrics path with `except: pass`" is not an acceptable resting
state for alpha — resolve to (1) or (2).

## Approach

1. Inventory what metrics code already exists (`attribute_metrics`,
   `record_attribute_flush_stats`, any cache counters). Trace consumers.
2. Apply the framing test; recommend drop-vs-commit with rationale.
3. Surface the recommendation before doing either. Coordinate with the
   [shim/except cleanup](ALPHA-shim-except-cleanup.md), which is already slated to
   fix the swallowed error at the call site (don't double-fix).

## Scope boundary

- **In scope:** the attribute/cache metrics surface and the drop-vs-commit call.
- **Out of scope:** a full observability framework (that's horizon-tier); the
  `except: pass` fix itself (owned by the shim/except prompt).

## Done means

A decision: either the accidental metrics surface is removed, or it's a
deliberate, documented, non-swallowing primitive.
