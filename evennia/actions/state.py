"""
State objects (CM1 Phase 3c): per-actor rule providers with a lifecycle.

A *state* is an ordinary class carrying ``@rule`` methods, attached to an actor
at runtime. Because the engine treats any object in the dispatch context as a
rule provider, a state needs no special engine support — it simply sits at the
front of the provider list (``actor.state_objects``) so its rules get the first,
highest-gate say in every phase.

States model transient, stacking conditions ("flatlined", "grappled", "in a
menu", "disambiguating a target"). They are stored on the *holder* object — the
character or account — under ``holder.ndb.active_states`` (a plain list,
non-persistent: states do not survive a reload by design). This module owns the
three lifecycle operations:

* :func:`enter_state` — append a state instance (most-recent last).
* :func:`exit_state` — drop every state of a given type.
* :func:`has_state` — membership test by type.

:class:`Actor` (see ``actor.py``) exposes these as methods that delegate here,
so rule bodies can write ``actor.exit_state(DisambiguationState)``.

Concrete game states (FlatlinedState, GrappledState, …) live game-side, not in
the engine; the engine ships only the base plus the two generic interaction
states in ``menus.py`` (EvMenuState, DisambiguationState).
"""

__all__ = [
    "StateProvider",
    "enter_state",
    "exit_state",
    "has_state",
    "get_states",
    "capture_holder",
    "rehydrate_captures",
]


class StateProvider:
    """Base for all state objects.

    Subclass it, attach ``@rule`` methods, and install an instance with
    :func:`enter_state`. The base is intentionally empty — it exists as a marker
    and a shared home for any future common behavior. A state's ``@rule`` methods
    typically target the catch-all base ``Action`` at high priority to gate or
    intercept *every* action while the state is active.
    """

    __slots__ = ()


def _active_list(holder, *, create=False):
    """Return the holder's live ``active_states`` list.

    Args:
        holder: an object exposing ``.ndb`` (Evennia non-persistent attributes).
        create (bool): if True, initialize the list on the holder when absent.

    Returns:
        list | None: the list (possibly newly created), or ``None`` when absent
        and ``create`` is False.
    """
    ndb = getattr(holder, "ndb", None)
    if ndb is None:
        if not create:
            return None
        raise TypeError(f"{holder!r} has no .ndb to hold states")
    current = getattr(ndb, "active_states", None)
    if current is None:
        if not create:
            return None
        current = []
        ndb.active_states = current
    return current


def enter_state(holder, state):
    """Install ``state`` on ``holder`` (appended, so newest sorts last).

    Args:
        holder: the character/account whose ``ndb.active_states`` holds states.
        state (StateProvider): the state instance to add.

    Returns:
        StateProvider: the installed ``state`` (for chaining).
    """
    states = _active_list(holder, create=True)
    states.append(state)
    return state


def exit_state(holder, state_type):
    """Remove every state that is an instance of ``state_type`` from ``holder``.

    Args:
        holder: the character/account holding the states.
        state_type (type): the StateProvider subclass to drop.

    Returns:
        list: the removed state instances (empty if none were active).
    """
    states = _active_list(holder)
    if not states:
        return []
    removed = [s for s in states if isinstance(s, state_type)]
    if removed:
        holder.ndb.active_states = [s for s in states if not isinstance(s, state_type)]
    return removed


def has_state(holder, state_type) -> bool:
    """True if ``holder`` currently carries any state of ``state_type``."""
    states = _active_list(holder)
    if not states:
        return False
    return any(isinstance(s, state_type) for s in states)


def get_states(holder):
    """Return a copy of ``holder``'s active states (newest last); ``[]`` if none."""
    states = _active_list(holder)
    return list(states) if states else []


def capture_holder(caller, session=None):
    """The body an input-capture state must attach to so the engine sees it.

    Input-capture (``get_input``, ``ask_yes_no``, EvMore, EvEditor, ...) must
    install its state on the same body the action engine reads for the *next*
    line from ``session``: the actor's focus (the puppeted character when IC, the
    account when OOC). Installing on the raw ``caller`` silently misses whenever
    caller and focus differ - e.g. an account-level caller while a character is
    puppeted - because dispatch reads states from ``Actor.holder`` (the focus),
    not from the caller. Resolving through :meth:`Actor.from_caller` guarantees
    the install target matches the dispatch-time read.

    Args:
        caller: the character / account / session that opened the capture.
        session: the originating session, if known. Disambiguates the focus body
            in multisession setups (which puppet this capture belongs to).

    Returns:
        The focus body to install the capture state on (falls back to ``caller``
        if no focus can be resolved).
    """
    from .actor import Actor

    return Actor.from_caller(caller, session=session).holder or caller


# --------------------------------------------------------------------------- #
# Persistent-capture rehydration seam
# --------------------------------------------------------------------------- #

# Declarative table of persisted input-captures: ``(marker_attribute, dotted
# path to a rehydrate(holder) callable)``. Each migrated capture util appends
# one row. The marker attribute is the Attribute the util persists its rebuild
# data under, so the (lazy) import below only happens for a holder that actually
# has a suspended capture of that kind.
_CAPTURE_REHYDRATORS = [
    ("_eveditor_saved", "evennia.utils.eveditor.rehydrate"),
]


def rehydrate_captures(holder):
    """Reinstall any persisted input-capture state on ``holder`` after a reload.

    States are non-persistent by design, so a ``persistent=True`` capture (e.g.
    an EvEditor open across a ``@reload``) loses its live :class:`StateProvider`
    even though its rebuild data survives in Attributes. This is the declarative
    seam that re-installs it: ``at_post_load`` calls it on every cache load, and
    each registered util re-installs its capture iff its marker Attribute is
    present.

    Cheap and idempotent by contract: the marker check gates the (lazy) import,
    and each rehydrator must no-op when its capture is already live (the hook
    fires on every cache load, not just first load).

    The marker is probed with the backend's cache-free ``query_key`` rather than
    ``attributes.has``: this runs on *every* object load, and ``has`` would cache
    a negative ``marker`` lookup on every captureless object (the overwhelming
    majority). On the default JSONB backend ``query_key`` is just a membership
    check against the already-loaded row, so the gate costs no extra query.

    Args:
        holder: the character / account being loaded into the idmapper cache.
    """
    attrhandler = getattr(holder, "attributes", None)
    if attrhandler is None:
        return
    backend = attrhandler.backend
    for attr, path in _CAPTURE_REHYDRATORS:
        if backend.query_key(attr, None):
            try:
                from evennia.utils.utils import class_from_module

                class_from_module(path)(holder)
            except Exception:
                from evennia.utils import logger

                logger.log_trace()
