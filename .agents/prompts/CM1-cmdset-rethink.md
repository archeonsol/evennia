# CM1: cmdset rethink

Status: todo

## Goal

Replace the merge-time-and-cached cmdset model with something
simpler: either query-time evaluation (no merge step, no cache,
recompute on each command lookup) or merge-time with drastically
fewer knobs and explicit contracts. Design open; pick during the
work.

A standalone introspector (`account.explain_cmd("look")`) was
considered as a survival tool for the current model and dropped —
CM1 should bake introspection into the new model rather than ship
throwaway debug machinery for the old one.

## Background

Target item CM1 in
[`engine-api-architecture.md`](../docs/engine-api-architecture.md).

Current model: priority + duplicates + merge type + key collision +
cmdset stacks. Genuinely hard to reason about. Footguns include
cache invalidation bugs (the `at_sync` bug fixed in Phase A is one
symptom). The downstream fork has multiple debugging tools partly
because the model is opaque.

This is **a large item**. Expect the design pass to take real time.
This is probably the most consequential single change in Level 2
after I1; treat it accordingly.

## Approach

1. Read the architecture doc CM1 item.
2. Read the cmdset-related sections of
   [`Typeclass-Hooks.md`](../../docs/source/Components/Typeclass-Hooks.md) (B1's output).
3. Read [`command-system.md`](../docs/command-system.md) for the
   current cmdset contracts.
4. Study the current cmdset implementation thoroughly. Understand
   why each knob exists and what it costs.
5. Read [AGENTS.md](../../AGENTS.md).
6. **Write a design proposal.** This should be a substantial doc
   (2-3 pages); design at this scale benefits from being written
   down before coding.
7. **Stop and get user review.** Expect multiple rounds.
8. After approval, implement.

## Design questions to resolve in your proposal

- **Query-time vs merge-time.** Tradeoffs in performance,
  predictability, debuggability, complexity. Pick one and argue
  for it.
- **Knob inventory.** Current model has priority, duplicates,
  `mergetype` (Union / Intersect / Replace / Remove), key collisions,
  no_objs / no_exits / no_channels filters, permissions. Which to
  keep, which to drop? Why?
- **Cache strategy.** Any caching? If yes, where and what's the
  invalidation contract? (`at_sync` bug history is the warning.)
- **Cmdset stacks.** Stacking semantics: keep as-is, simplify, or
  replace?
- **Backward compatibility.** Game-side cmdsets target current API.
  Migration story: automatic, scripted, manual rewrite? How big is
  the break?
- **Built-in debuggability.** "If I type X right now, exactly which
  command fires and why?" must be cheap to answer in the new model.
  Bake the trace surface (winning cmdset, priority, suppressed
  collisions, lock results) into the design — don't bolt it on later.
- **Hook integration.** Cmdset assembly fires hooks per B1's
  contract. Honor those; flag any that need to change.
- **Performance.** Current cache exists for performance. If you
  drop it, what's the cost on hot paths?
- **Edge cases.** Multi-puppet (I2), session re-attach (at_sync),
  cmdset add/remove at runtime, lock-gated commands. Each must work
  cleanly in the new model.

## Scope boundary

- **In scope**: cmdset model redesign, migration of engine-internal
  cmdsets, migration tooling/guidance for game-side cmdsets,
  comprehensive tests.
- **Out of scope**: rewriting commands themselves; changing
  cmdhandler beyond what the cmdset model requires; changing lock
  system.

## Existing code to study

- `evennia/commands/cmdset.py` — CmdSet class, merge logic, the
  knobs.
- `evennia/commands/cmdsethandler.py` — assembly, caching,
  invalidation paths.
- `evennia/commands/cmdhandler.py` — command resolution, lock
  evaluation. Coordinate at the cmdset/cmdhandler boundary.
- [`command-system.md`](../docs/command-system.md) — current
  cmdset contracts.
- Phase A's `at_sync` fix
  (`evennia/server/serversession.py`) — the kind of bug current
  caching produces.
- Downstream fork's cmdset debugging tools (ask user) if useful as
  prior art.

## Done means

- New cmdset model implemented with tests covering all edge cases
  identified in the design.
- Engine-internal cmdsets migrated.
- Game-side migration story documented (in a doc, not just the
  PR).
- All existing tests pass (with possible exceptions for tests that
  test the old model's knobs explicitly; coordinate with user).
- Architecture doc CM1 entry updated.
- PR description summarizes design and links the proposal.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Breaking changes to game-side cmdsets without a migration story.
- Removing knobs that are widely used in practice (audit before
  removing).
- Touching cmdhandler beyond the cmdset boundary.
- Scope creep into command-side changes.
