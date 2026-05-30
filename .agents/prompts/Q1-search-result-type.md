# Q1: search Result type

Status: todo

## Goal

Replace `caller.search`'s single / list / None shape soup with a
typed `Found | Ambiguous | NotFound` Result type. Disambiguation
prompts become a top-layer convenience built on Result, not an
engine concern.

## Background

Target item Q1 in
[`engine-api-architecture.md`](../docs/engine-api-architecture.md).
Read that item before starting.

Current `caller.search` returns:

- A single object if exactly one match.
- A list if `quiet=True` or multiple-match flags are set.
- None if no match.
- Optionally prints a disambiguation prompt as a side effect.

Every call site has to pattern-match on type, and display behavior
(the disambiguation prompt) is baked into the engine.

## Approach

This prompt asks you to **propose a design before implementing**.

1. Read the architecture doc Q1 item and the existing
   `caller.search` implementation. Trace the variants the current
   API produces.
2. Read [AGENTS.md](../../AGENTS.md) for repo conventions.
3. Propose a design covering the questions below.
4. **Stop and get user review on your design proposal.**
5. After approval, implement with tests.

## Design questions to resolve in your proposal

- Result type shape. Sum-type via subclasses, dataclass with status
  field, `match`-friendly enum + payload? What does each variant
  carry (matched object, candidate list, search string)?
- API surface. Lives alongside `search`, replaces it, or parallel
  `search_result` method? How does the old shape get deprecated?
- Disambiguation prompts. Top-layer convenience method? Helper
  function? Caller responsibility?
- Match semantics. What counts as Ambiguous vs Found? Current
  `multimatch_string` / `nofound_string` parameters: how do they fit?
- Backward compatibility. Migrate call sites immediately? Deprecate
  gradually? P3 (one canonical answer) says no permanent parallel
  paths.

## Scope boundary

- **In scope**: `caller.search`, `obj.search` on DefaultObject, and
  sibling search methods that share the shape soup.
- **Out of scope**: redesigning the underlying matching algorithm,
  changing `nicks` / `aliases` / global search behavior. Return
  shape only.

## Existing code to study

- `evennia/objects/objects.py` — `DefaultObject.search` and related.
- `evennia/commands/default/*` — call sites that pattern-match on
  the current shape; these inform migration cost.
- `evennia/utils/search.py` — module-level search functions; review
  for shape consistency.

## Done means

- Result type implemented with tests covering Found, Ambiguous, and
  NotFound branches.
- New API integrated with `caller.search`; old shape preserved as
  deprecated sugar or migrated, per approved design.
- All existing tests still pass.
- Architecture doc Q1 entry updated (mark shipped with commit SHA).
- PR description summarizes design decisions and links the
  architecture doc.

## Repo conventions

- Python >= 3.12, `uv run`, `make format`, `make lint`.
- TDD: tests first, no-DB > mocks > full-DB.
- Google-style docstrings.
- See [AGENTS.md](../../AGENTS.md).

## Ask before

- Changing the matching algorithm (out of scope).
- Removing the disambiguation prompt feature entirely (migrate, not
  vanish).
- Scope creep into Q1-adjacent areas (search filters, nicks, etc.).
- Expanding scope without user confirmation.
