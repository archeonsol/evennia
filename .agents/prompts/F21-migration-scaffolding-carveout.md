# F21: carve out one-time migration scaffolding (reconcile et al.)

Status: in-progress — I1 reconcile removed in `.72`; the broader sweep remains.

## Done in `.72`

The I1 ownership backfill (`ControlBinding.reconcile_ownership` /
`reconcile_account` / `ensure_playable` / `_identity_ids_for_account`,
`_PID_LOCK_RE`) and its `@verify-reconcile` gate command + cmdset registrations
were deleted from the engine after a confirmed `created: 0` pass in production.
The game owns its own `at_server_start` backfill now. **Remaining sweep below.**

## Goal

The fork carries one-time migration/backfill machinery that exists only to
repair pre-migration databases and is dead weight once every live DB has been
migrated. Sweep for *all* such scaffolding and produce a removal plan,
so it gets deleted on a deliberate deprecation cycle rather than lingering.

## Approach

1. **Inventory.** Grep the engine for one-time/backfill/repair scaffolding.
   Seeds:
   - `reconcile` (accounts/models.py, admin.py `@verify-reconcile`,
     cmdset_account.py / cmdset_character.py wiring).
   - data migrations that backfill/repair rather than alter schema
     (`evennia/*/migrations/` `RunPython` ops — e.g. the `.71`
     `0019_controlbinding_focus_floor` floor strip, JSONB-era data migrations).
   - comments/markers: `TEMPORARY`, `backfill`, `legacy`, `one-time`,
     `populate_`, `migrate_`, `repair`.
2. **Classify** each: (a) safe to delete now (every live DB past it), (b) keep
   until a gate confirms completion (like `@verify-reconcile`), (c) permanent
   (must stay for fresh installs / ongoing use).
3. **Gate before deleting.** The real gate is `ControlBinding.reconcile_ownership()`
   returning `created: 0` on a fresh pass against production — that means every
   ownership is already backfilled. The `@verify-reconcile` command is only a
   stock-cmdset convenience wrapper around it; a game with engine-only dispatch
   (empty anchor cmdsets that don't merge the Evennia defaults) can't reach the
   command at all, so do NOT assume it is runnable in-game. Run the check via the
   command where available, else `evennia shell -c "from evennia.accounts.models
   import ControlBinding; print(ControlBinding.reconcile_ownership())"`, or read
   the game's `at_server_start` reconcile log line. Only after a confirmed
   `created: 0` pass, delete `reconcile_ownership` / `reconcile_account` /
   `ensure_playable`, the `@verify-reconcile` command, and its two cmdset
   registrations (all marked TEMPORARY).
4. **Present the inventory + classification and get a decision** before deleting
   anything — removal timing depends on which databases are live.

## Scope boundary

- **In scope**: identifying migration/backfill scaffolding and planning or
  executing its removal once gated.
- **Out of scope**: schema migrations that are part of the permanent history
  (those stay); deleting anything a fresh install still needs.

## Existing code to study

- `evennia/accounts/models.py` — `reconcile_ownership` / `reconcile_account` /
  `ensure_playable` (ownership backfill, marked migration-only in `.71`).
- `evennia/commands/default/admin.py` — `CmdVerifyReconcile` (TEMPORARY gate);
  `cmdset_account.py` / `cmdset_character.py` registrations (TEMPORARY).
- `evennia/*/migrations/` — `RunPython` data ops (the `.71` floor strip; JSONB
  data migrations from the `.56`-`.62` range).

## Done means

- An inventory of one-time scaffolding with a keep/gate/delete classification.
- A removal plan (and, for already-completed items, the actual removal) with the
  gate evidence recorded in the commit message.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Deleting any backfill/reconcile code without gate evidence (e.g. a zero
  `@verify-reconcile` first pass) that every live DB is past it.
- Removing a migration that a fresh install still needs.
