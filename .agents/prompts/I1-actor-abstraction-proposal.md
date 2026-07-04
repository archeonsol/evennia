# I1 design proposal: retire `puppet`, split identity / body / channel

Status: proposal — round 2 (round-1 items resolved; concurrency + feature
axes added)
Parent task: [`I1-actor-abstraction.md`](I1-actor-abstraction.md)

## Problem

`session.puppet` is a single-valued pointer that conflates three
independent roles:

1. **Identity** — the persistent self: account binding, stats, skills,
   perms, XP.
2. **Body** — what currently receives commands (meat / matrix avatar /
   rigged vehicle / bot).
3. **Channel** — the session(s) carrying I/O.

Controlling a different body means destroying the pointer and rebuilding
it: `unpuppet_object` (veto hook + `del obj.account` + signal) →
`puppet_object` (access + `at_pre_puppet` + DB write `obj.account` /
`session.puid` + `at_post_puppet` + signal + cmdset-merge warmup)
(`evennia/accounts/accounts.py:547`).

Because the swap *erases* the link to who is really behind the body, the
game rebuilds it by hand:

- `avatar.db.controlling_character` dbref stashed on the avatar/rig.
- ~7 forwarding methods on `MatrixAvatar` (`get_stat_level`,
  `roll_check`, `get_skill_level`, `get_psychosis_stage`, …) each call
  `get_controlling_character()` → dbref deref → delegate.
- Multi-stage `delay()` + `reattach` flags to survive reload
  (`typeclasses/matrix/mixins/jack_in.py`).

Swap = pointer-destroy + manual-rebuild. That is the friction.

## Design: swap the body, not the puppet

The character is the durable **identity**; avatar/vehicle/bot is a
transient **body** the same identity drives. Move a lightweight focus,
leave identity fixed.

### Roles, decoupled

| role     | what it is                       | swaps?            | lives in            |
|----------|----------------------------------|-------------------|---------------------|
| identity | persistent self, stats, perms    | never mid-session | `ControlBinding`    |
| body     | receives input, dispatch-effective | freely (a stack) | `ControlBinding.focus_stack` |
| channel  | sessions doing I/O               | connect/disconnect | runtime on session  |

### Primitive

```python
class Embodiable(Protocol):           # engine never type-checks concretely
    location: object
    def msg(self, *a, **k) -> None: ...
    def search_for(self, name, *a, **k): ...
    def get_display_name(self, looker, **k) -> str: ...

@dataclass
class Actor:
    session: object = None             # the originating stream (per-dispatch)
    account: object = None
    binding: "ControlBinding" = None   # the durable control graph
    _focus_snapshot: object = None     # body captured at dispatch start
    _gen_at_start: int = 0             # binding.generation captured at start

    @property
    def identity(self):                # persistent IC self — explicit FK
        return self.binding.identity

    @property
    def focus(self):                   # active body = top of stack, never None
        return self._focus_snapshot or self.binding.focus

    @property
    def effective(self):               # what dispatch acts as — never None
        return self.focus
```

The stack bottom is **always** an explicit object: `[account]` at the
character-select screen, `[account, character]` after `@ic`,
`[account, character, avatar]` after a jack-in. `focus`/`effective` is
the top and therefore never `None` — no `x or y or z` fallback chain, no
`None`-checks scattered through the engine (round-2 Q2). `identity`
stays a distinct FK on the binding (the lowest *IC* body — the
character), separate from the account at the stack floor.

### Body swap — O(1), no hook cascade

```python
def push_focus(self, body):            # was puppet_object(session, avatar)
    self.binding.push(body)
    engine.emit(FocusChanged(old=..., new=body, actor=self))

def pop_focus(self, body=None):        # was unpuppet + re-puppet character
    dropped = self.binding.collapse_to(body)   # see stack semantics
    engine.emit(FocusChanged(old=dropped, new=self.focus, actor=self))
```

No `at_pre/post_puppet`, no `obj.account`/`session.puid` writes, no
cmdset-merge warmup (no cmdsets exist — the action engine replaced them).

## Decisions locked (review round 1)

### 1. Focus is a **stack** (nested control)

