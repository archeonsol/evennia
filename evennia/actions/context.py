"""
``ActionContext`` + ``ActionContextBuilder`` — the per-dispatch provider list
(CM1 Phase 1a / Phase 3b).

The context carries the world objects whose ``@rule`` methods may respond to an
action, in the canonical priority order the engine iterates::

    providers = [
        *actor.state_objects,    # states first — highest gate priority
        actor.effective,         # the actor's own rules (character or account)
        *actor.equipped_items,   # item before-rules (intercept, not gate)
        actor.location,          # room rules
        *action.targets,         # direct target(s)
        *actor.location.contents # other room objects
    ]

``ActionContext`` itself stays a plain ordered container. ``ActionContextBuilder``
(Phase 3b) assembles that list from an :class:`~evennia.actions.actor.Actor`,
replacing ``cmdhandler.get_and_merge_cmdsets``: a pure function, no side effects,
called once per dispatch. ``None`` providers are tolerated (the engine skips
them), so the builder can splat optional slots without filtering.
"""

from dataclasses import dataclass, field

__all__ = ["ActionContext", "ActionContextBuilder", "build_context"]


@dataclass
class ActionContext:
    """The ordered list of rule providers for a single dispatch.

    Attributes:
        providers (list): World objects whose classes may carry ``@rule`` methods,
            in dispatch priority order. The engine iterates this list per phase,
            gathers each provider's matching rules, and sorts the union by rule
            priority (stable, so this list order breaks priority ties).
        actor: the :class:`~evennia.actions.actor.Actor` this context was built
            for (``None`` for hand-built test contexts).
        raw_string (str): the raw input line that produced the dispatch.
        trace_id (str): an opaque id correlating this dispatch's trace records.
    """

    providers: list = field(default_factory=list)
    actor: object = None
    raw_string: str = ""
    trace_id: str = ""


class ActionContextBuilder:
    """Assembles an :class:`ActionContext` from an actor (Phase 3b).

    Pure and side-effect free. The provider order is the canonical Phase-1a
    order; duplicate objects are removed keeping their *first* (highest-priority)
    occurrence, and ``None`` slots are dropped. Targets are passed in by the
    caller (the parser knows them after it has resolved the action's fields).
    """

    def build(self, actor, raw_string="", targets=(), trace_id="", callertype=None, action_type=None):
        """Return the ordered :class:`ActionContext` for ``actor``.

        Args:
            actor (Actor): the acting entity.
            raw_string (str): the raw input line (stored on the context).
            targets (iterable): the action's resolved target object(s), inserted
                after the room and before the room's other contents.
            trace_id (str): optional correlation id for trace records.
            callertype (str | None): ``"account"`` / ``"object"`` / ``"session"``.
                Legacy: when ``"account"`` alone (no ``action_type``), the account
                is inserted before ``effective``.
            action_type (type | None): the :class:`~evennia.actions.action.Action`
                subclass being dispatched. When its
                :attr:`~evennia.actions.action.Action.__primary_handler__` matches
                ``actor.account``, that account is inserted before ``effective``
                while puppeted so account-shell rules fire on the production
                ``callertype="session"`` path. When given, the **B2 prefilter** drops
                every provider whose class carries no rule for this action type
                (concrete or catch-all) — a busy room's inert contents never enter
                the list the engine iterates. ``None`` keeps every provider (the
                back-compat path for hand-built test contexts).

        Returns:
            ActionContext: providers in canonical dispatch order.
        """
        effective = actor.effective
        location = actor.location
        targets = list(targets)
        account = getattr(actor, "account", None)

        handler = (
            getattr(action_type, "__primary_handler__", None)
            if action_type is not None
            else None
        )

        actor_providers = []
        if account is not None:
            if handler is not None:
                try:
                    if isinstance(account, handler) and effective is not account:
                        actor_providers.append(account)
                except TypeError:
                    pass
            elif callertype == "account":
                actor_providers.append(account)
        actor_providers.append(effective)

        ordered = [
            *actor.state_objects,
            *actor_providers,
            *actor.equipped_items,
            location,
            *targets,
            *self._room_contents(location),
        ]

        providers = self._dedupe(ordered)
        if action_type is not None:
            from .registry import rule_registry

            providers = [
                p for p in providers if rule_registry.responds(type(p), action_type)
            ]
        return ActionContext(
            providers=providers,
            actor=actor,
            raw_string=raw_string,
            trace_id=trace_id,
        )

    @staticmethod
    def _room_contents(location):
        """Other objects in the room (best-effort; ``[]`` when no location)."""
        if location is None:
            return []
        contents = getattr(location, "contents", None)
        return list(contents) if contents else []

    @staticmethod
    def _dedupe(objects):
        """Drop ``None`` and keep the first occurrence of each object (by identity)."""
        seen = set()
        out = []
        for obj in objects:
            if obj is None:
                continue
            key = id(obj)
            if key in seen:
                continue
            seen.add(key)
            out.append(obj)
        return out


#: shared builder instance
build_context = ActionContextBuilder().build
