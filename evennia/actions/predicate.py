"""
Predicate algebra — the typed, composable replacement for Evennia lock strings.

A lock is just a ``check`` rule whose body is a predicate. This module supplies
that body type: a small algebra of frozen, slotted dataclass nodes that compose
with ``&`` / ``|`` / ``~`` into a tree which is *data* — introspectable,
hashable, internable — rather than an opaque closure or a runtime-parsed string.

Why this shape (CM1 Phase 0f / Gate 5, absorbing L1)
====================================================

This is the typed authoring surface for *code-defined* command/action gates. It
does **not** retire Evennia's lock DSL: a string is the right serialization
format for runtime/persisted per-object locks, and that job stays with the DSL.
Both surfaces resolve against the one shared capability model (permission.py), so
a code gate ``requires=Builder`` and a persisted ``perm(Builder)`` agree by
construction — only the authoring surface differs, never the permission state.

The headline payoff is **introspectability**, not speed: a predicate tree is data,
so ``describe()`` renders it for help/UI, ``unmet()`` names the first failing
clause for an actionable error, and a proactive UI can grey out a disabled verb
*before* the player tries it. The type checker becomes the lock linter. (An opaque
runtime-parsed lock string offers none of this.)

Design constraints (deliberate):

* stdlib only — no new dependency;
* no homegrown expression DSL (that is just reinventing the lock string);
* no JIT/bytecode;
* no async predicates — ``check`` is synchronous per the AS1/CM1 contract.

The leverage is ``IntFlag`` (permission.py), frozen+slotted dataclasses, cost
ordering, and end-to-end static typing.

Cost model
==========

Each leaf declares an integer ``cost`` so :class:`And` / :class:`Or` can evaluate
cheap leaves first and short-circuit::

    0 = capability bit (int AND)   1 = identity compare
    2 = attribute read             3 = DB query / arbitrary callable

``And._build`` / ``Or._build`` flatten nested same-type nodes and sort leaves by
``cost`` ascending **once at construction** (the node is frozen), so
``Builder & Holds() & HasTag("vip")`` always checks the ~10ns capability bit
before the DB tag lookup. Identical expressions intern to one shared node, and
there is no per-dispatch parsing — ever.

The ``requires=`` contract
==========================

``@rule(..., requires=...)`` accepts a :class:`Predicate`, a
:class:`~evennia.actions.permission.Capability` (coerced to
:class:`HasCapability`), or a bare callable ``(action, actor) -> bool`` (coerced
to a ``cost=3`` leaf). It does **not** accept a lock *string* — that raises
``TypeError``. Strings are transpiled explicitly via :func:`from_lockstring`.
"""

import re
from dataclasses import dataclass

from .permission import (
    Capability,
    Scope,
    capability_for_name,
    get_capability_enum,
    rank_order,
    resolve_capabilities,
)

__all__ = [
    "Predicate",
    "HasCapability",
    "Holds",
    "HasTag",
    "HasAttr",
    "IsSelf",
    "IsObject",
    "IsAlive",
    "InSameRoom",
    "And",
    "Or",
    "Not",
    "Builder",
    "Admin",
    "Developer",
    "Player",
    "Helper",
    "Guest",
    "ALWAYS",
    "NEVER",
    "coerce_predicate",
    "from_lockstring",
    "LegacyLock",
]


# ---------------------------------------------------------------------------
# Interning
# ---------------------------------------------------------------------------
# Identical (by value) predicate trees collapse to a single shared instance, so
# `requires=` expressions compiled in different modules are `is`-identical. Frozen
# dataclasses are hashable by value, which makes this a one-line dict cache.
_INTERN: dict = {}


