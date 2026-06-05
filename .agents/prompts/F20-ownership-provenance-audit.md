# F20: ownership-change provenance (audit trail for moderation/recovery)

Status: todo

## Goal

The `.71` ControlBinding rework made ownership a single mutable fact
(`ObjectDB.db_account`) written at a few choke points: `CharactersHandler.add` /
`remove` and the I1 reconcile backfill. There is currently no record of *past*
ownership: when a character is disowned (`remove` clears `db_account`) or
reassigned (`add(transfer=True)`), the previous owner is simply overwritten.

Add a thin, cold-path **provenance trail** so staff can answer "who used to own
this body?" for moderation and recovery. This is history, not authority: it must
never be read during puppet/focus/permission resolution, so it cannot become a
second source of truth for current ownership (the whole point of the `.71`
rework was collapsing ownership to one home).

## Approach

**Start with discussion** — this is a small data-model decision with a few
shapes:

1. Where to store it. Likely an attribute on the character (e.g.
   `db._ownership_history` as an append-only list of `(account_id, action,
   timestamp)` entries), since the character is the thing whose ownership
   changed. Consider a cap on length.
2. What to record. At minimum: previous owner id, new owner id (or `None` on
   disown), the action (`add`/`transfer`/`remove`/`reconcile`), and a timestamp.
3. Where to write it. The single ownership choke point is
   `CharactersHandler.add` / `remove` (and `ControlBinding.ensure_playable` for
   the migration path). Write provenance there, guarded so a logging failure
   never breaks the ownership write.
4. Whether this is engine or game. Ownership provenance is arguably game policy
   (retention, format, who can read it). Lean toward a minimal engine hook /
   attribute that the game can extend, or leave it entirely game-side and only
   expose the choke point. Decide before building.

## Scope boundary

- **In scope**: recording previous-owner history at the ownership write sites;
  a staff-facing way to read it.
- **Out of scope**: anything that reads provenance during live
  puppet/focus/permission resolution (forbidden — it is not authority). Full
  audit-log infrastructure beyond ownership.

## Existing code to study

- `evennia/accounts/accounts.py` — `CharactersHandler.add` / `remove` (the
  ownership choke points).
- `evennia/accounts/models.py` — `ControlBinding.ensure_playable` (migration
  ownership writes).
- `.71` CHANGELOG-FORK.md entry for the ownership model.

## Done means

- Previous owner recorded on disown/transfer, cold-path only, failure-guarded.
- A documented way for staff to read it.
- An explicit note that provenance is history, never consulted for current
  ownership/authority.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Reading provenance anywhere on the puppet/focus/permission hot path.
- Building general audit-log infrastructure beyond ownership changes.
- Deciding engine-vs-game ownership of the feature without confirming.
