"""
``RuleEngine`` — the four-phase dispatch core (CM1 Phase 1, incl. 1g).

Given a typed :class:`~evennia.actions.action.Action`, an actor, and an
:class:`~evennia.actions.context.ActionContext` (the ordered provider list),
:meth:`RuleEngine.dispatch` runs the four phases — ``before → check →
carry_out → report`` — collecting each provider's matching ``@rule`` methods,
gating them by their compiled ``requires`` predicate, firing them in priority
order, and recording every contribution into an
:class:`~evennia.actions.result.ActionTrace`.

Phase semantics (read off the returned :class:`RuleResult`'s ``kind``):

* **before** — side effects allowed. First ``REDIRECT`` restarts dispatch with
  the new action (guarded at ``REDIRECT_LIMIT``). First blocking result halts.
  **Strictly synchronous.**
* **check** — pure predicate phase. Short-circuits on the first blocking result
  and sends its message to the actor. ``CLAIM``/``REDIRECT`` here are a
  phase-control contract violation: logged and ignored. **Strictly synchronous.**
  *Skipped entirely* when the action carries ``_unresolved`` (a required target
  failed to resolve): the miss was already reported, and no gate can meaningfully
  evaluate against a ``None`` target — so gate rules never see one.
* **carry_out** — the work. ``CLAIM`` stops the phase; an exception in one rule
  is logged and does not stop the others (absent ``CLAIM``). **May suspend.**
* **report** — narration. Every rule fires; nothing short-circuits. **May
  suspend.**

The AS1 boundary (Phase 1g)
===========================

``before`` and ``check`` are synchronous: a rule body returns a ``RuleResult``
now, full stop — a body that tries to suspend (returns a generator or
``Deferred``) raises ``ActionError``.

``carry_out`` and ``report`` drive three body shapes:

1. **Plain rule** — returns a ``RuleResult`` (or ``None``) synchronously.
2. **Generator rule** — a ``@rule`` whose body uses ``yield`` (the ``@interactive``
   shape). It ``yield``\\s a prompt string (ask the player), a number (pause that
   many seconds), or a ``Deferred`` (await it); the value it eventually
   ``return``\\s is coerced to a ``RuleResult``. The engine drives it with
   :func:`_drive_generator`, built on the same ``get_input`` + ``deferLater``
   primitives ``evennia.utils.utils.interactive`` uses — but, unlike that
   fire-and-forget helper, the driver returns a ``Deferred`` that fires with the
   generator's return value so the phase can resume deterministically.
3. **Deferred-returning rule** — a rule that calls ``defer.in_thread(...)`` and
   returns the ``Deferred``; the engine awaits it and coerces the resolved value.

**Phase serialization across suspension (the CLAIM-ordering guarantee):** the
``carry_out`` / ``report`` loops are ``async`` coroutines that ``await``
on every rule. A suspended rule pauses the *whole* phase; the next rule does not
start until the suspended one resolves and its result is inspected. Two rules
never interleave, so ``CLAIM`` stays deterministic even across player input or a
thread-pool round-trip.

:meth:`dispatch` therefore returns a ``Deferred[ActionTrace]``. When no rule
suspends, that Deferred is already fired (``.called is True``) and dispatch is
effectively synchronous — the machinery cost is paid only when a rule defers.
"""

import asyncio
import inspect

from evennia.utils import clock

from .context import ActionContext
from .exceptions import ActionError
from .registry import rule_registry
from .result import FAIL, PASS, SKIP, ActionTrace, PhaseTrace, RuleResult

__all__ = ["RuleEngine", "engine"]

#: Maximum ``before``-phase redirects before a loop is declared.
REDIRECT_LIMIT = 5

#: Sentinel: a provider/rule that does not apply (actor_type mismatch). Distinct
#: from ``SKIP`` (a rule that applied but whose ``requires`` failed — recorded).
_NA = object()


# ---------------------------------------------------------------------------
# Generator-driving primitives (Phase 1g)
# ---------------------------------------------------------------------------
def _is_deferred(raw) -> bool:
    return inspect.isawaitable(raw)


def _sleep(seconds):
    """An awaitable that fires after ``seconds`` on the loop (patchable in tests)."""
    return clock.defer_later(seconds)


def _caller_for(actor):
    """Mirror :meth:`RuleEngine._caller` without requiring the class."""
    return (
        getattr(actor, "character", None)
        or getattr(actor, "account", None)
        or getattr(actor, "session", None)
        or actor
    )


