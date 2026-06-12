# Fork changelog

This file tracks divergences in this fork from upstream Evennia. Upstream's
own `CHANGELOG.md` is preserved unchanged; this is the fork-only history.

Versions use [PEP 440 local segments](https://peps.python.org/pep-0440/#local-version-identifiers):
`<upstream-version>+underspire.<n>`, where the trailing integer increments
on each tagged fork release. The `+local` suffix means `pip` will install
this build correctly and it will never collide with a published upstream
release of the same base version.

Read `evennia.__version__` at runtime; it returns the full string with the
current git rev appended.

## Maintenance

**Don't compress individual release entries.** Forensic value ("why does X
work this way?") comes from the original detail; compression destroys it.
Git history and `grep` cover any browsing needs.

**Rotation policy: by upstream major, not by file size.** When this document hits 1000 lines, delete old entries until it is under the limit again.

See [`.agents/docs/releases.md`](.agents/docs/releases.md) for the
matching release procedure.

---

## 6.0.0+underspire.89 — unified system scheduler (AS2) and TickerHandler removal

### Engine

New [`evennia/utils/systems.py`](evennia/utils/systems.py): the unified
System Scheduler, the engine's single answer to "run this every N seconds /
at time T". A **System** declares a cadence (`every(seconds)`,
`calendar(daily|weekly|monthly)` on UTC boundaries, or `every_tick`) and a
scope (`global_scope`, `online_puppets`, `all_entities(component=...)`), and
a `run(ctx)` body called once per fire on the reactor with `ctx.now`,
`ctx.dt` (real elapsed, system-level only) and the entity set
(`ctx.entities` live puppets gathered on-reactor / `ctx.entity_ids` plain
pks fetched off-reactor via `defer.in_thread`). A dedicated 1 Hz
`LoopingCall` driver (started/stopped by
[`service.py`](evennia/server/service.py) alongside the maintenance task)
checks cadences with an injected clock; fires never overlap (due-while-in-
flight skips and warns, escalating to an error after three consecutive
skips so a wedged system cannot hide as a warning trickle); the whole fire
chain runs under `maybeDeferred`, so a synchronous raise in entity
selection clears the in-flight marker instead of wedging the system; a
failing system logs its traceback and never kills the driver or peers. The
durable calendar store happens before state is consumed, so a store failure
retries the boundary next tick rather than recording a fire that never ran.
Last-run persistence is split by cadence: durable
(ServerConfig) for `calendar` — a boundary crossed during downtime fires
once on the next tick, never replays — in-memory for `every`, none for
`every_tick`. Discovery is a settings-declared module list
(`SYSTEM_MODULES`); each module must define `register_systems()` and
register at least one system, and a broken entry is a loud startup error.
Registration is declared-startup-only by design: there is no runtime
per-object timer API, and the module docstring documents where one-shots go
instead (`delay`, lazy expiry, the job queue). This is the systems-plus-
clock half of a future ECS; component storage is deliberately not built.

The write-behind attribute flush moved out of `server_maintenance` and is
now the engine's first registered system,
[`flush-attributes`](evennia/server/engine_systems.py) (scope `global`,
cadence `every(ATTRIBUTE_FLUSH_INTERVAL)`, default 60s), keeping the
consecutive-failure CRITICAL escalation. The shutdown path now stops the
driver and runs one final `flush_all_dirty()` in all modes — previously
nothing drained the write-behind cache at shutdown, so dirty rows survived
teardown only if a query barrier happened to fire during it. The query
barriers themselves are untouched.

### Deletions (AS2 tranche A)

TickerHandler and its whole surface: the per-object timer model ("tick this
mob every 15s") is superseded by systems that select their entities.
Removed: `evennia/scripts/tickerhandler.py`, `utils.repeat`/`utils.unrepeat`,
the `repeat`/`unrepeat` inputfuncs and the GMCP `Char.Repeat.Update`
mapping, the `@tickers` command, the save/restore calls in `service.py`,
vestigial `TICKER_HANDLER` hooks in `typeclasses/models.py`, the
`TICKER_HANDLER` flat-API entry, the `turnbattle` contrib wholesale
(its `tb_items` depended on TickerHandler; EvMenu-precedent deletion rather
than porting a contrib slated for removal), and the dedicated doc pages for
all of the above. The `red_button` tutorial's blink loop became a
self-rescheduling persistent `delay`. A new `@systems` builder command
([`CmdSystems`](evennia/commands/default/system.py)) replaces `@tickers`'
introspection role, listing every registered system with cadence, scope,
last fire, fire count and in-flight state. Tranche B (`Script.interval`
machinery removal) is deferred until the game's five interval scripts
migrate.

### Review hardening

An adversarial fleet review of the branch surfaced one real bug and a set
of robustness gaps, all fixed before release: `_select_online_puppets` read
the legacy `session.puppet` attribute this fork removed (the scope would
have returned `[]` forever) and now uses the canonical `get_puppet()`
accessor, with a real-selector test so the seam can't silently regress;
the overlap guard escalation, `maybeDeferred` fire chain and
store-before-consume ordering above; the flush system's CRITICAL line is
rate-limited past the threshold (every 10th failure) instead of flooding;
and `calendar()`/`all_entities()` docstrings now state the
boundary-forfeit-on-error, name-keyed persistence (rename = fresh prime +
orphaned row) and exact-path (no subclass) matching semantics.

### Tests

New [`test_systems.py`](evennia/utils/tests/test_systems.py) (54 tests):
cadence boundaries against an injected clock, late-tick re-anchoring,
calendar crossing/catch-up-by-one/no-replay/persistence for daily, weekly
and monthly, short-month clamping and year-wrap, store-failure retry,
`ctx.dt` across gaps, error isolation, the overlap guard on both the sync
and async (`all_entities` pending-id-query) paths with skip escalation,
the real online-puppets selector, registry introspection and duplicate
rejection, scope selection paths, settings-declared discovery failure
modes (including a raising game module not displacing engine systems),
idempotent reload, and the flush-attributes system (cadence from setting,
disable-at-zero, flush called, escalation, counter reset, CRITICAL
rate-limit, sibling isolation under driver). `CmdSystems` is covered in
the default-command tests. Full suite green; the lone pre-existing
`DirtyReactorAggregateError` when `test_server` runs before twisted-trial
tests reproduces on `.88` and is tracked separately.

### Migration notes

- `ATTRIBUTE_FLUSH_ON_MAINTENANCE` (bool) is retired; use
  `ATTRIBUTE_FLUSH_INTERVAL` (seconds, default 60, 0 disables).
  `ATTRIBUTE_FLUSH_METRICS_EVERY_N_TICKS` now counts flush fires.
- Any game-side `flush_all_dirty()` calls on the game's global tick should
  be deleted; the engine system owns flush cadence (tune the setting).
- `TICKER_HANDLER`, `utils.repeat`/`unrepeat`, the `repeat` inputfunc,
  `@tickers` and `contrib.game_systems.turnbattle` no longer exist.
- Declare game systems in `SYSTEM_MODULES`; the downstream migration of
  `global_tick` handlers, interval scripts and APScheduler jobs is specced
  in [`AS2-system-scheduler.md`](.agents/prompts/AS2-system-scheduler.md)
  section 5.

---

## 6.0.0+underspire.88 — idmapper partial-load correctness and queue-drop honesty

### Engine

The `.87` `from_db` fix stopped the recursive refresh but left a regression:
its fallback set `_loaded_location_id = None` whenever `db_location_id` was
absent from the row. Under idmapper a partial `.only()` query returns the
*cached, fully-loaded* instance, so a missing field means "no information",
not "reset tracking". The clobber degraded targeted contents-cache
invalidation to the full-reinit-all-objects fallback (plus a spurious
warning) on the next direct `db_location` save.
[`ObjectDB.from_db`](evennia/objects/models.py) now reads the value from the
row when present and only initializes `None` when nothing is already tracked.

[`BulkTickContext._apply_uncached`](evennia/utils/bulk_tick.py) no longer
loads uncached objects with `.only("id", "db_attrs")` + `bulk_update`. Partial
instantiation of an uncached idmapper model is unsupported (construction reads
deferred fields, and idmapper can never refresh a deferred field because the
refresh query resolves to the same cached instance without applying values),
and crashed with `KeyError: 'db_attrs'` in production. The write-back now reads
rows via `values_list` and writes them with a single `CASE`/`WHEN` `update()`,
never instantiating a model. The invariant is recorded in the
[engine decisions doc](.agents/docs/engine-architecture/decisions.md).

### Jobs

[`enqueue_job`](evennia/jobs/queue.py) returned a fresh job id even when a
Redis outage caused the record to be dropped, so callers could not distinguish
a queued job from a lost one. [`_enqueue_redis`](evennia/jobs/queue.py) now
returns a bool and `enqueue_job` returns `None` when the push did not happen,
matching its documented "id or None" contract. The outage logging cadence (the
`.86` state machine) is unchanged.

### Logs

[`prune_rotated_logs`](evennia/utils/logger.py) stat-ed each candidate file
outside its per-file error handling. The server and portal processes both
prune at startup, so a backup can vanish between `os.listdir` and
`os.path.getmtime`; the loser of that race raised an uncaught `OSError`. The
stat is now inside a `try/except OSError`.

### Tests

New [`test_bulk_tick.py`](evennia/utils/tests/test_bulk_tick.py) covers the
uncached write-back path cold (object evicted from the idmapper cache). The
`.87` `TestObjectDBFromDbPartialLoad` test, which asserted the clobbering
behavior and failed deterministically, is replaced with one asserting cached
partial loads preserve tracking. `prune_rotated_logs` gains full coverage
(retention, per-name `max_backups`, active-log safety, disabling, throttle,
mid-scan race), and `TestRedisBackoff` gains the enqueue-drop case.

### Migration notes

No downstream changes required. The `enqueue_job` return change is contract-
tightening: code that already handled `None` (the disabled/rejected cases)
needs nothing; code that assumed a truthy return now correctly learns when a
job was dropped during an outage.

---

## 6.0.0+underspire.87 — log retention and bulk-tick deferred-field fixes

### Engine

Evennia rotated `server.log` / `portal.log` at size/time but never deleted old
backups (`server.log.2026_06_10__N`), so error-loop storms could fill the disk.
[`prune_rotated_logs`](evennia/utils/logger.py) runs on server/portal startup
and after rotation; settings `LOG_ROTATED_RETENTION_DAYS`, `LOG_ROTATED_MAX_BACKUPS`,
and `LOG_ROTATED_PRUNE_MIN_INTERVAL` control retention (defaults: 14 days, 30
backups, 3600s between scans during rotation).

[`ObjectDB.from_db`](evennia/objects/models.py) no longer reads
`instance.db_location_id` when hydrating partial ORM rows. Deferred FK access
triggered `refresh_from_db` → recursive `from_db` during
[`BulkTickContext.gather_objectdb`](evennia/utils/bulk_tick.py) `.only("id",
"db_attrs")` loads, flooding logs with `RecursionError` every global tick.

[`gather_objectdb`](evennia/utils/bulk_tick.py) seeds `db_attrs` on cached
instances via `values_list` instead of the deferred descriptor, and reads
uncached rows the same way, avoiding `KeyError: 'db_attrs'` on partially loaded
idmapper cache entries.

### Migration notes

Games may override log retention in `server/conf/settings.py` (Underspire uses
7 days / 20 backups). No other downstream changes required.

---

## 6.0.0+underspire.86 — calm, honest Redis job-queue liveness logging

### Engine

The background job queue treated a Redis *connection* failure exactly like a
genuine per-job bug: [`_dequeue_redis`](evennia/jobs/queue.py) logged a full
`log_trace` stack every time the polled dequeue hit an unreachable Redis. On the
maintenance ticker that meant a brief Redis outage flooded `server.log` with one
traceback per poll (~360/hour). [`_enqueue_redis`](evennia/jobs/queue.py) had the
same exposure on the enqueue path.

Redis is an optional backend (the default `JOB_QUEUE_BACKEND`), so an outage and
a not-installed environment now share one availability state machine:

- **Never seen alive** (not installed, or down since boot): complained about
  exactly once, then silent.
- **Was alive, then lost:** logged on first loss, then re-stated at most once
  per 10 minutes while the outage persists.
- **Recovery** is detected by a real successful probe (PING / round-trip), not a
  timer, and logged once. Connection/availability errors are matched
  structurally (by exception module + name, plus `ImportError`) so the engine
  needn't import the optional `redis` package; genuinely unexpected errors keep
  their full `log_trace`. Per-job failure logging in the run loop is unchanged.

New [`check_redis_backend`](evennia/jobs/queue.py) performs an active `PING` and
drives the cadence. It is wired into
[`server_maintenance`](evennia/server/service.py) (the existing 60s
`LoopingCall`, which starts with `now=True`), so Redis is probed at boot and
every 60s thereafter: a boot-time status line is emitted, recovery is detected
within ~60s while the queue is idle, and an ongoing outage stays visible without
flooding.

### Tests

Added [`TestRedisBackoff`](evennia/jobs/tests.py): a redis-shaped `ConnectionError`
synthesized without the optional dependency, asserting one warning (not a
traceback) across 20 failed polls, the not-installed (`ImportError`) path, a
preserved `log_trace` for unexpected errors, boot-live logging, and the
recheck/heartbeat/recovery cadence under a controlled clock.

## 6.0.0+underspire.85 — delete the legacy EvMenu menu system

### Engine

The legacy `EvMenu` node-graph menu and its cmdset-based input capture are
removed. Under the action engine the menu's `EvMenuCmdSet` (the `CMD_NOMATCH` /
`CMD_NOINPUT` catch-all commands) was never merged into dispatch, so a line
typed into an open `EvMenu` reached the engine and the menu command never ran:
the whole node-graph menu had been silently non-functional. Branching
interactive flows are now native `@interactive` generator screens that
`yield` an [`evennia.actions.menus.MenuPrompt`](evennia/actions/menus.py) (plus
the `confirm` / `paginate` combinators added in `.84`).

- Deleted `EvMenu`, `EvMenuCmdSet`, `CmdEvMenuNode`, `list_node`, and the
  menu-template parser. The [`evennia/utils/evmenu.py`](evennia/utils/evmenu.py)
  module no longer exists.
- The two survivors, `get_input` and `ask_yes_no` (StateProvider-backed since
  `.73`), moved into [`evennia.actions.menus`](evennia/actions/menus.py) beside
  the `GetInputState` / `YesNoState` they install, and are exported from
  `evennia.actions`.
- Removed the OLC prototype builder (`@olc`, `@spawn/menu`, `@spawn/edit`) from
  [`CmdSpawn`](evennia/commands/default/building.py) and deleted
  `evennia/prototypes/menus.py`. `@spawn` and its non-menu switches
  (`list`/`search`/`show`/`raw`/`save`/`update`/`delete`) are unchanged;
  prototypes are authored as `.py`/YAML.
- Dropped the `evennia.EvMenu` flat-API export.

### Engine (metrics)

Committed the attribute-flush observability path to the Prometheus surface
(`server/prometheus_metrics.py`, `/metrics`,
`ENGINE_PROMETHEUS_METRICS_ENABLED`). `flush_all_dirty` now calls
`record_attribute_flush` directly: the redundant outer `except: pass` is gone,
so a genuine metrics bug surfaces instead of vanishing (the metrics function is
safe-by-construction — init gate, None-guards, int coercion). The
`record_attribute_flush_stats` forwarder is collapsed, and the previously-dead
`maybe_warn_pending_dirty` is wired into `server_maintenance`
(off by default; `ATTRIBUTE_FLUSH_PENDING_WARN_THRESHOLD = 0`).

### Contrib

Deleted the nine contribs that depended on the `EvMenu` class:
`character_creator`, `evscaperoom`, `fieldfill`, `tree_select`, `menu_login`,
`ingame_reports`, `talking_npc`, `evadventure`, `tutorial_world`. All were
already non-functional under the action engine.

### Docs

Removed `Components/EvMenu.md`, the entire Beginner Tutorial (built on the
deleted `evadventure` / `tutorial_world` contribs and stock cmdset dispatch),
the nine contribs' narrative + autodoc pages, and the EvMenu-based
NPC-Merchants howto, pruning every inbound cross-link and toctree entry.

### Tests

Rewrote 84 path-string `@patch("a.b.c")` sites across 22 test files to
`patch.object(container, "c")`, which fails loudly when a patched target is
renamed or moved instead of silently no-opping against unpatched code. ~40 sites
targeting builtins, stdlib/third-party containers, Django managers, and runtime
singletons were intentionally left as path-strings (documented in
`.agents/prompts/F17`, with the dead `COMMAND_DEFAULT_CLASS` decorators carved
out to `F23`).

### Migration notes

- `from evennia.utils.evmenu import get_input, ask_yes_no` becomes
  `from evennia.actions.menus import get_input, ask_yes_no`. The
  `evennia.utils.evmenu` module and the `EvMenu` class are gone; convert any
  node-graph menu to an `@interactive` generator that yields `MenuPrompt`.
- Games using any deleted contrib or the `@olc` / `@spawn/menu` prototype
  builder must remove those references. See
  [`.agents/prompts/CM1-input-capture-migration.md`](.agents/prompts/CM1-input-capture-migration.md)
  for the full migration context.

---

## 6.0.0+underspire.84 — generator-flow menu combinators

> Tagged `underspire.84` but shipped without a version-file bump or changelog
> entry; this entry backfills it (the bump lands in `.85`).

### Engine

Added `confirm()` (a yes/no sub-flow over `MenuPrompt`, used as
`ok = yield from confirm(...)`) and `paginate()` (pure list slicing) to
[`evennia.actions.menus`](evennia/actions/menus.py), exported from
`evennia.actions` — the generic `@interactive` flow combinators native menu
screens use. Deleted the engine-native `EvMenuState` node-graph router state;
no engine consumer remained once the game moved its menus to native generator
screens.

---

## 6.0.0+underspire.83 — phrase action switches on multi-word verbs

### Engine

The action parser now recognizes slash switches on any word in a matched
multi-word verb phrase, not only the first token. This fixes inputs such as
`deck deal/faceup prismodal 2`, which should resolve as the `deck deal` action
with the `faceup` switch instead of falling through as an unknown
`deck deal/faceup` verb.

Argument tokens outside the matched verb span keep their original slash text,
so commands can still accept values like `north/east` without the parser
mistaking them for switches.

### Tests

- Added a parser regression test for a switch on the second word of a phrase
  verb, including an argument containing a slash.

## 6.0.0+underspire.82 — EvEditor input-capture on the action engine

### Engine

EvEditor ([`evennia/utils/eveditor.py`](evennia/utils/eveditor.py)) no longer
captures input through a cmdset. Following the `.77` EvMore migration, it now
installs an engine-native `EvEditorState(StateProvider)` (modeled on
`EvMoreState`) that seizes the next input line through the action engine and
routes it to the editor. This matters because the `.73` engine bridge is the
sole player-input dispatch path and **never merges the editor's cmdset** — so
under the engine the line-editor was silently broken: typed lines went to the
engine's NoMatch/NoInput handling and never reached the editor. Its existing
tests didn't catch this because they call the command `func`s directly rather
than routing through `cmdhandler` (green ≠ working).

What changed:

- `EvEditorState` replaces the `caller.cmdset.add(EvEditorCmdSet)` registration;
  install via `capture_holder` + `enter_state` in `EvEditor.__init__`, removed
  via `exit_state` in `EvEditor.quit()`.
- `CmdSaveYesNo` / `SaveYesNoCmdSet` are deleted. The save-before-quit prompt
  (`:q` on an unsaved buffer) is now an in-state `_save_confirm` sub-mode the
  next captured line resolves, preserving the legacy "unknown answer defaults to
  yes" behavior.
- `CmdEditorGroup` / `CmdLineInput` / `EvEditorCmdSet` are kept, but the cmdset
  is now **match-only**: `EvEditor.handle_input` resolves a captured line with
  `cmdparser.build_matches` against an `EvEditorCmdSet` instance (pure string
  matching, never merged into dispatch) and drives the matched command's
  `parse`/`func`. The ~360-line command body is untouched.
