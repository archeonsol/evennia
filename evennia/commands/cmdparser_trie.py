"""
Trie-backed command parser (Phase 4).

Default ``COMMAND_PARSER`` since 6.0.0+underspire.11. Same input/output
contract as :mod:`evennia.commands.cmdparser`; uses a token-prefix trie to
collect candidate Command objects before evaluating ``cmd.match()`` so large
merged cmdsets parse in roughly O(input-token-count) candidates rather than
O(total-commands).

Two safe shortcuts on top of the trie walk:

- **Unambiguous first-token abbreviations** (``COMMAND_PARSER_TRIE_ABBREV``,
  default ``True``): if the first typed token is not a trie edge but uniquely
  prefixes exactly one root key, the input is rewritten to use the canonical
  shortest matching root key so ``create_match`` slices ``args`` correctly.

- **Exact-match fast path** (``COMMAND_PARSER_TRIE_FASTPATH``, default
  ``True``): when there is exactly one candidate and the command class did
  not override ``match`` (and is not an exit), the boundary check is inlined
  to skip the ``cmd.match()`` call.

The trie is cached on the merged cmdset and invalidated whenever any
command's key, alias list, or ``is_exit`` flag changes, so reused merge-cache
cmdsets cannot get stuck with a stale trie.

Cache contract (per-cmdset trie attached as ``_trie_command_trie``):

- **Fills on:** first ``trie_build_matches`` call against a cmdset, after a
  cheap-key (``len(commands) + sum(id(c) for c in commands)``) miss followed
  by a structural-signature (``(key, sorted(aliases), is_exit)`` per command)
  miss. The two-tier check keeps the common reuse case cheap.
- **Invalidates on:** cheap-key change (commands added or removed) followed
  by signature change (any cmd's key, aliases, or ``is_exit`` flag mutated).
  In production the merge path produces a new cmdset object on any cmd
  change, which has no cached trie attribute and rebuilds from scratch.
- **Staleness bound:** zero under the production rebuild flow. The one
  caveat: in-place alias mutation on a *reused* cmdset object is not
  detected by the cheap key alone — callers must
  ``del cmdset._trie_command_trie`` to force a rebuild. The in-place
  mutation API (``Command.set_key`` / ``set_aliases``) was audited
  (F-5, cache audit) and has **zero live in-tree callers** — the one
  call inside ``evmenu._update_aliases`` is itself reachable only via
  two commented-out invocation sites. Footgun stays documented.

Opt-out: set ``settings.COMMAND_PARSER`` to
``"evennia.commands.cmdparser.cmdparser"`` to fall back to the linear
build_matches parser (kept for backward compatibility).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from django.conf import settings

from evennia.commands.cmdparser import (
    create_match,
    try_multimatch_differentiators,
    try_num_differentiators,
)
from evennia.utils.logger import log_trace, mask_sensitive_input
from evennia.utils.multimatch import resolve_multimatch_index

# Re-export parser-neutral helpers from the linear cmdparser module so
# downstream parser wrappers can import everything they need from
# ``cmdparser_trie`` without reaching into the now-non-default module.
# ``try_num_differentiators`` parses the numerical multimatch separator
# (``2-ball``) and is used by both the linear and trie parsers verbatim.
__all__ = (
    "CommandTrie",
    "cmdparser",
    "create_match",
    "fuzzy_command_suggestions",
    "levenshtein",
    "trie_build_matches",
    "try_num_differentiators",
)

_FIRST_TOKEN_RE = re.compile(r"^(\s*)(\S+)", re.UNICODE)


def _replace_first_token_raw(raw: str, new_lowercase_token: str) -> str:
    """Replace the first whitespace-delimited token with ``new_lowercase_token``."""
    m = _FIRST_TOKEN_RE.match(raw or "")
    if not m:
        return raw
    return m.group(1) + new_lowercase_token + raw[m.end() :]


def _collect_cmds_in_subtree(node: Any) -> List[Any]:
    """Depth-first list of Command instances under a trie node (deduped by id)."""
    if not isinstance(node, dict):
        return []
    seen: Set[int] = set()
    out: List[Any] = []

    def walk(n: dict) -> None:
        for cmd in n.get("_commands", ()):
            cid = id(cmd)
            if cid not in seen:
                seen.add(cid)
                out.append(cmd)
        for kk, sub in n.items():
            if kk == "_commands" or not isinstance(sub, dict):
                continue
            walk(sub)

    walk(node)
    return out


def _expand_first_token_abbrev(
    trie: "CommandTrie", words: List[str], raw_string: str
) -> Tuple[List[str], str]:
    """Rewrite an unambiguous first-token abbreviation to its canonical key.

    If the first token is not a direct trie child but uniquely prefixes
    exactly one root key's subtree (one command), rewrite ``raw_string`` /
    ``words`` using the **shortest** matching root key among those subtrees
    so aliases like ``o`` are not replaced by longer keys like ``out`` for
    the same exit.

    Args:
        trie: The :class:`CommandTrie` built from the current cmdset.
        words: Lowercased input tokens (``raw_string.lower().split()``).
        raw_string: Original raw input string preserving case and spacing.

    Returns:
        tuple: ``(words, raw_string)`` rewritten in-place if expansion
        applied; otherwise the inputs returned unchanged.
    """
    if not getattr(settings, "COMMAND_PARSER_TRIE_ABBREV", True):
        return words, raw_string
    if not words:
        return words, raw_string
    t0 = words[0]
    if t0 in trie.root and isinstance(trie.root.get(t0), dict):
        return words, raw_string

    matching_keys = [
        k
        for k in trie.root
        if k != "_commands" and isinstance(trie.root.get(k), dict) and k.startswith(t0)
    ]
    if not matching_keys:
        return words, raw_string

    union_ids: Set[int] = set()
    for k in matching_keys:
        for cmd in _collect_cmds_in_subtree(trie.root[k]):
            union_ids.add(id(cmd))
    if len(union_ids) != 1:
        return words, raw_string
    canon = min(matching_keys, key=len)
    new_raw = _replace_first_token_raw(raw_string, canon)
    new_words = [canon] + words[1:]
    return new_words, new_raw


def _cmdset_command_structure_sig(
    cmdset: Any,
) -> Tuple[Tuple[str, Tuple[str, ...], bool], ...]:
    """Signature of every command's key + aliases + ``is_exit`` on this cmdset.

    Used to invalidate a cached :class:`CommandTrie` when a merged cmdset is
    reused but exit commands were rebuilt (e.g. a new alias ``o`` on an
    ``out`` exit) so the trie is not stuck without short exit edges.
    """
    rows: List[Tuple[str, Tuple[str, ...], bool]] = []
    for cmd in cmdset:
        key = (getattr(cmd, "key", None) or "").strip().lower()
        alset: Set[str] = set()
        for a in getattr(cmd, "aliases", None) or ():
            ax = (a or "").strip().lower()
            if ax:
                alset.add(ax)
        aliases = tuple(sorted(alset))
        is_exit = bool(getattr(cmd, "is_exit", False))
        rows.append((key, aliases, is_exit))
    rows.sort()
    return tuple(rows)


def _try_fast_match_exact(cmd: Any, search_string: str) -> Optional[Tuple[str, str]]:
    """Return ``(cmdname, raw_cmdname)`` when it is safe to skip ``cmd.match()``.

    Honors ``arg_regex`` on the argument tail the same way ``Command.match``
    does. Skipped for exits (so exit-priority logic upstream still runs) and
    for command classes that override ``match`` (their behavior is opaque).
    """
    if not getattr(settings, "COMMAND_PARSER_TRIE_FASTPATH", True):
        return None
    if getattr(cmd, "is_exit", False):
        return None
    if "match" in type(cmd).__dict__:
        return None
    arg_re = getattr(cmd, "arg_regex", None)
    for cmd_key in getattr(cmd, "_keyaliases", ()) or ():
        if not cmd_key:
            continue
        if search_string == cmd_key or (
            len(search_string) > len(cmd_key) and search_string.startswith(cmd_key + " ")
        ):
            rest = search_string[len(cmd_key) :]
            if arg_re and not arg_re.match(rest):
                continue
            return cmd_key, cmd_key
    return None


class CommandTrie:
    """Nested dict trie keyed on whitespace-split tokens of command keys/aliases.

    Each token level is a dict key; matching commands live under the
    ``_commands`` list at their leaf. Multi-word keys like ``go shard`` are
    stored as multiple token levels and *also* registered at their first
    token so prefix typing surfaces them.
    """

    __slots__ = ("root",)

    def __init__(self):
        self.root: Dict[str, Any] = {}

    def insert(self, cmd: Any) -> None:
        """Insert a Command's key + aliases into the trie."""
        keys: List[str] = []
        k = (getattr(cmd, "key", None) or "").strip().lower()
        if k:
            keys.append(k)
        for al in getattr(cmd, "aliases", None) or []:
            al = (al or "").strip().lower()
            if al and al not in keys:
                keys.append(al)
        seen_paths: Set[Tuple[str, ...]] = set()
        for key in keys:
            parts = tuple(key.split())
            if not parts:
                continue
            if parts in seen_paths:
                continue
            seen_paths.add(parts)
            node = self.root
            for p in parts:
                node = node.setdefault(p, {})
            node.setdefault("_commands", []).append(cmd)
            # Multi-word commands also register at their first token so that
            # collect_candidates surfaces them when only the first word is
            # typed (e.g. "go" yields "go shard"). False positives like
            # "go north" surfacing "go shard" are filtered by the per-cmd
            # match() pass downstream; the extra match() call is cheaper than
            # a separate prefix walk.
            if len(parts) > 1:
                first = self.root.setdefault(parts[0], {})
                first.setdefault("_commands", []).append(cmd)

    def collect_candidates(self, input_words: List[str]) -> List[Any]:
        """Walk input tokens through the trie, accumulating candidate Commands."""
        if not input_words:
            return []
        seen: Set[int] = set()
        out: List[Any] = []
        node = self.root
        for w in input_words:
            if w not in node:
                break
            node = node[w]
            for cmd in node.get("_commands", ()):
                cid = id(cmd)
                if cid not in seen:
                    seen.add(cid)
                    out.append(cmd)
        return out

    @classmethod
    def from_cmdset(cls, cmdset) -> "CommandTrie":
        """Build a fresh trie containing every command in ``cmdset``."""
        t = cls()
        for cmd in cmdset:
            t.insert(cmd)
        return t


