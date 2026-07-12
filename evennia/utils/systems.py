"""
The unified System Scheduler: declared recurring work on the reactor.

A **System** is a named unit of recurring work with a declared cadence (WHEN
it fires) and scope (WHICH entities it runs over). Systems are registered at
server start from declared modules, driven by a single 1 Hz `LoopingCall`,
and run on the reactor thread where touching game objects is safe. This is
the engine's only answer to "run this every N seconds / at time T"; the
complete async surface is exactly four primitives:

- `evennia.utils.delay` — one-shot, action-tied, reactor. Dropped on reload.
- this scheduler — anything recurring, reactor.
- the job queue (`evennia.jobs`) — durable/retryable units that must happen
  even across downtime.
- `evennia.utils.defer` — reactor<->worker location; composes into the others.

The rule when scheduler and job queue blur: missing a run is acceptable ->
scheduler; must happen eventually / across downtime / retryable -> job queue.

Cadence (WHEN) — three kinds:

- `every(seconds=N)` — fire when at least N seconds have elapsed since the
  last run. Last-run is in-memory only; on boot the first fire happens N
  seconds after startup.
- `calendar(daily="HH:MM" | weekly=(weekday, "HH:MM") | monthly=(day,
  "HH:MM"))` — fire when a UTC boundary is crossed since the last run.
  Exactly one spec per cadence; weekday 0 is Monday; a monthly day short
  months lack clamps to the month's last day. Last-run is durable
  (ServerConfig): a boundary that passed while the server was down fires
  once on the next driver tick — once, not N times, and not never. A
  brand-new calendar system primes to "now" and first fires at the next
  boundary.
- `every_tick()` — fire on every driver tick. The driver ticks at
  `TICK_INTERVAL` (1 Hz); that rate is part of this contract, not a setting.

The scheduler decides whether to fire; bodies never check the clock.

Scope (WHICH / offline policy):

- `global_scope()` — run once per fire, no entity iteration; the body does
  its own querying. For cleanup jobs, digests, engine state.
- `online_puppets()` — run over currently-puppeted characters only
  (`ctx.entities`, live objects, gathered on the reactor; cheap, bounded).
  Offline players do NOT advance. A game wanting freeze-while-offline +
  catch-up-on-return pairs this with its own login-time reconciler; that
  reconciler is a *presence* trigger and deliberately not part of this
  scheduler (the scheduler owns clock-triggered work only).
- `all_entities(component=...)` — run over all matching entities regardless
  of online state: true world-state advancement (economy, respawn, world
  events). `component` is a typeclass path (or list of paths) today; it is
  the seam that becomes an ECS component query later without changing call
  sites. The id query runs off-reactor via `evennia.utils.defer.in_thread`
  and the body receives plain pks as `ctx.entity_ids`.

Dangerous cells in the scope x cadence matrix: `every_tick` over
`all_entities` is a reactor killer (a full-world sweep at 1 Hz) and logs a
loud warning at registration. `every_tick` is only sane over bounded online
sets or trivially cheap global bodies.

The run context — bodies are called ONCE PER FIRE, never once-per-entity:

- `ctx.now` — wall-clock epoch of this fire. Use it instead of calling time
  yourself; it keeps bodies testable and catch-up-aware.
- `ctx.dt` — real seconds since this system last ran (may exceed the nominal
  cadence after stalls or downtime). **System-level, never per-entity.** For
  `global_scope` and `all_entities` systems, `dt`-based integration (regen,
  decay) is correct. For `online_puppets` it is a trap: the puppet set
  changes across reconnects, so "seconds since the system fired" is not
  "seconds since this character advanced" — `hp += rate * ctx.dt` in an
  `online_puppets` body is wrong on every reconnect. Per-entity elapsed
  belongs on the entity (a last-touched attribute read by the body and by
  the game's login reconciler).
- `ctx.entities` / `ctx.entity_ids` — see Scope above; `None` for scopes
  that don't provide them.

A fire may be asynchronous: `run(ctx)` may return a Deferred, and
`all_entities` fires always are (the id query is off-reactor). Overlapping
fires of one system are forbidden — if a system comes due while its previous
fire is still in flight, the driver skips that fire and logs a warning; it
never stacks runs. Last-run advances at fire-decision time, so cadence is
fire-to-fire regardless of body duration. Errors in one system are logged
with full traceback and never kill the driver or other systems.

Heavy systems must not stall the reactor. For scalar-attribute sweeps over
many entities (the dominant case — regen, decay, hunger) use
`evennia.utils.bulk_tick.BulkTickContext` end-to-end: gather from L1 on the
reactor, compute in a worker, apply back in a single bulk UPDATE. The
reactor-stall watchdog flags regressions.

Registration is declared-startup-only. Modules listed in
`settings.SYSTEM_MODULES` (plus the engine's own
`evennia.server.engine_systems`) are loaded at server start; each must
define a `register_systems()` callable that registers at least one system.
A listed module that fails to import, lacks the callable, or registers
nothing raises `SystemRegistrationError` at startup — a loud error, never a
silent absence. There is deliberately NO runtime per-instance registration
("tick this object in 90 seconds"); the homes for that shape are: lazy
expiry checks on access (buffs, temporary effects — store `expires_at`, no
timer at all), `evennia.utils.delay` for drop-on-reload one-shots, and the
job queue for durable work.

Ownership split: the engine owns this mechanism and systems over engine
state (`flush-attributes` in `evennia.server.engine_systems` is the worked
example). Every game system body lives in game modules and registers via
`SYSTEM_MODULES`; the engine never imports or names them.

Worked example (game-side module listed in `SYSTEM_MODULES`)::

    from evennia.utils import systems

    def _hunger(ctx):
        # online-only pulse: per-entity elapsed lives on the entity,
        # NOT in ctx.dt (see the ctx.dt rule above).
        for char in ctx.entities:
            char.tick_hunger(at=ctx.now)

    def _faction_payday(ctx):
        # advances regardless of who is online; ctx.dt spans downtime.
        # still best-effort: a body error forfeits the boundary (no retry),
        # so a payday that must NEVER be missed belongs on the job queue.
        ...

    def register_systems():
        systems.register(
            name="hunger",
            cadence=systems.every(60),
            scope=systems.online_puppets(),
            run=_hunger,
        )
        systems.register(
            name="faction-payday",
            cadence=systems.calendar(monthly=(1, "00:00")),
            scope=systems.global_scope(),
            run=_faction_payday,
        )
"""

