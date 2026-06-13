"""
Capability lattice — the typed permission model behind code-defined gates.

This module is the privilege half of the predicate layer (CM1 Phase 0f). It
gives *code-defined* command/action gates a typed role check that the type
checker can lint, that ``describe()`` can render for a UI, and that resolves
against the same permission state Evennia already stores — so a gate written as
``requires=Builder`` and a persisted lock written as ``perm(Builder)`` agree by
construction. The DSL is **not** retired; it remains the serialization format for
runtime/persisted per-object locks. Only the *authoring surface* for code gates
moves to predicates; the permission state is shared, never forked.

Toolkit, not policy
===================

A concrete privilege lattice ("Builder < Admin < Developer", plus cross-cutting
grants like MODERATE) is *game policy*, so it cannot live in engine code without
breaking toolkit-not-game. The engine therefore ships:

* :class:`Capability` — an **empty**, member-less :class:`enum.IntFlag` base. A
  member-less IntFlag is the one kind of enum that can still be subclassed
  (enums are replace-not-extend: once a class has members it is closed, so you
  cannot subclass it, monkey-patch members in, or merge two enums — cross-class
  ``IntFlag`` ops silently drop to untyped ``int``). The empty base is the
  extension point.
* :class:`DefaultCapability` — a concrete reference lattice subclassing the base.
  It is the active default *and* the "copy this" template a game forks.

A game declares its own lattice by subclassing :class:`Capability` and pointing
``settings.CAPABILITY_ENUM`` at it; :func:`get_capability_enum` resolves that
setting (defaulting to :class:`DefaultCapability`).

Hierarchy & scope resolve fresh
===============================

Roles are **not** baked into a frozen mask carried on the actor — a cached
derived mask is exactly the stale-derived-state failure mode CM1 exists to kill,
and a single int cannot represent quell or multiple scopes. Instead
:func:`resolve_capabilities` recomputes the mask **fresh on every check** from the
object's in-memory permission tags (no DB round-trip in the normal case), mirroring
the engine's ``perm()`` lockfunc: a higher rank implies all lower ranks; quell
takes the lower of account/puppet rank; the :class:`Scope` argument selects whose
permissions count. Nothing is stored, so nothing goes stale.

The result is still a :class:`Capability` mask, so the check site stays a single
``cap in mask`` membership test::

    Capability.BUILDER in resolve_capabilities(obj)   # one int AND, no walk

If a genuinely hot path ever needs a stored mask, that is a deliberate opt-in
cache with explicit single-writer invalidation — the very thing CM1 warns about,
entered on purpose, not the default.

Relationship to predicates
===========================

A bare ``Capability`` is coerced to a :class:`~evennia.actions.predicate.HasCapability`
predicate by the ``requires=`` machinery, so authors write either
``requires=Capability.BUILDER`` or the ergonomic singleton ``requires=Builder``.
The headline payoff is introspectability — ``describe()`` / ``unmet()`` /
proactive UI and the type checker as lock linter — not raw speed.
"""

from enum import CONTINUOUS, Enum, IntFlag, auto, verify

__all__ = [
    "Capability",
    "DefaultCapability",
    "Scope",
    "STAFF",
    "get_capability_enum",
    "rank_order",
    "resolve_capabilities",
    "capability_for_name",
]


class Scope(Enum):
    """Whose permissions a capability check resolves against.

    * ``ACCOUNT`` — the controlling account's own permissions (ignores the puppet).
    * ``PUPPET`` — the puppeted object's own permissions (ignores the account).
    * ``EFFECTIVE`` — the combined account+puppet view with quell semantics, exactly
      mirroring the ``perm()`` lockfunc. The default.
    """

    ACCOUNT = "account"
    PUPPET = "puppet"
    EFFECTIVE = "effective"


class Capability(IntFlag):
    """Empty base of the privilege lattice — the engine's extension point.

    Member-less on purpose: a game declares its real lattice by subclassing this
    (``class MyCaps(Capability): GUEST = auto(); ...``) and pointing
    ``settings.CAPABILITY_ENUM`` at the subclass. A member-less ``IntFlag`` is the
    only enum shape that is still subclassable, so this base must stay empty.
    """


