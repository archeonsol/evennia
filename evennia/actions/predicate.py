"""Typed, composable action predicates.

Predicate trees are immutable data used for introspection, actionable failure
explanations, cost ordering, and per-dispatch memoization. A registered
namespaced capability string compiles directly to :class:`HasCapability`.
There is no rank hierarchy, expression parser, or compatibility evaluator.
"""

from dataclasses import dataclass

from evennia.authorization.capabilities import normalize_capability
from evennia.authorization.service import has_capability

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
    "ALWAYS",
    "NEVER",
    "coerce_predicate",
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
    * namespaced capability string → :class:`HasCapability` (interned).
    * bare callable ``(action, actor) -> bool`` → ``cost=3`` leaf.
    * namespaced ``str`` → :class:`HasCapability`.
    """
    if isinstance(value, Predicate):
        return value
    if isinstance(value, str):
        return _intern(HasCapability(value))
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
    """Require one namespaced capability from the structured grant store."""

    capability: str
    principal_scope: str = "effective"
    cost = 1

    def __post_init__(self):
        object.__setattr__(self, "capability", normalize_capability(self.capability))
        scope = str(self.principal_scope).lower()
        if scope not in {"effective", "account", "object", "session"}:
            raise ValueError(f"invalid principal scope {scope!r}")
        object.__setattr__(self, "principal_scope", scope)

    def _obj(self, actor):
        return (
            getattr(actor, "effective", None)
            or getattr(actor, "character", None)
            or actor
        )

    def __call__(self, action, actor) -> bool:
        principal = self._obj(actor)
        if self.principal_scope == "account":
            principal = getattr(actor, "account", None) or getattr(
                principal, "account", None
            )
        elif self.principal_scope == "session":
            principal = getattr(actor, "session", None)
        if not principal:
            return False
        facade = getattr(principal, "has_capability", None)
        if callable(facade):
            return bool(facade(self.capability))
        return has_capability(principal, self.capability)

    def describe(self) -> str:
        return f"requires {self.capability}"


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
# Constants
# ---------------------------------------------------------------------------
ALWAYS = _intern(_Const(True))
NEVER = _intern(_Const(False))


IsAlive = IsAlive()
InSameRoom = InSameRoom()
