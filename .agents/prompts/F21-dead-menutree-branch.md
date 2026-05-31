# F21: dead `caller.db._menutree` branch in fieldfill

Status: todo

## Goal

Delete dead code: `contrib/utils/fieldfill/fieldfill.py:253-270`
reads a persistent attribute `_menutree` that nothing writes.

## Background

F5 migrated `_menutree` consumers off the alias to `_evmenu`.
Some in-tree usages were caught; this branch was missed because
it reads from `caller.db` (persistent) rather than `caller.ndb`
(non-persistent), so it didn't show up in the same grep.

The branch is unreachable: nothing has written `caller.db._menutree`
since the migration completed.

## Approach

Pure deletion. No design questions.

1. Read `fieldfill.py:253-270` to confirm the branch shape.
2. `grep` for any `db._menutree` write in-tree to confirm
   nothing writes it. Expected result: zero.
3. Delete the branch. Adjust surrounding control flow as
   needed.
4. Run fieldfill tests.

If F13 (contribs policy) deprecates the whole `fieldfill`
contrib, this prompt is moot — coordinate timing. If F13 keeps
contribs, do this small cleanup either way.

## Scope boundary

- **In scope**: the dead branch at the stated lines.
- **Out of scope**: broader `fieldfill` cleanup; touching
  other `_menutree` / `_evmenu` consumers; the contribs
  policy question itself.

## Existing code to study

- `evennia/contrib/utils/fieldfill/fieldfill.py:253-270` —
  the branch.
- `evennia/contrib/utils/fieldfill/` — tests.
- F5 changelog entry in `CHANGELOG-FORK.md` for migration
  context.

## Done means

- Branch deleted.
- Fieldfill tests pass.
- One-line note in `CHANGELOG-FORK.md` if appropriate (this
  is small enough to maybe roll into the next release without
  its own entry — judgment call).

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Touching anything outside the named branch.
- Doing this if F13 has decided to deprecate fieldfill (then
  it goes with the contrib).
