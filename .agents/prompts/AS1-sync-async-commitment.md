# AS1: sync/async commitment

Status: todo

## Goal

Pick a direction for sync vs async, ship the supporting utilities
that make that direction usable. Stop being ambiguous about how I/O
in hooks should work.

## Background

Target item AS1 in
[`engine-api-architecture.md`](../docs/engine-api-architecture.md).

Twisted reactor underneath. Most game code is sync. `inlineCallbacks`
is supported but not recommended. Long-running hooks stall the
reactor. There's no documented story for "I want to call a slow API
from inside a hook."

Architecture doc recommends **option 1: sync-by-default + helpers**
unless someone has a concrete case for option 2 (async first-class).

## Approach

1. Read the architecture doc AS1 item.
2. Read [AGENTS.md](../../AGENTS.md).
3. Propose a design covering the questions below. **You may propose
   option 1 (recommended), option 2, or a hybrid; argue for whichever
   you think is right.**
4. **Stop and get user review on your design proposal.**
5. After approval, implement.

## Design questions to resolve in your proposal

- Direction commitment. Option 1, option 2, or hybrid? Why?
- Helper set. For option 1: thread-pool defer wrapper, fire-and-
  forget, scheduled task wrapper. What else is needed?
- API shape. `evennia.utils.defer.in_thread(func, ...)`?
  Decorators? Context managers? What's most ergonomic?
- Existing `inlineCallbacks` and `deferToThread` usage in the
  codebase: keep, deprecate, migrate?
- Detection of hooks that block too long. Worth instrumenting? How?
- Documentation. Where does the "this is how to do I/O" guidance
  live? Standalone doc? Section in existing docs?
- Edge cases: what if a hook does block? Reactor stalls; detectable?
  Logged?

## Scope boundary

- **In scope**: utility helpers, documented direction, basic
  detection for blocking-too-long if cheap.
- **Out of scope**: rewriting existing hook implementations to use
  the new helpers (gradual migration after this lands); making the
  whole engine async (option 2 is a large rewrite if chosen).

## Existing code to study

- `evennia/utils/utils.py` — existing defer/thread helpers.
- `evennia/server/serversession.py` and hook implementations — to
  understand what blocking patterns exist today.
- Twisted's `reactor.callInThread`, `deferToThread` patterns — these
  underlie any sync+helpers design.

## Done means

- Direction documented in a clear, findable place.
- Helper utilities implemented with tests.
- At least one example showing migration of a blocking pattern from
  ad-hoc to helper.
- All existing tests pass.
- Architecture doc AS1 entry updated.
- PR description summarizes direction chosen + helper set.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Switching from option 1 to option 2 (much bigger commit than AS1
  was scoped for).
- Mass-migrating existing blocking code (post-AS1 work).
- Adding new dependencies (async libraries, etc.).