import calendar as _stdlib_calendar
import importlib
import time
from datetime import datetime, timedelta, timezone

from django.conf import settings

from evennia.utils import clock, logger

#: Driver tick rate in seconds. Part of the `every_tick` contract ("once per
#: driver tick; the driver ticks at 1 Hz") — a constant, not a setting,
#: because changing it silently changes every `every_tick` system's meaning.
TICK_INTERVAL = 1.0

#: Consecutive due-while-in-flight skips after which the overlap warning
#: escalates to an error: a wedged system (hung worker, never-firing
#: Deferred) must not hide as an endless trickle of warnings.
_SKIP_ESCALATION_THRESHOLD = 3

_EVERY = "every"
_CALENDAR = "calendar"
_EVERY_TICK = "every_tick"

_GLOBAL = "global"
_ONLINE_PUPPETS = "online_puppets"
_ALL_ENTITIES = "all_entities"

#: Workload classes and their admission priority (lower = admitted first when a
#: tick is over its admission budget). A due system's firing order within a tick
#: is (class priority, then oldest-overdue first) so interactive work wins and no
#: class can be starved indefinitely — a deferred system's overdue age grows and
#: eventually outranks fresher work.
INTERACTIVE = "interactive"
SIMULATION = "simulation"
PERSISTENCE = "persistence"
MAINTENANCE = "maintenance"
_WORKLOAD_PRIORITY = {
    INTERACTIVE: 0,
    PERSISTENCE: 1,
    SIMULATION: 2,
    MAINTENANCE: 3,
}
_DEFAULT_WORKLOAD = SIMULATION

#: Engine-owned system modules, always loaded before `settings.SYSTEM_MODULES`
#: so a game overriding that setting cannot drop engine systems.
_ENGINE_SYSTEM_MODULES = ("evennia.server.engine_systems",)

_LAST_RUN_CONF_PREFIX = "system_lastrun_"