@verify(CONTINUOUS)
class DefaultCapability(Capability):
    """The reference lattice — active default and copy-this template.

    Rank bits mirror ``settings.PERMISSION_HIERARCHY`` (Guest < Player < Helper <
    Builder < Admin < Developer); cross-cutting bits (MODERATE, IMPERSONATE) are
    orthogonal grants no rank implies. ``@verify(CONTINUOUS)`` makes a missing or
    duplicated ``auto()`` an import-time error.
    """

    NONE = 0
    # --- rank bits (mirror settings.PERMISSION_HIERARCHY, low → high) ---
    GUEST = auto()
    PLAYER = auto()
    HELPER = auto()
    BUILDER = auto()
    ADMIN = auto()
    DEVELOPER = auto()
    # --- cross-cutting bits (orthogonal to rank; never implied by a rank) ---
    MODERATE = auto()
    IMPERSONATE = auto()


# Rank order (low → high) and historical name aliases are attached *after* class
# creation: an enum body turns plain assignments into members, but the enum
# metaclass permits setting brand-new (non-member) attributes afterwards.
DefaultCapability.__rank_order__ = (
    DefaultCapability.GUEST,
    DefaultCapability.PLAYER,
    DefaultCapability.HELPER,
    DefaultCapability.BUILDER,
    DefaultCapability.ADMIN,
    DefaultCapability.DEVELOPER,
)
# Historical Evennia aliases → rank bits (singular forms; the plural is handled
# by the trailing-"s" fallback in capability_for_name).
DefaultCapability.__name_aliases__ = {
    "immortal": DefaultCapability.DEVELOPER,
    "wizard": DefaultCapability.ADMIN,
    "account": DefaultCapability.PLAYER,
}

#: Convenience mask: any staff-level rank, in the default lattice.
STAFF = DefaultCapability.BUILDER | DefaultCapability.ADMIN | DefaultCapability.DEVELOPER


# ---------------------------------------------------------------------------
# Active-enum resolution (settings-driven, defaulting to DefaultCapability)
# ---------------------------------------------------------------------------
_ENUM_CACHE: dict = {}


def get_capability_enum():
    """Return the active capability enum.

    Resolves ``settings.CAPABILITY_ENUM`` (a dotted path to a
    :class:`Capability` subclass) once and caches it; defaults to
    :class:`DefaultCapability` when the setting is unset.
    """
    try:
        from django.conf import settings

        path = getattr(settings, "CAPABILITY_ENUM", None) or None
    except Exception:
        path = None
    if path is None:
        return DefaultCapability
    enum = _ENUM_CACHE.get(path)
    if enum is None:
        from evennia.utils.utils import class_from_module

        enum = class_from_module(path)
        _ENUM_CACHE[path] = enum
    return enum


def rank_order(enum=None):
    """Return the ordered rank lattice (low → high) for ``enum``.

    Games declare this as ``__rank_order__`` on their capability enum; an enum
    without it has no hierarchy (an empty tuple), and rank checks degrade to plain
    membership.
    """
    enum = enum or get_capability_enum()
    return getattr(enum, "__rank_order__", ())


# ---------------------------------------------------------------------------
# Name → capability resolution
# ---------------------------------------------------------------------------
_NAME_MAP_CACHE: dict = {}


def _name_map(enum):
    """Cached ``lowercased member name -> member`` map for ``enum``."""
    m = _NAME_MAP_CACHE.get(enum)
    if m is None:
        m = {member.name.lower(): member for member in enum if member.name}
        _NAME_MAP_CACHE[enum] = m
    return m


def capability_for_name(name, enum=None):
    """Resolve a single permission/role string to a :class:`Capability` bit.

    Args:
        name (str): A permission name as used in ``perm()`` locks or stored on an
            account (case-insensitive; a trailing plural "s" and historical
            aliases like "Immortals"/"Wizards" are tolerated).
        enum (type): The capability enum to resolve against; defaults to the
            active enum.

    Returns:
        Capability | None: The matching bit, or ``None`` if the name is not a
        known capability (the caller decides whether to treat it as a tag, a
        legacy lock, etc.).
    """
    if not name:
        return None
    enum = enum or get_capability_enum()
    names = _name_map(enum)
    aliases = getattr(enum, "__name_aliases__", {})
    key = name.strip().lower()
    if key in names:
        return names[key]
    if key in aliases:
        return aliases[key]
    if key.endswith("s"):
        singular = key[:-1]
        if singular in names:
            return names[singular]
        if singular in aliases:
            return aliases[singular]
    return None


# ---------------------------------------------------------------------------
# Fresh, scope-aware resolution (mirrors the perm() lockfunc)
# ---------------------------------------------------------------------------
def _perms_all(obj):
    """The permission strings stored on ``obj`` (in-memory tag cache), or []."""
    if obj is None:
        return []
    try:
        return list(obj.permissions.all())
    except (AttributeError, TypeError):
        return []


