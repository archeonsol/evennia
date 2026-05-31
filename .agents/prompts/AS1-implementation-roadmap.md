# AS1 Implementation Roadmap: sync-by-default + threaded I/O helpers

Status: design / pre-implementation. Branch: `as1-sync-async`.

Replaces the open-ended `AS1-sync-async-commitment.md` exploration with a
decided direction and an implementation plan. The prompt remains the
upstream task definition; this is the committed design.

---

## Decision

**Option 1: sync-by-default, with a small set of blessed helpers for
running blocking I/O off the reactor thread.** Not option 2 (async
first-class). Not a hybrid.

No new dependencies. The reactor thread-pool and `deferToThread` already
exist under Twisted; this is a thin, safe, documented surface over them
plus a stall watchdog, not new machinery.

---

## Why option 1 (grounded in the actual code)

The game is overwhelmingly synchronous, and the demonstrated need is
narrow and specific: a blessed, ergonomic way to run a blocking call off
the reactor thread. The evidence is already in the tree:

- Blocking `requests` calls sit in at least: `world/scheduler.py`,
  `world/engine_jobs.py`, `world/staff_pending.py`,
  `world/community/mentor.py`, `world/community/events.py`,
  `commands/bug_report.py`. Each of these stalls the reactor for the
  duration of the HTTP round-trip, freezing every connected player.
- A hand-rolled `deferToThread` wrapper already exists in
  `world/utils.py` (~line 114). That is the "every serious game writes
  this helper" signal: the engine should own it.

Option 2 (async first-class) would mean `async`/`await` or
`inlineCallbacks` threaded through the 95% of code that does zero I/O, for
no benefit to that code. The cost is enormous and the payoff is confined
to the handful of I/O sites above. Not justified.

---

## The boundary of what this solves (read this before designing)

Option 1 makes one pattern ergonomic and safe: **do blocking work, then
deliver a result or side effect later.** That covers commands, scripts,
periodic jobs, outbound webhooks, and fire-and-forget notifications. It is
the right tool for every blocking site listed above.

It does **not** make blocking I/O safe inside a hook that must return a
value synchronously to its caller (e.g. `at_pre_move` returning a bool to
veto a move, a lock function, a parser that must produce a result now).
You cannot offload those without changing the caller's contract. For those
the answer is: do not do blocking I/O there. Precompute, cache, or
restructure so the I/O happens in a deferrable context.

This boundary directly informs CM1 (the action-system work): `carry_out`
and `report` rules are deferrable like commands and can use these helpers;
`check` rules need a synchronous result and therefore must not block. AS1
settling first is what lets CM1 build its rule-body contract on a decided
direction instead of retrofitting one.

---

## The threading-safety contract (the core correctness rule)

Twisted runs the deferred function in a worker thread and runs its
callbacks/errbacks back on the reactor thread. The hazard is the worker
function touching game state.

**In the worker thread (the function passed to `in_thread`):**
- Allowed: stdlib and network I/O, pure computation. Return plain data
  (str / bytes / dict / list of primitives).
- Forbidden: any game-object access. No `.db` / `.ndb` / typeclass
  attributes, no `obj.msg(...)`, no typeclass-manager queries, no mutation
  of shared game state, no touching idmapper-cached instances.

**In the callback (runs on the reactor thread):** all game-object
interaction happens here, with the plain data returned by the worker.

Rationale: Django's ORM is per-thread-connection safe, but Evennia's
typeclass + idmapper layer is not designed for concurrent access. Treat
**all** game-object access as reactor-thread-only. The helpers and docs
must make the unsafe path hard and the safe path obvious.

---

## Helper API (engine: `evennia/utils/defer.py`)

Minimal surface. Three entry points; the common "deliver result to a
player" flow is a documented usage pattern, not a fourth function.

```python
def in_thread(fn, *args, **kwargs) -> Deferred:
    """
    Run a blocking, game-state-free callable in the reactor thread pool.
    Returns a Deferred whose callbacks run on the reactor thread.

    The callable must obey the threading-safety contract: pure I/O,
    returns plain data, touches no game objects.
    """

def background(fn, *args, on_error=None, **kwargs) -> None:
    """
    Fire-and-forget in_thread. No result is awaited. Exceptions are
    NOT swallowed: on_error(failure) is called on the reactor thread if
    given, otherwise the failure is logged via the engine logger.
    """

def threaded(fn):
    """
    Decorator. Marks a function as always-threaded: calling it returns a
    Deferred run via in_thread. Sugar for functions that are always I/O.
    """
```

