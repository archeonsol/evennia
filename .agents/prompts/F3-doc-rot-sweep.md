# F3: doc rot sweep in `docs/source/`

Status: todo (gated)

## Goal

Sweep `docs/source/` for references to removed APIs. Last count:
31 hits across `MuxCommand`, `MuxAccountCommand`,
`CMD_IGNORE_PREFIXES`. Likely more.

## Gating

**Wait for the newmoo `engine-16-adopt-contribs` PR to merge.**
Most contrib doc rot dies with the moved modules first; sweeping
before that wastes effort. Check with user whether that PR has
landed before starting.

If the user says "go ahead anyway," fine — just expect a
follow-up sweep after the PR merges.

## Approach

1. Confirm gating with user.
2. `grep` for known-removed symbols across `docs/source/`. Start
   with the three named above; expand as you find others (every
   "this API is deprecated" note from the last year is a
   candidate).
3. For each hit: delete the doc section, update to point at the
   replacement, or rewrite the example with current API. Pick
   per case; ask if unsure.
4. Run docs build if there is one; check for broken links.

This is mechanical work. No design questions. Don't redesign doc
structure while you're in there.

## Scope boundary

- **In scope**: textual references to removed APIs; broken
  examples; broken cross-links *caused by* removals.
- **Out of scope**: docs reorg, prose tone passes, adding new
  docs for current APIs, screenshot refreshes.

## Existing code to study

- `docs/source/` — target directory.
- `CHANGELOG-FORK.md` — which APIs got removed in which
  underspire release.
- `.agents/docs/command-system.md` — current cmdset/command
  contracts; the source of truth examples should match.

## Done means

- Zero `grep` hits for the targeted removed symbols.
- Examples in updated sections actually run.
- Docs build (if present) is clean.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Starting before newmoo `engine-16-adopt-contribs` merges.
- Restructuring doc layout (out of scope).
- Deleting whole pages rather than editing them.