def _account_of(obj):
    """The controlling account of ``obj``, or ``None`` (also None when ``obj`` is
    itself an account)."""
    return getattr(obj, "account", None)


def _is_quelled(account):
    try:
        return bool(account.attributes.get("_quell"))
    except AttributeError:
        return False


def _is_superuser(obj):
    try:
        return bool(getattr(obj, "is_superuser", False))
    except (AttributeError, TypeError):
        return False


def _highest_rank_index(enum):
    ranks = rank_order(enum)
    return len(ranks) - 1 if ranks else -1


def _apply_superuser_rank(highest, account, enum, quelled):
    """Map account superuser to top rank when not quelled (perm() bypass analogue)."""
    if account is None or quelled or not _is_superuser(account):
        return highest
    su_hi = _highest_rank_index(enum)
    return su_hi if su_hi > highest else highest


def _quelled_highest(hi_acct, hi_obj):
    """Lower of account/puppet rank; mirrors ``perm()`` when quelled.

    A missing rank (-1) participates in the min (``min(4, -1) == -1``), so a
    quelled staff account on a puppet with no rank tags does **not** inherit the
    account rank.
    """
    if hi_acct < 0 and hi_obj < 0:
        return -1
    if hi_acct < 0:
        return hi_obj
    if hi_obj < 0:
        return -1
    return min(hi_acct, hi_obj)


def _split_caps(perm_strings, enum):
    """Split permission strings into (highest rank position, cross-cutting mask)."""
    ranks = rank_order(enum)
    rank_set = set(ranks)
    highest = -1
    cross = enum(0)
    for perm in perm_strings:
        cap = capability_for_name(perm, enum)
        if cap is None:
            continue
        if cap in rank_set:
            idx = ranks.index(cap)
            if idx > highest:
                highest = idx
        else:
            cross |= cap
    return highest, cross


def _mask_from(highest, cross, enum):
    """Build a cumulative mask from a highest-rank position + cross-cutting bits."""
    ranks = rank_order(enum)
    mask = cross
    for i in range(highest + 1):
        mask |= ranks[i]
    return mask


def resolve_capabilities(obj, scope=Scope.EFFECTIVE, enum=None):
    """Resolve ``obj``'s capability mask **fresh**, mirroring the ``perm()`` lockfunc.

    Nothing is cached: the mask is recomputed from ``obj``'s in-memory permission
    tags on every call, so it can never go stale. A higher rank implies all lower
    ranks (encoded into the returned mask); cross-cutting bits are unioned in.

    Scope semantics:

    * :attr:`Scope.ACCOUNT` — the controlling account's own permissions (or
      ``obj``'s own, if it has no account), no quell.
    * :attr:`Scope.PUPPET` — ``obj``'s own permissions only, no quell.
    * :attr:`Scope.EFFECTIVE` (default) — the account+puppet view with quell: an
      unquelled puppet uses the account's rank; a quelled puppet uses the lower of
      account/puppet rank (so a high-rank object can't escalate the account);
      cross-cutting bits from both are unioned. With no account, ``obj``'s own
      permissions are used.

    Args:
        obj: The accessing object (a puppet/character, or an account).
        scope (Scope): Which permission set to resolve against. Default EFFECTIVE.
        enum (type): The capability enum; defaults to the active enum.

    Returns:
        Capability: The resolved mask.
    """
    enum = enum or get_capability_enum()
    if obj is None:
        return enum(0)

    if scope is Scope.PUPPET:
        highest, cross = _split_caps(_perms_all(obj), enum)
        return _mask_from(highest, cross, enum)

    if scope is Scope.ACCOUNT:
        account = _account_of(obj)
        source = account if account is not None else obj
        highest, cross = _split_caps(_perms_all(source), enum)
        highest = _apply_superuser_rank(
            highest, source if account is None else account, enum, False
        )
        return _mask_from(highest, cross, enum)

    # EFFECTIVE — mirror perm()
    account = _account_of(obj)
    if account is None:
        highest, cross = _split_caps(_perms_all(obj), enum)
        return _mask_from(highest, cross, enum)

    quelled = _is_quelled(account)
    hi_acct, cross_acct = _split_caps(_perms_all(account), enum)
    hi_acct = _apply_superuser_rank(hi_acct, account, enum, quelled)
    hi_obj, cross_obj = _split_caps(_perms_all(obj), enum)
    cross = cross_acct | cross_obj
    if quelled:
        highest = _quelled_highest(hi_acct, hi_obj)
    else:
        highest = hi_acct
    return _mask_from(highest, cross, enum)