def _intern(node):
    existing = _INTERN.get(node)
    if existing is not None:
        return existing
    _INTERN[node] = node
    return node


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------
class Predicate:
    """Base for all predicate nodes.

    Subclasses are frozen, slotted dataclasses. ``cost`` is a plain class
    attribute (not a dataclass field) so it never enters ``__eq__``/``__hash__``.
    """

    __slots__ = ()
    cost = 0

    def __call__(self, action, actor) -> bool:  # pragma: no cover - abstract
        raise NotImplementedError

    # --- composition -------------------------------------------------------
    def __and__(self, other):
        return And._build(self, coerce_predicate(other))

    def __rand__(self, other):
        return And._build(coerce_predicate(other), self)

    def __or__(self, other):
        return Or._build(self, coerce_predicate(other))

    def __ror__(self, other):
        return Or._build(coerce_predicate(other), self)

    def __invert__(self):
        if isinstance(self, Not):
            return self.inner
        if isinstance(self, _Const):
            return NEVER if self.value else ALWAYS
        return _intern(Not(self))

    # --- introspection -----------------------------------------------------
    def describe(self) -> str:  # pragma: no cover - abstract
        """Human-readable text for help / UI / failure messages."""
        raise NotImplementedError

    def unmet(self, action, actor):
        """Return the first failing leaf (for an error message), or ``None`` if
        this predicate is satisfied. Default leaf behavior; composites override."""
        return None if self(action, actor) else self

    # --- memoized evaluation ----------------------------------------------
    def eval(self, action, actor, memo=None):
        """Evaluate this node, optionally memoizing leaf results for one dispatch.

        ``memo`` is a ``{predicate_node: bool}`` dict scoped to a single dispatch.
        Predicate nodes are interned and hashable, so a cost-3 leaf (e.g.
        ``HasTag``) shared by several rules in the same dispatch runs its DB query
        exactly once. ``memo=None`` (the default) bypasses caching entirely. The
        membership test is sentinel-safe — a cached ``False`` is honored.

        Composites (:class:`And` / :class:`Or` / :class:`Not`) override this to
        thread ``memo`` to their children rather than cache the cheap composite.
        """
        if memo is None:
            return self(action, actor)
        if self in memo:
            return memo[self]
        val = self(action, actor)
        memo[self] = val
        return val


def coerce_predicate(value) -> Predicate:
    """Coerce ``requires=`` input to a :class:`Predicate`.

    * :class:`Predicate` → returned unchanged.
    * :class:`Capability` → :class:`HasCapability` (interned).
    * bare callable ``(action, actor) -> bool`` → ``cost=3`` leaf.
    * ``str`` → ``TypeError`` (lock strings must go through ``from_lockstring``).
    """
    if isinstance(value, Predicate):
        return value
    if isinstance(value, Capability):
        return _intern(HasCapability(value))
    if isinstance(value, str):
        raise TypeError(
            "requires= does not accept lock strings; use a Predicate/Capability, "
            "or transpile explicitly with evennia.actions.from_lockstring()."
        )
    if callable(value):
        return _intern(_Callable(value))
    raise TypeError(f"cannot coerce {value!r} to a Predicate")


# ---------------------------------------------------------------------------
# Constant / capability / callable leaves
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class _Const(Predicate):
    """Always-true / always-false leaf (`true()` / `false()` lock funcs)."""

    value: bool
    cost = 0

    def __call__(self, action, actor) -> bool:
        return self.value

    def describe(self) -> str:
        return "always" if self.value else "never"


@dataclass(frozen=True, slots=True)
class HasCapability(Predicate):
    """Actor holds a capability bit — the typed equivalent of ``perm()``.

    The mask is resolved **fresh** on every check via
    :func:`~evennia.actions.permission.resolve_capabilities` (no stored,
    staleable ``actor.capabilities``). ``scope`` selects whose permissions count
    and defaults to :attr:`Scope.EFFECTIVE` (the account+puppet+quell view). The
    underlying read hits the in-memory tag cache, so cost is 1 (a cheap leaf that
    still sorts ahead of attribute/DB leaves), not a DB query.
    """

    cap: Capability
    scope: Scope = Scope.EFFECTIVE
    cost = 1

    def _obj(self, actor):
        return getattr(actor, "effective", None) or getattr(actor, "character", None) or actor

    def __call__(self, action, actor) -> bool:
        return self.cap in resolve_capabilities(self._obj(actor), scope=self.scope)

    def describe(self) -> str:
        return f"requires {self.cap.name.title()}"


