# Hygiene Backlog

Catalogued findings. F-numbers are stable IDs; file order is execution
order. Move to **Shipped** when landed; delete on won't-fix (cite
reason). Severity: red/yellow/green = bug/smell/polish. Status: `open`
(act) or `audit` (read first).

---

## Open (execution order)

**F5.** Past-due TODO markers. Green. Five sites scheduled for upstream
1.0/5.0 (fork is on 6.0): `comms/comms.py:934`, `objects/objects.py:2848`,
`commands/default/building.py:4275`, `utils/evmenu.py:670` and `:1011`.
Delete dormant paths or convert to issues.

**F4.** Bare `except:` in engine. Yellow. Three sites need typed
exception: `evennia/web/website/views/help.py:267,273`,
`evennia/utils/utils.py:2991`. Spot-check confirmed these are
bounds-check fallbacks, not fail-closed offenders; fix is mechanical.
Wider 83-site `except *: pass` audit deferred.

**F2.** `evennia/server/deprecations.py` upstream cargo. Yellow. ~150 LOC.
Raises on settings deprecated pre-1.0 upstream (`CMDSET_DEFAULT`,
`INLINEFUNC_*`, `TIME_SEC_PER_MIN…`) that Underspire never set. Shrink
or delete. Version bump.

**F12.** Lane-2 test wording refinement. Yellow. `core-beliefs.md` says
"opt-out cleanly via a setting." Practice accepts subclass-override as
clean opt-out for naming/structural conventions (Phase 3 `@`-prefix
rekey otherwise looks like a violation). Two-sentence edit. No bump.

**F11.** Phase 2 caller-derived-state sweep. Yellow. Phase 2
(`+underspire.4`) normalized `self.account`/`self.character`; cmdhandler
resolves centrally. Engine defaults still read `caller.account` directly
(building.py:703,1491,1493; general.py:193,794,799+; others). Mechanical
sweep finishes the Phase 2 promise.

**F8.** Cache invalidation contracts. Yellow. Seven caches:
`location_cmdset_cache`, `cmd_access_cache`, `display_name_cache`, lock
cache, write-behind attrs, trie cache, `redis_attr_cache`. Belief
("Objects carry their own state") requires each cache name "what fills
me / what invalidates me / staleness bound." Step 1: docstring pass per
cache (mechanical, closes belief gap). Step 2: cross-cache invalidation
audit (`permissions_changed` flow + lock cache) and TTL/size-cap
rationalisation.

**F6.** Settings prefix split `CMD_*` vs `COMMAND_*`. Green. One
straggler (`CMD_ACCESS_CACHE_ENABLED`) vs eight `COMMAND_*`. Rename
(breaking, single symbol, Underspire-only consumer) or document
convention in `code-style.md`. Pick one.

**F13.** Contribs half-policy. Yellow. `evennia/contrib/*` is "no new
additions" by stance but still public, tested, shipping. Either (a)
survivors-and-why doc + maintenance commitment, or (b) deprecation
window with target removal. Limbo isn't an answer.

**F14.** Refactor-doc archive pattern. Yellow. CMDSET_REFACTOR.md
(620), CMDSET_MIGRATION.md (661), PHASE3_AUDIT.md (129) live at repo
root post-refactor. Mostly archaeology; some load-bearing (F7 cites
§8). Move shipped-refactor docs to archive subdir; extract live items
to backlog.

**F15.** Cache-addition discipline. Green. Seven caches added, zero
removed. Policy: next cache lands with invalidation-contract docstring
(F8) + changelog note defending why caches 1-N still all exist.

**F3.** Doc rot in `docs/source/`. Yellow. 31 hits for removed APIs
(`MuxCommand`, `MuxAccountCommand`, `CMD_IGNORE_PREFIXES`). Sweep after
the newmoo `engine-16-adopt-contribs` PR merges (most contrib doc rot
dies with the moved modules first).

**F7.** CMDSET_REFACTOR §8 open items. Yellow. Three need verification:
account-cmd access cache hit-rate / invalidation walk under Phase 2
caller assignment (`cmd_access_cache.py:136-143`); `arg_regex`
deprecation candidate count post-token-boundary matching; EvMore "q"
re-test after Phase 3 prefix-strip removal.

**F16.** Belief-coverage tests. Green. Only
`.agents/tools/cmdset_prefix_audit.py` enforces a belief (`@`-prefix
convention) via CI. Other beliefs (lane-2 opt-out, model patterns,
cache contracts) live entirely as prose. Identify which beliefs admit
mechanical enforcement; add audits modeled on the prefix audit. Out of
scope: beliefs that require human judgement.

**F17.** `@patch("dotted.path")` audit in engine tests. Yellow.
Path-based patches break on relocation; downstream's `+underspire.16`
sweep rewrote 14 sites in one contrib test file alone. Engine tests
likely carry similar at scale. Mechanical replace with
`@patch.object(module, "name")`. See
[`testing.md`](testing.md) "Mocking guidance."

---

## Audit (needs read before acting)

**F9.** Big modules. Green. Largest non-test engine files (LOC):
building 4622, objects 3822, utils 3156, prototypes/menus 2713,
evennia_launcher 2385, evmenu 2140, default/comms 2126, accounts 2098,
attributes 2015. Read with engine/game rubric; surface lane-3 (game) vs
lane-1 (infra) seams. Suspect first: comms.

---

## Deferred audits (no signal yet)

Named in scope; split out as concrete findings when touched.

- **Two-process boundary leaks.** Portal-side reaching server-side or Django ORM. Spot grep clean; needs focused read.
- **Mock-only test classes** in `evennia/commands/tests.py` (6500+ lines). Spot-check 5-10 for "test exercises only the mock." Relevant to "TDD first" belief.
- **Wider `except *: pass` audit.** 83 sites; hotspots in `evennia_launcher.py`, `utils/utils.py`, `utils/idmapper/models.py`, `cmd_access_cache.py`, `comms/models.py`, `evmenu.py`, `cmdsethandler.py`, `cmdhandler.py`, `building.py`, `attributes.py`, `help/utils.py` (3-6 each).
- **`@property` with side effects** (file-by-file read); **Logging convention** (`log_warn` vs `warning`, `print(` outside CLI; weak standalone); **Settings zombies** (~89 candidates, false-positive-heavy); **Type hints follow-up** on Phase-1-to-4 touched files.

---

## Shipped

- **F1.** Contrib mass extraction. Six contribs (cooldowns,
  name_generator, traits, components, buffs, rpsystem) moved to
  downstream newmoo. Shipped in `+underspire.16`.