def trie_build_matches(raw_string: str, cmdset) -> List[Tuple]:
    """Return match tuples for ``raw_string`` against ``cmdset`` via the trie.

    Drop-in for :func:`evennia.commands.cmdparser.build_matches`. Falls back
    to the linear ``build_matches`` if the trie produces no candidates (so
    custom ``Command.match`` overrides that use non-prefix logic still get
    evaluated) or if anything in the trie path raises.
    """
    matches: List[Tuple] = []
    try:
        # Two-tier cache: the cheap key (length + id-sum of command
        # instances) catches the common case (cmdset membership
        # unchanged) without paying the O(n) signature cost.
        # add()/remove() change the id-sum and force a sig recheck.
        # In-place mutation of a cached command's key/aliases is *not*
        # detected by the cheap key on its own; production code rebuilds
        # the containing cmdset on any cmd change, which produces a new
        # cmdset object (no cached attrs) and rebuilds the trie. If you
        # mutate a command's aliases in place while reusing the same
        # cmdset object, clear ``cmdset._trie_command_trie`` to force a
        # rebuild.
        commands = cmdset.commands
        cheap_key = (len(commands), sum(id(c) for c in commands))
        trie = getattr(cmdset, "_trie_command_trie", None)
        old_cheap = getattr(cmdset, "_trie_command_trie_cheap", None)
        if trie is None or old_cheap != cheap_key:
            sig = _cmdset_command_structure_sig(cmdset)
            old_sig = getattr(cmdset, "_trie_command_trie_sig", None)
            if trie is None or old_sig != sig:
                trie = CommandTrie.from_cmdset(cmdset)
                # Safe under Evennia's single-threaded Twisted reactor. If
                # parsing ever moves to deferToThread, a per-cmdset lock is
                # needed.
                cmdset._trie_command_trie = trie
                cmdset._trie_command_trie_sig = sig
            cmdset._trie_command_trie_cheap = cheap_key
        search_string = raw_string.lower()
        words = search_string.split()
        if words:
            words, raw_string = _expand_first_token_abbrev(trie, words, raw_string)
            search_string = raw_string.lower()
            words = search_string.split()
        candidates = trie.collect_candidates(words) if words else []
        if not candidates:
            from evennia.commands.cmdparser import build_matches as linear_bm

            return linear_bm(raw_string, cmdset)
        # Prefer multi-word commands ("go shard") over single-token prefixes ("go").
        candidates = sorted(
            {id(c): c for c in candidates}.values(),
            key=lambda c: (
                -len(((getattr(c, "key", None) or "") or "").split()),
                -len((getattr(c, "key", None) or "") or ""),
            ),
        )
        if len(candidates) == 1:
            sole = candidates[0]
            fast = _try_fast_match_exact(sole, search_string)
            if fast:
                cmdname, raw_cmdname = fast
                matches.append(create_match(cmdname, raw_string, sole, raw_cmdname))
                return matches
        for cmd in candidates:
            cmdname, raw_cmdname = cmd.match(search_string)
            if cmdname:
                matches.append(create_match(cmdname, raw_string, cmd, raw_cmdname))
        if not matches:
            from evennia.commands.cmdparser import build_matches as linear_bm

            return linear_bm(raw_string, cmdset)
    except Exception:
        log_trace(
            "cmdparser_trie.trie_build_matches raw_input:%s" % mask_sensitive_input(raw_string)
        )
        from evennia.commands.cmdparser import build_matches as linear_bm

        return linear_bm(raw_string, cmdset)
    return matches


