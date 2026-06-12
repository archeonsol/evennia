# AS2: Unified System Scheduler — Engine Primitive

**Status:** Engine side shipped in `6.0.0+underspire.89` (2026-06-11):
`evennia/utils/systems.py`, `evennia/server/engine_systems.py`
(`flush-attributes`), service wiring, tranche A deletions, tests.
Remaining: the downstream migration (section 5, game repo) and tranche B
(`Script.interval` machinery removal, gated on the game's 5 interval
scripts migrating — section 4.6).

**Audience:** an engine contributor (agent or human) implementing in the
`archeonsol/evennia` fork. The companion downstream migration happens in the
game repo (newmoo) and is described at the end under "Downstream (game repo)".

**Lockstep note:** the engine fork and the game are owned by the same team and
upgraded together via the `EVENNIA_REF` pin. There is NO external consumer of
this engine. Therefore: **no deprecation cycle, no back-compat shims, no
parallel old+new paths.** When this work lands, the old mechanisms it replaces
are deleted outright in the same upgrade. Do not preserve `Script.interval`
or `TickerHandler` ergonomics "just in case." Build the new thing cleanly.

---

## 0. Substrate already shipped (do not rebuild)

- **`evennia/utils/defer.py`** (AS1, `underspire.50`/`.51`): `in_thread` /
  `background` / `threaded`, with Django connection hygiene in the worker.
  This is the off-reactor query primitive heavy systems use.
- **`evennia/utils/bulk_tick.py`** (`BulkTickContext`): the heavy-sweep apply
  engine. Gather from L1 on the reactor → pure computation in a worker →
  apply back to L1 on the reactor, with uncached rows handled via a single
  `CASE`/`WHEN` UPDATE. This already solves "sweep many entities' scalar
  attributes without stalling the reactor."
- **Reactor-stall watchdog** (`evennia/utils/reactor_watchdog.py`, started in
  `service.py`): flags any reactor turn over the threshold. System bodies run
  on the reactor, so a stalling system shows up here with no extra
  instrumentation.

What does NOT exist yet: `evennia/utils/systems.py`, the System/Cadence/Scope
model, the registry, the `ctx` object, the driver, last-run persistence,
tests, docs. All of section 4 remains to be built.

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

DELETE (engine-side dead weight made redundant by AS2):
- `TickerHandler` and its whole surface (see "Deletions" in section 4). Its
  per-object subscription model ("this mob ticks every 15s") is the wrong
  granularity — the right tool is one system that selects the relevant
  entities, which is exactly what AS2 provides. The game never adopted it;
  it does not survive as a concept.