_SYSTEM_REGISTRY = {}


class SystemRegistrationError(Exception):
    """Raised on invalid registration or a broken `SYSTEM_MODULES` entry."""


# ---------------------------------------------------------------------------
# Cadence
# ---------------------------------------------------------------------------


def _parse_hhmm(value):
    """
    Parse an "HH:MM" string into an (hour, minute) tuple.

    Args:
        value (str): Time-of-day string, 24h UTC.

    Returns:
        tuple: `(hour, minute)` ints.

    Raises:
        ValueError: If the string is not a valid HH:MM time.

    """
    try:
        hour_str, minute_str = str(value).split(":")
        hour, minute = int(hour_str), int(minute_str)
    except (ValueError, TypeError) as err:
        raise ValueError(f"calendar time must be 'HH:MM', got {value!r}") from err
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"calendar time out of range: {value!r}")
    return hour, minute


class Cadence:
    """
    WHEN a system fires. Built via `every`, `calendar` or `every_tick`.

    Attributes:
        kind (str): One of "every", "calendar", "every_tick".
        seconds (float or None): Interval for the "every" kind.
        at (tuple or None): `(hour, minute)` UTC for the "calendar" kind.
        weekday (int or None): 0-6 (Monday-Sunday) for weekly calendars.
        monthday (int or None): 1-31 for monthly calendars (clamped to the
            month's last day in short months).

    """

    def __init__(self, kind, seconds=None, at=None, weekday=None, monthday=None):
        self.kind = kind
        self.seconds = seconds
        self.at = at
        self.weekday = weekday
        self.monthday = monthday

    def describe(self):
        """
        Returns:
            str: Human-readable cadence summary for introspection.

        """
        if self.kind == _EVERY:
            return f"every {self.seconds:g}s"
        if self.kind == _EVERY_TICK:
            return f"every tick ({TICK_INTERVAL:g}s)"
        hhmm = "%02d:%02d" % self.at
        if self.weekday is not None:
            day = _stdlib_calendar.day_name[self.weekday]
            return f"weekly {day} {hhmm} UTC"
        if self.monthday is not None:
            return f"monthly day {self.monthday} {hhmm} UTC"
        return f"daily {hhmm} UTC"

    def __repr__(self):
        return f"<Cadence {self.describe()}>"


def every(seconds):
    """
    Cadence firing when at least `seconds` have elapsed since the last run.

    Last-run is in-memory only: after a reboot the first fire happens
    `seconds` after startup.

    Args:
        seconds (float): Minimum elapsed seconds between fires. Must be > 0.

    Returns:
        Cadence: The cadence.

    Raises:
        ValueError: If `seconds` is not positive.

    """
    seconds = float(seconds)
    if seconds <= 0:
        raise ValueError(f"every() requires seconds > 0, got {seconds}")
    return Cadence(_EVERY, seconds=seconds)


def calendar(daily=None, weekly=None, monthly=None):
    """
    Cadence firing when a UTC calendar boundary is crossed since the last run.

    Last-run is durable: a boundary that passed while the server was down
    fires once on the next driver tick (catch-up-by-one, never a replay).
    The fire is still best-effort: last-run is consumed at fire time, so a
    body that errors forfeits that boundary with no retry. Work that must
    happen eventually belongs on the job queue, not here. The durable
    last-run is keyed by system name — renaming a calendar system primes
    fresh (and orphans the old `system_lastrun_<name>` ServerConfig row).

    Args:
        daily (str, optional): "HH:MM" UTC.
        weekly (tuple, optional): `(weekday, "HH:MM")` with weekday 0-6,
            Monday is 0.
        monthly (tuple, optional): `(day, "HH:MM")` with day 1-31; days short
            months lack clamp to the month's last day.

    Returns:
        Cadence: The cadence.

    Raises:
        ValueError: If not exactly one spec is given, or a spec is malformed.

    """
    given = [spec for spec in (daily, weekly, monthly) if spec is not None]
    if len(given) != 1:
        raise ValueError(
            "calendar() takes exactly one of daily=, weekly=, monthly= "
            "(a system wanting two calendars is two systems)"
        )
    if daily is not None:
        return Cadence(_CALENDAR, at=_parse_hhmm(daily))
    if weekly is not None:
        weekday, hhmm = weekly
        weekday = int(weekday)
        if not 0 <= weekday <= 6:
            raise ValueError(f"weekly weekday must be 0-6 (Monday=0), got {weekday}")
        return Cadence(_CALENDAR, at=_parse_hhmm(hhmm), weekday=weekday)
    monthday, hhmm = monthly
    monthday = int(monthday)
    if not 1 <= monthday <= 31:
        raise ValueError(f"monthly day must be 1-31, got {monthday}")
    return Cadence(_CALENDAR, at=_parse_hhmm(hhmm), monthday=monthday)