def _get_input_future(actor, prompt):
    """Ask the actor for one line; completes the Future when input arrives."""
    from .menus import InputCaptureState

    loop = clock.get_bound_loop()
    fut = loop.create_future()
    caller = _caller_for(actor)
    if prompt:
        caller.msg(prompt)
    actor.enter_state(InputCaptureState(fut))
    return fut


async def _drive_generator(gen, actor):
    """Drive a rule generator to completion, returning its ``return`` value.

    Each ``yield`` from the body is interpreted:

    * ``str`` → a prompt; suspend on :func:`_get_input_future`, resume with the
      player's line (sent back into the generator).
    * :class:`~evennia.actions.menus.MenuPrompt` → render numbered options, capture
      input, resume with the chosen key (``None`` on quit).
    * ``int`` / ``float`` → a pause; suspend on :func:`_sleep`.
    * ``Deferred`` → await it; resume with its result.
    * anything else → ignored (resume with ``None``).

    Returns:
        Deferred: fires with the value the generator ``return``\\s (``None`` if it
        falls off the end).
    """
    from .menus import MenuPrompt, format_menu_prompt, parse_menu_choice

    caller = _caller_for(actor)
    to_send = None
    while True:
        try:
            value = gen.send(to_send)
        except StopIteration as stop:
            return getattr(stop, "value", None)
        to_send = None
        if _is_deferred(value):
            to_send = await value
        elif isinstance(value, MenuPrompt):
            while True:
                caller.msg(format_menu_prompt(value))
                raw = await _get_input_future(actor, "")
                choice = parse_menu_choice(raw, value)
                if choice == "__look__":
                    continue
                if choice == "__invalid__":
                    caller.msg("Invalid option. Try again.")
                    continue
                to_send = choice
                break
        elif isinstance(value, str):
            to_send = await _get_input_future(actor, value)
        elif isinstance(value, (int, float)):
            await _sleep(value)
        # else: unknown yield value — resume with None


