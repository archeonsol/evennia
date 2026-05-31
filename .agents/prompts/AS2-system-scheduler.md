# AS2: Unified System Scheduler — Engine Primitive

**Status:** Not started. Design approved; this prompt is the build spec.

**Audience:** an engine contributor (agent or human) implementing in the
`archeonsol/evennia` fork. The companion downstream migration happens in the
game repo (newmoo) and is described at the end under "Downstream (game repo)".

**Lockstep note:** the engine fork and the game are owned by the same team and
upgraded together via the `EVENNIA_REF` pin. There is NO external consumer of
this engine. Therefore: **no deprecation cycle, no back-compat shims, no
parallel old+new paths.** When this work lands, the old mechanisms it replaces
are deleted outright in the same upgrade. Do not preserve `Script.interval`
ergonomics "just in case." Build the new thing cleanly.

---

## 1. Why this exists

The game currently schedules recurring work through three overlapping
mechanisms, plus two that are fine and stay:

KEEP (untouched, distinct shapes):
- `evennia.utils.delay` — one-shot, action-tied reactor callbacks. ~162 sites.
- the job queue (`evennia.jobs`) — durable/retryable units of work.
- `evennia.utils.defer` — reactor<->worker location primitive (AS1). Composes
  INTO systems; not a scheduler itself.

REPLACE (three implementations of one idea: "recurring work on the reactor"):
- the game's homegrown `global_tick` signal (one 1 Hz `LoopingCall` fanning out
  to ~33 `@receiver` handlers, each self-gating with `if tick_count % N`).
- Evennia `Script.interval` global singletons (~5 of them).
- APScheduler (`BackgroundScheduler`, ~15 jobs) running on a **daemon thread**,
  which is a correctness bug: its jobs touch `.db`/typeclasses/idmapper off the
  reactor and race live game state.