def every_tick():
    """
    Cadence firing on every driver tick (`TICK_INTERVAL`, 1 Hz).

    Only sane over bounded online sets (`online_puppets`) or trivially cheap
    `global_scope` bodies; see the dangerous-cells note in the module
    docstring.

    Returns:
        Cadence: The cadence.

    """
    return Cadence(_EVERY_TICK)


def _most_recent_occurrence(cadence, now):
    """
    The latest calendar boundary at or before `now`, as epoch seconds.

    Args:
        cadence (Cadence): A calendar-kind cadence.
        now (float): Current wall-clock epoch seconds.

    Returns:
        float: Epoch seconds of the most recent boundary.

    """
    dt_now = datetime.fromtimestamp(now, tz=timezone.utc)
    hour, minute = cadence.at
    if cadence.weekday is not None:
        candidate = dt_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        candidate -= timedelta(days=(dt_now.weekday() - cadence.weekday) % 7)
        if candidate > dt_now:
            candidate -= timedelta(days=7)
    elif cadence.monthday is not None:

        def _clamped(year, month):
            last_day = _stdlib_calendar.monthrange(year, month)[1]
            return datetime(
                year,
                month,
                min(cadence.monthday, last_day),
                hour,
                minute,
                tzinfo=timezone.utc,
            )

        candidate = _clamped(dt_now.year, dt_now.month)
        if candidate > dt_now:
            year, month = dt_now.year, dt_now.month - 1
            if month == 0:
                year, month = year - 1, 12
            candidate = _clamped(year, month)
    else:
        candidate = dt_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate > dt_now:
            candidate -= timedelta(days=1)
    return candidate.timestamp()


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


class Scope:
    """
    WHICH entities a system runs over, and thereby its offline policy.

    Built via `global_scope`, `online_puppets` or `all_entities`.

    Attributes:
        kind (str): One of "global", "online_puppets", "all_entities".
        component (str, list or None): Typeclass path(s) for "all_entities".

    """

    def __init__(self, kind, component=None):
        self.kind = kind
        self.component = component

    def describe(self):
        """
        Returns:
            str: Human-readable scope summary for introspection.

        """
        if self.kind == _ALL_ENTITIES:
            return f"all_entities({self.component})"
        return self.kind

    def __repr__(self):
        return f"<Scope {self.describe()}>"


def global_scope():
    """
    Scope for systems that run once per fire with no entity iteration.

    Returns:
        Scope: The scope.

    """
    return Scope(_GLOBAL)


def online_puppets():
    """
    Scope over currently-puppeted characters only (`ctx.entities`).

    Offline players do not advance; pair with a game-side login reconciler
    for freeze-while-offline + catch-up-on-return semantics.

    Returns:
        Scope: The scope.

    """
    return Scope(_ONLINE_PUPPETS)


def all_entities(component):
    """
    Scope over all matching entities regardless of online state.

    The id query runs off-reactor; the body receives plain pks as
    `ctx.entity_ids`. `component` is the future ECS seam — today a typeclass
    path matched exactly against `db_typeclass_path`: subclasses and
    alternate import paths are NOT matched. List every concrete path the
    sweep should cover.

    Args:
        component (str or list): Typeclass path or list of paths.

    Returns:
        Scope: The scope.

    Raises:
        ValueError: If `component` is empty.

    """
    if not component:
        raise ValueError("all_entities() requires a component (typeclass path or list)")
    return Scope(_ALL_ENTITIES, component=component)


# ---------------------------------------------------------------------------
# System and context
# ---------------------------------------------------------------------------


