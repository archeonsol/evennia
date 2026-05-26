# Hygiene Backlog

Catalogued findings. F-numbers are stable IDs; file order is execution
order. Move to **Shipped** when landed; delete on won't-fix (cite
reason). Severity: red/yellow/green = bug/smell/polish. Status: `open`
(act) or `audit` (read first).

---

## Open (execution order)

**F8.** Cache invalidation contracts. Yellow. Seven caches
(location-cmdset, cmd-access, display-name, lock, write-behind attrs,
trie, redis-attr). Belief requires each name "what fills me / what
invalidates me / staleness bound." Step 1: docstring pass per cache.
Step 2: cross-cache invalidation audit + TTL/size-cap rationalisation.

**F6.** Settings prefix split `CMD_*` vs `COMMAND_*`. Green. One
straggler (`CMD_ACCESS_CACHE_ENABLED`) vs eight `COMMAND_*`. Rename
(breaking, single symbol, Underspire-only consumer) or document
convention in `code-style.md`. Pick one.

**F13.** Contribs half-policy. Yellow. `evennia/contrib/*` is "no new
additions" by stance but still public, tested, shipping. Either
survivors-and-why doc + maintenance, or deprecation window. Limbo
isn't an answer.

**F14.** Refactor-doc archive pattern. Yellow. CMDSET_REFACTOR.md,
CMDSET_MIGRATION.md, PHASE3_AUDIT.md live at repo root post-refactor;
mostly archaeology, some load-bearing (F7 cites §8). Archive subdir.

**F15.** Cache-addition discipline. Green. Seven caches added, zero
removed. Policy: next cache lands with invalidation-contract docstring
(F8) + changelog note defending why caches 1-N still all exist.

**F3.** Doc rot in `docs/source/`. Yellow. 31 hits for removed APIs
(`MuxCommand`, `MuxAccountCommand`, `CMD_IGNORE_PREFIXES`). Sweep after
the newmoo `engine-16-adopt-contribs` PR merges (most contrib doc rot
dies with the moved modules first).

**F7.** CMDSET_REFACTOR §8 items. Yellow. Three verifications:
account-cmd access cache hit-rate / invalidation walk under Phase 2
(`cmd_access_cache.py:136-143`); `arg_regex` deprecation candidate
count; EvMore "q" re-test post-prefix-strip.

**F16.** Belief-coverage tests. Green. Only `cmdset_prefix_audit.py`
enforces a belief via CI; lane-2 opt-out, model patterns, cache
contracts live as prose. Add prefix-audit-style checks for beliefs
that admit mechanical enforcement.

**F17.** `@patch("dotted.path")` audit. Yellow. Path-based patches
break on relocation; `+underspire.16` partner rewrote 14 sites in one
contrib test. Engine likely similar at scale. See [`testing.md`](testing.md).

**F18.** Unused engine handlers. Yellow. `MONITOR_HANDLER`,
`ON_DEMAND_HANDLER` persist/restore empty state every reload; MSDP
inputfuncs load but no Underspire client speaks MSDP. Settings-gate
or document "kept for future webclient/MSDP work."

**F19.** Unused Django apps in `INSTALLED_APPS`. Yellow.
`django.contrib.flatpages` (zero usage), `django.contrib.admindocs`
(one `/doc/` admin URL, likely unhit). Remove with fake-apply
migration discipline.

**F20.** Prototype `exec` key. Yellow. `prototypes/spawner.py:874`
runs arbitrary Python from the `exec` key; `building.py` gates on
Developer. Either rip exec out (spawn hooks belong on typeclasses)
or document the gate as permanent. Surfaced from F5.

**F21.** Dead `caller.db._menutree` branch. Green.
`contrib/utils/fieldfill/fieldfill.py:253-270` reads a persistent
attr nothing writes. Pre-existing; surfaced from F5.

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

- **F1.** Contrib mass extraction — six contribs moved to downstream newmoo (cooldowns, name_generator, traits, components, buffs, rpsystem). Shipped in `+underspire.16`.
- **F2.** `server/deprecations.py` shrunk 188 → 88 LOC. Pre-1.0 setting-rename breadcrumbs (CMDSET_DEFAULT, BASE_COMM_TYPECLASS, *_TYPECLASS_PATHS, INLINEFUNC_*, PROTFUNC_MODULES, TIME_*_PER_*, etc.) deleted; surviving checks (WEBSERVER_PORTS shape, CHANNEL_CONNECTINFO type, template/static_overrides dir renames, MULTISESSION coherence) kept. Plus `prototypes/prototypes.py` docstring fix for `PROTFUNC_MODULES` rename and rewrite of `TestDeprecations` for the smaller surface. Shipped in `+underspire.19`.
- **F12.** Lane-2 belief test now accepts subclass-override as clean opt-out for structural/naming conventions (Phase 3 `@`-prefix isn't a violation). Two-sentence edit to `core-beliefs.md`. Shipped in `+underspire.19`.
- **F11.** Killed (won't-fix). Premise was wrong: Phase 2's `_normalize_account_command_caller` is explicitly no-op for ordinary `Command` subclasses (per `+underspire.3` changelog), so the 10 cited `caller.account` reads in default object commands have no `self.account` to fall back to. The reads are correct as-is.
- **F4.** Bare excepts in engine narrowed to typed (`(AssertionError, IndexError)` in `web/website/views/help.py`, `ValueError` in `utils/utils.py:str2int`). Four sites total; backlog entry under-counted utils.py. Wider 83-site `except *: pass` audit still deferred. Shipped in `+underspire.18`.
- **F5.** Past-due TODO markers — five sites cleared. `Channel.*` deprecation stubs deleted; `at_pre_drop` missing-lock escape removed; `building.py` exec-gate comment corrected (real removal → F20); `evmenu.py` `ndb._menutree` alias removed, all in-tree consumers (prototypes OLC menu + tests, evscaperoom, fieldfill, tree_select, character_creator, evmenu tests/example) migrated to `ndb._evmenu`. Surfaced F20, F21. Shipped in `+underspire.17`.