Documented usage pattern for the common case (deliver result to a target,
handle errors), which is what every current blocking site actually wants:

```python
from evennia.utils import defer

def _fetch():                      # worker thread: pure I/O, no game objects
    return requests.get(url, timeout=10).json()

def _deliver(data):                # reactor thread: safe to touch game objects
    caller.msg(f"Result: {data['field']}")

def _failed(failure):              # reactor thread
    caller.msg("The request failed.")
    return None                    # handled; don't re-raise

defer.in_thread(_fetch).addCallbacks(_deliver, _failed)
```

`background` covers the no-result case (outbound webhook, notification):

```python
defer.background(_post_webhook, payload)   # logs on failure, never blocks
```

Note the `on_error`/default-log behavior is deliberate: it satisfies the
"no silent/suppressed errors" rule. A `background` call that fails will
surface in the log rather than vanish.

---

## Reactor-stall watchdog (recommended; decision below)

A cheap watchdog that logs when a single reactor turn exceeds a threshold.
This is the instrument that *finds* the blocking hooks worth migrating;
without it, offenders are invisible until a player reports a freeze.

- Setting: `REACTOR_STALL_WARNING_MS` (default e.g. 200; `0` disables).
- Mechanism: a low-overhead periodic check that measures gaps between
  reactor iterations and logs a warning (with the best available stack
  hint) when one exceeds the threshold.
- Off the hot path by design: it observes, it does not wrap every call.

Recommended to include in v1 because it is small and high-signal, and it
turns "migrate blocking I/O" from guesswork into a worklist. Marked as a
decision below in case you would rather ship the helpers first and add the
watchdog as a fast-follow.

---

## Existing `inlineCallbacks` / `deferToThread` usage

Recommendation: **keep working, discourage in docs, migrate
opportunistically.** Do not hard-deprecate.

- Existing `inlineCallbacks` commands keep working unchanged.
- The hand-rolled `world/utils.py` deferToThread wrapper is retired in
  favor of the engine helper as part of the downstream migration (Phase
  2), so there is one blessed path.
- New code uses `defer.in_thread` / `background`. Docs steer there.

---

## Documentation

- Engine: a new short doc, "Doing I/O without blocking the reactor,"
  covering the threading-safety contract, the three helpers, the
  deliver-result pattern, and the hook-return boundary.
- Downstream (`docs/performance.md`): a section pointing at the engine
  helper as the one correct way to do outbound I/O, with the worked
  migration of one real site as the example.

---

## Phases

**Phase 0 (engine).** `evennia/utils/defer.py` with `in_thread`,
`background`, `threaded`, plus tests. No game-facing changes. Bump
`EVENNIA_REF` when downstream will import it.

**Phase 1 (engine; see decision).** Reactor-stall watchdog +
`REACTOR_STALL_WARNING_MS` setting + test.

**Phase 2 (downstream / game).** Migrate the blocking sites
(`scheduler`, `engine_jobs`, `staff_pending`, `community/mentor`,
`community/events`, `bug_report`) to `background` / `in_thread`. Retire
the `world/utils.py` wrapper. Each migrated site is a worked example.

**Phase 3 (docs).** Engine doc + `docs/performance.md` section.

---

## Open decisions to confirm

1. **Helper home.** `evennia/utils/defer.py` in the engine (recommended,
   since AS1 is an engine-fork item and the wrapper should be owned
   centrally). Confirm.
2. **Stall watchdog in v1** (recommended) or fast-follow.
3. **`inlineCallbacks` stance.** Keep + discourage + opportunistic-migrate
   (recommended), or actively deprecate.

---

## Testing

- `in_thread`: returns a Deferred; the worker function runs off the
  reactor thread (assert a different thread identity); the callback runs
  on the reactor thread.
- `background`: a worker exception is logged (or routed to `on_error`) and
  does not propagate to the caller.
- `threaded`: decorated call returns a Deferred and runs in a thread.
- Watchdog (if in v1): a deliberate over-threshold reactor block produces
  exactly one warning log line.

---

## Coordination

- **CM1:** AS1's decision (sync + helpers, with the hook-return boundary)
  is the contract CM1's rule bodies build on. `carry_out`/`report` may
  defer; `check` may not block. Settling AS1 first is the point.
- **Engine pin:** Phase 0 adds a new importable symbol
  (`evennia.utils.defer`); bump `EVENNIA_REF` to the tag containing it
  before any game code imports it.
- **No package-manager changes:** Twisted is already a dependency; this
  adds no new ones. If the watchdog ever wants a third-party profiler,
  raise it separately rather than bundling a dependency bump here.
