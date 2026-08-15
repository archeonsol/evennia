"""
Activities (CM1): first-class sustained, interruptible, re-validating processes.

A *state* (``state.py``) is a passive rule provider — it sits in the dispatch
context and gates/observes single actions. An **Activity** is the active dual: a
long-running, actor-owned process that *drives* dispatch over time. It
generalizes the bespoke ``ndb._walk_queue``/``delay()``/``_recheck`` machinery of
staggered walking, vehicle autopilot, and any future channel/cast/surgery-over-
time into one primitive.

The shape mirrors the engine's Phase 1g generator driver (``engine._drive_generator``),
lifted from "drive a suspended ``carry_out`` rule in the foreground" to "drive a
standalone background coroutine". An ``Activity.run()`` is a generator whose
``yield`` grammar is::

    yield <number>             # pause that many seconds (deferLater)
    yield engine.dispatch(...)  # run a sub-action; resume with its ActionTrace
    yield <Deferred>           # await any Deferred (e.g. an event-coordination
                               #   Deferred a @subscribe handler fires); resume
                               #   with its result
    return                     # complete (fires on_complete)

Re-validation falls out for free: an activity that wants to re-check a condition
before each step simply ``yield``\\s ``engine.dispatch(action, ...)`` again — the
same rules run, with no separate ``precheck``/``_recheck`` pair.

Activities are stored on the *holder* (character, else account) under
``holder.ndb.active_activities`` — non-persistent by design (they do not survive
a reload), exactly like states and like today's walk queue. ``exclusive_group``
gives mutual exclusion: starting a ``"locomotion"`` activity cancels any other in
that group (you cannot walk and autopilot at once). The module owns the same
three lifecycle verbs as ``state.py`` — start / cancel / query — so "is X
walking?" becomes a typed query (``is_active(holder, "locomotion")``) instead of
a scattered ``ndb`` flag.

The driver is an ``async`` coroutine (kicked off via ``clock.run_coroutine``);
it still awaits ``clock.defer_later`` sleeps and Deferred-returning bodies.
Cancellation cancels the in-flight ``Deferred`` and runs ``on_cancel``; the
driver guards a vanished actor/character and a crashing body.
"""

import asyncio
import inspect

from twisted.internet.defer import CancelledError, Deferred

from evennia.utils import clock

__all__ = [
    "Activity",
    "start_activity",
    "cancel_activity",
    "get_activities",
    "active_activity",
    "is_active",
]


class Activity:
    """Base for an actor-owned background process.

    Subclass it, set :attr:`key` (and optionally :attr:`exclusive_group`), and
    implement :meth:`run` as a generator following the module's ``yield`` grammar.
    Install an instance with :func:`start_activity`.

    Attributes:
        key (str): identity (e.g. ``"locomotion"``); :func:`active_activity`
            looks one up by it, and starting a new activity with the same key
            cancels the old one.
        exclusive_group (str | None): when set, starting this activity cancels any
            other active activity sharing the group (mutual exclusion).
        actor: the :class:`~evennia.actions.actor.Actor` (or any object the body
            needs to act through). The body uses it to build contexts / dispatch.
    """

    key = "activity"
    exclusive_group = None

    def __init__(self, actor=None):
        self.actor = actor
        self._cancelled = False
        self._cancel_reason = None
        self._pending = None  # in-flight Deferred, for cancellation
        self._holder = None  # set by start_activity

    # -- introspection ------------------------------------------------------
    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def running(self) -> bool:
        """True once started and not yet cancelled/finished (registered)."""
        return self._holder is not None and not self._cancelled

    # -- body + hooks (override) -------------------------------------------
    def run(self):
        """Override with a generator body (see the module ``yield`` grammar)."""
        raise NotImplementedError("Activity.run must be a generator")
        yield  # pragma: no cover  (marks this a generator even if not overridden)

    def on_cancel(self, reason=None):
        """Cleanup hook fired when the activity is cancelled. Override."""

    def on_complete(self):
        """Cleanup hook fired when ``run()`` returns normally. Override."""

    # -- control ------------------------------------------------------------
    def cancel(self, reason=None):
        """Cancel this activity. Idempotent; safe to call from inside ``run()``.

        Sets the cancelled flag, records ``reason`` (passed to :meth:`on_cancel`),
        and cancels any in-flight ``Deferred`` so a suspended body unwinds now.
        """
        if self._cancelled:
            return
        self._cancelled = True
        self._cancel_reason = reason
        pending = self._pending
        # ``pending`` may be an asyncio Task/Future (clock.defer_later) or a
        # Twisted Deferred; ``.cancel()`` on an already-finished one is a safe
        # no-op on both, so no ``.called``/``.done()`` pre-check is needed (and
        # a Task has no ``.called``).
        if pending is not None:
            try:
                pending.cancel()
            except Exception:  # noqa: BLE001 - cancellation must never raise
                pass


