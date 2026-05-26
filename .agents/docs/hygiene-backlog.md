# Hygiene Backlog

Catalogued findings from hygiene passes. Future passes start here.
F-numbers are stable IDs (referenced from changelog entries and other
docs); the order in this file is execution order. Move entries to
**Shipped** when they land; delete if explicitly won't-fix (cite reason
inline).

Severity: red = bug/belief violation; yellow = clear smell; green = polish.
Status: `open` (action recommended), `audit` (needs read before acting).

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
"what's still here and why" doc + maintenance commitment, or (b) start
a deprecation window with target removal release. Limbo isn't an
answer.

**F14.** Refactor-doc archive pattern. Yellow. `CMDSET_REFACTOR.md` (620),
`CMDSET_MIGRATION.md` (661), `PHASE3_AUDIT.md` (129) live at repo root
post-refactor. Mostly archaeology; some content load-bearing (F7
references `CMDSET_REFACTOR §8`). Introduce an archive subdir pattern:
shipped-refactor docs move there, load-bearing items extract to backlog
or live docs.

**F15.** Cache-addition discipline. Green. Seven caches added, zero
removed. Adopt policy: next cache lands with its invalidation-contract
docstring (see F8) and a changelog note defending why caches 1-N should
all still exist. Policy doc, not code.

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