@dataclass(frozen=True, slots=True)
class _Callable(Predicate):
    """Wraps a bare ``(action, actor) -> bool`` callable. Opaque, so cost=3."""

    func: object
    cost = 3

    def __call__(self, action, actor) -> bool:
        return bool(self.func(action, actor))

    def describe(self) -> str:
        return getattr(self.func, "__name__", repr(self.func))


# ---------------------------------------------------------------------------
# Relational / contextual leaves
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class Holds(Predicate):
    """The action target is in the actor's inventory (`holds()`)."""

    cost = 2

    def __call__(self, action, actor) -> bool:
        char = getattr(actor, "character", None)
        target = getattr(action, "target", None)
        if char is None or target is None:
            return False
        return target in char.contents

    def describe(self) -> str:
        return "must be holding it"


@dataclass(frozen=True, slots=True)
class InSameRoom(Predicate):
    """The action target shares the actor's location (identity compare)."""

    cost = 1

    def __call__(self, action, actor) -> bool:
        target = getattr(action, "target", None)
        loc = getattr(actor, "location", None)
        if target is None or loc is None:
            return False
        return getattr(target, "location", None) is loc

    def describe(self) -> str:
        return "must be in the same room"


@dataclass(frozen=True, slots=True)
class IsSelf(Predicate):
    """The action target is the actor's own character (`self()`)."""

    cost = 1

    def __call__(self, action, actor) -> bool:
        return getattr(action, "target", None) is getattr(actor, "character", None)

    def describe(self) -> str:
        return "must target yourself"


@dataclass(frozen=True, slots=True)
class IsObject(Predicate):
    """The actor's effective object has a specific dbref (`id()` / `dbref()`)."""

    dbref: int
    cost = 1

    def __call__(self, action, actor) -> bool:
        obj = getattr(actor, "effective", None) or getattr(actor, "character", None)
        return getattr(obj, "id", None) == self.dbref

    def describe(self) -> str:
        return f"must be #{self.dbref}"


@dataclass(frozen=True, slots=True)
class IsAlive(Predicate):
    """The actor's character is alive (attribute read).

    Reads the conventional ``alive`` attribute, defaulting to True when absent so
    a character with no life model is treated as alive. Games with a richer life
    model can register their own leaf; this is the engine default.
    """

    cost = 2

    def __call__(self, action, actor) -> bool:
        char = getattr(actor, "character", None)
        if char is None:
            return False
        db = getattr(char, "db", None)
        if db is not None:
            val = getattr(db, "alive", None)
            if val is not None:
                return bool(val)
        return bool(getattr(char, "alive", True))

    def describe(self) -> str:
        return "must be alive"


@dataclass(frozen=True, slots=True)
class HasAttr(Predicate):
    """The actor's effective object has an attribute (optionally ==value) (`attr()`)."""

    name: str
    value: object = None
    cost = 2

    def __call__(self, action, actor) -> bool:
        obj = getattr(actor, "effective", None) or getattr(actor, "character", None)
        if obj is None:
            return False
        attrs = getattr(obj, "attributes", None)
        if attrs is not None and hasattr(attrs, "has"):
            if self.value is None:
                return bool(attrs.has(self.name))
            return attrs.get(self.name, default=None) == self.value
        # fallback: plain attribute
        if not hasattr(obj, self.name):
            return False
        return self.value is None or getattr(obj, self.name) == self.value

    def describe(self) -> str:
        if self.value is None:
            return f"requires attribute {self.name!r}"
        return f"requires {self.name}={self.value!r}"


