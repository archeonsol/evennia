# CM1 Action System — Implementation Roadmap

Status: **Phase 1–8 complete: engine bridge is the only player dispatch path; status gates on StateProviders and room rules; player cmdsets are empty anchors in `world/cmdsets/anchors.py`; legacy `commands/*_cmds` tree deleted. Remaining (optional): archive `evennia/commands/cmdset.py` substrate to `legacy/` and retire `CmdSet` from `evennia/__init__.py`.**

Replaces `CM1-cmdset-rethink.md`. This document is the authoritative design
and implementation plan for replacing Evennia's cmdset model with a
typed-action + rule-phase dispatch system modelled on Inform 7's action
machinery.

---

## Purpose and Goal

This rewrite is not a bug fix. The goal is the best command-dispatch engine
achievable in Python — typed, composable, target-centric, reactive, and fully
introspectable. The cache-invalidation bugs in the current model are a symptom,
not the target. The target is a qualitatively more capable substrate.

Accepted tradeoffs going in:

- **Logic scatter**: a verb's behavior is distributed across provider classes
  (target, actor, room, state objects). This is the direct flip side of
  target-centric ownership, not an accident. `explain()` answers "where does
  this live" at runtime. The locality convention (see Pre-Implementation
  Checklist below) answers it while reading code. Knowingly accepted.
- **Feature velocity hit during migration**: two dispatch paths in production
  simultaneously, every command touched. This is a multi-week commitment.
  Justified because the capability gain is not available incrementally and the
  new substrate directly enables future work (ICE, combat, reactive world
  objects, R1 render pipeline).
- **Enforced safety vs. expressive power**: additive `carry_out` is more
  expressive than the cmdset model's forced single-winner but places the
  determinism contract on the author. `CLAIM` + locality convention make this
  manageable. Not a surprise footgun once the convention is in place.

---

## Pre-Implementation Checklist (Hard Gates)

These four decisions and one benchmark must be resolved before the core types
harden. Making them after porting begins is expensive. **Phase 0 does not ship
until all five are checked.**

### Gate 1 — Performance benchmark

Per-dispatch rule collection (provider iteration + dict lookups) must be
measured against the current merge-cache path under a worst-case scenario:
busy combat room, 10+ objects present, a catch-all `@rule(Action, ...)` on
one state object, transitional string-lock `requires` on several rules.

**Expected outcome**: collection is fast (class-level dict lookups, no set
merge). The residual concern is `requires` predicate evaluation cost,
including string lock re-evaluation, scaled by room occupancy. If regression
is real, design a per-context rule-list cache keyed on
`(frozenset(provider_ids), action_type)` — simpler invariant than the merge
cache — before proceeding.

**Exit criterion**: benchmark numbers in the PR for Phase 0. No assumed
acceptable; measured acceptable.

**Result (CLEARED — proceed cache-free).** Measured via
`world/tests/test_cm1_dispatch_bench.py` (game repo, real engine `CmdSet`
merge + `build_matches` + `Command.access` on the baseline side; faithful
rule-collection prototype on the new side). ns/op, min of 5 rounds × 10k iters,
base char cmdset = 80 cmds, PostgreSQL:

| path | n=5 | n=10 | n=20 |
|---|---|---|---|
| `base_hit` — cmdset merge-cache HIT (steady state) | 100,873 | 104,848 | 119,463 |
| `base_miss` — cmdset cold full merge | 259,141 | 564,269 | 1,205,639 |
| `collect` — new path, no `requires` | 16,807 | 24,061 | 44,125 |
| `callable` — new path, cheap predicate `requires` | 18,454 | 25,327 | 40,714 |
| `strlock` — new path, 1 uncached string-lock `requires` | 110,042 | 116,751 | 130,572 |
| `lockcache` — new path, §1c per-dispatch lock memo | 109,033 | 116,256 | 131,647 |

Findings:

1. **Rule collection is ~4× faster than the *cached* cmdset path and scales
   better.** `base_hit` grows with merged-command count (`build_matches` is a
   linear scan over every command in the merged set, regardless of which wins);
   `collect` grows only with provider count and stays far flatter. The busier
   the room, the wider the gap. No per-context rule-list cache is needed —
   collection already beats the cached baseline, so the Gate 1 fallback design
   is **not** built.

2. **The one cost-parity vector is string-lock `requires`.** `strlock` ≈
   `base_hit` because both models pay exactly one `perm()` lock evaluation
   (~90µs), which dominates and is *identical work* in both. Parity, not
   regression. Directive for Phase 1 / L1: hot `check` rules use cheap callable
   predicates (`collect`/`callable` columns), and L1 `Permission` objects
   (set-membership, ~ns) replace string locks — collapsing the only column
   where the new path is not already far ahead.

3. **No merge cliff.** `base_miss` (cold merge: new room layout, cmdset
   mutation, cache eviction) blows out to 0.56–1.2ms; the new path has no merge
   to invalidate, so it has no equivalent cliff — its worst case is `collect`.
   This is the `at_sync`-class robustness win, now quantified.

4. **`lockcache` ≈ `strlock` (honest null result).** The §1c per-dispatch memo
   only pays off when multiple rules share a lockstring within one dispatch;
   with a single lock it is a no-op. Keep §1c (it is cheap and helps lock-heavy
   dispatches) but do not rely on it — the real lock fix is L1.

Absolute cost (25–130µs/dispatch) is acceptable on both sides for this workload
(≳8k dispatches/sec single-threaded at the worst point); Gate 1 is about the
*shape*, and the shape favors the new path everywhere except the transitional
string-lock case, which L1 removes.

### Gate 2 — Arbitration and phase mutation contracts

`carry_out` is additive by default. Without arbitration, multiple providers
handling the same verb double-execute. The fix is `CLAIM` (see Phase 0d) plus
the locality convention, but these must be *decided and written down* before
any rules are authored.

**Decisions:**

**`CLAIM` result in `carry_out`**: a rule returning `CLAIM` stops further
`carry_out` rules in that phase (like `stopPropagation()`). Primary handler
claims. Reactors do not. Default is all fire; `CLAIM` is opt-in.

**`check` does not arbitrate `carry_out`**: `check` gates whether the action
proceeds at all; any `FAIL` blocks the whole action. It cannot select which
of several `carry_out` handlers runs. These are separate concerns. Do not
conflate them.

**Phase mutation contracts** (explicit, not implied):