def cmdparser(raw_string, cmdset, caller, match_index=None, session=None, **kwargs):
    """Trie-backed default command parser. See :mod:`cmdparser_trie`.

    Drop-in replacement for :func:`evennia.commands.cmdparser.cmdparser` with
    identical input and output contract: returns a list of match tuples as
    produced by :func:`evennia.commands.cmdparser.create_match`.

    Args:
        raw_string (str): The unparsed text entered by the caller.
        cmdset (CmdSet): The merged cmdset valid for this dispatch.
        caller (Session, Account or Object): The caller triggering parsing.
        match_index (int, optional): Disambiguator index for repeated parses
            (the ``1-look``, ``2-look`` style multimatch tiebreak).
        session (Session, optional): Session passed to access checks.

    Returns:
        list: Match tuples; empty if no command matched.
    """
    if not raw_string:
        return []

    matches = trie_build_matches(raw_string, cmdset)

    match_selector = None
    if not matches or len(matches) > 1:
        match_selector, new_raw_string = try_multimatch_differentiators(raw_string)
        if match_selector is not None:
            matches.extend(trie_build_matches(new_raw_string, cmdset))

    if getattr(settings, "CMD_ACCESS_CACHE_ENABLED", False):
        from evennia.commands.cmd_access_cache import cached_cmd_access

        matches = [
            match for match in matches if cached_cmd_access(match[2], caller, session=session)
        ]
    else:
        matches = [match for match in matches if match[2].access(caller, "cmd", session=session)]

    if len(matches) > 1:
        trimmed = [match for match in matches if raw_string.startswith(match[0])]
        if trimmed:
            matches = trimmed

    if len(matches) > 1:
        matches = sorted(matches, key=lambda m: m[3])
        quality = [mat[3] for mat in matches]
        matches = matches[-quality.count(quality[-1]) :]

    if len(matches) > 1:
        matches = sorted(matches, key=lambda m: m[4])
        quality = [mat[4] for mat in matches]
        matches = matches[-quality.count(quality[-1]) :]

    if len(matches) > 1 and match_selector is not None:
        idx = resolve_multimatch_index(match_selector, len(matches))
        if idx is not None:
            matches = [matches[idx]]
        else:
            matches = []

    return matches