@dataclass(frozen=True, slots=True)
class HasTag(Predicate):
    """The actor's effective object carries a tag (`tag()`). DB query → cost=3."""

    key: str
    category: str = None
    cost = 3

    def __call__(self, action, actor) -> bool:
        obj = getattr(actor, "effective", None) or getattr(actor, "character", None)
        if obj is None:
            return False
        tags = getattr(obj, "tags", None)
        if tags is None or not hasattr(tags, "has"):
            return False
        return bool(tags.has(self.key, category=self.category))

    def describe(self) -> str:
        if self.category is None:
            return f"requires tag {self.key!r}"
        return f"requires tag {self.key!r} ({self.category})"


# ---------------------------------------------------------------------------
# Composite nodes
# ---------------------------------------------------------------------------
def _flatten(node_cls, preds):
    for p in preds:
        if type(p) is node_cls:
            yield from p.parts
        else:
            yield p


@dataclass(frozen=True, slots=True)
class And(Predicate):
    """All parts must pass. Parts are cost-sorted at build so cheap leaves
    short-circuit a failing tree first."""

    parts: tuple
    cost = 0

    @classmethod
    def _build(cls, *preds):
        flat = tuple(sorted(_flatten(cls, preds), key=lambda p: p.cost))
        if len(flat) == 1:
            return flat[0]
        return _intern(cls(flat))

    def __call__(self, action, actor) -> bool:
        return all(p(action, actor) for p in self.parts)

    def eval(self, action, actor, memo=None):
        return all(p.eval(action, actor, memo) for p in self.parts)

    def describe(self) -> str:
        return " and ".join(p.describe() for p in self.parts)

    def unmet(self, action, actor):
        for p in self.parts:
            failing = p.unmet(action, actor)
            if failing is not None:
                return failing
        return None


@dataclass(frozen=True, slots=True)
class Or(Predicate):
    """Any part passing is enough. Parts are cost-sorted so a cheap truthy leaf
    wins first."""

    parts: tuple
    cost = 0

    @classmethod
    def _build(cls, *preds):
        flat = tuple(sorted(_flatten(cls, preds), key=lambda p: p.cost))
        if len(flat) == 1:
            return flat[0]
        return _intern(cls(flat))

    def __call__(self, action, actor) -> bool:
        return any(p(action, actor) for p in self.parts)

    def eval(self, action, actor, memo=None):
        return any(p.eval(action, actor, memo) for p in self.parts)

    def describe(self) -> str:
        return " or ".join(p.describe() for p in self.parts)

    def unmet(self, action, actor):
        # the whole disjunction is what's unmet when none pass
        return None if self(action, actor) else self


@dataclass(frozen=True, slots=True)
class Not(Predicate):
    """Negation of a single predicate."""

    inner: Predicate

    @property
    def cost(self):
        return self.inner.cost

    def __call__(self, action, actor) -> bool:
        return not self.inner(action, actor)

    def eval(self, action, actor, memo=None):
        return not self.inner.eval(action, actor, memo)

    def describe(self) -> str:
        return f"not ({self.inner.describe()})"

    def unmet(self, action, actor):
        return None if self(action, actor) else self


# ---------------------------------------------------------------------------
# Ergonomic singletons — authors never touch the leaf classes directly
# ---------------------------------------------------------------------------
ALWAYS = _intern(_Const(True))
NEVER = _intern(_Const(False))


def _cap_singleton(rank_name):
    """Bind a rank singleton to the active enum's member, or NEVER if absent.

    The standard rank names (GUEST..DEVELOPER) mirror PERMISSION_HIERARCHY; a game
    that swaps the enum but keeps those names keeps these singletons too.
    """
    cap = getattr(get_capability_enum(), rank_name, None)
    return _intern(HasCapability(cap)) if cap is not None else NEVER