class SystemContext:
    """
    Per-fire context passed to a system body as its only argument.

    Attributes:
        now (float): Wall-clock epoch seconds of this fire.
        dt (float): Real seconds since this system last ran. System-level,
            never per-entity — see the module docstring before integrating
            over it in an `online_puppets` body.
        entities (list or None): Live puppet objects (`online_puppets` scope).
        entity_ids (list or None): Plain pks (`all_entities` scope).

    """

    def __init__(self, now, dt, entities=None, entity_ids=None):
        self.now = now
        self.dt = dt
        self.entities = entities
        self.entity_ids = entity_ids


class System:
    """
    A registered unit of recurring work.

    Attributes:
        name (str): Unique, stable name; the registry and persistence key.
        cadence (Cadence): When the system fires.
        scope (Scope): What it runs over.
        run (callable): The body, called as `run(ctx)` once per fire.
        last_run (float or None): Epoch of the last fire decision.
        fire_count (int): Fires since registration (this process).
        in_flight (bool): Whether a fire is currently executing.
        skip_count (int): Consecutive fires skipped because the previous one
            was still in flight; resets on a successful fire decision.

    """

    def __init__(self, name, cadence, scope, run, workload_class=_DEFAULT_WORKLOAD):
        if not name or not isinstance(name, str):
            raise SystemRegistrationError(f"system name must be a non-empty str, got {name!r}")
        if not isinstance(cadence, Cadence):
            raise SystemRegistrationError(f"system '{name}': cadence must be a Cadence")
        if not isinstance(scope, Scope):
            raise SystemRegistrationError(f"system '{name}': scope must be a Scope")
        if not callable(run):
            raise SystemRegistrationError(f"system '{name}': run must be callable")
        if workload_class not in _WORKLOAD_PRIORITY:
            raise SystemRegistrationError(
                f"system '{name}': workload_class must be one of "
                f"{sorted(_WORKLOAD_PRIORITY)}, got {workload_class!r}"
            )
        self.name = name
        self.cadence = cadence
        self.scope = scope
        self.run = run
        self.workload_class = workload_class
        self.last_run = None
        self.fire_count = 0
        self.in_flight = False
        self.skip_count = 0
        self._calendar_loaded = False

    def __repr__(self):
        return f"<System '{self.name}' {self.cadence.describe()} over {self.scope.describe()}>"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def register_system(system):
    """
    Add a `System` to the registry.

    Args:
        system (System): The system to register.

    Returns:
        System: The registered system.

    Raises:
        SystemRegistrationError: If the name is already registered.

    """
    if system.name in _SYSTEM_REGISTRY:
        raise SystemRegistrationError(
            f"a system named '{system.name}' is already registered; names are "
            "the persistence and introspection key and must be unique"
        )
    if system.cadence.kind == _EVERY_TICK and system.scope.kind == _ALL_ENTITIES:
        logger.log_warn(
            f"System '{system.name}' combines every_tick with all_entities — "
            "a full-world sweep at 1 Hz. This is a reactor killer; use a "
            "coarser cadence or a bounded scope."
        )
    _SYSTEM_REGISTRY[system.name] = system
    return system


def register(name, cadence, scope, run, workload_class=_DEFAULT_WORKLOAD):
    """
    Build and register a `System` in one call.

    Args:
        name (str): Unique, stable system name.
        cadence (Cadence): From `every`, `calendar` or `every_tick`.
        scope (Scope): From `global_scope`, `online_puppets` or `all_entities`.
        run (callable): The body, called as `run(ctx)` once per fire.
        workload_class (str): One of `INTERACTIVE`, `SIMULATION` (default),
            `PERSISTENCE`, `MAINTENANCE`. Sets admission priority when a tick is
            over its `SYSTEM_TICK_MAX_ADMISSIONS` budget.

    Returns:
        System: The registered system.

    Raises:
        SystemRegistrationError: On invalid arguments or duplicate name.

    """
    return register_system(System(name, cadence, scope, run, workload_class=workload_class))


def get_system(name):
    """
    Args:
        name (str): A system name.

    Returns:
        System or None: The registered system, if any.

    """
    return _SYSTEM_REGISTRY.get(name)


