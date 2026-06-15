# ALPHA: jobs/ + event bus — draw the engine/game line (cross-validation)

Status: todo (cross-repo: engine + game)

## Context

Two subsystems are fully built in the engine but have **zero in-repo
consumers** (verified by grep; contrib excluded):

- **`evennia/jobs/`** — `enqueue_job` / `process_pending_jobs` /
  `register_job_type`, a Redis + Postgres backend with a `SELECT FOR UPDATE SKIP
  LOCKED` dequeue and an `EngineJob` model. Unreached scaffolding; the only
  references are `settings_default.py` config defaults and the backing tables in
  `server/migrations/0004_gameevent_enginejob.py`.
- **`evennia/events/bus.py`** — `emit` / `subscribe`, a Redis/Postgres
  `GameEvent` bus. Also zero consumers. **And it squats the top-level
  `evennia.events` name** while the actually-used event system is a *different*,
  unrelated module: `evennia/actions/events.py` (in-process `EventRegistry`, no
  DB, no Redis), which has the real callsites (`actions/default/movement.py`,
  `actions/actor.py`, `accounts/accounts.py`). Active naming collision.

## The intended line (per the user)

The machinery should live in the **engine**; the **usage** should live in the
**game**. So the goal is not necessarily to cut these, but to confirm the engine
exposes the right primitive and the game is (or will be) the consumer. This needs
an engine ↔ game cross-validation to draw a clean boundary.

## Goal

Decide, per subsystem: **wire** (confirm it is the blessed engine primitive,
document the game-side usage contract, and ensure the game consumes it) or
**cut** (delete the package, its `EngineJob`/`GameEvent` model in `server/0004`,
and the settings keys). Either way, resolve the `evennia.events` vs.
`evennia.actions.events` name collision.

## Approach (cross-validation first)

1. Read the game repo to see whether it already has its own job-queue /
   event-bus usage, or rolls its own equivalent that should consume an engine
   primitive instead. (Reading the game is in scope here — this is the cross-repo
   boundary task.)
2. For each subsystem decide wire-vs-cut on evidence, not aspiration. A fully
   built but unused backend is a maintenance + schema liability for alpha; only
   keep it if the game will consume it on a near horizon.
3. Rename to kill the collision regardless: the top-level `evennia.events` should
   not shadow the in-process `evennia.actions.events`. If `events/bus.py` is cut,
   the collision resolves for free.
4. If cut, the `server` migration squash
   ([`ALPHA-migration-squash.md`](ALPHA-migration-squash.md)) gets simpler —
   coordinate.

## Scope boundary

- **In scope:** wire-or-cut decision for `jobs/` and `events/bus.py`, the name
  collision, the backing model/settings, and the engine↔game usage contract.
- **Out of scope:** `evennia/actions/events.py` (the live in-process system —
  leave it); the unified scheduler (shipped; see
  [`engine-architecture/decisions.md`](../docs/engine-architecture/decisions.md)).

## Done means

Each subsystem is either consumed by the game through a documented engine
contract, or removed along with its model and settings; `evennia.events` no
longer collides with `evennia.actions.events`.