Guest = _cap_singleton("GUEST")
Player = _cap_singleton("PLAYER")
Helper = _cap_singleton("HELPER")
Builder = _cap_singleton("BUILDER")
Admin = _cap_singleton("ADMIN")
Developer = _cap_singleton("DEVELOPER")

IsAlive = IsAlive()
InSameRoom = InSameRoom()


# ---------------------------------------------------------------------------
# Lock-string transpiler + LegacyLock fallback (Phase 0f-bis)
# ---------------------------------------------------------------------------
# Token regex: a func-call atom OR a boolean operator. The func alternative is
# listed first so an operator inside arguments is never split out separately.
_TOKEN_RE = re.compile(r"\w+\([^)]*\)|\bAND\b|\bOR\b|\bNOT\b", re.IGNORECASE)
_FUNC_RE = re.compile(r"^(\w+)\((.*)\)$", re.DOTALL)

# Once-per-lockstring dedupe so a transpiled LegacyLock warns exactly once.
_LEGACY_WARNED: set = set()


def _parse_args(rest):
    """Split a lockfunc arg string into positional args (kwargs ignored for
    mapping; LegacyLock preserves the raw string for full fidelity)."""
    return [a.strip() for a in rest.split(",") if a.strip() and "=" not in a]


@dataclass(frozen=True, slots=True)
class LegacyLock(Predicate):
    """Fallback leaf for lock funcs with no typed predicate equivalent.

    Re-enters Evennia's existing lock evaluator (``check_lockstring``) against the
    actor's effective object, so transpilation NEVER silently drops semantics.
    Warns once per distinct lockstring so flagged sites can be hand-ported, then
    the ``LockHandler`` retires once ``LegacyLock`` dispatch hits zero (Phase 8).
    """

    lockstring: str
    access_type: str
    cost = 3

    def __call__(self, action, actor) -> bool:
        from evennia.locks.lockhandler import check_lockstring

        if self.lockstring not in _LEGACY_WARNED:
            _LEGACY_WARNED.add(self.lockstring)
            try:
                from evennia.utils import logger

                logger.log_warn(
                    f"LegacyLock fallback for unported lock {self.lockstring!r} "
                    f"(access_type={self.access_type!r}); hand-port to a predicate."
                )
            except Exception:
                pass
        obj = getattr(actor, "effective", None) or getattr(actor, "character", None)
        account = getattr(obj, "account", None) if obj is not None else None
        if account is None and actor is not None:
            account = getattr(actor, "account", None)
        no_bypass = False
        if account is not None:
            try:
                no_bypass = bool(account.attributes.get("_quell"))
            except (AttributeError, TypeError):
                no_bypass = False
        return bool(
            check_lockstring(
                obj,
                self.lockstring,
                access_type=self.access_type,
                default=False,
                no_superuser_bypass=no_bypass,
            )
        )

    def describe(self) -> str:
        return f"legacy lock: {self.lockstring}"


def _atom(funcstring, access_type, full_lockstring):
    """Map one ``funcname(args)`` token to a predicate leaf."""
    m = _FUNC_RE.match(funcstring.strip())
    if not m:
        return LegacyLock(full_lockstring, access_type)
    name = m.group(1).strip().lower()
    args = _parse_args(m.group(2))

    if name in ("true", "all"):
        return ALWAYS
    if name in ("false", "none"):
        return NEVER
    if name in ("perm", "pperm"):
        # pperm() is account-scoped; perm() uses the effective (quell-aware) view.
        scope = Scope.ACCOUNT if name == "pperm" else Scope.EFFECTIVE
        cap = capability_for_name(args[0]) if args else None
        return (
            _intern(HasCapability(cap, scope))
            if cap is not None
            else LegacyLock(full_lockstring, access_type)
        )
    if name in ("perm_above", "pperm_above"):
        scope = Scope.ACCOUNT if name == "pperm_above" else Scope.EFFECTIVE
        cap = capability_for_name(args[0]) if args else None
        ranks = rank_order()
        if cap is not None and cap in ranks:
            idx = ranks.index(cap)
            if idx + 1 < len(ranks):
                return _intern(HasCapability(ranks[idx + 1], scope))
        return LegacyLock(full_lockstring, access_type)
    if name == "holds":
        return _intern(Holds())
    if name == "self":
        return _intern(IsSelf())
    if name in ("id", "dbref"):
        try:
            return _intern(IsObject(int(args[0])))
        except (ValueError, IndexError):
            return LegacyLock(full_lockstring, access_type)
    if name == "tag":
        key = args[0] if args else ""
        category = args[1] if len(args) > 1 else None
        return _intern(HasTag(key, category))
    if name == "attr":
        attr_name = args[0] if args else ""
        value = args[1] if len(args) > 1 else None
        return _intern(HasAttr(attr_name, value))
    return LegacyLock(full_lockstring, access_type)