These are the same shape ("run this every N / at time T, on the reactor,
possibly over many entities") implemented several ways. AS2 collapses them
into a single engine primitive: a **System Scheduler**.

This is also the deliberate on-ramp to a future ECS. ECS = data-oriented storage
(entities as ids + component stores) PLUS systems (functions run over a
component set on a clock). AS2 builds the *systems + clock* half now, with a
declared-cadence, declared-scope, registry-based interface, and explicitly does
NOT build component storage. When ECS storage lands later, only the *body* of
each system changes (how it selects entities); cadence, scope, registry, and the
driver stay. Build AS2 so that later step is a body swap, not a rewrite.

### The engine/game ownership split (load-bearing; keep it crisp)

- **Engine owns the mechanism:** the driver, the registry, `Cadence`/`Scope`,
  `ctx`, error isolation, last-run persistence. Pure scheduling plumbing,
  game-agnostic.
- **Engine owns systems over engine state:** recurring work whose subject is
  engine infrastructure registers engine-side. The first (and so far only)
  such system is `flush-attributes` (section 4.5).
- **Game owns every game system body:** combat pulses, infection, economy,
  calendar payouts. They live in game modules and register via the game's
  settings-declared module list. The engine never imports them, never names
  them, never knows they exist.

The game's hand-rolled 1 Hz `LoopingCall` + signal fan-out was never game
logic — it was scheduling infrastructure the game built because the engine
didn't offer one. That mechanism comes home to the engine; the handler bodies
stay in the game and merely re-declare their wiring. Net standing loops across
both repos: unchanged (game deletes one 1 Hz loop, engine gains one).

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
  Exactly one of daily/weekly/monthly per cadence (a system wanting two
  calendars is two systems). `monthly` with a day short months lack (29-31)
  clamps to the month's last day — document the clamp, don't skip the month.
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

**Dangerous cells in the scope × cadence matrix.** `every_tick` +
`all_entities` is a reactor killer (a full-world sweep at 1 Hz). `every_tick`
is only sane over bounded online sets (`online_puppets`) or trivially cheap
`global` bodies. Document this in the module docstring; hard enforcement is
optional, a loud log warning at registration time is enough.

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

**`ctx.dt` is system-level, never per-entity.** For `global` and
`all_entities` systems, `dt`-based integration is correct. For
`online_puppets` systems it is a trap: the puppet set changes across
reconnects and restarts, so "seconds since the system last fired" is not
"seconds since this character was last advanced." Per-entity elapsed lives on
the entity (the same last-touched attribute the game's login reconciler
already reads). Spell this out in the docstring — `regen += rate * ctx.dt` in
an `online_puppets` body is wrong on every reconnect, and someone will write
it unless told not to.

For per-entity scopes, the iteration is provided by the scheduler, and the
call shape is **decided: once-per-fire**, never once-per-entity. The body is
called a single time per fire with the entity set on the context:

- `online_puppets` → `ctx.entities`: live puppet objects, gathered on the
  reactor from the session handler (cheap, bounded, no DB hit).
- `all_entities` → `ctx.entity_ids`: plain pks, fetched off-reactor via
  `defer.in_thread` before the body runs (see "Heavy systems" below). Ids,
  not instances — the dominant consumer is `BulkTickContext`, which wants
  exactly that; the rare body that needs full instances loads them itself
  and owns keeping that work bounded.

Once-per-entity was rejected because it invites per-entity reactor
callbacks and makes per-entity `ctx.dt` misuse look natural — both exactly
the traps documented above. Once-per-fire makes the
`stamina_regen_all` pattern (query off-reactor, apply on-reactor via
`bulk_tick`) the path of least resistance.

### Declared-startup-only (the anti-TickerHandler boundary)

The registry accepts systems at startup, from declared modules. It must
**never** grow an API for runtime per-instance one-shot registration ("tick
this object in 90 seconds"). That is how AS2 would degrade back into a
per-object timer registry and lose the systems-select-entities model. The
homes for that shape are:

- **Expiry you can evaluate lazily** (the common case — buffs, temporary
  effects): store `expires_at` on the entity and check it on access, or fold
  the check into the relevant system's tick. No timer at all.
- **Drop-on-reload-acceptable one-shots:** `evennia.utils.delay`.
- **Must-fire-at-T, durable, unattended** (rare; no concrete case known yet):
  the future home is a `run_at` field on the job queue. **Deferred — do not
  build it in AS2.** Recorded for the implementer: trivial on the DB backend
  (a `not_before` column + dequeue filter), a storage-model change on the
  Redis backend (FIFO list → ZSET scored by `run_at`). Build it as its own
  small change the first time a real case appears.

---

## 3. Hard constraints

1. **Everything runs on the reactor thread.** System bodies may touch game
   objects freely because they are on the reactor. NEVER run a system body on a
   worker or daemon thread. This is the whole point: it removes the APScheduler
   daemon-thread race.

2. **Heavy / large-scope systems must not stall the reactor.** The blessed
   pattern is already shipped: offload the *query* via `defer.in_thread` to
   get plain ids off-reactor, then apply on the reactor. For scalar-attribute
   sweeps (the dominant case — regen, decay, hunger), `BulkTickContext`
   (`evennia/utils/bulk_tick.py`) is the answer end-to-end: gather → worker
   compute → single bulk apply, no chunking needed. Reserve Twisted
   `cooperate`-style chunking only for the rare sweep that must touch full
   typeclass instances; if that case actually arises, bless a helper then,
   not preemptively. The module docstring points at `bulk_tick` as the
   reference. The body's reactor-time work must stay bounded; the
   reactor-stall watchdog (shipped) will flag regressions.

3. **Calendar cadence must survive restarts sanely.** On startup, a calendar
   system whose boundary passed while the server was down fires once on the
   next driver tick (catch-up-by-one), not N times, and not never. Document
   this "fire once if missed" semantics explicitly. Do NOT attempt to replay
   every missed occurrence.

   **Persistence is split by cadence — do not lump them:**
   - `calendar` → durable last-run (ServerConfig or equivalent). This is the
     only cadence that genuinely needs to detect a boundary crossed during
     downtime.
   - `every(seconds=N)` → in-memory last-run only. Reset on boot is fine
     (first fire happens N seconds after startup). No DB writes.
   - `every_tick` → no last-run tracking at all. Persisting it would be a
     1 Hz ServerConfig write per system — forbidden.

4. **Registration is explicit and introspectable.** A real registry
   (`register_system(System)` + a queryable list), not import-time
   `@receiver` side effects. Names are the persistence and introspection
   key, so registering a second system under an existing name raises
   immediately rather than replacing. It must be possible to list all registered
   systems, their cadence, scope, and last-run time (for a future `@systems`
   admin command and for tests). Discovery is a **settings-declared module
   list** (e.g. `SYSTEM_MODULES = [...]`) scanned at server start — the same
   shape as `AT_STARTSTOP_MODULE` and cmdset paths. This is the only design
   that actually delivers "forgetting to import a module cannot silently drop
   a system": a module in the list that fails to import or registers nothing
   is a loud startup error, not a silent absence. Decorator-at-import-time
   registration cannot provide this property; do not use it.

5. **Errors in one system never kill the driver or other systems.** A failing
   system logs (via engine logger, full traceback) and is skipped for that fire.
   Do not swallow silently; do not let it propagate into the `LoopingCall`.

6. **No new third-party dependency.** Twisted + stdlib only. (This work lets the
   game drop apscheduler/tenacity/more_itertools downstream, but dependency
   removal is the game's call, not yours.)

7. **A fire may be asynchronous; overlapping fires of one system are
   forbidden.** `run(ctx)` may return a Deferred, and `all_entities` fires
   are inherently async (the id query runs off-reactor before the body).
   The driver tracks an in-flight marker per system: if a system comes due
   while its previous fire has not completed, the driver **skips** that fire
   and logs a warning — it never queues or stacks runs. Last-run advances at
   fire-decision time, so cadence is fire-to-fire regardless of body
   duration. Without this guard, an `every_tick` or short-`every` system
   with a slow query stacks concurrent runs, which is the same race class
   AS2 exists to remove.

---

## 4. Deliverables (engine)

### 4.1 The scheduler module

`evennia/utils/systems.py` (or a small package if it grows): the `System`
model, `Cadence` constructors (`every`, `calendar`, `every_tick`), `Scope`
constructors, the registry, the driver, and the `ctx` object.

### 4.2 The driver (decided — build it this way)

- A **dedicated 1 Hz `LoopingCall`** owned by the scheduler module, started
  and stopped by `service.py` alongside `maintenance_task`. This is the
  game's `global_tick` mechanism moving into the engine, not a new standing
  cost — the game deletes its loop in the downstream migration.
- **Do not drive the scheduler off `server_maintenance`.** That loop is
  semantically calibrated at 60s (its `maintenance_count` modulo gates,
  flush metrics, idle-timeout checks are all in minutes); it stays as-is for
  process hygiene, minus the flush branch (see 4.5).
- **Do not piggyback on the stall watchdog's loop.** The watchdog measures
  its own tick lateness to detect stalls; running work inside it corrupts the
  instrument.
- **Always running, no-op cheap.** An empty registry costs one call and a
  length check per second. No lazy-start lifecycle.
- **Clock injection.** The driver takes `now` from a callable injected at
  construction (real time in prod, `twisted.internet.task.Clock` in tests)
  and threads it into cadence checks and `ctx.now`. Cadence logic never calls
  `time.time()` itself.
- **The tick rate is a module constant and part of the `every_tick`
  contract** ("once per driver tick; the driver ticks at 1 Hz"). Not a
  setting — changing it silently changes every `every_tick` system's meaning.
  If a different rate is ever needed, it is a one-line fork change.

### 4.3 Last-run persistence

Per the cadence split in constraint 3: ServerConfig persistence for
`calendar` only; in-memory for `every`; nothing for `every_tick`.

### 4.4 Module docstring

The full contract: the cadence/scope taxonomy, the dangerous scope×cadence
cells, the offline-policy explanation (scope × login-hook), the `ctx.dt`
system-level-only rule, the declared-startup-only boundary and where one-shot
timers go instead, the heavy-system pattern (pointing at `bulk_tick`), the
engine/game ownership split, and worked examples. This becomes the reference
the game copies, so make it as good as the `defer.py` docstring.

### 4.5 `flush-attributes`: the engine's first registered system

The write-behind attribute flush moves out of `server_maintenance` and
becomes the engine's first system — it is recurring work over engine-owned
state (the L1 cache), which is exactly the engine-side registration case, and
it doubles as the worked example the docstring needs.

- Register `flush-attributes` engine-side: scope `global`,
  `every(seconds=N)` with N from a setting (default 60, may now go finer
  since the driver runs at 1 Hz). The body is the current maintenance branch:
  `flush_all_dirty()` + `maybe_log_flush_metrics` + `maybe_warn_pending_dirty`
  move with it.
- Delete the `ATTRIBUTE_FLUSH_ON_MAINTENANCE` branch from
  `server_maintenance` (`evennia/server/service.py` ~line 136) and retire
  that setting in favor of the cadence setting.
- **The query barriers stay untouched** (`_flush_attr_writes` in
  `evennia/typeclasses/managers.py`, and the equivalent in
  `evennia/objects/manager.py`). They are read-your-writes correctness, not
  scheduling — and they are what makes a missed flush run safe, which is
  precisely the guardrail test for "scheduler, not job queue."
- This makes the driver load-bearing for persistence: if the driver dies,
  flushes stop. Constraint 5 isolates a failing flush run, and
  `maybe_warn_pending_dirty` is the accumulating-dirty alarm, but the flush
  system's own failure path must log loudly — never rely solely on the
  threshold warning.
- **Shutdown gets a final flush.** There is currently no shutdown-time
  `flush_all_dirty()` anywhere in the engine — dirty rows survive teardown
  only if a query barrier happens to fire during it. That pre-existing gap
  becomes untenable once the driver owns flush cadence. The shutdown path
  (`service.shutdown`, all modes — reload/reset/shutdown) stops the driver,
  then runs one final `flush_all_dirty()`, isolated and logged on failure,
  in the same always-runs region where handler state is saved.

### 4.6 Deletions (engine)

Tranche A — lands with AS2 (nothing in the game uses these; the per-object
timer model is superseded):

- `evennia/scripts/tickerhandler.py` (TickerHandler, TickerPool, Ticker,
  `TICKER_HANDLER`).
- `utils.repeat` / `utils.unrepeat` (`evennia/utils/utils.py`).
- The `repeat` inputfunc (`evennia/server/inputfuncs.py` ~line 394).
- The `@tickers` command (`evennia/commands/default/system.py` ~line 960).
- The TickerHandler save/restore calls in `evennia/server/service.py`
  (~lines 675, 768) and the per-object ticker cleanup in
  `evennia/typeclasses/models.py` (~line 800).
- The `TICKER_HANDLER` flat-API entry in `evennia/__init__.py`.
- The one remaining contrib dependent: `turnbattle/tb_items` (delete the
  module, its import in `turnbattle/__init__.py`, and its test classes in
  `turnbattle/tests.py`), consistent with the EvMenu precedent (`.85`
  deleted EvMenu + 9 contribs rather than migrating them). Verified
  2026-06-11: `evadventure` and `tutorial_world` are already gone from the
  tree, and `slow_exit` uses `delay`, not TickerHandler — earlier drafts
  listed all four.
- `TestTickerHandler` in `evennia/scripts/tests.py`.

Tranche B — after the downstream migration moves the game's 5 interval
scripts onto the scheduler (same lockstep arc, may be a follow-up commit
coordinated with the same `EVENNIA_REF` bump): remove the `Script.interval`
timer machinery (`ExtendedLoopingCall` and the interval/start-delay paths in
`evennia/scripts/scripts.py`, plus the `db_interval`-driven fields if a
migration is warranted). Script-as-persistent-object survives only if
something still uses it; check before keeping.

### 4.7 Tests (engine-side, the helpers themselves)

- `every(seconds=N)` fires at the right elapsed boundaries; uses the injected
  clock, never real time.
- `calendar(daily=...)` fires once on boundary crossing; fires once-if-missed
  after a simulated downtime gap; does not double-fire; does not replay.
- `every_tick` fires each tick.
- `dt` reflects real elapsed including gaps.
- a failing system is isolated (driver and peers survive; error logged).
- registry lists systems with introspectable cadence/scope/last-run.
- scope selection calls the right entity-selection path (mock the query;
  assert online_puppets vs all_entities choose different sources).
- settings-declared discovery: a listed module that fails to import or
  registers nothing produces a loud startup error.
- `flush-attributes`: registered at startup, fires on cadence, flushes dirty
  backends, failure is isolated and logged.

### 4.8 Release

`CHANGELOG-FORK.md` entry and a version bump (the game pins by `EVENNIA_REF`;
coordinate the tag with the downstream migration so they land together).

Out of scope for the engine work: ECS component storage; the job-queue
`run_at` extension (deferred; see section 2); the game's actual systems
(infection, matrix cleanup, etc.); dependency removal.

---

## 5. Downstream (game repo, newmoo) — companion migration, NOT engine work

Listed so the engine API is shaped to serve it. This is a separate game-repo
task gated on the engine tag.

Migrate every recurring mechanism onto the scheduler, then delete the old ones:

- `world/events/tick_handlers.py` ~33 `@receiver(global_tick)` handlers →
  systems. Modulo gates (`% 60`, `% 300`) become declared `every(seconds=...)`;
  the 1 Hz combat/AI pulses become `every_tick`. Scope `online_puppets`. The
  handler bodies stay in game modules; only the wiring changes. Delete the
  game's `global_tick` `LoopingCall` + signal.
- Any game-side `flush_all_dirty()` calls on the old global tick → delete;
  the engine's `flush-attributes` system owns flush cadence now (tune its
  setting if 60s was too coarse).
- The 5 global `Script.interval` singletons (`typeclasses/scripts.py`,
  `typeclasses/matrix/scripts.py`, `world/network/nous_hails.py`) → interval
  systems. Delete the Script typeclasses (this unblocks engine tranche B).
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
- System Scheduler — anything recurring, reactor (this work; absorbs the
  game's three old mechanisms AND the engine's TickerHandler/`Script.interval`
  recurring shapes).
- job queue — durable/retryable units that must happen even across downtime.
- `defer` — reactor<->worker location, composes into the others.

This claim is measured **engine-wide, not just game-side**: after both
tranches of deletions there is no second engine answer to "how do I run
something every N seconds."

The two that can blur are the System Scheduler and the job queue (a daily
cleanup could be written as either). The rule: **missing a run is acceptable →
scheduler calendar cadence; must happen eventually / across downtime / retryable
→ job queue.** A durable one-shot timer ("fire at T even across downtime") is a
job-queue concern (`run_at`, deferred until a concrete case exists) — never a
scheduler registration. If a need fits none of the four, stop and reconsider
rather than adding a fifth mechanism.
