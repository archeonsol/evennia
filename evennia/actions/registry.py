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

from collections import deque
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
                    prev[j] + 1,  # deletion
                    cur[j - 1] + 1,  # insertion
                    prev[j - 1] + (ca != cb),  # substitution
                )
            )
        prev = cur
    return prev[-1]


#: Largest distance a deletion index is built for. Beyond this the registry
#: falls back to the exact linear scan (the index grows as O(V * L**max_dist)).
_SUGGEST_INDEX_MAX_DIST = 3


def _deletion_variants(word: str, max_dist: int) -> set:
    """Every string reachable from ``word`` by at most ``max_dist`` deletions.

    The SymSpell candidate property: when the edit distance between two words
    is at most ``max_dist``, their deletion-variant sets intersect. Building the
    index from the dictionary side and probing it from the query side therefore
    yields a superset of all words within ``max_dist``, which is then verified
    with an exact (banded) edit distance.
    """
    variants = {word}
    for _ in range(max_dist):
        expanded = set()
        for current in variants:
            for i in range(len(current)):
                expanded.add(current[:i] + current[i + 1 :])
        variants |= expanded
    return variants


def _banded_levenshtein(a: str, b: str, max_dist: int) -> int:
    """Exact edit distance with an early exit once a row exceeds ``max_dist``.

    Returns the true distance when it is ``<= max_dist``; otherwise returns an
    arbitrary value greater than ``max_dist`` (callers only compare against the
    bound). Rows whose minimum exceeds the bound cannot lead to a result within
    it, so the computation stops there (Ukkonen cutoff).
    """
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if abs(la - lb) > max_dist:
        return max_dist + 1
    prev = list(range(lb + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        row_min = i
        for j, cb in enumerate(b, 1):
            value = min(
                prev[j] + 1,
                cur[j - 1] + 1,
                prev[j - 1] + (ca != cb),
            )
            cur.append(value)
            if value < row_min:
                row_min = value
        if row_min > max_dist:
            return max_dist + 1
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
        """All ``(verb, cls)`` reachable at or below ``node``.

        Iterative depth-first walk (recursion paid a function call per node on
        a wide single-character subtree); the order of the returned leaves is
        unspecified.
        """
        out = []
        stack = [node]
        while stack:
            current = stack.pop()
            if current["verb"] is not None:
                out.append((current["verb"], current["cls"]))
            stack.extend(current["children"].values())
        return out

    def iter_leaves_ranked(self, token: str):
        """Yield ``(verb, cls)`` leaves under ``token`` in rank order.

        Rank is the parser's abbreviation confidence — shortest verb first,
        lexicographic within a length — produced by a breadth-first walk with
        each node's children visited in sorted order. The walk is lazy, so a
        caller that stops after a few results never materializes a wide
        subtree (a one-character prefix can cover dozens of verbs).
        """
        node = self._descend(token)
        if node is None:
            return
        queue = deque([node])
        while queue:
            current = queue.popleft()
            if current["verb"] is not None:
                yield current["verb"], current["cls"]
            for char in sorted(current["children"]):
                queue.append(current["children"][char])

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
        leaves = self._first_leaves(node, 2)
        if len(leaves) == 1:
            verb, cls = leaves[0]
            return (verb, cls, len(token) / len(verb))
        return None

    def _first_leaves(self, node, cap):
        """Up to ``cap`` ``(verb, cls)`` leaves at or below ``node``.

        Abandons the walk once ``cap`` leaves are found, so an ambiguous prefix
        does not materialize an entire subtree the way :meth:`_leaves` does.
        Only the leaf *count* (against ``cap``) matters to callers; the order
        of the returned leaves is unspecified.
        """
        out = []
        stack = [node]
        while stack and len(out) < cap:
            current = stack.pop()
            if current["verb"] is not None:
                out.append((current["verb"], current["cls"]))
                if len(out) >= cap:
                    break
            stack.extend(current["children"].values())
        return out

    def prefix_candidates(self, token: str):
        """All verbs that have ``token`` as a prefix.

        The parser uses this to offer the candidates a player was abbreviating
        when an ambiguous prefix fails to resolve.
        """
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
        self._verbs_by_action = {}
        self._actions = []
        self._trie = None  # lazily built; invalidated on register
        self._symbol_verbs = None  # lazily built; invalidated on register
        self._glued_verbs = None  # explicitly opted-in word-bearing prefixes
        self._no_space_prefix = None  # combined symbol+glued tuple; invalidated on register
        self._phrase_metadata = None  # lazily built; invalidated on register
        self._suggest_indexes = {}  # max_dist -> SymSpell deletion index; cleared on register

    def _rebuild_phrase_metadata(self):
        """Recompute and cache ``(max_phrase_words, multi_word_starters)``.

        Scanning every verb once per ``register`` call made registration
        quadratic in the verb count: ~630 verbs over ~630 registrations meant
        ~400k scans at import time, all of them discarded by the next
        registration. Registration now only invalidates, exactly as it does for
        the trie, and this runs once on the first read afterwards.
        """
        max_words = 1
        starters = set()
        for verb in self._by_verb:
            if _is_system_verb(verb):
                continue
            parts = verb.split()
            max_words = max(max_words, len(parts))
            if len(parts) > 1:
                starters.add(parts[0])
        self._phrase_metadata = (max_words, frozenset(starters))
        return self._phrase_metadata

    @property
    def _phrase_metadata_pair(self):
        metadata = self._phrase_metadata
        if metadata is None:
            metadata = self._rebuild_phrase_metadata()
        return metadata

    @property
    def _max_phrase_words(self):
        """Longest registered non-system verb phrase, in words."""
        return self._phrase_metadata_pair[0]

    @property
    def _multi_word_starters(self):
        """First words of every registered multi-word verb phrase."""
        return self._phrase_metadata_pair[1]

    def register(self, action_cls, verbs):
        for verb in verbs:
            key = verb.lower()
            existing = self._by_verb.get(key)
            if existing is not None and existing is not action_cls:
                engine_default = existing.__module__.startswith("evennia.actions.default.")
                game_override = not action_cls.__module__.startswith("evennia.actions.default.")
                if not (engine_default and game_override):
                    raise RuleConflict(
                        f"verb {verb!r} already registered to {existing.__name__}; "
                        f"cannot also bind to {action_cls.__name__}"
                    )
            self._by_verb[key] = action_cls
            action_verbs = self._verbs_by_action.setdefault(action_cls, [])
            if key not in action_verbs:
                action_verbs.append(key)
        if action_cls not in self._actions:
            self._actions.append(action_cls)
        self._trie = None
        self._symbol_verbs = None
        self._glued_verbs = None
        self._no_space_prefix = None
        self._phrase_metadata = None
        self._suggest_indexes.clear()
        return action_cls

    def get(self, verb):
        return self._by_verb.get(verb.lower())

    def all_actions(self):
        return list(self._actions)

    def verbs(self):
        return dict(self._by_verb)

    def verbs_for(self, action_cls):
        """Currently owned verbs for one action, in registration order."""
        return tuple(
            verb
            for verb in self._verbs_by_action.get(action_cls, ())
            if self._by_verb.get(verb) is action_cls
        )

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

    @property
    def glued_verbs(self):
        """Explicit aliases allowed as no-space prefixes, longest-first.

        Punctuation-only verbs already participate through
        :attr:`symbol_verbs`. This separate opt-in keeps ordinary word verbs
        from swallowing arbitrary input while permitting compact game syntax
        such as ``p.wave``.
        """
        if self._glued_verbs is None:
            found = []
            for action_cls in self._actions:
                owned = set(self.verbs_for(action_cls))
                for verb in getattr(action_cls, "__action_glued_verbs__", ()):
                    key = str(verb).lower()
                    if key in owned and key not in found:
                        found.append(key)
            found.sort(key=len, reverse=True)
            self._glued_verbs = found
        return self._glued_verbs

    @property
    def no_space_prefix_verbs(self):
        """All aliases eligible to consume glued argument text.

        Cached (a tuple) and invalidated on ``register``: the parser reads this
        on every trie-miss input, and rebuilding the merged list per call was
        pure allocation.
        """
        if self._no_space_prefix is None:
            found = list(self.symbol_verbs)
            for verb in self.glued_verbs:
                if verb not in found:
                    found.append(verb)
            found.sort(key=len, reverse=True)
            self._no_space_prefix = tuple(found)
        return self._no_space_prefix

    def _suggest_index(self, max_dist: int) -> dict:
        """Lazily build (and cache) the deletion index for ``max_dist``."""
        index = self._suggest_indexes.get(max_dist)
        if index is None:
            index = {}
            for verb in self._by_verb:
                if _is_system_verb(verb):
                    continue
                for variant in _deletion_variants(verb, max_dist):
                    index.setdefault(variant, []).append(verb)
            self._suggest_indexes[max_dist] = index
        return index

    def _suggest_candidates(self, token: str, max_dist: int) -> list:
        """Dictionary words whose deletion variants intersect ``token``'s.

        A superset of the words within ``max_dist`` of ``token`` (the SymSpell
        property); the caller verifies each candidate with an exact distance.
        """
        index = self._suggest_index(max_dist)
        candidates = []
        seen = set()
        for variant in _deletion_variants(token, max_dist):
            for verb in index.get(variant, ()):
                if verb not in seen:
                    seen.add(verb)
                    candidates.append(verb)
        return candidates

    def suggest_verbs(self, token: str, max_dist: int = 2, limit: int = 3, reachable=None):
        """Return up to ``limit`` registered verbs within edit distance
        ``max_dist`` of ``token``, closest first (for "did you mean…" output).

        Candidates come from a SymSpell-style deletion index (built lazily per
        ``max_dist`` and invalidated on ``register``), so a typo costs roughly
        O(candidates) instead of a full scan over every verb. Distances beyond
        :data:`_SUGGEST_INDEX_MAX_DIST` fall back to the exact linear scan,
        because index size grows as ``O(V * L**max_dist)``.

        The effective bound is also capped at ``len(token) - 1`` when
        ``max_dist > 0``: a token cannot be corrected by more edits than it has
        characters, so one-character input never pulls in unrelated
        one-character verbs (aliases and punctuation syntax) and two-character
        input admits a single edit. Longer tokens are unaffected.

        Args:
            token (str): the mistyped verb.
            max_dist (int): maximum edit distance to consider (upper bound;
                the effective bound is also capped by the token length).
            limit (int): maximum number of suggestions returned.
            reachable (callable, optional): ``(verb, action_cls) -> bool``
                filter applied *during* the closest-first walk, so a rejected
                near candidate does not crowd an accepted farther one out of
                the ``limit`` slice. Used to hide verbs the asking actor has
                no non-gated path to (the suggestion side of the fail-closed
                dispatch boundary).
        """
        token = token.lower()
        if max_dist > 0:
            max_dist = min(max_dist, len(token) - 1)
        scored = []
        if max_dist >= 0:
            if max_dist <= _SUGGEST_INDEX_MAX_DIST:
                candidates = self._suggest_candidates(token, max_dist)
            else:
                candidates = (verb for verb in self._by_verb if not _is_system_verb(verb))
            for verb in candidates:
                dist = _banded_levenshtein(token, verb, max_dist)
                if dist <= max_dist:
                    scored.append((dist, verb))
            scored.sort(key=lambda pair: (pair[0], pair[1]))
        if reachable is None:
            return [verb for _, verb in scored[:limit]]
        out = []
        for _, verb in scored:
            if len(out) >= limit:
                break
            if reachable(verb, self._by_verb[verb]):
                out.append(verb)
        return out


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
        concrete = {}  # (action_type, phase) -> list[RuleSpec]
        catchall = {}  # phase -> list[RuleSpec]
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