These three are the same shape ("run this every N / at time T, on the reactor,
possibly over many entities") implemented three ways. AS2 collapses them into a
single engine primitive: a **System Scheduler**.

This is also the deliberate on-ramp to a future ECS. ECS = data-oriented storage
(entities as ids + component stores) PLUS systems (functions run over a
component set on a clock). AS2 builds the *systems + clock* half now, with a
declared-cadence, declared-scope, registry-based interface, and explicitly does
NOT build component storage. When ECS storage lands later, only the *body* of
each system changes (how it selects entities); cadence, scope, registry, and the
driver stay. Build AS2 so that later step is a body swap, not a rewrite.

---

## 2. The model

A **System** is a declared unit of recurring work:

```python
System = {
    "name": str,              # unique, stable; used for registry + last-run persistence
    "cadence": Cadence,       # WHEN it fires (see below)
    "scope": Scope,           # WHICH entities / what offline policy (see below)
    "run": Callable,          # the body: run(ctx) -> None
}
```

### Cadence (WHEN) — three kinds, one registry

- `every(seconds=N)` — fire when at least N seconds have elapsed since last run.
  Subsumes both the `global_tick` modulo gates and `Script.interval`.
- `calendar(daily="HH:MM", monthly=(day, "HH:MM"), weekly=(weekday, "HH:MM"))`
  (UTC) — fire when a UTC boundary is crossed since last run. Subsumes
  APScheduler's cron jobs. This is the one genuinely new capability.
- `every_tick()` — fire every driver tick (for the 1 Hz combat/AI pulses that
  currently run on every `global_tick`).

Cadence is a **declared property**, not a magic number buried in the body. The
scheduler decides whether to fire; the body never checks the clock.

### Scope (WHICH / offline policy) — this is the offline-behavior axis

Scope answers "what does this system run over, and does it run while players are
offline?" The game must be able to choose per system. Provide at minimum:

- `online_puppets` — run only over currently-puppeted characters. This is the
  "eager but online-only" behavior the `global_tick` pulses have today. Use for
  systems that should NOT advance for absent players (e.g. infection,
  addiction). Absent players are caught up separately on login (see below).
- `all_entities(component=...)` — run over all matching entities regardless of
  online state. This is "run while offline" / true world-state advancement
  (e.g. faction economy, wilderness respawn, world events). Today `component`
  is expressed as a typeclass-path / queryset filter (no real components yet);
  design the API so it becomes an ECS component query later without changing
  call sites.
- `global` — run once, not per-entity (e.g. cleanup jobs, digests, index
  maintenance). The body does its own querying.

**Important boundary:** "catch up after login" is NOT a scope. It is a different
*trigger* (presence/login, not the clock) and must stay in the `at_post_puppet`
reconcile path the game already has (`world/medical/offline_reconciliation.py`).
The scheduler owns clock-triggered work only. A game system that wants
"freeze-while-offline + catch-up-on-return" registers an `online_puppets` clock
system AND (game-side) a login reconciler. A system that wants
"advance-while-offline" registers an `all_entities` clock system and no
reconciler. The engine does not need to know about the login hook; it just must
not force offline iteration on systems that don't want it. The two offline
policies fall out of (scope choice) x (whether the game also wires a login
reconciler). Do not build a login hook into the scheduler.

### The run context (`ctx`)

`run(ctx)` receives a context object carrying at least:
- `ctx.now` — the wall-clock time of this fire (float epoch). Pass it in; do not
  let bodies call time themselves (keeps them testable and catch-up-aware).
- `ctx.dt` — seconds since this system last ran (real elapsed, may exceed the
  nominal cadence if the server was busy or down). Bodies that integrate over
  time (regen, decay) use `dt` instead of assuming a fixed step.
- For per-entity scopes, the iteration is provided by the scheduler (see
  "Heavy systems" below for how entity lists are produced without stalling the
  reactor). Decide whether `run` is called once-per-fire with the entity set
  available via `ctx`, or once-per-entity; pick the one that makes the
  `stamina_regen_all` pattern (query off-reactor, apply on-reactor in chunks)
  natural. Document the choice.

---

## 3. Hard constraints

1. **Everything runs on the reactor thread.** System bodies may touch game
   objects freely because they are on the reactor. The scheduler driver is the
   existing 1 Hz `LoopingCall` (or one like it). NEVER run a system body on a
   worker or daemon thread. This is the whole point: it removes the APScheduler
   daemon-thread race.

2. **Heavy / large-scope systems must not stall the reactor.** A system that
   sweeps all characters does the AS1 pattern: offload the *query* via
   `defer.in_thread` to get a list of plain ids off-reactor, then apply on the
   reactor (in chunks if needed). The reference is the game's existing
   `stamina_regen_all` (`world/rpg/stamina.py`). Provide a helper or documented
   convention so game systems don't each reinvent this. The body's reactor-time
   work must stay bounded; the reactor-stall watchdog (AS1) will flag regressions.

3. **Calendar cadence must survive restarts sanely.** Persist each system's
   last-run timestamp (ServerConfig or equivalent). On startup, a calendar
   system whose boundary passed while the server was down fires once on the next
   driver tick (catch-up-by-one), not N times, and not never. Document this
   "fire once if missed" semantics explicitly. Do NOT attempt to replay every
   missed occurrence.

4. **Registration is explicit and introspectable.** A real registry
   (`register_system(System)` + a queryable list), not import-time
   `@receiver` side effects. It must be possible to list all registered systems,
   their cadence, scope, and last-run time (for a future `@systems` admin
   command and for tests). Forgetting to import a module should not silently
   drop a system; prefer a registration path that is discoverable.

5. **Errors in one system never kill the driver or other systems.** A failing
   system logs (via engine logger, full traceback) and is skipped for that fire.
   Do not swallow silently; do not let it propagate into the `LoopingCall`.

6. **No new third-party dependency.** Twisted + stdlib only. (This work lets the
   game drop apscheduler/tenacity/more_itertools downstream, but dependency
   removal is the game's call, not yours.)

---

## 4. Deliverables (engine)

1. `evennia/utils/systems.py` (or a small package if it grows): the `System`
   model, `Cadence` constructors (`every`, `calendar`, `every_tick`), `Scope`
   constructors, the registry, the driver integration, and the `ctx` object.
2. Driver wiring: integrate with the engine's tick loop. Decide whether the
   scheduler owns its own `LoopingCall` or hooks the existing one; one clock is
   preferred. Document it.
3. Last-run persistence for calendar/interval cadence.
4. `evennia/utils/systems.py` module docstring: the full contract, the
   cadence/scope taxonomy, the offline-policy explanation (scope x login-hook),
   the heavy-system pattern, and worked examples. This becomes the reference the
   game copies, so make it as good as the `defer.py` docstring.
5. Tests (engine-side, the helpers themselves):
   - `every(seconds=N)` fires at the right elapsed boundaries; uses injected
     `now`, not real time (the codebase forbids `Date.now`-style nondeterminism
     in tests; thread time in via the driver).
   - `calendar(daily=...)` fires once on boundary crossing; fires once-if-missed
     after a simulated downtime gap; does not double-fire; does not replay.
   - `every_tick` fires each tick.
   - `dt` reflects real elapsed including gaps.
   - a failing system is isolated (driver and peers survive; error logged).
   - registry lists systems with introspectable cadence/scope/last-run.
   - scope selection calls the right entity-selection path (mock the query;
     assert online_puppets vs all_entities choose different sources).
6. `CHANGELOG-FORK.md` entry and a version bump (the game pins by `EVENNIA_REF`;
   coordinate the tag with the downstream migration so they land together).

Out of scope for the engine work: ECS component storage; reimplementing
`Script` on top of the scheduler (possible later, not now); the game's actual
systems (infection, matrix cleanup, etc.); dependency removal.

---

## 5. Downstream (game repo, newmoo) — companion migration, NOT engine work

Listed so the engine API is shaped to serve it. This is a separate game-repo
task gated on the engine tag.

Migrate every recurring mechanism onto the scheduler, then delete the old ones:

- `world/events/tick_handlers.py` ~33 `@receiver(global_tick)` handlers →
  systems. Modulo gates (`% 60`, `% 300`) become declared `every(seconds=...)`;
  the 1 Hz combat/AI pulses become `every_tick`. Scope `online_puppets`.
- The 5 global `Script.interval` singletons (`typeclasses/scripts.py`,
  `typeclasses/matrix/scripts.py`, `world/network/nous_hails.py`) → interval
  systems. Delete the Script typeclasses.
- `world/scheduler.py` APScheduler jobs + the external `register_*_jobs`
  (`world/rpg/factions/pay.py`, `world/rpg/xp.py`, `world/wilderness_map.py`,
  `world/food/jobs.py`) → calendar/interval systems with scope chosen per job.
  Delete `world/scheduler.py` entirely. Flag apscheduler/tenacity/more_itertools
  as removable (the user edits dependency files, not the agent).
- **Offline-policy correction (call out, do not silently change):** today the
  APScheduler `infection_tick`/`addiction_tick` sweep ALL characters every 30
  min on a daemon thread, which can advance (and per game design, potentially
  kill) offline players. The game's intent is that offline players are frozen
  and caught up on login. So these become `online_puppets` clock systems (online
  players only) and rely on the existing `at_post_puppet` reconcile for absent
  players. This is a deliberate behavior change; flag each one for review rather
  than folding it into a "behavior-preserving" lift.
- Verify each medical/economy sweep against its login-reconcile counterpart in
  `world/medical/offline_reconciliation.py` before choosing its scope.

Leave `delay()`, the job queue, and `defer` untouched downstream; they are
distinct shapes.

---

## 6. The guardrail (why this is fewer mechanisms, not more)

After AS2 the complete async/scheduling surface is exactly four primitives with
non-overlapping definitions:

- `delay()` — one-shot, action-tied, reactor.
- System Scheduler — anything recurring, reactor (this work; absorbs three old
  mechanisms).
- job queue — durable/retryable units that must happen even across downtime.
- `defer` — reactor<->worker location, composes into the others.

The two that can blur are the System Scheduler and the job queue (a daily
cleanup could be written as either). The rule: **missing a run is acceptable →
scheduler calendar cadence; must happen eventually / across downtime / retryable
→ job queue.** If a need fits none of the four, stop and reconsider rather than
adding a fifth mechanism.