- Capture is **session-agnostic** (the state's session is left `None`). EvEditor
  output is broadcast to all of the caller's sessions, so input is scoped to the
  focus *body* via `capture_holder` only; there is no `session=` API change.

Public `EvEditor(...)` signature and behavior are unchanged.

### Engine — persistent-capture rehydration seam

States are non-persistent by design, so a `persistent=True` editor (the stock
default for the help/attr/`@py` editors) lost its live capture across a
`@reload`. New shared seam in
[`evennia/actions/state.py`](evennia/actions/state.py): a declarative
`_CAPTURE_REHYDRATORS` table + `rehydrate_captures(holder)`, called from
`at_post_load` to re-install any persisted capture whose marker Attribute is
present. The editor's rebuild data already survives in Attributes; the seam
re-points the lost *trigger* through `eveditor.rehydrate`, which wraps the
existing `_load_editor`.

Notes for the next consumer (EvMenu migration):

- The table ships with **only the eveditor row**. EvMenu still uses working
  cmdset-reimport persistence; its row is added by *its* migration (adding it now
  would `log_trace` on every menu reload).
- The seam is wired into `LifecycleMixin.at_post_load`
  ([`evennia/objects/mixins/lifecycle.py`](evennia/objects/mixins/lifecycle.py))
  for objects and `DefaultAccount.at_post_load`
  ([`evennia/accounts/accounts.py`](evennia/accounts/accounts.py)) for accounts,
  **not** `TypedObject.at_post_load`: those overrides shadow the base hook
  without `super()` (a pre-existing fact — `apply_schema_migrations` likewise
  never runs for objects via `at_post_load`; see
  [`.agents/prompts/F22-at-post-load-schema-migration-trap.md`](.agents/prompts/F22-at-post-load-schema-migration-trap.md)).
- The marker is probed with the backend's cache-free `query_key`, not
  `attributes.has`: this runs on every object load, and `has` would cache a
  negative marker lookup on every captureless object. On the default JSONB
  backend `query_key` is a membership check against the already-loaded row, so
  the gate costs no extra query.

### Tests

[`evennia/utils/tests/test_eveditor.py`](evennia/utils/tests/test_eveditor.py)
gains engine-routed coverage that drives input the way dispatch does, including a
test through the **real `cmdhandler` bridge** (`execute_cmd`), a session-scope
test, install/quit lifecycle, and a `persistent=True` reload test (drop `ndb`,
call `at_post_load`, assert input is captured again). The existing func-direct
tests still pass.

### Migration notes

No downstream changes required: the public `EvEditor` / `EvMore` API is
unchanged. Games that subclassed or imported `CmdSaveYesNo` / `SaveYesNoCmdSet`
(neither is a documented extension point) must drop those references.

## 6.0.0+underspire.81 — check_database error exits use a non-zero status

### Engine

The error-exit paths in `check_database`
([`evennia/server/evennia_launcher.py`](evennia/server/evennia_launcher.py))
called bare `sys.exit()`, which exits with status **0**. A deploy/CI runner
that gates on exit code therefore saw a *passing* run even though the launcher
had just printed a fatal database error: the build shipped on top of an
unreachable or broken database. (Introduced for the unreachable case in `.80`;
the schema-error paths had carried it latently.)

All three error exits now use `sys.exit(1)`:
- `ERROR_DATABASE_UNREACHABLE` (connectivity failure, `.80`)
- `ERROR_DATABASE` (schema missing/broken on the `AccountDB` lookup)
- the non-interactive "could not create Account#1 superuser" failure

The interactive "user answered N to the superuser-move prompt" exit is left at
status 0: that is an operator-chosen abort, not an error.

### Migration

None. Behavior delta: `evennia` commands that fail on a database error now
return a non-zero exit status, so deploy/CI pipelines correctly mark the run as
failed instead of passing on a printed error.

## 6.0.0+underspire.80 — unreachable DB is fatal even under always_return

### Engine

`check_database` in [`evennia/server/evennia_launcher.py`](evennia/server/evennia_launcher.py)
previously returned `False` quietly on a connectivity failure when called with
`always_return=True`. The only such caller is the `migrate` branch of `main`
([line 2402](evennia/server/evennia_launcher.py)), which interprets `False` as
"DB not set up yet" and falls through to running `evennia migrate`. Against an
*unreachable* DB that fall-through reused the already-severed connection and
died with a misleading `InterfaceError: connection already closed` deep inside
a migration module, instead of the clear connectivity message.

The reachability `OperationalError` now prints `ERROR_DATABASE_UNREACHABLE` and
exits **regardless of `always_return`**. An unreachable database is never
migrate-fixable, so falling through to a migrate attempt is always wrong. The
"not set up yet" case (`ProgrammingError` on the `AccountDB` lookup) still
honors `always_return` and returns `False`, so first-run `evennia migrate`
against a reachable-but-empty DB is unchanged.

This is the third of three `.78`–`.80` commits hardening the launcher's
database-error reporting; together they ensure every entry point (`start`,
plain commands, and `migrate`) reports a dead/unreachable database as a clear
connectivity error rather than a raw traceback.

### Migration

None. No settings or API change. `always_return`'s documented contract is
narrowed: it suppresses output only for the not-set-up case, never for a
connectivity failure.

## 6.0.0+underspire.79 — launcher reachability probe runs a real query (pooler-aware)

### Engine

Corrects the `.78` reachability probe in
[`evennia/server/evennia_launcher.py`](evennia/server/evennia_launcher.py).
`.78` probed with `connection.ensure_connection()`, which only performs the
transport + auth handshake. A connection pooler (e.g. PgBouncer) accepts that
handshake immediately and only dials its Postgres backend when a query needs a
server, so a dead/unreachable backend slipped past the probe and the failure
still escaped as a raw `OperationalError` traceback at the first real query.

The probe is now that first real query itself: `get_table_list` (a `SELECT`
against `pg_catalog`) is wrapped in the `OperationalError` handler. This forces
the pooler to bind a backend, so backend-down surfaces as
`ERROR_DATABASE_UNREACHABLE` (or a quiet `False` under `always_return=True`)
rather than a traceback. The redundant `ensure_connection()` call is removed.
`ProgrammingError` and other schema-shaped failures still fall through to the
existing `ERROR_DATABASE` + `evennia migrate` path.

### Migration

None. No settings or API change.

## 6.0.0+underspire.78 — launcher reports DB connectivity failures as connectivity

### Engine

`check_database` in [`evennia/server/evennia_launcher.py`](evennia/server/evennia_launcher.py)
now probes reachability with `connection.ensure_connection()` before any
schema introspection. Previously the first DB touch
(`connection.introspection.get_table_list(connection.cursor())`) sat outside
the error handler, so a connection-level failure escaped as a raw
`OperationalError` traceback and also violated the `always_return=True`
contract (documented to return `True`/`False` quietly on critical errors
rather than raise).

The probe catches `OperationalError` specifically. With `always_return=True`
it returns `False`; otherwise it prints the new `ERROR_DATABASE_UNREACHABLE`
message and exits. `ProgrammingError` and other schema-shaped failures still
fall through to the existing `ERROR_DATABASE` + `evennia migrate` path, so the
"can't connect" and "schema not set up" cases stay distinct.

`ERROR_DATABASE_UNREACHABLE` states outright that the failure is connectivity
(not a missing/out-of-date schema, so `migrate` will not help) and names the
common causes: server down/unreachable, a pooler up but its backend down, bad
credentials, or a session-init statement the server/pooler rejects. The last
case points at `ENGINE_DATABASE_STATEMENT_TIMEOUT_MS` and
[`evennia/server/database_postgres.py`](evennia/server/database_postgres.py),
whose `connection_created` receiver issues a `SET statement_timeout` on each
new connection: a fragile spot behind PgBouncer transaction-pool mode (see
that module's own caveat).

### Migration

None. No settings or API change. The behavior delta is limited to clearer
output and `always_return` being honored on connection failure.

## 6.0.0+underspire.77 — EvMore→StateProvider + focus/session-scoped captures; account rename db_key sync; retire bridge-dead default commands

EvMore's interactive paging input no longer rides a cmdset. The `.73`
deprecation flagged it as bypassed by the engine bridge; it now captures input
through `EvMoreState`, an engine-native `StateProvider` modeled on
`GetInputState`/`YesNoState`. No public signature change: `EvMore(...)` and
`evmore.msg(...)` callers are unaffected.

This release also fixes a latent holder/session-scoping bug shared by all
input-capture states (surfaced migrating EvMore). Captures now install on the
**focus body** the engine actually reads (not the raw caller, which diverges
from the focus when e.g. an account-level caller opens a capture while a
character is puppeted), and the capture only fires for input from the **session**
the prompt was shown to (so in multisession a second session driving the same or
another body is not hijacked).

### Engine — EvMore

- [`evennia/utils/evmore.py`](evennia/utils/evmore.py): deleted `CmdMore`,
  `CmdMoreExit`, and `CmdSetMore`; added `EvMoreState`. `EvMore.start()` now
  `enter_state`s the pager state (exit-before-enter to avoid stacking) instead of
  `cmdset.add(CmdSetMore)`, and `page_quit()` `exit_state`s it instead of
  `cmdset.remove`. Recognized paging keys (`n`/`p`/`t`/`e`/`q`/...) drive the
  pager; an empty line pages forward; any other line exits the pager and is
  re-dispatched normally (the legacy `CMD_NOMATCH` behavior). Behavior change vs
  legacy: `quit` now quits the pager (the old `CmdMore` left `quit` out of its
  quit tuple, so it paged *forward*); `q`/`a`/`abort` quit as before.
- [`evennia/utils/tests/test_evmore.py`](evennia/utils/tests/test_evmore.py):
  new. Routes input through the real `RuleEngine` (`ENGINE.dispatch`) with the
  state active, not by calling command funcs directly, so it proves capture
  survives the bridge's before/carry_out REDIRECT loop. Covers paging keys,
  empty-line next, quit, unknown-command exit + re-fire, install/remove
  lifecycle through a real `EvMore`, and session-scoped capture (a different
  session's line is not seized).

### Engine — capture-state holder + session scope

- [`evennia/actions/state.py`](evennia/actions/state.py): new `capture_holder(caller,
  session)` returns `Actor.from_caller(caller, session=session).holder` — the
  body the next line's actor will read. Capture utilities install/exit on this,
  not the raw caller. Body-scoped; no `state_objects` aggregation or engine
  semantics change.
- [`evennia/actions/menus.py`](evennia/actions/menus.py): new `session_mismatch(state_session,
  actor)` helper; `InputCaptureState`/`GetInputState`/`YesNoState` capture rules
  now `PASS` when the line's session isn't the one the prompt was opened on
  (`None` session = agnostic, the prior behavior). `InputCaptureState` gained a
  `session` arg. Scoping applies only to captures whose output is
  session-targeted: `@interactive` (`_get_input_deferred` in `engine.py`)
  broadcasts its prompt to all sessions, so it leaves the session `None` — any of
  a player's windows may answer (correct under `MULTISESSION_MODE` 1; distinct
  puppets are already isolated by body).
- [`evennia/utils/evmenu.py`](evennia/utils/evmenu.py): `get_input` / `ask_yes_no`
  install via `capture_holder` instead of the raw caller.
- [`evennia/utils/evmore.py`](evennia/utils/evmore.py): `EvMore` resolves
  `self._holder = capture_holder(caller, session)` once and enters/exits the
  state on it; `EvMoreState.capture_input` session-guards via `session_mismatch`.

### Deprecation — still pending

- **EvEditor** ([`utils/eveditor.py`](evennia/utils/eveditor.py)) and any custom
  `CMD_NOMATCH`/`CMD_NOINPUT` overrides remain on the bypassed cmdset-capture
  path and must migrate before the legacy cmdset dispatch path is removed. See
  the `.73` deprecation note.

### Engine — account rename

Account renames now keep the `db_key` identity column in sync and fire the rename
signal, and three default commands that the action-engine bridge had already made
unreachable are removed. No schema changes.

- [`evennia/accounts/models.py`](evennia/accounts/models.py): `AccountDB`'s
  `key`/`name`/`username` property setter (`__username_set`) now also writes the
  `db_key` column in the same save, so a rename no longer leaves `db_key` stranded
  at the creation-time value. The setter already fired
  [`SIGNAL_ACCOUNT_POST_RENAME`](evennia/server/signals.py); routing renames
  through the property is the supported path.
- [`evennia/commands/default/building.py`](evennia/commands/default/building.py):
  `CmdName`'s account branch (`@name *acct=new`) now sets `obj.key` instead of
  writing `obj.username` directly, so the db_key sync and signal fire.

### Engine — remove action-bridge-dead default commands

Since the action engine became the sole input dispatcher, a legacy command that
delegated to another via an in-process `self.execute_cmd("@othercmd ...")` string
re-dispatch can no longer resolve that verb (the bridge owns input and the target
is not a registered action). These commands were already broken for normal input
and are removed rather than left as dead surface.

- [`evennia/commands/default/building.py`](evennia/commands/default/building.py):
  removed `CmdMvAttr` (delegated to `@cpattr`) and `CmdTunnel` (delegated to
  `@dig`), plus their `__all__` entries and the
  [`cmdset_character.py`](evennia/commands/default/cmdset_character.py)
  registrations.
- `evennia/commands/default/batchprocess.py` **deleted** —
  `CmdBatchCommands`/`CmdBatchCode` ran each `.ev` line via
  `caller.execute_cmd(line)` (bridge-dead in core), and their interactive mode was
  built on the cmdset-capture machinery now being retired. The `.ev` file *parser*
  ([`evennia/utils/batchprocessors.py`](evennia/utils/batchprocessors.py)) is
  retained. Removed the flat-API registration in
  [`evennia/__init__.py`](evennia/__init__.py) and the test-base patch in
  [`evennia/utils/test_resources.py`](evennia/utils/test_resources.py).

### Migration notes

- Downstream code that wraps or subclasses
  `evennia.commands.default.batchprocess` (`CmdBatchCommands`/`CmdBatchCode`) must
  rehome the batch processor into the game; the `.ev` parser in
  `evennia.utils.batchprocessors` is unaffected.
- Account renames must go through the `account.key`/`account.name` property, not a
  raw `account.username =` write, to pick up the `db_key` sync and
  `SIGNAL_ACCOUNT_POST_RENAME`.

### Tests

- [`evennia/accounts/tests.py`](evennia/accounts/tests.py): new
  `test_rename_syncs_db_key_and_fires_signal` (rename via the property asserts
  `db_key`/`username` aligned and persisted, and the signal fired with old/new
  names).
- [`evennia/commands/default/tests.py`](evennia/commands/default/tests.py):
  `test_name` now asserts `db_key` syncs on `@name *acct`; removed `test_tunnel`,
  `test_tunnel_exit_typeclass`, `TestBatchProcess`, and the `CmdMvAttr` assertions
  in `test_attribute_commands`.

---

## 6.0.0+underspire.76 — mux-style argument parser for the action engine

New helper that gives the action engine the lhs/rhs/objdef structure the legacy
command classes derived in `Command.parse`. The action parser strips the verb and
`/switches`; this splits the remaining free-text args so actions hosting former
mux/objmanip commands can carry the same structured arguments. No schema changes.

### Engine — `evennia/actions/muxargs.py` (new)

- [`evennia/actions/muxargs.py`](evennia/actions/muxargs.py): adds `MuxArgs`
  (a dataclass holding `args`/`arglist`/`lhs`/`rhs`/`lhslist`/`rhslist`/
  `lhs_objs`/`rhs_objs`) and `mux_parse(raw_args, rhs_split="=")`. The lhs/rhs
  split mirrors [`Command.parse`](evennia/commands/command.py); each
  comma-separated objdef is broken down as `name;alias;alias:option` exactly as
  [`ObjManipCommand.parse`](evennia/commands/default/building.py) does. The
  `rhs_split` argument accepts a single delimiter or an iterable tried in order
  (first present wins), matching `Command.rhs_split`. Switch extraction is
  intentionally not duplicated: the action parser owns the verb and switches.
- Named `muxargs` rather than `argparse` to avoid shadowing the stdlib module.
- [`evennia/actions/__init__.py`](evennia/actions/__init__.py): `MuxArgs` and
  `mux_parse` flat-exported.

### Tests

- [`evennia/actions/tests/test_muxargs.py`](evennia/actions/tests/test_muxargs.py):
  16 no-DB unit tests covering empty/`None` input, the no-rhs case, first-`=`-only
  split, comma lists, the empty-rhs-vs-no-rhs distinction, objdef
  alias/option/rightmost-colon parsing, and custom/iterable `rhs_split`
  delimiters.

---

## 6.0.0+underspire.75 — remove dead tag-search wrappers

Parallel cleanup to `.74`'s attribute-wrapper removal, for the tag-search facade.
The flat tag API kept four model-specific wrappers for symmetry, but three had no
callers anywhere. No schema changes.

### Engine — `search.py` wrappers removed

- [`utils/search.py`](evennia/utils/search.py): deleted `search_account_tag`,
  `search_script_tag`, and `search_channel_tag`, and dropped them from `__all__`.
  None were re-exported in the flat `evennia.*` API (only `search_tag` is).
  `search_account_tag`/`search_channel_tag` had zero callers; `search_script_tag`
  was referenced only by its own tests.
- `search_object_by_tag` (aliased as `search_tag`) stays: it backs the common
  `ObjectDB` case and has live engine callers. Engine code that searches tags on
  other models (`Msg`, `DbPrototype`) continues to use the managers directly,
  which is the correct path for non-`ObjectDB` reverse lookups.

### Tests

- [`utils/tests/test_search.py`](evennia/utils/tests/test_search.py): removed the
  four `search_script_tag` tests covering the deleted wrapper.

---

## 6.0.0+underspire.74 — remove dead attribute-search wrappers, force-gate the rest

Attribute search forces a process-wide `flush_all_dirty()` on every call. This
release removes the dead convenience wrappers and makes the remaining attribute
searches refuse to run from game logic: reverse lookups must be modelled as
indexed Tags. No schema changes.

### Engine — `search.py` wrappers removed

The downstream game migrated every reverse lookup (Discord link id/token,
tailoring web-edit tokens, web-write draft-by-title) to indexed tags, so these
wrappers had no remaining callers anywhere in the engine, contribs, or the game.

- [`utils/search.py`](evennia/utils/search.py): deleted `search_object_attribute`,
  `search_account_attribute`, `search_script_attribute`, and
  `search_channel_attribute`. None were in `__all__`.
- [`typeclasses/managers.py`](evennia/typeclasses/managers.py):
  `TypedObjectManager.get_by_attribute` deleted. Its only callers were the four
  wrappers above (the account/script/channel managers expose no `attribute_name`
  search path), so it was fully dead once they were gone.

### Engine — remaining attribute search is force-gated

The only surviving attribute-search primitives now refuse to run unless the
caller explicitly opts in with `force=True`, which exists for tests, cleanup,
and migrations only. Game logic must not trigger the flush.

- [`objects/manager.py`](evennia/objects/manager.py):
  `ObjectDB.objects.get_objs_with_attr_value` and `get_objs_with_attr` gained a
  `force=False` argument; calling without it raises `RuntimeError` pointing to
  Tags (`_require_attribute_search_force`).
- The `search_object(attribute_name=…, attribute_value=…)` /
  `DefaultObject.search(attribute_name=…)` path falls through to
  `get_objs_with_attr_value` **without** `force`, so it now raises in game logic.
  (A `db_<field>` property match still returns first and is unaffected.)
- `get_objs_with_attr` remains separately deprecated (unindexed full scan) on top
  of the new gate.

### Migration notes

- `search.search_<type>_attribute(...)` and `Manager.get_by_attribute(...)` are
  gone. Use indexed Tags for reverse lookups.
- `search_object(attribute_name=…)` / `.search(attribute_name=…)` now raise
  `RuntimeError` when they reach the attribute scan. Migrate those lookups to
  Tags, or to a real `db_<field>` property search where applicable.
- Maintenance code that genuinely needs the scan calls the manager method
  directly with `force=True`.

### Tests

- [`utils/tests/test_search.py`](evennia/utils/tests/test_search.py): dropped the
  four wrapper tests (`test_search_object_attribute[_wrong]`,
  `test_search_script_attribute[_wrong]`) and their imports.
- [`objects/tests/test_objects.py`](evennia/objects/tests/test_objects.py): added
  `test_get_objs_with_attr_value_force_gated` and
  `test_search_object_attribute_name_penalized`; updated `test_get_objs_with_attr`
  to assert the gate and pass `force=True`. `evennia.objects` + search suites pass.

## 6.0.0+underspire.73 — action engine is the sole player-input dispatch path

Makes the CM1 action engine unconditional and starts the one-version deprecation
clock on the legacy cmdset dispatch path. No schema changes.

### Engine — `ACTION_ENGINE_ENABLED` gate removed

The setting is deleted and every site that read it is now unconditional, so the
engine bridge owns all player input and there is no flag to turn it off:

- [`settings_default.py`](evennia/settings_default.py): `ACTION_ENGINE_ENABLED`
  removed (its stale `HELP_INDEX_ACTIONS` comment fixed).
- [`commands/default/help.py`](evennia/commands/default/help.py): `CmdHelp.collect_topics`
  and `CmdHelp.func` use the action-registry/`render_help` path unconditionally;
  ~220 lines of dead legacy-cmdset help body deleted.
- Stale doc comments updated in [`help/catalog.py`](evennia/help/catalog.py),
  [`help/__init__.py`](evennia/help/__init__.py), and
  [`actions/tests/test_dispatch.py`](evennia/actions/tests/test_dispatch.py).

### Engine — `get_input` / `ask_yes_no` are engine-native states

[`utils/evmenu.py`](evennia/utils/evmenu.py)'s `get_input` and `ask_yes_no` no
longer install `InputCmdSet` / `YesNoQuestionCmdSet` (which the engine bridge
never routed to, leaving the prompts dead). They now install
`GetInputState` / `YesNoState`, new `StateProvider`s in
[`actions/menus.py`](evennia/actions/menus.py) modeled on `InputCaptureState`, so
the engine routes the reply line. The four legacy cmdset classes
(`CmdGetInput`, `InputCmdSet`, `CmdYesNoQuestion`, `YesNoQuestionCmdSet`) and the
`_Prompt` holder are deleted. **Fixes** `@tasks`/`@delays` confirmation prompts
and any `get_input`/`ask_yes_no`/`@interactive` flow under the engine.

### Engine — `cmdobj=` injection bypasses the bridge

[`commands/cmdhandler.py`](evennia/commands/cmdhandler.py): the action-engine
bridge now runs only when `cmdobj is None`. A `cmdobj=`-injected Command (login
`connect`, contrib menu commands) goes straight to the legacy command-run path so
its `func` actually executes. **Fixes** issue 2627: the crash-in-`connect` error
log masks the password again (`'connect johnny ***********'`), since the command
once more reaches the error path that masks it.

### Tests

- [`commands/default/tests.py`](evennia/commands/default/tests.py): `TestCmdTasks`
  (×15) green via the `YesNoState` migration.
- [`commands/tests.py`](evennia/commands/tests.py): `TestIssue2627` green via the
  `cmdobj=` bypass. `TestCmdsetMergeErrorSignal` **deleted** — `on_cmdset_merge_error`
  has no producer on the engine input path (legacy-only signal). `TestPosePassthroughIntegration`
  **rewritten** to the engine layer: it asserts the production parser routes `.`/`,`
  to the native `Pose` action verbatim and an unregistered leading-punct (`;`) reaches
  a `NoMatchRules` provider with `raw_string` intact (replacing the legacy cmdset
  `CMD_NOMATCH` fixture).

### Deprecation — legacy cmdset dispatch + cmdset input-capture (removed next release)

The legacy cmdset merge/match block in `cmdhandler` is now reachable only by
`cmdobj=` injection and will be **removed in the next release**. Subsystems that
still capture input via cmdset `CMD_NOMATCH`/`CMD_NOINPUT` commands are bypassed
by the engine bridge and must migrate to engine `StateProvider`s (model them on
`GetInputState`/`YesNoState`): **EvMore** paging ([`utils/evmore.py`](evennia/utils/evmore.py)),
**EvEditor** ([`utils/eveditor.py`](evennia/utils/eveditor.py)), and any custom
`CMD_NOMATCH` overrides. Their existing tests pass only because they call the
command `func` directly, not through `cmdhandler`, so green tests do not prove the
routing survives the bridge.

### Migration notes

- None (no schema change). **Downstream:** remove any game-side compatibility
  bridge that re-plugged unported verbs or input-capture cmdsets into the engine.
  This is the last release before the legacy cmdset dispatch path is removed.

---

## 6.0.0+underspire.72 — finish the has_account liveness audit; delete the I1 reconcile scaffolding

Completes the `.71` API-honesty work and lands the deprecation-cycle cleanup that
`.71` set up. No schema changes.

### Engine — `has_account` liveness-reader audit (F10)

`.71` flipped `has_account`/`obj.account` from "currently driven" to "durable
owner" and added `is_puppeted`/`puppeteer`, but switched only the *permission*
readers. This release audits the remaining engine *liveness* readers per-site and
moves the ones that meant "is someone playing this body" onto the driver:

- [`objects/mixins/movement.py`](evennia/objects/mixins/movement.py): the
  location-destroyed and create-into-inventory notifications gate on
  `is_puppeted` (notify a live driver), not ownership.
- [`commands/default/building.py`](evennia/commands/default/building.py):
  `@teleport/tonone` refuses on `is_puppeted` and names the `puppeteer` — **fixes
  a `.71` regression** where an owned-but-offline character could no longer be
  teleported to `None`.
- [`commands/default/admin.py`](evennia/commands/default/admin.py): `@pemit/@cemit`
  `accounts_only` emits to live-driven bodies (`is_puppeted`).
- [`typeclasses/attributes.py`](evennia/typeclasses/attributes.py): `NickHandler`
  merges the **driver's** account nicks (`puppeteer`), not the owner's — **fixes
  possession**: a staff-driven NPC expands the staff's input nicks.
- Deliberately kept on ownership (correct as "owned"): `@examine`'s account-info
  block ([`building.py`](evennia/commands/default/building.py)) and the
  `has_account` lockfunc ([`locks/lockfuncs.py`](evennia/locks/lockfuncs.py)).
- Contrib `has_account` readers (barter, tutorial_world) are a noted follow-on,
  out of scope for this engine pass.

### Engine — I1 reconcile scaffolding deleted

After a confirmed `created: 0` reconcile pass in production, the one-time I1
ownership backfill is removed from the engine
([`accounts/models.py`](evennia/accounts/models.py)): `reconcile_ownership`,
`reconcile_account`, `ensure_playable`, `_identity_ids_for_account`, the
`_PID_LOCK_RE` regex (and the now-unused `import re`), plus the temporary
`@verify-reconcile` command ([`commands/default/admin.py`](evennia/commands/default/admin.py))
and its two cmdset registrations. The game owns its own `at_server_start` backfill
now. (Tracked in `.agents/prompts/F21`; the broader scaffolding sweep remains.)

### API / semantics — deferred review findings closed as by-design

The fleet review's two remaining API findings recommend deprecation shims; both
are resolved **by design**, consistent with the fork's no-back-compat-shims policy
(set at `.65`, restated at `.71`): the renamed membership hooks
(`at_post_add_character`/`at_post_remove_character` → `at_character_added`/
`at_character_removed`) and the removed `session.puppet`/`session.puid` (use
`get_puppet()`) stay broken without an alias.

### Migration notes

- None (no schema change). **Deploy coordination:** the engine no longer defines
  `ControlBinding.reconcile_ownership`; deploy this together with the game change
  that drops its `at_server_start` reconcile call, or the game's startup will
  `AttributeError`.

### Tests

- [`accounts/tests.py`](evennia/accounts/tests.py): `test_nickreplace_uses_driver_account_nicks`
  (possession uses the driver's nicks).
- [`commands/default/tests.py`](evennia/commands/default/tests.py):
  `test_teleport_tonone_offline_owned_allowed` (the regression fix; the existing
  puppeted-refused case still holds).
- [`actions/tests/test_control_binding.py`](evennia/actions/tests/test_control_binding.py):
  dropped `test_ensure_playable_sets_ownership_only` (tested deleted API).

---

## 6.0.0+underspire.71 — ControlBinding single-source rework; permissions follow the driver

Follow-up to the fleet review of the I1 ControlBinding subsystem. The control
graph is reworked so that each fact has exactly one home, and the permission
system is corrected to key off the live driver rather than the durable owner.
The control graph now distinguishes three independent facts that the `.65`
design conflated:

- **Ownership** ("who a character belongs to"): the character's
  `ObjectDB.db_account`. This alone defines an account's playable roster.
- **Controller** (the account a control graph's floor rests on — where `focus`
  returns when nothing is pushed): `ControlBinding.db_account`. Equal to the
  owner for a player on their own character, but different when staff possess
  an NPC or another's body.
- **Live driving** (who is actually playing a body right now):
  `ObjectDB.sessions`, surfaced as `is_puppeted` / the new `puppeteer`.

### Engine — control graph

- [`evennia/accounts/models.py`](evennia/accounts/models.py): `ControlBinding`
  is now a `SharedMemoryModel` (idmapper-backed). Every session driving the same
  binding resolves the one shared in-memory instance, so a co-session's
  push/pop is seen immediately with no per-read query — this *deletes* the
  session-level binding cache plus its `db_generation` poll and `refresh_from_db`
  from [`serversession.py`](evennia/server/serversession.py) (the staleness
  guard is now structural). `session.binding` resolves via `objects.get(pk=...)`
  (a cache hit) and `for_identity` caches a freshly created binding so the
  creator's instance is canonical.
- **F2 focus stack.** The controller floor is no longer stored. `db_focus_stack`
  holds only the bodies *above* the floor; `focus` returns `db_account` (the
  controller) on an empty stack. `_ensure_floor` is removed; `collapse_to_floor`
  replaces collapsing to a stored account entry. This removes the last duplicated
  copy of the controller.
- **`for_identity` repoints the controller, not ownership.** Attaching/puppeting
  (re)points `ControlBinding.db_account` at the current driver (so possession and
  takeover work) and never touches the identity's `ObjectDB.db_account`. The
  `.67`/`.70` `for_identity`-transfers-ownership behaviour and the reconcile
  `identity ← binding` resync are removed: the resync corrupted ownership for any
  possessed (driver ≠ owner) body.
- Session takeover detaches the old session with `collapse=False` and reattaches
  the new session to the same binding, so a pushed avatar/vehicle stack survives
  the takeover.

### Engine — ownership / roster

- [`evennia/accounts/accounts.py`](evennia/accounts/accounts.py): the playable
  roster (`CharactersHandler`) is now derived from ownership
  (`ObjectDB.db_account`), decoupled from the control graph. `add(transfer=)`
  refuses a character owned by another account unless `transfer=True`; `remove`
  disowns (clears `db_account`) and drops any control graph, so ownership and the
  graph cannot diverge. `remove` detaches any live session first, so disowning a
  currently-driven character never leaves a session half-attached to a body whose
  binding is gone.
- The I1 legacy-ownership backfill (`ControlBinding.reconcile_ownership` /
  `reconcile_account` / `ensure_playable`) is now ownership-only — it sets
  `ObjectDB.db_account` from legacy signals (`_last_puppet`,
  `_playable_characters`, `pid()` locks) and never creates bindings. The engine
  no longer auto-runs it on PSYNC ([`server/service.py`](evennia/server/service.py));
  the game owns invoking its backfill. The temporary Developer-locked
  **`@verify-reconcile`** command ([`commands/default/admin.py`](evennia/commands/default/admin.py))
  gates eventual deletion of the machinery.

### Engine — permissions follow the live driver

- The permissions that apply when a body acts now come from the account
  *driving* it (`DefaultObject.puppeteer`), never the durable owner. This closes
  a privilege path opened by the `.65` `obj.account`=owner repurposing: a session
  driving a body whose owner had higher perms would inherit the owner's perms.
  It also restores the original "an Object *controlled by* an Account" contract —
  pre-I1 `obj.account` was the live-puppet pointer.
- Spots moved from owner to driver: `perm` / `pperm` / `_to_account`
  ([`locks/lockfuncs.py`](evennia/locks/lockfuncs.py)),
  `TypedObject.check_permstring` superuser bypass
  ([`typeclasses/models.py`](evennia/typeclasses/models.py)),
  `DefaultObject.is_superuser` and `get_cmdset_providers`
  ([`objects/object.py`](evennia/objects/object.py)), and the `is_ooc` lockfunc
  (a session possessing an unowned NPC is IC, not OOC).
  `Character.at_post_puppet` records `_last_puppet` on the driver
  ([`objects/character.py`](evennia/objects/character.py)) (an undriven/possessed
  body has no owner to record on). An *undriven* body now has no account
  elevation at all (only its own object perms) — the secure default. Ownership-
  based access should be an explicit ownership lock, not perm-elevation.

### API / semantics

- New `DefaultObject.is_puppeted` (a live session drives this body) and
  `DefaultObject.puppeteer` (the driving account), distinct from `has_account`
  (durable owner) and `is_connected` (owner connected somewhere).

### Migration notes

- One migration, [`accounts/0019`](evennia/accounts/migrations/0019_controlbinding_focus_floor.py):
  strips the stored `["account", id]` floor entry from every focus stack
  (reversible). No `ControlBinding` column is dropped — `db_account` stays as the
  controller. No back-compat shims (consistent with the `.65` API churn): game
  code that read `obj.has_account` as "currently driven" must use `is_puppeted`;
  code relying on an *offline* character carrying its account's perms must switch
  to an explicit ownership check; ownership writes must route through the
  `characters` handler.

### Tests

- [`actions/tests/test_control_binding.py`](evennia/actions/tests/test_control_binding.py):
  F2 focus semantics, controller-vs-ownership, idmapper instance sharing, and
  co-session push visibility.
- [`accounts/tests.py`](evennia/accounts/tests.py): possession leaves ownership
  untouched; `is_puppeted` / `puppeteer`; ownership-based roster + `transfer=`;
  takeover preserves the pushed stack; and `TestPermissionsFollowDriver` (the
  ground-truth escalation tests).
- [`locks/tests.py`](evennia/locks/tests.py): the puppet-perm tests now drive
  `char2` with its account, reflecting "permissions follow the live driver."

---

## 6.0.0+underspire.70 — fix crash when deleting a puppeted character (I1)

### Engine

- [`evennia/objects/mixins/lifecycle.py`](evennia/objects/mixins/lifecycle.py):
  `delete()` now detaches live sessions (`unpuppet_object`) **before** dropping
  the character's `ControlBinding` (`characters.remove`). The prior order
  deleted the binding row first, so `unpuppet_object` → `collapse_to` →
  `_bump` then issued `save(update_fields=...)` against an already-deleted row,
  raising `ControlBinding.NotUpdated` and aborting the delete. Any attempt to
  delete a currently logged-in/puppeted character crashed. Latent since the
  `.65` I1 control-graph rewrite; the regression test only now exercises the
  path.

### Tests

- [`evennia/accounts/tests.py`](evennia/accounts/tests.py): updated three
  `CharactersHandler` tests to the `ControlBinding`-derived playable set
  (`test_characters_property`, `test_add_character_to_playable_list`,
  `test_puppet_deletion`). They previously asserted the removed
  `_playable_characters` attribute semantics. `test_puppet_deletion` is now the
  ground-truth test for the delete-while-puppeted fix above.
- [`evennia/help/`](evennia/help/): moved `tests.py` into the `tests/` package
  as `tests/test_help.py`. The `.65` CM1 work added the `tests/` package
  (`test_catalog.py`) alongside the existing `tests.py` module; Python cannot
  resolve `evennia.help.tests` as both, which aborted full-suite discovery
  (`evennia test evennia`) with an `ImportError`. Per-module runs were
  unaffected, so it went unnoticed.
- [`evennia/actions/tests/test_control_binding.py`](evennia/actions/tests/test_control_binding.py),
  [`test_actor_focus.py`](evennia/actions/tests/test_actor_focus.py): the
  `ControlBinding` focus-stack tests created a binding on `self.char1`, but the
  shared `EvenniaTest` setUp now auto-binds char1 (login puppets it), colliding
  on the OneToOne `db_identity` and starting with a non-empty stack. Bind a
  fresh, unpuppeted identity instead; the `FocusChanged.providers()` assertion
  now also distinguishes the three scopes (body, identity, focus) it could not
  before. 42 tests restored.

### Migration

- None. Behavior-only fix.

---

## 6.0.0+underspire.69 — ignore webhook-origin Discord messages

### Engine

- [`evennia/server/portal/discord.py`](evennia/server/portal/discord.py):
  `MESSAGE_CREATE` events carrying a `webhook_id` are now dropped before they
  reach `BUS.emit`. Channel webhook posts (including the bridge's own channel
  webhook fallback) are not player chat; without this guard each outbound
  bridge delivery re-entered the game as inbound, producing an echo loop.

### Migration

- None. Behavior-only fix for deployments using Discord channel webhooks.

---

## 6.0.0+underspire.68 — bulk_tick reactor-thread check (Twisted compat)

### Engine

- [`evennia/utils/bulk_tick.py`](evennia/utils/bulk_tick.py): `gather_objectdb` and
  `apply` now assert the I/O thread via `twisted.python.threadable.isInIOThread()`
  instead of `reactor.isInIOThread()`. Production `EPollReactor` on older Twisted
  builds lacks the reactor method and raised `AttributeError` every global-tick
  heartbeat, forcing the game into legacy per-object fallback while spamming logs.

### Migration

- Pin production to `underspire.68` (or newer) in the game deploy workflow
  `EVENNIA_REF`.

---

## 6.0.0+underspire.67 — ControlBinding ownership reconcile (I1 migration gaps)

### Engine

- [`evennia/accounts/models.py`](evennia/accounts/models.py): `ControlBinding`
  gains `reconcile_ownership()` (global), `reconcile_account()` (per-account),
  and `ensure_playable()` (idempotent binding + `db_account` sync). The boot
  bulk-job now rebuilds the I1 control graph from every legacy ownership
  signal: `ObjectDB.db_account`, `_last_puppet`, `_playable_characters`, and
  `pid(<n>)` puppet locks on character typeclasses with no `db_account`.
  `populate_missing()` is retained as a thin wrapper over
  `reconcile_ownership()` for callers.
- [`evennia/server/service.py`](evennia/server/service.py): the reconcile job
  runs on **every** server start *and* reload, not just cold boot. The prior
  `mode != "reload"` guard skipped warm reloads, so bindings created by signals
  after the cold start (or rows missed because `db_account` was already set)
  never reconciled. Startup now logs the full stats dict
  (`accounts`, `puppet_lock`, `db_account_resync`).

### Migration

- Idempotent. No action required; existing installs converge on the next
  start or `@reload`.

---

## 6.0.0+underspire.66 — character ownership `db_account` sync on `characters.add`

### Engine

- [`evennia/accounts/accounts.py`](evennia/accounts/accounts.py):
  `CharactersHandler.add` now writes `ObjectDB.db_account` alongside the
  `ControlBinding` it creates. I1 ownership reads through `ControlBinding`, but
  legacy paths (web chargen, locks, `populate_missing`) still read the durable
  `db_account` field; leaving it unset desynced those reads from the control
  graph. The field is durable ownership, not the live session driver.

### Migration

- Idempotent; the write is skipped when `db_account` already matches the owner.

---

## 6.0.0+underspire.63 — deprecate `ObjectDB.objects.get_objs_with_attr()`

Key-existence attribute search (`get_objs_with_attr(attr_name)`, "any value")
is unsalvageable under the JSONB attribute model and is now deprecated ahead of
removal at the Underspire API freeze.

### Performance

The method's docstring claimed "GIN-indexed on PostgreSQL." That was wrong. The
query compiles to `(db_attrs -> '~' -> '_d') ? attr_name`, testing the `?`
operator against an *extracted nested object*. The shipped index is a plain
`USING GIN(db_attrs)` with default `jsonb_ops`
([`0017_objectdb_db_attrs_gin.py`](evennia/objects/migrations/0017_objectdb_db_attrs_gin.py)),
which only accelerates top-level `db_attrs @> …` and `db_attrs ? …`. Once the
expression extracts a sub-document the index cannot apply, so this is an
unindexed sequential scan on every backend, PostgreSQL included. Key-existence
also cannot be made indexable through containment: `@>` requires a value, so
"has key K with any value" has no GIN-friendly form short of a dedicated
expression index we don't ship. The result set is near-useless besides (every
object with e.g. `desc` set is almost the whole table).

### API

- [`get_objs_with_attr()`](evennia/objects/manager.py:145) now emits a
  `DeprecationWarning` and documents the true cost. The in-DB PostgreSQL filter
  is retained so an existing call doesn't stream every row's `db_attrs` into
  Python, but it is marked for removal.
- `get_objs_with_attr_value()` is unaffected: its top-level `db_attrs @> {…}`
  containment query is genuinely GIN-indexed.

### Migration notes

Downstream callers of `get_objs_with_attr()` should switch to
[`get_objs_with_attr_value()`](evennia/objects/manager.py:181) (restrict to a
known value) or pass `candidates=` to bound the scan. The method will be removed
at the API freeze, so audit usage now: key-only existence searches may require
larger query refactors and should not be left to the deprecation deadline.

### Tests

- `test_get_objs_with_attr` now asserts the `DeprecationWarning` fires
  ([`test_objects.py`](evennia/objects/tests/test_objects.py)). Object suite
  passes (96 tests, SQLite).

## 6.0.0+underspire.62 — remove the `ingame_python` contrib

Deletes the `ingame_python` contrib (the in-game Python event/callback scripting
system). It is unused by our game and lets builders execute arbitrary Python
in-game, a security surface that contradicts the prototype `exec` removal in
``.49``. It was also opt-in but did a DB query (`ScriptDB.objects.get`) at module
import time, making it brittle to import before DB setup.

- Removed `evennia/contrib/base_systems/ingame_python/` and its docs
  (`Contrib-Ingame-Python*.md`, the `Contribs-Overview` entry, and the
  generated API stubs).
- Left untouched: the historical `*_convert_contrib_typeclass_paths` migrations
  that reference the old path as a string remap (no code import; harmless and
  needed for old DBs).

## 6.0.0+underspire.61 — fix `comms/0019` data migration on fresh installs

The 2021 channel-alias data migration queried the live `ChannelDB` model, which
selects `db_attrs` (a column added by a much later migration), so it errored on
a fresh install even with an empty channel table. ``.56`` had hidden this behind
a blanket `except Exception` wrapping the whole body, which also swallowed
genuine failures.

- Detect whether there is anything to migrate via the *historical* model
  (`apps.get_model`), whose query does not reference later-added columns, and
  early-return on the empty/fresh-install case.
- The live model is only touched once real channel data exists; the catch around
  it is narrowed to `OperationalError`/`ProgrammingError` (schema-not-ready on an
  old upgrade) so real bugs propagate. `atomic=False` is retained only to make
  that narrow DB-error skip safe.

A fresh `evennia migrate` now applies `comms.0019` with no skipped/no-such-column
message; the comms suite passes.

## 6.0.0+underspire.60 — fix JSONB serialization regressions (hidden dbobjs, stable caching)

A full-suite run (2171 tests; prior rounds only ran narrow subsets) surfaced
core ``dbserialize`` regressions introduced by the JSONB backend.

- ``jsonb_util.to_jsonb`` left embedded *hidden* dbobjs serialized. ``to_pickle``
  calls ``__serialize_dbobjs__`` in place, replacing a container's hidden dbobj
  with its bytes, and the owning attribute kept referencing that mutated live
  object, so later reads returned raw bytes. ``to_jsonb`` now restores the live
  value after encoding via a read-only walk that calls ``__deserialize_dbobjs__``
  (it must not reconstruct containers: ``from_pickle`` cannot rebuild ``_Saver*``
  types from a generator, and ``deserialize`` mishandles dict/list subclasses).
- ``to_jsonb`` masked the real serialization error. An un-storable hidden dbobj
  now propagates the underlying error (e.g. ``TypeError``) instead of a generic
  ``ValueError``, restoring the documented "raises on unstorable value" contract.
- ``evennia/utils/tests/test_dbserialize.py``: relaxed two assertions that
  required the read-back object to be the *same* object assigned. Identity
  across the write-behind store is not a contract (the value is round-tripped);
  the tests now assert what is guaranteed: consecutive reads are stable and
  hidden dbobjs round-trip.
- Contrib fallout from stable caching: a read now returns the same ``_Saver``
  object each time (instead of a fresh copy), which exposed two
  modify-during-iteration bugs that iterated an attribute collection while
  deleting from it. Both now iterate a snapshot:
  [`turnbattle/tb_items.py`](evennia/contrib/game_systems/turnbattle/tb_items.py)
  (`itemfunc_cure_condition`) and
  [`evadventure/combat_turnbased.py`](evennia/contrib/tutorials/evadventure/combat_turnbased.py)
  (`stop_combat`).

Known pre-existing failure (unrelated, not addressed): the
``evennia.contrib.base_systems.ingame_python`` package errors at import time
because ``register_events`` runs ``ScriptDB.objects.get(...)`` at module load,
before the DB exists during test discovery.

## 6.0.0+underspire.59 — JSONB attribute search on all backends; fix REST set_attribute

The ``.56a``/``.57`` attribute-search restoration was PostgreSQL-only: the
queries returned ``none()`` (or skipped their filters) on SQLite/MySQL, so
attribute search silently returned empty results, and the existing tests failed
outright on the default (SQLite) backend.

- Added a portable Python fallback (``_jsonb_match_pks`` in
  [`typeclasses/managers.py`](evennia/typeclasses/managers.py)) used on every
  non-PostgreSQL backend; PostgreSQL keeps the GIN-indexed ``@>`` containment
  fast path. Applies to ``get_by_attribute``, ``smart_search`` attr tokens, and
  ``ObjectDBManager.get_objs_with_attr``/``get_objs_with_attr_value``. No
  candidate-passing required; MySQL rides the fallback.
- These DB-level queries now flush pending write-behind attribute writes first
  (``_flush_attr_writes``): JSONB attributes live in the L1 cache until the
  maintenance tick, so a search reading ``db_attrs`` directly would otherwise
  miss recently-set attributes. This affected all backends.
- [`web/api/serializers.py`](evennia/web/api/serializers.py): ``AttributeSerializer.db_value``
  reverted to ``CharField`` (the ``.57`` ``JSONField`` rejected form-encoded
  scalar values like ``"test_value"``, 400ing ``set_attribute`` for every
  object type).
- Test isolation: ``_DIRTY_BACKENDS`` is now reset in ``tearDown``
  (``discard_dirty_backends``). ``flush_cache()`` clears the idmapper but not
  this separate write-behind set, so dirty backends leaked across tests; under
  SQLite pk reuse a stale backend's document clobbered a later test's write
  during ``flush_all_dirty()``.

## 6.0.0+underspire.58 — collapse no-op AlterField migrations into AddField

The per-typeclass attribute migrations split the ``db_attrs`` column addition
across an ``AddField`` followed by an ``AlterField`` that only edited the
field's ``help_text`` (no DDL, no ``null``/``db_index`` change despite the
original commit message). Collapsed each into a single ``AddField``, removing
the dead AlterField migrations.

## 6.0.0+underspire.57 — fix JSONB review gaps (C1-C4, migration deps, test cleanup)

Addresses dangling references and breakage left by the M2M removal (``.55``/``.56``).
Note: the attribute-search restorations here were PostgreSQL-only and were made
backend-portable in ``.59``.

- Restored ``ObjectDBManager.get_objs_with_attr`` / ``get_objs_with_attr_value``
  (fixes ``search_object(attribute_name=...)``) and Postgres-guarded
  ``get_by_attribute`` (it had raised ``NotSupportedError`` on SQLite).
- ``TypeclassManager.smart_search``: ``attr==`` / ``attr!=`` tokens ported from
  the removed ``db_attributes__`` M2M lookup to JSONB ``@>`` containment.
- [`web/api/serializers.py`](evennia/web/api/serializers.py): ``AttributeSerializer``
  field sources realigned so ``views.set_attribute`` reads the right keys;
  ``search_attribute_object`` reduced to a stub (the ``Attribute`` model is gone).
- Migration ``typeclasses/0023`` (delete ``Attribute``) gained explicit
  dependencies on the four ``RemoveField(db_attributes)`` through-table drops,
  so the parent table is dropped after the FK-holding through tables on
  PostgreSQL/MySQL regardless of the migration planner's sort order.
- Removed ``TestTypedAttrFilterKwargs`` (it targeted the orphaned
  ``db_attributes__`` ``filter_kwargs``, which now raises ``NotImplementedError``).

## 6.0.0+underspire.56a — restore `get_by_attribute` via JSONB containment

``search_object_attribute`` / ``search_account_attribute`` /
``search_script_attribute`` delegate to ``TypedObjectManager.get_by_attribute``,
which ``.56`` removed while leaving the callers in place. Re-added it using
GIN-indexed ``@>`` containment (``filter(db_attrs__contains={cat: {_d: {key: value}}})``),
absorbing the now-unused ``strvalue``/``attrtype`` params via ``**kwargs``.
(Made backend-portable in ``.59``.)

## 6.0.0+underspire.56 — BREAKING: Phase 2 M2M removal, delete `Attribute` model

Final step of the JSONB attribute migration. Removes the `Attribute` Django
model, `AttributeManager`, `ModelAttributeBackend`, and all write-behind /
orphan-tracking infrastructure. All attributes now live exclusively in the
`db_attrs` JSONB column on the owning object row via `JsonbAttributeBackend`,
which becomes the default `ATTRIBUTE_BACKEND_CLASS`.

**Breaking for downstream code that queried attributes through the ORM.**
We are in lockstep with our single consumer, so this lands without a
deprecation cycle. The following public query helpers are gone; attribute
lookups by value must go through JSONB queries (`db_attrs__contains={...}`)
or the game-side `world.db_utils.attrs_match()` / `attrs_exists()` helpers:

- Manager methods removed from `TypedObjectManager` / `ObjectDBManager`:
  `get_attribute`, `get_nick`, `get_by_attribute`, `get_by_nick`,
  `get_objs_with_attr`, `get_objs_with_attr_value`.
- `evennia.managers.attributes` (the `Attribute.objects` container) is gone.

What changed:

- `typeclasses/attributes.py`: removed the `Attribute` model,
  `ModelAttributeBackend`, `value_query_filter`, `_classify_value`,
  `_mark_attr_dirty`, `flush_if_pending`, and orphan dirty tracking.
- `settings_default.py`: default `ATTRIBUTE_BACKEND_CLASS` is now
  `JsonbAttributeBackend`.
- `accounts/object.py`, `accounts/accounts.py`: `NickHandler` is wired via
  `settings.ATTRIBUTE_BACKEND_CLASS` instead of the hardcoded (now deleted)
  model backend. Nicks store their 4-tuple in the `_t_nick__<cat>` document
  section like any other attrtype.
- `prototypes/prototypes.py`: the `Attribute` ORM lookup for registered
  prototypes is replaced with a `DefaultScript` loop reading the prototype
  from each script's own `db_attrs`.
- `web/api/serializers.py`: `AttributeSerializer` ported from a
  `ModelSerializer` to a plain `Serializer` (the model is gone).
- `web/admin/attributes.py`: reduced to a stub; the inline `Attribute` admin
  is dropped from the accounts/comms/objects/scripts admin pages.
- `server/prometheus_metrics.py`, `typeclasses/attribute_metrics.py`:
  orphan-flush stats removed.
- Migration `typeclasses/0023`: drops the `typeclasses_attribute` table and
  its value-type indexes. `comms/0019` (a 2021 migration) carries a
  compatibility shim for fresh installs.

## 6.0.0+underspire.55 — drop `db_attributes` M2M field, reactor guards

Phase 1 of M2M removal. Drops the `db_attributes` `ManyToManyField` from
`TypedObject` and the per-typeclass through tables, leaving the `Attribute`
model itself for Phase 2 (`.56`).

What changed:

- `typeclasses/models.py`: removed the `db_attributes` M2M field. Migrations
  for objects/accounts/scripts/comms drop the through tables.
- `typeclasses/attributes.py`, `typeclasses/managers.py`,
  `typeclasses/models.py`: `ModelAttributeBackend._get_m2m()`, the
  `remove_attributes_on_delete` signal handler, and `managers.get_attribute`
  through-access are guarded to no-op when the field is absent.
- `web/api/views.py`: `obj.db_attributes.all()` becomes
  `obj.attributes.all()` (handler-level access, backend-agnostic).
- `web/admin/*`: dropped the `db_attributes.through` inline admin classes.
- `utils/bulk_tick.py`: `gather_objectdb()` and `apply()` assert
  `reactor.isInIOThread()`, since both touch the idmapper and L1 dicts that
  are reactor-thread-only.
- `typeclasses/jsonb_handler.py`: synthetic attribute `pk` is now a
  per-backend incrementing counter (`_pk_counter`) instead of
  `hash((key, category, id(self)))`, eliminating collision and id-reuse risk.
- Tests: `TestFlushRetry`, `TestAttrtypeQueryAll`, `TestPendingCount`,
  `TestPkCounter` added to `test_jsonb.py`.

## 6.0.0+underspire.54 — fix JSONB backend bugs from review

Bug-fix pass on the `.52` JSONB backend following a multi-agent review.

- **Write-behind never flushed JSONB backends.** `flush_all_dirty()` /
  `count_pending_dirty()` gated on `_dirty_attrs`, which `JsonbAttributeBackend`
  does not have, so every JSONB write stayed in the L1 dict and was lost on
  shutdown. Added `IAttributeBackend.pending_count()`; the flush loop now
  flushes every backend in `_DIRTY_BACKENDS` unconditionally.
- **GIN index migrations crashed SQLite/MySQL CI.** `CREATE INDEX
  CONCURRENTLY ... USING GIN` is Postgres-only. The four GIN migrations
  (objects/0017, accounts/0016, comms/0026, scripts/0022) now use `RunPython`
  guarded on `schema_editor.connection.vendor == "postgresql"` (still
  `atomic = False`), no-op on other backends.
- **`to_jsonb` silently stored `null`** for unserializable values. Now raises
  `ValueError` so the failure surfaces instead of corrupting the slot.
- **Dead-weakref writes** on `JsonbAttribute.value` / `lock_storage` setters
  now log an error instead of silently dropping the write.
- **`do_update_attribute` strvalue branch** left the cached attr's
  `db_strvalue` / `db_value` stale; both are now updated to match the
  non-strvalue branch.
- **`_do_flush`** logged nothing and could retry a poison document forever.
  It now logs each failure with the pk and gives up after 10 attempts.
- **`query_all`** blanket-skipped all `_`-prefixed category keys, hiding
  attrtype sections (`_t_nick__~`, etc.). Attrtype backends now filter to
  their own `_t_{attrtype}__` prefix.
- `typed_attr.filter_kwargs` (targeted the orphaned M2M table) now raises
  `NotImplementedError` pointing at the JSONB query helpers.
- Reverted the `containers.py` `capacity` field to its original
  `AttributeProperty(default=20)` (the `.52` `TypedAttr(int, ..., min=0)`
  swap was an unintended behavior change).

## 6.0.0+underspire.52 — JSONB attribute storage, GIN indexes, bulk-tick

Introduces JSONB-backed attribute storage as an opt-in backend, alongside
the existing M2M storage (which `.55`/`.56` later remove). JSON-safe values
are stored verbatim; complex Python objects are pickle-encoded behind a
`__P:<base64>` sentinel.

What changed:

- `typeclasses/jsonb_handler.py`: `JsonbAttributeBackend`, an
  `IAttributeBackend` storing all of an object's attributes in a single
  `db_attrs` JSONField on its own row, with an in-process L1 cache and
  write-behind flush on the maintenance tick.
- `typeclasses/jsonb_util.py`: `to_jsonb` / `from_jsonb` encode-decode
  helpers (the `__P:` sentinel path routes through Evennia's serializer so
  dbrefs and `_Saver*` proxies normalise correctly).
- `typeclasses/typed_attr.py`: structured attribute descriptors for hot-path
  fields.
- `typeclasses/redis_attr_cache.py`: slimmed to a pure L1 cache; the Redis
  eviction path is removed (JSONB is the persistence layer now).
- `utils/bulk_tick.py`: `BulkTickContext`, a three-phase
  (reactor / worker / reactor) coordinator for vectorised attribute ticks.
- Per-typeclass migrations add the `db_attrs` JSONB column and a
  `CREATE INDEX CONCURRENTLY` GIN index (`jsonb_ops`, supporting `@@`, `@?`,
  `@>`).
- `native/`: dormant Rust/PyO3 scaffold for future engine hot paths (wired
  into nothing). `pyproject.toml` scopes package discovery to `evennia*` so
  setuptools does not treat `native/` as a second top-level package.

## 6.0.0+underspire.51 — AS1: Django connection hygiene in `in_thread`

Follow-up to AS1 (`.50`). The blessed
[`in_thread`](evennia/utils/defer.py) was a thin `deferToThread` that did
**not** manage Django connections, unlike the hand-rolled wrapper it
replaced downstream. A pooled worker that touches the ORM (stamina, search,
help, export handlers) could reuse or accumulate stale DB connections
("server has gone away"). Since off-reactor ORM work is a primary, expected
use of the helper, connection hygiene belongs in the engine so the blessed
path is safe by default rather than relying on every worker author to
remember it.

### Engine — `in_thread` manages connection lifecycle

- [`evennia/utils/defer.py`](evennia/utils/defer.py): the worker is wrapped
  so `close_old_connections()` runs before and after it, in the worker
  thread. The finally-close is the essential one (the next job on that
  pooled thread starts clean); the before-close guards against a connection
  that went stale between jobs. Respects `CONN_MAX_AGE` (persistent
  connections are reused until they age out). `background` and `threaded`
  inherit this via `in_thread`. Negligible cost for workers that never touch
  the ORM.
- Threading-safety contract docstring updated to **positively permit direct
  Django ORM queries on plain (non-typeclass) models** in the worker (it was
  only implied before), noting the helper keeps those connections clean.
  Typeclass / idmapper / `.db` / `.ndb` / `obj.msg` access remains
  reactor-thread-only.

### Migration

No downstream code change required. Workers that previously relied on the
hand-rolled wrapper's `close_old_connections` now get it from the engine
helper; ORM-touching workers no longer need to manage connections
themselves. (Outstanding: the downstream AS1 Phase 2 blocking-site
migration and the job-queue drain/ack semantics — fire-and-forget handlers
may offload via `background`, but completion-semantic jobs like index
rebuilds must stay inline to keep "acked" meaning "done".)

### Tests

- [`evennia/utils/tests/test_defer.py`](evennia/utils/tests/test_defer.py)
  (now 8): added a check that `close_old_connections` is called twice (before
  and after the worker) and both on the worker thread.

## 6.0.0+underspire.50 — AS1: sync-by-default + threaded I/O helpers

Settles the sync/async direction (AS1): the game stays synchronous by
default, with a small blessed surface for pushing blocking I/O off the
Twisted reactor thread, plus a watchdog that finds the sites that still
block. No new dependencies (Twisted's reactor thread pool already exists).

### Engine — AS1 Phase 0: `evennia.utils.defer`

New module [`evennia/utils/defer.py`](evennia/utils/defer.py), the one
blessed way to run blocking, game-state-free I/O off the reactor:

- `in_thread(fn, *args, **kwargs) -> Deferred` — run `fn` in the reactor
  thread pool; the returned Deferred's callbacks run on the reactor thread.
- `background(fn, *args, on_error=None, **kwargs) -> None` — fire-and-forget
  `in_thread`. On failure, calls `on_error(failure)` on the reactor thread
  if given, else logs via the engine logger. Never swallows the error.
- `threaded(fn)` — decorator; calling the wrapped fn returns an `in_thread`
  Deferred.

The module and per-function docstrings state the **threading-safety
contract**: the worker callable may do only stdlib/network I/O and pure
computation and must return plain data; it must not touch game objects
(`.db`/`.ndb`, typeclass attrs, `obj.msg`, manager queries, idmapper-cached
instances). All game-object interaction happens in the reactor-thread
callback. They also state the hook-return boundary: this makes "do blocking
work, then deliver a result later" safe (commands, scripts, jobs, webhooks),
but does **not** make blocking I/O safe inside a hook that must return a
value synchronously (`at_pre_move`, lock functions); for those, do not
block — precompute, cache, or restructure.

### Engine — AS1 Phase 1: reactor-stall watchdog

New module
[`evennia/utils/reactor_watchdog.py`](evennia/utils/reactor_watchdog.py): a
low-overhead `LoopingCall` that logs exactly one warning when a single
reactor turn blocks longer than `REACTOR_STALL_WARNING_MS` (new setting,
default `200`; `0` disables). Because the tick runs on the reactor thread it
cannot fire during a block and fires late once the block clears; the
measured lateness is the stall. Wired into `EvenniaServerService`: started
after the maintenance task, stopped on shutdown. Coarse by design — it
reports the stall duration but cannot name the culprit (the blocking call
has already returned by the time the late tick fires); it is a worklist
generator, not a profiler.

### Migration — `worker_pool` removed, superseded by `defer`

**Breaking for any downstream importing `evennia.utils.worker_pool`.** The
module had no live callers in the engine and duplicated the `deferToThread`
wrapper that `defer` now owns as the single blessed surface.

- Deleted `evennia/utils/worker_pool.py` and its test. Replace
  `worker_pool.defer_to_worker(fn, callback=, errback=)` with
  `defer.in_thread(fn).addCallbacks(...)`; `schedule_on_reactor` is no
  longer needed (callbacks already run on the reactor — return plain data
  from the worker and act on it in the callback).
- Removed settings `ENGINE_WORKER_POOL_ENABLED` and
  `ENGINE_WORKER_BLOCK_WARN_MS`. The latter's in-thread timing warning
  measured the wrong thing (long worker-thread time is expected when
  offloading); the reactor-stall watchdog is the correct instrument.
- [`ENGINE.md`](ENGINE.md) and the
  [`evennia/jobs/queue.py`](evennia/jobs/queue.py) docstring now point at
  `evennia.utils.defer`.

**Engine pin:** `evennia.utils.defer` is a new importable symbol. Bump
`EVENNIA_REF` to a tag containing this release before any downstream code
imports it (the downstream blocking-site migration is AS1 Phase 2, tracked
separately).

### Migration — `process_pending_jobs` threading contract corrected

[`evennia/jobs/queue.py`](evennia/jobs/queue.py) `process_pending_jobs`
previously documented draining jobs "in the worker thread pool" via
`in_thread`. That is unsafe: job handlers that touch game objects would race
the reactor through the non-concurrency-safe idmapper/typeclass layer. The
docstring now states the correct model (matching `ENGINE.md`): drain on the
reactor thread (the global tick, gated by `JOB_QUEUE_DRAIN_EVERY_N_TICKS`),
where handlers may touch game objects freely, and any blocking I/O inside a
handler must itself offload via `evennia.utils.defer`. Contract-only change;
no behavior change. Downstream consumers wiring the drain or registering
handlers must confirm the drain runs on the reactor (not a worker) and audit
each handler for inline blocking I/O.

### Tests

- [`evennia/utils/tests/test_defer.py`](evennia/utils/tests/test_defer.py)
  (7): worker runs off the reactor thread and the callback on it (real
  thread-identity assertions via a per-test reactor pool drained with
  `runUntilCurrent`), kwargs forwarding, decorator returns a Deferred, and
  `background`'s error-routing / default-logging / no-propagation contract.
- [`evennia/utils/tests/test_reactor_watchdog.py`](evennia/utils/tests/test_reactor_watchdog.py)
  (5): over-threshold block warns exactly once, sub-threshold jitter never
  warns, threshold defaults to the setting, and the disabled/start/stop
  lifecycle behaves.
- Full `evennia.server.tests` suite (67) green with the watchdog wiring.

## 6.0.0+underspire.49 — BREAKING: remove prototype `exec` key (F20)

Removes the `exec` prototype key, which ran arbitrary Python at spawn
time. Spawn-time logic now belongs exclusively on the typeclass.

### Engine — F20: drop prototype `exec` support

**Breaking for downstream prototype authors who used `exec:`.** The key
is gone. Migrate inline code to typeclass hooks: `at_object_creation`
(once, on first creation), `at_init` (every load), or `at_prototype_spawn`
(every spawn, already called by the spawner right where `exec` used to run).

What changed:

- `prototypes/spawner.py`: removed the `exec()` call in
  `batch_create_object`, the `exec`/`execs` plumbing in `spawn()`, the
  `execs` element of the per-object param tuple (now a 7-tuple), and the
  two `exec`-skip branches in the prototype-diff apply path. Dropped the
  now-unused `import evennia` (it only existed for the exec namespace).
- `prototypes/prototypes.py`: `homogenize_prototype` now raises
  `RuntimeError` with a migration message if a prototype contains `exec`,
  instead of silently turning it into a stray attribute named `"exec"`.
- `commands/default/building.py`: removed the Developer-only gate on the
  `exec` key in `@spawn` (its only reason to exist is gone; the
  `homogenize_prototype` rejection now produces a clean in-game error).

Note: through the public `spawn()` API and in-game `@spawn`, the `exec`
key was already effectively dead. Both homogenize prototypes first, which
routed the unreserved `exec` key into `attrs` before the spawn-time
`pop("exec")` could see it. Only direct `batch_create_object` callers
passing a pre-built param tuple ever triggered the `exec()`.

## 6.0.0+underspire.48 — cleanup: dead `_menutree` branch (F21) + cmdset verifications (F7)

Bundled cleanup. F21 removes one dead conditional in `fieldfill`. F7
closes the three open verification items from the retired
`CMDSET_REFACTOR.md §8` — two confirm existing code is correct, one
confirms a setting is still load-bearing.

### Engine — F21: drop dead `caller.db._menutree` read branch

F5 migrated `_menutree` consumers off the persistent-attribute alias to
`_evmenu`. One read site in `fieldfill` was missed because it read from
`caller.db` (persistent) rather than `caller.ndb` (non-persistent), so it
didn't show up in F5's `ndb._menutree` grep. Confirmed unreachable: zero
in-tree writes to `caller.db._menutree`.

- [`evennia/contrib/utils/fieldfill/fieldfill.py`](evennia/contrib/utils/fieldfill/fieldfill.py) — `menunode_fieldfill` collapsed to the `ndb._evmenu` branch.

### Engine — F7(a): account cmdset invalidation walk verified

When an account-level cmdset changes,
[`invalidate_for_cmdset_owner`](evennia/commands/cmd_access_cache.py)
walks `obj.characters.all()` and drops each playable character's
cache. `Account.characters` is a `CharactersHandler` over
`_playable_characters` with a real `.all()` method, so the walk
resolves. Coverage is conservative-correct: all playable characters
get invalidated, not just currently-puppeted ones, which is safe
over-invalidation. No code change.

### Engine — F7(b): `arg_regex` remains load-bearing

Audit of `arg_regex` usage across the engine and the one downstream
consumer (`newmoo`) shows it is still actively used as an escape hatch:
exits (`arg_regex=r"^$"`), EvMenu nodes, building commands with
`/switch` syntax, evscaperoom, help, eveditor, clothing, crafting,
menu_login, and unloggedin all set non-default values. Not a
deprecation candidate. No code change.

### Contrib — tutorial_world `look` migrated off dropped `search(quiet=)` kwarg

Surfaced by the full test suite while verifying F21/F7.
`CmdTutorialLook.func` ([`evennia/contrib/tutorials/tutorial_world/rooms.py`](evennia/contrib/tutorials/tutorial_world/rooms.py))
called `caller.search(..., quiet=True)`, but `SearchMixin.search()` no
longer accepts `quiet` — the new API is `search_for()` returning a
typed `Found` / `Ambiguous` / `NotFound`. Migrated the detail-fallback
flow to `search_for()` and pattern-matching on the typed result, which
restores the test (`test_cmdtutorial`) and the original behavior:
on no-match or multi-match, check for a room detail first; if no
detail, delegate to the default error handler.

### Engine — F7(c): EvMore "q" sys-cmd dedup re-verified

`cmdset.py:534` strips sys commands carried through the raw
`commands[:]` copy before re-adding the merged sys set, so a sys cmd
never appears twice after a merge. The original multi-match this fixed
was between EvMore's `CmdMore` (key=`__noinput`, alias `q`) and
`CmdMoreExit` (key=`__nomatch`) on Replace-style merges. Existing
coverage in
[`evennia/commands/tests.py`](evennia/commands/tests.py) —
`test_system_cmds_not_duplicated_after_replace` and
`test_system_cmds_not_duplicated_after_union` — exercises the dedup
behavior at both merge types. EvMore's alias path is unchanged. No
code change.

## 6.0.0+underspire.47 — settings: rename `CMD_ACCESS_CACHE_ENABLED` (F6)

Single-symbol breaking rename. All other settings in the command layer
use the `COMMAND_*` prefix (`COMMAND_PARSER`, `COMMAND_RATE_WARNING`,
`COMMAND_TRACE_ENABLED`, etc.); this one straggler used `CMD_*` and has
been brought into line.

### Engine — `CMD_ACCESS_CACHE_ENABLED` → `COMMAND_ACCESS_CACHE_ENABLED`

- [`evennia/settings_default.py`](evennia/settings_default.py) — default declaration.
- [`evennia/commands/cmd_access_cache.py`](evennia/commands/cmd_access_cache.py) — read site + module docstring.
- [`evennia/commands/cmdparser.py`](evennia/commands/cmdparser.py), [`evennia/commands/cmdparser_trie.py`](evennia/commands/cmdparser_trie.py) — read sites.
- [`evennia/commands/tests.py`](evennia/commands/tests.py) — `@override_settings` decorators.
- [`ENGINE.md`](ENGINE.md) — references.

### Migration

Breaking for any downstream that referenced the old name. Default is
unchanged (`True`), so games that never set this explicitly need no
action.

**1. `server/conf/settings.py`** — if you pinned the value, rename:

```python
# before
CMD_ACCESS_CACHE_ENABLED = False
# after
COMMAND_ACCESS_CACHE_ENABLED = False
```

**2. Tests** — rename any `@override_settings(CMD_ACCESS_CACHE_ENABLED=...)`
decorators in the game's test suite to `COMMAND_ACCESS_CACHE_ENABLED`.
Django silently accepts unknown setting names in `override_settings`, so
these will not raise; they just stop affecting the cache. Grep your
game tree:

```sh
grep -rn CMD_ACCESS_CACHE_ENABLED .
```

**3. Comments / docs** — same grep catches stale references in
docstrings, comments, or README snippets.

No data migration; no DB changes; no restart sequencing.

## 6.0.0+underspire.46 — F8 cache audit

End-to-end pass over the seven derived-state caches the fork carries:
location-cmdset, cmd-access, trie, redis-attr, channel-subscriber
(folded into the seven as an eighth), lock-check, and write-behind.
Step 1 wrote the invalidation contract at every cache home. Step 2
audited side-by-side and shipped all nine findings (F-1 through F-9):
two helpers consolidating fan-out, three `pre_delete` receivers wiring
proactive cleanup, one default flip (`CMD_ACCESS_CACHE_ENABLED`), two
real bug fixes, and four doc-only seams.

Working notes are in
[`.agents/audits/cache-audit.md`](.agents/audits/cache-audit.md)
(transient — slated for deletion once the audit context is no longer
useful for reference).

### Engine — cache invalidation contracts (step 1)

Every cache module now carries a **Fills on / Invalidates on /
Staleness bound** docstring so the next contributor doesn't have to
re-derive the invariants from the call sites:

- [`evennia/commands/location_cmdset_cache.py`](evennia/commands/location_cmdset_cache.py) — module docstring.
- [`evennia/commands/cmd_access_cache.py`](evennia/commands/cmd_access_cache.py) — module docstring.
- [`evennia/commands/cmdparser_trie.py`](evennia/commands/cmdparser_trie.py) — module docstring (appended).
- [`evennia/typeclasses/redis_attr_cache.py`](evennia/typeclasses/redis_attr_cache.py) — module docstring.
- [`evennia/comms/channel_subscriber_cache.py`](evennia/comms/channel_subscriber_cache.py) — module docstring.
- [`evennia/locks/lockhandler.py`](evennia/locks/lockhandler.py) — `invalidate_lock_cache` docstring.
- [`evennia/typeclasses/attributes.py`](evennia/typeclasses/attributes.py) — block comment above `_DIRTY_BACKENDS`.

### Engine — `invalidate_caller_access(*targets)` helper (F-2)

Every permission-mutating call site used to fire both
`invalidate_cmd_access_cache(target)` and `invalidate_lock_cache(target)`
manually, a paired-invalidation footgun for any future engine path.
New single seam in
[`evennia/commands/cmd_access_cache.py`](evennia/commands/cmd_access_cache.py):

- Variadic; `None` targets silently skipped so optional puppets need
  no guard (`invalidate_caller_access(account, puppet)`).
- Fans out to every engine-owned per-caller access cache in the order
  a subsequent check needs to see fresh state.
- Migrated call sites:
  [`evennia/commands/default/account.py`](evennia/commands/default/account.py)
  (`@quell` / `@unquell`),
  [`evennia/commands/default/admin.py`](evennia/commands/default/admin.py) (`@perm`).
- The `permissions_changed` signal contract in
  [`evennia/commands/signals.py`](evennia/commands/signals.py) now names
  the helper as the required pre-fire step. Future engine-owned
  per-caller access caches are added inside the helper, not at every
  call site.

### Engine — channel-subscriber `pre_delete` (F-4)

Deleting an `AccountDB` or `ObjectDB` used to leak the entity's
`a:<pk>` / `o:<pk>` ref into every channel subscriber-set it was on,
self-healing only when each channel was independently fan-out-queried
and `get_cached_subscribers` tripped `ObjectDoesNotExist`. Stale refs
accumulated in proportion to subscription count × death rate.

- New helper
  [`channel_subscriber_cache.remove_subscriber_from_all_channels`](evennia/comms/channel_subscriber_cache.py):
  reads the entity's `account_subscription_set` /
  `object_subscription_set` reverse managers (still intact at
  `pre_delete` time) and `SREM`s the ref from every channel's Redis
  set in one pipeline.
- Receiver wired in
  [`evennia/comms/models.py`](evennia/comms/models.py); catch-all
  `pre_delete` that bails on a single `hasattr` check, so overhead on
  unrelated model deletes is one attribute lookup.

### Engine — `CMD_ACCESS_CACHE_ENABLED` default on + override auto-skip (F-6)

Resolves the historical asymmetry where the cmd-access cache defaulted
off while the lock-check cache defaulted on — both landed in the same
"core overhaul" commit with no documented reason for the split.

- [`evennia/settings_default.py`](evennia/settings_default.py):
  `CMD_ACCESS_CACHE_ENABLED` flipped from `False` to `True`. Settings
  comment updated to point at `invalidate_caller_access` and the
  override-skip below.
- [`evennia/commands/cmd_access_cache.py`](evennia/commands/cmd_access_cache.py):
  new `_command_uses_base_access(cmd)` check (lazy-loads
  `Command.access` once, compares `type(cmd).access is Command.access`).
  Commands whose class overrides `.access()` (directly or via an
  inherited subclass) are auto-skipped — mirrors the existing
  `"match" in type(cmd).__dict__` fast-path skip in
  `cmdparser_trie._try_fast_match_exact`. Stock Command.access just
  delegates to LockHandler, so its result is pure-function-of-cached-state
  and safe to cache; overrides may consult time, randomness, or ad-hoc
  DB queries that the cache key cannot see.

**Migration:** games with Command classes that override
`Command.access` are auto-skipped, so no opt-out is needed. Games that
want the prior default-off behavior can set
`CMD_ACCESS_CACHE_ENABLED = False` in their settings.

### Fix — `Script.delete()` attribute cleanup (F-8)

`ObjectDB`, `AccountDB`, and `ChannelDB` all called
`self.attributes.clear()` in their custom `delete()` before
`super().delete()`. `Script.delete()` did not, so every deleted
`Script` left its `Attribute` rows orphaned in PG (the Django M2M
cascade only removed the through-row) and its Redis L2 keys orphaned
until TTL expiry. Surfaced while writing the F-3 redis TTL audit.

[`evennia/scripts/scripts.py`](evennia/scripts/scripts.py): adds the
missing `self.attributes.clear()` call. **Behavior change:** deleted
Scripts now also delete their `Attribute` rows. Test in
[`evennia/scripts/tests.py`](evennia/scripts/tests.py)
(`TestScriptDB.test_delete_routes_attributes_through_handler`)
verifies and guards against regression.

### Engine — redis-attr `pre_delete` (F-9)

`_cache_drop_object` existed on `RedisCachedModelAttributeBackend`
but no caller invoked it. Owner deletion either left the per-owner
Redis index orphaned until TTL (3600s) or — when typeclass `delete()`
called `attributes.clear()` — issued N per-attr Redis round-trips
instead of one per-owner `DEL`.

- New module-level helper
  [`redis_attr_cache.drop_owner_keys(model, obj_id)`](evennia/typeclasses/redis_attr_cache.py)
  promoted from the method body. The method now delegates to it.
- Receiver wired in
  [`evennia/typeclasses/models.py`](evennia/typeclasses/models.py) for
  the four owner dbmodels (`ObjectDB` / `AccountDB` / `ScriptDB` /
  `ChannelDB`). Reads the dbmodel name via `instance.__dbclass__`
  rather than `instance._meta.model_name`, because Evennia's typeclass
  metaclass leaves `_meta.model_name` pointing at the typeclass (e.g.
  `"defaultcharacter"`), not the dbmodel (`"objectdb"`).
- Also covers downstream typeclasses that override `delete()` without
  calling `attributes.clear()` — the receiver fires regardless of
  whether the override remembers per-attr cleanup.

### Engine — display-name seam note (F-1)

The display-name cache moved game-side in `+underspire.36`; the engine
seam is `AppearanceMixin.get_display_name`. Nothing in-tree documented
this. [`evennia/objects/mixins/appearance.py`](evennia/objects/mixins/appearance.py)
now carries a Notes paragraph: engine code mutating state that affects
the returned name (visibility flag flips, identity reveals,
format-context changes) must fire an existing hook the game-side cache
can subscribe to, or expose a dedicated signal, rather than relying on
the cache to guess.

### Engine — trie cheap-key footgun audit (F-5)

The trie's two-tier cache (cheap key + structural signature) doesn't
detect in-place mutation of a Command's `key` / `aliases` on a *reused*
cmdset object. The footgun was already documented; the audit asked
whether any in-tree caller actually hits it.

Result: zero live callers. The mutation API
(`Command.set_key` / `set_aliases`) is invoked only from
`evmenu._update_aliases`, which is itself reachable solely from two
**commented-out** invocation sites
([`evennia/utils/evmenu.py:403`](evennia/utils/evmenu.py),
[`evennia/utils/evmenu.py:430`](evennia/utils/evmenu.py)).
[`evennia/commands/cmdparser_trie.py`](evennia/commands/cmdparser_trie.py)
docstring records the audit outcome.

### Engine — lock-cache layering note (F-7)

`invalidate_lock_cache` docstring in
[`evennia/locks/lockhandler.py`](evennia/locks/lockhandler.py) picks up
a Layering paragraph: on the cmd-parse hot path the lock-check cache
is fronted by `cmd_access_cache` (default on since F-6), so most
lock-cache hits land on non-cmd-parse paths (visibility, traversal,
contrib code). Contributors benchmarking lock-cache effectiveness need
to know cmd-access absorbs the dispatch-path hits.

### Engine — redis-attr TTL is load-bearing (F-3)

Cross-process attribute staleness is bounded by the 60s
maintenance-loop flush tick, not by the 3600s
`ATTRIBUTE_REDIS_CACHE_TTL`. Audit traced every Redis write/invalidation
path and found three escape routes the TTL covers:

1. `_cache_drop_object` was defined but no caller invoked it
   (resolved in F-9, this release).
2. `Script.delete()` did not call `self.attributes.clear()`
   (resolved in F-8, this release).
3. Downstream typeclass overrides of `delete()` that skip
   `attributes.clear()` leak the same way (mitigated by F-9's
   catch-all receiver).

TTL stays. Without it, Redis would grow unboundedly with stale keys.
Module docstring in
[`evennia/typeclasses/redis_attr_cache.py`](evennia/typeclasses/redis_attr_cache.py)
documents the load-bearing role so the value is not lowered or removed
on a future read.

### Settings changes

| Setting | Old | New |
|---|---|---|
| `CMD_ACCESS_CACHE_ENABLED` | `False` | `True` |

### Tests

- [`evennia/commands/tests.py`](evennia/commands/tests.py):
  `TestInvalidateCallerAccess` (4 tests) and
  `TestCmdAccessCacheBypassOnAccessOverride` (3 tests).
- [`evennia/comms/tests.py`](evennia/comms/tests.py):
  `TestRemoveSubscriberFromAllChannels` (5 tests) including end-to-end
  pre_delete signal coverage.
- [`evennia/typeclasses/tests/test_attribute_fork.py`](evennia/typeclasses/tests/test_attribute_fork.py):
  `TestRedisAttrCacheOwnerDelete` (3 tests).
- [`evennia/scripts/tests.py`](evennia/scripts/tests.py):
  `TestScriptDB.test_delete_routes_attributes_through_handler` (1 test).
- Full `evennia.typeclasses` + `evennia.scripts` + `evennia.comms` +
  `evennia.commands` (414 tests) pass with the default flip.

---

## 6.0.0+underspire.45 — Q1 typed search result

Splits `caller.search` / `Account.search` into a typed primitive plus a
thin sugar wrapper, eliminating the single-or-list-or-None return-shape
soup. The primitive `search_for(...)` returns a typed `SearchResult`
variant (`Found`, `Ambiguous`, or `NotFound`); the sugar `search(...)`
calls it, emits the default disambiguation prompt on miss, and returns
`Object | None`. The `quiet=True` mode is gone — callers that want raw
control use `search_for()` and pattern-match. Also drops the long-stale
S1 (typed-settings) prompt from the architecture-doc backlog.

### Engine — typed search result

New module [`evennia/objects/search_result.py`](evennia/objects/search_result.py):

- `Found(obj, stack=None)` — truthy. Carries the matched object, plus a
  `stack` list when `stacked=N` collapsed identical matches.
- `Ambiguous(candidates, search_string, invalid_other=False)` — falsy.
  Carries the candidate list and the original query. `invalid_other`
  flags the "other <name>" selector against a non-two match count.
- `NotFound(search_string)` — falsy. Carries the original query.

All three are frozen dataclasses subclassing `SearchResult`; Python
pattern matching narrows cleanly.

### Engine — `DefaultObject.search` split

[`evennia/objects/mixins/search.py`](evennia/objects/mixins/search.py)
now exposes two methods:

- `search_for(searchdata, ...)` — runs the full pipeline (nick replace,
  qualifier parse, candidate compute, "me"/"here" short-circuit, DB
  query, lock filter, selector narrowing, autopick, stack collapse) and
  returns a `SearchResult`. No side effects.
- `search(searchdata, ...)` — sugar over `search_for`. Returns the
  matched object (or the stack list when stacked), or `None` when the
  result is `Ambiguous` / `NotFound` (with the prompt emitted via
  `settings.SEARCH_AT_RESULT`).

The pipeline helper hooks (`get_search_query_replacement`,
`get_search_direct_match`, `get_search_candidates`, `get_search_result`,
`get_stacked_results`) are unchanged — same names, same signatures,
still `@hook(event="search", ...)`. The internal collapse helper
`handle_search_results` is removed (folded into `search_for`).

**Migration (engine API):**

- `quiet=True` is removed. Callers that used it migrate to
  `search_for()` and pattern-match on the variant.
- `nofound_string` → `not_found`; `multimatch_string` → `ambiguous` on
  `.search()`.
- The `at_search_result` hook (the `SEARCH_AT_RESULT` setting target)
  still takes the old kwarg names (`nofound_string`,
  `multimatch_string`). The sugar layer translates. Games that override
  the hook need no changes.

### Engine — `DefaultAccount.search` split

[`evennia/accounts/accounts.py`](evennia/accounts/accounts.py) gets the
same treatment: `Account.search_for(...)` returns the shared
`SearchResult` type (parameterized on `AccountDB` matches when
`search_object=False`, `ObjectDB` matches otherwise); `Account.search`
becomes a thin sugar wrapper with the renamed kwargs and the
`return_puppet` collapse preserved.

### Engine — call-site migration

All 11 engine-side `quiet=True` call sites migrated to the typed API:

- [`evennia/commands/default/building.py`](evennia/commands/default/building.py):
  link, exit-create, examine, script-search, tag commands.
- [`evennia/commands/default/comms.py`](evennia/commands/default/comms.py): page command target resolution.
- [`evennia/commands/default/account.py`](evennia/commands/default/account.py): `@ic` candidate gathering.
- [`evennia/prototypes/menus.py`](evennia/prototypes/menus.py): OLC dbref search.
- [`evennia/contrib/full_systems/evscaperoom/commands.py`](evennia/contrib/full_systems/evscaperoom/commands.py): command target resolution.
- [`evennia/contrib/game_systems/clothing/clothing.py`](evennia/contrib/game_systems/clothing/clothing.py): wear-style fallback.

Kwarg renames (`nofound_string`/`multimatch_string` →
`not_found`/`ambiguous`) applied at:

- [`evennia/commands/default/general.py`](evennia/commands/default/general.py): `drop`, `give`.
- [`evennia/contrib/game_systems/containers/containers.py`](evennia/contrib/game_systems/containers/containers.py): container target.

Stub docstrings in [`evennia/objects/object.py`](evennia/objects/object.py)
and [`evennia/game_template/typeclasses/`](evennia/game_template/typeclasses/)
updated to reflect the new method signatures.

**Migration (game-side):** any game calling `caller.search(quiet=True)`
must move to `caller.search_for(...)` and pattern-match. Any game
passing `nofound_string=` / `multimatch_string=` must rename to
`not_found=` / `ambiguous=`. Game-side `at_search_result` overrides do
not need to change.

### Tests

- New [`evennia/objects/tests/test_search_result.py`](evennia/objects/tests/test_search_result.py)
  — 11 pure unit tests over truthiness, fields, pattern matching, equality.
- Updated 5 search tests in
  [`evennia/objects/tests/test_objects.py`](evennia/objects/tests/test_objects.py)
  to assert `Found` / `Ambiguous` / `NotFound` shapes via `search_for`.
- Full `evennia.objects` (98), `evennia.commands` / `evennia.accounts`
  / `evennia.prototypes` (346), and affected contrib (22) suites pass.

### Docs — backlog

- S1 (typed settings objects) dropped from
  [`engine-api-architecture.md`](.agents/docs/engine-api-architecture.md)
  after a design pass: ~290 settings + ~1000 read sites for modest
  gain, with `OptionHandler` already covering the per-account
  cosmetic-preference use case. Note preserved in the doc explaining
  why a future revisit (if any) should not redo the full framework.
- Q1 marked shipped; the prompt at `.agents/prompts/Q1-search-result-type.md`
  and the dropped `.agents/prompts/S1-settings-objects.md` removed.

---

## 6.0.0+underspire.44 — H1 hook registry

Ships [`evennia.hooks`](evennia/hooks/__init__.py), a descriptive
registry over every public engine hook. The `@hook(...)` decorator
attaches a `HookSpec` to each `at_*` / `get_*` / `return_*` method on
engine typeclasses; the lint surface validates the surface; a doc
generator writes back into the published docs. Dispatch stays direct
(engine still calls `obj.at_pre_move(...)` itself) — the registry is
metadata + validation, not a dispatcher. Companion to B1's published
contracts.

This release also ships three new `at_<noun>_post_creation` stubs to
close a long-standing asymmetry on Account/Channel/Script, and scrubs
internal H1/bucket phasing language from the published docs.

### Engine — hook registry surface

- New package `evennia/hooks/` with `HookSpec` (dataclass; validates
  `phase`, `returns`, `discipline`, `state_cache_state`), the
  `@hook(...)` decorator, and lookup functions:
  `hooks.describe(method)`, `hooks.for_event(name)`,
  `hooks.list_all()`, `hooks.lint()`.
- Decorator unwraps `classmethod` / `staticmethod` so the spec lives
  on the underlying function and works regardless of stacking order.
- Override registration is silent: a subclass that redefines a hook
  without `@hook` still inherits the parent spec via MRO lookup.
  Overrides with behavior worth recording may use
  `@hook(extends="ParentClass.method", notes="...")`.
- Flat-API export at `evennia.hooks` (lazy via `_LAZY_EXPORTS` in
  [`evennia/__init__.py`](evennia/__init__.py)).
- ~108 decorators applied across `TypedObject`, `LifecycleMixin`,
  `MovementMixin`, `AppearanceMixin`, `MessagingMixin`, `SearchMixin`,
  `DefaultObject`/`DefaultCharacter`/`DefaultRoom`/`DefaultExit`,
  `DefaultAccount`/`DefaultGuest`, `DefaultChannel`, `DefaultScript`,
  `ScriptBase`, `ServerSession`, `ObjectDB`, and the bot subclasses.

### Engine — symmetric post-creation hooks

`DefaultObject` has `at_object_creation` + `at_object_post_creation`
around the `_createdict` processing in `at_first_save`. Account,
Channel, and Script lacked the analog. Adds:

- [`DefaultAccount.at_account_post_creation`](evennia/accounts/accounts.py)
- [`DefaultChannel.at_channel_post_creation`](evennia/comms/comms.py)
- [`ScriptBase.at_script_post_creation`](evennia/scripts/scripts.py)

Each is wired into the existing `at_first_save` flow, fires once
after `_createdict` processing and engine-side setup, defaults to
`pass`, and is registered with
`@hook(event=<noun>_creation, phase=post)`.

**Migration:** new public override surface. Existing games that
override `at_first_save` directly continue to work. Games that wanted
"run after creation _and_ after `_createdict`-driven setup" no longer
need to subclass `at_first_save`; they can override the new hook
instead.

### Engine — startup lint

[`evennia.hooks.warn_at_startup`](evennia/hooks/lint.py) runs in
`ServerService.run_init_hooks`. Findings (MISSING_DECORATOR,
UNRESOLVED_FIRES_FROM, PHASE_MISMATCH) log to the twisted logger
without raising — an engine documentation issue cannot block a game
from booting. Strict enforcement lives in the test suite (see
**Tests** below).

Lint scope:
- Walks engine-side subclasses of registered base typeclasses + the
  five mixins + `TypedObject` + `ServerSession` (recursively via
  `__subclasses__`), then filters to `evennia.*`.
- `evennia.contrib.*` and `evennia.game_template.*` excluded: those
  are game-shaped, not engine.
- Class-level aliases (`get_absolute_url = web_get_detail_url` style)
  detected by qualname mismatch and skipped.
- `fires_from` resolver searches the engine class set, MRO ancestors,
  and a small list of known handler classes (`CmdSetHandler`,
  `LockHandler`).

### Engine — doc generator

[`evennia.hooks.docs`](evennia/hooks/docs.py) emits markdown tables
from the registry and rewrites the published docs between marker
pairs:

```
<!-- hooks-gen:start <section> -->
...generated content...
<!-- hooks-gen:end -->
```

Two sections ship:
- `return-contracts` — every registered hook grouped by declared
  returns category. Lands in
  [`Typeclass-Hooks-Reference.md`](docs/source/Components/Typeclass-Hooks-Reference.md)
  §3.7 as the authoritative roster; hand-curated 3.1–3.6 stay for
  nuance.
- `misshapen` — PHASE_MISMATCH findings plus specs that self-flag in
  notes. Lands in
  [`Typeclass-Hooks.md`](docs/source/Components/Typeclass-Hooks.md)
  after §6.

CLI:

```
python -m evennia.hooks.docs --check     # exit 1 if stale
python -m evennia.hooks.docs --write     # rewrite in place
```

### Docs — migration into Sphinx tree

- [`.agents/docs/typeclass-hooks.md`](.agents/docs/) moved to
  [`docs/source/Components/Typeclass-Hooks.md`](docs/source/Components/Typeclass-Hooks.md)
  and wired into `Components-Overview.md` under Base components.
- [`.agents/docs/typeclass-hooks-reference.md`](.agents/docs/) moved
  to [`docs/source/Components/Typeclass-Hooks-Reference.md`](docs/source/Components/Typeclass-Hooks-Reference.md).
- Internal phasing language (H1, H1a-H1e, R/S/D/H bucket names,
  "Phase C cleanup inputs", "design predecessor to H1") scrubbed from
  the published pages. Strikethrough migration notes preserved
  ("Renamed from `at_X`") for game-dev reference.
- §7 "Cleanup triage" deleted — it was internal disposition tracking
  for in-progress work; all items shipped or have superseding
  references and history lives in git log.
- New entry in [`FUTURE-IDEAS.md`](FUTURE-IDEAS.md) for the deferred
  `appearance_template` ↔ `get_display_*` slot-registry coupling.
- H1 entry in
  [`engine-api-architecture.md`](.agents/docs/engine-api-architecture.md)
  expanded with the locked design (schema, enforcement scope, dispatch
  mode, API surface, five sub-phases, H-bucket forced decisions).

### Tests

New tests in `evennia/hooks/tests/`:

- [`test_registry.py`](evennia/hooks/tests/test_registry.py) (15 tests)
  — HookSpec field validation, decorator behavior including duplicate
  detection, `extends` overrides, save/restore registry pattern, and
  a pilot integration test asserting
  `LifecycleMixin.at_pre_puppet` round-trips through the registry.
- [`test_lint.py`](evennia/hooks/tests/test_lint.py) (8 tests) —
  per-check coverage plus
  `PilotIntegrationTest.test_engine_lint_is_clean`, which asserts
  `lint()` returns zero findings. **This is the CI gate**: any new
  engine hook added without `@hook` (and without inheriting one)
  fails this test, so the issue surfaces at `evennia test` time
  rather than at server boot.
- [`test_docs.py`](evennia/hooks/tests/test_docs.py) (10 tests) —
  renderer behavior plus `PublishedDocsInSyncTest`, which fails if
  the published docs are stale relative to the registry. Run
  `python -m evennia.hooks.docs --write` to regenerate after
  decorator changes.

Full suite passes (~2000 tests).

---

## 6.0.0+underspire.43 — Engine/game boundary migration Phase A

Phase A of the engine/game boundary migration plan
([`engine-boundary-migration.md`](.agents/docs/engine-boundary-migration.md)).
Four small items bundled in one release: language-agnostic polish on
`AppearanceMixin`, lazy flat-API plumbing in `evennia/__init__.py`,
invalidation-contract docstring on `bump_cmdset_generation`, and a fix
for the `at_sync` reattach bug (puppet hooks now fire on server
reload). Lock-step with downstream; no deprecation aliases.

### Engine — A1 language-agnostic polish

Three remaining hardcoded-English sites in
[`evennia/objects/mixins/appearance.py`](evennia/objects/mixins/appearance.py)
move behind the same seam pattern Bundle 2 used.

- `get_display_exits` no longer hardcodes the `_("Exits")` prefix.
  The label now routes through the existing
  `get_content_group_label("exits", looker)` hook (default `""`), and
  the helper drops the prefix entirely when the hook returns empty
  (same `if not exit_names: return ""` short-circuit
  `get_display_characters` / `get_display_things` already use). Stock
  `look` therefore no longer prints `Exits: ...`; overrides returning
  a non-empty label restore the old prefix.
- New `get_self_pronoun(looker, **kwargs)` method replaces the three
  hardcoded `_("You")` substitutions in `at_say`'s self/receiver/
  location mappings. Default still returns `_("You")`, but the
  per-receiver branch now resolves the pronoun inside the loop with
  the receiver as `looker`, opening viewer-aware variation without
  another round of plumbing.
- New `list_endsep` class attribute (default `_(", and")`) replaces
  the three `iter_to_str(..., endsep=_(", and"))` call sites in
  `get_display_exits`, `get_display_characters`, and
  `get_display_things`. Class attribute is sufficient because list
  joining has no plausible viewer-aware variation.

The Bundle 2 audit items (movement broadcasts, channel echo,
`get_numbered_name` English pluralization) are deferred to
opportunistic work-as-touched; the Bundle 2 / Phase A seam pattern is
the template for future touches.

**Migration:** games that relied on the stock `Exits:` prefix should
override `get_content_group_label("exits", looker)` to return
`"Exits"` (or whatever language string they want). No other action
required.

### Engine — A2 lazy flat-API hygiene

[`evennia/__init__.py`](evennia/__init__.py) replaces the
triple-declaration pattern (top-level `X = None`, `global X` in
`_init`, `from .y import X` in `_init`) with a single registry plus
PEP 562 module-level `__getattr__`:

- `_LAZY_EXPORTS` maps each pure-import name to a `"module:attr"`
  spec. Submodule entries are spelled `".utils.ansi:"` (empty attr).
  Resolution happens on first attribute access; the resolved value is
  cached back into module globals so subsequent access is a plain
  dict lookup.
- `_INIT_POPULATED` lists the names that can't be expressed as pure
  imports: dynamic container instances (`managers`, `default_cmds`,
  `syscmdkeys`) and portal-vs-server boot state (`SESSION_HANDLER`,
  `GLOBAL_SCRIPTS`, `EVENNIA_*_SERVICE`, etc.). Those stay `None`
  until `_init` runs.
- Explicit `__all__` (union of both, sorted) declares the public
  flat-API surface so `from evennia import *` and the `DOCSTRING`
  enumeration both work from a single declared list rather than a
  `globals()` scan.

`_init` shrinks from 60+ globals + imports to just the dynamic
container construction and portal/server gating it actually needs.

**Bootstrap-edge-case risk:** code paths that touch the flat API
before Django setup will now trigger lazy load (and surface
`ImproperlyConfigured`) instead of receiving the historical `None`
sentinel. Today's callers all run after `_init`, so this should be
safe. The engine-boundary doc notes the fallback (registry-driven
explicit `_init`) if CI surfaces a violator.

The dead `inputhandler = None` top-level declaration (never assigned
in `_init`, no in-tree consumer) is dropped.

### Engine — A3 `bump_cmdset_generation` invalidation contract

[`evennia/commands/location_cmdset_cache.py`](evennia/commands/location_cmdset_cache.py)
keeps the hook (no engine callers outside its own cache, but the only
known external consumer would otherwise have to monkey-patch the
merge path to keep its viewer-aware display-name cache coherent) and
adds an explicit docstring covering:

- when callers must fire it (any cmdset stack mutation or location
  move on objects that share a room),
- what the cache machinery guarantees in exchange (stale rows
  bypassed on subsequent lookups; not eagerly evicted; bounded by
  `LOCATION_CMDSET_CACHE_MAXSIZE`),
- who the intended consumers are (anyone pairing per-caller
  display/permission filtering with cached cmdset lookups, fork-side
  game today).

A test (`TestLocationCmdsetCache.test_bump_cmdset_generation_docstring_describes_contract`)
pins the docstring so it can't silently rot in a future cleanup pass.

### Engine — A4 `at_sync` reattach fires puppet hooks

Pre-existing bug:
[`evennia/server/serversession.py`](evennia/server/serversession.py)'s
`at_sync` re-bound `session.puppet` after a server reload (the
`puid` path) without firing `at_pre_puppet` or `at_post_puppet`. Any
non-persistent state the game built in the puppet path — most
visibly the merged cmdset stack — was lost on every reload.

`at_sync` now calls `obj.at_pre_puppet(account, session=self,
reattach=True)` (a veto clears `puid`/`puppet` and skips the
reattach) and then `obj.at_post_puppet(reattach=True)` after the
connection state is wired.

Default `at_post_puppet` in both
[`evennia/objects/mixins/lifecycle.py`](evennia/objects/mixins/lifecycle.py)
and [`evennia/objects/character.py`](evennia/objects/character.py)
short-circuits on `kwargs.get("reattach")` so the per-puppet
"You become X" echo, look output, and "X has entered the game" room
broadcast don't fire on every reload. Game-side overrides receive the
same kwarg and can decide what to skip.

Contrib fix:
[`evennia/contrib/base_systems/ingame_python/typeclasses.py`](evennia/contrib/base_systems/ingame_python/typeclasses.py)'s
`Character.at_post_puppet` signature gains `**kwargs` to accept the
new keyword; behavior unchanged. The contrib's events still fire on
reattach; gating them on `reattach=True` is a contrib decision.

Fix is deliberately minimal; Phase C (identity model) will reshape
this territory and a larger refactor now risks being undone.

**Migration:** games that override `at_post_puppet` and want to skip
their own reattach-time work should branch on
`kwargs.get("reattach")`. Existing overrides without the branch
behave as before, just firing one extra time per server reload.

### Tests

- New
  [`evennia/objects/tests/test_objects.py::TestExitsContentGroupLabel`](evennia/objects/tests/test_objects.py),
  `TestSelfPronounHook`, `TestListEndsep` — cover the A1 hooks at
  stock and overridden values.
- Updated `test_exit_order` to reflect the no-prefix default.
- Updated
  [`evennia/contrib/tutorials/evadventure/tests/test_rooms.py`](evennia/contrib/tutorials/evadventure/tests/test_rooms.py)
  for the same.
- New
  [`evennia/server/tests/test_flat_api.py`](evennia/server/tests/test_flat_api.py) —
  every lazy export resolves; resolution caches; `__all__` matches
  the union of `_LAZY_EXPORTS` and `_INIT_POPULATED`; unknown names
  raise `AttributeError`.
- New `TestLocationCmdsetCache.test_bump_cmdset_generation_docstring_describes_contract`
  in [`evennia/commands/tests.py`](evennia/commands/tests.py).
- New
  [`evennia/server/tests/test_misc.py::TestAtSyncFiresPuppetHooks`](evennia/server/tests/test_misc.py) —
  reattach fires hooks with `reattach=True`; veto aborts the
  reattach; default `at_post_puppet` suppresses echo on reattach.

---

## 6.0.0+underspire.42 — Engine/game boundary migration Bundle 2

Three items from the engine/game boundary migration plan
([`engine-boundary-migration.md`](.agents/docs/engine-boundary-migration.md)).
Lock-step with downstream; no deprecation aliases. Stock cosmetic
break: `say` and `whisper` no longer broadcast English templates by
default, and room `look` output drops the `Characters:` / `You see:`
prefixes. Real games override the new hooks (or already overrode
`at_say` / `get_display_*` wholesale) and feel no change.

### Engine — `at_say` template hooks

[`evennia/objects/mixins/appearance.py`](evennia/objects/mixins/appearance.py)
gains three overridable methods on `AppearanceMixin`:

- `get_say_template_self(whisper=False, **kwargs) -> ""`
- `get_say_template_location(whisper=False, **kwargs) -> ""`
- `get_say_template_receivers(whisper=False, **kwargs) -> ""`

All default empty. `at_say` sources its `msg_self`, `msg_location`, and
`msg_receivers` defaults through the hooks; the existing
`if msg_self:` / `if msg_location:` / `if receivers and msg_receivers:`
guards short-circuit on empty so no `msg`/`msg_contents` call is made.
The literal English templates (`'{self} say, "..."'`,
`'{object} says, "..."'`, the whisper variants) are removed from the
engine body.

The opinion (English phrasing, vocative comma, quote styling) was the
only game-shaped piece of `at_say`. The broadcast machinery
(mapping substitution, `type=say`/`type=whisper` markers, exclude-list
handling for `msg_contents`) is generic and stays in the engine, so
overrides reuse it rather than re-implementing it.

`at_pre_say` is unchanged; the Bundle 1.5 transform-rule contract
remains the authoritative pre-hook surface.

### Engine — `get_content_group_label` hook

[`evennia/objects/mixins/appearance.py`](evennia/objects/mixins/appearance.py)
gains `get_content_group_label(group, looker, **kwargs) -> ""`.

`get_display_characters` calls it with `group="characters"`;
`get_display_things` with `group="things"`. When the hook returns `""`
(stock default), the helpers render the names without a prefix. When
the content group is empty, the helpers return `""` outright so
`format_appearance` collapses cleanly with no orphan blank line — the
same pattern as the `{extra_state}` fix in
[`+underspire.40`](#600underspire40---engine-boundary-migration-bundle-1).

Stock `look` therefore drops the `Characters:` and `You see:` lines.
Override returning a non-empty label restores the old prefix.
`clothing` and `tutorial_world` already build their own labels and are
unaffected.

### Engine — Cmdset merge cache warmup

New module: [`evennia/commands/cmdset_merge_warmup.py`](evennia/commands/cmdset_merge_warmup.py).
Public surface:

- `warm_cmdset_merge_for_session(session)` — runs
  `generate_cmdset_providers` + `get_and_merge_cmdsets` for one session,
  no command parse, no NOINPUT message.
- `schedule_cmdset_merge_warmup_for_character(character)` — defers to
  the next reactor tick and warms every session puppeting the character.
- `warm_all_logged_in_puppet_sessions()` — warms every logged-in,
  puppeted session in `SESSION_HANDLER`.

Always on; no setting. Wired into two call sites:

- [`evennia/accounts/accounts.py`](evennia/accounts/accounts.py)
  `puppet_object` invokes
  `schedule_cmdset_merge_warmup_for_character(obj)` after
  `at_post_puppet` and the `SIGNAL_OBJECT_POST_PUPPET` signal.
- [`evennia/server/service.py`](evennia/server/service.py)
  `at_post_portal_sync` invokes `warm_all_logged_in_puppet_sessions()`
  when `mode == "reload"`.

The original downstream module used bare `except: pass` at every
boundary; the engine version replaces those with `logger.log_trace` so
real failures surface in logs. The perf claim is about perception, not
throughput: users tolerate a multi-second pause at login or after a
reload (those are moments where a pause reads as normal), but the
same pause on the first typed command reads as a hung server. The
warmup eats the merge cost during the tolerable window.

### Contrib

[`evennia/contrib/base_systems/ingame_python/typeclasses.py`](evennia/contrib/base_systems/ingame_python/typeclasses.py)
`EventCharacter` overrides the three new template hooks to restore the
old English templates, so its `at_say` body's `super().at_say(...)`
call continues to broadcast. The contrib's behavior is unchanged from
`+underspire.41`.

### Tests

[`evennia/objects/tests/test_objects.py`](evennia/objects/tests/test_objects.py):

- `TestSayTemplateHooks` — stock `at_say` broadcasts nothing;
  overrides on each template hook restore the corresponding send.
- `TestContentGroupLabel` — stock characters/things sections omit the
  prefix; override restores `Characters:`; empty group returns `""`
  with no orphan whitespace.

[`evennia/commands/tests.py`](evennia/commands/tests.py)
`TestCmdsetMergeWarmup` covers the warmup entry points: no-sessions
noop, with-sessions deferral via `delay(0, ...)`, and the skip path
for non-puppeted sessions.

[`evennia/commands/default/tests.py`](evennia/commands/default/tests.py)
`test_say`, `test_whisper`, and `test_force` updated to assert the new
empty-default broadcast (the cmd still runs without error, but no
speech reaches self/location/receivers absent an override).

### Migration notes for downstream

- Any local `at_say` override that calls `super().at_say(...)` and
  relied on the engine's English templates must override the three new
  hooks (recommended) or pass its own `msg_self`/`msg_location`/
  `msg_receivers` to `super().at_say(...)` explicitly. Overriding the
  hooks is one screenful and preserves the engine's broadcast
  machinery; overriding `at_say` wholesale is the legacy path.
- Any local `get_display_characters` / `get_display_things` override
  that relied on the engine's `Characters:` / `You see:` prefixes
  being present must inject its own prefix via `get_content_group_label`
  (recommended) or hardcode the label string in the override.
- The downstream `world/cmdset_merge_warmup.py` module is now redundant.
  Remove it and the two call sites
  (`server/conf/at_server_startstop.py` post-reload, and the per-puppet
  scheduling call in the character typeclass's `at_post_puppet`).
  Engine wiring fires both automatically.

A downstream migration prompt covering the audit checklist will be
shipped alongside the tag.

---

## 6.0.0+underspire.41 — Universal veto rule + transform rule for pre-hooks

Mid-bundle correction landed between `+underspire.40` (engine/game
boundary migration Bundle 1) and Bundle 2. Closes a footgun the Bundle 1
rename sweep exposed: the existing veto convention (`if not result:`)
silently blocked an operation any time an override forgot the explicit
`return True`. The contrib sweep in `+underspire.40` had to add
`return True` to three former `at_object_leave` bodies to keep them
working as `at_pre_leave` overrides. Same footgun lurks for transform
hooks (`at_pre_say`, `at_pre_msg`, `at_pre_channel_msg`) in a different
shape.

`+underspire.41` adopts two canonical rules across every pre-hook in
the engine and updates every call site to match.

### Engine — new helpers

- [`evennia/utils/utils.py`](evennia/utils/utils.py):
  **`is_veto(result)`**. Canonical check for veto-capable pre-hooks.
  Returns `True` if the hook return should abort. Rule: **falsy AND not
  None vetoes**. `False`, `0`, `""`, `[]`, `()` veto. `None` does NOT
  veto (treated as "no opinion / allow"). The carve-out for `None` is
  the footgun fix: forgetting `return True` no longer silently blocks.
- [`evennia/utils/utils.py`](evennia/utils/utils.py):
  **`resolve_transform(result, original)`**. Canonical resolver for
  transform-style pre-hooks. Returns the hook's transformed value, or
  `original` when the hook returned `None`, or the non-None falsy as-is
  (so the caller's existing `if not result: return` check still aborts
  cleanly). Symmetric closure of the same footgun for transform hooks.

### Engine — vetoable pre-hooks (all updated to `is_veto` rule)

`at_pre_move`, `at_pre_leave`, `at_pre_arrive` in
[`movement.py`](evennia/objects/mixins/movement.py); `at_pre_traverse`
in [`exit.py`](evennia/objects/exit.py); `at_pre_rename` in
[`typeclasses/models.py`](evennia/typeclasses/models.py); `at_pre_get`,
`at_pre_drop`, `at_pre_give` in
[`appearance.py`](evennia/objects/mixins/appearance.py). Docstrings
rewritten to spell out the new rule and link `is_veto`.

**New vetoable hook**: `at_pre_puppet` is now veto-capable. Previously
the engine called it for notification only and ignored the return.
[`evennia/accounts/accounts.py`](evennia/accounts/accounts.py)
`puppet_object` now checks `is_veto(obj.at_pre_puppet(...))` and bails
silently on veto (the override is expected to `account.msg(...)` an
explanation before returning `False`). Replaces the existing
"subclass `puppet_object` to gate puppeting" pattern with a clean hook.

### Engine — transform pre-hooks (all updated to `resolve_transform` rule)

`at_pre_say` in
[`appearance.py`](evennia/objects/mixins/appearance.py); `at_pre_msg`
in [`comms/comms.py`](evennia/comms/comms.py); `at_pre_channel_msg` in
[`accounts/accounts.py`](evennia/accounts/accounts.py). Call sites in
[`commands/default/general.py`](evennia/commands/default/general.py)
(say + whisper) and
[`comms/comms.py`](evennia/comms/comms.py) (channel send) wrap the
hook call with `resolve_transform(hook_result, original_message)`,
preserving the existing `if not message: abort` check downstream.

Docstrings rewritten to document the new contract:

- Non-empty string replaces the message.
- `False` or `""` aborts.
- `None` falls back to the original message (was: abort).

### Engine — non-vetoable pre-hooks (documented as exceptions)

`at_pre_unpuppet` in
[`lifecycle.py`](evennia/objects/mixins/lifecycle.py) and
`at_pre_login` in
[`accounts/accounts.py`](evennia/accounts/accounts.py) explicitly
document that the return value is ignored. These run during session
teardown / login where vetoing would strand state between the engine
and the underlying transport.

### Engine — command pre-hooks (documented as exceptions, inverted convention)

`at_pre_parse` and `at_pre_cmd` in
[`commands/command.py`](evennia/commands/command.py) keep the inverted
"truthy aborts" convention they shipped with under
`+underspire.2`/`+underspire.4`. Docstrings now call out the exception
explicitly so it doesn't read as a bug against the universal rule. The
truthy return is a useful error code / message that the cmdhandler
propagates back to the caller; that semantic predates this rule and is
worth preserving.

### Contrib sweep

Call sites in
[`game_systems/containers/containers.py`](evennia/contrib/game_systems/containers/containers.py)
(plus the container-internal `at_pre_get_from` /
`at_pre_put_in` hooks),
[`game_systems/storage/storage.py`](evennia/contrib/game_systems/storage/storage.py),
[`grid/wilderness/wilderness.py`](evennia/contrib/grid/wilderness/wilderness.py),
and [`tutorials/evadventure/commands.py`](evennia/contrib/tutorials/evadventure/commands.py)
updated from the old `if not hook(...)` pattern (or the inverse
`if hook(...)` in storage) to `is_veto(hook(...))`.

Stale docstrings in
[`turnbattle/tb_basic.py`](evennia/contrib/game_systems/turnbattle/tb_basic.py),
[`turnbattle/tb_range.py`](evennia/contrib/game_systems/turnbattle/tb_range.py),
[`containers.py`](evennia/contrib/game_systems/containers/containers.py),
and [`ingame_python/typeclasses.py`](evennia/contrib/base_systems/ingame_python/typeclasses.py)
swept to reference the new rule.

The `ingame_python` `at_pre_say` override in
[`typeclasses.py`](evennia/contrib/base_systems/ingame_python/typeclasses.py)
previously used `return` (bare, returns `None`) to mean "abort this
say." Under `+underspire.41` that would silently fall through to the
original message. Changed to `return False` on both abort paths.

### Tests

- [`evennia/utils/tests/test_utils.py`](evennia/utils/tests/test_utils.py):
  `TestIsVeto` (8 cases) and `TestResolveTransform` (5 cases) covering
  every value class against the rule.
- [`evennia/objects/tests/test_objects.py`](evennia/objects/tests/test_objects.py):
  `TestVetoRule` (8 cases) — implicit `None` allows for every veto hook;
  explicit `False` blocks; `0` also blocks (defensive). `TestAtPrePuppetVeto`
  (2 cases) — `False` blocks puppet attach, `None` allows.
  `TestTransformHookNoneRule` (3 cases) — `at_pre_say` with `None` uses
  original speech; with `False` aborts; with a string replaces.

### Migration — downstream sweep required

Lock-step fork must audit two behavior deltas. Both are silent failures:
your tests pass, your overrides run, behavior is quietly wrong.

**Delta 1 — veto hooks: implicit `None` now allows (was: blocked).**
Re-audit every override of `at_pre_move`, `at_pre_leave`, `at_pre_arrive`,
`at_pre_traverse`, `at_pre_rename`, `at_pre_get`, `at_pre_drop`,
`at_pre_give`, and `at_pre_puppet` (newly vetoable!). Branches that
returned `None` to abort under `+underspire.40` now allow. Change those
paths to `return False`.

**Delta 2 — transform hooks: `None` now falls back to original (was:
abort).** Re-audit every override of `at_pre_say`, `at_pre_msg`,
`at_pre_channel_msg`. Branches that `return` (bare) or `return None`
to abort now silently let the original message through. Change those
paths to `return False` or `return ""`.

**Delta 3 — bool-return audit (pre-existing latent bug).** Any
transform-hook override that does `return True` (or any non-string
truthy) puts a bool into the downstream message slot, causing a
confusing crash. Always existed; .41 docstrings make the misreading
more likely. Grep at migration time.

A full migration addendum has been issued to the downstream agent
alongside this release with the exact greps and case analysis.

---

## 6.0.0+underspire.40 — Engine/game boundary migration, Bundle 1 (hooks + naming sweep)

First batch of the upstream-PR sequencing tracked in
[`.agents/docs/engine-boundary-migration.md`](.agents/docs/engine-boundary-migration.md).
Lands the additive seams downstream needs in order to push fork-owned
infrastructure (multipuppet, pose/emote, follow, scene broadcast) up,
plus a long-overdue cleanup of asymmetric and ambiguous hook names.

This release is lock-step with the downstream game. No deprecation
aliases are shipped; renamed hooks must be mirrored in every override
on the game side. The downstream-facing breaking-change list is at the
bottom of this entry.

### Engine — new hooks

- [`evennia/accounts/accounts.py`](evennia/accounts/accounts.py):
  `DefaultAccount.at_puppet_added(character, session=None)` and
  `at_puppet_removed(character, session=None)`. Fire on **first-attach**
  and **last-detach** only; additional sessions attaching to a character
  the account is already puppeting (sharing in `MULTISESSION_MODE` 1/3,
  session takeover) do not trigger them — those are per-session events
  already covered by `Object.at_post_puppet` / `at_post_unpuppet`. The
  semantics match set membership in `get_all_puppets()`. Wired into the
  existing `puppet_object` / `unpuppet_object` paths via a
  `was_already_owned` flag captured before any takeover-unpuppet runs.
- [`evennia/objects/mixins/appearance.py`](evennia/objects/mixins/appearance.py):
  `AppearanceMixin.get_extra_display_state(looker)` plus the matching
  `{extra_state}` key in the default `appearance_template`. Empty by
  default; overrides return `""` for nothing or content prefixed with a
  leading newline for a separate line. The template appends
  `{extra_state}` directly to the name line so stock output is
  byte-identical when the hook is the default stub. Unlocks pose, AFK,
  mood, combat stance, and similar persistent-state lines without
  forcing games to rewrite `return_appearance`.
- [`evennia/objects/mixins/movement.py`](evennia/objects/mixins/movement.py):
  `DefaultObject.at_post_leave(moved_obj, target_location, ...)` fires
  on the source location **after** the location change. Mirrors the
  existing `at_post_arrive` timing on the destination; the pair is
  unordered relative to each other, both fire before mover-side
  `at_post_move`.
- [`evennia/objects/mixins/movement.py`](evennia/objects/mixins/movement.py):
  `DefaultObject.at_pre_traverse(traversing_object, target_location, ...)`
  fires inside `do_traverse` before the move is attempted. Return
  `False` to route to `at_failed_traverse` and abort. Closes the
  missing pre-side of the traverse lifecycle.
- [`evennia/typeclasses/models.py`](evennia/typeclasses/models.py):
  `TypedObject.at_pre_rename(oldname, newname)` fires before the
  rename commits, with veto semantics. Identity renames
  (`oldname == newname`) now no-op and skip both hooks and
  `SIGNAL_TYPED_OBJECT_POST_RENAME` rather than firing them with
  unchanged values.

### Engine — renames (no aliases)

- `at_pre_object_leave`   → `at_pre_leave`
- `at_pre_object_receive` → `at_pre_arrive`
- `at_object_receive`     → `at_post_arrive`
- `at_object_leave`       → dropped; existing bodies should move into
  `at_pre_leave` (with `return True`) for "object still here" semantics,
  or into `at_post_leave` for "object has departed" semantics. Most
  existing overrides want the former.
- `at_init` → `at_post_load` on every typeclass
  ([`TypedObject`](evennia/typeclasses/models.py),
  [`DefaultObject`](evennia/objects/mixins/lifecycle.py),
  [`DefaultExit`](evennia/objects/exit.py),
  [`DefaultAccount`](evennia/accounts/accounts.py),
  [`DefaultBot`](evennia/accounts/bots.py),
  [`DefaultScript`](evennia/scripts/scripts.py),
  [`DefaultChannel`](evennia/comms/comms.py)). The historical name
  suggested object creation; it actually fires on **idmapper cache load**
  (every initial fetch and every server reload). New name says what it
  does. All internal callers updated:
  [`utils/idmapper/models.py`](evennia/utils/idmapper/models.py),
  [`server/sessionhandler.py`](evennia/server/sessionhandler.py),
  [`server/at_init_scheduler.py`](evennia/server/at_init_scheduler.py),
  [`server/service.py`](evennia/server/service.py),
  [`web/admin/comms.py`](evennia/web/admin/comms.py),
  [`web/admin/objects.py`](evennia/web/admin/objects.py),
  [`commands/default/building.py`](evennia/commands/default/building.py).
  Settings comments (`AT_INIT_BATCH_SIZE` etc.) reworded; the setting
  names themselves are unchanged.
- `at_traverse` → `do_traverse` on
  [`DefaultObject`](evennia/objects/mixins/movement.py) and
  [`DefaultExit`](evennia/objects/exit.py). The method performs the
  move; the `at_*` prefix wrongly implied a notification hook. The
  three notification hooks (`at_pre_traverse`, `at_post_traverse`,
  `at_failed_traverse`) keep their names. `do_traverse` now calls
  `at_pre_traverse` first and routes False to `at_failed_traverse`
  before attempting the move.

### Contrib sweep

All in-tree contrib overrides and direct callers of the renamed hooks
are updated to match. No behavior change in contribs.

- Tutorial world: [`rooms.py`](evennia/contrib/tutorials/tutorial_world/rooms.py)
  (9 override renames; the three former `at_object_leave` bodies now
  return `True` after their cleanup),
  [`mob.py`](evennia/contrib/tutorials/tutorial_world/mob.py),
  [`objects.py`](evennia/contrib/tutorials/tutorial_world/objects.py),
  [`tests.py`](evennia/contrib/tutorials/tutorial_world/tests.py)
  (direct calls in the test fixtures).
- Evadventure: [`characters.py`](evennia/contrib/tutorials/evadventure/characters.py)
  (4 overrides; the equipment-remove side effect previously split
  between `at_pre_object_leave` and `at_object_leave` is now folded
  into the single renamed `at_pre_leave`),
  [`dungeon.py`](evennia/contrib/tutorials/evadventure/dungeon.py),
  [`combat_twitch.py`](evennia/contrib/tutorials/evadventure/combat_twitch.py),
  [`tests/test_dungeon.py`](evennia/contrib/tutorials/evadventure/tests/test_dungeon.py).
- Evscaperoom: [`room.py`](evennia/contrib/full_systems/evscaperoom/room.py),
  [`commands.py`](evennia/contrib/full_systems/evscaperoom/commands.py),
  [`menu.py`](evennia/contrib/full_systems/evscaperoom/menu.py).
- Wilderness: [`wilderness.py`](evennia/contrib/grid/wilderness/wilderness.py)
  (`do_traverse` plus the `at_pre_leave` / `at_post_arrive` overrides
  on `WildernessRoom`).
- Slow exit: [`slow_exit.py`](evennia/contrib/grid/slow_exit/slow_exit.py),
  [`tests.py`](evennia/contrib/grid/slow_exit/tests.py).
- Ingame Python: [`typeclasses.py`](evennia/contrib/base_systems/ingame_python/typeclasses.py)
  (`do_traverse` override).
- Game template: [`objects.py`](evennia/game_template/typeclasses/objects.py),
  [`accounts.py`](evennia/game_template/typeclasses/accounts.py),
  [`channels.py`](evennia/game_template/typeclasses/channels.py)
  (docstring hook listings updated to match).

### Test-infra fixes

Both fix pre-existing failures on `underspire` HEAD that surfaced when
this branch's tests ran against the full affected sweep.

- [`evennia/scripts/tickerhandler.py`](evennia/scripts/tickerhandler.py):
  `_schedule_save` now detects `settings.TEST_ENVIRONMENT` and saves
  synchronously instead of going through `reactor.callLater`. Trial
  runs each test on a stub reactor that never drains queued calls;
  the pending `_do_scheduled_save` lingered into teardown and tripped
  `DirtyReactorAggregateError` in
  `evennia.server.tests.test_amp_connection.TestAMPClientRecv.test_adminportal2server`.
  Production behavior unchanged (still deferred and coalesced on the
  real reactor). The existing reactor-not-running fallback also clears
  `_save_scheduled` before the synchronous save so the flag never gets
  stuck.
- [`evennia/contrib/grid/wilderness/tests.py`](evennia/contrib/grid/wilderness/tests.py):
  `TestWilderness.test_room_creation` was asserting `has_account` after
  `sessions.add(1)`, but the new char1/char2 objects created in the
  wilderness `setUp` overwrite the BaseEvenniaTest-attached chars and
  `sessions.add` never sets `db_account`. The "Pretend that both char1
  and char2 are connected" comment was aspirational. Now attaches
  `self.account` / `self.account2` to the new chars before adding
  sessions, so `has_account` reflects what the test claims to test.

### Tests

- [`evennia/accounts/tests.py`](evennia/accounts/tests.py):
  `TestAccountPuppetSetHooks` — three cases covering first-attach,
  session takeover (must not fire `at_puppet_added` again), and
  last-detach.
- [`evennia/objects/tests/test_objects.py`](evennia/objects/tests/test_objects.py):
  `TestExtraDisplayState` (stub doesn't introduce a blank line; override
  appears in output), `TestMovementHookRenames` (pre-leave / pre-arrive
  veto; post-leave fires with mover out of source; post-arrive fires
  with mover in destination), `TestAtPreRename` (veto, allow, identity
  rename skips hooks), `TestTraverseRefactor` (pre-traverse veto routes
  to failed; allow lets move through), `TestAtPostLoadRename` (hook is
  callable; `at_init` is no longer a class attribute).
- [`evennia/server/tests/test_server.py`](evennia/server/tests/test_server.py)
  and [`test_at_init_scheduler.py`](evennia/server/tests/test_at_init_scheduler.py):
  updated existing mock references from `at_init` to `at_post_load`.

### Docs

- [`.agents/docs/engine-boundary-migration.md`](.agents/docs/engine-boundary-migration.md)
  (new): full Bundle 1..4 plan reconciled with downstream, in
  implementation order. Indexed from
  [`AGENTS.md`](AGENTS.md) alongside the existing hygiene backlog and
  future-ideas docs.

### Migration — downstream sweep required

Lock-step fork must mirror these renames in every game-side override
and direct call. No aliases; old names will fail.

| Old | New |
|---|---|
| `at_pre_object_leave`   | `at_pre_leave` |
| `at_pre_object_receive` | `at_pre_arrive` |
| `at_object_receive`     | `at_post_arrive` |
| `at_object_leave`       | `at_pre_leave` (append `return True`) OR `at_post_leave` |
| `at_init`               | `at_post_load` |
| `at_traverse`           | `do_traverse` |
| `obj.at_traverse(...)`        (direct call) | `obj.do_traverse(...)` |
| `room.at_object_receive(...)` (direct call) | `room.at_post_arrive(...)` |
| `room.at_object_leave(...)`   (direct call) | `room.at_pre_leave(...)` |

### Audit follow-up

A hook-naming audit at the start of this bundle flagged
`at_msg_send` / `at_msg_receive` as potentially asymmetric. Inspection
of [`evennia/objects/mixins/messaging.py`](evennia/objects/mixins/messaging.py)
showed the auditor was wrong: `at_msg_send` already fires on the sender
and `at_msg_receive` on the receiver. No change needed there.

### Next bundle

Bundle 2 covers minor stock-output breaks bundled with release notes:
empty `at_say` templates, content-group label hook, cmdset merge cache
warmup utility. Tracked in
[`.agents/docs/engine-boundary-migration.md`](.agents/docs/engine-boundary-migration.md).

---

## 6.0.0+underspire.39 — Cache hit/miss metrics for the remaining three caches

Closes the engine cleanup checklist. Adds Prometheus hit/miss counters
to `location_cmdset_cache`, `channel_subscriber_cache`, and
`redis_attr_cache`, matching the pattern already in place for
`cmd_access_cache`. Operators can now answer "is this cache earning its
keep" with data instead of intuition.

No behavior change other than metric emission. All counters no-op when
`prometheus_client` is unavailable or
`ENGINE_PROMETHEUS_METRICS_ENABLED = False`.

### Engine

- [`evennia/server/prometheus_metrics.py`](evennia/server/prometheus_metrics.py):
  added six counters and six `record_*` helpers (one hit + one miss
  per cache). Registered on the default Prometheus registry inside
  `_init_metrics` next to the existing `CMD_ACCESS_CACHE_*` pair.
- [`evennia/commands/location_cmdset_cache.py`](evennia/commands/location_cmdset_cache.py):
  `get_cached_location_cmdsets` records hit when `_CACHE.get(key)`
  returns a value, miss otherwise. No-op when the cache is disabled.
- [`evennia/comms/channel_subscriber_cache.py`](evennia/comms/channel_subscriber_cache.py):
  `get_cached_subscribers` records hit when Redis already had the
  channel key populated, miss when the key was absent and required
  rebuild via `sync_channel_subscribers`. Redis-unavailable path
  doesn't increment either counter (already covered by the
  `redis unavailable` log line).
- [`evennia/typeclasses/redis_attr_cache.py`](evennia/typeclasses/redis_attr_cache.py):
  added local `_record_hit` / `_record_miss` helpers (so the three
  call sites stay readable) and wired them into `query_key`,
  `query_category`, and `query_all`. Hit counts include
  `_MISSING_MARKER` lookups (a negative cache hit is still a hit
  semantically). Misses count the PG fall-through path.

### Docs

- [`ENGINE.md`](ENGINE.md): listed the six new metric names under the
  attribute write-behind metrics section. Grouped by cache and
  conditioned on the matching `*_CACHE_ENABLED` setting.

### Migration

None. New counters appear on the `/metrics` endpoint automatically
when `django-prometheus` is installed and engine metrics are enabled
(the default).

### Closes

This release closes out the engine cleanup checklist that has been
tracked under `.fleet-review/engine-cleanup-checklist.md`. The
checklist file is being removed locally; the shipped record lives in
this changelog. Final tally of the Tier 2 work:

| Phase | Result | Release |
|---|---|---|
| 1 | Module-cached settings sweep | `+underspire.34` |
| 2 | Redis attr cache write-behind ordering | `+underspire.35` |
| 2b | Orphan-dirty flush retry on failure | `+underspire.38` |
| 3 | `except Exception:` narrowing | Opportunistic (see hygiene-backlog) |
| 4 | Cache layer unification | Dropped after research |
| 5 | `display_name_cache` moves to game | `+underspire.36` |
| 6 | Channel subscriber resolve batching + setting rename | `+underspire.37` |
| (this) | Hit/miss metrics for remaining caches | `+underspire.39` |

Phase 3 stays as opportunistic cleanup applied when touching
surrounding code (the wider `except *: pass` audit is also tracked in
[`.agents/docs/hygiene-backlog.md`](.agents/docs/hygiene-backlog.md)
under "Deferred audits"). Nothing else from the checklist is
outstanding.

---

## 6.0.0+underspire.38 — Orphan-dirty flush retry on failure (Phase 2b)

Closes the last item from the engine cleanup checklist. Fixes a silent
data-loss window in the orphan-dirty write-behind path discovered while
shipping Phase 2 (`+underspire.35`).

### Engine

- [`evennia/typeclasses/attributes.py`](evennia/typeclasses/attributes.py):
  `_flush_orphan_dirty` now mirrors the failure-handling shape of
  `ModelAttributeBackend.flush_dirty`. Previously it called
  `_ORPHAN_DIRTY_ATTRS.discard(attr)` for every dirty entry *before*
  calling `bulk_update`. If `bulk_update` raised (deadlock, connection
  drop, schema lock), the dirty entries were already gone from the
  retry set and the writes were silently lost. The backend path was
  hardened against this earlier (snapshot, `bulk_update`, then
  `difference_update` on success only — see comments at
  `attributes.py:1468-1473`); the orphan path was not.
  Fix: snapshot `dirty`, run `bulk_update`, then iterate the snapshot
  to discard from `_ORPHAN_DIRTY_ATTRS` only after the PG write
  succeeds. On exception the entries stay queued and the call
  re-raises so the next maintenance tick retries.

### Tests

- [`evennia/typeclasses/tests/test_attribute_fork.py`](evennia/typeclasses/tests/test_attribute_fork.py):
  added `test_orphan_flush_failure_keeps_attrs_queued`. Patches
  `Attribute.objects.bulk_update` to raise `RuntimeError`, calls
  `flush_all_dirty`, asserts the attr is still in `_ORPHAN_DIRTY_ATTRS`
  after the failure. Mirrors the existing
  `test_flush_failure_does_not_publish_redis` regression for the
  backend path.

### Migration

None. Behavior change is failure-only: successful flushes are
identical; failed flushes now retry instead of silently dropping
writes.

### Closes

Engine cleanup checklist
([`.fleet-review/engine-cleanup-checklist.md`](.fleet-review/engine-cleanup-checklist.md))
is now closed for the second tier of work. Phase 3 (`except Exception:`
narrowing) stays as opportunistic cleanup applied when touching
surrounding code, not a phase. Phase 4 stayed dropped. Phase 5, 6, and
2b shipped across `+underspire.36`, `.37`, `.38`. A deferred follow-up
(hit/miss metrics for the three engine caches that lack them) is noted
under Phase 6 but isn't scheduled.

---

## 6.0.0+underspire.37 — Channel subscriber resolve batching + setting rename (Phase 6)

Closes out the engine cleanup checklist's lingering items on the
`channel_subscriber_cache` surface.

### Engine

- [`evennia/comms/channel_subscriber_cache.py`](evennia/comms/channel_subscriber_cache.py):
  `_resolve_refs` now batches PG lookups. Previously the function
  looped `AccountDB.objects.get(id=pk)` / `ObjectDB.objects.get(id=pk)`
  once per ref, so a broadcast to N online subscribers cost N PG
  round-trips even on a Redis cache hit. The function now groups refs
  by kind, runs one `filter(pk__in=...)` per kind (max two queries
  total regardless of subscriber count), then replays the input order
  to preserve the prior return-order contract. Stale refs (pk no
  longer in DB) are silently skipped exactly as before. Bulk-resolve
  failure is now `log_trace`'d once per kind instead of swallowed
  silently per ref.

### Settings

- [`evennia/settings_default.py`](evennia/settings_default.py):
  renamed `CHANNEL_SUBSCRIBER_CACHE_REDIS_ALIAS` to
  `CHANNEL_SUBSCRIBER_CACHE_ALIAS`. The redundant `REDIS_` qualifier in
  the suffix was the one real inconsistency across the cache settings
  surface (the cache module's name already implies Redis as the
  backing store, the way `ATTRIBUTE_REDIS_CACHE_ALIAS` doesn't need a
  second `_REDIS_`). All other cache settings already follow a
  consistent `<PREFIX>_CACHE_<KNOB>` shape.

### Migration

Downstream games using `CHANNEL_SUBSCRIBER_CACHE_REDIS_ALIAS` in
`settings.py` must rename it to `CHANNEL_SUBSCRIBER_CACHE_ALIAS`. No
deprecation shim — coordinate with this release. Default value
unchanged (`"default"`).

### Tests

`evennia.comms` test suite (8 tests) passes.

### Closes

This is the last item from
[`.fleet-review/engine-cleanup-checklist.md`](.fleet-review/engine-cleanup-checklist.md)
worth addressing now. Remaining work on the checklist is Phase 2b
(orphan-flush retry on failure, still queued) and Phase 3 (`except
Exception:` narrowing, opportunistic). Phase 4 (cache unification)
stays dropped; Phase 5 shipped in `.36`. A deferred follow-up
(hit/miss metrics for the four engine caches that lack them) is
noted in Phase 6 of the checklist but isn't scheduled — reopen when
there's a concrete cache question worth measuring.

---

## 6.0.0+underspire.36 — Display-name cache moves to game (Phase 5)

Removes `evennia.utils.display_name_cache` from the engine. The cache
existed to memoize `obj.get_display_name(looker)` per looker, but its
invalidation triggers (`bump_recog_generation` / `bump_sdesc_generation`)
were never called from core. They were only ever called by downstream
games (rpsystem contrib, Underspire) that override `get_display_name`
with sdesc/recog logic expensive enough to warrant caching. Stock
Evennia paid the coordination cost (per-looker ndb dict, generation
protocol on objects, TTL bookkeeping, two settings, conditional imports
in `msg_contents` and `funcparser`) to memoize a function whose default
body is roughly a lockstring check plus an f-string.

Concretely, the downstream coupling included a reach-in to engine
private symbols (`_cache`, `_enabled`, `_perception_gen`, `_ttl`) to
build a custom cache key, and a `bump_*_generation` call paired
immediately with `invalidate_display_name_cache` because the generation
protocol alone wasn't trusted. Both go away when the cache lives next
to the override that makes display-name resolution expensive.

`get_display_name` itself remains the hook. Only the cache around it
moves. Games with cheap default `get_display_name` see no change; games
with expensive overrides (Underspire, rpsystem) own their own
memoization and invalidation policy.

### Engine

- [`evennia/utils/display_name_cache.py`](evennia/utils/display_name_cache.py):
  deleted. Module exported `cached_get_display_name`,
  `invalidate_display_name_cache`, `bump_recog_generation`,
  `bump_sdesc_generation`. All gone.
- [`evennia/utils/tests/test_display_name_cache.py`](evennia/utils/tests/test_display_name_cache.py):
  deleted.
- [`evennia/objects/mixins/messaging.py`](evennia/objects/mixins/messaging.py):
  `msg_contents` now calls `obj.get_display_name(looker=receiver)`
  directly inside the per-receiver display_names mapping. The
  try/except cache import with inline fallback is gone.
- [`evennia/utils/funcparser.py`](evennia/utils/funcparser.py):
  the `$you` and `$your` callables now call
  `caller.get_display_name(looker=receiver)` directly. Both sites had
  the same try/except cache pattern; both are now the plain call.

### Settings

- [`evennia/settings_default.py`](evennia/settings_default.py):
  removed `MSG_DISPLAY_NAME_CACHE_ENABLED` and
  `MSG_DISPLAY_NAME_CACHE_TTL`.

### Migration

Downstream games that called `bump_recog_generation`,
`bump_sdesc_generation`, `invalidate_display_name_cache`, or imported
`cached_get_display_name` from `evennia.utils.display_name_cache` must
move that logic into their own `get_display_name` override on the
relevant typeclass. The override is where the cache, the key, and the
invalidation triggers all belong. Underspire's migration to a
self-contained cache inside `roleplay_mixin.get_display_name` is the
worked example.

Games with no `get_display_name` override or a cheap one need no
migration. The `MSG_DISPLAY_NAME_CACHE_*` settings can be deleted from
local `settings.py` if present; otherwise Django will warn about them
as unknown settings (which is harmless).

### Tests

`evennia.utils.tests.test_funcparser` (84 tests) and
`evennia.objects.tests.test_objects` (142 tests) pass. The deleted
`test_display_name_cache` had 3 tests; they covered the deleted module
and have no replacement on the engine side.

### Rationale

This change reifies a principle now applied across the fork: the engine
hosts code that pays off agnostically; game-shaped optimizations belong
in the game. Hooks (the seams games plug into, like `get_display_name`)
stay in engine even when no engine code exercises them. Implementations
of behavior only some games want (the cache *around* the hook) move to
the game. See Phase 5 in
[`.fleet-review/engine-cleanup-checklist.md`](.fleet-review/engine-cleanup-checklist.md)
for the full reasoning.

---

## 6.0.0+underspire.35 — Redis attr cache write-behind ordering (Phase 2)

Closes a phantom-data window in the Redis L2 attribute cache. Previously,
[`RedisCachedModelAttributeBackend.do_update_attribute`](evennia/typeclasses/redis_attr_cache.py)
called `_cache_set` immediately after `super().do_update_attribute()`,
which only marks the attr dirty for later `bulk_update`. A crash between
that Redis publish and the next `flush_all_dirty` left Redis serving
values PostgreSQL never received, persisting until TTL expiry (~1h).

After this release the invariant is uniform: **Redis is never more
current than PG**, regardless of write path.

### Engine

- [`evennia/typeclasses/redis_attr_cache.py`](evennia/typeclasses/redis_attr_cache.py):
  removed the `do_update_attribute` override entirely. Redis is now
  republished only from `flush_dirty`, which was already ordered
  correctly (calls `super().flush_dirty()` first; re-raises on
  `bulk_update` failure; only writes Redis for the exact attrs the
  parent confirmed flushed). Same-process readers still see new values
  immediately via the `AttributeHandler` in-process cache, which holds
  the mutated `attr` instance directly. Cross-process readers see stale
  Redis values until the next flush tick, matching the implicit PG
  durability window.

- [`evennia/typeclasses/redis_attr_cache.py`](evennia/typeclasses/redis_attr_cache.py):
  added `invalidate_attrs(attrs)`. The orphan-dirty path
  (`_flush_orphan_dirty` for direct `attr.value = X` writes) calls
  `bulk_update` without going through any backend, so its writes would
  otherwise leave Redis stale forever. `invalidate_attrs` groups the
  flushed attrs by `db_model`, resolves owner pks via the through-table
  per model, and drops the affected Redis keys in one round-trip. Best-
  effort: no-op when Redis is disabled or unavailable.

- [`evennia/typeclasses/attributes.py`](evennia/typeclasses/attributes.py):
  `_flush_orphan_dirty` now calls `invalidate_attrs(dirty)` after a
  successful `bulk_update`.

### Settings

- [`evennia/settings_default.py`](evennia/settings_default.py):
  `ATTRIBUTE_FLUSH_ON_MAINTENANCE` defaults from `False` to `True`.
  With the publish-on-flush invariant in place, this caps cross-process
  Redis staleness at the maintenance tick (~60s) rather than the
  opportunistic `flush_if_pending` cadence (which depended on reads
  triggering a flush). Existing deployments that disabled this for
  perf reasons should set it explicitly in `settings.py`.

### Tests

- [`evennia/typeclasses/tests/test_attribute_fork.py`](evennia/typeclasses/tests/test_attribute_fork.py):
  three new regressions:
  - `test_update_does_not_publish_redis_before_flush` — handler-driven
    update marks dirty without touching Redis; `flush_dirty` publishes.
  - `test_flush_failure_does_not_publish_redis` — if `bulk_update`
    raises, Redis is left untouched and entries stay queued for retry.
  - `test_orphan_flush_invalidates_redis` — direct `attr.value = X`
    triggers Redis drop via `invalidate_attrs` after the orphan flush.

  `evennia.typeclasses` suite goes 55/55 green.

### Migration

No required changes for downstream games. Two behavior shifts to be
aware of:

1. Cross-process readers may briefly see a previous value for an
   attribute that was just written in another process, up to the next
   `flush_all_dirty` tick. Same-process readers are unaffected (they
   see the mutated in-memory attr immediately).
2. With `ATTRIBUTE_FLUSH_ON_MAINTENANCE` now defaulting `True`, the
   server maintenance tick will batch-flush dirty attrs every ~60s.
   Disable explicitly in `settings.py` if your deployment hand-tunes
   flush cadence elsewhere.

### Discovered (not fixed in this release)

`_flush_orphan_dirty` removes attrs from `_ORPHAN_DIRTY_ATTRS` before
calling `bulk_update`. If `bulk_update` raises, the dirty entries are
lost rather than retried — same failure shape that backend `flush_dirty`
was already hardened against. Captured as Phase 2b in
[`.fleet-review/engine-cleanup-checklist.md`](.fleet-review/engine-cleanup-checklist.md).

---

## 6.0.0+underspire.34 — Module-cached settings sweep (Phase 1)

Engine-wide cleanup of the "module-level `_X = settings.Y` snapshot
at import time" anti-pattern. These snapshots silently broke
`@override_settings` decorators (a class of tests was no-op'ing
without anyone noticing) and runtime reloads of `settings.py`.

After this release, behavioral settings are read via `settings.X`
directly at the call site across the engine. Django caches
`settings` attribute access internally, so the per-call cost is
sub-microsecond and the readability gain (no hidden snapshot layer)
is substantial. No `LazySetting` machinery was added; only revisit
if a real perf regression appears.

Touched-area test suites (`evennia.utils`, `evennia.server.tests`,
`evennia.web`, `evennia.accounts`, `evennia.commands`,
`evennia.typeclasses`, `evennia.events`, `evennia.help`) go
1154/1154 green.

### Engine — modules converted

- [`evennia/server/sessionhandler.py`](evennia/server/sessionhandler.py):
  six snapshots inlined — `FUNCPARSER_PARSE_OUTGOING_MESSAGES_ENABLED`,
  `BROADCAST_SERVER_RESTART_MESSAGES`, `SERVERNAME`, `MULTISESSION_MODE`,
  `IDLE_TIMEOUT`, `DELAY_CMD_LOGINSTART`.

- [`evennia/accounts/accounts.py`](evennia/accounts/accounts.py):
  six snapshots — `MULTISESSION_MODE` (3 sites),
  `AUTO_CREATE_CHARACTER_WITH_ACCOUNT`, `AUTO_PUPPET_ON_LOGIN`,
  `MAX_NR_SIMULTANEOUS_PUPPETS`, `MAX_NR_CHARACTERS`, `CMDSET_ACCOUNT`.

- [`evennia/accounts/bots.py`](evennia/accounts/bots.py):
  five bot-enabled flags (`IRC_ENABLED`, `RSS_ENABLED`,
  `GRAPEVINE_ENABLED`, `DISCORD_ENABLED` + token check) plus
  removed an unused `_IDLE_TIMEOUT` snapshot.

- [`evennia/commands/cmdhandler.py`](evennia/commands/cmdhandler.py)
  + [`evennia/commands/cmdsethandler.py`](evennia/commands/cmdsethandler.py):
  `IN_GAME_ERRORS` (multiple sites), `CMDSET_MERGE_CACHE_MAXSIZE`,
  `CMDSET_FALLBACKS`, `CMDSET_PATHS`.

- [`evennia/server/inputfuncs.py`](evennia/server/inputfuncs.py):
  `IDLE_COMMAND` (tuple-shaping logic moved to a `_idle_commands()`
  helper) and the `MXP_ENABLED and MXP_OUTGOING_ONLY` compound check.

- [`evennia/server/portal/portalsessionhandler.py`](evennia/server/portal/portalsessionhandler.py):
  `COMMAND_RATE_WARNING`, `MAX_CHAR_LIMIT_WARNING`.

- [`evennia/utils/funcparser.py`](evennia/utils/funcparser.py):
  `CLIENT_DEFAULT_WIDTH` (6 sites), `FUNCPARSER_MAX_NESTING`,
  `FUNCPARSER_START_CHAR`, `FUNCPARSER_ESCAPE_CHAR`. Default args on
  `FuncParser.__init__` changed from setting-snapshot defaults to
  `None` with body resolution.

  Also fixed a latent bug in this module: the max-nesting depth check
  was reading the module constant directly, ignoring any custom
  `max_nesting` passed to `__init__`. Now stored as
  `self.max_nesting` and read at the depth check.

- [`evennia/utils/evtable.py`](evennia/utils/evtable.py): `wrap()`
  and `fill()` default-arg pattern changed to `width=None` with body
  resolution (default args are evaluated at function-definition
  time, so a default of `settings.CLIENT_DEFAULT_WIDTH` snapshots
  at import).

- [`evennia/utils/evmore.py`](evennia/utils/evmore.py),
  [`evennia/utils/evmenu.py`](evennia/utils/evmenu.py),
  [`evennia/utils/eveditor.py`](evennia/utils/eveditor.py),
  [`evennia/utils/utils.py`](evennia/utils/utils.py): inlined
  `CLIENT_DEFAULT_WIDTH`/`HEIGHT` and `SEARCH_MULTIMATCH_TEMPLATE`.
  `evennia/utils/ansi.py` lost an unused `_COLOR_NO_DEFAULT`
  snapshot.

- [`evennia/typeclasses/tags.py`](evennia/typeclasses/tags.py)
  + [`evennia/typeclasses/attributes.py`](evennia/typeclasses/attributes.py):
  `TYPECLASS_AGGRESSIVE_CACHE` (17+ sites across cache short-circuits).
  Tests that flip this setting via `@override_settings` now actually
  exercise both code paths.

- [`evennia/typeclasses/models.py`](evennia/typeclasses/models.py):
  `PERMISSION_HIERARCHY` (originally a `[p.lower() for p in ...]`
  transform snapshotted at import) now rebuilds per call in
  `check_permstring`. 5-element list, sub-microsecond, irrelevant
  against the DB query already in the function.

- [`evennia/commands/default/*`](evennia/commands/default/):
  `MAX_NR_CHARACTERS`, `AUTO_PUPPET_ON_LOGIN`, `CLIENT_DEFAULT_WIDTH`
  (9 sites across `comms.py`/`building.py`),
  `BROADCAST_SERVER_RESTART_MESSAGES`.

- [`evennia/objects/object.py`](evennia/objects/object.py):
  `MULTISESSION_MODE` + the derived `_SESSID_MAX` constant. The
  latter is now a `_sessid_max()` helper.

- [`evennia/server/webserver.py`](evennia/server/webserver.py)
  + [`evennia/server/portal/webclient.py`](evennia/server/portal/webclient.py)
  + [`evennia/server/portal/webclient_ajax.py`](evennia/server/portal/webclient_ajax.py):
  `UPSTREAM_IPS`, `DEBUG`, `SERVERNAME`.

- [`evennia/help/filehelp.py`](evennia/help/filehelp.py):
  `DEFAULT_HELP_CATEGORY`.

- Contrib: `character_creator` (`MAX_NR_CHARACTERS`), `menu_login`
  (`CONNECTION_SCREEN_MODULE`, `GUEST_ENABLED`), `building_menu`
  (removed unused snapshot), `ingame_map_display` (`BASIC_MAP_SIZE`,
  `MAX_MAP_SIZE` via helpers; default-arg pattern in `Map.__init__`
  reworked).

### Tests — patch sites updated

Four existing tests had to monkey-patch the module-level snapshots to
exercise overrides. They now use `self.settings(...)` /
`@override_settings` as intended:

- [`evennia/commands/default/tests.py`](evennia/commands/default/tests.py)
  `test_ooc_look`: three nested `patch` blocks collapsed to one
  `self.settings(...)`.
- [`evennia/accounts/tests.py`](evennia/accounts/tests.py)
  `test_puppet_success`: `patch` → `self.settings`.
- [`evennia/typeclasses/tests/test_typeclasses.py`](evennia/typeclasses/tests/test_typeclasses.py)
  `test_attrhandler_nocache`: dropped redundant module-constant
  `patch`, kept `@override_settings`.
- [`evennia/utils/tests/test_funcparser.py`](evennia/utils/tests/test_funcparser.py)
  max-nesting test: now mutates `self.parser.max_nesting` directly
  (matches the bug fix that moved this onto the instance).

### Guideline

The "read settings at the call site" pattern is documented in
[`.agents/docs/code-style.md`](.agents/docs/code-style.md) under
"Settings reads", with the rationale and the carve-out for true
boot constants (paths, crypto issuer, encodings — eight remaining
sites are deliberately left as import-time snapshots). No
automated guard: catching this in review is enough.

### Migration notes

- **Downstream code that imports any of the removed `_X` module
  constants** (e.g. `from evennia.accounts.accounts import _MULTISESSION_MODE`)
  must read `from django.conf import settings; settings.X` instead.
  Most consumers wouldn't import these since the underscore prefix
  signals "private to module", but worth checking.
- **`FuncParser` subclasses that overrode `_MAX_NESTING`** at module
  level no longer affect the depth check. Override
  `self.max_nesting` after `super().__init__()` instead, or pass
  `max_nesting=N` to `__init__`.
- **`_HELP_TEXT` width display in `eveditor.py`** is still
  formatted at import time (the f-string in the help text is
  cosmetic, not runtime-critical). Editor width math elsewhere
  in the module now reads live.

---

## 6.0.0+underspire.33 — Tier 1 security/correctness fixes from fleet-review audit

Targeted pass over the highest-signal findings from the fleet-review
engine audit. Five concrete fixes plus a defense-in-depth invariant
test. Touched-area test suites (`evennia.utils.tests.test_text2html`,
`evennia.server.tests`, `evennia.web`, `evennia.events`) go 143/143
green, including the previously stale `test__server_maintenance_reset`
left over from `.29`'s `_runtime_config_row` optimization.

### Security

- [`evennia/server/evennia_launcher.py`](evennia/server/evennia_launcher.py)
  `_check_database` move-existing-superuser confirmation prompt
  replaces `eval(input("Continue [Y]/N: "))` with `input(...).strip()`.
  The `eval` was a latent footgun (empty input would `SyntaxError`)
  with no purpose at that prompt; the loop logic still works because
  it operates on strings.

- [`evennia/server/portal/amp.py`](evennia/server/portal/amp.py)
  `AMPConnection.data_in` legacy pickle fallback is now gated on
  `AMP_SESSION_ACCEPT_LEGACY_PICKLE`. The docstring already documented
  this as the contract; previously the code accepted legacy pickle
  unconditionally regardless of setting. Aligns admin-channel
  behavior with the matching gate already in
  [`amp_serde.accept_legacy_session_pickle`](evennia/server/amp_serde.py).

- [`evennia/server/portal/amp.py`](evennia/server/portal/amp.py)
  `receive_functioncall` allowlist is now fail-closed. Empty
  `AMP_FUNCTIONCALL_MODULES` disables FunctionCall entirely (matching
  the [`settings_default.py`](evennia/settings_default.py) docstring's
  "an empty tuple disables FunctionCall entirely"); previously empty
  meant "allow any module", which inverted the safer default. Vanilla
  ships a populated tuple so this only affects deployments that
  explicitly emptied the setting — those were relying on a fail-open
  behavior bug.

- [`evennia/server/portal/webclient.py`](evennia/server/portal/webclient.py)
  `_send_text_legacy` adds a sharp warning comment on the
  `client_raw=True` opt-out so a future contributor doesn't
  accidentally route player-influenced content through it.
  No behavior change.

- [`evennia/utils/tests/test_text2html.py`](evennia/utils/tests/test_text2html.py)
  New `test_parse_html_escapes_user_html` pins the XSS invariant
  that the fleet-review audit had flagged. `parse_html` is the trust
  boundary for the webclient (default_out plugin renders its output
  via `.html()`/string concat), so raw `<`, `>`, `&` from upstream
  text must always come out as entities before any tag generation.
  This was already true via `re_string` → `sub_text` running first
  in [`text2html.TextToHTMLparser.parse`](evennia/utils/text2html.py);
  the test exists so a future refactor can't silently regress it.

### Correctness

- [`evennia/web/api/views.py`](evennia/web/api/views.py)
  `ObjectDBViewSet.set_attribute` no longer treats falsy `db_value`
  (`0`, `False`, `""`) as a delete request. Now keys deletion off
  `value is None`, so REST clients can store legitimate zero/empty
  values without them being silently removed.

- [`evennia/events/bus.py`](evennia/events/bus.py)
  `emit` stores `actor.dbref` (e.g. `"#42"`) instead of `str(actor)`
  (which was usually the account/object name). The docstring already
  advertised "stored as dbref string only"; this brings the code
  into line so actor refs survive renames and remain stable for
  audit joins.

### Tests

- [`evennia/server/tests/test_server.py`](evennia/server/tests/test_server.py)
  `test__server_maintenance_reset` was asserting the pre-`.29`
  contract (`conf("runtime", value)` called every tick). Since
  `.29`'s `_runtime_config_row` optimization, the first maintenance
  tick reads via `conf("runtime", default=0.0)` and subsequent ticks
  persist directly to the cached `ServerConfig` row. Test updated to
  match the new contract.

### Migration notes

- **REST clients**: any tooling that relies on PUT-ing
  `{"db_value": 0}` or `{"db_value": ""}` to delete an attribute via
  the REST API must now send no `db_value` field (or explicit `null`)
  to delete. The old falsy-delete behavior was a bug.

- **`AMP_FUNCTIONCALL_MODULES`**: if your `settings.py` explicitly
  sets this to an empty tuple, FunctionCall is now disabled instead
  of allowing any module. Restore the default by removing the
  override or by populating it with `("evennia.server.portal.amp_server",
  "evennia.server.amp_client")`.

- **`AMP_SESSION_ACCEPT_LEGACY_PICKLE`**: if you have two processes
  mid-rolling-restart with the legacy pickle wire format on the
  *admin* channel (separate from the session channel which was
  already gated), set this to `True` for the restart window. Vanilla
  installs and any fully-upgraded fork are not affected.

- **`GameEvent.actor_ref` format**: new event rows store dbrefs
  (`"#42"`) instead of names. Old rows keep their existing format.
  Anything that filters or joins on `actor_ref` will see mixed
  formats across the changeover boundary.

---

## 6.0.0+underspire.32 — Cmdset cache invalidation, stale typeclass paths, `id()` cache keys

Follow-up cleanup pass closing out the remaining fleet-review batches.
No API surface change — downstream consumers should be able to bump
straight from `.31` and run. No new migrations.

Full test suite goes from 3 failing on `.31` to 2 (the last two are
a wilderness contrib that's broken at import-time and one upstream
test stale from `.29`'s `_runtime_config_row` optimization).

### Engine — cmdset cache subsystem (A1, A2, A3, T2-7)

- [`evennia/objects/mixins/movement.py`](evennia/objects/mixins/movement.py)
  `MovementMixin.move_to`: after a successful move, bumps the
  `_cmdset_generation` counter on both the source and destination
  locations via `evennia.commands.location_cmdset_cache.bump_cmdset_generation`.
  The location-cmdset cache key is keyed by location generation, so this
  is what actually invalidates it when room contents change.
- [`evennia/objects/mixins/lifecycle.py`](evennia/objects/mixins/lifecycle.py)
  `LifecycleMixin.delete`: bumps the location's generation before
  nulling `self.location`. Object deletion now invalidates the
  containing room's cached cmdset set.

  These two together fix the long-standing "after the first cache fill,
  players entering a room see the original cmdset forever" bug —
  `bump_cmdset_generation` was previously only fired on cmdset *stack*
  mutations, not on the movements/lifecycle events that change what
  the cache aggregates over.

- [`evennia/commands/cmdhandler.py`](evennia/commands/cmdhandler.py)
  Local-cmdset cache restructure. Now caches the filtered object list
  (post-`access('call')` filter, the expensive per-caller work) instead
  of the final `cmdset_stack` list. On each dispatch we re-run
  `at_cmdset_get(caller=caller)` per object and re-read each object's
  `cmdset.cmdset_stack`. `at_cmdset_get` is documented as a per-request
  dynamic hook for game code that mutates the cmdset stack live; the
  old cache silenced those mutations from the 2nd dispatch onward.
- `cmdhandler.py` also shallow-copies the gathered csets before
  applying the room-gather `duplicates` rule (treat `None` as `True`).
  The previous in-place mutation on shared cset references raced
  concurrent dispatches that interleaved through `yield` points in
  the merge loop, occasionally leaking `duplicates=True` past the
  intended restore. The restore loop is gone — copies are per-call,
  nothing's shared with the next dispatch, originals stay `None`.
- [`evennia/commands/cmdsethandler.py`](evennia/commands/cmdsethandler.py)
  `_invalidate_cmd_access_caches` no longer swallows exceptions
  silently; a failure here would leak stale permission decisions
  (cached `cmd.access` results returning True for commands whose
  cmdset was just revoked).

### Engine — stale `evennia.objects.objects` typeclass paths

After the underspire object-module refactor, `DefaultObject` and
friends live at `evennia.objects.object.DefaultObject` (singular,
since each class moved into its own module). The old
`evennia.objects.objects` path remains only as a compatibility shim
that re-exports the new classes. `__class__.__module__` always returns
the new singular path.

- [`evennia/locks/lockfuncs.py`](evennia/locks/lockfuncs.py): three
  `utils.inherits_from` calls (in `perm`, `perm_above` via `perm`,
  and `_to_account`) passed the stale plural string, making the
  `DefaultObject` check unconditionally return False for puppeted
  characters. Puppet/quell/perm-above lockfuncs all then routed
  through the no-account branch and ignored the account's permissions
  — including the security-relevant "puppet escalation prevention"
  path. Updated to the new singular path.
- [`evennia/objects/tests/test_objects.py`](evennia/objects/tests/test_objects.py),
  [`evennia/prototypes/tests.py`](evennia/prototypes/tests.py),
  [`evennia/commands/default/tests.py`](evennia/commands/default/tests.py):
  fixtures and assertions all updated. The spawner's
  `prototype_from_object` normalises typeclass paths via
  `class_from_module(...).__module__`, so it emits the new singular
  form. The tests now match.

### Engine — settings-blind regex caches

- [`evennia/commands/cmdparser.py`](evennia/commands/cmdparser.py) and
  [`evennia/objects/manager.py`](evennia/objects/manager.py) both
  module-cached `re.compile(settings.SEARCH_MULTIMATCH_REGEX)` at
  import time. Tests and downstream code using `@override_settings`
  or runtime mutation could not affect the cached value. Both now go
  through `evennia.utils.multimatch._multimatch_regex()` which compiles
  freshly from current settings each call (already the pattern used
  elsewhere in the multimatch module). Compile cost is negligible.

### Engine — `id()` cache keys replaced with stable identifiers

CPython recycles `id()` after GC. A freed object plus a freshly-allocated
object at the same address would silently alias under any cache that
used `id()` as part of its key — returning the prior object's result.

- [`evennia/utils/display_name_cache.py`](evennia/utils/display_name_cache.py):
  the per-looker ndb cache keyed on `(id(obj), id(looker), ...)`.
  `looker` is already implicit in the per-looker ndb store, so removing
  `id(looker)` from the key is a tidiness fix. `id(obj)` swapped for
  `obj.pk` (falling back to `id(obj)` only for unsaved transient
  objects, which can't outlive their dispatch anyway).
- [`evennia/commands/cmd_access_cache.py`](evennia/commands/cmd_access_cache.py):
  `id(session)` in the lookup key swapped for `session.sessid`.
- [`evennia/locks/lockhandler.py`](evennia/locks/lockhandler.py): the
  per-caller lock check cache keyed on `(id(self.obj), ..., id(session))`
  for Command instances (which have no pk). Now keys on
  `(class.__module__, class.__name__, cmd.obj.pk, ..., session.sessid)`.
  Two Command instances of the same class bound to the same DB object
  share lock decisions (which is correct — the lockstring is class-level
  and `holds()`-style lockfuncs check the bound object). Commands without
  a bound `cmd.obj` or sessions without `sessid` now bypass the cache
  rather than aliasing on `id()`.

### Tests

- [`evennia/objects/tests/test_objects.py`](evennia/objects/tests/test_objects.py)
  `test_search_autopick`, `test_search_ordinal_last`,
  `test_search_location_scope`: updated to expect the documented
  `quiet=True` returns a list contract. The previous assertions
  documented an alternate "autopick fires in quiet mode" contract
  that conflicted with `test_search_by_tag_kwarg`'s also-pre-existing
  expectation and that would have silently broken downstream callers
  iterating quiet-mode results as a list. Autopick semantics are
  preserved via the non-quiet path.
- [`evennia/commands/tests.py`](evennia/commands/tests.py) removed
  the stale `test_num_differentiators`. It exercised a suffix-N
  regex form ("look me-3") that was removed when the counting
  feature was reworked. `test_num_differentiators_hyphenated_names`
  below covers the current prefix-N syntax.
- [`evennia/typeclasses/tests/test_typeclasses.py`](evennia/typeclasses/tests/test_typeclasses.py):
  one dotted-path string in `test_typeclass_search__inputs` still
  pointed at the old single-module location of the test fixture
  class (before `tests.py` was moved into the `tests/` package).
  Updated to the new path.

### Migration

None. No schema changes, no settings changes, no API surface change.
Downstream consumers should be able to bump from `.31` to `.32` and
run directly.

---

## 6.0.0+underspire.31 — Make `0020_remove_redundant_tag_index` tolerate phantom-applied history

[`evennia/typeclasses/migrations/0020_remove_redundant_tag_index.py`](evennia/typeclasses/migrations/0020_remove_redundant_tag_index.py)
raised on databases whose history was recorded against the earlier
no-op revision of `0017_use_index_instead_of_index_together_in_tags`
(its docstring at line 12 acknowledges the original `0017` "never ran
any ops on fresh databases"). On those DBs Django records `0017`
applied but the index `typeclasses_db_key_be0c81_idx` was never
created; the subsequent `0018` `RenameIndex` is also recorded but
silently renames nothing; then `0020`'s `RemoveIndex` blows up trying
to drop an index that isn't there.

Production PG was unaffected because either it was initialized after
`0017` was rewritten to be non-empty, or the rename failure was
resolved at the time. Downstream consumers with longer migration
histories hit the failure on upgrade to `.30`.

### Engine

- [`evennia/typeclasses/migrations/0020_remove_redundant_tag_index.py`](evennia/typeclasses/migrations/0020_remove_redundant_tag_index.py):
  swap the bare `RemoveIndex` op for a `SeparateDatabaseAndState` block.
  - `database_operations` runs `DROP INDEX IF EXISTS
    typeclasses_db_key_be0c81_idx` so the DDL succeeds on both
    DBs-that-have-the-index and DBs-that-never-did. `reverse_sql`
    matches with `CREATE INDEX IF NOT EXISTS`.
  - `state_operations` keeps the original `RemoveIndex` so Django's
    model state stays aligned regardless of which database path
    actually ran. Subsequent migrations and `makemigrations` won't
    see a phantom index.

### Migration

No behavior change for fresh databases or PG production. DBs that
hit the failure on `.30` upgrade can re-run `evennia migrate` after
upgrading to `.31` and the index drop will silently no-op.

---

## 6.0.0+underspire.30 — Fleet review pass: attribute typed-column stabilization, shutdown/Discord/cmdset fixes

A multi-batch correctness pass driven by a fleet-review of the
underspire fork against the engine surface. The headline fix unblocks
in-place mutation of container attributes on the typed-column path —
the regression that silently broke `self.db.dct[k] = v` everywhere
the JSON branch was taken (149 xyzgrid tests failing among others).
Plus a stack of smaller correctness fixes across the shutdown, Discord,
sessionhandler, scene-resolution, attribute query/cache, job queue,
channel cache, event bus, and script-pause paths.

Net effect: full test suite goes from **262 failing → 20 failing**.
The remaining 20 are pre-existing issues in unrelated areas (search
edge cases, prototypes, lock-system corners) that already failed on
`.29`; none are regressions from this release.

### Engine — attribute typed-column refactor

- [`evennia/typeclasses/attributes.py`](evennia/typeclasses/attributes.py)
  value getter, JSON branch: deserialized container values are now wrapped
  in `_Saver*` proxies via `from_pickle(raw, db_obj=self)`. Without this,
  every `attr.value` read returned a fresh `json.loads()` dict/list and
  in-place mutations (`self.db.map_data[zcoord] = mapdata`, etc.) were
  silently lost. Pre-refactor pickle storage masked this because `Saver*`
  wrappers wrote through the value setter; the JSON path missed it.
- `_classify_value` / `_is_json_safe`: tuples (and any container holding
  a tuple anywhere in its tree) now route to pickle instead of JSON to
  preserve type. Tuples have no JSON type and were silently demoted to
  lists on read.
- `_classify_value`: ints outside the signed 64-bit range now route to
  pickle. `db_int_val` is a `BigIntegerField`; oversize values raised
  `DataError`/`OverflowError` at INSERT before.
- `_classify_value`, value getter JSON branch: `json.loads(None)` or
  malformed JSON now logs and returns `None`, matching the pickle
  branch's existing corruption-recovery behavior.
- `value_query_filter`: container queries now route to `db_str_val`
  with `db_val_type='json'` (was returning zero rows by querying the
  empty `db_value`). Every primitive branch now pins `db_val_type` so
  a string search can't collide with a JSON row whose serialized text
  matches. Oversize-int branch routes to the pickle column.
- [`evennia/objects/manager.py`](evennia/objects/manager.py)
  `get_objs_with_attr_value`: the str branch is now exact-match instead
  of `__iexact`, aligning with int/float/bool/none branches and
  `value_query_filter`. **Behavior change**: callers that relied on
  case-insensitive string attribute search will now miss. If you need
  case-insensitive, filter on `db_attributes__db_str_val__iexact`
  explicitly.
- [`evennia/typeclasses/attributes.py`](evennia/typeclasses/attributes.py)
  `ModelAttributeBackend.flush_dirty`: clears `_dirty_attrs` *after*
  `bulk_update` succeeds (via `difference_update`, so concurrent dirty
  marks survive). On failure, dirty entries stay queued and the backend
  stays in `_DIRTY_BACKENDS` so the next maintenance tick retries.
  Returns the flushed list so the Redis subclass can publish exactly
  what was written.
- [`evennia/typeclasses/redis_attr_cache.py`](evennia/typeclasses/redis_attr_cache.py)
  `_cache_set` gains `nx_only=True`. `query_key` uses it after PG read
  so a concurrent writer's newer Redis value isn't overwritten with the
  stale PG snapshot (TOCTOU close). `flush_dirty` now consumes the
  flushed list returned from super so it never re-publishes
  unflushed attrs.
- New migration
  [`0022_attribute_typed_value_indexes`](evennia/typeclasses/migrations/0022_attribute_typed_value_indexes.py):
  composite indexes on `(db_val_type, db_int_val)` and `(db_val_type,
  db_float_val)`. Every value-equality query pins `db_val_type` so the
  discriminator-first composite lets the planner do an index range scan
  instead of a sequential Attribute-table scan. `db_str_val` deliberately
  unindexed for now (PG btree page-overflow risk on long values; revisit
  with a partial/hash index if a real callsite warrants it).

### Engine — server / sessionhandler / Discord

- [`evennia/server/service.py`](evennia/server/service.py)
  `shutdown()` now awaits hook Deferreds via a new `_await_hooks` helper
  (was discarding them via list-comprehension-for-side-effects). User
  hooks returning Deferreds — `at_server_reload`, `at_server_shutdown`,
  `unpuppet_all` — are now actually awaited; the reactor no longer stops
  with writes in flight. Per-instance hook errors are caught and logged
  so one bad hook doesn't abort the rest.
- `shutdown()` re-entry guard for concurrent SRELOAD/SRESET/SSHUTD.
  Coexists with the SIGINT handler's pre-set flag by skipping the guard
  when `_reactor_stopping=True`.
- `server_maintenance` attribute-flush failures: now `log_err` with a
  consecutive-failure counter, escalating to a `CRITICAL` log after 3
  consecutive failures. Was silently `log_trace`-only; the only safety
  net for the write-behind cache could fail invisibly.
- [`evennia/server/portal/discord.py`](evennia/server/portal/discord.py)
  `resume()`: was referencing an undefined `self.sequence_id` (the
  attribute is `self.last_sequence`) and lacked a `return` after the
  `identify()` fallback. Calls raised `AttributeError`, killing the
  websocket. Fixed both.
- `get_gateway_url`: added an errback, and both the non-200 branch and
  the new error path now reset `is_connecting=False` so the
  `ReconnectingClientFactory` can actually retry. A failed gateway HTTP
  fetch previously left the bot wedged forever.
- [`evennia/server/sessionhandler.py`](evennia/server/sessionhandler.py)
  `_flush_outbuf`: was merging every kwarg with last-wins for non-text
  keys, silently dropping concurrent OOB/GMCP/prompt payloads on
  overlapping `data_out` calls in the same reactor tick. Pure-text
  messages still coalesce; any message carrying non-text kwargs now
  ships as its own AMP frame, restoring pre-batcher one-frame-per-call
  semantics.

### Engine — scene_index removal

The Redis-backed room membership cache for `msg_contents` recipient
resolution was deleted. Its mutation API (`on_move`, `add_to_room`,
`remove_from_room`) was never wired into `MovementMixin.move_to`,
`LifecycleMixin.delete`, `Character.at_pre_puppet/at_post_unpuppet`,
or `ObjectDB.at_db_location_postsave`. Once a room's Redis set was
populated lazily, it never refreshed — characters' movements,
deletions, and unpuppets all left stale membership, so `msg_contents`
served wrong recipient lists indefinitely. Additionally,
`resolve_recipients` filtered by `ROOM_SCENE_INDEX_TYPECLASS_PATHS`
which silently dropped any non-Character recipient (scripted props,
broadcast relays, items overriding `at_msg_receive`).

- Removed: `evennia/objects/scene_index.py`,
  `evennia/objects/tests/test_scene_index.py`.
- [`evennia/objects/mixins/messaging.py`](evennia/objects/mixins/messaging.py)
  `get_message_recipients` reverted to direct contents walk.
- [`evennia/settings_default.py`](evennia/settings_default.py):
  `ROOM_SCENE_INDEX_ENABLED`, `ROOM_SCENE_INDEX_REDIS_ALIAS`, and
  `ROOM_SCENE_INDEX_TYPECLASS_PATHS` removed.

### Engine — misc correctness

- [`evennia/jobs/queue.py`](evennia/jobs/queue.py) `_dequeue_db`: wraps
  the claim in `transaction.atomic()` with `SELECT FOR UPDATE SKIP
  LOCKED`. Two concurrent workers no longer claim the same pending row
  and run the job twice. Falls back to a plain SELECT on SQLite.
- [`evennia/comms/channel_subscriber_cache.py`](evennia/comms/channel_subscriber_cache.py)
  `sync_channel_subscribers`: pipeline now uses `transaction=True` so
  the DELETE+SADD pair is atomic. A concurrent `add_subscriber` /
  `remove_subscriber` is no longer silently overwritten.
- [`evennia/comms/models.py`](evennia/comms/models.py)
  `SubscriptionHandler.add`: the `add_subscriber` cache call sat outside
  the loop, referencing the leaked loop variable. Only the last
  subscriber was registered when multiple were added at once. Fixed.
- [`evennia/events/bus.py`](evennia/events/bus.py): per-call
  `persist=False` now wins over the backend default. The previous
  `backend in (...) or _should_persist(...)` ordering made the opt-out
  unreachable when `EVENT_BUS_BACKEND` forced postgres/both.
- [`evennia/scripts/scripts.py`](evennia/scripts/scripts.py)
  `_pause_task`: added a `self.pk is None` guard before
  `save(update_fields=...)`. Test fixtures and pre-start lifecycle
  paths that called `pause()` on unsaved Scripts no longer raise
  `ValueError: Cannot force an update in save() with no primary key`.
- [`evennia/scripts/taskhandler.py`](evennia/scripts/taskhandler.py)
  `TaskHandler.add`: scans for the lowest free ID (pre-refactor
  behavior) instead of monotonically growing forever. Freed IDs are
  reused, restoring downstream expectations.
- [`evennia/scripts/ondemandhandler.py`](evennia/scripts/ondemandhandler.py)
  `save()`: validates each task entry individually so a single
  unpicklable/recursive task only purges itself instead of bailing the
  whole save and losing all good timer state.

### Migration

- **Apply `evennia migrate`** — three new migrations:
  - `typeclasses/0022_attribute_typed_value_indexes` (additive
    `AddIndex`; online on PG 11+).
  - `scripts/0019_backfill_paused_state_from_attributes` (RunPython
    data migration; copies the old Attribute-based pause state into
    the columns introduced by `0018` and removes the orphan
    Attribute rows).
- **Settings cleanup (optional)**: remove `ROOM_SCENE_INDEX_*` from
  `server/conf/settings.py` if set. Harmless if left — Python ignores
  unknown settings — but they're dead config now.
- **Behavior change to verify**:
  `get_objs_with_attr_value(name, "<string>")` is now exact-match. If
  you have search code relying on the previous case-insensitive
  behavior, replace with an explicit
  `Q(db_attributes__db_str_val__iexact=value)` filter.
- **Transparent improvements (no code change required)**: in-place
  container mutations (`obj.db.dct[k] = v`) now persist, tuples come
  back as tuples, large ints don't crash, container-value queries
  actually match, hook-returning-Deferred users get awaited, OOB/GMCP
  payloads in the same tick no longer overwrite each other.

### Tests

- New `_classify_value` coverage in
  [`tests/test_attribute_fork.py`](evennia/typeclasses/tests/test_attribute_fork.py)
  for tuple-in-container demotion, int overflow, JSON-path classification.
- Fixed stale `attr:v1:*` SCAN pattern assertion (production is
  `attr:v2:*`).
- Moved [`evennia/typeclasses/tests.py`](evennia/typeclasses/tests/test_typeclasses.py)
  into the `tests/` package. The file was being silently shadowed by
  the `tests/` package and never ran; Python 3.14 strict discovery
  also refused to walk past the duplicate name. The 489 lines of legacy
  typeclass tests are now running again.

---

## 6.0.0+underspire.29 — Admin AMP sessiondata serde fixes

Backfilled: bug fixes on `evennia/server/amp_serde.py` for the admin
sessiondata map. See `0aa7b3f92`, `36cff5abc`, `7db8c1536`, `c2699aa81`.

---

## 6.0.0+underspire.28 — Replace pickle RCE vectors with JSON serde

Backfilled: security work replacing pickle deserialization on AMP
channels with a JSON-only serde. See `9df79df85`, `820b9c439`.

---

## 6.0.0+underspire.27 — Fix `CmdSetHandler.clear()` storage corruption

Backfilled: cmdset storage preserved list type on clear. See `2c320f00e`,
`226d0450f`.

---

## 6.0.0+underspire.26 — CSP fix, language handler guard, engine migration

Backfilled. See `6bf7402b6`, `606e8cf38`.

---

## 6.0.0+underspire.25 — Fix `TypeError` on shutdown: `clear_all_sessids()` is sync

Backfilled. See `f071b3bc5`, `429e0fd91`, `7f248e7fe`.

---

## 6.0.0+underspire.24 — Fix `evennia` launcher infinite recursion in non-TTY shells

[`evennia/server/evennia_launcher.py:1549-1551`](evennia/server/evennia_launcher.py)
`check_database`'s "no Account#1 found" branch unconditionally
recursed into itself after calling `create_superuser()`. In
interactive shells `createsuperuser` blocks for input and the recursion
exits naturally once an account exists. In **non-interactive shells**
(CI runners, scripted tooling, `evennia makemigrations` piped through
anything), Django's `createsuperuser` skips silently with a
"Superuser creation skipped due to not running in a TTY" notice. The
account remains absent. The recursion has no termination condition
and burns CPU until something kills it.

This bug isn't new — long-standing in the launcher — but it became
visible during the `.22`/`.23` migration-drift investigation when
`evennia makemigrations` was attempted from agent shells. Caught
during follow-up review.

### Engine

- [`evennia/server/evennia_launcher.py`](evennia/server/evennia_launcher.py):
  replaced the recursion with a direct `AccountDB.objects.filter(id=1).exists()`
  check after `create_superuser()`. If the account still doesn't exist
  (the create silently no-op'd), print the same `ERROR_DATABASE`
  template the other failure paths use, with a body explaining the
  non-interactive-shell case and pointing at the
  `EVENNIA_SUPERUSER_USERNAME/EMAIL/PASSWORD` env-var alternative.
  `always_return=True` callers still get a `False` return instead of
  exiting, matching the existing contract.

### Migration

None. Pure bug fix; behaviour in interactive shells is unchanged
(Django's interactive `createsuperuser` still blocks, account gets
created, function returns).

---

## 6.0.0+underspire.23 — Pin Django exactly to stop hash-drift migration churn

Downstream review of `.22` surfaced unrecorded model changes in two
engine apps (`server`, `typeclasses`). Investigation showed two
distinct drift causes:

1. **Django version drift on auto-generated index names.** Django's
   `_create_index_name` derives a 6-char hash from `(table, fields)`
   that has not been stable across Django minor versions. Migrations
   generated on Django 5.x baked in one hash; running on 6.0.4 today
   computes a different one. The autodetector sees the mismatch and
   keeps wanting to rename indexes that already exist on disk under
   the old name — pure noise, but it surfaces as drift on every
   release.
2. **`AutoField` vs `BigAutoField` in old migrations.** The
   `server` app's migration `0004_gameevent_enginejob` was written
   before [`settings_default.py:381`](evennia/settings_default.py)
   set `DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"`, so the
   recorded `id` field types disagree with what the project default
   would generate today. Pure drift, no behavioural impact.

This release addresses cause (1) for the future and documents what
remains manual.

### Engine

- [`pyproject.toml`](pyproject.toml): tightened the Django pin from
  `django >= 6.0.2, < 6.1` to `django == 6.0.4` (the version that
  generated the most recent migrations, including the typeclasses
  `0018_rename_tag_…` index rename). With an exact pin, hash names
  are stable across upgrades and a future Django minor bump cannot
  silently re-introduce this class of drift. Bumping Django becomes
  a deliberate engine release with a regenerated set of migrations,
  not a transparent dependency update.
- [`uv.lock`](uv.lock): regenerated to lock to Django 6.0.4.

### Migration

Downstream consumers should re-resolve their lockfile after pulling
this release. Anyone who had been resolving to a 6.0.x newer than
6.0.4 will pin back to 6.0.4 — no schema impact, but the resolver
output will change. Add this to the consumer's CI to catch any
*future* drift at PR time rather than at deploy:

```yaml
- name: Check for migration drift
  run: evennia makemigrations --check --dry-run
```

This is recommended in the consumer's CI, not the engine's, because
the engine ships against the default settings while consumers ship
against their own. The check only catches drift against the
configuration that will actually run in production.

### Known unfixed (deferred to a future release)

The pin closes the door on new drift but doesn't retroactively fix
the existing mismatches surfaced by `.22` review. Two engine
migrations still need to be written and shipped:

- **`server`**: `AlterField id` on `gameevent` and `enginejob`
  (`AutoField → BigAutoField`), plus `RenameIndex` on
  `gameevent.subject_created_at` from `_0e8f0d_idx` to
  `_40e896_idx`. Safe to land via straight `makemigrations` output.
- **`typeclasses`**: an index reconciliation on `tag`. The
  autodetector proposes `RemoveIndex` of
  `typeclasses_tag_db_key_db_category_db_tagtype_db_model_idx`, but
  blindly accepting would drop the index on deployed DBs. Correct
  fix requires confirming the actual on-disk index name on
  production (`SELECT indexname FROM pg_indexes WHERE tablename =
  'typeclasses_tag'`) and writing a `SeparateDatabaseAndState`
  migration that aligns recorded state without touching schema.

Both are tracked for a follow-up release. The downstream `world`
app likely needs its own `makemigrations` pass (separate concern,
consumer-side fix).

---

## 6.0.0+underspire.22 — Help prefix rip-out, test-suite regression fix

The Phase-3 `@`-prefix convention says `@kudos` and `kudos` are distinct
commands and the parser does not strip the prefix
([code-style.md Command Naming](.agents/docs/code-style.md)). The help
command's display layer was still papering over that convention by
hiding the `@` whenever no non-prefixed twin existed, leaving users
reading `kudos` in the index and getting "did you mean @kudos?" when
they typed it. The whole prefix-tolerance layer is removed.

### Engine

- [`evennia/commands/command.py`](evennia/commands/command.py): dropped
  `_HELP_PREFIX_CHARS` constant and the `stripped_key`/`stripped_aliases`
  block that built the `no_prefix` field in `search_index_entry`. The
  index entry no longer carries a parallel unprefixed variant.
- [`evennia/commands/default/help.py`](evennia/commands/default/help.py):
  dropped the duplicate `_HELP_PREFIX_CHARS`, the `no_prefix` field on
  `HelpCategory.search_index_entry`, the `strip_prefix` local function
  in `do_search`, the `no_prefix` lunr search field (boost 6), the two
  rerank loops that treated prefixed/unprefixed matches as equivalent,
  the `strip_cmd_prefix` method, and its five call sites (index listing,
  text-search suggestions, topic header, alias list, suggestion list).
  Help now displays the registered key verbatim — `@kudos` shows as
  `@kudos`. Want `help kudos` to also resolve? Add `kudos` as an alias
  on the command; aliases are still indexed.
- [`evennia/help/filehelp.py`](evennia/help/filehelp.py),
  [`evennia/help/models.py`](evennia/help/models.py): dropped the empty
  `"no_prefix": ""` placeholders in `search_index_entry` for schema
  consistency now that the lunr field is gone.
- [`evennia/objects/character.py`](evennia/objects/character.py):
  re-applied the `+underspire.21` `_last_puppet` fix here, since the
  recent objects-module split moved `DefaultCharacter` to its own
  file and the new override did not carry the fix from the old
  monolithic `objects.py:3371`. Without it `@ic` between characters
  would silently regress on `.22+`.

### Behaviour delta

- Exact `help <unprefixed-key>` no longer resolves to a prefixed
  command via the `no_prefix` indexed alias. The fuzzy suggester
  (`COMMAND_FUZZY_SUGGESTIONS_MAX_DIST = 2`) still surfaces `@kudos`
  when you type `kudos` (edit distance 1) as a "did you mean…"
  suggestion, so users are not stranded — they just stop being
  silently auto-routed.
- Considered bumping `COMMAND_FUZZY_SUGGESTIONS_MAX_DIST` to 3 to
  "make room for prefixes"; rejected. Distance-3 on short tokens
  (`look`, `get`, `say`) produces explosive false positives that kill
  the suggestion UX. The rip-out alone covers the prefix case.

### Tests

- Renamed [`evennia/objects/tests.py`](evennia/objects/tests.py) →
  `evennia/objects/tests/test_objects.py`. The `tests/` directory was
  introduced in `+underspire.21` with `__init__.py` so Django could
  discover `test_scene_index.py` and the new `_last_puppet` test, but
  Python's package-over-module precedence then silently shadowed the
  950-line `tests.py` — `evennia.objects.tests` resolved to the new
  empty package and the legacy suite went dark in CI. Migrating the
  file into the package restores discovery without churning callers
  (`evennia.objects.tests.SomeTest` still resolves). This is a `.21`
  regression that should have been caught in that release.

### Known pre-existing failures (not introduced here)

The full `evennia.objects` suite now reports 6 failures, all in the
multimatch tests that landed in `+underspire.20` (`f2511a5d4`):

- `DefaultObjectTest.test_search_autopick`
- `DefaultObjectTest.test_search_ordinal_last`
- `DefaultObjectTest.test_search_location_scope`
- `TestObjectManager.test_get_objs_with_key_and_typeclass`
- `TestObjectManager.test_get_objs_with_key_or_alias`
- `TestObjectManager.test_search_object`

These were dark in CI for the same `tests.py`-shadowing reason as
above, so they were never observed at PR time. The root cause is a
mismatch between the multimatch test expectations and
[`evennia/objects/object.py:729-731`](evennia/objects/object.py)
`at_search_result`'s `quiet=True` branch: tests expect autopick /
single-unwrap to fire under `quiet=True`, but the implementation
short-circuits to `return list(results)` first. Left for a follow-up
release because the fix is a deliberate semantic choice (whose contract
wins, `quiet=True` documented behaviour or `try_autopick` ergonomics).

### Migration

No downstream changes required. Games that relied on the help-display
prefix-strip to make admin commands appear "unprefixed" in the help
index should add explicit unprefixed aliases on those commands — that
was always the right shape under the Phase-3 convention.

---

## 6.0.0+underspire.21 — Engine bug fixes (swap_typeclass hooks, character _last_puppet)

Two genuine engine bugs caught during a five-item bug audit (three of
the five turned out to be false positives on re-read; see the release
commit body for the dispositions).

### Engine

- [`evennia/typeclasses/models.py:684`](evennia/typeclasses/models.py):
  `swap_typeclass(..., run_start_hooks="hook_a hook_b")` raised
  `AttributeError` when given multiple space-separated hook names.
  The loop variable was `start_hook` but the call used the original
  `run_start_hooks` string, so `getattr(self, "hook_a hook_b")` was
  attempted on every iteration. Single-name strings happened to work
  because `split()` yields one token equal to the original. Fixed to
  `getattr(self, start_hook)()`. Effect: multi-hook callers (anything
  passing more than one name to `run_start_hooks`) now actually run
  each named hook once instead of erroring on the first iteration.
- [`evennia/objects/objects.py`](evennia/objects/objects.py)
  `DefaultCharacter.at_post_puppet` did not update
  `account.db._last_puppet`. `DefaultObject.at_post_puppet` sets it,
  but the character override didn't call `super()` and didn't set
  the attribute itself, so the value only got written at character
  creation ([`accounts.py:987`](evennia/accounts/accounts.py)),
  initial setup, and the admin path. Switching `@ic` between
  characters left `_last_puppet` stale, so reconnect / auto-puppet
  flows ([`accounts.py:1738`](evennia/accounts/accounts.py),
  [`accounts.py:2075`](evennia/accounts/accounts.py)) could re-attach
  the wrong character. Fixed by setting `_last_puppet` directly in
  the character override rather than chaining `super()` (the parent
  also emits a "You become …" message which would have duplicated
  the character's own message).

### Tests

- Added [`evennia/typeclasses/tests/test_swap_typeclass_hooks.py`](evennia/typeclasses/tests/test_swap_typeclass_hooks.py)
  — attaches two `Mock` methods, calls `swap_typeclass` with both
  names, asserts each fires exactly once. Without the fix the test
  raises `AttributeError` on the bad `getattr`.
- Added [`evennia/objects/tests/test_character_post_puppet.py`](evennia/objects/tests/test_character_post_puppet.py)
  — clears `account.db._last_puppet`, calls `char1.at_post_puppet()`,
  asserts the attribute points at the character. Without the fix it
  stays `None`.
- Added [`evennia/objects/tests/__init__.py`](evennia/objects/tests/__init__.py).
  The directory was missing the package marker, so Django's test
  loader silently skipped both the new test and the pre-existing
  [`test_scene_index.py`](evennia/objects/tests/test_scene_index.py)
  (which had been dark in CI since it was added). Both now discover
  and pass.

### Migration

Downstream should not need changes. The hook-loop fix only affects
callers passing multi-name `run_start_hooks` strings, which would
previously have hard-errored — no working code depended on the
broken behavior. The `_last_puppet` fix restores the documented
contract; any downstream override of `DefaultCharacter.at_post_puppet`
that needs to preserve the new behavior should either call `super()`
or set `self.account.db._last_puppet = self` itself.

---

## 6.0.0+underspire.20 — Multimatch UX (ordinals, location scope, auto-pick)

Natural-language object multimatch: display and input use `first` / `second` /
`last` / `other` (two matches only), plus `my` / `here` / `worn` location scopes
and conservative inventory/room auto-pick. Numeric `1-sword` prefix form remains
supported.

### Engine

- New [`evennia/utils/multimatch.py`](evennia/utils/multimatch.py) — shared
  parsing, labels, location hints, auto-pick.
- [`evennia/settings_default.py`](evennia/settings_default.py) — default
  `SEARCH_MULTIMATCH_REGEX` (prefix numeric), `SEARCH_MULTIMATCH_TEMPLATE`
  (`{label}`), `SEARCH_MULTIMATCH_INPUT`, `SEARCH_MULTIMATCH_AUTOPICK`,
  `SEARCH_MULTIMATCH_LOCATION_PREFIXES`, `SEARCH_MULTIMATCH_WORN_FILTER`.
- [`evennia/objects/manager.py`](evennia/objects/manager.py),
  [`evennia/objects/objects.py`](evennia/objects/objects.py),
  [`evennia/utils/utils.py`](evennia/utils/utils.py) `at_search_result`,
  [`evennia/commands/cmdparser.py`](evennia/commands/cmdparser.py) — wired to
  multimatch helpers.
- [`evennia/typeclasses/models.py`](evennia/typeclasses/models.py) —
  `get_extra_info` delegates to `location_hint`.

### Migration

- Multimatch **display** changes from `sword-1` to `first sword` (etc.). Input
  `1-sword` still works.
- Auto-pick is **on** by default (`SEARCH_MULTIMATCH_AUTOPICK = True`). Set
  `False` to always show the multimatch prompt.
- Pose/emote targeting in game code is unchanged (separate `emote.py` parser).

### Tests

- [`evennia/utils/tests/test_multimatch.py`](evennia/utils/tests/test_multimatch.py)
- Updates to [`evennia/utils/tests/test_utils.py`](evennia/utils/tests/test_utils.py),
  [`evennia/objects/tests.py`](evennia/objects/tests.py),
  [`evennia/commands/tests.py`](evennia/commands/tests.py).

---

## 6.0.0+underspire.19 — Deprecation breadcrumb cleanup (F2, F12)

Engine-side cleanup of pre-1.0 upstream cargo and a doc clarification.

### Engine

- [`evennia/server/deprecations.py`](evennia/server/deprecations.py)
  shrunk from 188 to 88 LOC. The launcher-time `check_errors` hook
  used to raise on ~17 setting names renamed pre-1.0 upstream
  (`CMDSET_DEFAULT`, `CMDSET_OOC`, `BASE_COMM_TYPECLASS`,
  `COMM_TYPECLASS_PATHS`, `CHARACTER_DEFAULT_HOME`,
  `OBJECT/SCRIPT/ACCOUNT/CHANNEL_TYPECLASS_PATHS`,
  `SEARCH_MULTIMATCH_SEPARATOR`, `INLINEFUNC_ENABLED`,
  `INLINEFUNC_STACK_MAXSIZE`, `INLINEFUNC_MODULES`,
  `PROTFUNC_MODULES`, `TIME_*_PER_*` (six total),
  `GAME_DIRECTORY_LISTING`, `AMP_ENABLED`, `CYCLE_LOGFILES`,
  `CHANNEL_COMMAND_CLASS`, `CHANNEL_HANDLER_CLASS`). Underspire
  never set any of these (grep clean across `evennia/` and
  `newmoo/`); the entire block was breadcrumbs for a migration
  that already happened.

  Surviving checks still defend against real foot-guns on the
  current schema and stay: `WEBSERVER_PORTS` tuple-shape,
  `CHANNEL_CONNECTINFO` type-shape, `template_overrides` and
  `static_overrides` directory-rename guards, and the
  `MULTISESSION_MODE` vs `MAX_NR_SIMULTANEOUS_PUPPETS` coherence
  check. `check_warnings` (dev-mode prod-readiness signals:
  `DEBUG`, `IN_GAME_ERRORS`, `ALLOWED_HOSTS=*`,
  `SERVER_HOSTNAME=localhost`, psycopg2 deprecation) is unchanged.
- [`evennia/prototypes/prototypes.py`](evennia/prototypes/prototypes.py)
  docstring fix: stale reference to `settings.PROTFUNC_MODULES`
  renamed to `settings.FUNCPARSER_PROTOTYPE_VALUE_MODULES` (the
  pre-1.0 rename whose deprecation breadcrumb was just removed).

### Tests

[`evennia/server/tests/test_misc.py`](evennia/server/tests/test_misc.py)
`TestDeprecations` rewritten. The old test enumerated the 17
pre-1.0 names that no longer exist as gates, and only worked
because each pre-1.0 check raised before the next one would have
AttributeErrored on the single-field MockSettings. Replaced with
four targeted cases against the surviving gates: WEBSERVER_PORTS
tuple-shape (fail + pass), CHANNEL_CONNECTINFO type, and
MULTISESSION coherence. MockSettings is now a defaults-class.

Two pre-existing server-test failures that surfaced along the F2
test path (reproduce on the prior underspire HEAD) were fixed in
the same release rather than carried forward as "known fail":

- [`evennia/server/portal/tests.py`](evennia/server/portal/tests.py)`::TestAMPServer::test_amp_in`
  asserted a hand-baked pickle byte literal for the
  `MsgPortal2Server` wire, but that command path uses the JSON
  session-serde envelope (`dumps_session` /
  `pack_session_message`), not pickle. Structurally wrong, not
  just version-fragile. Rewrote to assert the transport saw the
  right AMP frame and that `dumps_session` / `loads_session`
  round-trip the payload.
- [`evennia/server/tests/test_at_init_scheduler.py`](evennia/server/tests/test_at_init_scheduler.py)
  forced `AT_INIT_BATCH_SIZE=2` against 3 entities; the third
  entity's `at_init` was scheduled through `reactor.callLater`,
  which never fires under Django `TestCase` (same class as the
  `test_worker_pool` failure fixed in `+underspire.17`). Patched
  `reactor.callLater` with a synchronous stand-in.

### Docs

[`core-beliefs.md`](.agents/docs/core-beliefs.md): the lane-2 test
("a game that didn't want it can opt out cleanly via a setting")
was strict about settings as the opt-out shape. Practice already
accepts subclass-override as a clean opt-out for structural or
naming conventions that travel with a class (the Phase 3
`@`-prefix command convention being the canonical case). Wording
adjusted to two sentences acknowledging both shapes without
re-litigating the prefix design (F12).

### Migration

Downstream should not need changes. Underspire was not setting any
of the removed pre-1.0 names, and the launcher will no longer raise
on them if a downstream consumer happens to set one — it will
silently accept and ignore, which is the same as having never been
there. If anyone needs the old breadcrumb messages, they're
recoverable from git history.

### Hygiene backlog

F2 and F12 → Shipped. F11 (Phase 2 caller-derived-state sweep)
removed from Open as won't-fix: the backlog entry assumed
`_normalize_account_command_caller` applied to all Command subclasses
but the `+underspire.3` changelog explicitly states it's a no-op
outside AccountCommand subclasses, so the cited `caller.account`
reads in default object commands are correct as-is.

---

## 6.0.0+underspire.18 — Typed-exception sweep (F4)

Four bare `except:` clauses in the engine narrowed to typed exception
clauses. Hygiene-backlog finding F4. The wider 83-site
`except *: pass` audit remains deferred.

### Engine changes

- [`evennia/web/website/views/help.py`](evennia/web/website/views/help.py)
  (`HelpDetailView.get_context_data`): two bare-except blocks wrapping
  prev/next-topic lookup narrowed to `(AssertionError, IndexError)`.
  Drops a latent `NameError` swallow that would have masked an empty
  category set as a real bug.
- [`evennia/utils/utils.py`](evennia/utils/utils.py) (`str2int`): two
  consecutive bare-except blocks around `int(number)` and
  `int(number[:-2])` narrowed to `ValueError`. The backlog entry
  listed only line 2991, but there are actually two sites; both fixed.

### Migration

Downstream code should not need changes. The narrowing is invisible
under normal load. If something previously hidden behind the bare
except was raising an unexpected exception type, that exception will
now propagate — but in those cases the prior silent-swallow was the
bug, not a feature.

### Tests

`evennia.utils evennia.web evennia.help` — 714 pass, 2 pre-existing
skips. No regressions.

### Hygiene backlog

F4 moves from Open → Shipped. No new findings.

---

## 6.0.0+underspire.17 — Stale TODO sweep (F5)

Five past-due TODO markers in engine code, all scheduled for upstream
1.0 or 5.0 removal. Fork is on 6.0. Hygiene-backlog finding F5.

### Engine deletions

- **`Channel.*` deprecation stubs** ([`comms/comms.py`](evennia/comms/comms.py)):
  eight methods on `DefaultChannel` that raised `RuntimeError` pointing
  callers at their 1.0+ replacements — `message_transform`,
  `distribute_message`, `format_senders`, `pose_transform`,
  `format_external`, `format_message`, `pre_send_message`,
  `post_send_message`. All removed, along with the dead docstring
  references in the class docstring and the matching paste in
  [`game_template/typeclasses/channels.py`](evennia/game_template/typeclasses/channels.py).
  No engine callers; pure tombstones.

- **`at_pre_drop` missing-lock escape hatch**
  ([`objects/objects.py`](evennia/objects/objects.py)): the three-line
  guard `if not self.locks.get("drop"): return True` is removed. Every
  object that goes through `basetype_setup` gets `drop:holds()` by
  default (objects.py:2057). Downstream edge case: any object created
  without `basetype_setup` and without an explicit drop lock now fails
  `at_pre_drop` (consistent with the lock system's fail-closed belief).

- **`caller.ndb._menutree` deprecation alias**
  ([`utils/evmenu.py`](evennia/utils/evmenu.py)): the alias that
  shadowed `caller.ndb._evmenu` for backwards compat is removed at both
  the `__init__` write site and the `close_menu` cleanup. Internal
  `self._menutree` (the parsed menudata dict) and the persistent
  `_menutree_saved` attribute key are unrelated and stay.

  In-tree consumer migrations bundled in the same release:
  [`prototypes/menus.py`](evennia/prototypes/menus.py) (~25 sites; the
  default prototype OLC menu, lane-2 engine code),
  [`prototypes/tests.py`](evennia/prototypes/tests.py),
  [`commands/default/tests.py`](evennia/commands/default/tests.py),
  [`contrib/full_systems/evscaperoom/menu.py`](evennia/contrib/full_systems/evscaperoom/menu.py),
  [`contrib/utils/fieldfill/fieldfill.py`](evennia/contrib/utils/fieldfill/fieldfill.py)
  (only the `ndb` branch — a separate broken `caller.db._menutree`
  branch is left untouched as a pre-existing bug),
  [`contrib/utils/tree_select/tree_select.py`](evennia/contrib/utils/tree_select/tree_select.py),
  [`contrib/rpg/character_creator/tests.py`](evennia/contrib/rpg/character_creator/tests.py),
  [`utils/tests/test_evmenu.py`](evennia/utils/tests/test_evmenu.py),
  [`utils/tests/data/evmenu_example.py`](evennia/utils/tests/data/evmenu_example.py).

### Comment-only fix (no behavior change)

- **`building.py` exec gate comment** ([`commands/default/building.py`](evennia/commands/default/building.py)):
  the TODO at building.py:4275 claimed "Exec support is deprecated.
  Remove completely for 1.0" — but exec support is still live in
  [`prototypes/spawner.py`](evennia/prototypes/spawner.py) (`exec(code, ...)`
  on prototype `exec` strings at spawn time). The if-check the comment
  guards is the actual privilege barrier blocking non-Developer staff
  from arbitrary code execution. Comment rewritten to describe what the
  check does. Removing exec support entirely is tracked as a new
  backlog item (out of scope for F5).

### Migration (required)

Downstream code that still reads `caller.ndb._menutree` must rename to
`caller.ndb._evmenu`. Newmoo grepped clean before merge. The alias has
been deprecated since pre-1.0 upstream; this just lands the removal
that was already scheduled.

The eight `Channel.*` stub methods were never callable (they raised on
invocation). If downstream code overrode any of them, the override
won't be inherited from `DefaultChannel` any more, but since the base
implementations raised, any inheriting override was already shadowing
a dead method.

### Tests

All 91 tests in
`evennia.utils.tests.test_evmenu evennia.comms evennia.objects evennia.prototypes`
pass after the F5 changes.

Six failures that surfaced during the F5 sweep but were pre-existing
on the prior underspire HEAD were also fixed in this release (commits
land after the `release:` commit, in the same branch):

- **`test_display_name_cache` (3 tests)**: test bug. The mock was on
  `self.char1.get_display_name` (the looker) while
  `cached_get_display_name(self.char2, self.char1)` calls the
  *object's* method (`self.char2`), so the mock never registered.
  Patch target swapped to `self.char2`; args left intact so
  `invalidate_display_name_cache` and `bump_recog_generation` still
  hit the looker (`self.char1`).

- **`test_search` (2 tests)**: real engine bug surfaced by the recent
  typed-column optimization in
  [`typeclasses/attributes.py`](evennia/typeclasses/attributes.py).
  Primitive Attribute values now live in `db_int_val` /
  `db_float_val` / `db_str_val` with `db_value` NULL; the search
  managers still filtered on `db_attributes__db_value=value` and
  silently returned no matches for any primitive search. Added
  `value_query_filter(value, prefix)` in `attributes.py` mirroring
  the writer's dispatch (str → `db_str_val`, bool → `db_int_val` +
  `db_val_type="bool"` to disambiguate from int 0/1, etc.) and wired
  both `get_attribute` and `get_by_attribute` in
  [`typeclasses/managers.py`](evennia/typeclasses/managers.py) through
  it. Affects every `search_*_attribute` helper.

- **`test_worker_pool` (1 test)**: test/framework mismatch. The test
  drove the real `twisted.internet.threads.deferToThread` and waited
  on a `threading.Event`, but `deferToThread` schedules its callback
  via `reactor.callFromThread`; Django's `TestCase` doesn't run a
  reactor, so the callback queued and the event never set — a 5s
  hang on every run. Rewritten as four targeted unit tests that
  patch `deferToThread` and assert the wrapper's contract (returns
  Deferred, wraps args, chains callback/errback, raises on disabled
  pool, fires elapsed-time warn past threshold).

Migration impact: code that called any `search_*_attribute` with a
primitive `value=` argument was silently returning no results. After
this release it returns matches.

### Hygiene backlog

F5 moves from Open → Shipped. No new findings opened here; the
backlog gained F18/F19 in a parallel doc commit on this same branch.

---

## 6.0.0+underspire.16 — Contrib extraction: move six contribs into downstream

Six `evennia.contrib.*` packages moved to downstream Underspire as
ordinary game code. Each was either used as-is via a single
`TypeProperty` (cooldowns, traits), used by one consumer at
module-level (name_generator), or so deeply extended by the game that
the engine was hosting a system the consumer had already built on top
of (components, buffs). The `rpsystem` package goes the same way: only
`rplanguage` was imported; the rest of the system has long been ported
into game code (`world/rp_features.py`, `RecogHandler`, helmet/mask
display rules) so the contrib delete is total.

Core-belief rationale: "toolkit not game." Underspire is the sole
consumer of this fork; carrying these in the engine added no toolkit
value past what the game now hosts directly. Mirrors the
`+underspire.10` methodology (extended_room, puzzles) at larger scale.

### Engine deletions

| Contrib | LOC | Downstream home |
|---|---|---|
| `evennia.contrib.game_systems.cooldowns` | 419 | `world.cooldowns` |
| `evennia.contrib.utils.name_generator` | 32 033 (mostly data files) | `world.name_generator` |
| `evennia.contrib.rpg.traits` | 3 303 | `world.traits` |
| `evennia.contrib.base_systems.components` | 1 655 | `world.components` |
| `evennia.contrib.rpg.buffs` | 2 222 | `world.buffs_base` |
| `evennia.contrib.rpg.rpsystem` | 3 036 | only `rplanguage` → `world.rpg.rplanguage` |

Each contrib's tests travel with the code. Engine `evennia.contrib`
test count drops accordingly. No engine non-test code referenced any
of these modules; verified by grep before each commit.

### Migration (required)

Downstream import sweep:

| Old import | New import |
|---|---|
| `from evennia.contrib.game_systems.cooldowns import CooldownHandler` | `from world.cooldowns import CooldownHandler` |
| `from evennia.contrib.utils.name_generator import namegen` | `from world.name_generator import namegen` |
| `from evennia.contrib.rpg.traits import TraitHandler` | `from world.traits import TraitHandler` |
| `from evennia.contrib.base_systems.components import Component, ComponentHolderMixin, ComponentProperty, DBField` | `from world.components import ...` |
| `from evennia.contrib.rpg.buffs.buff import BaseBuff, BuffHandler, Mod` | `from world.buffs_base import ...` |
| `from evennia.contrib.rpg.rpsystem.rplanguage import add_language, obfuscate_language` | `from world.rpg.rplanguage import add_language, obfuscate_language` |

Newmoo partner branch: `engine-16-adopt-contribs`. Engine release ships
only after the partner PR is ready against the engine pin.

`evennia.contrib.grid.xyzgrid` and `evennia.contrib.grid.wilderness`
remain in the engine per prior decision; both are generic spatial
infrastructure and still match the toolkit shape.

### Hygiene backlog

This release also lands [`.agents/docs/hygiene-backlog.md`](.agents/docs/hygiene-backlog.md):
catalogues findings from the hygiene pass that weren't actioned in
this release (upstream-deprecations cargo, doc rot in
`docs/source/`, bare excepts, past-due TODO markers, settings-prefix
split, CMDSET_REFACTOR §8 verifications, cache-coherence audit, big-
module audit, and six deferred audits). Future hygiene passes start
from this file. Indexed in [`AGENTS.md`](AGENTS.md).

### Tests

- `evennia.commands`: 284/284 pass.
- `evennia.contrib`: 466 → fewer tests as the contrib surface
  shrinks; all remaining contrib tests pass.

---

## 6.0.0+underspire.15 — Cmdset refactor followups: switch case + docs

Three small followups from the downstream alignment pass. One latent
bug fix shipped as a behavior default flip, one doc-of-record update,
one downstream wrapper recorded as redundant for the next game-side
cleanup.

### Latent fix: `Command.parse` lowercases switches by default

`Command.parse` previously stored user-supplied switches on
`self.switches` with case preserved. Two problems:

1. **Latent validation bug.** `switch_options` is class-lowercased at
   parse time
   ([`command.py:565`](evennia/commands/command.py)), but user input
   was compared raw at line 579, so a command declaring
   `switch_options=("del",)` would silently reject `/Del` as an
   "unused" switch and warn the user — even though that's obviously
   what the user meant. Nobody noticed because the warning text just
   said "Extra switch /Del ignored" and the user assumed the command
   was case-sensitive.
2. **Per-consumer rework.** Real-world consumers compare against
   lowercase literals (`if "del" in self.switches`); case preservation
   meant every consumer had to re-implement the same `[s.lower() for
   s in self.switches]` step.

New class flag `parse_lowercase_switches = True` (default `True`).
When `True`, `parse` lowercases user input before storing on
`self.switches` *and* before `switch_options` validation, so both the
bug above and the per-consumer lowercasing go away.

Set `parse_lowercase_switches = False` on subclasses that genuinely
need case-sensitive switch handling.

**Migration:** tiny breaking change for any consumer that compared
`self.switches` against uppercase literals. The flag exists for them
to flip. Downstream Underspire kept its own one-line lowercase wrapper
during the deprecation window; it can drop the wrapper now that the
engine handles it.

Three new tests in `TestAccountCommandNormalization` cover the
default, the `switch_options` regression, and the opt-out path. 284/284
commands tests pass.

### Docs: `CMDSET_REFACTOR.md` Phase 5 callout

Adds an explicit "lesson learned" callout to the Phase 5 section of
[`CMDSET_REFACTOR.md`](CMDSET_REFACTOR.md): most of the enumerated
Phase 5 cleanup items got removed incrementally during downstream
Phase 0-4 alignment commits, not deferred to a final pass. The actual
Phase 5 PR ended up tiny. Future fork migrations should expect the
same shape — clean up as the API lands, not as a big final-cleanup
commit. The Phase 5 list now reads as a backstop catalog rather than
a planned final commit.

### Recorded: redundant downstream patch

Underspire's `commands/staff_admin_wrappers.py:97-103` patches
`CmdNick.func` to recognize `@nicks` as triggering list mode. The
engine already does this since `b78cf2e88` (Phase 3 step 4,
[+underspire.8](#600underspire8--phase-3-token-boundary-matching--drop-cmd_ignore_prefixes))
— the wrapper predates the engine fix and was never compared back.
No engine commit needed. Downstream cleanup: delete the wrapper, verify
`@nicks` still triggers list mode in stock behavior. Recorded here so
the next downstream cleanup pass knows to look for it.

---

## 6.0.0+underspire.14 — Phase 4 polish: integration tests + ergonomics

Small post-Phase-4 release driven by downstream alignment feedback from
the Underspire migration. Three changes, all additive.

### Pose passthrough integration tests (`evennia/commands/tests.py`)

New `TestPosePassthroughIntegration` exercises the full cmdhandler
dispatch path against a fixture `CmdNoMatch`. Asserts that:

- `.pose smiles`, `;nods`, and `,grins` (leading punctuation not
  aliased by any engine default) reach `CmdNoMatch.func()` with
  `self.raw_string` equal to the original input — no trie shortcut
  intercepts them, no abbrev rewrite mangles the string.
- When a custom `CMD_NOMATCH` is registered, the cmdhandler's built-in
  fuzzy fallback (`"Maybe you meant ...?"`) is short-circuited and
  `fuzzy_command_suggestions` is never invoked. This is the guarantee
  that lets downstream pose/emote handlers run without the engine
  preempting them with a typo suggestion.

`:` is intentionally not covered: it's an explicit alias of the engine's
default `CmdPose` (with `arg_regex = None`), so `:waves` legitimately
matches CmdPose rather than falling through. The dot/semicolon/comma
cases are the ones the trie+fuzzy work needed to keep clean.

Downstreams inherit this safety net rather than each writing their own
smoke test.

### `try_num_differentiators` re-exported from `cmdparser_trie`

Parser wrappers that subclass or compose on `cmdparser_trie` previously
had to reach across to `evennia.commands.cmdparser` for
`try_num_differentiators` (the `2-ball` multimatch separator parser).
That's now re-exported so:

```python
from evennia.commands.cmdparser_trie import (
    cmdparser,
    trie_build_matches,
    create_match,
    try_num_differentiators,
    fuzzy_command_suggestions,
)
```

works without crossing modules. `__all__` is declared on
`cmdparser_trie` to make the public surface explicit. The original
`evennia.commands.cmdparser.try_num_differentiators` import path
continues to work unchanged.

### Migration doc clarifications (`CMDSET_MIGRATION.md` § Phase 4)

Two clarifications added in response to downstream feedback:

- **"Wrapper still earns its keep" path.** The optional-cleanup section
  used to read as "delete the `COMMAND_PARSER` override." Now it
  explicitly calls out the case where the override does work other
  than parser selection (per-caller access caching, alias gating,
  telemetry, outer LRU) and instructs you to rebase that wrapper on
  `trie_build_matches` instead of deleting it.
- **Worked example of fuzzy suggestions inside a custom `CmdNoMatch`.**
  The engine's fuzzy fallback is *skipped* when a custom CMD_NOMATCH
  is registered (the common case for any non-trivial game), so a
  literal read of "fuzzy is the default" misleads downstreams. The
  migration doc now ships a copy-pasteable `CmdNoMatch.func()` body
  showing the `fuzzy_command_suggestions(raw, self.cmdset)` call,
  including the rule that the call must come *after* any
  leading-punctuation pose/emote interception so the fuzzy hint
  doesn't preempt intended emote input.

### Migration

None required. All changes are additive (new test class, additive
re-exports, doc clarifications).

---

## 6.0.0+underspire.13 — Phase 4: trie parser default + fuzzy suggestions + Phase 3 cleanup bundle

Promotes the trie-backed command parser into the engine as the default
`COMMAND_PARSER`, bundles Levenshtein-based fuzzy suggestions on the
no-match fallback, and finishes the Phase 3 leftovers (`include_prefixes`
kwarg removal, `CMD_IGNORE_PREFIXES` setting deletion).

### Trie parser (`evennia/commands/cmdparser_trie.py`, new module)

- Ported from Underspire's `world/parsing/trie_parser.py`. Public
  callable `cmdparser(raw_string, cmdset, caller, match_index=None,
  session=None, **kwargs)` matches the existing `COMMAND_PARSER`
  contract; `trie_build_matches(raw_string, cmdset)` mirrors
  `cmdparser.build_matches`'s output.
- Collects candidate commands via a token-prefix trie keyed on each
  command's key/aliases (multi-word keys like `go shard` live at the
  appropriate token depth and are also surfaced from the first token).
- Unambiguous first-token abbreviation expansion: when the typed first
  token is not a trie edge but uniquely prefixes one root key, the input
  is rewritten to the shortest matching canonical key so `create_match`
  slices `args` correctly. Gated on `COMMAND_PARSER_TRIE_ABBREV` (default
  `True`).
- Exact-match fast path skips `cmd.match()` for the sole candidate when
  the class did not override `match` and is not an exit. Honors
  `arg_regex`. Gated on `COMMAND_PARSER_TRIE_FASTPATH` (default `True`).
- Two-tier cache on the merged cmdset (`_trie_command_trie` +
  `_trie_command_trie_cheap` + `_trie_command_trie_sig`): the cheap key
  (length + id-sum of commands) short-circuits the O(n) structure
  signature on every parse. Trie rebuilds only when the cheap key
  drifts (cmdset membership change) or the full sig drifts (in-place
  key/alias mutation on an existing cmd). Production code rebuilds the
  containing cmdset on any cmd change, so the cheap key catches the
  common case.
- Falls back to the linear `cmdparser.build_matches` when the trie
  produces zero candidates, so commands whose `match()` overrides use
  non-prefix logic still get evaluated.

### Default flip + opt-out (`evennia/settings_default.py`)

- `COMMAND_PARSER` default flipped to
  `evennia.commands.cmdparser_trie.cmdparser`. The linear
  `evennia.commands.cmdparser.cmdparser` stays available for opt-out
  (set the setting back if a downstream needs the old behavior).
- New settings: `COMMAND_PARSER_TRIE_FASTPATH` (default `True`),
  `COMMAND_PARSER_TRIE_ABBREV` (default `True`),
  `COMMAND_FUZZY_SUGGESTIONS_ENABLED` (default `True`),
  `COMMAND_FUZZY_SUGGESTIONS_MAX_DIST` (default `2`),
  `COMMAND_FUZZY_SUGGESTIONS_LIMIT` (default `3`).

### Performance baseline

Synthetic 500-cmd cmdset, 2000 parses across 8 unique inputs:
- linear: ~72 µs/parse
- trie:   ~45 µs/parse  (**1.59× faster**)

Synthetic 20-cmd cmdset:
- linear: ~3.6 µs/parse
- trie:   ~6.8 µs/parse  (3 µs absolute regression on tiny cmdsets;
  well below user-perceptible thresholds, and gameplay cmdsets are
  typically 30–80 commands)

### Fuzzy command suggestions (`evennia/commands/cmdhandler.py`)

- The no-match fallback in the cmdhandler now offers Levenshtein-based
  suggestions ("Maybe you meant ...?") via
  `cmdparser_trie.fuzzy_command_suggestions`. Replaces the previous
  `difflib`-based `string_suggestions` call.
- Gated on `COMMAND_FUZZY_SUGGESTIONS_ENABLED` (default `True`).
- **Only fires when no custom `CMD_NOMATCH` command is registered.**
  Downstream code that registers a `CmdNoMatch` (e.g. emote/pose
  parsing on leading punctuation) is unaffected: the cmdhandler
  delegates the entire no-match branch to the override and never
  reaches the fallback text.

### Phase 3 cleanup bundle

- `Command.match()` and `cmdparser.build_matches()` lose the no-op
  `include_prefixes` kwarg (both signatures documented as ignored since
  `+underspire.8`; engine-wide audit found no surviving call sites
  passing it).
- `settings.CMD_IGNORE_PREFIXES` is **deleted** from
  `settings_default.py`. The startup warning in
  `evennia/__init__.py:_init()` is removed.
- Two remaining help-system consumers (help-search index in
  `commands/command.py` and help lookup in `commands/default/help.py`)
  inline `_HELP_PREFIX_CHARS = "@&/+"` as a module constant. Purely a
  help-search convenience (`help @open` and `help open` resolve to the
  same entry when only one exists); no longer user-configurable.

### Migration

- Downstreams setting `COMMAND_PARSER` to a custom parser keep working;
  the new default only applies when the setting is unset.
- Downstreams that referenced `settings.CMD_IGNORE_PREFIXES` get an
  `AttributeError` at import. The setting was a no-op since
  `+underspire.8`; remove the reference. If a custom help-search needs
  the prefix-strip behavior, inline the constant locally.
- `Command.match` and `cmdparser.build_matches` accept no `include_prefixes`
  kwarg. Downstream subclasses overriding `match(self, cmdname,
  include_prefixes=True)` should drop the kwarg.

See [`CMDSET_MIGRATION.md`](CMDSET_MIGRATION.md) Phase 4 for the full
downstream migration walkthrough.

---

## 6.0.0+underspire.12 — `redis_attr_cache` value serialisation fix

Single-commit release (`ec39b3028`). `PickledObjectField` always holds a
decoded Python object in memory, not raw bytes. Calling
`base64.b64encode()` on a dict/list raised `TypeError`. The cache now
serialises `db_value` via `dbsafe_encode` / `dbsafe_decode` so complex
attribute values round-trip through the Redis L2 cache correctly.
Cache schema bumped `v1` → `v2` to invalidate stale entries from before
the fix.

---

## 6.0.0+underspire.11 — Discord portal: interaction routing, remove_role, slash-command registration

Three additions to the Discord portal layer enabling full Discord interactions
(slash commands + typing indicators) and non-blocking role removal.

### Portal (`evennia/server/portal/discord.py`)

- **Fix: `INTERACTION_CREATE` type key no longer clobbered.**
  The `else` branch in `DiscordClient.data_in` previously called
  `keywords.update(data["d"])`, which silently overwrote
  `keywords["type"] = "INTERACTION_CREATE"` with the integer `2` from
  the Discord payload's own `type` field.  The inner dict is now copied,
  its `type` popped and stored as `keywords["interaction_type"]`, and
  only then merged into `keywords`.  The routing string in
  `keywords["type"]` is preserved for `DiscordBot.execute_cmd` dispatch.
  Same protection applies to any future Gateway event whose `d` payload
  contains a `type` integer (e.g. `TYPING_START` channel type).

- **New outputfunc `send_remove_role(role_id, guild_id, user_id)`.**
  Issues a non-blocking REST `DELETE /guilds/{guild_id}/members/{user_id}/roles/{role_id}`.
  Use via `session.msg(remove_role=(role_id, guild_id, user_id))`.

- **New outputfunc `send_interaction_reply(content, interaction_id, token)`.**
  Posts an interaction callback (type 4 = `CHANNEL_MESSAGE_WITH_SOURCE`)
  to `POST /interactions/{interaction_id}/{token}/callback`.
  Use via `session.msg(interaction_reply=(content, interaction_id, token))`.

- **New outputfunc `send_register_commands(commands, app_id, guild_id)`.**
  Bulk-overwrites guild slash commands via
  `PUT /applications/{app_id}/guilds/{guild_id}/commands`.
  Use via `session.msg(register_commands=(commands_list, app_id, guild_id))`.

### Bot base class (`evennia/accounts/bots.py`)

- **`DiscordBot.remove_role(role_id, guild_id, user_id)`** — thin wrapper
  calling `super().msg(remove_role=(...))`.
- **`DiscordBot.interaction_reply(content, interaction_id, token)`** —
  thin wrapper calling `super().msg(interaction_reply=(...))`.
- **`DiscordBot.register_guild_commands(commands, app_id, guild_id)`** —
  thin wrapper calling `super().msg(register_commands=(...))`.

### Migration

No breaking changes.  Downstream `DiscordBot` subclasses that previously
used `reactor.callInThread(requests.delete, ...)` for role removal can
switch to `self.remove_role(...)` (or `super().msg(remove_role=...)`
directly).  The old thread-based path will continue to work; this is an
additive improvement.

---

## 6.0.0+underspire.10 — Phase 2 cleanup + unused contrib removal

Two pre-existing Phase 2 bugs surfaced during the Phase 3 sweep and
the +underspire.9 follow-up; fixed in a single commit since both are
small and tightly scoped to the same migration. Bundled with deletion
of two unused contribs to cut test-sweep noise from code Underspire
doesn't run.

### Engine

- **`CmdPerm.func` case-insensitive duplicate check.**
  `obj.permissions.all()` returns lowercased strings, but the
  "already defined" check compared against the input verbatim. Typing
  `@perm Obj = Builder` against a target that already had `builder`
  fell through to re-add and reported `"given"` instead of
  `"already defined"`. After +underspire.9 this also caused a
  redundant `cmd_access_cache` flush and a spurious
  `permissions_changed` signal fire. Fix: build a lowercased set
  once and compare case-insensitively.

- **`test_resources.call()` hardcoded `providers["account"]`.** Tests
  that passed `caller=self.account2` (or a different character) had
  their account silently rewritten to `self.account` inside the
  `account_command_caller` normalisation branch, masking real
  behavior. Same for `cmdobj.account`. Pre-existing since
  +underspire.6 when `_normalize_account_command_caller` was added.
  Fix: derive `cmd_account` from caller (caller-if-Account →
  caller.account → self.account fallback) and use it for both
  `cmdobj.account` and `providers["account"]`.

### Test fallout

`evennia.contrib.game_systems.mail.tests.TestMail.test_mail` now
passes — it was failing on the second assertion (`caller=self.account2`
sending to `TestAccount2`) because the account hardcoding produced
`"from TestAccount"` instead of `"from TestAccount2"`.

`TestPermissionsChangedSignal.test_cmd_perm_no_op_does_not_fire`
restored (was dropped in +underspire.9 because the case-insensitivity
bug made it untestable).

All 263 evennia.commands + evennia.commands.default.tests +
evennia.contrib.game_systems.mail.tests pass.

### Contrib removals

`evennia/contrib/grid/extended_room/` and
`evennia/contrib/game_systems/puzzles/` deleted outright. Neither has
any engine consumer; both are opt-in features Underspire doesn't use,
and both had broken tests in the +underspire.9 sweep that were pure
noise for this fork. The typeclass-path remap entries in
`evennia/accounts/migrations/0011_*`,
`evennia/objects/migrations/0012_*`, and
`evennia/scripts/migrations/0015_*` are kept as-is — they reference
the modules by string for DB-side path normalisation and don't import
them, so anyone migrating an old DB still gets the remap.

### Migration

No downstream action required for the bug fixes. Behaviour changes:

- `@perm Obj = Builder` against an obj that already has the
  permission now correctly reports "already defined" and does not
  fire `permissions_changed` (downstream subscribers see fewer false
  signals).
- Tests that called `self.call(..., caller=self.account2, ...)` or
  similar will now see the correct account on `cmdobj.account` and
  in the normalisation providers. If a test was inadvertently relying
  on the old "everything is self.account" behavior, it will need to
  update its expected output.
- Importing `evennia.contrib.grid.extended_room` or
  `evennia.contrib.game_systems.puzzles` raises ImportError. Anyone
  relying on these contribs in this fork needs to vendor them in
  their game tree.

---

## 6.0.0+underspire.9 — Engine-owned permission cache invalidation

Phase 2 follow-up. `CmdPerm` and `CmdQuell` now invalidate the
`cmd_access_cache` and `lock_cache` for the affected entities
themselves, and fire a new `permissions_changed` signal so downstream
consumers can subscribe instead of monkey-patching the command
classes.

### Engine

- New signal `evennia.commands.signals.permissions_changed`. Kwargs:
  `target` (the mutated entity), `added` (tuple of perm strings),
  `removed` (tuple of perm strings), `actor` (the caller), and
  `account_mode` (bool). For `@quell` / `@unquell` both `added` and
  `removed` are empty since the raw permission list isn't changing
  — only the effective permission stack flips. Sender is the
  concrete Command class (`CmdPerm`, `CmdQuell`).
- `CmdPerm.func` (`evennia/commands/default/admin.py`): tracks the
  actually-added and actually-removed perm strings inside the
  existing loops, then after the mutations calls
  `invalidate_cmd_access_cache(obj)` and `invalidate_lock_cache(obj)`
  for the target, and fires `permissions_changed` exactly once with
  the cumulative sets. Both invalidation and signal are skipped if
  the dispatch was a no-op (e.g. trying to add an already-present
  permission).
- `CmdQuell.func` (`evennia/commands/default/account.py`): on
  successful quell or unquell, invalidates `cmd_access_cache` and
  `lock_cache` for both the account and the active puppet (if any),
  then fires `permissions_changed` once with `target=account`,
  `added=()`, `removed=()`, `account_mode=True`. Already-quelled
  `@quell` and already-unquelled `@unquell` no-ops do not fire.
- Drive-by fix in `CmdQuell.func`: the `self.cmdstring in
  ("@unquell", "@unquell")` test had a duplicated literal from the
  +underspire.8 re-key sweep; collapsed to an equality comparison.

### Invalidate-then-fire ordering

The engine flushes its own caches *before* firing the signal so that
downstream subscribers observe consistent engine state. A
signal-driven invalidation pattern (engine listener subscribed first)
was considered and rejected: django.dispatch ordering is import-
order-dependent, which is too brittle for a correctness-critical
ordering invariant.

### Tests

`evennia/commands/tests.py:TestPermissionsChangedSignal` covers:

- `@perm <obj> = <perm>`: cache cleared on the target, signal fired
  with `added=(perm,)` / `removed=()`.
- `@perm/del <obj> = <perm>`: cache cleared, signal fired with
  `added=()` / `removed=(perm,)`.
- `@perm` against an already-set permission: no-op, no cache flush,
  no signal.
- `@quell` from unquelled state: cache cleared on account and
  active puppet, signal fired with empty sets and
  `account_mode=True`.
- `@unquell` from quelled state: same shape.

### Migration

Downstream code that monkey-patched `CmdPerm.func` / `CmdQuell.func`
to invalidate caches (or to run audit hooks) can subscribe to
`permissions_changed` instead. Example:

```python
from django.dispatch import receiver
from evennia.commands.signals import permissions_changed

@receiver(permissions_changed)
def audit_perm_change(sender, target, added, removed, actor,
                      account_mode, **kwargs):
    audit_event(actor, target, added, removed, account_mode)
```

The engine's own invalidation runs before any subscriber observes
the signal, so subscribers can derive their own state from the
target without worrying about cache coherence.

---

## 6.0.0+underspire.8 — Phase 3: token-boundary matching + drop CMD_IGNORE_PREFIXES

The big semantic break. After this release, `@open` and `open` are
distinct command keys. `CMD_IGNORE_PREFIXES` no longer strips prefix
characters at parse time, so prefix characters are load-bearing parts
of the key.

Couples with a re-key sweep across the engine default cmdsets to align
with the IC/OOC convention (no prefix for character actions, `@` for
account/OOC actions). See [`.agents/docs/code-style.md`](.agents/docs/code-style.md)
(Command Naming) for the rule and [`PHASE3_AUDIT.md`](PHASE3_AUDIT.md)
for the full inventory.

**Breaking:** any command keyed without `@` that downstream callers
were reaching via `@`-prefix (or vice versa) will stop matching. The
re-key list below covers every engine-default that moved.

### Engine matching changes

- `evennia/commands/command.py`:
  - `Command._optimize()` no longer builds `_noprefix_aliases`. The
    mapping from stripped-key → original-key is gone; no external
    callers (engine, contrib, tests) read it.
  - `Command.match()` is now single-pass, token-boundary only. The
    `include_prefixes` kwarg is retained on the signature for
    backward compatibility with any custom subclass that still passes
    it, but it is ignored. Token boundary is enforced via the
    existing `arg_regex` default (`r"^[ /]|\n|$"`).
- `evennia/commands/cmdparser.py`:
  - `build_matches()` no longer calls `raw_string.lstrip(
    _CMD_IGNORE_PREFIXES)`. The `include_prefixes` kwarg is retained
    on the signature but ignored.
  - `cmdparser()` no longer makes a second
    `build_matches(..., include_prefixes=False)` fallback pass on
    no-match. Matching is single-shot.
- `evennia/__init__.py:_init()` emits a `logger.log_warn` once at
  server startup if `settings.CMD_IGNORE_PREFIXES` is non-empty,
  noting that the setting is a no-op since +underspire.8 and will be
  deleted in a future release.

### Engine re-key sweep

Every command listed below received a new `@`-prefixed key (and its
aliases were re-prefixed to match). Help-text Usage lines and inline
`Usage:` error messages were updated. Cross-references inside
docstrings (e.g. "Use unquell" → "Use @unquell") were updated.

- **admin.py**: `ban` → `@ban` (`bans` → `@bans`), `unban` → `@unban`,
  `boot` → `@boot`, `emit` → `@emit` (`pemit`, `remit` → `@pemit`,
  `@remit`), `force` → `@force`, `perm` → `@perm` (`setperm` →
  `@setperm`), `wall` → `@wall`, `userpassword` → `@userpassword`.
- **batchprocess.py**: `batchcommands` → `@batchcommands`
  (`batchcommand`, `batchcmd` → `@batchcommand`, `@batchcmd`),
  `batchcode` → `@batchcode` (`batchcodes` → `@batchcodes`).
- **help.py**: `sethelp` → `@sethelp`. `help` (the player-facing
  meta command) intentionally stays unprefixed.
- **building.py**: `unlink` → `@unlink` (lone straggler; every other
  builder command was already `@`-prefixed in the engine).
- **account.py**: OOC `look` (CmdOOCLook) → `@look` (aliases `l`,
  `ls` → `@l`, `@ls`); IC `look` in `general.py` is unchanged.
  `charcreate` → `@charcreate`, `chardelete` → `@chardelete`,
  `ic` → `@ic` (`puppet` → `@puppet`), `ooc` → `@ooc`
  (`unpuppet` → `@unpuppet`), `sessions` → `@sessions`,
  `who` → `@who` (`doing` → `@doing`), `option` → `@option`
  (`options` → `@options`), `password` → `@password`,
  `quit` → `@quit`, `color` → `@color`, `style` → `@style`,
  `quell` → `@quell` (`unquell` → `@unquell`).
- **comms.py**: `page` → `@page` (`tell` → `@tell`),
  `irc2chan` → `@irc2chan`, `ircstatus` → `@ircstatus`,
  `rss2chan` → `@rss2chan`, `grapevine2chan` → `@grapevine2chan`,
  `discord2chan` → `@discord2chan` (`discord` → `@discord`).
- **general.py**: `nick` → `@nick` (`nickname`, `nicks` → `@nickname`,
  `@nicks`), `access` → `@access` (`groups`, `hierarchy` → `@groups`,
  `@hierarchy`).

Engine call sites that issue these commands via `execute_cmd` were
also updated:

- `CmdMvAttr.func` issues `@cpattr` / `@cpattr/move` (was unprefixed).
- `contrib/rpg/character_creator`: dispatches `@charcreate`, `@ic`,
  `@look` (was unprefixed).
- `contrib/tutorials/tutorial_world/rooms`: dispatches `@quell` (was
  unprefixed).
- `contrib/tutorials/batchprocessor/example_batch_cmds*.ev`: the
  example batch files now use `@create`, `@set`, `@teleport`.
- `prototypes/tests`: test dispatches `@spawn/list` (was unprefixed).

`UnloggedinCmdSet` is unchanged — pre-login commands (`connect`,
`create`, `quit`, etc.) sit outside the IC/OOC distinction.

### Tests

`evennia/commands/tests.py:TestCmdParser.test_build_matches`
rewritten for the new semantics:

- Plain key matches verbatim (`test1 rock` → `test1`).
- `@another command ...` no longer matches the unprefixed
  `another command` key.
- A `&`-keyed command requires the `&` in the input.

All other test failures from the re-key sweep were either expected-
output string updates (e.g. `Use unban` → `Use @unban`) or call-site
fixes (the engine sites listed above).

### Rationale

`CMD_IGNORE_PREFIXES` made the `@`-prefix semantically meaningless:
any command keyed `@open` was also reachable as `open`, and any
command keyed `open` could be reached as `@open`. Downstream games
(Underspire/newmoo) ended up shipping `safe_remove(EngineCmd) +
GameCmd()` pairs to forcibly delete the engine command so the strip
couldn't reach it. This phase removes the strip entirely so the
prefix carries meaning, then re-keys the engine defaults to the
IC/OOC convention so the keys ship right out of the box.

### Migration

- Downstream `CmdAt*` wrapper classes that only added the `@`-prefix
  (no behavior changes) can be deleted in favor of the engine
  defaults. The `safe_remove(EngineCmd) + add(WrapperCmd())` pairs
  go with them.
- Custom commands that subclassed `Command` and overrode `match()`
  with custom prefix-strip logic should drop the prefix-strip — it's
  a no-op now.
- Game code that called `caller.execute_cmd("ban ...")` or similar
  must update to the new keys (`@ban`, etc.).
- If you want to preserve the old behavior temporarily for a
  custom command, add the unprefixed name as an alias on your
  Command subclass. The engine no longer does this for you.
- `CMD_IGNORE_PREFIXES` setting is preserved in `settings_default.py`
  for one release with a startup warning if non-empty. Slated for
  removal in a follow-up.

---

## 6.0.0+underspire.7 — ftfy normalisation in cmdhandler

Moves `ftfy.fix_text` into the engine. Every dispatched raw command
string is repaired at the cmdhandler entry, ahead of cmdset merge,
parser, signal payloads, and the final `cmd.raw_string`. Completes
Phase 2 of the cmdset refactor.

### Engine

- `evennia/commands/cmdhandler.py`: import `ftfy.fix_text` at module
  scope and apply it to `raw_string` at the top of `cmdhandler()`,
  before `generate_cmdset_providers` / `_resolve_signal_session` /
  the cmdset merge. Gated on `INPUT_FTFY_NORMALIZE` (default `True`).
  Skipped for non-str `raw_string` (sentinel paths).
- `evennia/settings_default.py`: new `INPUT_FTFY_NORMALIZE = True`
  setting next to `CMD_IGNORE_PREFIXES`.
- `pyproject.toml`: `ftfy == 6.3.1` is now a hard dependency (was
  not previously declared, direct or transitive). `uv.lock` updated.

### Rationale

The game already ftfy'd input at the parser layer. Promoting the
pass into the engine means downstream consumers (signal receivers,
`cmd.raw_string` readers, the cmdset merge cache, the parser) all
observe normalised text from a single point, instead of relying on
every consumer to ftfy independently. `ftfy` becomes a hard dep
rather than optional, so the import is unconditional; if it is
missing the engine refuses to start, which is the correct contract.

### Migration

- Downstream code that ftfy'd input itself can drop that pass. No
  other action required.
- Set `INPUT_FTFY_NORMALIZE = False` in `server.conf.settings` to
  opt out of the per-dispatch normalisation cost.

---

## 6.0.0+underspire.6 — Delete MuxCommand / MuxAccountCommand

Removes `MuxCommand` and `MuxAccountCommand` entirely. Engine-internal
subclasses (default commands, contribs, test patches) all converted
to `Command` / `AccountCommand` in the same release.

**Breaking:** importing `evennia.commands.default.muxcommand` or
`evennia.default_cmds.MuxCommand` / `MuxAccountCommand` raises
`ImportError`. Subclassing these classes is no longer possible. The
module file `evennia/commands/default/muxcommand.py` is deleted.

### Engine

- `settings_default.COMMAND_DEFAULT_CLASS` switched from
  `evennia.commands.default.muxcommand.MuxCommand` to
  `evennia.commands.command.Command`. Default commands that use
  `COMMAND_DEFAULT_CLASS` now inherit `Command` (switch parsing
  preserved via `Command.parse` since `+underspire.5`).
- `evennia/commands/default/account.py`: every account command now
  subclasses `AccountCommand` directly. All `account_caller =
  True` attributes dropped — redundant on `AccountCommand`, which
  carries the engine flag `account_command_caller = True`.
- `evennia/contrib/*`: every MuxCommand / MuxAccountCommand
  subclass converted to `Command` / `AccountCommand`, covering
  both direct-import (`from evennia.commands.default.muxcommand
  import MuxCommand`) and `default_cmds.MuxCommand` patterns. The
  single contrib `account_caller = True` usage
  (`ingame_reports.CmdReport`) becomes `account_command_caller =
  True`.
- `evennia/commands/default/comms.py`: `CmdChannel` and `CmdPage`
  swapped `account_caller = True` for `account_command_caller =
  True`. `CmdObjectChannel` (character-context channel command)
  overrides with `account_command_caller = False` to disable the
  engine pre-parse normalisation. Test helpers that runtime-toggled
  `cmd.account_caller = False` for character-context dispatch
  swapped to `cmd.account_command_caller = False`.
- `evennia/__init__.py`: `default_cmds` API extended with
  `Command` and `AccountCommand` shortcuts so contrib code can
  subclass them via the public path (`default_cmds.Command`)
  rather than reaching into `evennia.commands.command`.
- `evennia/utils/test_resources.py`: test patches of
  `COMMAND_DEFAULT_CLASS` swapped from `MuxCommand` to `Command`.
- Engine tests that previously exercised `MuxCommand` /
  `MuxAccountCommand` behaviour deleted: the legacy `account_caller`
  parse-time block is gone (no path to test); the engine
  normalisation path is already covered by `AccountCommand` tests;
  the deprecation-warning machinery is gone with the classes.
  `test_mux_command` in `commands/default/tests.py` redirected to
  use `Command` directly (still tests E2E switch handling via
  `.call()`).

### Rationale

After `+underspire.5`, `MuxCommand` no longer carried unique
behaviour beyond the legacy `account_caller` parse-time
normalisation block. Once the engine sweep had no remaining
internal subclasses, the classes were dead weight and the legacy
block had no consumers worth preserving. Delete instead of
deprecate; downstream pins `+underspire.5` if they need migration
time.

### Migration

- **`from evennia.commands.default.muxcommand import MuxCommand`**:
  → `from evennia.commands.command import Command`. Switch parsing
  keeps working (now in `Command.parse`). If your `parse` override
  called `super().parse()` expecting the historical no-op, add
  `parse_mux_syntax = False` to the class — see `+underspire.5`
  migration notes.
- **`from evennia.commands.default.muxcommand import
  MuxAccountCommand`**: → `from evennia.commands.command import
  AccountCommand`. Engine pre-parse normalisation already gives the
  same `caller`/`account`/`character` shape via the
  `account_command_caller = True` flag.
- **`default_cmds.MuxCommand` / `default_cmds.MuxAccountCommand`**:
  → `default_cmds.Command` / `default_cmds.AccountCommand` (added
  to the API in this release).
- **Third-party subclasses with `account_caller = True`** (without
  the engine flag): the parse-time normalisation block is gone.
  Switch the base to `AccountCommand` (cleanest) or set
  `account_command_caller = True` directly on the class.

### Tests

All `evennia.commands`, `evennia.commands.default`, and
`evennia.contrib` tests pass (modulo pre-existing failures in
`puzzles`, `mail`, and `extended_room` that exist on
`+underspire.5` and are unrelated to this change).

### Bug fix: Redis L2 attribute cache + idmapper teardown

`RedisCachedModelAttributeBackend` was not wired into the idmapper
`flush_cache` path. CI runs with persistent Redis saw stale
`Attribute` rows leak from one test's writes into a later test's
reads; local runs (Redis backend disabled by default) never tripped
it. New `evennia.typeclasses.redis_attr_cache.flush_all_keys()`
scans `attr:<version>:*` and deletes via `SCAN` (non-blocking on
large keyspaces); `evennia.utils.idmapper.models.flush_cache` calls
it after the in-process idmapper flush. No-op when
`ATTRIBUTE_REDIS_CACHE_ENABLED` is off or Redis is unreachable,
wrapped so any Redis hiccup never breaks idmapper flush.

Covers all three flush call-sites by free: test `tearDown`,
`post_migrate` signal, and the `@reload/flush` admin command.

Three new tests in `test_attribute_fork.TestRedisAttrCache`:
`flush_cache` drives the scan+delete; disabled flag is a no-op
without opening a connection; unreachable Redis is a no-op without
raising.

---

## 6.0.0+underspire.5 — Switch parsing in Command.parse

MuxCommand's switch / lhs / rhs parsing is promoted into the base
`Command.parse`. Sets up the `MuxCommand = Command` collapse coming
in a follow-up release.

### Engine

- `Command.parse` now splits `self.args` into `switches`, `lhs`,
  `rhs`, `lhslist`, `rhslist`, `arglist`, and stashes the original
  on `self.raw`. Honours optional class attrs `switch_options`
  (validates supplied switches, abbreviation match, warns on
  unknown/ambiguous via `self.msg`) and `rhs_split` (delimiter or
  iterable of delimiters, default `"="`).
- `Command.parse_mux_syntax = True` class flag gates the new
  behaviour. Subclasses that want `super().parse()` to behave like
  the historical no-op set `parse_mux_syntax = False` — one line,
  no need to override `parse`.
- `MuxCommand.parse` reduced to `super().parse()` + the legacy
  `account_caller` normalisation block. The block stays for
  third-party subclasses that set `account_caller = True` without
  the engine `account_command_caller` flag; it short-circuits when
  the engine flag is set (unchanged from `+underspire.3.1`).

### Backwards compatibility

- Existing `MuxCommand` subclasses: no change. `MuxCommand.parse`
  still produces the same attribute shape (via `super().parse()` now
  instead of inline).
- Existing `Command` subclasses that override `parse` without
  calling `super`: no change.
- Existing `Command` subclasses that *do* call `super().parse()`
  expecting the historical no-op: **breaking** — they now get switch
  parsing applied to `self.args`. Fix: add `parse_mux_syntax =
  False` to the subclass. The pattern of calling `super` on a no-op
  is unusual but exists; flagged loudly here.

### Migration

If `super().parse()` calls in your `Command` subclasses produced
unexpected `self.args` mutation (stripped, post-switch), add:

```python
class MyCmd(Command):
    parse_mux_syntax = False
    def parse(self):
        super().parse()  # no-op shape, as before
        ...
```

If you want the new switch parsing in a `Command` subclass that
previously had its own parse logic, drop the override and let
`Command.parse` handle it (or call `super().parse()` first and add
your own logic after).

### Tests

`evennia.commands.tests.TestAccountCommandNormalization` extended
with three cases: base `Command.parse` produces MuxCommand-style
attrs; `parse_mux_syntax = False` opts out; `MuxCommand.parse`
delegates to `super` then runs the legacy `account_caller` block.

---

## 6.0.0+underspire.4 — Drop at_pre_cmd subclass guard

The `__init_subclass__` guard introduced in `+underspire.2` is
removed. The post-parse `at_pre_cmd` hook is now freely
subclass-able: override it for input validation that needs parsed
state (`self.args`, `self.switches`, `self.character`) or for
gating that depends on parse results.

### Engine

- `Command.__init_subclass__` deleted. There was no other logic in
  it; the guard was its sole purpose.
- `Command.at_pre_cmd` docstring updated to describe its role
  ("preferred home for post-parse, pre-dispatch logic") and to
  distinguish it from `at_pre_parse`.

### Dispatch order (unchanged from `+underspire.2`)

```
at_pre_parse → parse → at_pre_cmd → func → at_post_cmd
```

Both `at_pre_parse` and `at_pre_cmd` return-truthy-to-abort. Truthy
abort from `at_pre_cmd` skips `func` and `at_post_cmd`.

### Migration

No required action. Downstream that previously moved post-parse
logic into `func` (or into a `parse` override) to dodge the guard
can now move it into `at_pre_cmd` if that reads better. No code
that ran on `+underspire.3.1` breaks on `+underspire.4`.

### Tests

`TestAtPreCmdRename.test_subclassing_at_pre_cmd_raises_typeerror_at_import`
deleted. Replaced with
`test_at_pre_cmd_override_runs_after_parse_before_func` and
`test_at_pre_cmd_truthy_return_aborts_after_parse_before_func`,
which exercise the now-allowed override path.

---

## 6.0.0+underspire.3.1 — MuxAccountCommand opts into engine normalisation

Targeted follow-up to `+underspire.3` driven by downstream feedback.
Unifies the detection contract for "this command is account-context"
so a single flag covers both engine `AccountCommand` and stock
`MuxAccountCommand` subclasses.

### Engine

- `evennia.commands.default.muxcommand.MuxAccountCommand` now sets
  `account_command_caller = True`. Engine pre-parse normalisation
  (`cmdhandler._normalize_account_command_caller`) therefore runs for
  every `MuxAccountCommand` subclass, including stock commands like
  `CmdIC`, `CmdOOC`, and the account-context `CmdHelp`.
- `MuxCommand.parse`'s legacy `account_caller` normalisation block
  short-circuits when `account_command_caller` is truthy on the
  instance. The engine path produces the same
  `self.caller` / `self.account` / `self.character` shape, so the
  legacy block would just re-call `get_puppet` for no benefit. Pure
  `account_caller`-only third-party subclasses (no engine flag) keep
  the legacy path during the deprecation window.

### Downstream detection contract

Use the flag, not `isinstance`:

```python
def _is_account_command(cmd):
    return getattr(cmd, "account_command_caller", False)
```

This covers both `evennia.commands.command.AccountCommand` and
`MuxAccountCommand` subclasses uniformly. A pure
`isinstance(matched, AccountCommand)` check is **not** safe yet:
stock `MuxAccountCommand` does not inherit from engine
`AccountCommand`. The flag-based check stays correct across the
forthcoming MuxCommand sweep too.

### Migration

No breaking changes. Downstream that previously fell back to
`getattr(cmd, "account_caller", False)` because the engine flag missed
stock account commands can drop the fallback and rely on
`account_command_caller` alone.

### Note on tagging

Local version uses an extra dot (`+underspire.3.1`), which is
permitted by PEP 440 local-segment rules and orders strictly above
`+underspire.3`. Tagged on a `--no-ff` merge into `underspire`.

---

## 6.0.0+underspire.3 — Phase 2 step 2: AccountCommand + caller normalisation

Adds the first-class engine class for Account-level commands and the
cmdhandler normalisation that guarantees a consistent
``caller``/``account``/``character`` shape before any hook runs.

### Engine

- New ``evennia.commands.command.AccountCommand``: sibling of
  ``Command`` with class flag ``account_command_caller = True``. No
  metaclass tricks; subclasses just inherit.
- ``Command.account_command_caller = False`` added on the base so the
  attribute always exists.
- ``cmdhandler._normalize_account_command_caller(cmd, caller,
  cmdset_providers)``: invoked inside ``_run_command`` after the
  existing runtime-attr block and *before* the ``_testing`` early
  return and ``at_pre_parse``. For commands with
  ``account_command_caller`` truthy:
  - ``cmd.caller`` becomes the Account (from
    ``cmdset_providers["account"]``, falling back to
    ``caller.account``).
  - ``cmd.account`` becomes the same Account (alias of ``cmd.caller``).
  - ``cmd.character`` becomes the puppet for the dispatching session
    (``cmdset_providers.get("object")``), or ``None`` if OOC.
  No-op for ordinary ``Command`` subclasses; their ``cmd.caller`` is
  untouched and no ``character`` attribute is set on the instance.
- ``EvenniaCommandTestMixin.call`` mirrors the normalisation so
  ``BaseEvenniaCommandTest``-based tests of ``AccountCommand``
  subclasses observe the same attribute shape as live dispatch.

### Tracing / caches (unchanged on purpose)

- ``command_trace.begin_command_trace`` continues to receive the
  pre-normalisation ``caller`` (the dispatch origin). The trace_id
  surfaces puppet→account routing in error logs without rewriting
  caller identity.
- ``cmd_access_cache`` keys on the pre-normalisation ``caller``
  (resolved during ``_COMMAND_PARSER``). Access is still gated against
  the puppeted Character; normalisation only affects ``cmd.caller``
  after the match, which is the right boundary. ``_cmd_identity``
  differentiates ``Command`` and ``AccountCommand`` subclasses
  naturally via class name, so cache keys do not collide.

### Migration

No breaking changes. Downstream ``AccountCommand`` shims can subclass
``evennia.commands.command.AccountCommand`` and drop their own
``_normalize_account_caller`` step. See
``CMDSET_MIGRATION.md`` §"Phase 2 — Step 2".

---

## 6.0.0+underspire.2 — Phase 2 step 1: at_pre_cmd rename + dispatch reorder

First slice of the Phase 2 cmdset refactor. Renames the pre-parse hook
and introduces a (currently engine-only) post-parse hook. Pure rename
under a hard-error guard: no semantic change yet, no auto-alias.

### Breaking

- **`Command.at_pre_cmd` renamed to `Command.at_pre_parse`.** Same
  semantics (runs before `parse()`, return truthy to abort).
- **`Command.__init_subclass__` hard-error guard.** Any subclass that
  defines `at_pre_cmd` raises `TypeError` at class-creation pointing at
  the file/class with a migration message. The new post-parse
  `at_pre_cmd` exists but is engine-only during this window;
  subclassing it is forbidden so legacy overrides cannot silently
  no-op.
- **Cmdhandler dispatch order changed** to
  `at_pre_parse → parse → at_pre_cmd → func → at_post_cmd`. The new
  `at_pre_cmd` runs *after* parse. The base class's `at_pre_cmd` is a
  no-op; it does nothing observable to current code.
- `evennia.utils.test_resources.BaseEvenniaCommandTest.call` mirrors
  the new dispatch order. Tests that subclass `Command` and override
  the old hook must rename to `at_pre_parse`.

### Migration

If you see `TypeError: <Module>.<Class> defines at_pre_cmd, which was
renamed to at_pre_parse...` at import:

1. Rename the method to `at_pre_parse`.
2. Update any `super().at_pre_cmd()` calls to `super().at_pre_parse()`.
3. Re-import; the guard accepts the new name.

The post-parse `at_pre_cmd` will become subclass-able in a future
release (target: `6.0.0+underspire.4`) once the guard is removed.

### Engine renames in this commit

`evennia/contrib/game_systems/storage/storage.py`,
`evennia/contrib/base_systems/email_login/email_login.py`,
`evennia/contrib/base_systems/ingame_reports/reports.py`,
`evennia/commands/default/unloggedin.py`. The no-op
`MuxCommand.at_pre_cmd` stub was deleted (it inherited the base
no-op). Test fixtures in `commands/default/tests.py` and
`contrib/base_systems/ingame_reports/tests.py` renamed.

### Signals

`on_command_pre.elapsed_ms` window unchanged in semantics but
documented relative to `at_pre_parse` rather than the old name. No
signal contract changes.

### PostgreSQL session init via `connection_created`

`apply_postgres_engine_defaults` no longer injects
`OPTIONS["options"] = "-c statement_timeout=..."` and
`build_read_replica_entry` no longer injects
`-c default_transaction_read_only=on`. PgBouncer in transaction-pool
mode rejects the `options` startup parameter at the protocol level
(`FATAL: unsupported startup parameter in options: ...`), so the
previous defaults broke pooled deployments out of the box.

Replacement: a `connection_created` receiver issues `SET
statement_timeout` on the `default` alias and `SET
default_transaction_read_only = on` on aliases registered by
`build_read_replica_entry`. Works through PgBouncer. Caveat for pure
transaction-pool deployments: session-level `SET` may not persist
across backend rebinding — set at the role level (`ALTER ROLE ... SET
statement_timeout = '30s'`) for hard guarantees, and treat the signal
receiver as best-effort on top.

`build_read_replica_entry(primary, name=...)` now has a side effect:
the `name` argument is registered in
`evennia.server.database_postgres._READ_REPLICA_ALIASES` so the
receiver knows which aliases get the read-only flag. The caller must
still assign the returned dict at `DATABASES[name]`.

---

## 6.0.0+underspire.1 — initial fork version mark

> **⚠ Packaging caveat.** The tagged commit (`02063d4e6`) bumped
> `evennia/VERSION.txt` to `6.0.0+underspire.1` but `pyproject.toml`
> still read `6.0.0`. A follow-up commit (`607ecc4fa`) fixed
> `pyproject.toml`, but it landed after the tag. Pip-installing from
> exactly `underspire.1` therefore records the installed version as
> `6.0.0` (no local segment), even though `evennia.__version__` at
> runtime reads `6.0.0+underspire.1` from `VERSION.txt`.
>
> Consumers that gate on `pkg_resources.get_distribution("evennia").version`
> (or equivalent) should pin **`>= 6.0.0+underspire.2`** instead. Runtime
> code reading `evennia.__version__` is unaffected.
>
> Tags from `underspire.2` onward bundle both files in the same commit.


First release tagged after the fork diverged meaningfully from upstream
`6.0.0`. Base = upstream `6.0.0`; everything below is fork-only.

### Engine

**Cmdset refactor (phases 0 + 1, partial)**
- Phase 0 hygiene: `CmdSet.remove()` is idempotent and returns `bool`;
  `CmdSet.replace(old, new)` added. Removes the need for the downstream
  `cmdset_utils.py` helper.
- Phase 1 signals (`evennia.commands.signals`): `on_command_pre`,
  `on_command_post`, `on_command_error`, plus the companion
  `on_cmdset_merge_error` for the three merge-error sites in
  `get_and_merge_cmdsets`. `send_robust` semantics so receivers cannot
  break dispatch.
- `ErrorReported` carries `.trace_id`, populated from
  `command_trace.get_trace_id()` when the exception is raised inside an
  active command trace.
- Session-proxy contract on `cmdhandler.cmdhandler(session=...)`: any
  object with `get_cmdset_providers()` works. Multipuppet relays can
  drop their `Command` monkey-patch.
- Signal payload + `cmd.session` always carry the real `ServerSession`
  when one is involved. Proxies expose the wrapped real session via a
  `real_session` attribute. `callertype="session"` no longer leaks a
  `None` session into signal kwargs.

See `CMDSET_REFACTOR.md` and `CMDSET_MIGRATION.md` for the full plan
(phases 2–4 pending).

**Attribute system**
- Typed value columns on `Attribute` (`db_val_type`, `db_int_val`,
  `db_float_val`, `db_str_val`) for fast-path reads of simple Python
  types; pickle path retained for complex values and back-compat.
- Write-behind dirty queue for attribute updates: `do_update_attribute`
  marks the attr dirty instead of saving immediately; `flush_all_dirty()`
  bulk-writes from the server tick.
- **ORM query correctness fix:** `TypedObjectManager.get_queryset` and
  a new `AttributeManager.get_queryset` flush pending writes before any
  query, so `.filter(db_attributes__db_value=...)` and
  `get_by_attribute(value=obj)` see committed values rather than stale
  rows. Reentrance guarded.
- Redis-backed attribute cache backend added (`redis_attr_cache.py`).

**Command tracing / profiling**
- `evennia.utils.command_trace` provides a thread-local trace-id around
  each dispatched command, surfaced on `ErrorReported` and signal kwargs.
- `evennia.server.prometheus_metrics` exposes per-command and
  attribute-flush counters/gauges.
- `evennia.typeclasses.attribute_metrics` records pending/flushed/duration
  for the write-behind queue.

**Cmdset performance / caching**
- `commands/location_cmdset_cache.py` caches local-obj cmdset stacks
  per-location keyed by the merge fingerprint.
- `commands/cmd_access_cache.py` caches per-caller cmd access results.

**Object resolution**
- `evennia/objects/scene_index.py` added for fast recipient resolution in
  `DefaultObject.get_msg_recipients()` (formerly silent-fallback wrapped;
  now hard errors propagate so misuse is visible).

**At-init scheduler / signal lifecycle**
- `evennia/server/at_init_scheduler.py` defers `at_init` hooks to avoid
  reload races.

**Other**
- Python 3.13/3.14 syntax warning fixes.
- IntFlag serialization in `dbserialize.to_pickle` honors the enum value.
- ContentType-based dbobj packing handles defaultdict misses cleanly.

### Migration / breaking

- Settings: new `INPUT_FTFY_NORMALIZE`, `COMMAND_TRACE_ENABLED`,
  `ATTRIBUTE_FLUSH_*`, `ATTRIBUTE_BACKEND_CLASS` (see
  `evennia/settings_default.py`).
- `do_update_attribute` no longer saves synchronously. Code paths that
  expected an immediate DB write should call `flush_all_dirty()` or rely
  on the manager-level flush in `get_queryset`.
- `on_command_error` does NOT fire for cmdset-merge failures; subscribe
  to `on_cmdset_merge_error` for those.

### Known gaps

- Cmdset refactor phases 2–4 not yet started. Tracked in
  `CMDSET_REFACTOR.md` §5.
- `pyproject.toml` version field tracks upstream `6.0.0`; update to
  `6.0.0+underspire.1` to match `VERSION.txt`.