`focus_stack` is ordered, top = active. Jack into Matrix →
`push(avatar)`; rig a bot from inside → `push(bot)`. Today this is
impossible without nested puppet swaps.

**Pop semantics (the subtle part):** inner control depends on the body
beneath it still being focused, so popping a body *collapses everything
above it*. `collapse_to(body)` pops until `body` is the top (or the
stack bottoms out at identity); a forced jackout of a mid-stack body
unwinds every body above it, emitting one `FocusChanged` per level so
each body's teardown subscriber fires in order. Out-of-order release is
therefore always well-defined.

### 2. **Rip `session.puppet` out fully**

No shim. `session.puppet` / `session.puid` / `obj.account` /
`get_puppet` / `get_all_puppets` are removed; readers move to
`Actor`/`ControlBinding`. Audit scope (core, non-test, non-contrib):

- `server/serversession.py`, `server/session.py` — session ↔ body link,
  msg routing.
- `server/inputfuncs.py` — input routing to the effective object.
- `accounts/accounts.py` — `puppet_object`/`unpuppet_object`/`*_all`
  delete; replaced by `ControlBinding` ops.
- `objects/mixins/lifecycle.py`, `objects/character.py` —
  `at_*_puppet` hooks become `@subscribe(FocusChanged)`.
- `locks/lockfuncs.py` — `pperm`/puppet-aware lockfuncs read identity.
- `typeclasses/models.py`, `utils/utils.py` — `.puppet` conveniences.
- `commands/default/{account,building}.py` — `@ic`/`@ooc`/examine.

`@ic` / `@ooc` become `push_focus(character)` / `pop_focus()` over the
binding. `AccountCommand` retires — the Actor knows OOC (focus is the
account) vs IC intrinsically.

### 3. Persist in a **dedicated model**

```python
class ControlBinding(models.Model):
    account     = FK(AccountDB, related_name="control")
    identity    = FK(ObjectDB, related_name="control_binding")
    focus_stack = JSONField(default=list)   # [account, identity, ...bodies], top last
    generation  = PositiveIntegerField(default=0)  # bumps on push/pop (race guard)
    # sessions are runtime-only — re-attached at connect, never stored
```

Queryable ("who controls what"), one row per identity, survives reload
by reconstruction (no staged `delay()` replay, no `reattach` flag).
JSON(B) for the stack matches the engine's recent JSONB direction.
Needs one migration.

### 4. Identity still hears select messages — **relay rule**

Default delivery is focus-only. A `FocusChanged`-aware relay rule
forwards messages explicitly tagged for it (msg option
`relay_to_identity=True`) to the identity even while focus is elsewhere
— so "your body convulses in the rig" reaches the meat body during a
matrix dive, without spamming every line. The tag is the opt-in; absence
= focus-only.

## What this deletes / simplifies in game code

- `avatar.db.controlling_character` + `get_controlling_character()` and
  all ~7 `MatrixAvatar` stat/skill forwarders → avatar rules read
  `actor.identity` directly.
- `reattach` plumbing and the staged disconnect `delay()` chain → rebuild
  binding from the model on `at_sync`.
- `at_pre_puppet`/`at_post_puppet`/`at_*_unpuppet` overrides across
  Character, MatrixAvatar, riggable → `@subscribe(FocusChanged)` on the
  existing action event bus. One lifecycle mechanism, not two.

## Performance

- swap: list op + one event vs unpuppet(hook+DB del)+puppet(hook+DB
  write+warmup).
- stats on a focused avatar: one `actor.identity` resolve vs a dbref
  deref + dict lookups per forwarded call.
- reload: one row read + reconstruct vs staged puppet replay.

## I2 (multi-puppet) readiness

One account driving two characters = two `ControlBinding` rows = two
Actors, each with its own focus stack. No global puppet table, no
contention. I2 becomes "more than one binding per account" — already the
model's shape.

## Done means (for the eventual implementation PR)

- `ControlBinding` model + migration; `Actor` reshaped around it.
- `session.puppet` & co. removed; all core readers migrated.
- `push_focus`/`pop_focus` + `FocusChanged` event + relay rule.
- Jack-in / rigging / `@ic` / `@ooc` ported to focus ops; avatar
  delegation deleted.
