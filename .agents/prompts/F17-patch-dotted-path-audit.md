# F17: `@patch("dotted.path")` audit

Status: todo

## Goal

Path-based `@patch("dotted.path")` decorations break silently when
code is relocated. The `+underspire.16` partner had to rewrite 14
sites in one contrib test after a refactor. Engine tests likely
have similar fragility at scale.

Audit engine tests, rewrite path-based patches to `patch.object()`
form where the target is importable.

## Background

See [`testing.md`](../docs/testing.md) for the testing belief
context. Path-based patching is fragile; object-based patching
fails loud at import time when the target moves.

## Approach

1. `grep` for `@patch(` and `patch(` (decorator and context
   manager forms) across `evennia/` tests. Categorize results.
2. For each site, judge: can the target be imported and patched
   via `patch.object`? If yes, rewrite. If no (patching a
   third-party import path you don't own), leave it and note
   why.
3. Ship as one PR or several; reviewer choice based on size.

This is mechanical work modulo the judgment call in step 2. No
design questions.

## Scope boundary

- **In scope**: `evennia/` test files.
- **Out of scope**: `evennia/contrib/` (handled with whatever F13
  decides about contribs); test-base-class refactoring;
  introducing a project-wide patching helper.

## Existing code to study

- `evennia/**/tests*.py` — target files.
- Python docs on `unittest.mock.patch.object` — the form to
  rewrite to.
- `.agents/docs/testing.md` — context.

## Done means

- All in-scope `@patch("dotted.path")` sites either rewritten or
  noted with reason for staying.
- All existing tests still pass.
- Short writeup in PR description summarizing scale of the
  rewrite and any sites left as-is.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Touching contribs.
- Introducing a helper or test-base addition (out of scope).
- Bundling with unrelated test cleanups.
