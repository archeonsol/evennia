# ALPHA: dead-code removal batch

Status: todo (re-verified 2026-06-07; original audit list had drifted)

The audit list was grep-re-verified against the live tree on 2026-06-07. The
tree moved a lot since the audit (CM1, JSONB, metrics work), so the items below
are sorted into three tiers by what's actually true now. **Tier 1 is the only
"easy dead code"; Tier 2 claims are stale (do not remove); Tier 3 are real but
behavior-changing.** (contrib excluded; tests don't count as consumers.)

## Tier 1 — confirmed dead, safe to remove (grep-clean as of 2026-06-07)

Group into reviewable per-cluster commits (utils / server / actions /
typeclasses). Remove dead tests alongside their target.

- **`run_async` + `_PPOOL` / `_PCMD` / `_PROC_ERR`** (`utils/utils.py:1341-1401`)
  — dead ProcPool offload superseded by `utils/defer`; only self-reference
  in-repo.
- **`validate_sessions`** (`server/sessionhandler.py:678`) — deprecated; idle
  timeout moved to `EvenniaServerService.process_idle_timeouts`; no callers.
- **`sessions_from_puppet` / `sessions_from_character`**
  (`server/sessionhandler.py:780-795`) — orphaned by the ControlBinding/bid
  rework; def + alias only, no callers. NB: the sibling `sessions_from_account`
  (`:766`) **is** live (e.g. `accounts/tests.py` patches it) — keep it.
- **`init_new_account`** (`utils/utils.py:1779-1785`) — body is just a
  deprecation log; no callers.
- **`TaskHandler._next_task_id`** (`scripts/taskhandler.py:259,399,555`) —
  written in 3 places, never read (orphaned optimization). Remove the 3
  assignments + the comment.
- **`nomatch_provider`** singular back-compat alias — `actions/default/nomatch.py:56`
  (`nomatch_provider = nomatch_providers`), in `__all__` at `nomatch.py:25` and
  `actions/default/__init__.py:37`, and the import at `__init__.py:13`. Only in
  those lists; remove the alias + entries + import name.
- **`remove_attributes_on_delete`** (`typeclasses/models.py:91`) — body is a
  confirmed no-op (`getattr(instance, "db_attributes", None)` is always None
  post-M2M). Remove the def **and** the `signals.pre_delete.connect(...)` at
  `models.py:185`.
- **South-detection branch** (`utils/utils.py:1421`) — South is irrelevant on
  Django 6; remove the `if "south" in settings.INSTALLED_APPS` branch.
- **`clean_senddata` stale residue** (`server/sessionhandler.py:213-214,233`) —
  commented-out `parse_inlinefunc` line + "picketable" pickle-era docstring;
  comment-only, the live FuncParser call stays.
- **`cumulative_rank_mask`** (`actions/permission.py:239`) — exported
  (`permission.py:75`) and exercised only by `test_permission.py`; no prod
  caller. Remove def + export + the two test cases.

## Tier 2 — STALE claims, do NOT remove (audit list was wrong here)

- **`from_lockstring` / `LegacyLock`** — **not dead.** 30 + 19 refs: the live
  lock-string→predicate transpiler in `predicate.py` still emits `LegacyLock`
  leaves (`return LegacyLock(...)` at `:606-647`), both are public exports in
  `actions/__init__.py`, and `test_predicate.py` covers them. Removing breaks
  the public API and tests. (The `Holds`/`IsSelf` follow-on is moot while this
  stays.)
- **`noinput` branch + "unused middleware"** — **not dead.** The middleware
  system is live: `register_middleware`/`clear_middlewares`/`get_middlewares`
  are exported and the chain is iterated in the dispatch loop
  (`dispatch.py:307,331`); `__noinput__` is a live registered action
  (`parser.py:99`, `registry.py:31`). Nothing clearly dead here.
- **Retired Redis L2 attribute cache** — **already gone.** Zero `redis`/`L2`
  refs in `typeclasses/attributes.py`; removed in a prior commit.

## Tier 3 — real items, but behavior-changing (NOT easy dead code; carve out)

Each needs its own trace/test, not a blind delete. Recommend a separate prompt.

- **WebSocket legacy "no wire_format" fallback + `_send_text_legacy`**
  (`server/portal/webclient.py:471,480-482,545,570`) — reachability is not
  obvious: `wire_format` can be `None` (init `:150`, defensive re-set
  `:280-288`). Prove the `else` branches are unreachable before deleting.
- **`ActionContextBuilder.build` `elif callertype == "account"` branch**
  (`actions/context.py:104`) — this is a **documented** legacy path (docstring
  `:69-71`), not unreachable scaffolding. Removing it is a behavior change.
- **Base `IAttributeBackend._get_cache_key`** (`typeclasses/attributes.py:477`)
  — a correctness *fix* ("broken for the InMemory backend"), not a deletion;
  write a failing test first. If [`A1-attribute-descriptors.md`](A1-attribute-descriptors.md)
  is active, coordinate so this isn't done twice.

## Incomplete-refactor seams — route to their owning track (do NOT fold here)

These are real but belong elsewhere; listed so they aren't lost:

- **Predicate actor-view inconsistency / I1 character seam** — `Holds`/`IsSelf`/
  `IsAlive` (`predicate.py:277,311,344`) read `actor.character` while siblings
  use `actor.effective or actor.character`. → [`I1-actor-abstraction.md`](I1-actor-abstraction.md).
- **Four coexisting schedulers** (global_tick / Script.interval / ondemand /
  defer) + **Script start-delay dual field** (`db_start_delay` bool vs
  `db_start_delay_secs`) → [`AS2-system-scheduler.md`](AS2-system-scheduler.md).
- **help.py half-live formatters** and the **`commands/default/` tree** →
  [`ALPHA-cmdset-retirement-audit.md`](ALPHA-cmdset-retirement-audit.md).
- **`get_all_attributes` / `.all()` N+1 rehydrate** (uncertain, INEFFICIENT) →
  attribute-storage track; needs a measurement before action.

## Approach

Re-confirm each grep at removal time (the tree moves). TDD where a behavior is
asserted; for pure dead code, deletion + green suite is the proof. Format only
touched files. One coherent commit per cluster (actions / server / utils /
attributes).

## Scope boundary

- **In scope:** the Tier 1 removals above.
- **Out of scope:** Tier 2 (stale claims, leave as-is), Tier 3 (behavior changes
  needing their own trace/test), everything in the "route to owning track" list,
  and the cmdset machinery / default command tree (their own track).

## Done means

The Tier 1 surfaces are gone, the suite is green, and no grep finds a dangling
reference.