class RuleEngine:
    """Stateless four-phase dispatcher. One shared instance (``engine``) is fine;
    all per-dispatch state lives in locals + the :class:`ActionTrace`."""

    # -- public API ---------------------------------------------------------
    async def dispatch(
        self,
        action,
        actor,
        context: ActionContext,
        dry_run: bool = False,
        record_phases: bool = True,
    ):
        """Run the four phases for ``action`` and fire with its :class:`ActionTrace`.

        Args:
            action (Action): The typed action to dispatch.
            actor: The acting object (carries ``character``/``effective``/``msg``).
            context (ActionContext): The ordered provider list.
            dry_run (bool): If True, ``requires`` predicates are still evaluated
                (recorded as SKIP/PASS) but no rule body fires — used by
                :meth:`explain`. No redirect, block, or suspension occurs.
            record_phases (bool): B3 lazy tracing. When False the engine skips
                building per-rule :class:`PhaseTrace` records (it still tracks a
                cheap ``fired`` counter, so ``outcome`` stays correct). The
                production bridge passes False; direct callers / ``explain`` keep
                the default True so the trace stays inspectable.

        Returns:
            Deferred[ActionTrace]: Fires with the full dispatch record. Already
                fired (synchronous) unless a ``carry_out``/``report`` rule
                suspended.

        Raises:
            ActionError: on a redirect loop, or a suspending ``before``/``check``
                body (those phases must be synchronous). Surfaced through the
                Deferred's errback.
        """
        action_type = type(action)
        redirects = 0

        # I1: pin focus + generation for the whole dispatch (multi-session race
        # guard). No-op for a legacy/unbound actor.
        snapshot = getattr(actor, "snapshot_focus", None)
        if snapshot is not None:
            snapshot()

        while True:
            trace = ActionTrace(action=action, actor_key=self._actor_key(actor))
            trace.redirect_count = redirects
            trace.record_phases = record_phases
            action._actor = actor
            action._trace = trace
            memo: dict = {}

            # B1: compile the provider list into a per-phase plan once (one walk
            # + sort), then run all four phases off it — no re-collect per phase.
            plan = self._build_plan(context, action_type)

            # --- before (synchronous) ------------------------------------
            signal, payload = self._phase_before(action, actor, plan, trace, memo, dry_run)
            if signal == "redirect":
                redirects += 1
                if redirects > REDIRECT_LIMIT:
                    raise ActionError(f"redirect loop after {REDIRECT_LIMIT} redirects")
                action = payload
                action_type = type(action)
                continue
            if signal == "blocked":
                trace.outcome = "blocked"
                trace.block_message = payload.message
                self._send(actor, payload.message, dry_run)
                return trace

            # --- check (synchronous) -------------------------------------
            # An action whose required target failed to resolve carries
            # ``_unresolved`` (the parser set it; ``search`` already reported the
            # miss). ``check`` is the gate phase — every check rule exists to
            # evaluate a predicate against the action's target/args — so running
            # it against a ``None`` target is both pointless (no gate can pass) and
            # the exact footgun the flag guards: a gate like
            # ``action.target.db.locked`` would raise ``AttributeError``. Skip it;
            # ``before`` already ran (state capture / interception), and the owning
            # ``carry_out`` rule swallows the unresolved action quietly.
            if not action._unresolved:
                blocking = self._phase_check(action, actor, plan, trace, memo, dry_run)
                if blocking is not None:
                    trace.outcome = "blocked"
                    trace.block_message = blocking.message
                    self._send(actor, blocking.message, dry_run)
                    return trace

            # --- carry_out + report (may suspend) ------------------------
            # In dry-run, ``_eval_rule_async`` records each rule (PASS/SKIP from
            # its ``requires`` gate) without firing the body, so the trace still
            # reflects all four phases for ``explain()``.
            aborted = await self._run_phase(action, actor, plan, trace, memo, "carry_out", dry_run)
            if aborted:
                # The focus body this dispatch acted for was popped/collapsed by
                # another session while a carry_out rule was suspended. Stop —
                # don't narrate (report) work that no longer has a valid body.
                trace.outcome = "aborted"
                return trace
            await self._run_phase(action, actor, plan, trace, memo, "report", dry_run)

            trace.outcome = self._final_outcome(trace)
            return trace

    def explain(self, action, actor, context: ActionContext):
        """Dry-run dispatch: evaluate every rule's ``requires`` but fire no body.
        Returns a ``Deferred[ActionTrace]`` (already fired — dry-run never
        suspends) for help / UI / a ``@rules`` command."""
        return self.dispatch(action, actor, context, dry_run=True)

    def emit(self, event):
        """Fire ``event`` to every ``@subscribe`` handler in its provider scope.

        The scope is the event's own (``event.providers()``) — collected fresh,
        never from a persistent registry, so a handler can never fire on a stale
        subscription. Handlers across the scope are gathered, deduped by their
        provider object, globally priority-sorted (provider order breaks ties),
        and fired as ``handler(event)``. Handlers are report-like: side effects
        allowed, no short-circuit, return value ignored; a buggy one is logged and
        does not stop the rest. A handler that needs to run over time starts its
        own :class:`~evennia.actions.process.Activity`.

        Returns:
            int: how many handlers fired (handy for tests / instrumentation).
        """
        from .events import event_registry

        event_type = type(event)
        pairs = []
        seen = set()
        for provider in event.providers():
            if provider is None or id(provider) in seen:
                continue
            seen.add(id(provider))
            for spec in event_registry.handlers_for(type(provider), event_type):
                pairs.append((provider, spec))
        if len(pairs) > 1:
            pairs.sort(key=lambda ps: -ps[1].priority)
        fired = 0
        for provider, spec in pairs:
            try:
                getattr(provider, spec.handler_name)(event)
                fired += 1
            except Exception:  # noqa: BLE001 - a buggy handler must not break emit
                from evennia.utils import logger

                logger.log_trace(
                    f"event handler {spec.handler_name!r} on "
                    f"{type(provider).__name__} raised for {event_type.__name__}"
                )
        return fired

    # -- synchronous phases (before / check) --------------------------------
    def _phase_before(self, action, actor, plan, trace, memo, dry_run):
        for provider, spec in plan["before"]:
            result = self._eval_sync(provider, spec, action, actor, memo, trace, "before", dry_run)
            if result is _NA or result.is_skip:
                continue
            if result.is_redirect:
                return ("redirect", result.redirect_to)
            if result.blocks:
                return ("blocked", result)
        return ("continue", None)

    def _phase_check(self, action, actor, plan, trace, memo, dry_run):
        for provider, spec in plan["check"]:
            result = self._eval_sync(provider, spec, action, actor, memo, trace, "check", dry_run)
            if result is _NA or result.is_skip:
                continue
            if result.is_claim or result.is_redirect:
                self._warn_check_violation(provider, spec, result)
                continue
            if result.blocks:
                return result
        return None

    def _eval_sync(self, provider, spec, action, actor, memo, trace, phase, dry_run):
        """Gate + fire one synchronous (``before``/``check``) rule. Records a
        :class:`PhaseTrace`; returns the result, ``_NA``, or ``SKIP``."""
        if not self._actor_type_ok(spec, actor):
            return _NA
        if spec.requires is not None and not spec.requires.eval(action, actor, memo):
            self._record(trace, phase, provider, spec, SKIP)
            return SKIP
        if dry_run and phase != "check":
            self._record(trace, phase, provider, spec, PASS)
            return PASS
        raw = getattr(provider, spec.rule_name)(action, actor)
        result = self._coerce_sync(raw, phase)
        self._record(trace, phase, provider, spec, result)
        return result

    @staticmethod
    def _coerce_sync(raw, phase) -> RuleResult:
        """Coerce a synchronous-phase body return. A generator/Deferred here is a
        contract violation — ``before``/``check`` must not suspend."""
        if isinstance(raw, RuleResult):
            return raw
        if raw is None:
            return PASS
        if inspect.isgenerator(raw) or _is_deferred(raw):
            raise ActionError(
                f"{phase} rules must be synchronous, but a "
                f"{type(raw).__name__} (generator/Deferred) was returned"
            )
        return PASS

    # -- suspendable phases (carry_out / report) ----------------------------
    async def _run_phase(self, action, actor, plan, trace, memo, phase, dry_run):
        """Drive one suspendable phase. ``carry_out`` stops on ``CLAIM``; both
        phases serialize across suspension (the next rule waits for this one).

        Returns ``True`` if the phase was aborted because the actor's pinned
        focus was invalidated mid-suspend (I1 race guard), else ``False``. The
        guard is only consulted after a rule that actually *suspended* — a
        synchronous rule cannot yield the reactor to another session, so no
        DB re-read is paid on the fast path."""
        stop_on_claim = phase == "carry_out"
        guard = getattr(actor, "focus_still_valid", None)
        for provider, spec in plan[phase]:
            result, suspended = await self._eval_rule_async(
                provider, spec, action, actor, memo, trace, phase, dry_run
            )
            if suspended and guard is not None and not guard():
                return True
            if result is _NA or result.is_skip:
                continue
            if stop_on_claim and result.is_claim:
                break
        return False

    async def _eval_rule_async(self, provider, spec, action, actor, memo, trace, phase, dry_run):
        """Gate, fire, and (if it suspends) drive one ``carry_out``/``report``
        rule, coercing its eventual return to a :class:`RuleResult`.

        A buggy rule that raises is logged and recorded as a failure but does not
        stop the phase (it returns ``PASS`` — "fired, did not claim").

        Returns ``(result, suspended)``: ``suspended`` is ``True`` when the body
        was a generator/Deferred (i.e. it could have yielded the reactor to
        another session), so the caller knows whether to consult the focus
        guard.
        """
        if not self._actor_type_ok(spec, actor):
            return _NA, False
        if spec.requires is not None and not spec.requires.eval(action, actor, memo):
            if phase == "carry_out":
                trace.carry_out_gated += 1
            self._record(trace, phase, provider, spec, SKIP)
            return SKIP, False
        if dry_run:
            self._record(trace, phase, provider, spec, PASS)
            return PASS, False
        suspended = False
        try:
            raw = self._fire(provider, spec, action, actor)
            if inspect.isgenerator(raw):
                suspended = True
                final = await _drive_generator(raw, actor)
                result = self._coerce_final(final)
            elif _is_deferred(raw):
                suspended = True
                final = await raw
                result = self._coerce_final(final)
            else:
                result = self._coerce_final(raw)
        except Exception as exc:  # noqa: BLE001 - a buggy rule must not kill the phase
            from evennia.utils import logger

            logger.log_trace(f"action rule {spec.rule_name!r} raised during {phase} phase")
            self._record(trace, phase, provider, spec, FAIL(f"exception: {exc}"))
            self._notify_rule_error(actor)
            return PASS, suspended
        self._record(trace, phase, provider, spec, result)
        return result, suspended

    @staticmethod
    def _fire(provider, spec, action, actor):
        """Call the rule body. The single firing seam — its raw return (a
        ``RuleResult``, ``None``, generator, or ``Deferred``) is interpreted by
        the caller."""
        return getattr(provider, spec.rule_name)(action, actor)

    @staticmethod
    def _coerce_final(value) -> RuleResult:
        """Coerce a fully-resolved ``carry_out``/``report`` value to a
        ``RuleResult``: a ``RuleResult`` passes through; ``None`` / anything else
        becomes ``PASS`` ("fired, did not claim")."""
        if isinstance(value, RuleResult):
            return value
        return PASS

    # -- collection ---------------------------------------------------------
    _PLAN_PHASES = ("before", "check", "carry_out", "report")

    @classmethod
    def _build_plan(cls, context, action_type):
        """B1: walk the provider list *once* and compile a per-phase dispatch
        plan ``{phase: [(provider, spec), ...]}`` for ``action_type``, each
        bucket globally sorted by priority desc (provider list order — states
        first — breaks ties).

        Built once per dispatch iteration in :meth:`dispatch` and consumed by all
        four phases, replacing a per-phase re-walk + re-sort of the provider list.
        """
        plan = {phase: [] for phase in cls._PLAN_PHASES}
        rules_for = rule_registry.rules_for
        for provider in context.providers:
            if provider is None:
                continue
            ptype = type(provider)
            for phase, bucket in plan.items():
                for spec in rules_for(ptype, action_type, phase):
                    bucket.append((provider, spec))
        for bucket in plan.values():
            if len(bucket) > 1:
                bucket.sort(key=lambda ps: -ps[1].priority)
        return plan

    # -- helpers ------------------------------------------------------------
    @staticmethod
    def _actor_type_ok(spec, actor) -> bool:
        if spec.actor_type is None:
            return True
        candidates = (
            actor,
            getattr(actor, "account", None),
            getattr(actor, "character", None),
            getattr(actor, "effective", None),
        )
        return any(isinstance(c, spec.actor_type) for c in candidates if c is not None)

    @staticmethod
    def _caller(actor):
        """The object that receives prompts for an interactive rule."""
        return _caller_for(actor)

    @staticmethod
    def _record(trace, phase, provider, spec, result):
        # B3: a non-skip rule "fired" — track it cheaply so ``_final_outcome``
        # is correct even when phase recording is off. The per-phase slices
        # feed the dispatch bridge's fail-closed feedback.
        if not result.is_skip:
            trace.fired += 1
            if phase == "carry_out":
                trace.carry_out_fired += 1
            elif phase == "report":
                trace.report_fired += 1
        if not trace.record_phases:
            return
        trace.record(
            PhaseTrace(
                phase=phase,
                provider_key=getattr(provider, "key", type(provider).__name__),
                provider_dbref=getattr(provider, "id", None),
                rule_name=spec.rule_name,
                result=result,
            )
        )

    @staticmethod
    def _final_outcome(trace) -> str:
        return "succeeded" if trace.fired else "no_rules"

    @staticmethod
    def _actor_key(actor) -> str:
        for obj in (
            getattr(actor, "effective", None),
            getattr(actor, "character", None),
            actor,
        ):
            key = getattr(obj, "key", None)
            if key:
                return key
        return repr(actor)

    @classmethod
    def _notify_rule_error(cls, actor):
        """Player-facing notice for a rule body that raised. The traceback is
        already logged; this keeps the failure from being silent at the prompt.
        ``IN_GAME_ERRORS`` includes the traceback, mirroring the legacy command
        path's ``_msg_err``."""
        from traceback import format_exc

        from django.conf import settings

        if getattr(settings, "IN_GAME_ERRORS", False):
            text = f"{format_exc().strip()}\nAn untrapped error occurred."
        else:
            text = (
                "An untrapped error occurred. Please file a bug report "
                "detailing the steps to reproduce."
            )
        cls._send(actor, text, False)

    @staticmethod
    def _send(actor, message, dry_run):
        if dry_run or not message:
            return
        for target in (
            actor,
            getattr(actor, "character", None),
            getattr(actor, "effective", None),
        ):
            msg = getattr(target, "msg", None)
            if callable(msg):
                msg(message)
                return

    @staticmethod
    def _warn_check_violation(provider, spec, result):
        from evennia.utils import logger

        logger.log_warn(
            f"check rule {spec.rule_name!r} on {type(provider).__name__} returned "
            f"{result.kind!r}; the check phase must be a pure predicate "
            f"(no CLAIM/REDIRECT/side effects). Result ignored."
        )


#: Shared engine instance.
engine = RuleEngine()
