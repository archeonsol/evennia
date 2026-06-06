"""
``Actor`` (CM1 Phase 3a): the acting entity for one dispatch.

An :class:`Actor` bundles the (session, account, character) triple a command
used to reach piecemeal through ``caller``/``session``. It is the single thing
the parser and engine ask "who is doing this, where are they, what's their
state?". It is a thin, computed view — it owns no game data, it just navigates
the objects it wraps.

Key derived views:

* :attr:`effective` — the object that *acts*: the puppeted character if there is
  one, else the account (OOC). Capability checks and ``search`` go through it.
* :attr:`location` — the character's room (``None`` when OOC / unpuppeted).
* :attr:`state_objects` — the actor's active :class:`StateProvider` instances,
  which lead the dispatch provider list.
* :attr:`equipped_items` — items whose rules should intercept (worn/wielded).

The state lifecycle methods (:meth:`enter_state` / :meth:`exit_state` /
:meth:`has_state`) delegate to :mod:`evennia.actions.state`, operating on the
character when puppeted, else the account — so a state set while OOC and one set
IC live on the appropriate holder.
"""

from dataclasses import dataclass

from . import process as _process
from . import state as _state
from .events import Event

__all__ = ["Actor", "FocusChanged"]


@dataclass
class FocusChanged(Event):
    """A focus-stack level was pushed or popped on an actor's binding.

    Emitted one per level so an ordered teardown (a ``collapse``) fires a
    ``"pop"`` for each body top-first — the @subscribe replacement for the old
    ``at_pre_puppet``/``at_post_unpuppet`` hooks.

    Attributes:
        actor: the :class:`Actor` whose focus changed.
        body: the body pushed onto / popped off the stack at this level.
        change (str): ``"push"`` or ``"pop"``.
        focus: the resulting top focus after the change (the new active body).
    """

    actor: object = None
    body: object = None
    change: str = ""
    focus: object = None

    def providers(self):
        objs = [self.body]
        if self.actor is not None:
            objs.append(self.actor.identity)
            objs.append(self.actor.focus)
        return objs