def levenshtein(a: str, b: str) -> int:
    """Classic O(len(a)*len(b)) edit distance; intended for short strings."""
    if a == b:
        return 0
    la, lb = len(a), len(b)
    if la == 0:
        return lb
    if lb == 0:
        return la
    row = list(range(lb + 1))
    for i in range(1, la + 1):
        prev = row[0]
        row[0] = i
        for j in range(1, lb + 1):
            cur = row[j]
            cost = 0 if a[i - 1] == b[j - 1] else 1
            row[j] = min(row[j] + 1, row[j - 1] + 1, prev + cost)
            prev = cur
    return row[-1]


def fuzzy_command_suggestions(
    partial: str,
    cmdset,
    *,
    max_dist: Optional[int] = None,
    limit: Optional[int] = None,
) -> List[str]:
    """Return up to ``limit`` command keys/aliases close to the first token.

    Defaults to :setting:`COMMAND_FUZZY_SUGGESTIONS_MAX_DIST` (Levenshtein
    distance) and :setting:`COMMAND_FUZZY_SUGGESTIONS_LIMIT` when the kwargs
    are omitted.

    Args:
        partial: Raw input string; only the first whitespace-delimited token
            is compared.
        cmdset: Iterable of Command instances to search over.
        max_dist: Maximum edit distance, inclusive.
        limit: Maximum number of suggestions to return.

    Returns:
        list: Suggestion keys, ranked closest-first then lexicographically.
    """
    if max_dist is None:
        max_dist = getattr(settings, "COMMAND_FUZZY_SUGGESTIONS_MAX_DIST", 2)
    if limit is None:
        limit = getattr(settings, "COMMAND_FUZZY_SUGGESTIONS_LIMIT", 3)
    parts = (partial or "").strip().lower().split()
    if not parts:
        return []
    token = parts[0]
    names: Set[str] = set()
    for cmd in cmdset:
        k = (getattr(cmd, "key", None) or "").strip().lower()
        if k:
            names.add(k)
        for al in getattr(cmd, "aliases", None) or []:
            al = (al or "").strip().lower()
            if al:
                names.add(al)
    ranked: List[Tuple[int, str]] = []
    for name in names:
        d = levenshtein(token, name)
        if d <= max_dist:
            ranked.append((d, name))
    ranked.sort(key=lambda x: (x[0], x[1]))
    return [r[1] for r in ranked[:limit]]
