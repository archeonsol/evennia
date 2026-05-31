# F8: cache invalidation contracts

Status: todo

## Goal

Each of the fork's seven derived-state caches gets a documented
invalidation contract: **what fills me / what invalidates me /
staleness bound.** Then a cross-cache audit on whether TTLs and size
caps still make sense.

The seven caches: location-cmdset, cmd-access, display-name, lock,
write-behind attrs, trie, redis-attr.

## Background

The cache-discipline belief lives in
[`core-beliefs.md`](../docs/core-beliefs.md) under "Objects carry
their own state." It already says caches earn their place with a
documented invalidation contract; this prompt is about actually
writing those contracts down where the cache lives.

Cache-addition discipline (also in core-beliefs) requires this
content exist before the next cache lands.

## Approach

Two steps. Ship as two separate commits or two separate PRs;
reviewer choice.

1. **Docstring pass.** For each cache module, add or expand the
   module-level docstring to cover the three contract questions.
   Mechanical work; no design decisions.
2. **Audit.** Read all seven contracts side by side. Look for:
   redundant invalidation (cache A invalidates on event X but cache
   B should too), unbounded growth (no size cap, no TTL), staleness
   bounds that are wrong by an order of magnitude. Propose
   adjustments; **stop and review with user before changing
   behavior**.

Step 1 is safe to do without checking in. Step 2 produces a
proposal, not edits, until reviewed.

## Scope boundary

- **In scope**: docstrings on cache modules; audit document listing
  findings.
- **Out of scope**: rewriting invalidation logic in step 1; adding
  new caches; removing caches without explicit approval.

## Existing code to study

Use `grep` to find the seven cache modules; they're scattered. Start
points: `evennia/commands/cmd_access_cache.py`,
`evennia/utils/idmapper/`, the trie cache in cmdset code,
`display_name_cache` (game-side after `+underspire.36`; engine-side
seam is the cache target).

## Done means

- Each cache module has a docstring covering fill / invalidate /
  staleness.
- Audit findings written up; any agreed adjustments shipped as
  separate commits with tests.
- All existing tests pass.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Changing invalidation behavior (audit produces a proposal first).
- Removing a cache (separate decision; see cache-discipline rule).
- Adding a cache (won't happen in this work; flag if tempted).