| Phase | Side effects | Short-circuit |
|---|---|---|
| `before` | **Allowed.** May mutate, reveal, log. | First `REDIRECT` restarts. First `FAIL` blocks. |
| `check` | **Forbidden.** Pure predicates only. | First `FAIL` stops (don't evaluate further checks after one fails). |
| `carry_out` | Intended. Primary execution. | `CLAIM` stops further `carry_out` in phase. |
| `report` | Intended. Output only. | Never stops. All fire. |

`check` short-circuits on first `FAIL` (not "fire all, collect failures" —
there's no value in evaluating remaining checks once blocked, and side-effect-
free predicates have no state to accumulate).

### Gate 3 — Locality convention

Before porting a single command: every action has exactly one
`__primary_handler__` class. That class owns the canonical `carry_out` and
returns `CLAIM`. All other providers contribute `before`/`check`/`report`
only. Exceptions (secondary `carry_out` for independent additive effects that
don't conflict) must be explicitly documented in the rule.

```python
@action("kick")
@dataclass
class Kick(Action):
    __primary_handler__ = Ball  # Ball owns the canonical kick carry_out
    target: GameObject
```

Not hard-enforced by machinery; enforced by convention and visible in
`explain()` (two `CLAIM` results in one phase is a bug surface). Write this
down as a law in `docs/writing_commands.md` before Phase 7 begins.

### Gate 4 — Menu / yield / disambiguation: worked designs (AS1 settled this)

**AS1 is decided. This gate is no longer a hard blocker — the direction is
fixed and the design follows from it.** AS1 shipped (`6.0.0+underspire.50`) as
**Option 1: sync-by-default + threaded I/O helpers**, not async-first. So the
dispatch loop is synchronous; there is no `await actor.prompt(...)`. The
mid-execution input pattern uses the engine's existing generator-based
`@interactive` machinery (`evennia/utils/utils.py::interactive`, built on
`evmenu.get_input` + `deferLater`). `yield` replaces `await`; the linear-code
ergonomics are identical.

A large fraction of game flow depends on intercepting the next player input
mid-execution: `EvMenu` (chargen, surgery, device menus), deferred-input
commands (confirmations, multi-step flows), and search disambiguation
("1-ball or 2-ball?"). All three resolve through the same mechanism below.

**Design for interactive flows — `@interactive` `carry_out` rules:**

A `carry_out` (or `report`) rule may be an `@interactive` generator. Inside it,
`response = yield "prompt text"` suspends the rule, sends the prompt to the
actor, and resumes with the next line of input the actor types. `yield(n)`
pauses `n` seconds. This is the existing engine pattern; the rule engine reuses
it unchanged — it does **not** block the reactor, it suspends via a Deferred
that fires on the next input.

```python
class DiveRig(NetworkedObject):

    @rule(BeginSurgery, phase="carry_out")
    @interactive
    def carry_out_surgery(self, action, actor):
        patient = action.patient
        actor.character.msg(f"|wSurgery on {patient.key}|n")
        choice = yield "[1] Repair organ  [2] Remove cyberware  [3] Cancel: "
        if choice == "3":
            actor.character.msg("Cancelled.")
            return CLAIM
        if choice == "1":
            organs = patient.get_damaged_organs()
            actor.character.msg("\n".join(f"[{i+1}] {o.name}" for i, o in enumerate(organs)))
            idx = yield "Select organ: "
            organs[int(idx) - 1].repair()
            actor.character.msg("Repaired.")
        return CLAIM
```

For `@interactive` rules, the decorated function must have an arg the engine
can resolve to `caller` for `get_input` (the rule engine passes
`actor.character`/`actor.effective` as that caller; `@rule` registration wires
this so rule authors don't manage it).

**EvMenu compatibility:** `EvMenu`'s node-graph API stays as a thin wrapper.
Existing `EvMenu(caller, menudata)` calls keep working — internally an
`EvMenuState` (a `StateProvider`) captures input via a catch-all `before` rule
and routes to nodes, exactly as the current cmdset-based EvMenu does. New flows
should prefer `@interactive` `carry_out` rules (linear code) over node graphs.
Migration is opportunistic, not forced.

```python
class EvMenuState(StateProvider):
    """Internal. Backs the legacy EvMenu node-graph API under the action engine."""
    def __init__(self, menutree, startnode="start", **kwargs): ...

    @rule(Action, phase="before", priority=9999)
    def capture_input(self, action, actor):
        return REDIRECT(MenuInputAction(raw=action._raw_string, menu=self))
```

**Rule-body execution contract (this is the AS1 boundary, made concrete):**

| Phase | May block? | May `yield`/be `@interactive`? | May `defer.in_thread`? | Must return now? |
|---|---|---|---|---|
| `before` | No | No | No | Yes — sync gate/redirect |
| `check` | **No** | **No** | **No** | **Yes — needs a sync result** |
| `carry_out` | No (use helpers) | **Yes** | **Yes** | No — deferrable |
| `report` | No (use helpers) | **Yes** | **Yes** | No — deferrable |

`check` rules must produce `PASS`/`FAIL`/`SKIP` synchronously — they gate
whether the action proceeds, so they cannot await input or I/O. If a check
needs data that isn't in memory, precompute/cache it (per the AS1 hook-return
boundary). `carry_out`/`report` are deferrable: they may suspend for input
(`@interactive`) or push blocking I/O to a worker thread
(`evennia.utils.defer.in_thread` / `background`), following AS1's
threading-safety contract (worker thread: pure I/O, no game-object access;
deliver results in the reactor-thread callback).

**CLAIM ordering with deferred carry_out:** the `carry_out` phase is
serialized. If a `carry_out` rule is `@interactive` or returns a `Deferred`,
the engine suspends the *entire* `carry_out` phase until that rule completes,
then inspects its result. Only after it resolves (with `CLAIM` or not) does the
engine decide whether to run the next `carry_out` rule. This keeps `CLAIM`
deterministic even across suspension — two rules never interleave.

**Design for disambiguation:**

`Action.parse()` calls `actor.search()` which may produce multiple matches.
`actor.search()` raises `AmbiguousTarget(candidates, original_raw)` instead
of printing inline and returning `None`. The dispatch loop catches
`AmbiguousTarget`, installs a `DisambiguationState(candidates, pending_action)`,
and sends the disambiguation prompt to the actor. On next input,
`DisambiguationState`'s `before` rule resolves the choice, patches the
pending action's target, and re-dispatches. One-shot state; exits on
resolution or cancellation.

```python
class DisambiguationState(StateProvider):
    def __init__(self, candidates, pending_action):
        self._candidates = candidates
        self._pending = pending_action

    @rule(Action, phase="before", priority=9999)
    def resolve_disambiguation(self, action: Action, actor: Actor) -> RuleResult:
        choice = _parse_choice(action._raw_string, self._candidates)
        if choice is None:
            actor.msg("Invalid choice. Cancelled.")
            actor.exit_state(DisambiguationState)
            return SILENT_FAIL
        self._pending.target = choice
        actor.exit_state(DisambiguationState)
        return REDIRECT(self._pending)
```

**Help availability index:**

`ActionRegistry.available_for(actor, context)` does a dry-run check phase
across all registered actions filtered by `actor_type`. Called infrequently
(help, tab-complete). O(n_actions × avg_check_rules). Cache per actor-state-
hash (`frozenset(type(s) for s in actor.state_objects)`); invalidated on
`enter_state`/`exit_state`.

**Exit criterion for Gate 4**: the three worked designs above (EvMenuState +
`@interactive`, yield/deferred-input, disambiguation) must be reviewed and accepted before
Phase 4 begins. If a spike reveals a flaw in any design, fix it before
proceeding.

### Gate 5 — Actor and permission scope ownership

This work owns the shape of `Actor` and the `requires` predicate algebra.
The architecture doc lists I1 (actor abstraction) and L1 (permission objects)
as separate items. That framing is wrong for this work: the `Actor` built in
Phase 3 IS the I1 actor; the predicate system built in Phase 1 IS the
beginning of L1. Do not build minimal versions with a plan to reconcile later
— that guarantees doing it twice.

**Decision**: Phase 3 absorbs I1's initial scope. The Phase 0f
`Capability`-lattice + `Predicate`-algebra absorbs L1's initial scope and is the
typed authoring surface for **code-defined command/action gates** (see 0f /
0f-bis): roles become a `Capability` bitmask resolved fresh per check, relational
gates become composable frozen-dataclass predicate nodes. This does **not** retire
the lock DSL — a string remains the right serialization format for runtime/persisted
per-object locks. Both surfaces resolve against one shared capability model, so a
code gate `requires=Builder` and a persisted `perm(Builder)` agree by construction;
`perm()`/`pperm()` become thin shims over `resolve_capabilities` rather than a forked
permission store. `from_lockstring` (+ `LegacyLock` fallback) is a migration aid for
*code* lockstrings, not a mandate to convert every runtime lock. The architecture
doc's I1 and L1 entries become "outputs of CM1" rather than "dependencies of CM1."
Update `engine-architecture/decisions.md` when these phases ship.

---

## Design Summary

### The core inversion

**Old model:** actor owns command sets → commands tell the world what to do.

**New model:** actor *attempts* a typed action → world objects respond via
rules attached to themselves.

```
raw text
  → ActionParser   (text → typed Action dataclass + resolved targets)
  → RuleEngine     (fires rules in phase order across all relevant objects)
  → result
```

No `CmdSet` class. No merge step. No cached merged state. No priority/
duplicates/mergetype knobs. Commands as string keys disappear; actions are
Python types.

### Action types

```python
@action("look", "l", "examine", "ex")
@dataclass
class Look(Action):
    __primary_handler__ = Character
    target: GameObject | None = None

@action("kick")
@dataclass
class Kick(Action):
    __primary_handler__ = Ball
    target: GameObject
    strength: Literal["soft", "hard"] = "soft"
```

Registered globally at import time. Static registry. Parser maps
`"kick ball hard"` → `Kick(target=<ball>, strength="hard")`. Typos in verb
strings are import-time errors.

### Rule phases

Every action dispatched through four phases in order:

```
before → check → carry_out → report
```

| Phase | Semantics | Side effects | Short-circuit |
|---|---|---|---|
| `before` | Intercept, redirect, transform | Allowed | First REDIRECT restarts; first FAIL blocks |
| `check` | Pure gate predicates | Forbidden | First FAIL stops (short-circuit) |
| `carry_out` | Execution | Intended | CLAIM stops further carry_out in phase |
| `report` | Output/messaging | Intended | Never; all fire |

### Rule results

```python
PASS            # check/before passed; continue
FAIL("msg")     # blocked; send msg to actor; stop dispatch
SKIP            # rule doesn't apply (guard not met); continue to next
REDIRECT(action)# replace action and re-dispatch (before phase only)
CLAIM           # carry_out executed and claimed; stop further carry_out
SILENT_FAIL     # blocked; no message
```

### Rules declared on objects

```python
class Ball(DefaultObject):

    @rule(Kick, phase="check")
    def check_kick(self, action: Kick, actor: Actor) -> RuleResult:
        if actor.location != self.location:
            return FAIL("You can't reach it.")
        return PASS  # pure predicate; no side effects

    @rule(Kick, phase="carry_out")
    def carry_out_kick(self, action: Kick, actor: Actor) -> RuleResult:
        dest = random.choice(list(self.location.exits.values())).destination
        self.move_to(dest)
        return CLAIM  # Ball is primary handler; stop here
```

### What replaces cmdset patterns

| Old pattern | New pattern |
|---|---|
| `FlatlinedCmdSet` (Replace, priority 200) | `FlatlinedState` — `check` rules FAIL everything except `Look` |
| `GrappledCmdSet` (priority 180) | `GrappledState` — `check` rules |
| `UnconsciousCmdSet` | `UnconsciousState` |
| `SplinterPodCmdSet` | `SplinterPodState` |
| `CombatGrappleCmdSet` | `CombatState` |
| `add FishingCmdSet on pickup` | FishingRod `before` rule; `SKIP` unless held |
| Dark room prevents look | Room `check` rule for `Look` returns `FAIL` |
| `EvMenu` replacement cmdset | `EvMenuState` wrapper (legacy node graph) or `@interactive` `carry_out` rule (new linear flows) |
| Disambiguation `CMD_MULTIMATCH` | `DisambiguationState` |
| `perm(Builder)` lock string (code-defined gate) | `@rule(..., requires=Builder)` — `Capability` bit, resolved fresh per check; introspectable via `describe()`/`unmet()` |
| `AccountCommand` | `@rule(..., actor_type=Account)` |
| `allow_multipuppet_relay = False` | `@rule(..., relay=False)` |
| Merge cache | Eliminated — no cross-object merged state |
| `at_sync` cache invalidation bug | Impossible — nothing to invalidate |

---

## Phase 0 — Foundation

*Build the core types. Nothing game-facing yet. All existing tests pass.*
*All five Pre-Implementation Gates must be checked before this phase ships.*

**Status: foundation type layer BUILT** on branch `cm1-action-system` (123 tests
passing via `evennia test evennia.actions.tests`; no game-facing changes). Built:
`__init__.py` (flat re-exports), `exceptions.py`, `result.py` (0d/0e),
`permission.py` + `predicate.py` (0f/0f-bis), `rule.py` + `registry.py` + `action.py`
(0b/0c), and `engine.py` + `context.py` (Phase 1 complete, incl. 1g async/interactive
driving). Deferred to their natural later phases (each needs machinery that doesn't
exist yet): `actor.py` (→ Phase 3a),
`state.py` (`StateProvider` → Phase 3c), `menus.py` (`EvMenuState`/`Disambiguation`
→ Phase 3e/Gate 4). The Phase-0 deliverable is the *type substrate* every later
phase builds on; the loop and integrations are not Phase-0 work.

### 0a. Package skeleton

```
evennia/actions/
    __init__.py          flat re-exports                                  [BUILT]
    action.py            Action base, @action decorator                   [BUILT]
    rule.py              @rule decorator, RuleSpec, PHASES                 [BUILT]
    registry.py          ActionRegistry + RuleRegistry singletons         [BUILT]
    engine.py            RuleEngine — phases, dispatch, trace             [Phase 1]
    actor.py             Actor context object (exposes .effective/.account;
                         capabilities resolved fresh, never cached)       [Phase 3a]
    result.py            RuleResult, FAIL/REDIRECT, ActionTrace,PhaseTrace [BUILT]
    permission.py        Capability IntFlag lattice; grant/resolve helpers [BUILT]
    predicate.py         Predicate algebra, leaves, from_lockstring,Legacy [BUILT]
    state.py             StateProvider base, enter_state/exit_state       [Phase 3c]
    exceptions.py        ActionError, RuleConflict, AmbiguousTarget,Parse  [BUILT]
    menus.py             EvMenuState, MenuInputAction, DisambiguationState [Phase 3e]
```

**Implementation notes (built layer):**

- `RuleResult.redirect_to` is `compare=False, hash=False` so `REDIRECT(...)`
  results stay hashable/comparable even when the wrapped action is not.
- `@rule` supports **stacking** (multiple decorators on one method) and tuple
  `action_type`; `requires` is compiled to a `Predicate` once at decoration and a
  lock *string* raises `TypeError`.
- `RuleRegistry.collect` walks the MRO; a subclass method definition (even without
  `@rule`) **shadows** the base method's rules. Catch-all rules (`@rule(Action,
  ...)`, detected via an own-dict marker on the base to avoid an import cycle) are
  stored per-phase and merged into every `rules_for(cls, action_type, phase)`
  lookup, which is **memoized per provider class** (`__evennia_rules_cache__`).
- `Action` base carries `_actor`/`_raw_string`/`_trace` as `init=False` fields so
  subclasses may declare required fields without the dataclass ordering error.

### 0b. `Action` base class

```python
@dataclass
class Action:
    """Base for all typed actions."""
    __primary_handler__: ClassVar[type | None] = None
    _actor: Actor = field(default=None, init=False, repr=False)
    _raw_string: str = field(default="", init=False, repr=False)
    _trace: ActionTrace = field(default=None, init=False, repr=False)
```

`@action(*verbs)` decorator: registers on `ActionRegistry`, attaches
`.__action_verbs__`. Duplicate verb registration raises `RuleConflict` at
import.

### 0c. `@rule` decorator

```python
@rule(
    action_type,             # Action subclass or tuple of them
    phase="carry_out",       # before | check | carry_out | report
    priority=0,              # within-phase ordering; higher fires first
    actor_type=None,         # Character | Account | None (any)
    requires=None,           # callable(action, actor) -> bool | Predicate | str lock
    relay=True,              # multipuppet relay allowed
    help_category=None,      # for help index
)
def my_rule(self, action, actor) -> RuleResult: ...
```

`RuleRegistry.collect(cls)` walks MRO, indexes `(action_type, phase)` →
sorted list of `RuleSpec`. Called at class definition time via metaclass or
explicit `register_rules(cls)`. Catch-all rules (`action_type=Action`) are
indexed separately and merged into every lookup result.

### 0d. `RuleResult` types

```python
@dataclass(frozen=True)
class RuleResult:
    kind: Literal["pass", "fail", "skip", "redirect", "claim", "silent_fail"]
    message: str | None = None
    redirect_to: Action | None = None

PASS         = RuleResult("pass")
SKIP         = RuleResult("skip")
CLAIM        = RuleResult("claim")
SILENT_FAIL  = RuleResult("silent_fail")

def FAIL(msg: str) -> RuleResult: ...
def REDIRECT(action: Action) -> RuleResult: ...
```

### 0e. `ActionTrace` — baked-in debuggability

```python
@dataclass
class PhaseTrace:
    phase: str
    provider_key: str
    provider_dbref: int | None
    rule_name: str
    result: RuleResult
    elapsed_us: int

@dataclass
class ActionTrace:
    action: Action
    actor_key: str
    phases: list[PhaseTrace]
    outcome: Literal["succeeded", "blocked", "no_rules", "redirected"]
    block_message: str | None
```

Every dispatch produces a full `ActionTrace`. `actor.explain(Look)` reruns
in `dry_run=True` mode and returns the trace. Output:

```
> explain kick
kick (target: ball#34)
  [before]  FishingRod#12.intercept_throw   SKIP  (rod not held)
  [check]   Ball#34.check_kick              PASS
  [check]   GatekeeperState.check_alive     PASS
  [carry_out] Ball#34.carry_out_kick        would CLAIM
  [report]  Ball#34.report_kick             would fire
Outcome: would succeed
```

### 0f. Capability lattice + `Predicate` algebra (typed code-defined gates)

**Status: BUILT** on branch `cm1-action-system` —
`evennia/actions/permission.py` + `evennia/actions/predicate.py`, with
`evennia/actions/tests/test_permission.py` + `test_predicate.py` (passing via
`evennia test evennia.actions.tests`). Implementation deltas from the spec below,
all deliberate:

- **Toolkit-not-game enum split (S2).** The engine ships an **empty, member-less
  `Capability(IntFlag)` base** (the one enum shape that stays subclassable) plus a
  concrete `DefaultCapability(Capability)` reference lattice covering the full
  `PERMISSION_HIERARCHY` (`GUEST, PLAYER, HELPER, BUILDER, ADMIN, DEVELOPER` +
  cross-cutting `MODERATE, IMPERSONATE`, `@verify(CONTINUOUS)`). A game declares its
  own lattice by subclassing the base and setting `settings.CAPABILITY_ENUM` (dotted
  path); `get_capability_enum()` resolves it, defaulting to `DefaultCapability`. Rank
  order lives on the enum as `__rank_order__`; aliases as `__name_aliases__`.
- **Fresh, scope-aware resolution (S3).** There is **no precomputed
  `actor.capabilities`** — a frozen mask reintroduces exactly the cached-derived-state
  staleness CM1 exists to kill, and a single int can't express quell or multi-scope.
  `resolve_capabilities(obj, scope=Scope.EFFECTIVE, enum=None)` recomputes the mask
  fresh on every check from the object's in-memory permission tags (no DB round-trip
  normally), mirroring the `perm()` lockfunc (rank-implies-lower, quell takes the
  lower of account/puppet, `Scope` selects whose perms). Helpers: `capability_for_name`
  (name→bit, case/plural/alias tolerant), `cumulative_rank_mask`, `rank_order`.
- **`HasCapability(cap, scope=Scope.EFFECTIVE)`** resolves fresh per call;
  `cost = 1` (an in-memory tag read, honest — not 0, still cheaper than attr/DB leaves).
- **`IsAlive` / `InSameRoom` ship as pre-instantiated singletons** (parameterless
  leaves). Engine-default `IsAlive` reads `character.db.alive` defaulting True so a
  character with no life model counts as alive; games override with their own leaf.
- **`Not.cost` is a property** returning `inner.cost`; `~~p` and `~Const` simplify.
  `coerce_predicate` is the public coercion entry point; the interning cache requires
  all leaves be hashable.

This subsection absorbs L1's initial scope (Gate 5). It is the typed authoring
surface for **code-defined command/action gates**: Evennia's lock DSL
(`obj.access()` against `db_lock_storage`) is a stringly-typed, runtime-parsed gate
system, and **a lock is just a `check` rule whose body is a predicate.** For code
gates we author that body as typed, composable, introspectable predicate nodes
instead of a string. We do **not** retire the DSL — a string is the correct
serialization format for runtime/persisted per-object locks, and that stays. Both
surfaces resolve against the one shared capability model, so `perm()`/`pperm()`
become thin shims over `resolve_capabilities` (don't fork permission state, only
the authoring surface).

The headline payoff is **introspectability, not speed**: a predicate tree is data,
so `describe()` renders it for help/UI, `unmet()` names the first failing clause for
an actionable error, and a proactive UI can grey out a disabled verb before the
player tries it. The type checker becomes the lock linter. (The Gate 1 benchmark's
string-lock column is no longer framed as a "must beat" target — fresh capability
resolution is an in-memory tag read, comfortably cheap, and the win we're buying is
the typed, inspectable gate, not nanoseconds.)

Bleeding-edge-but-right-sized: stdlib only, no new dependency, no homegrown
expression DSL (that's just reinventing the lock string), no JIT/bytecode, no async
predicates (`check` is synchronous per the AS1/CM1 contract). The modern leverage is
`IntFlag`, frozen+slotted dataclass nodes, and end-to-end static typing.

**`permission.py` — the privilege lattice (shared with `perm()`):**

The engine ships an empty base (the extension point) and a concrete default the
game can fork via a setting:

```python
from enum import IntFlag, Enum, auto, verify, CONTINUOUS

class Scope(Enum):
    ACCOUNT = "account"; PUPPET = "puppet"; EFFECTIVE = "effective"

class Capability(IntFlag):
    """Empty base — member-less so it stays subclassable. Games extend this."""

@verify(CONTINUOUS)
class DefaultCapability(Capability):       # active default + copy-this reference
    NONE = 0
    GUEST = auto(); PLAYER = auto(); HELPER = auto()
    BUILDER = auto(); ADMIN = auto(); DEVELOPER = auto()
    MODERATE = auto(); IMPERSONATE = auto()   # cross-cutting, orthogonal to rank

DefaultCapability.__rank_order__ = (GUEST, PLAYER, HELPER, BUILDER, ADMIN, DEVELOPER)
STAFF = DefaultCapability.BUILDER | DefaultCapability.ADMIN | DefaultCapability.DEVELOPER
# settings.CAPABILITY_ENUM (dotted path) → get_capability_enum(); default DefaultCapability
```

Hierarchy ("Admin implies Builder") is encoded when the mask is **resolved** — the
returned mask already includes every lower rank bit — so the *check site* does zero
hierarchy walking, but the resolve is fresh each call (no stored mask to go stale):

```python
Capability.BUILDER in resolve_capabilities(actor.effective)   # one int AND, fresh
```

A stored mask is allowed only as a deliberate opt-in cache with explicit
single-writer invalidation — the thing CM1 warns about, entered on purpose.

**`predicate.py` — the relational/contextual algebra (replaces `holds`/`tag`/`attr`/…):**

Predicate nodes are frozen, slotted dataclasses, so the tree is *data*
(introspectable, hashable, internable) — not an opaque closure.

```python
class Predicate:
    __slots__ = ()
    cost: int = 0                        # 0=cap bit  1=identity  2=attr  3=db query
    def __call__(self, action: Action, actor: Actor) -> bool: ...
    def __and__(self, other): return And._build(self, _coerce(other))
    def __or__(self, other):  return Or._build(self, _coerce(other))
    def __invert__(self):     return Not(self)
    def describe(self) -> str: ...                 # human text for help/UI
    def unmet(self, action, actor) -> "Predicate | None": ...  # first failing leaf

@dataclass(frozen=True, slots=True)
class HasCapability(Predicate):
    cap: Capability
    scope: Scope = Scope.EFFECTIVE
    cost = 1                              # in-memory tag read, resolved fresh
    def __call__(self, a, actor):
        return self.cap in resolve_capabilities(actor.effective, scope=self.scope)
    def describe(self): return f"requires {self.cap.name.title()}"

@dataclass(frozen=True, slots=True)
class Holds(Predicate):
    cost = 2
    def __call__(self, a, actor): return a.target in actor.character.contents
    def describe(self): return "must be holding it"

@dataclass(frozen=True, slots=True)
class And(Predicate):
    parts: tuple[Predicate, ...]
    def __call__(self, a, actor): return all(p(a, actor) for p in self.parts)
    @classmethod
    def _build(cls, *preds):
        # flatten nested Ands; sort by .cost asc so cheap bits short-circuit first
        flat = tuple(sorted(_flatten(And, preds), key=lambda p: p.cost))
        return flat[0] if len(flat) == 1 else cls(flat)
# Or analogous (cost-asc; first truthy wins). Not wraps a single predicate.

# ergonomic singletons — authors never touch the classes directly
Builder    = HasCapability(Capability.BUILDER)
Admin      = HasCapability(Capability.ADMIN)
ALWAYS     = _Const(True)
NEVER      = _Const(False)
IsAlive    = ...   # attr leaf, cost=2
InSameRoom = ...   # identity leaf, cost=1
HasTag     = lambda key: ...   # db leaf, cost=3
```

Authoring is pure, type-checked Python — typos are import errors, pyright
autocompletes, refactors are safe:

```python
@rule(OpenDoor, phase="check", requires=Builder | Admin)
@rule(Unlock,   phase="check", requires=Builder & Holds())
@rule(Kick,     phase="check", requires=~Grappled)
```

**Compile-once cost ordering:** `And._build`/`Or._build` flatten and sort leaves
by `.cost` ascending at construction (the node is frozen, so this happens once),
so `Builder & Holds() & HasTag("vip")` checks the cheap in-memory capability leaf
before the DB tag lookup and fails fast. Identical `requires` expressions intern to one
shared node. There is **no per-dispatch parsing — ever** (the whole defect of
the lock string).

**`requires` contract.** `@rule(..., requires=...)` accepts a `Predicate`, a
`Capability` (coerced to `HasCapability`), or a bare callable
`(action, actor) -> bool` (coerced to a `cost=3` leaf). It does **not** accept a
lock string. The compiled predicate is stored on the `RuleSpec` at registration;
Phase 1c evaluates it (and the §1c per-dispatch memo keys on the predicate node,
not a lockstring).

### 0f-bis. Lock-string transpiler + `LegacyLock` fallback (migration safety)

**Status: BUILT** (in `predicate.py`). `from_lockstring(lockstring, access_type)`
extracts the requested access-type clause (bare `"perm(Builder)"` or prefixed
`"cmd:perm(Builder)"`, `;`-separated multi-clause supported), tokenizes it, and
parses with a recursive-descent parser honoring Python precedence (`not > and >
or`). Known funcs map to leaves (`perm/pperm`→`HasCapability`, `perm_above`→next
rank up, `holds`→`Holds`, `self`→`IsSelf`, `id/dbref`→`IsObject`, `tag`→`HasTag`,
`attr`→`HasAttr`, `true/all`→`ALWAYS`, `false/none`→`NEVER`); a `perm()` with a
non-capability role, or any unknown func, becomes `LegacyLock(lockstring,
access_type)`. `LegacyLock.__call__` re-enters `evennia.locks.lockhandler.
check_lockstring` against `actor.effective` and logs a once-per-lockstring
warning. No clause for the requested access type → `NEVER`.

To retire existing `db_lock_storage` without a risky big-bang:

```python
def from_lockstring(lockstring: str, access_type: str) -> Predicate:
    """Transpile one Evennia lock string (for a given access_type) into a
    predicate tree. Known lockfuncs map cleanly:
        perm()/perm_above() -> HasCapability   true()/false() -> ALWAYS/NEVER
        holds()             -> Holds           self()         -> IsSelf
        tag()               -> HasTag          attr()/attr_*  -> HasAttr
        id()/dbref()        -> IsObject        ...
    Unknown / custom game lockfuncs -> LegacyLock(lockstring): falls back to the
    existing lock evaluator and logs a once-per-lockstring warning, so
    transpilation NEVER silently drops semantics. Flagged sites get hand-ported."""
```

`LegacyLock` is a migration aid for *code* lockstrings, not a mandate to convert
every runtime lock. The known lockfunc set converts mechanically; anything custom
keeps working via the existing evaluator and surfaces itself with a once-per-string
warning so flagged code sites can be hand-ported to predicates.

**Scope note (honest boundary, S1).** What Phase 0f replaces is the *authoring
surface for code-defined gates* — the `cmd:`-style `requires` written in Python.
The lock **DSL is retained** as the serialization format for runtime/persisted
per-object locks (the one job a string is good at): a builder typing a lock at
runtime, web-admin lock fields, and `db_lock_storage` keep using strings. Because
both surfaces resolve against the one shared `Capability` model, `perm()`/`pperm()`
are reimplemented as thin shims over `resolve_capabilities` — same permission state,
two authoring surfaces, no fork. `from_lockstring` migrates code lockstrings (and
can opportunistically convert other access types — `traverse`/`get`/`control`/… —
into `check` rules where that's the right move, Phases 6–7), but full `LockHandler`
retirement is **not** a goal; the DSL stays for the persisted-lock job.

### 0g. Tests for Phase 0

- `ActionRegistry` registers verbs; duplicate raises `RuleConflict`.
- `@rule` attaches specs; `RuleRegistry.collect` indexes correctly including
  catch-all (`Action`) rules.
- `CLAIM` is frozen; `FAIL("x").message == "x"`.
- `ActionTrace` records all phases.
- `Capability` is a `CONTINUOUS` `IntFlag`; `BUILDER in (BUILDER|ADMIN)` is True;
  membership check does not touch the DB.
- `Predicate` `&`/`|`/`~` operators compose correctly.
- `And._build` flattens nested `And`s and sorts leaves by `.cost` ascending
  (cheap capability bit evaluated before a `cost=3` db leaf); short-circuits.
- Identical `requires` expressions intern to one node (`is` identity).
- `Predicate.unmet()` returns the first failing leaf; `describe()` renders text.
- `requires=` coerces a `Capability` to `HasCapability` and a bare callable to a
  `cost=3` leaf; a lock *string* is rejected (`TypeError`, not silently parsed).
- `from_lockstring("perm(Builder)", "cmd")` → `HasCapability(BUILDER)`;
  `from_lockstring("holds()", "cmd")` → `Holds`; an unknown lockfunc →
  `LegacyLock` that falls back to the old evaluator and logs once.
- `EvMenuState` catch-all before rule redirects all input to `MenuInputAction`.
- `DisambiguationState` resolves on valid choice, cancels on invalid.

---

## Phase 1 — Rule Engine

*Core dispatch. Cmdsets untouched. Benchmark runs here (Gate 1).*

**Status: SHIPPED (1a–1g).** `evennia/actions/engine.py` (`RuleEngine`, `engine`)
+ `evennia/actions/context.py` (`ActionContext`) implement all of Phase 1;
`Predicate.eval(action, actor, memo)` adds the per-dispatch leaf memo (1c). 26
tests in `tests/test_engine.py` (123 total in the package, all green).

* **before / check** are strictly synchronous; a body that returns a
  generator/`Deferred` raises `ActionError` (`_coerce_sync`).
* **carry_out / report** are `@inlineCallbacks` loops (`_run_phase`) that `yield`
  on every rule, so a suspended rule pauses the *whole* phase (the CLAIM-ordering
  guarantee — no interleaving). `_eval_rule_async` drives three body shapes: plain
  `RuleResult`, a generator (the `@interactive` shape — driven by
  `_drive_generator`, which interprets `yield <str>` as a `get_input` prompt,
  `yield <num>` as a `_sleep` pause, `yield <Deferred>` as an await, and coerces
  the generator's `return` value), and a `Deferred`-returning body.
* **`dispatch` returns `Deferred[ActionTrace]`** — already fired (`.called`) when
  nothing suspends, so the all-synchronous path stays effectively synchronous.

1g built its own generator driver because `evennia.utils.utils.interactive` is
fire-and-forget (its `_iterate` swallows `StopIteration` and returns no completion
Deferred); `_drive_generator` reuses the same `get_input` + `deferLater`
primitives but returns a Deferred that fires with the body's return value, which
the phase loop needs to resume deterministically.

Note (footgun): the package re-exports the `engine` *instance*, which shadows the
`engine` submodule attribute — `import evennia.actions.engine` binds the instance,
not the module. Reach the module via `sys.modules["evennia.actions.engine"]` (as
the tests do) when you need to patch a module-level symbol.

### 1a. `RuleEngine.dispatch(action, actor, context)`

```python
def dispatch(
    self,
    action: Action,
    actor: Actor,
    context: ActionContext,
    dry_run: bool = False,
) -> ActionTrace:
```

`ActionContext` carries the ordered provider list:

```
providers = [
    actor.state_objects,          # states first — highest gate priority
    actor.character (or account), # actor's own rules
    actor.equipped_items,         # item before-rules (intercept, not gate)
    actor.location,               # room rules
    action.targets,               # direct target(s)
    actor.location.contents,      # other room objects
]
```

**Phase execution:**

1. **`before`** — collect rules from all providers, sort by priority desc.
   Fire in order. May have side effects. First `REDIRECT`: restart dispatch
   with new action (increment redirect counter). First `FAIL`: block.
   All others continue. `SKIP`: skip this rule, continue.

2. **`check`** — collect rules from all providers. Pure predicates, no side
   effects. Fire in priority order. **Short-circuit on first `FAIL`**: send
   message to actor, return trace with `outcome="blocked"`. Do not fire
   remaining check rules after a failure.

3. **`carry_out`** — collect rules from all providers. Fire in priority order.
   `CLAIM` stops further `carry_out` rules. Exceptions logged and flagged in
   trace; do not stop other rules unless explicitly claimed.

4. **`report`** — collect all rules. Fire all. No short-circuit.

### 1b. Rule collection and indexing

Rule lookup per provider: `provider.__class__.__evennia_rules__[(action_type,
phase)]`. Class-level dict built by `RuleRegistry.collect` at class definition.
Catch-all rules (`action_type=Action`) stored separately; merged into every
lookup result at collection time, not dispatch time.

For catch-all rules on state objects with `priority >= 900`, these act as
universal gates (FlatlinedState, etc.). Their priority ensures they sort
before any normal rule in `check` phase, so they short-circuit early.

**Benchmark target** (Gate 1): CLEARED. Measured rule collection is ~4× faster
than the cached-cmdset path and scales better with room occupancy (see Gate 1
Result table). Proceed cache-free; the per-context rule-list cache is not built.
The one historical cost-parity vector was uncached string-lock `requires`; typed
predicates resolve that as an in-memory capability read (no runtime parse), so it is
no longer a parity concern — and the win we bought is the typed, inspectable gate,
not nanoseconds. Re-run `world/tests/test_cm1_dispatch_bench.py` if the collection
inner loop changes materially.

### 1c. `requires` evaluation

Evaluated before the rule body. The `requires` on a `RuleSpec` is a compiled
`Predicate` (Phase 0f): a `Capability` bit, a relational leaf, an `And`/`Or`/`Not`
tree (cost-ordered, cheap leaves first), or a coerced callable. If
`requires(action, actor)` is False → `SKIP`. No string parsing occurs at dispatch
time — the predicate was built once at registration.

Per-dispatch memo (the cost-3 / DB-leaf optimization): cache leaf results keyed on
the **predicate node identity** within a single dispatch, so a `HasTag("vip")`
shared by several rules evaluates its DB query once. Benchmark note: the Gate 1
`lockcache` column showed this is a no-op for single-leaf gates and only pays off
under multi-leaf-sharing dispatches — keep it (cheap), don't rely on it. Capability
leaves are a fresh in-memory tag read (resolved per check, never a stored mask) and
are not worth memoizing. Any residual `LegacyLock` leaf (transpiler fallback)
re-enters the old lock evaluator and is the one thing that still benefits from
memoization until it is hand-ported out.

### 1d. Redirect loop guard

Max 5 redirects per dispatch. `ActionError("redirect loop after 5 redirects")`
on breach. Redirect count visible in `ActionTrace`.

### 1e. `actor.explain(action_type_or_instance)`

Character/Account convenience method. `dry_run=True`: `requires` predicates
evaluated, rule bodies skipped. Returns `ActionTrace`. Formatted output sent
to actor by default; raw trace returned for programmatic use.

### 1g. Driving deferred / interactive `carry_out` and `report` rules

This is the concrete realization of the AS1 boundary in the engine. The
`before` and `check` phases are strictly synchronous — a rule body returns a
`RuleResult` now, full stop. The `carry_out` and `report` phases must drive
three rule-body shapes:

1. **Plain rule** — returns a `RuleResult` synchronously. Fire and inspect.
2. **`@interactive` generator rule** — the body `yield`s a prompt string (or a
   number of seconds). The engine drives it with the *existing* engine
   machinery: `evennia.utils.utils.interactive` is built on `evmenu.get_input`
   + `deferLater`, producing a `Deferred` that fires with the player's next
   input line. The rule engine does **not** reimplement this — it detects a
   generator return and hands it to the same driver, passing
   `actor.character`/`actor.effective` as the `caller` that `get_input`
   requires (wired by `@rule` registration so authors never manage it).
3. **`Deferred`-returning rule** — a rule that calls `defer.in_thread(...)` and
   returns the `Deferred`. The engine `yield`s it (inside the `inlineCallbacks`
   dispatch) and resumes with the resolved value, on the reactor thread.

In all three deferred/interactive cases the engine wraps the rule so its
eventual result is coerced to a `RuleResult` (a bare `None`/non-result is
treated as "fired, did not claim"; an explicit `CLAIM`/`FAIL`/etc. is honored).

**Phase serialization across suspension (the CLAIM-ordering guarantee):** when
a `carry_out` rule suspends (interactive or Deferred), the engine suspends the
*entire* `carry_out` phase — it does not start the next `carry_out` rule until
the suspended one resolves and its result is inspected. Only then does it
decide whether `CLAIM` stops the phase or the next rule runs. Two `carry_out`
rules never interleave; `CLAIM` stays deterministic even when a rule pauses for
player input or a thread-pool round-trip. `report` rules likewise run in order;
they never short-circuit, but a suspended `report` rule still completes before
the next one fires.

`RuleEngine.dispatch` therefore returns a `Deferred[ActionTrace]` (see Phase
4a). When no rule suspends, that Deferred is already-fired and dispatch is
effectively synchronous; the cost of the machinery is paid only when a rule
actually defers.

### 1f. Tests for Phase 1

- `check` short-circuits on first `FAIL`; later check rules do not fire.
- `check` pure-predicate contract: a check rule with a side effect logs a
  warning (detectable via mock in test).
- `carry_out` `CLAIM` stops further carry_out; no further rules fire.
- `carry_out` exception in one rule does not stop others (absent `CLAIM`).
- `before` `REDIRECT` restarts dispatch; redirect counter increments.
- Redirect loop guard fires at 5.
- Catch-all `@rule(Action, ...)` on state object fires for every action type.
- A rule whose `requires` predicate is False → `SKIP` (body never runs); True →
  body runs. No lock-string parsing happens at dispatch time (assert via a spy
  that the lock evaluator is not called for a `Capability`/relational predicate).
- Per-dispatch memo: a DB leaf (`HasTag`) shared by two rules evaluates its query
  once (mock the query, assert call count == 1 across the dispatch).
- `dry_run=True` skips all rule bodies; `requires` still evaluated.
- `explain()` returns full trace with all phases.
- An `@interactive` `carry_out` rule suspends, resumes with injected input, and
  its eventual `CLAIM` stops the phase (next `carry_out` rule does not fire).
- A `Deferred`-returning `carry_out` rule (`defer.in_thread`) resumes with the
  resolved value coerced to a `RuleResult`; the phase does not advance until it
  fires.
- `carry_out` phase serialization: a suspended rule blocks the next `carry_out`
  rule until it resolves — assert no interleaving.
- A `check` rule that returns a generator/`Deferred` is a contract violation —
  engine raises (check must be synchronous).
- `dispatch` returns an already-fired `Deferred` when no rule suspends.
- Benchmark result recorded in test output (not a pass/fail; a measurement).

### 1h. Activities, typed events, and dynamic exit resolution *(shipped)*

Three engine primitives added after Phase 1g, enabling the movement rewrite and
any sustained/reactive verb family:

**Activities (`evennia/actions/process.py`).** `Activity` + `ActivityManager`
(start/cancel/query, exclusive groups) + a generalized generator driver (numeric
`yield` → `deferLater` pause). `Actor.start_activity` / `cancel_activity` /
`is_active` delegate here. A sustained process re-validates by re-dispatching
instantaneous actions — the Locomotion walk is the reference pattern. Tests:
`evennia/actions/tests/test_process.py`.

**Typed events (`evennia/actions/events.py`).** `Event` base + `@subscribe`
decorator + `EventRegistry` + `engine.emit(event)`. Handlers are gathered from
the event's own `providers()` scope (fresh per emit — no stale subscription
leak). Tests: `evennia/actions/tests/test_events.py`.

**DynamicVerbResolver (`evennia/actions/parser.py`).** Protocol + resolver chain
consulted after trie-miss and symbol-prefix-miss, before `_nomatch`. Tests:
`evennia/actions/tests/test_parser.py` (resolver chain + ambiguity).

**Default shipped movement (`evennia/actions/default/`).** The action-engine
analogue of `evennia/commands/default/`: generic `Move`, baseline traverse
rules, `Locomotion` (with overridable hooks), `Moved`/`Departed`/`Arrived`, and
`exit_resolver` + `register_exit_resolver()`. Re-exported from `evennia.actions`.
Games layer game-specific `check` rules on Character/Exit without redefining
`Move`. See §6d as-built + ledger §C.2.

**`_unresolved` guard (`action.py`, `engine.py`).** Promoted from convention to a
first-class `Action` field; when set (parser target miss after `search` reported
the miss), the engine skips the `check` phase so observer rules cannot
`AttributeError` on `action.target is None`.

---

## Phase 2 — Action Parser

*Text → typed Action. Trie stays alive for the bridge.*

**Status: SHIPPED (2a–2f).** `evennia/actions/parser.py` (`ActionParser`,
`ParseResult`, `parser` singleton, system actions `NoInputAction` /
`NoMatchAction` / `LoginStartAction`); `GameObject` marker + default
`Action.parse` classmethod in `action.py`; `VerbTrie` + `match_verb` /
`suggest_verbs` (lazy-built, register-invalidated, char-keyed, system verbs
excluded) + a pure `levenshtein` in `registry.py`. 14 tests in
`tests/test_parser.py` (137 total in the package, all green).

* **Verb resolution:** exact → confidence `1.0`; unique prefix →
  `len(token)/len(verb)` (< 1.0); ambiguous prefix or unknown → `NoMatchAction`
  with fuzzy `suggestions` (edit distance ≤ 2, ≤ 3 results).
* **Default `Action.parse`** auto-assigns positional tokens by dataclass field
  type: `GameObject` → `actor.search(token)` (`None` → `ParseError`,
  `AmbiguousTarget` **propagates**); `Literal[...]` → token *or* a matching
  switch; `int` → `int(token)`; trailing `str` field is greedy. Missing required
  field → `ParseError`.
* **Switches** (`verb/sw1/sw2`) are split off the verb token in
  `ActionParser._split_verb` and passed to `parse(switches=...)`; the default
  parser feeds them to `Literal` fields.
* **Failure mapping:** empty input → `parse` returns `None` (Phase-4 bridge
  substitutes `NoInputAction`); a matched action's `ParseError` → `NoMatchAction`
  carrying the message; `AmbiguousTarget` is *not* swallowed (Phase-4 dispatch
  loop will install a disambiguation state).
* **Footgun reminder:** `@action` registers into the *global* `action_registry`
  at import; tests build an isolated `ActionRegistry` + `ActionParser(registry=)`
  to avoid global verb collisions.

### 2a. `ActionParser`

```python
class ActionParser:
    def parse(
        self,
        raw_string: str,
        actor: Actor,
        context: ActionContext,
    ) -> ParseResult | None:
        # Returns None only if raw_string is empty → NoInputAction
```

`ParseResult`:
```python
@dataclass
class ParseResult:
    action: Action
    raw_verb: str
    raw_args: str
    confidence: float   # 1.0 exact, <1.0 prefix/fuzzy
```

### 2b. Verb trie

`ActionRegistry` maintains a trie of verb strings → `Action` subclass. Built
once at startup. Same trie logic as `cmdparser_trie`.

### 2c. Per-action `parse` classmethod

```python
@classmethod
def parse(cls, raw_args: str, actor: Actor, context: ActionContext) -> "Self":
    ...
    # Raises ParseError(msg) on bad input
    # Raises AmbiguousTarget(candidates, raw_args) on multi-match
```

Default `parse` for simple actions: auto-parse by dataclass field types
(`GameObject` → `actor.search()`, `Literal` → string match). Custom `parse`
for complex syntax.

`actor.search()` raises `AmbiguousTarget` instead of printing and returning
`None`. Caught by the dispatch loop (Phase 4) which installs
`DisambiguationState`.

### 2d. Switches

`kick/hard ball` → `Kick(target=ball, strength="hard")`. Switch extraction
in base `ActionParser` before `Action.parse()`.

### 2e. System actions

```python
@action("__noinput__")
class NoInputAction(Action): ...

@action("__nomatch__")
@dataclass
class NoMatchAction(Action):
    raw_string: str

@action("__loginstart__")
class LoginStartAction(Action): ...
```

`CMD_NOINPUT`, `CMD_NOMATCH`, `CMD_LOGINSTART` constants retire.

### 2f. Tests for Phase 2

- Exact match → action type, confidence 1.0.
- Prefix match → confidence < 1.0.
- Unknown verb → `NoMatchAction`.
- `Kick.parse("ball hard", ...)` → `Kick(target=ball, strength="hard")`.
- `ParseError` → nomatch message.
- `AmbiguousTarget` raised (not swallowed) from `actor.search()`.
- Fuzzy suggestions on near-miss verb.

---

## Phase 3 — Actor Context

*Owns the shape of Actor (absorbing I1 initial scope) and predicate algebra
(absorbing L1 initial scope). See Gate 5.*

**Status: SHIPPED (3a, 3b, 3c, 3e).** `evennia/actions/actor.py` (`Actor`
dataclass + `effective`/`location`/`state_objects`/`equipped_items` views,
`enter_state`/`exit_state`/`has_state` delegating methods, `from_caller`);
`evennia/actions/state.py` (`StateProvider` base + module-level
`enter_state`/`exit_state`/`has_state`/`get_states` operating on
`holder.ndb.active_states`); `ActionContext` extended with
`actor`/`raw_string`/`trace_id` (defaulted, so existing callers unaffected) +
`ActionContextBuilder.build(actor, raw_string, targets=, trace_id=)` (pure,
canonical Phase-1a provider order, dedupe-by-identity, None-tolerant);
`evennia/actions/menus.py` (`MenuInputAction`, `EvMenuState`,
`DisambiguationState`). 17 tests in `tests/test_state.py` (154 total, all green).

* **State lifecycle** lives on the *holder* (`character` else `account`) under
  `ndb.active_states` (non-persistent by design); `Actor` methods delegate.
* **Provider order** (builder): `state_objects → effective → equipped_items →
  location → targets → other room contents`, first-occurrence wins on dupes.
* **Interaction states** work purely through the engine: a catch-all `before`
  rule at priority 9999 seizes the next input. `EvMenuState.capture_input`
  `REDIRECT`s into `MenuInputAction` (and PASSes the MenuInputAction itself, or
  it would redirect-loop), then `run_node` (carry_out) routes to the node graph
  and `CLAIM`s. `DisambiguationState.resolve_disambiguation` reads the next line
  as a choice, patches the pending action's `target`, `exit_state`s, and
  `REDIRECT`s — guarded by `has_state` because the engine **reuses the same
  provider list across a REDIRECT** (so the just-exited state stays in `context`
  for the redirected dispatch and must no-op).

**Deferred from Phase 3 (intentional):**
* **3d game states** (`FlatlinedState`, `GrappledState`, `UnconsciousState`,
  `SplinterPodState`, `CombatState`, `GatekeeperState`): these are *newmoo* game
  content and reference game actions (`Look`, `ResistGrapple`), so they belong
  game-side (Phase 7), not in the engine. The engine ships only the
  `StateProvider` substrate + the two generic interaction states; `test_state.py`
  proves the gating pattern with a test-local `TestFlatlined`.
* **Full legacy `EvMenu(caller, menudata)` wrapper**: `EvMenuState` implements a
  minimal node-routing model (`{name: callable(actor, raw)->next|None}`) good
  enough to validate capture/route/exit; wrapping the real `evmenu` node-graph
  API and real input capture needs the cmdhandler bridge → Phase 4.
* **`DisambiguationState` signature**: built per the Gate-4 design
  `(candidates, pending_action)`; the Phase-4 sketch at §4 instead shows
  `pending_raw=raw_string` (re-parse on resolve). Reconcile when wiring Phase 4.
* **`@interactive` `carry_out` linear flow**: already covered by
  `test_engine.py::TestInteractiveRule` (Phase 1g); not re-tested here.

### 3a. `Actor`

```python
@dataclass
class Actor:
    session: ServerSession | None
    account: DefaultAccount | None
    character: DefaultCharacter | None

    @property
    def effective(self) -> DefaultCharacter | DefaultAccount:
        return self.character or self.account

    @property
    def location(self) -> DefaultRoom | None:
        return self.character.location if self.character else None

    @property
    def state_objects(self) -> list[StateProvider]:
        return list(self.character.ndb.active_states or []) if self.character else []

    @property
    def equipped_items(self) -> list:
        return list(self.character.ndb.equipped_for_rules or []) if self.character else []

    @classmethod
    def from_caller(cls, caller, session) -> "Actor": ...
```

### 3b. `ActionContext`

```python
@dataclass
class ActionContext:
    actor: Actor
    providers: list
    raw_string: str
    trace_id: str
```

`ActionContextBuilder.build(actor, raw_string)` — replaces
`cmdhandler.get_and_merge_cmdsets`. Returns ordered provider list per Phase
1a. Pure function; no side effects; called once per dispatch.

### 3c. `StateProvider` base

```python
class StateProvider:
    """Base for all state objects. Subclass, attach @rule, register via enter_state."""
    pass

# On Character / Account:
def enter_state(self, state: StateProvider): ...
def exit_state(self, state_type: type[StateProvider]): ...
def has_state(self, state_type: type[StateProvider]) -> bool: ...
```

### 3d. Built-in state objects

```python
class FlatlinedState(StateProvider):
    @rule(Action, phase="check", priority=1000)
    def block_all(self, action, actor):
        if isinstance(action, Look): return SKIP
        return FAIL("You are flatlined.")

class GrappledState(StateProvider):
    @rule(Action, phase="check", priority=950)
    def restrict_grappled(self, action, actor):
        if isinstance(action, (Look, ResistGrapple)): return SKIP
        return FAIL("You are grappled.")

class UnconsciousState(StateProvider):
    @rule(Action, phase="check", priority=1000)
    def block_all(self, action, actor):
        if isinstance(action, Look): return SKIP
        return FAIL("You are unconscious.")

class SplinterPodState(StateProvider): ...
class CombatState(StateProvider): ...
class GatekeeperState(StateProvider): ...  # replaces gatekeeper.py; always active
```

`make_flatlined()` → `character.enter_state(FlatlinedState())`.
`recover_from_flatline()` → `character.exit_state(FlatlinedState)`.

### 3e. `EvMenuState` and `DisambiguationState` (from Gate 4 designs)

These are in `evennia/actions/menus.py`. Implementation per Gate 4 worked
designs above.

- `EvMenuState(StateProvider)` backs the **legacy** `EvMenu` node-graph API:
  its catch-all `before` rule (priority 9999) captures the next input and
  routes it to the current node, exactly as the cmdset-based EvMenu does
  today. The existing `EvMenu(caller, menudata)` constructor is preserved as a
  thin wrapper that installs an `EvMenuState` — zero game-side changes.
- New linear flows should prefer `@interactive` `carry_out` rules (`response =
  yield "prompt"`) over node graphs; no state object needed, the engine's
  existing generator-driver handles suspension (see Phase 1g). Migration from
  node graphs to `@interactive` is opportunistic, not forced.
- `DisambiguationState(StateProvider)` is the one-shot state installed when the
  parser raises `AmbiguousTarget`; its `before` rule resolves the choice and
  `REDIRECT`s the pending action.

### 3f. Tests for Phase 3

- `Actor.effective` returns character when puppeted, account otherwise.
- `ActionContextBuilder` provider order matches spec.
- `FlatlinedState` blocks non-Look; passes Look.
- `GrappledState` passes `ResistGrapple`; blocks movement.
- `enter_state`/`exit_state`/`has_state` round-trip.
- `EvMenuState` catches all input; menu node routes correctly; exits cleanly.
- `@interactive` `carry_out` rule prompts, resumes with input, completes (linear flow).
- `DisambiguationState` resolves valid choice; cancels on invalid.
- `AmbiguousTarget` from parser → `DisambiguationState` installed.

---

## Phase 4 — Wire Into cmdhandler

**Status: SHIPPED (4a–4d), flag-gated, no prod bump.** `evennia/actions/dispatch.py`
(`try_action_dispatch` — the cmdhandler bridge; `DispatchMiddleware` +
`ProfilingMiddleware` + `register_middleware`/`clear_middlewares`/`get_middlewares`);
the shim in `evennia/commands/cmdhandler.py` (after ftfy normalize, before
`generate_cmdset_providers`, gated on `ACTION_ENGINE_ENABLED`, lazy import,
`yield try_action_dispatch(...)` → `return` on True); `ACTION_ENGINE_ENABLED = False`
in `settings_default.py`; `action.py` gained `targets`/`_object_field_names`;
`actor.py` gained the disambiguation search-override (`search`/`_take_search_override`/
`set_search_override`); `DisambiguationState` extended with
`pending_raw`/`ambiguous_name`. 11 tests in `tests/test_dispatch.py` (165 actions
total + 146 commands, all green).

> **Superseded — current bridge contract (dispatch-honesty fix, 2026-06).** The
> `Deferred[bool]` / `return False` / fall-through contract described in this
> Phase 4/5 record was the flag-OFF transitional design and is **no longer
> live**. The engine is the sole player-input dispatch path; `cmdhandler`'s
> `cmdobj is None` branch dispatches and returns unconditionally (no
> `if handled` fall-through, no `ACTION_ENGINE_ENABLED` flag). `try_action_dispatch`
> now returns `Deferred[ActionTrace | None]` (`None` only when the bridge
> consumed the line without an engine dispatch — disambiguation prompt installed
> or pending choice cancelled). A parsed verb that fires no `carry_out`/`report`
> rule fails closed: a fully `requires`-gated verb is logged to the security log
> and re-dispatched as a suggestion-free `NoMatchAction` (indistinguishable from
> an unknown verb, so it leaks nothing), and a verb with no responding rules at
> all gets a direct refusal. Bodies that raise are reported to the player
> (engine `_notify_rule_error` for rule bodies; `cmdhandler` wraps the bridge in
> `_msg_err`; `inputfuncs.text` adds an errback backstop). See
> `evennia/actions/dispatch.py` module docstring for the authoritative statement.

* **Flag-gated shim, default OFF.** The bridge is inert in production until a deploy
  flips `ACTION_ENGINE_ENABLED`. With the flag off the legacy cmdset body is reached
  byte-for-byte unchanged (146 commands tests still green) — the shim is a single
  `getattr(settings, ...)` guard above `generate_cmdset_providers`.
* **Routing (the bridge's decision):** (1) active capturing state (EvMenu/disambig)
  → always engine-route so the state's catch-all `before` rule sees the input;
  (2) verb matches a registered, non-system action → dispatch; (3) otherwise
  (empty/unknown/system verb, no state) → `return False` so the legacy path handles
  `__noinput__`/`__nomatch__` and every unported command. Returns a `Deferred[bool]`:
  True = engine handled (cmdhandler returns), False = fall through.
* **Disambiguation reconciliation (resolves the Phase-3 §4 open item):**
  `AmbiguousTarget` is raised *mid-parse*, before any action object exists, so the
  bridge resolves the choice itself — installs `DisambiguationState(pending_raw,
  ambiguous_name)`, prompts, and on the next line stashes a one-shot search override
  (`actor.set_search_override`) and **replays `pending_raw`** through the parser
  (the override makes the re-`search` return the chosen candidate). The engine-native
  `REDIRECT(pending_action)` path (Phase 3) stays for states that already hold an
  action object.
* **Signals + middleware** wrap each engine dispatch in `_dispatch_with_signals`:
  `on_command_pre` → middleware `before_dispatch` → `engine.dispatch`
  (`on_command_error` + re-raise on exception) → middleware `after_dispatch` →
  `on_command_post` (carrying `elapsed_ms`). `ProfilingMiddleware` records per-action-
  type timing on the engine path via the middleware hooks, and on the legacy path via
  its `on_command_post` signal receiver (wire it to the signal).

**Deferred from Phase 4 (intentional):**
* **No `EVENNIA_REF` bump / no flag enable.** Per decision: land + test locally; defer
  the prod pin bump and turning the flag on until verbs are ported (Phase 7). The
  legacy path (cmdset fall-through) is still the only live path in prod.
* **No bridge (revised Phase 5):** the original §4a sketch gated on
  `not isinstance(parse_result.action, LegacyAction)`, assuming a `CmdSetBridge`
  would wrap legacy commands as actions. That design is dropped (see the revised
  Phase 5 — Gate Overlap). While the flag was OFF, the shipped `dispatch.py`
  returned False for any unported verb so the legacy cmdset path handled it,
  keeping unported commands gated by the firewall (Phase 5) meanwhile. This
  fall-through was the **transition** mechanism, not the end state: once every
  verb was ported and the flag turned on (`.73`), it became unreachable, and the
  dispatch-honesty fix (see the superseded-contract note under Phase 4 Status)
  retired the bool return entirely.

*Original design notes below (4a–4d) retained for reference. Note the §4a
`LegacyAction` gate is superseded — see the revised Phase 5.*

*Requires Gate 4's worked designs (menu/yield/disambiguation) accepted before
starting. AS1 is already settled (sync-by-default; see Gate 4 and the
Cross-Cutting AS1 section) — it is no longer a pending decision, it is the
contract this phase builds on.*

### 4a. Dual dispatch

The existing `cmdhandler` is an `inlineCallbacks` coroutine (it `yield`s
Deferreds today so a command's `func` can return a Deferred without blocking
the reactor). We keep that shape — it is exactly the machinery that lets a
`carry_out`/`report` rule suspend (`@interactive`) or defer I/O
(`defer.in_thread`) under sync-by-default AS1. `RuleEngine.dispatch` therefore
returns a `Deferred` that fires with the completed `ActionTrace`; the
`before`/`check` phases resolve synchronously inside it, and only deferrable
`carry_out`/`report` rules cause the Deferred to actually suspend.

```python
from twisted.internet.defer import inlineCallbacks, returnValue

@inlineCallbacks
def cmdhandler(called_by, raw_string, session=None, ...):
    actor = Actor.from_caller(called_by, session)
    context = ActionContextBuilder.build(actor, raw_string)

    try:
        parse_result = ActionParser().parse(raw_string, actor, context)
    except AmbiguousTarget as e:
        actor.enter_state(DisambiguationState(e.candidates, pending_raw=raw_string))
        actor.msg(_format_disambiguation(e.candidates))
        return

    if parse_result is not None and not isinstance(parse_result.action, LegacyAction):
        # dispatch returns a Deferred; before/check resolve sync, carry_out/
        # report may suspend (@interactive) or defer I/O (defer.in_thread).
        trace = yield RuleEngine().dispatch(parse_result.action, actor, context)
        _emit_signals(trace, actor, session)
        returnValue(trace)

    yield _legacy_cmdset_dispatch(called_by, raw_string, session, ...)
```

No `async`/`await`: that would be option-2 (async-first), which AS1 explicitly
did not choose. The engine stays on Twisted's `inlineCallbacks` throughout.

### 4b. Signal bridge

`on_command_pre`, `on_command_post`, `on_command_error` fire from rule engine
dispatch wrapper. `on_cmdset_merge_error` fires only from the legacy path.
`trace_id` unchanged.

### 4c. Profiling middleware

```python
class ProfilingMiddleware(DispatchMiddleware):
    def before_dispatch(self, action, actor, context): ...
    def after_dispatch(self, action, actor, context, trace): ...
```

Replaces `ProfilingCommandMixin`. Records timing per action type. Works for
both dispatch paths.

### 4d. Tests for Phase 4

- New-style action: dispatches through rule engine; signals fire.
- Legacy verb: falls through to cmdset path; signals fire.
- `AmbiguousTarget`: `DisambiguationState` installed; disambiguation prompt sent.
- Profiling records timing in both paths.

---

## Phase 5 — Gate Overlap (no compatibility bridge)

*Supersedes the original "Compatibility Bridge" design. There is NO
`LegacyAction`, NO `CmdSetBridge`, NO re-running of the legacy hook chain from
inside the engine. Instead, ported and unported commands coexist because their
**gates** coexist.*

### Why the bridge is gone

The bridge existed to plug one gap: when `ACTION_ENGINE_ENABLED` is on, a typed
verb that matches a registered action is engine-dispatched, and engine dispatch
does NOT run the legacy `at_pre_cmd` gate. A flatlined player could `kick`
even though the legacy world forbids it.

> **Audit note (newmoo/mootest, 2026-06):** this game gates character status
> through a single mechanism — the **gatekeeper firewall**
> `world.status.gatekeeper.check_command_gated`, invoked from
> `_apply_character_gates` in `commands.base_cmds` at `at_pre_cmd`. It covers
> flatlined, incapacitated, unconscious, grappled, grappling, dead-lobby,
> OOC-lounge, chargen-holding, and (location-based) splinter-pod, all keyed on
> `cmd.key`. The restrictive **state-cmdsets** named in `docs/architecture.md`
> (`FlatlinedCmdSet`, `UnconsciousCmdSet`, `GrappledCmdSet`, `SplinterPodCmdSet`,
> `CombatGrappleCmdSet`) **do not exist in code** — they were removed in a
> deliberate "cmdset reduction plan" (see `world/edge_cmdset_policy.py`;
> `world/death/core.py:345` "no cmdset swap"). That doc is stale. So the "legacy
> gate" the engine overlaps with is the firewall, not a cmdset.

Re-implementing the cmdhandler hot-path hook chain
(`on_command_pre → at_pre_parse → parse → at_pre_cmd → func → at_post_cmd →
on_command_post`, plus InterruptCommand / generator / coroutine / recursion /
`save_for_next`) inside a rule provider is the single highest-risk item in the
roadmap, and it gets deleted again in Phase 8. The cheaper, lower-risk move is
to **port the gates before the commands they gate**, and let the engine gate
and the legacy gate overlap during migration.

### Core principle

> Install engine gates **early**, remove legacy gates **late**, overlap both
> during migration so every typeable command is gated by exactly one
> appropriate mechanism — the engine state if its verb is ported, the legacy
> `at_pre_cmd` firewall if it isn't.

At no moment of the rollout does any command fall through ungated. A
`FlatlinedState` StateProvider with a catch-all `check` rule blocks ported
actions exactly as the firewall's flatline branch blocks unported ones. The two
are complementary by construction: a **ported** verb routes through the engine,
the bridge returns `True`, and `cmdhandler` returns *before* `at_pre_cmd` runs —
so the engine state is its only gate; an **unported** verb falls through
(`try_action_dispatch` → `False`) to the legacy path, where `at_pre_cmd` fires
the firewall. Neither double-gates, neither leaves a hole.

### 5a. Engine gates first (behind the OFF flag)

Build the StateProviders + their catch-all `check` rules **before** porting any
command they should block. These are inert while the flag is off and nothing is
ported, so they land and test in isolation:

- `FlatlinedState`, `UnconsciousState`, `GrappledState`, `SplinterPodState`,
  `CombatState` — each a `StateProvider` with a catch-all `check` rule mirroring
  the corresponding branch of `check_command_gated` (the allowlist + block
  message; e.g. `FlatlinedState` permits only `look`/`l`). These are simple
  allowlist variants of one another.
- `GatekeeperState` — always-on (added in `Character.at_object_creation`, never
  removed), replacing the `at_pre_cmd` gatekeeper call. (This is roadmap item
  6e, pulled early because it is a gate.) **Not** a simple allowlist: it branches
  on location (OOC lounge, chargen holding, dead lobby), so it depends on
  rooms-as-providers (6d) — defer it until then.
- stealth-reveal `before` rule on `Character` (roadmap item 6f, same reasoning).

**Status (proof-of-shape landed):** `FlatlinedState`
(`world/death/states.py`) and the stealth-reveal rule
(`world/rpg/stealth_state.py`, `StealthRevealMixin`) are built + tested
(`world/tests/test_flatlined_state.py`, `test_stealth_reveal_state.py`), proving
the two distinct shapes — a `check` gate that blocks and a `before` rule that
reacts-then-`PASS`es. Both are game-side (gate *policy*); the engine ships only
the `StateProvider` machinery. The remaining simple allowlist gates
(`Unconscious`/`Grappled`/`SplinterPod`) are deferred until the first gated verb
is ported, so their tests assert against live dispatch rather than more fakes.

### 5b. State-transition functions install the engine state

Because the firewall is already always-on for unported verbs (it just reads
`db.death_state` / `db.grappled_by` / … at `at_pre_cmd`), the overlap needs
**no change to the legacy half** — only the engine half. Modify each
state-transition function — `make_flatlined`, `set_unconscious`, `grapple`,
splinter-pod entry — to additionally `actor.enter_state(<State>())`, and the
inverse (revive / wake / release / leave-pod) to `actor.exit_state(<State>)`.
During overlap a flatlined player carries `FlatlinedState` (gates ported verbs)
while the firewall's flatline branch still gates the unported ones. No
belt-and-suspenders bookkeeping, no cmdset to load — just keep the engine state's
lifecycle in lockstep with the `db` flag the firewall reads.

> Do this per-state **only when there is a ported verb that state must gate** —
> installing a state that nothing consults yet is dead weight (and touches a hot
> production death-path for no effect). `FlatlinedState`'s install wiring waits
> for the first flatline-gated verb (`look` is allowed, so the first *blocked*
> verb — e.g. a ported combat/move verb — is the real trigger).

### 5c. Tests for Phase 5

- Each engine state's catch-all `check` rule blocks the right verbs and permits
  the exempt ones (`look` under flatline, `look`/`resist` under grapple). *(Done
  for `FlatlinedState`.)*
- The stealth-reveal `before` rule reveals on a breaking action and abstains on a
  safe one, never blocking. *(Done.)*
- A state-transition function installs the engine state and its inverse removes
  it (added with the wiring in 5b).
- With the flag off, none of the above changes legacy behavior (states inert).

---

## Phase 6 — Port Engine-Internal Cmdsets

*Engine internals first. Game-side stays on its legacy cmdset until ported
(Phase 7); the gate-overlap of Phase 5 keeps unported commands correctly
gated throughout. Gates 6e (`GatekeeperState`) and 6f (stealth-reveal) are
pulled forward into Phase 5a because they are gates — the entries below are
retained for detail and cross-reference.*

### 6a. Account-level actions

`AccountCmdSet` → actions with `actor_type=Account` on `DefaultAccount`:
`Who`, `Quit`, `OOCLook`, `IC`, `OOC`, `Sessions`, `Password`, etc.

### 6b. Character base actions

`CharacterCmdSet` defaults → actions on `Character` and target objects:
`Look`, `Say`, `Emote`, `Get`, `Drop`, `Inventory`, `Go`.

`Go(direction="north")` action. Room exits contribute `carry_out` rules
(see 6d). Character contributes `check` rules (can move?, not grappled?).

**Status — `Look` ported (first live verb).** `world/actions/look.py` defines
the `Look` action (`@action("look", "l", "ls")`) + `LookMixin`, a `carry_out`
rule that mirrors the core of `commands.base_cmds.CmdLook` / Evennia's
`DefaultCmdLook.func`: resolve the target (the room when no arg), render
`caller.at_look(target)`, send it with the `{"type": "look"}` outputfunc, and
`CLAIM` (Character is the primary handler). The rule self-guards
(`self is not actor.character → SKIP`) so a bystander character in the room
abstains. `world/tests/test_look_action.py` proves it end-to-end against the
*real* game `Character`/`Room` typeclasses (a test-only `LookCharacter(LookMixin,
Character)` under `EvenniaTest`), covering both direct `engine.dispatch` and the
full `try_action_dispatch` bridge path (parse → route → dispatch) for `look` and
`look <obj>` — 5/5 green.

Deliberately deferred (still served by the legacy `CmdLook` while the flag is
OFF, so nothing is lost): the `look <target> in <photograph>` close-up,
directional peek-through (`look north`), and room-detail lookups — additive
special cases that become their own rules when they're worth porting.

Two wiring steps are held for **Phase 7** because they are deploy-affecting
(importing the action module at startup makes `evennia.actions` a hard import on
the production `Character`, which requires the pinned `EVENNIA_REF` to contain
the package — the prod-bump Phase 4 explicitly deferred): (1) mix `LookMixin`
into `typeclasses.characters.base.Character`; (2) set
`Look.__primary_handler__ = Character`. Until then `world/actions/look.py` is a
pure addition — imported only by its test — and inert in production.

**Status — `Say`, `Pose`, `Emote`, `Inventory` ported (second batch).** Same
pattern as `Look`, each a `carry_out` rule with the `self is not actor.character
→ SKIP` bystander guard, `CLAIM`ing as the primary handler, and faithful to its
legacy command rather than reimplementing the work:

- `world/actions/say.py` — `Say` (`@action("say", '"', "'")`) mirrors stock
  `CmdSay.func`: `at_pre_say` → `resolve_transform` → `at_say(speech,
  msg_self=True)`. The game does not override `CmdSay`; all RP richness lives in
  the `Character.at_say` hook, so the rule just drives it. Whisper not ported.
- `world/actions/emote.py` — `Pose` (`@action("pose", ".", ",")`) and `Emote`
  (`@action("emote")`); both call `commands.roleplay_cmds._run_emote` (lazy
  import) with the legacy args (`msg_type="pose"` + `performance_improvising`;
  `msg_type="emote", literal_third=True`). Pose's `.`/`,` markers *inside* the
  text work via the word form (`pose ,text`).
- `world/actions/inventory.py` — `Inventory` (`@action("inventory", "inv", "i")`)
  replicates `CmdInventory.func` (hands line + grouped worn/wielded items),
  reusing `_counted_label` and `world.clothing.get_worn_items`.

`world/tests/test_rp_actions.py` proves all four end-to-end against the real
`Character` (test-only `RPCharacter(SayMixin, EmoteMixin, InventoryMixin,
Character)`), via direct `engine.dispatch` and the `try_action_dispatch` bridge —
15/15 green (incl. the no-space symbol-prefix routing below). Same Phase-7 wiring
deferral as `Look` (mix the mixins into the production `Character`, set each
`__primary_handler__`, bump `EVENNIA_REF`, flip the flag); modules are imported
only by their test and inert in production.

**Status — `Get`, `Drop` ported (core; fourth batch).** `world/actions/get_drop.py`
defines `Get` (`@action("get")`) and `Drop` (`@action("drop")`) + `GetDropMixin`,
mirroring the core of `DefaultCmdGet.func` / `DefaultCmdDrop.func` — what the
game's `CmdGet`/`CmdDrop` delegate to via `super().func()`: target resolved at
parse (room scope for get, inventory scope for drop, with the `_unresolved`
silent-claim idiom), `get` lock + `get_err_msg`, `at_pre_get`/`at_post_get`
(`at_pre_drop`/`at_post_drop`) vetoes, `move_to(..., move_type=...)`, and the
`$You() $conj(pick) up X` / `$You() $conj(drop) X` room messages. Self-guarded so
bystanders abstain. `world/tests/test_get_drop_action.py` (8/8 green) asserts both
the move side effect and the message, via direct dispatch and the bridge.

Tracked on the burndown (still served by legacy while the flag is OFF, but **not**
permanently legacy — every one must be ported before the flag flip): `get <item>
from <container>`, cash-pile interception, corpse/unconscious/logged-off gating,
numbered/stacked pickup (`get 3 coins`), and the flatlined/dead state gates. Once
`get`/`look` are registered the engine claims the verb and stops falling through
to legacy for these syntaxes, so each is a regression until ported. Same Phase-7
wiring deferral as the batches above.

**Status — `Give`, `Put`, `Enter` ported (core; fifth batch).**
`world/actions/give_put.py` defines `Give` (`@action("give")`), `Put`
(`@action("put", "insert")`), `Enter` (`@action("enter", "ride", "board")`) +
`ObjectMoveMixin`, mirroring the core of `commands.base_cmds` `CmdGive`/`CmdPut`/
`CmdEnter` (and `DefaultCmdGive.func`): `give` parses `=`/` to ` into item
(inventory scope) + recipient (default scope — characters resolve by **sdesc**,
not key, via the game's `SearchMixin`, so tests target by sdesc fragment), runs
`at_pre_give`/`at_post_give`, `move_to(target, move_type="give")`, and the
`You give X to Y` / `Y gives you X` messages; `put` parses ` in `/` into ` into
item + container, applies the self/own/holding guards + the immovable-fixture
`allow_put` exception + `at_pre_arrive`/`at_post_arrive`, and emits a funcparser
`$You() $conj(put) X in Y` room message (an improvement over the legacy plain-name
line); `enter` gates on `EnterableMixin` then calls `at_enter`. Self-guarded.
`world/tests/test_give_put_action.py` (10/10 green). Burndown deferrals: numbered/
stacked transfer (`give 3 coins to bob`) and the multi-object give path — fold in
with the shared numbered-search parse helper.

**Parser enhancement — no-space symbol prefixes (done).** The thin parser used to
split only on the first space, so *no-space* symbol prefixes (`.wave`, `"hi`,
`'hi`, `,text`) did not route. The parser now falls back to a symbol-prefix scan
when the space-split verb fails: it matches the longest *all-punctuation*
registered verb that the input starts with and peels it off (`"hi` → verb `"` +
args `hi`) — the engine analogue of a command's `arg_regex=None`. Restricting to
all-punctuation verbs means word verbs are never glued to their args
(`sayhello` stays a no-match). Implementation:

- `registry.py` — `ActionRegistry.symbol_verbs` (cached, invalidated on
  `register`): registered verbs made entirely of non-alphanumeric chars,
  longest-first.
- `parser.py` — `_match_symbol_prefix(stripped)` fallback in `parse`; and `parse`
  now threads the matched verb into `action_cls.parse(..., verb=...)`.
- `action.py` — base `Action.parse` gains `verb=None` (the engine analogue of a
  command's `cmdstring`; lets an override behave differently per alias).
- `world/actions/emote.py` — `Pose.parse` uses `verb` to re-attach the `,`
  no-conjugation marker the parser consumed as the verb, so `,text` and
  `pose ,text` both yield `,text` (matching legacy `CmdPose`, which strips a
  leading `.` but keeps a `,`).

Tests: `evennia/actions/tests/test_parser.py::TestSymbolPrefix` (engine unit) +
no-space routing cases in `world/tests/test_rp_actions.py` (`"hi`, `'hi`, `.wave`,
`,marker`). Still inert in production (flag OFF, actions package not imported at
startup), so no `EVENNIA_REF` bump now — it rides the Phase-7 bump with the rest
of the package.

### 6c. Session/login actions

`SessionCmdSet` → `Connect`, `CreateAccount`, `UnloggedHelp`, `LoginStart`.

### 6d. Exits as rule providers

```python
class Exit(DefaultExit):

    @rule(Go, phase="check")
    def check_traversable(self, action: Go, actor: Actor) -> RuleResult:
        if action.direction not in (self.key, *self.aliases):
            return SKIP
        if not self.access(actor.character, "traverse"):
            return FAIL(self.get_traverse_error(actor.character))
        return PASS

    @rule(Go, phase="carry_out")
    def carry_out_traverse(self, action: Go, actor: Actor) -> RuleResult:
        if action.direction not in (self.key, *self.aliases):
            return SKIP
        actor.character.move_to(self.destination)
        return CLAIM
```

No exit cmdset. Exit objects ARE the traversal handler.

**As-built (6d implemented as a full movement rewrite).** The sketch above is the
seed; the landed design generalizes it on three new engine primitives, and the
*generic core ships with the engine* in `evennia/actions/default/` (the
action-engine analogue of `evennia/commands/default/`) — the `Move` type, baseline
traverse rules, `Locomotion`, the events, and the default exit resolver. The game
layers only its own gates/pacing on top:

- **`Move` action + decomposed predicates.** `precheck_exit_traversal` is split into
  typed `check` rules across the Exit/Room/Character providers, each returning a
  `MoveBlock` IntFlag reason (sitting, closed/locked door, sealed bulkhead, faction
  gate, …) instead of one monolithic precheck. `carry_out` either moves instantly
  (a `Locomotion` step, `staggered=False`) or hands off to a `Locomotion` Activity.
- **`Locomotion` Activity** (`evennia/actions/process.py`) — a sustained,
  interruptible, re-validating route follower that supersedes
  `world/rpg/staggered_movement.py`: it paces RP delays and re-dispatches a fresh
  `Move` per step (so a door slammed mid-walk stops the walk). Vehicle `autopilot`
  is the same primitive (its driver loop stays in `world.movement.tunnels`).
- **Typed events + `@subscribe`** (`evennia/actions/events.py`, `engine.emit`) — a
  `Move` emits `Departed`/`Arrived`; follow/shadow/escort are event-subscriber
  Activities rather than imperative hooks. Handler scope is the event's own provider
  set, collected fresh per emit (no stale-subscription leak).
- **`DynamicVerbResolver`** (`evennia/actions/parser.py`) — a trie-miss resolver
  chain turns a bare per-room exit name ("north", "fire escape") into a `Move`
  action, the seam that lets `ExitCmdSet` retire. The engine ships the protocol
  *and* a default `exit_resolver` (`evennia/actions/default/movement.py`); a game
  opts in with `register_exit_resolver()`.

`Exit.do_traverse`/`ExitCommand` are retired by this design; the production typeclass
wiring + flag flip remain the deferred Phase-7 deploy step (see ledger §C.2).

**Status — Phase 7a family 2 (movement) ported.** All 26 commands across 6 legacy
modules are engine-complete (`[x]` on ledger §C.2). Game package:
`world/actions/movement/` (`MovementMixin`, `ExitMovementMixin`; imports register
`exit_resolver`). End-to-end tests: `world/tests/test_movement_action.py`
(**41**/41 green) — gates, resolver/bridge, Locomotion, follow/escort/shadow,
doors, bulkheads, autopilot, rentable (`@interactive` rent + programdoor),
city-grid builder cmds. Engine package: **213** tests in `evennia.actions.tests`.

Same Phase-7 wiring deferral as earlier batches: mix mixins into production
`Character`/`Exit`, bump `EVENNIA_REF`, flip `ACTION_ENGINE_ENABLED`. Until
then legacy `ExitCmdSet` + `staggered_movement` + `Exit.do_traverse` remain the
production path via gate-overlap; the new stack is imported only by tests.

**Burndown deferrals (not permanently legacy):** production typeclass wiring;
retire `staggered_movement.py` callers; retire `Exit.do_traverse` override;
rich per-tier staggered narration (Locomotion hooks exist — see
`locomotion.py::announce` / presentation layer). Tracked ledger §D + §E.1.

### 6e. Gatekeeper → `GatekeeperState` *(built early in Phase 5a)*

`world/status/gatekeeper.py` logic → `GatekeeperState(StateProvider)` with
`check` rules per gate condition. `GatekeeperState` is always active on every
character (added in `Character.at_object_creation`, never removed). Replaces
`at_pre_cmd` gatekeeper call.

### 6f. Stealth reveal → `before` rule on `Character` *(built early in Phase 5a)*

```python
class Character(DefaultCharacter):
    @rule(Action, phase="before", priority=500)
    def stealth_reveal(self, action: Action, actor: Actor) -> RuleResult:
        if self is not actor.character: return SKIP  # only fires for self
        if not self.db.stealth_hidden: return SKIP
        if not command_breaks_stealth(action): return SKIP
        self.reveal()
        return PASS  # reveal, then continue; don't block
```

### 6g. Tests for Phase 6

- Engine-internal actions dispatch correctly.
- State objects gate correctly.
- Exit traversal: lock block → FAIL; permitted → CLAIM; non-matching direction → SKIP.
- Gatekeeper check rules block dead/flatlined/grappled characters.
- Stealth reveal fires on action-breaking actions; skips non-breaking.
- **Activities:** lifecycle, cancel, exclusive groups (`test_process.py`).
- **Events:** emit + `@subscribe` + provider scoping (`test_events.py`).
- **DynamicVerbResolver:** exit-name match, ambiguity, trie precedence (`test_parser.py`).
- **Movement (game):** decomposed gates, Locomotion re-validation, follow on
  `Departed`, door/bulkhead/rentable/autopilot/citygrid verbs (`test_movement_action.py`).

---

## Phase 7 — Port Game-Side Commands

*Incremental, module by module. Anything not yet ported stays on its legacy
cmdset and is gated by the legacy mechanism; the Phase 5 gate-overlap keeps it
correctly gated. Flip `ACTION_ENGINE_ENABLED` on (with the matching
`EVENNIA_REF` bump) once enough is ported to be worth it — partial porting is
safe because of the overlap. Locality convention (Gate 3) in effect for all
rules authored here.*

### Migration pattern

```python
# OLD
class CmdKick(Command):
    key = "kick"
    aliases = ["boot"]
    locks = "cmd:all()"
    def func(self):
        target = self.caller.search(self.args)
        if not target: return
        target.location.msg_contents(f"{self.caller} kicks {target}!")

# NEW
@action("kick", "boot")
@dataclass
class Kick(Action):
    __primary_handler__ = Ball  # or Character if generic
    target: GameObject

    @classmethod
    def parse(cls, raw_args, actor, context):
        target = actor.character.search(raw_args.strip())
        return cls(target=target)  # AmbiguousTarget raised by search if needed

class Ball(DefaultObject):
    @rule(Kick, phase="carry_out")
    def carry_out_kick(self, action: Kick, actor: Actor) -> RuleResult:
        actor.location.msg_contents(f"{actor.character} kicks {self}!")
        return CLAIM
```

### 7a. Port order

1. **Roleplay** (`Say`, `Emote`, `Look`) — high-use, clear ownership. **Mostly done**
   (see §6b status blocks; `roleplay_cmds` `[x]`, `base_cmds` `[~]` for Phase-8
   flatlined firewall only).
2. **Movement** — **Done (test-only wiring).** Engine generic core in
   `evennia/actions/default/`; game gates + 26 verbs in `world/actions/movement/`.
   Production deploy deferred (ledger §E.1).
3. **Object interaction** (`Get`, `Drop`, `Wear`, `Remove`, `Use`) — target-centric.
   **Next.** Core get/drop/give/put/enter `[x]`; remaining: `use_cmds` (1),
   `cosmetic_cmds` (6), `food_cmds` (6), `hygiene_cmds` (1).
4. **Combat** (`Attack`, `Aim`, `Cover`, `Grapple`, `LetGo`, `Dodge`) — `CombatState` makes check rules clean.
5. **Medical** — action-per-procedure; target is patient.
6. **Matrix** — modal; `MatrixState` provider replaces matrix cmdset.
7. **Staff commands** — `actor_type=Account`, `requires=Builder` (capability bit).
8. **Remaining player commands** (chargen, XP, sheet, etc.).

### 7b. Multipuppet relay

```python
# OLD: allow_multipuppet_relay = False on Command
# NEW:
@rule(SomeAction, phase="carry_out", relay=False)
def carry_out_something(self, action, actor): ...
```

Rule engine skips `relay=False` rules when `actor.session` is a relay session.
`world/multipuppet/` relay machinery reads `RuleSpec.relay`.

### 7c. Help system

`Action.__doc__` → short description, usage, examples (same convention as
`Command.__doc__`). `RuleSpec.help_category` replaces `Command.help_category`.
`help kick` → `Kick.__doc__` + list of carry_out rules available to actor
(filtered by `actor_type`, `requires`).

Help availability computed by `ActionRegistry.available_for(actor, context)`:
dry-run check phase per registered action, cached per actor-state-hash.

---

## Phase 8 — Remove Legacy Gates and Cmdset Machinery

*Per-state, as each state's full gated command set finishes porting; then the
cmdset substrate once no command needs it.*

1. **Retire the firewall per-state.** For each status, once its *entire* gated
   command set is ported, delete that branch from
   `world.status.gatekeeper.check_command_gated` (and any matching `at_pre_cmd`
   short-circuit). Do this per-status, not all at once — the flatline branch can
   go the moment every verb it restricts is ported, independent of the grapple
   branch. The engine `<State>` remains and is now the sole gate. (There is no
   restrictive cmdset to remove — see the Phase 5 audit note; this game already
   gates purely through the firewall.)
2. Verify zero legacy cmdset dispatches in production (no verb still routes
   through `generate_cmdset_providers`), and that `check_command_gated` is dead
   code before deleting it.
3. Remove `evennia/commands/cmdset.py`, `cmdsethandler.py` — or archive as
   `evennia/commands/legacy/` for downstream forks mid-migration.
4. Simplify `cmdhandler.py` — remove the dual-dispatch branch (drop the
   `ACTION_ENGINE_ENABLED` shim; the engine becomes the only path), merge cache,
   fingerprint logic.
5. Remove `CmdSet` from `evennia/__init__.py` exports (with deprecation in
   the preceding release).
6. **Converge code gates on predicates (NOT a string-lock purge).** Once
   `LegacyLock` dispatch rate = 0% in the *code-gate* path (all code lockstrings
   transpiled/hand-ported), the action engine's gates are fully typed. The lock
   **DSL and `LockHandler` are retained** — a string is the right serialization
   format for runtime/persisted per-object locks, and `perm()`/`pperm()` remain as
   thin shims over the shared `resolve_capabilities`. Do not remove the DSL; only
   confirm the code-gate authoring surface no longer constructs `LegacyLock` leaves.
7. Update `engine-architecture/decisions.md` CM1 entry to "done." Mark I1 and L1
   initial-scope items as "shipped via CM1" (L1 = `Capability`/`Predicate` for code
   gates; one shared permission model; persisted-lock DSL retained).

---

## Cross-Cutting Concerns

### AS1 dependency (settled — this is the contract, not a blocker)

AS1 shipped in `6.0.0+underspire.50` as **Option 1: sync-by-default + threaded
I/O helpers** (`evennia/utils/defer.py`: `in_thread`, `background`,
`threaded`), *not* async-first. The CM1 dispatch loop inherits that decision
directly:

- **Dispatch stays on Twisted `inlineCallbacks`**, not `async`/`await` (Phase
  4a). The loop is synchronous in shape; it only suspends when a deferrable
  rule actually defers.
- **The rule-body contract is the AS1 hook-return boundary made concrete**
  (see the Gate 4 table): `before`/`check` must return a `RuleResult`
  synchronously and may not block, `yield`, or defer — `check` gates whether
  the action proceeds, so it needs a sync result. `carry_out`/`report` are
  deferrable: they may suspend for input via `@interactive` (`yield "prompt"`)
  or push blocking I/O to the thread pool via `defer.in_thread`/`background`,
  obeying AS1's threading-safety contract (worker thread = pure I/O + plain
  data, no game-object access; deliver results in the reactor-thread callback).
- **The engine drives the deferrable shapes** (Phase 1g) by reusing the
  existing `@interactive`/`get_input`/`inlineCallbacks` machinery — no new
  async substrate, no new dependency.

So AS1 is not a decision Phase 4 waits on; it is the foundation Phase 4 is
built against. The reactor-stall watchdog (`REACTOR_STALL_WARNING_MS`) shipped
with AS1 is also the instrument that flags any `before`/`check` rule that
violates the no-block contract in production.

### R1 display pipeline

`report` phase rules are the natural R1 integration point. Once R1 ships,
report rules call `render(obj, actor.effective) → RenderNode` then
`deliver(node, actor.effective)` instead of `actor.msg(...)`. Until then,
`actor.character.msg(...)` in report rules is correct. No structural change
at the rule engine level when R1 lands.

### H1 hook registry

`@rule` can carry `@hook`-style metadata once H1's `HookSpec` schema is
finalized. Coordinate at H1c. Until then, independent.

### Engine pin

Every phase shipping new `evennia.actions.*` symbols bumps `EVENNIA_REF` in
`mootest/.github/workflows/deploy.yml` to the tag containing them. Minimum
bumps: Phase 0 (package exists), Phase 4 (engine wired, safe to import from
game code). Track in the PR for each phase.

---

## Testing Strategy

### Unit tests

Each phase's "Tests" section is authoritative. Live in
`evennia/actions/tests/` mirroring package structure.

### Integration tests

- Full dispatch: text input → `ActionTrace` with correct outcome.
- State objects: flatlined, grappled, OOC account — all gate correctly.
- `EvMenuState`: intercepts all input; node routing; exits cleanly.
- `@interactive` `carry_out` rule: prompts, resumes with next input, claims.
- `DisambiguationState`: installs on `AmbiguousTarget`; resolves correctly.
- Bridge: legacy command dispatches through bridge; hook order preserved; signals match.
- Multipuppet: `relay=False` rules skip on relay sessions.

### Game-side tests

Test actions directly, not via raw string:

```python
def test_kick_moves_ball(self):
    action = Kick(target=self.ball)
    trace = RuleEngine().dispatch(action, Actor(character=self.char), ctx)
    self.assertEqual(trace.outcome, "succeeded")
    self.assertNotEqual(self.ball.location, self.room)
```

### Regression

All existing `world/tests/` pass throughout. Track legacy cmdset dispatch
rate (verbs still falling through to `generate_cmdset_providers`) in CI;
target 0% by Phase 8.

---

## Migration Cheatsheet

| Command pattern | Action system equivalent |
|---|---|
| `key = "kick"` | `@action("kick")` on `Action` subclass |
| `aliases = ["boot"]` | `@action("kick", "boot")` |
| `locks = "cmd:all()"` | no `requires` |
| `locks = "cmd:perm(Builder)"` (code-defined gate) | `@rule(..., requires=Builder)` — `Capability` bit, resolved fresh; introspectable. `from_lockstring()` transpiles code lockstrings. Persisted/runtime per-object locks keep the DSL (one shared `Capability` model). |
| `obj.access(x, "traverse")` / other lock types | `check` rule + `Predicate` `requires`; `from_lockstring(ls, "traverse")` transpiles, `LegacyLock` covers custom lockfuncs |
| `help_category = "Combat"` | `@rule(..., help_category="Combat")` |
| `def parse(self): self.target = ...` | `Action.parse(cls, raw_args, actor, ctx)` classmethod |
| `def func(self): ...` | `@rule(MyAction, phase="carry_out")` on owning class; return `CLAIM` if primary handler |
| custom `at_pre_cmd` gate | `@rule(MyAction, phase="check")` — pure predicate, no side effects |
| `AccountCommand` | `@rule(..., actor_type=Account)` |
| `allow_multipuppet_relay = False` | `@rule(..., relay=False)` |
| `CmdSet.at_cmdset_creation: self.add(Cmd)` | rule declared on typeclass directly |
| `EvMenu(caller, menudata)` | unchanged — `EvMenu()` wrapper installs `EvMenuState` internally; new flows prefer `@interactive` `carry_out` rule |
| node-graph menu / deferred-input command | `@interactive` `carry_out` rule: `response = yield "prompt"` (sync-AS1 generator pattern, not `await`) |
| blocking I/O in a command `func` | `defer.in_thread(fn).addCallbacks(...)` / `defer.background(fn)` in a `carry_out`/`report` rule (never in `check`) |
| `caller.search(x)` → multimatch prompt | `AmbiguousTarget` exception → `DisambiguationState` — automatic |

---

## Done Means

- [ ] All five Pre-Implementation Gates checked and documented.
      (Gate 1 CLEARED — see Result table; Gates 2/3/5 decided; Gate 4 designs
      pending review.)
- [~] `evennia/actions/` package with full test coverage.
      (**213** engine tests green; game-side movement + RP/object tests green;
      full `world/tests/` suite not yet re-run as a gate.)
- [x] Benchmark results recorded (Gate 1) — proceed cache-free.
- [x] Rule-body contract enforced against the AS1 boundary: `before`/`check`
      synchronous (no block/yield/defer); `carry_out`/`report` drive
      `@interactive` and `Deferred` rules with serialized `CLAIM` ordering.
      Verified in engine tests + rent/programdoor/@airroom/grid flows.
- [~] All engine-internal cmdsets retired.
      (6d movement substrate shipped; session/login/account cmdsets still legacy.)
- [~] All game-side commands in `commands/` ported.
      (families 1–2 largely done; families 3–8 remain — see ledger §C.)
- [ ] Compatibility bridge removed.
- [ ] `CmdSet` / `CmdSetHandler` retired.
- [ ] String locks eliminated: `Capability` + `Predicate` are the sole gate
      system; `db_lock_storage` transpiled, `LegacyLock` at 0%, `LockHandler`
      eval path removed.
- [ ] `cmdhandler.py` simplified to action dispatch only.
- [ ] `actor.explain(action_type)` works and returns full trace.
- [ ] `EvMenu` API preserved via `EvMenuState` wrapper; `@interactive`
      `carry_out` rules available for new linear flows.
- [ ] All existing `world/tests/` pass.
- [ ] `docs/writing_commands.md` rewritten for new model.
- [ ] `docs/architecture.md` command system section updated.
- [ ] Engine pin bumped at each phase boundary.
- [ ] `engine-architecture/decisions.md` CM1 entry marked done; I1/L1 initial scope noted as shipped via CM1.
- [ ] PR description links this roadmap.
