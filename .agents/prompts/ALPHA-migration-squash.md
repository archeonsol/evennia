# ALPHA: migration squash (meticulous, cross-repo coordinated)

Status: todo (cross-repo: engine + game; do slowly and carefully)

## Context

The fork has long per-app migration chains (objects ~19, accounts ~19, comms
~28, scripts ~24, typeclasses ~23, help ~6, server ~5) carrying a decade of
base-Evennia history plus the fork's M2M→JSONB attribute migration. There is a
**single downstream consumer whose DB is confirmed at head** — so no historical
data migration ever needs to run again on it; the chains only matter for
fresh installs and reverse/audit paths.

This is split into its own session **not because it is hard but because it must
be done meticulously**, and because there are likely **game-side migrations and
backfills** to consider and integrate. Coordinate across repos before squashing.

## Load-bearing facts (verified against the migration files)

- **`typeclasses`** creates the `Attribute` model in `0001_initial` (lines
  15-90) and deletes it in `0023` (`DeleteModel(name="Attribute")`, line 26).
  The model is gone from current source. Migrations `0001,0005,0006,0007,0012,
  0015,0016,0019,0022,0023` build, index, type-migrate, and finally destroy a
  now-deleted table — pure create-then-destroy churn.
- **Per-app JSONB arc** is uniform: add `db_attrs` JSONField → alter → GIN index
  (Postgres-only `RunPython` `CREATE INDEX CONCURRENTLY`) → remove `db_attributes`
  M2M. E.g. objects `0015→0016→0017→0018`; mirrored accounts `0014-0017`, comms
  `0024-0027`, scripts `0020-0023`.
- **Cross-app constraint:** `typeclasses/0023` depends on the four
  `*_remove_*_db_attributes` migrations (objects 0018, accounts 0017, scripts
  0023, comms 0027) because their M2M through-tables FK'd `typeclasses_attribute`
  and must drop first. **Any squash must keep `typeclasses` last.**
- **The one real hazard:** `scripts/0019_backfill_paused_state_from_attributes`
  reads the now-deleted model via `apps.get_model("typeclasses","Attribute")`
  (line 50). A squashed `typeclasses` initial without `Attribute` breaks this
  migration's historical state if it remains in the graph. Its forward effect is
  already applied at head → **drop `0019`** and fold the final ScriptDB
  pause-column shape into the squashed scripts initial.
- Obsolete one-time data moves (safe to drop): `accounts/0007_copy_player_to_
  account` (no `players` app on fresh install), and the `convert_contrib_*`
  path-remap migrations (objects 0012, scripts 0015, accounts 0011) — doubly
  obsolete since the fork is removing contrib.

## Per-app verdict

| App | Verdict | Care |
|-----|---------|------|
| typeclasses | SAFE-TO-SQUASH | Squash **last**. Fresh initial emits only `Tag` + current Tag indexes; omit `Attribute` entirely. |
| objects | with care | Preserve GIN `0017` as `RunPython` with `atomic=False`. Drop contrib remap `0012`. |
| accounts | with care | Drop `0007`,`0011` (obsolete). Fold `0019_controlbinding...strip_floor`'s final field defs into the create. Preserve GIN `0016`. |
| comms | with care | Preserve GIN `0026`. Squash erases the old duplicate-`0011` + `0012_merge` branch artifact. |
| scripts | hazard | **Drop `0019`** (effect at head); fold final pause-columns into initial. Preserve GIN `0022`. |
| help | SAFE | Trivial. |
| server | SAFE | `0004` creates `GameEvent`/`EngineJob` — coordinate with [`ALPHA-jobs-eventbus-boundary.md`](ALPHA-jobs-eventbus-boundary.md); if those are cut, the model drops out and the squash simplifies. |

## Approach

1. **Coordinate cross-repo first.** Inventory game-side migrations/backfills that
   depend on engine migration state or replay engine data moves. Decide how they
   integrate with squashed engine initials before touching anything.
2. Do **not** blind-run `squashmigrations`. Squash per-app in dependency order
   with `typeclasses` last. Before squashing scripts, delete `0019`.
3. Manually verify each squashed initial reproduces the *current* schema
   (diff against `sqlmigrate` / `makemigrations --check`).
4. Re-attach the four Postgres GIN ops to the squashed initials of
   objects/accounts/comms/scripts. **`squashmigrations` makes the initial atomic
   by default; `CREATE INDEX CONCURRENTLY` cannot run in a transaction** — the
   GIN `RunPython` must keep `atomic=False`. Check this explicitly.
5. Validate a fresh install bootstraps cleanly on both SQLite and Postgres, and
   that the existing consumer DB still reports no pending migrations.

## Scope boundary

- **In scope:** squashing the engine migration chains + the GIN/atomic and
  scripts/0019 hazards + cross-repo coordination of game migrations.
- **Out of scope:** schema changes; the jobs/events model decision itself (just
  coordinate the timing).

## Done means

Each app's chain is collapsed (or consciously left), fresh installs bootstrap on
SQLite + Postgres, the consumer DB has no pending migrations, GIN indexes are
preserved with `atomic=False`, scripts/0019 is gone, and game-side migrations are
reconciled.
