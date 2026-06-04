"""
The action + rule registries (CM1 Phase 0c, supports Phase 1 dispatch).

`ActionRegistry` maps verb strings → Action subclasses (built at import time via
``@action``). `RuleRegistry` collects ``@rule`` specs off a provider class's MRO
and answers "which rules respond to ``(action_type, phase)`` on this class?".

Catch-all rules (registered against the base ``Action``) are stored per-phase and
merged into every lookup, so a state gate like ``FlatlinedState`` that blocks all
actions fires for action types it never named explicitly. Merge results are
memoized on the provider class, so the per-dispatch cost is a dict hit, not a
re-merge (the Gate 1 "cache-free" decision is about the *cross-object* merge that
cmdsets did; this is a tiny per-class index, not that).
"""

from dataclasses import dataclass

from .exceptions import RuleConflict

__all__ = [
    "ActionRegistry",
    "RuleRegistry",
    "VerbMatch",
    "VerbTrie",
    "action_registry",
    "rule_registry",
]


def _is_system_verb(verb: str) -> bool:
    """True for engine-internal verbs like ``__noinput__`` that should never be
    matched by player input or offered as suggestions."""
    return verb.startswith("__") and verb.endswith("__")


def levenshtein(a: str, b: str) -> int:
    """Plain edit distance (insert/delete/substitute), iterative two-row."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(
                min(
                    prev[j] + 1,         # deletion
                    cur[j - 1] + 1,      # insertion
                    prev[j - 1] + (ca != cb),  # substitution
                )
            )
        prev = cur
    return prev[-1]


class VerbTrie:
    """A character-keyed trie of verb strings → ``Action`` subclass.

    Supports exact lookup and unambiguous-prefix lookup, mirroring the
    abbreviation behavior of ``cmdparser`` (typing ``lo`` matches ``look`` when
    no other verb shares that prefix). System verbs (``__...__``) are excluded.
    """

    __slots__ = ("_root",)

    def __init__(self):
        # node: {"children": {char: node}, "verb": str|None, "cls": type|None}
        self._root = self._node()

    @staticmethod
    def _node():
        return {"children": {}, "verb": None, "cls": None}

    def insert(self, verb: str, action_cls):
        node = self._root
        for ch in verb:
            node = node["children"].setdefault(ch, self._node())
        node["verb"] = verb
        node["cls"] = action_cls

    def _descend(self, token: str):
        node = self._root
        for ch in token:
            node = node["children"].get(ch)
            if node is None:
                return None
        return node

    def _leaves(self, node):
        """All ``(verb, cls)`` reachable at or below ``node``."""
        out = []
        if node["verb"] is not None:
            out.append((node["verb"], node["cls"]))
        for child in node["children"].values():
            out.extend(self._leaves(child))
        return out

    def match(self, token: str):
        """Resolve ``token`` to ``(verb, action_cls, confidence)``.

        Returns ``None`` if nothing matches. An exact verb yields confidence
        ``1.0``; a unique prefix yields ``len(token) / len(verb)`` (always
        < 1.0). An ambiguous prefix (several verbs share it) returns ``None`` —
        the caller treats that as a no-match (and may offer the candidates as
        suggestions).
        """
        if not token:
            return None
        node = self._descend(token)
        if node is None:
            return None
        if node["verb"] is not None:
            # exact landing — but a longer verb may also extend it; an exact
            # hit still wins outright.
            return (node["verb"], node["cls"], 1.0)
        leaves = self._leaves(node)
        if len(leaves) == 1:
            verb, cls = leaves[0]
            return (verb, cls, len(token) / len(verb))
        return None

    def prefix_candidates(self, token: str):
        """All verbs that have ``token`` as a prefix (for disambiguation msgs)."""
        node = self._descend(token)
        if node is None:
            return []
        return [verb for verb, _ in self._leaves(node)]


@dataclass(frozen=True, slots=True)
class VerbMatch:
    """Result of resolving input tokens against the verb registry."""

    canonical: str
    action_cls: type
    confidence: float
    span: int


class ActionRegistry:
    """Verb → Action subclass map. One global instance (`action_registry`) is the
    import-time registry; tests may build isolated instances."""

    def __init__(self):
        self._by_verb = {}
        self._actions = []
        self._trie = None  # lazily built; invalidated on register
        self._symbol_verbs = None  # lazily built; invalidated on register
        self._max_phrase_words = 1
        self._multi_word_starters = frozenset()

    def _rebuild_phrase_metadata(self):
        max_words = 1
        starters = set()
        for verb in self._by_verb:
            if _is_system_verb(verb):
                continue
            parts = verb.split()
            max_words = max(max_words, len(parts))
            if len(parts) > 1:
                starters.add(parts[0])
        self._max_phrase_words = max_words
        self._multi_word_starters = frozenset(starters)

    def register(self, action_cls, verbs):
        for verb in verbs:
            key = verb.lower()
            existing = self._by_verb.get(key)
            if existing is not None and existing is not action_cls:
                raise RuleConflict(
                    f"verb {verb!r} already registered to {existing.__name__}; "
                    f"cannot also bind to {action_cls.__name__}"
                )
            self._by_verb[key] = action_cls
        if action_cls not in self._actions:
            self._actions.append(action_cls)
        self._trie = None
        self._symbol_verbs = None
        self._rebuild_phrase_metadata()
        return action_cls

    def get(self, verb):
        return self._by_verb.get(verb.lower())

    def all_actions(self):
        return list(self._actions)

    def verbs(self):
        return dict(self._by_verb)

    @property
    def trie(self) -> "VerbTrie":
        """The verb trie, built once from the current verb map (system verbs
        excluded) and rebuilt the next time it's read after a ``register``."""
        if self._trie is None:
            trie = VerbTrie()
            for verb, action_cls in self._by_verb.items():
                if _is_system_verb(verb):
                    continue
                trie.insert(verb, action_cls)
            self._trie = trie
        return self._trie

    def match_tokens(self, tokens: list[str]):
        """Resolve ``tokens`` to a :class:`VerbMatch`, longest exact phrase first.

        Tries registered multi-word phrases (``go shard`` before ``go``), then
        falls back to single-token trie prefix abbreviation on ``tokens[0]``.
        """
        if not tokens:
            return None
        head = tokens[0].lower()
        if _is_system_verb(head):
            return None

        n = min(len(tokens), self._max_phrase_words)
        starters = self._multi_word_starters
        for i in range(n, 0, -1):
            if i > 1 and head not in starters:
                continue
            phrase = " ".join(t.lower() for t in tokens[:i])
            if _is_system_verb(phrase):
                continue
            action_cls = self._by_verb.get(phrase)
            if action_cls is not None:
                return VerbMatch(
                    canonical=phrase,
                    action_cls=action_cls,
                    confidence=1.0,
                    span=i,
                )

        matched = self.trie.match(head)
        if matched is None:
            return None
        canonical, action_cls, confidence = matched
        return VerbMatch(
            canonical=canonical,
            action_cls=action_cls,
            confidence=confidence,
            span=1,
        )

    def match_verb(self, token: str):
        """Resolve a verb token to ``(verb, action_cls, confidence)`` or ``None``
        (exact → 1.0, unique prefix → < 1.0). System verbs are never matched."""
        if not token or _is_system_verb(token.lower()):
            return None
        vm = self.match_tokens([token])
        if vm is None:
            return None
        return (vm.canonical, vm.action_cls, vm.confidence)

    @property
    def symbol_verbs(self):
        """Registered verbs made entirely of non-alphanumeric characters (e.g.
        ``"``, ``'``, ``.``, ``,``), longest-first. The parser may match these as
        *no-space* prefixes (``"hi`` → verb ``"`` + args ``hi``) — the engine
        analogue of a command's ``arg_regex=None``. Restricting to all-punctuation
        verbs means word verbs are never glued to their arguments. Rebuilt the
        next time it is read after a ``register``."""
        if self._symbol_verbs is None:
            syms = [
                v
                for v in self._by_verb
                if v and not _is_system_verb(v) and not any(ch.isalnum() for ch in v)
            ]
            syms.sort(key=len, reverse=True)
            self._symbol_verbs = syms
        return self._symbol_verbs

    def suggest_verbs(self, token: str, max_dist: int = 2, limit: int = 3):
        """Return up to ``limit`` registered verbs within edit distance
        ``max_dist`` of ``token``, closest first (for "did you mean…" output)."""
        token = token.lower()
        scored = []
        for verb in self._by_verb:
            if _is_system_verb(verb):
                continue
            dist = levenshtein(token, verb)
            if dist <= max_dist:
                scored.append((dist, verb))
        scored.sort(key=lambda pair: (pair[0], pair[1]))
        return [verb for _, verb in scored[:limit]]