def all_systems():
    """
    Returns:
        list: All registered `System` objects, in registration order. Each is
            introspectable via `.name`, `.cadence.describe()`,
            `.scope.describe()`, `.last_run` and `.fire_count`.

    """
    return list(_SYSTEM_REGISTRY.values())


def _clear_registry():
    """Empty the registry. Test support only; never call in production."""
    _SYSTEM_REGISTRY.clear()


# ---------------------------------------------------------------------------
# Last-run persistence (calendar cadence only; see module docstring)
# ---------------------------------------------------------------------------


def _load_last_run(name):
    """
    Load the durable last-run epoch for a calendar system.

    Args:
        name (str): The system name.

    Returns:
        float or None: Stored epoch seconds, or None if never stored.

    """
    from evennia.server.models import ServerConfig

    value = ServerConfig.objects.conf(_LAST_RUN_CONF_PREFIX + name, default=None)
    return float(value) if value is not None else None


def _store_last_run(name, when):
    """
    Persist the durable last-run epoch for a calendar system.

    Args:
        name (str): The system name.
        when (float): Epoch seconds to store.

    """
    from evennia.server.models import ServerConfig

    ServerConfig.objects.conf(_LAST_RUN_CONF_PREFIX + name, float(when))


# ---------------------------------------------------------------------------
# Entity selection
# ---------------------------------------------------------------------------


def _select_online_puppets():
    """
    Gather the distinct currently-puppeted objects, on the reactor.

    Returns:
        list: Live puppet objects, deduplicated, in session order.

    """
    import evennia

    seen = set()
    puppets = []
    for session in evennia.SESSION_HANDLER.values():
        # the focus-stack model replaced the old `session.puppet` attribute;
        # get_puppet() is the canonical accessor
        puppet = session.get_puppet()
        if puppet is None:
            continue
        pk = getattr(puppet, "id", None)
        if pk in seen:
            continue
        seen.add(pk)
        puppets.append(puppet)
    return puppets


def _query_entity_ids(component):
    """
    Worker-thread body: fetch matching entity pks as plain ints.

    Args:
        component (str or list): Typeclass path or list of paths.

    Returns:
        list: Matching pks.

    """
    from evennia.objects.models import ObjectDB

    paths = [component] if isinstance(component, str) else list(component)
    return list(ObjectDB.objects.filter(db_typeclass_path__in=paths).values_list("id", flat=True))


def _entity_ids_deferred(component):
    """
    Run the entity-id query off-reactor.

    Args:
        component (str or list): Typeclass path or list of paths.

    Returns:
        Deferred: Fires on the reactor with the list of pks.

    """
    from evennia.utils import defer as defer_utils

    return defer_utils.in_thread(_query_entity_ids, component)


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


