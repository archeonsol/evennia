# ALPHA: jobs/ + event bus — rename the bus, document the contract

Status: in-progress (cross-repo: engine + game). **Premise corrected
2026-08-19** — scope narrowed from "wire or cut" to "rename + document + fix
the persist set". **Item 1 (the rename) is done**, uncommitted, in both repos;
items 2 and 3 remain.

## Correction: both subsystems are wired

This prompt previously opened with:

> Two subsystems are fully built in the engine but have **zero in-repo
> consumers** (verified by grep; contrib excluded)

**That is no longer true, for either subsystem.** The grep predates the
game-side wiring. Step 1 of the old approach ("read the game repo to see whether
it already has usage") has now been done, and the answer is yes twice over. The
wire-or-cut decision is therefore **settled as wire**, and nothing should delete
either package, its models in `server/0004`, or its settings keys on the
strength of the old framing.

The evidence, verified against both trees:

### `evennia/jobs/` — wired, eleven registered job types

`JOB_QUEUE_REGISTRY` in the game's `server/conf/settings.py` maps eleven types to
handlers in `world/engine_jobs.py`:

`help_index_rebuild`, `search_index_rebuild`, `active_entity_index_rebuild`,
`channel_subscriber_cache_rebuild`, `game_event_export`, `staff_pending_cleanup`,
`discord_webhook`, `channel_discord_webhook`, `email_send`,
`moderation_network_lists`, `moderation_screen_signup`.

`JOB_QUEUE_ENABLED = True`, `JOB_QUEUE_BACKEND = "redis"`. Enqueue sites:
`server/conf/at_server_startstop.py` (four), `world/account_signup.py`,
`world/channels/discord_bridge.py`, `world/channels/staff_discord.py`,
`world/mail.py`, `world/systems.py`. Drained on the reactor by the
`job-queue-drain` system in `world/systems.py` through `process_pending_jobs`.

### `evennia/events/bus.py` — wired through one game-side choke point

`EVENT_BUS_ENABLED = True`, `EVENT_BUS_BACKEND = "both"` (Redis + Postgres).

The game funnels every emission through `world/audit/emit.py`, which exposes
`audit_economy_transfer`, `audit_moderation_action`,
`audit_staff_pending_resolve`, and `audit_perm_change`. Callers:
`world/rpg/economy.py`, `world/staff_pending.py`, `world/tickets/core.py`.

The engine also emits: `evennia/moderation/sanctions.py::_audit` fires
`moderation.sanction_issue` and `moderation.sanction_revoke` on every issue and
revoke.

`GameEvent` rows are read back by `job_game_event_export` in
`world/engine_jobs.py`, which writes JSON lines under `GAMELOG_DIR`.

---

## Remaining work

Three items. None of them is a wire-or-cut decision.

## 1. Rename `evennia.events` to `evennia.eventbus` — DONE (uncommitted)

Landed 2026-08-19 in both working trees, not yet committed or released. What
shipped:

- `evennia/events/` moved to `evennia/eventbus/` (`git mv`, history preserved).
- `evennia/eventbus/__init__.py` docstring now names the distinction from
  `evennia.actions.events` explicitly, so the next reader does not have to
  rediscover it.
- `evennia/events/__init__.py` recreated as a one-release deprecation shim:
  re-exports `emit` / `subscribe` from the new path and raises
  `DeprecationWarning`. Verified that the shim's `emit` **is** the new `emit`
  (same object) and that the warning fires.
- Call sites updated: `evennia/moderation/sanctions.py` and the game's
  `world/audit/emit.py`.
- `EVENT_BUS_REDIS_STREAM = "evennia:events"` deliberately **unchanged** — it is
  a stream name, and changing it would orphan the live stream.
- API doc tree updated by hand to match what `_autodoc-index` would emit:
  `docs/source/api/evennia.eventbus.md` and `.bus.md` added,
  `evennia.events.bus.md` removed, `evennia.events.md` kept for the shim with
  its toctree dropped, and `evennia.md`'s toctree listing both.
  **Re-run the real generator before the release** (`cd docs && EVDIR=... make
  _autodoc-index`) — sphinx was not installed in this environment.
- Tests: `evennia.eventbus`, `evennia.moderation`, and `evennia.jobs` pass
  (224 tests). `ruff format` and `ruff check` clean.

**Still to do for this item:** cut the release (four files per
[`releases.md`](../docs/releases.md)), and drop the shim one release later.

### Original analysis, kept as the record


The name collision is the one part of the original prompt that survives intact,
and it is now doing measurable damage.

**The damage, concretely.** Grep `subscribe(` across both repos and *every hit*
resolves to `evennia/actions/events.py`'s decorator — the in-process
`EventRegistry` used by `actions/default/movement.py`, `actions/actor.py`, and
`accounts/accounts.py`. The bus's own `subscribe` has **zero consumers
anywhere**. So the top-level name is held by the module nobody imports, for the
concept everybody uses, and a reader greping for the live system lands in the
wrong package every time.

### Why the rename is cheap

Four import sites in total, and none of the usual hazards apply:

| Site | Note |
| --- | --- |
| `underspire/world/audit/emit.py:24` | the **only** game-side line |
| `evennia/moderation/sanctions.py:214` | lazy import inside `_audit` |
| `evennia/events/__init__.py:11` | the package itself |
| `evennia/events/tests.py:7` | imports `evennia.events.bus` directly |

- **Not a Django app.** Not in `INSTALLED_APPS`; no `models.py`, no
  `migrations/`. `GameEvent` lives in `evennia/server/models.py`, i.e. the
  `server` app. **No table rename, no `db_table` change, no migration.**
- **Not in the flat API.** `evennia/__init__.py` has no `events` entry, so there
  is no `evennia.events` attribute contract to preserve.
- **Settings are already path-free.** Keys are `EVENT_BUS_*`. The one string that
  looks like a path, `EVENT_BUS_REDIS_STREAM = "evennia:events"`, is a Redis
  stream *name* — **leave it unchanged**, or the rename orphans the live stream.
- Both real call sites are lazy imports inside functions; there is no
  module-level import ordering to disturb.

### The hazard is silence, not breakage

`world/audit/emit.py` wraps its import in `try: ... except: log_trace(...)`. If
the engine renames and the game is not updated in the same change, the bus
**silently stops recording** — economy transfers, moderation actions, staff
pending resolves, and permission changes all quietly stop being audited, and
nothing raises.

Mitigations, both cheap, do both:

1. Land engine and game in one coordinated cross-repo change.
2. Keep `evennia/events/__init__.py` as a shim for one release, re-exporting from
   `evennia.eventbus` and issuing a `DeprecationWarning`. This converts a silent
   audit outage into a log line if the repos drift anyway.

Drop the shim in the release after, and note the removal in
[`CHANGELOG-FORK.md`](../../CHANGELOG-FORK.md).

### A second, softer collision to note but not fix

The game has a Django signal named `security_alert` in `world/ai/events.py`,
unrelated to the bus. It does not conflict at import level and is out of scope
here — recorded so a future reader does not mistake it for a third event system.

## 2. Fix the `EVENT_BUS_PERSIST_SUBJECTS` mismatch

Found while verifying the above. The game's persist set and the subjects
actually emitted do not line up in **either** direction:

```python
# underspire/server/conf/settings.py
EVENT_BUS_PERSIST_SUBJECTS = frozenset({
    "moderation.flag",      # nothing emits this
    "moderation.action",    # emitted by world/audit/emit.py
    "economy.transfer",     # emitted by world/audit/emit.py
    "security.alert",       # nothing emits this
})
```

- **Two listed subjects never fire.** `moderation.flag` is not emitted anywhere:
  `moderation/flags.py::raise_flag` writes the `ModerationFlag` row and does not
  touch the bus. `security.alert` is not emitted either; the similarly named
  game-side Django signal is a different mechanism.
- **Two firing subjects are not listed.** The engine's own
  `moderation.sanction_issue` and `moderation.sanction_revoke` fire on every
  sanction decision and are **not** persisted, so they are best-effort and
  non-durable — while the game's `moderation.action` (permission changes, staff
  pending resolutions) *is* durable.