def _is_catch_all(action_type) -> bool:
    """True if ``action_type`` is the catch-all base Action (own marker only, so
    concrete subclasses do not inherit catch-all status)."""
    return "_is_catch_all_base" in getattr(action_type, "__dict__", {})


class RuleRegistry:
    """Collects and indexes ``@rule`` specs per provider class."""

    def collect(self, cls):
        """Walk ``cls``'s MRO, gather rule specs, and cache the index on the
        class. The most-derived definition of a method wins (a subclass override
        — even one without ``@rule`` — shadows the base method's rules).

        Returns:
            dict: the ``(action_type, phase) -> [RuleSpec]`` concrete index
            (also stored as ``cls.__evennia_rules__``).
        """
        concrete = {}            # (action_type, phase) -> list[RuleSpec]
        catchall = {}            # phase -> list[RuleSpec]
        seen_methods = set()

        for klass in cls.__mro__:
            for name, attr in vars(klass).items():
                if name in seen_methods:
                    continue
                seen_methods.add(name)
                specs = getattr(attr, "__evennia_rule_specs__", None)
                if not specs:
                    continue
                for spec in specs:
                    if _is_catch_all(spec.action_type):
                        catchall.setdefault(spec.phase, []).append(spec)
                    else:
                        concrete.setdefault((spec.action_type, spec.phase), []).append(spec)

        # sort each bucket by priority desc, stable
        for bucket in concrete.values():
            bucket.sort(key=lambda s: -s.priority)
        for bucket in catchall.values():
            bucket.sort(key=lambda s: -s.priority)

        cls.__evennia_rules__ = concrete
        cls.__evennia_catchall__ = catchall
        cls.__evennia_rules_cache__ = {}
        cls.__evennia_responds_cache__ = {}
        return concrete

    def _ensure_collected(self, cls):
        # collected only when this exact class defines its own index (not inherited)
        if "__evennia_rules__" not in cls.__dict__:
            self.collect(cls)

    def rules_for(self, cls, action_type, phase):
        """Return the merged, priority-sorted rule list for ``(action_type,
        phase)`` on provider class ``cls`` — concrete rules for that action type
        plus any catch-all rules for the phase. Memoized per class."""
        self._ensure_collected(cls)
        cache = cls.__evennia_rules_cache__
        key = (action_type, phase)
        merged = cache.get(key)
        if merged is not None:
            return merged
        concrete = cls.__evennia_rules__.get((action_type, phase), ())
        catch = cls.__evennia_catchall__.get(phase, ())
        if not catch:
            merged = list(concrete)
        elif not concrete:
            merged = list(catch)
        else:
            merged = sorted([*concrete, *catch], key=lambda s: -s.priority)
        cache[key] = merged
        return merged

    #: phases scanned when deciding whether a class responds to an action type.
    _ALL_PHASES = ("before", "check", "carry_out", "report")

    def responds(self, cls, action_type) -> bool:
        """True if provider class ``cls`` has *any* rule that could fire for
        ``action_type`` (any phase) — concrete rules for that type, or *any*
        catch-all rule (which fires for every action type).

        This is the B2 provider prefilter: a class that answers ``False`` can
        never contribute to a dispatch of ``action_type``, so it can be dropped
        from the provider list before the engine ever iterates it (the win on a
        busy room full of inert objects). Memoized per ``(cls, action_type)``;
        static after import.
        """
        self._ensure_collected(cls)
        cache = getattr(cls, "__evennia_responds_cache__", None)
        if cache is None:
            cache = cls.__evennia_responds_cache__ = {}
        hit = cache.get(action_type)
        if hit is not None:
            return hit
        if cls.__evennia_catchall__:
            result = True
        else:
            idx = cls.__evennia_rules__
            result = any((action_type, ph) in idx for ph in self._ALL_PHASES)
        cache[action_type] = result
        return result

    def all_specs(self, cls):
        """Every spec registered on ``cls`` (concrete + catch-all), for
        introspection / a future ``@rules`` admin command."""
        self._ensure_collected(cls)
        out = []
        for bucket in cls.__evennia_rules__.values():
            out.extend(bucket)
        for bucket in cls.__evennia_catchall__.values():
            out.extend(bucket)
        return out


#: import-time singletons
action_registry = ActionRegistry()
rule_registry = RuleRegistry()
