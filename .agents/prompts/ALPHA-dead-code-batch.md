# ALPHA: dead-code removal batch

Status: todo (low-risk; the easy one)

All items below were grep-verified during the alpha audit as having **zero live
in-repo consumers** (contrib excluded; tests don't count as consumers). No
external API promises, so removal is fair game. Group into a few reviewable
removal commits.

## Confirmed-dead removals

- **`run_async` + `_PPOOL` / `_PCMD` / `_PROC_ERR`** (`utils/utils.py:1341-1401`)
  — dead ProcPool offload superseded by `utils/defer`; only self-reference
  in-repo. (It also force-attaches an errback even when `errback is None`.)
- **`validate_sessions`** (`server/sessionhandler.py:678-699`) — deprecated;
  idle timeout moved to `EvenniaServerService.process_idle_timeouts`; no callers.
- **`sessions_from_puppet` / `sessions_from_character`**
  (`server/sessionhandler.py:780-795`) — orphaned by the ControlBinding/bid
  rework; no callers.
- **`remove_attributes_on_delete`** pre_delete handler — permanent no-op after
  M2M `Attribute` removal.
- **`from_lockstring` / `LegacyLock` transpiler** (`evennia/actions/`) —
  migration-only scaffolding, zero consumers. NB: this emits the `Holds` /
  `IsSelf` predicate leaves — once it is gone, confirm those leaves
  (`predicate.py`) are also unused and remove them together (they had no
  `requires=` site either).
- **`noinput` dead no-op branch + unused middleware** in the dispatch bridge
  (`evennia/actions/`).
- **`cumulative_rank_mask`** — exported, never called.
- **`nomatch_provider`** singular back-compat alias (`actions/default/nomatch.py:25`,
  re-exported in `actions/default/__init__.py:37`) — only in the two `__all__`
  lists.
- **`ActionContextBuilder.build` `elif callertype == "account"` branch**
  (`actions/context.py:104`) — unreachable in production (the bridge always
  passes `action_type`); test-only. Collapse to one account-injection rule.
- **WebSocket legacy "no wire_format" fallback branches + `_send_text_legacy`**
  (`server/portal/webclient.py`) — unreachable.
- **`init_new_account`** (`utils/utils.py:1779-1785`) — body is just a
  deprecation log; no callers.
- **South-detection branch** (`utils/utils.py:1421`) — South is irrelevant on
  Django 6.
- **`TaskHandler._next_task_id`** — written in 3 places, never read (orphaned
  optimization).
- **`clean_senddata` stale residue** (`server/sessionhandler.py:213-214,233`) —
  commented-out `parse_inlinefunc` line + "picketable" pickle-era docstring;
  comment-only, the live FuncParser call stays.

## Removable dead paths from the M2M→JSONB transition

- **Retired Redis L2 attribute cache still imported and fired on every
  delete/flush** (`typeclasses/attributes.py`) — remove the dead wiring.
- **Base `IAttributeBackend._get_cache_key` still uses M2M through-table
  indirection** (broken for the InMemory backend) — fix to the JSONB reality.

  Both touch attribute storage; if [`A1-attribute-descriptors.md`](A1-attribute-descriptors.md)
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

- **In scope:** the confirmed-dead and M2M-transition removals above.
- **Out of scope:** everything in the "route to owning track" list; the cmdset
  machinery and default command tree (their own track).

## Done means

The listed surfaces are gone, the suite is green, and no grep finds a dangling
reference.
