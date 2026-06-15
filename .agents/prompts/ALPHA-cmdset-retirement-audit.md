# ALPHA: cmdset retirement (the CM1 finish line) — chunk index

Status: in-progress (chunked 2026-06-15). Gates cleared. This is the parent
index; the work is split into six self-contained chunk prompts. Predecessors:
[`CM1-action-system-roadmap.md`](CM1-action-system-roadmap.md) (Phase 1-8
shipped), [`CM1-input-capture-migration.md`](CM1-input-capture-migration.md)
(EvMore/EvEditor migrated; EvMenu **deleted** in `.85`, not migrated), login
engine-owned in `6.0.0+underspire.95`.

## Gates — all cleared

The action engine has been the sole player-input dispatch path since `.73`. The
historical blockers are resolved: EvMore (`.77`) + EvEditor (`.82`) capture
migrated; EvMenu **deleted** in `.85` (no consumer remained, so the
`TestEvMenuState`/`EvMenuState` scaffold concern is moot — both are gone); login
engine-owned in `.95`. No precondition remains before starting.

## The consumer-tier finding (why this is six chunks, not one)

The original "delete `CmdSet`/`Command`/handler" framing was wrong: the substrate
has **three tiers of consumer** that must be peeled in order, and contrib is a
large external tier the repo-locked audit set aside.

1. **Dead default tree** (`commands/default/*`, 14.2k lines) — no live consumer
   except one help-formatter carve-out (`CmdHelp`/`HelpCategory` reused by
   `help/renderer.py` and the engine `Help`/`SetHelp` actions).
2. **Engine-internal `CmdSet`/`Command` users** — `utils/eveditor.py`
   (`:`-command match via `cmdparser.build_matches`) and `help/renderer.py`.
3. **Contrib** (~16 modules) + the `db_cmdset_storage` column + the `.cmdset`
   handler. `CmdSet`/handler/`Command` cannot die until contrib is cleaned up.

Decided forks: **Fork A** — drop the `db_cmdset_storage` column (yes, via
migration, coordinated with the squash). **Contrib** — clean it up rather than
keep inert `CmdSet`/`Command` shims (option C1); it is its own chunk, not a
permanent blocker.

## Chunks (one reviewable PR each; folder convention — drop into a fresh context)

| # | Chunk | Gated on | Startable now |
|---|---|---|---|
| 1 | [help formatters → `evennia/help/`](ALPHA-cmdset-1-help-formatters.md) | — | ✅ |
| 2 | [EvEditor off `CmdSet`/`cmdparser`](ALPHA-cmdset-2-eveditor-cmdset.md) | — | ✅ |
| 3 | [delete `commands/default/` tree](ALPHA-cmdset-3-default-tree.md) | 1 | after 1 |
| 4 | [delete legacy dispatch machinery](ALPHA-cmdset-4-dispatch-machinery.md) | 2, 3 | after 2,3 |
| 5 | [contrib cmdset cleanup](ALPHA-cmdset-5-contrib-cleanup.md) | — (∥ 1-4) | ✅ |
| 6 | [delete the substrate + DB column](ALPHA-cmdset-6-substrate.md) | 4, 5 | gated |

Chunks **1-4 and 5 are all startable now in parallel**; only 6 is gated.

## Scheduling note (affects the alpha critical path)

Chunk 6 carries the `db_cmdset_storage` migration, so it touches
[`ALPHA-migration-squash.md`](ALPHA-migration-squash.md). And chunk 6 gates on
chunk 5 (contrib). **Therefore contrib cleanup is on the critical path to the
squash**, not a side quest — the burn-down's single "CM1 cmdset retirement" node
should expand to: engine-internal (1-4, now) + contrib gate (5) + final
substrate (6, gates squash).

## Companion

[`ALPHA-engine-minimal-inventory.md`](ALPHA-engine-minimal-inventory.md) — the
read-only "what's actually left" finding (the `default/` tree is the only large
removable mass; no game-shaped code hides in the engine). Reference, not a task.