- Tests: stack push/pop/collapse, reload reconstruction, relay, OOC↔IC.
- `engine-api-architecture.md` I1 entry updated.

## Concurrency: the multi-session stack race (round 2)

Sessions are runtime-only and several may attach to one binding
(multisession sharing), so two sessions share one `focus_stack`. The
reactor is single-threaded — no true parallelism — but `carry_out` /
`report` **suspend** (`inlineCallbacks`), so between a `yield` and its
resume another session's command runs and can mutate the shared stack.
That is a cooperative-interleaving window, not preemption.

Mitigation (three parts):

1. **Per-session Actor token.** The `Actor` passed to the engine is an
   explicit `(session, binding)` pair, never shared across sessions.
2. **Focus snapshot.** `dispatch` resolves `focus` once at entry into
   `Actor._focus_snapshot`; the whole dispatch acts on the body it
   started with, even if a co-session pops the stack mid-suspend.
3. **Generation guard.** `ControlBinding.generation` bumps on every
   push/pop. The dispatch captures it at start (`_gen_at_start`); on
   resume past a suspend, a structural mismatch re-validates the body or
   aborts with a defined message ("your connection shifted") instead of
   silently acting on a popped body.

Shared stacks stay legal (that *is* sharing); the guard makes structural
mutation safe rather than forbidding it.

## Body-owned vs identity-owned stats (round 2 feature axis)

The role split turns "whose stats" from hardcoded delegation into a
per-stat policy, because identity and body are now distinct objects both
reachable from the Actor:

```python
class StatSource(Enum):
    BODY = auto()       # the focused body's own stats
    IDENTITY = auto()   # the meat character behind it

def resolve_stat(actor, stat_key, source):
    holder = actor.focus if source is StatSource.BODY else actor.identity
    return holder.get_stat_level(stat_key)
```

- avatar uses meat stats → `IDENTITY` (one line; deletes the 7
  `MatrixAvatar` forwarders).
- avatar has its own stats → `BODY` (weak hacker, god-tier avatar).
- mixed → matrix-combat from `BODY`, willpower/psychosis from `IDENTITY`
  (psychosis "follows the meat" becomes an explicit `IDENTITY`
  declaration, not a special-case method).

The body declares which stats it owns vs inherits.

## Co-residency: the Silverhand pattern (extension, builds on I1)

The focus stack is about **control**. A second consciousness in one body
(sees what the host sees + private back-channel, no control) is a
different axis. I1's split makes it expressible; it needs one extra
primitive on the body:

```
body.controller  -> Actor whose focus top is this body (drives input)
body.residents   -> identities co-present in the body (host + passenger)
```

- **shared perception** — R1 viewer seam: a body's perceived event is
  delivered to the controller *and* every resident. A passenger attaches
  `OBSERVER` (read-only feed, no control).
- **private inner voice** — an `InnerVoice` event whose `providers()`
  scope is `body.residents` only; never the room. Room-invisible by
  construction on the existing action event bus — no new delivery path.
- **control handoff** — passenger grabs the wheel = a focus op flipping
  the body's controller; residency set unchanged.

Scope: the `residents` layer overlaps I2 (multi-identity) and R1
(perception/viewer), so it ships as an **extension on top of I1**, not
core I1 — but the identity/body split is its precondition. Unbuildable
today: one `puppet`, no notion of "present but not controlling."

## Round-1 items — resolved

- **Q1 `FocusChanged` shape → one event per level.** Ordered teardown:
  drone powers down sensors, *then* its vehicle engages the brake. A
  flat collapse forces manual loops/switches back into handlers.
- **Q2 account as focus target → explicit stack floor.** `focus`/
  `effective` is always the stack top and never `None`; the floor is the
  account (`[account]` at char-select). Eliminates the fallback chain and
  scattered `None`-checks. (Folded into the primitive above.)
- **Q3 migration → clean cut at deploy.** Bindings are lightweight rows
  (account FK + identity FK + stack); a boot routine clears legacy
  session pointers and bulk-populates fresh `ControlBinding` rows on
  first start. No data-migration script.