def _tokenize(rhs):
    tokens = []
    for m in _TOKEN_RE.finditer(rhs):
        t = m.group(0)
        up = t.upper()
        if up in ("AND", "OR", "NOT"):
            tokens.append((up.lower(),))
        else:
            tokens.append(("func", t))
    return tokens


def _parse(tokens, access_type, full_lockstring):
    """Recursive-descent parse honoring Python precedence: not > and > or."""
    pos = 0

    def parse_or():
        nonlocal pos
        node = parse_and()
        while pos < len(tokens) and tokens[pos][0] == "or":
            pos += 1
            node = node | parse_and()
        return node

    def parse_and():
        nonlocal pos
        node = parse_not()
        while pos < len(tokens) and tokens[pos][0] == "and":
            pos += 1
            node = node & parse_not()
        return node

    def parse_not():
        nonlocal pos
        if pos < len(tokens) and tokens[pos][0] == "not":
            pos += 1
            return ~parse_not()
        tok = tokens[pos]
        pos += 1
        return _atom(tok[1], access_type, full_lockstring)

    return parse_or()


def from_lockstring(lockstring: str, access_type: str) -> Predicate:
    """Transpile one Evennia lock string into a predicate tree.

    Known lock funcs map cleanly to typed leaves::

        perm()/pperm()         -> HasCapability      true()/all()  -> ALWAYS
        perm_above()           -> HasCapability(next) false()/none()-> NEVER
        holds()                -> Holds              self()        -> IsSelf
        id()/dbref()           -> IsObject           tag()         -> HasTag
        attr()                 -> HasAttr

    Unknown / custom game lock funcs become a :class:`LegacyLock` leaf that falls
    back to the existing evaluator and warns once, so semantics are never
    silently dropped.

    Args:
        lockstring (str): A lock string, either a bare definition
            (``"perm(Builder)"``) or one carrying its access type
            (``"cmd:perm(Builder)"``); may contain several ``;``-separated
            access types, in which case ``access_type`` selects the clause.
        access_type (str): The access type to extract / attach.

    Returns:
        Predicate: The compiled predicate tree (single leaf or And/Or/Not).
        Returns :data:`NEVER` if no clause matches the requested access type.
    """
    if not isinstance(lockstring, str):
        raise TypeError("from_lockstring expects a lock string")

    # Locate the clause for the requested access_type. A bare definition with no
    # "atype:" prefix is treated as already being the requested clause.
    rhs = None
    matched_clause = lockstring
    for clause in lockstring.split(";"):
        clause = clause.strip()
        if not clause:
            continue
        if ":" in clause:
            atype, body = (p.strip() for p in clause.split(":", 1))
            if atype.lower() == access_type.lower():
                rhs = body
                matched_clause = clause
                break
        else:
            # no prefix -> this *is* the clause body
            rhs = clause
            matched_clause = f"{access_type}:{clause}"
            break

    if rhs is None:
        return NEVER

    tokens = _tokenize(rhs)
    if not tokens:
        return NEVER
    return _parse(tokens, access_type, matched_clause)