@dataclass
class Actor:
    """The acting entity: a control-graph view.

    An actor built without a :attr:`binding` falls back to the legacy
    ``(session, account, character)`` triple, with every derived view below
    identical to the old puppet behavior (the path for bindingless / programmatic
    actors). When a :class:`~evennia.accounts.models.ControlBinding` is attached
    — the normal player-dispatch case since ``.71`` — identity/focus/effective
    resolve from the durable focus stack instead.

    CM1 I1 closeout: this is the substrate that retires piecemeal
    ``self.caller``. Legacy ``self.caller`` consumers migrate onto ``Actor`` as
    their command modules port to native actions (the builder / command-group
    migration), not pre-emptively — so the remaining ``self.caller`` uses are
    expected to disappear with those modules. New engine code should take an
    ``Actor``.

    Attributes:
        session: the originating ``ServerSession`` (or ``None`` for system /
            scripted actions).
        account: the controlling ``DefaultAccount`` (or ``None``).
        character: the legacy puppeted ``DefaultCharacter`` (``None`` when OOC).
        binding: the :class:`ControlBinding` backing this actor's focus stack,
            or ``None`` (legacy puppet path).
        _focus_snapshot: the body resolved at dispatch entry — pins the active
            focus across a suspended ``carry_out`` even if another session mutates
            the stack (the multi-session race guard; set in Task 4).
        _gen_at_start: the binding generation captured at dispatch entry, used to
            detect a concurrent push/pop on resume (Task 4).
    """

    session: object = None
    account: object = None
    character: object = None
    binding: object = None
    _focus_snapshot: object = None
    _gen_at_start: object = None

    # -- derived views -------------------------------------------------------
    @property
    def identity(self):
        """The persistent IC self (never swaps): the binding's identity when
        bound, else the legacy character."""
        if self.binding is not None:
            return self.binding.db_identity
        return self.character

    @property
    def focus(self):
        """The active body: the dispatch-entry snapshot if pinned, else the
        binding's live top focus, else (legacy) the character or account."""
        if self._focus_snapshot is not None:
            return self._focus_snapshot
        if self.binding is not None:
            return self.binding.focus
        return self.character or self.account or self.session

    @property
    def effective(self):
        """The object that acts: the current focus (character, account, or
        session on the connect screen when neither is logged in yet)."""
        return self.focus

    @property
    def location(self):
        """The focus body's room, or ``None`` (OOC / account focus)."""
        body = self.focus
        return getattr(body, "location", None) if body is not None else None

    @property
    def holder(self):
        """The object that stores this actor's states: the current focus."""
        return self.focus

    @property
    def state_objects(self):
        """Active :class:`StateProvider` instances, newest last; ``[]`` if none."""
        holder = self.holder
        return _state.get_states(holder) if holder is not None else []

    @property
    def equipped_items(self):
        """Items whose rules intercept this actor's actions (worn / wielded)."""
        body = self.focus
        if body is None:
            return []
        ndb = getattr(body, "ndb", None)
        return list(getattr(ndb, "equipped_for_rules", None) or []) if ndb else []

    # -- convenience ---------------------------------------------------------
    def search(self, name, *args, **kwargs):
        """Resolve ``name`` to a world object via the effective object.

        Honors a one-shot disambiguation override first: when a prior
        :class:`AmbiguousTarget` was resolved by the player, the chosen object is
        stashed on ``holder.ndb._disambig_overrides`` keyed by the searched name,
        so replaying the original command returns that choice instead of raising
        again. The override is consumed on use.

        Otherwise it bridges the game's search to the engine's disambiguation
        contract. Stock Evennia ``.search()`` handles a multi-match by *printing*
        the "1-ball / 2-ball?" prompt and returning ``None`` — it never raises. So
        we first probe the typed primitive ``effective.search_for`` (which never
        messages, and which runs the *same* sdesc/recog matching + game search
        filters the legacy command path uses) purely to detect ambiguity and
        raise :class:`AmbiguousTarget` for the dispatch loop to catch. A single or
        no match then falls through to the sugar ``effective.search`` so the
        game's own filters and the stock not-found prompt fire exactly as on the
        legacy path — nothing about object resolution is reimplemented here.

        Visibility (e.g. stealth) is applied by the engine's perception seam
        (:mod:`evennia.actions.perception`): the registered predicate filters
        both the ambiguity candidate set (so hidden objects never enter a
        disambiguation prompt) and the resolved result (so a hidden single match
        resolves to "not found"). This is the substrate-level chokepoint that
        lets a game retire its own ``.search`` visibility override without
        regressing typed-action targeting; until then the two simply agree (the
        filter is idempotent over an already-filtered result).
        """
        override = self._take_search_override(name)
        if override is not None:
            return override
        from . import perception

        eff = self.effective
        search_for = getattr(eff, "search_for", None)
        if callable(search_for):
            from evennia.objects.search_result import Ambiguous

            probe = search_for(name, *args, **kwargs)
            if isinstance(probe, Ambiguous):
                from .exceptions import AmbiguousTarget

                visible = perception.filter_visible(eff, probe.candidates)
                if len(visible) > 1:
                    raise AmbiguousTarget(visible, name)
                if len(visible) == 1:
                    return visible[0]
                # Every candidate is hidden: fall through to the sugar so the
                # game emits its standard not-found handling, not a prompt
                # listing objects the searcher cannot perceive.
        result = eff.search(name, *args, **kwargs)
        if isinstance(result, (list, tuple)):
            return perception.filter_visible(eff, result)
        return result if perception.is_visible(eff, result) else None

    def _take_search_override(self, name):
        holder = self.holder
        ndb = getattr(holder, "ndb", None) if holder is not None else None
        overrides = getattr(ndb, "_disambig_overrides", None) if ndb else None
        if not overrides or name not in overrides:
            return None
        return overrides.pop(name)

    def set_search_override(self, name, obj):
        """Stash ``obj`` as the one-shot resolution for a future ``search(name)``
        (used by the disambiguation bridge after the player picks a candidate)."""
        holder = self.holder
        if holder is None:
            return
        overrides = getattr(holder.ndb, "_disambig_overrides", None)
        if overrides is None:
            overrides = {}
            holder.ndb._disambig_overrides = overrides
        overrides[name] = obj

    def msg(self, *args, **kwargs):
        """Send text to the actor (effective object's ``msg``)."""
        return self.effective.msg(*args, **kwargs)

    # -- state lifecycle (delegates to evennia.actions.state) ---------------
    def enter_state(self, state):
        """Install ``state`` on this actor's holder."""
        holder = self.holder
        if holder is None:
            raise TypeError("Actor has no holder for state (no character, account, or session)")
        return _state.enter_state(holder, state)

    def exit_state(self, state_type):
        """Remove every ``state_type`` state from this actor's holder."""
        return _state.exit_state(self.holder, state_type)

    def has_state(self, state_type) -> bool:
        """True if this actor currently carries a ``state_type`` state."""
        return _state.has_state(self.holder, state_type)

    # -- activity lifecycle (delegates to evennia.actions.process) ----------
    def start_activity(self, activity):
        """Install and kick ``activity`` on this actor's holder.

        The activity's ``actor`` is set to this :class:`Actor` if it has none, so
        a body built without one (``start_activity(Locomotion(route=...))``) can
        still dispatch through the acting entity.
        """
        if getattr(activity, "actor", None) is None:
            activity.actor = self
        return _process.start_activity(self.holder, activity)

    def cancel_activity(self, selector, reason=None):
        """Cancel matching activities (by instance, key, or group) on the holder."""
        return _process.cancel_activity(self.holder, selector, reason=reason)

    def activities(self):
        """A copy of this actor's active activities; ``[]`` if none."""
        return _process.get_activities(self.holder)

    def active_activity(self, key):
        """The active activity with ``key`` on this actor's holder, or ``None``."""
        return _process.active_activity(self.holder, key)

    def is_active(self, key_or_group) -> bool:
        """True if this actor has an active activity matching key or group."""
        return _process.is_active(self.holder, key_or_group)

    # -- focus lifecycle (delegates to ControlBinding) ----------------------
    def push_focus(self, body):
        """Push ``body`` as the new top focus; emit ``FocusChanged(push)``.

        No-op (returns ``None``) on a legacy actor with no :attr:`binding`."""
        if self.binding is None:
            return None
        self.binding.push(body)
        self._emit_focus(body, "push")
        return body

    def pop_focus(self):
        """Pop the top focus (never below the account floor); emit
        ``FocusChanged(pop)`` for the dropped body. Returns the dropped body or
        ``None`` (already at floor / legacy actor)."""
        if self.binding is None:
            return None
        dropped = self.binding.pop()
        if dropped is not None:
            self._emit_focus(dropped, "pop")
        return dropped

    def collapse_focus(self, body):
        """Unwind the stack down to ``body`` (or the floor if absent), emitting
        one ``FocusChanged(pop)`` per dropped level, top-first. Returns the list
        of dropped bodies (top-first); ``[]`` for a legacy actor."""
        if self.binding is None:
            return []
        dropped = self.binding.collapse_to(body)
        for d in dropped:
            self._emit_focus(d, "pop")
        return dropped

    def _emit_focus(self, body, change):
        from .engine import engine as _engine

        _engine.emit(FocusChanged(actor=self, body=body, change=change, focus=self.focus))

    # -- multi-session race guard -------------------------------------------
    def snapshot_focus(self):
        """Pin the current focus + generation for the span of one dispatch.

        Called by the engine at dispatch entry. Pinning :attr:`_focus_snapshot`
        freezes :attr:`focus` for the whole dispatch, so a suspended
        ``carry_out`` keeps acting on the body it started on even if another
        session pushes/pops the *live* stack meanwhile. No-op for a legacy
        (unbound) actor."""
        if self.binding is None:
            return
        self._focus_snapshot = self.binding.focus
        self._gen_at_start = self.binding.db_generation

    def focus_still_valid(self) -> bool:
        """Whether the pinned focus body still belongs on the live (DB) stack.

        Cross-session-correct: re-reads the binding's generation/stack from the
        DB (another session mutates a *different* in-memory instance of the same
        row). Fast path returns ``True`` when the generation is unchanged. Used
        by the engine after a suspended rule resumes — ``False`` means the body
        this dispatch acts for was popped/collapsed away (or the binding row was
        deleted), so the remainder of the phase must abort."""
        if self.binding is None or self._focus_snapshot is None:
            return True
        current = self.binding.current_generation()
        if current is None:
            return False
        if current == self._gen_at_start:
            return True
        return self.binding.live_contains(self._focus_snapshot)

    # -- construction --------------------------------------------------------
    @classmethod
    def from_caller(cls, caller, session=None, callertype=None):
        """Build an :class:`Actor` from a cmdhandler ``caller`` (+ optional session).

        Mirrors how ``cmdhandler`` resolves the acting entity. When ``callertype``
        is given (``"session"`` / ``"account"`` / ``"object"`` — the same token
        cmdhandler passes), the triple is filled deterministically; this is the
        path the Phase-4 bridge uses. Attribute-sniffing alone is ambiguous: a
        ``ServerSession`` exposes *both* ``.account`` and a focus body (via
        ``get_puppet()``), so without ``callertype`` it would be misread as a
        character.

        Args:
            caller: a character, account, or session object.
            session: the originating session, if known separately.
            callertype (str | None): ``"session"``/``"account"``/``"object"``;
                ``None`` falls back to attribute heuristics (programmatic callers).

        Returns:
            Actor: the assembled actor view.
        """
        # Deterministic construction from cmdhandler's callertype.
        if callertype == "session":
            sess = session or caller
            return cls(
                session=sess,
                account=getattr(sess, "account", None),
                binding=getattr(sess, "binding", None),
                character=sess.get_puppet() if hasattr(sess, "get_puppet") else None,
            )
        if callertype == "account":
            # An account caller may act OOC while one of its sessions drives a
            # body. With no explicit session, resolve the account's active
            # session so the focus body (and its binding) is reachable — mirrors
            # the "object" branch and the heuristic fallback below.
            if session is None:
                getter = getattr(getattr(caller, "sessions", None), "get", None)
                if callable(getter):
                    got = getter()
                    session = got[0] if got else None
            return cls(
                session=session,
                account=caller,
                binding=getattr(session, "binding", None) if session else None,
                character=(
                    session.get_puppet() if session and hasattr(session, "get_puppet") else None
                ),
            )
        if callertype == "object":
            account = getattr(caller, "account", None)
            if session is None:
                getter = getattr(getattr(caller, "sessions", None), "get", None)
                if callable(getter):
                    got = getter()
                    session = got[0] if got else None
            return cls(session=session, account=account, character=caller)

        # Fallback heuristic (tests / programmatic callers without a callertype).
        character = account = None
        if session is None:
            session = getattr(caller, "session", None)

        if hasattr(caller, "is_superuser") and hasattr(caller, "characters"):
            # looks like an account
            account = caller
            # An account drives via a session; its active body (if any) comes
            # from the session's binding, resolved just below.
            character = None
        elif hasattr(caller, "account"):
            # looks like a character/object
            character = caller
            account = getattr(caller, "account", None)
        else:
            # fall back: treat as character
            character = caller

        if session is None and account is not None:
            sessions = getattr(getattr(account, "sessions", None), "all", None)
            if callable(sessions):
                all_sessions = sessions()
                session = all_sessions[0] if all_sessions else None

        binding = getattr(session, "binding", None) if session is not None else None
        return cls(session=session, account=account, binding=binding, character=character)