That asymmetry is almost certainly unintended: the engine's sanction decisions
are the most consequential events the bus carries and currently the least
durably recorded of the set.

Decide per subject rather than by blanket edit:

- Should `raise_flag` emit `moderation.flag`? A flag is already a durable row, so
  the bus emission is only worth it if a subscriber wants the notification. The
  W1 console's flag-queue live feed is a plausible first such subscriber.
- Should `sanction_issue` / `sanction_revoke` be persisted? Note the `Sanction`
  table already carries a tamper-evident hash chain, so the bus copy is a
  convenience for export and analytics, not the record of truth. Say which it is
  in the docstring.
- Should `security.alert` be dropped from the set, or is an emitter missing?

## 3. Document the engine ↔ game usage contract

The line the original prompt wanted drawn ("machinery in the engine, usage in the
game") turns out to be already drawn correctly in the code and simply undocumented.
Write it down so the next reader does not re-derive it:

- **Jobs.** The engine owns enqueue, lease, retry, backoff, dead-letter, and the
  `SELECT FOR UPDATE SKIP LOCKED` dequeue. The game owns `JOB_QUEUE_REGISTRY`
  (settings-declared, dotted paths), the handler bodies, and the drain cadence.
  Handlers run on the reactor and offload blocking work through
  `evennia.utils.defer`; the job is acked before that I/O completes, which is
  best-effort by design. Note that `register_job_type()` exists for runtime
  registration and has no consumers — the settings dict is the used path.
- **Event bus.** The engine owns `emit`, `subscribe`, the backends, and
  persistence policy. The game owns which subjects it emits, which it persists,
  and any subscriber. `world/audit/emit.py` is the reference shape for a game-side
  choke point: one module, sanitized bounded payloads, failures logged and
  swallowed so an audit write never breaks the operation it describes.

Both belong in the engine's published docs rather than only here.

---

## Scope boundary

- **In scope:** the rename and its shim, the persist-set reconciliation, and the
  usage-contract documentation.
- **Out of scope:** `evennia/actions/events.py` (the live in-process system —
  leave it, it keeps its name); the unified scheduler (shipped); deleting either
  subsystem or its models.

## Coordination

- **[`ALPHA-migration-squash.md`](ALPHA-migration-squash.md).** The old prompt
  noted the squash "gets simpler if cut." It is not cut, so **the squash gains
  nothing here**: `GameEvent` and `EngineJob` stay, and `server/0004` folds into
  the squashed `server` initial with both models intact. Update that expectation
  before planning the squash.
- **[`W1-console-implementation-plan.md`](W1-console-implementation-plan.md).**
  D6 records the same finding and depends on it: the console ships a Jobs panel
  and a `GameEvent` view. **Do the rename before the console names a panel**, or
  a UI label cements the ambiguity between the two event systems. D2 also has the
  console emitting a `console.*` subject onto this bus, which is a new caller to
  account for in the persist-set decision.

## Done means

`evennia.events` no longer shadows `evennia.actions.events`; the game's single
import site is updated in the same change and a deprecation shim covers one
release; `EVENT_BUS_PERSIST_SUBJECTS` lists only subjects that fire and every
subject that should be durable; and the jobs and bus usage contracts are written
down in the published docs.
