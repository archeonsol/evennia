# C2: cmdset introspector

Status: todo

## Goal

Build `account.explain_cmd("look")` that returns full trace data:
which cmdset, what priority, which collisions were suppressed, which
locks gated it, why a given command won out (or didn't).

Survival tool for the current cmdset model; superseded by CM1
(cmdset rethink) as the endpoint. C2 makes the current mess
debuggable without changing it.

## Background

Target item C2 in
[`engine-api-architecture.md`](../docs/engine-api-architecture.md).

Current cmdset model is genuinely hard to reason about. Game devs
have no built-in answer to "if I type X right now, here is exactly
which command fires and why." The downstream fork has multiple
debugging tools partly because no introspector exists upstream.

## Approach

This prompt asks you to **propose a design before implementing**.

1. Read the architecture doc C2 item and current cmdset merge/match
   implementation. Understand what trace data is actually available.
2. Read [AGENTS.md](../../AGENTS.md) for repo conventions.
3. Propose a design covering the questions below.
4. **Stop and get user review on your design proposal.**
5. After approval, implement with tests.

## Design questions to resolve in your proposal

- API surface. `account.explain_cmd(cmd_string)`? Also on session,
  on object? Just one entry point?
- Return type. Dict? Dataclass? Structured object? Supports both
  programmatic and human-readable output?
- Trace contents. At minimum: winning command's cmdset, priority,
  what other matches existed and why they lost, what locks were
  checked, lock results. What else?
- Dry-run vs replay. Does the introspector run a real cmdset
  resolution under the hood, or replay a cached one? Tradeoffs?
- Output format. Plain method returning data, plus a separate
  `print_explanation` for human display?

## Scope boundary

- **In scope**: introspection-only API. Reads existing cmdset state;
  doesn't modify it.
- **Out of scope**: changing the cmdset merge algorithm, the cmdset
  cache, or any cmdset semantics. CM1 will do that later.
- Roughly ~200 lines per downstream's estimate. If you find yourself
  needing to modify cmdset internals to expose trace data, stop and
  flag it; that's likely scope creep.

## Existing code to study

- `evennia/commands/cmdset.py` — CmdSet and merge logic.
- `evennia/commands/cmdsethandler.py` — assembly and caching.
- `evennia/commands/cmdhandler.py` — command resolution and lock
  evaluation.
- Downstream fork's cmdset debugging tools (`cmdset_audit.py`,
  `cmdset_merge_warmup.py`, `edge_cmdset_policy.py`) if accessible.
  Ask the user before pulling fork code wholesale.

## Done means

- `explain_cmd` implemented with tests covering: clean match,
  multimatch, lock failure, priority resolution.
- Output usable both programmatically and human-readable.
- All existing tests still pass.
- Architecture doc C2 entry updated.
- PR description summarizes trace data captured.

## Repo conventions

- Python >= 3.12, `uv run`, `make format`, `make lint`.
- TDD: tests first, no-DB > mocks > full-DB.
- Google-style docstrings.
- See [AGENTS.md](../../AGENTS.md).

## Ask before

- Modifying cmdset internals to expose trace data (scope creep into
  CM1 territory).
- Changing merge algorithm or cache.
- Pulling fork code wholesale; let the user decide what's reusable.