# --------------------------------------------------------------------------- #
# Holder-backed registry (mirrors state.py's active_states list)
# --------------------------------------------------------------------------- #


def _registry(holder, *, create=False):
    """Return the holder's live ``active_activities`` list (or ``None``)."""
    ndb = getattr(holder, "ndb", None)
    if ndb is None:
        if not create:
            return None
        raise TypeError(f"{holder!r} has no .ndb to hold activities")
    current = getattr(ndb, "active_activities", None)
    if current is None:
        if not create:
            return None
        current = []
        ndb.active_activities = current
    return current


def _unregister(activity):
    holder = activity._holder
    if holder is None:
        return
    reg = _registry(holder)
    if reg and activity in reg:
        reg.remove(activity)


def _safe(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - a hook must never break the scheduler
        from evennia.utils import logger

        logger.log_trace(f"activity hook {getattr(fn, '__name__', '?')!r} raised")


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #


def start_activity(holder, activity):
    """Install ``activity`` on ``holder`` and kick its driver.

    Enforces exclusivity first: any already-active activity sharing this one's
    :attr:`~Activity.key` (or its :attr:`~Activity.exclusive_group`, when set) is
    cancelled with reason ``"superseded"``. Then the new activity is registered
    and its body is driven synchronously up to the first real suspension (like
    ``inlineCallbacks`` was), so first-step side effects land before this returns;
    only the awaits/sleeps beyond that run on the loop.

    Returns:
        Activity: the started ``activity`` (for chaining / handle-keeping).
    """
    activity._holder = holder
    existing = _registry(holder) or []
    for other in list(existing):
        if other is activity:
            continue
        same_key = other.key == activity.key
        same_group = (
            activity.exclusive_group is not None
            and other.exclusive_group == activity.exclusive_group
        )
        if same_key or same_group:
            cancel_activity(holder, other, reason="superseded")

    reg = _registry(holder, create=True)
    if activity not in reg:
        reg.append(activity)

    # Run the synchronous prefix inline (up to the first real suspension), so any
    # first-step side effect (dispatch/msg before the first await) happens before
    # we return, matching the old inlineCallbacks contract. The prefix touches no
    # loop primitive, so deferring it would only reorder observable output.
    gen = activity.run()
    kind, value = _step_activity(activity, gen, None)
    if kind in ("done", "cancelled", "error"):
        _finish_activity(activity, gen, completed=(kind == "done"))
    else:
        clock.run_coroutine(_drive_activity(activity, gen, kind, value), task_kind="activity")
    return activity


def cancel_activity(holder, selector, reason=None):
    """Cancel matching activities on ``holder``.

    Args:
        selector: an :class:`Activity` instance (cancel exactly it), or a string
            matched against each activity's ``key`` *or* ``exclusive_group``.
        reason: passed to each cancelled activity's :meth:`Activity.on_cancel`.

    Returns:
        list: the activities that were cancelled.
    """
    reg = _registry(holder)
    if not reg:
        return []
    if isinstance(selector, Activity):
        targets = [a for a in reg if a is selector]
    else:
        targets = [a for a in reg if a.key == selector or a.exclusive_group == selector]
    for act in targets:
        act.cancel(reason)
    return targets


def get_activities(holder):
    """A copy of ``holder``'s active activities (start order); ``[]`` if none."""
    reg = _registry(holder)
    return list(reg) if reg else []


def active_activity(holder, key):
    """The active activity with ``key`` on ``holder``, or ``None``."""
    reg = _registry(holder)
    if not reg:
        return None
    for act in reg:
        if act.key == key:
            return act
    return None


def is_active(holder, key_or_group) -> bool:
    """True if ``holder`` has an active activity whose key or group matches."""
    reg = _registry(holder)
    if not reg:
        return False
    return any(act.key == key_or_group or act.exclusive_group == key_or_group for act in reg)


# --------------------------------------------------------------------------- #
# Driver (generalized from engine._drive_generator)
# --------------------------------------------------------------------------- #


def _step_activity(activity, gen, to_send):
    """Advance the generator to the next real suspension or its terminus.

    Runs the synchronous part of the body (``gen.send`` plus any inline
    "unknown yield value → resume with None" steps) without ever awaiting, and
    returns ``(kind, value)``:

    - ``("done", None)`` — the body returned (``StopIteration``).
    - ``("cancelled", None)`` — the activity was cancelled before a step.
    - ``("await", awaitable)`` — the body yielded an awaitable to await.
    - ``("sleep", seconds)`` — the body yielded a numeric delay to sleep on.
    - ``("error", None)`` — the body raised (already logged).

    The caller owns loop suspension, so the synchronous prefix can run inline in
    :func:`start_activity` and the async remainder in :func:`_drive_activity`
    off one classifier (no divergence between the two paths).
    """
    while True:
        if activity._cancelled:
            return ("cancelled", None)
        try:
            value = gen.send(to_send)
        except StopIteration:
            return ("done", None)
        except Exception:  # noqa: BLE001 - a crashing body must not kill the loop
            from evennia.utils import logger

            logger.log_trace(f"activity {activity.key!r} crashed")
            return ("error", None)
        to_send = None
        if inspect.isawaitable(value) and not isinstance(value, (int, float)):
            return ("await", value)
        if isinstance(value, (int, float)):
            return ("sleep", value)
        # unknown yield value — resume with None, no loop suspension


def _finish_activity(activity, gen, completed):
    """Close the generator, unregister, and fire the terminal hook once."""
    try:
        gen.close()
    except Exception:  # noqa: BLE001
        pass
    _unregister(activity)
    if activity._cancelled:
        _safe(activity.on_cancel, activity._cancel_reason)
    elif completed:
        _safe(activity.on_complete)


async def _drive_activity(activity, gen, kind, value):
    """Await an activity's suspensions and drive it to completion/cancellation.

    Entered only after :func:`start_activity` has run the synchronous prefix up
    to the first real suspension (``kind``/``value``). Fire-and-forget: it runs
    on the loop in the background, awaiting that suspension and each later one,
    running the synchronous steps between them inline via :func:`_step_activity`.
    A cancelled activity unwinds at the next suspension (or via its cancelled
    in-flight awaitable); body exceptions are logged, never propagated, and the
    terminal hook (``on_complete``/``on_cancel``) always fires once.
    """
    completed = False
    try:
        while True:
            try:
                if kind == "await":
                    activity._pending = value
                    to_send = await clock.maybe_await(value)
                else:  # "sleep"
                    activity._pending = clock.defer_later(max(0.0, float(value)))
                    await clock.maybe_await(activity._pending)
                    to_send = None
            except (CancelledError, asyncio.CancelledError):
                # asyncio.CancelledError (a BaseException) arrives when a
                # clock.defer_later Task is cancelled; twisted CancelledError
                # when a Deferred is. Both mean an orderly cancel, not a crash.
                break
            finally:
                activity._pending = None

            kind, value = _step_activity(activity, gen, to_send)
            if kind == "done":
                completed = True
                break
            if kind in ("cancelled", "error"):
                break
    except Exception:  # noqa: BLE001 - an awaited body raising must not kill the loop
        from evennia.utils import logger

        logger.log_trace(f"activity {activity.key!r} crashed")
    finally:
        _finish_activity(activity, gen, completed)