class SystemDriver:
    """
    The 1 Hz `LoopingCall` that checks cadences and fires due systems.

    Always running once started; an empty registry costs one call and a
    length check per tick. Owned and started/stopped by
    `evennia.server.service` alongside the maintenance task.

    Args:
        now (callable, optional): Wall clock returning epoch seconds,
            injectable for testing. Defaults to `time.time`. Cadence logic
            never calls time itself.

    """

    def __init__(self, now=time.time):
        self._now = now
        self._loop = clock.make_looping(self.tick)
        # Live fire tasks, kept so shutdown can drain/cancel them before the
        # final durability barrier (a fire finishing after the last attribute
        # flush would re-dirty state that then never persists).
        self._inflight = set()

    def start(self):
        """Start ticking at `TICK_INTERVAL`. No-op if already running."""
        if self._loop.running:
            return
        self._loop.start(TICK_INTERVAL, now=False)

    def stop(self):
        """Stop ticking. Safe to call when not running.

        Stops *scheduling* new fires only; in-flight fires are not awaited. Use
        :meth:`quiesce` in the shutdown path to also drain them.
        """
        if self._loop.running:
            self._loop.stop()

    async def quiesce(self, timeout=5.0):
        """
        Stop scheduling and drain in-flight fires before a durability barrier.

        Stops the loop, then awaits currently-running system fires up to
        ``timeout`` seconds and cancels any that overrun. After this returns no
        system body is still mutating state, so a following attribute flush is
        the true final write.
        """
        self.stop()
        pending = [t for t in list(self._inflight) if hasattr(t, "cancel")]
        if not pending:
            return
        import asyncio

        try:
            await asyncio.wait(pending, timeout=max(0.0, timeout))
        except Exception:
            logger.log_trace("SystemDriver.quiesce: error awaiting in-flight fires")
        overran = [t for t in pending if not t.done()]
        for task in overran:
            task.cancel()
            logger.log_warn(
                "SystemDriver.quiesce: cancelled a system fire that overran the "
                "%.1fs drain window during shutdown" % timeout
            )
        if overran:
            # let the cancellations settle so the tasks are truly finished
            try:
                await asyncio.gather(*overran, return_exceptions=True)
            except Exception:
                logger.log_trace("SystemDriver.quiesce: error settling cancellations")

    def tick(self):
        """One driver tick: collect due systems, then fire them under a global
        admission budget in workload-class + oldest-overdue order.

        Firing order is no longer raw registry order: due systems are ranked by
        workload class (interactive first) then by how overdue they are, so
        latency-sensitive work wins and a deferred system ages until it
        outranks fresher work. When `settings.SYSTEM_TICK_MAX_ADMISSIONS` caps
        how many may start in one tick, the overflow is left due (not dropped)
        and admitted a later tick."""
        if not _SYSTEM_REGISTRY:
            return
        now = self._now()
        due = []
        for system in list(_SYSTEM_REGISTRY.values()):
            try:
                if self._check_due(system, now):
                    due.append(system)
            except Exception:
                # scheduling-side error (cadence bug or durable-store failure;
                # the latter retries next tick) — body errors are isolated via
                # the fire coroutine's handler instead
                logger.log_trace(f"System scheduler: error scheduling '{system.name}'")
        if not due:
            return

        def _overdue_age(system):
            # never-fired (last_run None) ranks as maximally overdue so it is
            # not starved by systems that have run recently
            return now - system.last_run if system.last_run is not None else float("inf")

        due.sort(
            key=lambda s: (_WORKLOAD_PRIORITY.get(s.workload_class, 99), -_overdue_age(s))
        )

        cap = getattr(settings, "SYSTEM_TICK_MAX_ADMISSIONS", None)
        admitted = due if not cap else due[: int(cap)]
        for system in admitted:
            try:
                self._fire(system, now)
            except Exception:
                logger.log_trace(f"System scheduler: error firing '{system.name}'")
        deferred = len(due) - len(admitted)
        if deferred:
            logger.log_info(
                "system scheduler: %d due system(s) deferred past the admission "
                "budget (%s); they age and admit on a later tick." % (deferred, cap)
            )

    def _check_due(self, system, now):
        """
        Decide whether `system` should fire at `now`. Not a pure predicate:
        priming a fresh system mutates `last_run`, and priming a brand-new
        calendar system also writes its durable last-run (ServerConfig).

        Args:
            system (System): The system to check.
            now (float): Current epoch seconds.

        Returns:
            bool: Whether the system is due.

        """
        cadence = system.cadence
        if cadence.kind == _EVERY_TICK:
            return True
        if cadence.kind == _EVERY:
            if system.last_run is None:
                # prime on first sight: first fire happens `seconds` from now
                system.last_run = now
                return False
            return now - system.last_run >= cadence.seconds
        # calendar: durable last-run, loaded lazily on first check after boot
        if not system._calendar_loaded:
            system._calendar_loaded = True
            stored = _load_last_run(system.name)
            if stored is None:
                # brand-new calendar system: prime durably, fire at the next
                # boundary rather than immediately on first deploy
                system.last_run = now
                _store_last_run(system.name, now)
                return False
            system.last_run = stored
        return _most_recent_occurrence(system.cadence, now) > system.last_run

    def _maybe_fire(self, system, now):
        """Check due + fire in one call (used by direct callers/tests)."""
        if not self._check_due(system, now):
            return
        self._fire(system, now)

    def _fire(self, system, now):
        """
        Fire `system` (already determined due) unless it is in flight.

        Args:
            system (System): The due system to fire.
            now (float): Current epoch seconds.

        """
        if system.in_flight:
            system.skip_count += 1
            message = (
                f"System '{system.name}' is due but its previous fire has not "
                f"completed; skipping this fire (runs never overlap; "
                f"consecutive skips: {system.skip_count}). If this recurs, "
                "the body is too slow for its cadence or its Deferred never "
                "fired."
            )
            if system.skip_count >= _SKIP_ESCALATION_THRESHOLD:
                # a wedged system (hung worker, never-firing Deferred) must
                # not hide as an endless trickle of warnings
                logger.log_err(message)
            else:
                logger.log_warn(message)
            return
        system.skip_count = 0
        # last_run is None here only on an every_tick first fire ( _check_due
        # primes it for every/calendar before they can come due)
        dt = now - system.last_run if system.last_run is not None else TICK_INTERVAL
        # persist before consuming state: if the durable store fails, the
        # exception reaches tick()'s handler with last_run/fire_count
        # untouched, so the boundary is retried next tick instead of being
        # recorded as a fire that never ran
        if system.cadence.kind == _CALENDAR:
            _store_last_run(system.name, now)
        system.last_run = now
        system.fire_count += 1
        system.in_flight = True

        async def _run_fire():
            try:
                await self._invoke_async(system, now, dt)
            except Exception:
                logger.log_trace(
                    f"System '{system.name}' errored on fire (run skipped, driver "
                    f"and other systems unaffected)."
                )
            finally:
                system.in_flight = False

        task = clock.run_coroutine(_run_fire())
        # Track the handle so shutdown can drain/cancel it; a synchronous
        # test-mode result has no add_done_callback and needs no tracking.
        if hasattr(task, "add_done_callback"):
            self._inflight.add(task)
            task.add_done_callback(self._inflight.discard)

    async def _invoke_async(self, system, now, dt):
        scope = system.scope
        if scope.kind == _ONLINE_PUPPETS:
            ctx = SystemContext(now=now, dt=dt, entities=_select_online_puppets())
            return await clock.maybe_await(system.run(ctx))
        if scope.kind == _ALL_ENTITIES:
            entity_ids = await _entity_ids_deferred(scope.component)
            ctx = SystemContext(now=now, dt=dt, entity_ids=entity_ids)
            return await clock.maybe_await(system.run(ctx))
        ctx = SystemContext(now=now, dt=dt)
        return await clock.maybe_await(system.run(ctx))


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def _load_one_module(path, require_registration):
    """
    Import one system module and run its `register_systems()`.

    Args:
        path (str): Dotted module path.
        require_registration (bool): Whether registering nothing is an error
            (True for game modules; engine modules may legitimately register
            nothing, e.g. a setting-disabled system).

    Raises:
        SystemRegistrationError: On import failure, missing
            `register_systems`, or (when required) an empty registration.

    """
    try:
        module = importlib.import_module(path)
    except Exception as err:
        raise SystemRegistrationError(f"SYSTEM_MODULES: could not import '{path}': {err}") from err
    register_fn = getattr(module, "register_systems", None)
    if not callable(register_fn):
        raise SystemRegistrationError(
            f"SYSTEM_MODULES: '{path}' defines no register_systems() callable"
        )
    count_before = len(_SYSTEM_REGISTRY)
    register_fn()
    if require_registration and len(_SYSTEM_REGISTRY) == count_before:
        raise SystemRegistrationError(
            f"SYSTEM_MODULES: '{path}'.register_systems() registered no systems"
        )


def load_system_modules():
    """
    Load all declared system modules: engine-owned first, then
    `settings.SYSTEM_MODULES`.

    Called at server start, before the driver starts. Idempotent: the
    registry is rebuilt from the declared modules on every call, so a repeat
    call (server init hooks may run more than once in one test process)
    replaces rather than duplicates. Durable calendar last-run state lives in
    ServerConfig and is unaffected by a rebuild. Any broken entry raises so a
    misdeclared module is a loud startup failure rather than a silently
    absent system.

    Raises:
        SystemRegistrationError: If any declared module is broken.

    """
    _SYSTEM_REGISTRY.clear()
    for path in _ENGINE_SYSTEM_MODULES:
        _load_one_module(path, require_registration=False)
    for path in getattr(settings, "SYSTEM_MODULES", None) or []:
        _load_one_module(str(path), require_registration=True)
