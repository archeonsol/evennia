# Fork changelog

This file tracks divergences in this fork from upstream Evennia. Upstream's
own `CHANGELOG.md` is preserved unchanged; this is the fork-only history.

Versions use [PEP 440 local segments](https://peps.python.org/pep-0440/#local-version-identifiers):
`<upstream-version>+underspire.<n>`, where the trailing integer increments
on each tagged fork release. The `+local` suffix means `pip` will install
this build correctly and it will never collide with a published upstream
release of the same base version.

Read `evennia.__version__` at runtime; it returns the full string with the
current git rev appended.

## Maintenance

**Don't compress individual release entries.** Forensic value ("why does X
work this way?") comes from the original detail; compression destroys it.
Git history and `grep` cover any browsing needs.

**Rotation policy: by upstream major, not by file size.** When the upstream
base bumps (e.g. `6.0.0` → `6.1.0` and the first `6.1.0+underspire.1`
ships), move the entire previous-major block (`6.0.x+underspire.*`) into
`CHANGELOG-FORK-6.0.x.md` next to this file, then start this file fresh
with the new major's first entry. The boundary is natural; the archive
file stays grep-able and never goes stale.

Mid-major rotation by date or size is a fallback only if a single major
balloons past ~3000 lines before its successor ships — at that point a
dated archive (e.g. `CHANGELOG-FORK-2026H1.md`) is acceptable. As of
`+underspire.14`, the file is ~1100 lines and well inside the comfortable
range, so no archival action is pending.

See [`.agents/docs/releases.md`](.agents/docs/releases.md) for the
matching release procedure.

---

## 6.0.0+underspire.36 — Display-name cache moves to game (Phase 5)

Removes `evennia.utils.display_name_cache` from the engine. The cache
existed to memoize `obj.get_display_name(looker)` per looker, but its
invalidation triggers (`bump_recog_generation` / `bump_sdesc_generation`)
were never called from core. They were only ever called by downstream
games (rpsystem contrib, Underspire) that override `get_display_name`
with sdesc/recog logic expensive enough to warrant caching. Stock
Evennia paid the coordination cost (per-looker ndb dict, generation
protocol on objects, TTL bookkeeping, two settings, conditional imports
in `msg_contents` and `funcparser`) to memoize a function whose default
body is roughly a lockstring check plus an f-string.

Concretely, the downstream coupling included a reach-in to engine
private symbols (`_cache`, `_enabled`, `_perception_gen`, `_ttl`) to
build a custom cache key, and a `bump_*_generation` call paired
immediately with `invalidate_display_name_cache` because the generation
protocol alone wasn't trusted. Both go away when the cache lives next
to the override that makes display-name resolution expensive.

`get_display_name` itself remains the hook. Only the cache around it
moves. Games with cheap default `get_display_name` see no change; games
with expensive overrides (Underspire, rpsystem) own their own
memoization and invalidation policy.

### Engine

- [`evennia/utils/display_name_cache.py`](evennia/utils/display_name_cache.py):
  deleted. Module exported `cached_get_display_name`,
  `invalidate_display_name_cache`, `bump_recog_generation`,
  `bump_sdesc_generation`. All gone.
- [`evennia/utils/tests/test_display_name_cache.py`](evennia/utils/tests/test_display_name_cache.py):
  deleted.
- [`evennia/objects/mixins/messaging.py`](evennia/objects/mixins/messaging.py):
  `msg_contents` now calls `obj.get_display_name(looker=receiver)`
  directly inside the per-receiver display_names mapping. The
  try/except cache import with inline fallback is gone.
- [`evennia/utils/funcparser.py`](evennia/utils/funcparser.py):
  the `$you` and `$your` callables now call
  `caller.get_display_name(looker=receiver)` directly. Both sites had
  the same try/except cache pattern; both are now the plain call.

### Settings

- [`evennia/settings_default.py`](evennia/settings_default.py):
  removed `MSG_DISPLAY_NAME_CACHE_ENABLED` and
  `MSG_DISPLAY_NAME_CACHE_TTL`.

### Migration

Downstream games that called `bump_recog_generation`,
`bump_sdesc_generation`, `invalidate_display_name_cache`, or imported
`cached_get_display_name` from `evennia.utils.display_name_cache` must
move that logic into their own `get_display_name` override on the
relevant typeclass. The override is where the cache, the key, and the
invalidation triggers all belong. Underspire's migration to a
self-contained cache inside `roleplay_mixin.get_display_name` is the
worked example.

Games with no `get_display_name` override or a cheap one need no
migration. The `MSG_DISPLAY_NAME_CACHE_*` settings can be deleted from
local `settings.py` if present; otherwise Django will warn about them
as unknown settings (which is harmless).

### Tests

`evennia.utils.tests.test_funcparser` (84 tests) and
`evennia.objects.tests.test_objects` (142 tests) pass. The deleted
`test_display_name_cache` had 3 tests; they covered the deleted module
and have no replacement on the engine side.

### Rationale

This change reifies a principle now applied across the fork: the engine
hosts code that pays off agnostically; game-shaped optimizations belong
in the game. Hooks (the seams games plug into, like `get_display_name`)
stay in engine even when no engine code exercises them. Implementations
of behavior only some games want (the cache *around* the hook) move to
the game. See Phase 5 in
[`.fleet-review/engine-cleanup-checklist.md`](.fleet-review/engine-cleanup-checklist.md)
for the full reasoning.

---

## 6.0.0+underspire.35 — Redis attr cache write-behind ordering (Phase 2)

Closes a phantom-data window in the Redis L2 attribute cache. Previously,
[`RedisCachedModelAttributeBackend.do_update_attribute`](evennia/typeclasses/redis_attr_cache.py)
called `_cache_set` immediately after `super().do_update_attribute()`,
which only marks the attr dirty for later `bulk_update`. A crash between
that Redis publish and the next `flush_all_dirty` left Redis serving
values PostgreSQL never received, persisting until TTL expiry (~1h).

After this release the invariant is uniform: **Redis is never more
current than PG**, regardless of write path.

### Engine

- [`evennia/typeclasses/redis_attr_cache.py`](evennia/typeclasses/redis_attr_cache.py):
  removed the `do_update_attribute` override entirely. Redis is now
  republished only from `flush_dirty`, which was already ordered
  correctly (calls `super().flush_dirty()` first; re-raises on
  `bulk_update` failure; only writes Redis for the exact attrs the
  parent confirmed flushed). Same-process readers still see new values
  immediately via the `AttributeHandler` in-process cache, which holds
  the mutated `attr` instance directly. Cross-process readers see stale
  Redis values until the next flush tick, matching the implicit PG
  durability window.

- [`evennia/typeclasses/redis_attr_cache.py`](evennia/typeclasses/redis_attr_cache.py):
  added `invalidate_attrs(attrs)`. The orphan-dirty path
  (`_flush_orphan_dirty` for direct `attr.value = X` writes) calls
  `bulk_update` without going through any backend, so its writes would
  otherwise leave Redis stale forever. `invalidate_attrs` groups the
  flushed attrs by `db_model`, resolves owner pks via the through-table
  per model, and drops the affected Redis keys in one round-trip. Best-
  effort: no-op when Redis is disabled or unavailable.

- [`evennia/typeclasses/attributes.py`](evennia/typeclasses/attributes.py):
  `_flush_orphan_dirty` now calls `invalidate_attrs(dirty)` after a
  successful `bulk_update`.

### Settings

- [`evennia/settings_default.py`](evennia/settings_default.py):
  `ATTRIBUTE_FLUSH_ON_MAINTENANCE` defaults from `False` to `True`.
  With the publish-on-flush invariant in place, this caps cross-process
  Redis staleness at the maintenance tick (~60s) rather than the
  opportunistic `flush_if_pending` cadence (which depended on reads
  triggering a flush). Existing deployments that disabled this for
  perf reasons should set it explicitly in `settings.py`.

### Tests

- [`evennia/typeclasses/tests/test_attribute_fork.py`](evennia/typeclasses/tests/test_attribute_fork.py):
  three new regressions:
  - `test_update_does_not_publish_redis_before_flush` — handler-driven
    update marks dirty without touching Redis; `flush_dirty` publishes.
  - `test_flush_failure_does_not_publish_redis` — if `bulk_update`
    raises, Redis is left untouched and entries stay queued for retry.
  - `test_orphan_flush_invalidates_redis` — direct `attr.value = X`
    triggers Redis drop via `invalidate_attrs` after the orphan flush.

  `evennia.typeclasses` suite goes 55/55 green.

### Migration

No required changes for downstream games. Two behavior shifts to be
aware of:

1. Cross-process readers may briefly see a previous value for an
   attribute that was just written in another process, up to the next
   `flush_all_dirty` tick. Same-process readers are unaffected (they
   see the mutated in-memory attr immediately).
2. With `ATTRIBUTE_FLUSH_ON_MAINTENANCE` now defaulting `True`, the
   server maintenance tick will batch-flush dirty attrs every ~60s.
   Disable explicitly in `settings.py` if your deployment hand-tunes
   flush cadence elsewhere.

### Discovered (not fixed in this release)

`_flush_orphan_dirty` removes attrs from `_ORPHAN_DIRTY_ATTRS` before
calling `bulk_update`. If `bulk_update` raises, the dirty entries are
lost rather than retried — same failure shape that backend `flush_dirty`
was already hardened against. Captured as Phase 2b in
[`.fleet-review/engine-cleanup-checklist.md`](.fleet-review/engine-cleanup-checklist.md).

---

## 6.0.0+underspire.34 — Module-cached settings sweep (Phase 1)

Engine-wide cleanup of the "module-level `_X = settings.Y` snapshot
at import time" anti-pattern. These snapshots silently broke
`@override_settings` decorators (a class of tests was no-op'ing
without anyone noticing) and runtime reloads of `settings.py`.

After this release, behavioral settings are read via `settings.X`
directly at the call site across the engine. Django caches
`settings` attribute access internally, so the per-call cost is
sub-microsecond and the readability gain (no hidden snapshot layer)
is substantial. No `LazySetting` machinery was added; only revisit
if a real perf regression appears.

Touched-area test suites (`evennia.utils`, `evennia.server.tests`,
`evennia.web`, `evennia.accounts`, `evennia.commands`,
`evennia.typeclasses`, `evennia.events`, `evennia.help`) go
1154/1154 green.

### Engine — modules converted

- [`evennia/server/sessionhandler.py`](evennia/server/sessionhandler.py):
  six snapshots inlined — `FUNCPARSER_PARSE_OUTGOING_MESSAGES_ENABLED`,
  `BROADCAST_SERVER_RESTART_MESSAGES`, `SERVERNAME`, `MULTISESSION_MODE`,
  `IDLE_TIMEOUT`, `DELAY_CMD_LOGINSTART`.

- [`evennia/accounts/accounts.py`](evennia/accounts/accounts.py):
  six snapshots — `MULTISESSION_MODE` (3 sites),
  `AUTO_CREATE_CHARACTER_WITH_ACCOUNT`, `AUTO_PUPPET_ON_LOGIN`,
  `MAX_NR_SIMULTANEOUS_PUPPETS`, `MAX_NR_CHARACTERS`, `CMDSET_ACCOUNT`.

- [`evennia/accounts/bots.py`](evennia/accounts/bots.py):
  five bot-enabled flags (`IRC_ENABLED`, `RSS_ENABLED`,
  `GRAPEVINE_ENABLED`, `DISCORD_ENABLED` + token check) plus
  removed an unused `_IDLE_TIMEOUT` snapshot.

- [`evennia/commands/cmdhandler.py`](evennia/commands/cmdhandler.py)
  + [`evennia/commands/cmdsethandler.py`](evennia/commands/cmdsethandler.py):
  `IN_GAME_ERRORS` (multiple sites), `CMDSET_MERGE_CACHE_MAXSIZE`,
  `CMDSET_FALLBACKS`, `CMDSET_PATHS`.

- [`evennia/server/inputfuncs.py`](evennia/server/inputfuncs.py):
  `IDLE_COMMAND` (tuple-shaping logic moved to a `_idle_commands()`
  helper) and the `MXP_ENABLED and MXP_OUTGOING_ONLY` compound check.

- [`evennia/server/portal/portalsessionhandler.py`](evennia/server/portal/portalsessionhandler.py):
  `COMMAND_RATE_WARNING`, `MAX_CHAR_LIMIT_WARNING`.

- [`evennia/utils/funcparser.py`](evennia/utils/funcparser.py):
  `CLIENT_DEFAULT_WIDTH` (6 sites), `FUNCPARSER_MAX_NESTING`,
  `FUNCPARSER_START_CHAR`, `FUNCPARSER_ESCAPE_CHAR`. Default args on
  `FuncParser.__init__` changed from setting-snapshot defaults to
  `None` with body resolution.

  Also fixed a latent bug in this module: the max-nesting depth check
  was reading the module constant directly, ignoring any custom
  `max_nesting` passed to `__init__`. Now stored as
  `self.max_nesting` and read at the depth check.

- [`evennia/utils/evtable.py`](evennia/utils/evtable.py): `wrap()`
  and `fill()` default-arg pattern changed to `width=None` with body
  resolution (default args are evaluated at function-definition
  time, so a default of `settings.CLIENT_DEFAULT_WIDTH` snapshots
  at import).

- [`evennia/utils/evmore.py`](evennia/utils/evmore.py),
  [`evennia/utils/evmenu.py`](evennia/utils/evmenu.py),
  [`evennia/utils/eveditor.py`](evennia/utils/eveditor.py),
  [`evennia/utils/utils.py`](evennia/utils/utils.py): inlined
  `CLIENT_DEFAULT_WIDTH`/`HEIGHT` and `SEARCH_MULTIMATCH_TEMPLATE`.
  `evennia/utils/ansi.py` lost an unused `_COLOR_NO_DEFAULT`
  snapshot.

- [`evennia/typeclasses/tags.py`](evennia/typeclasses/tags.py)
  + [`evennia/typeclasses/attributes.py`](evennia/typeclasses/attributes.py):
  `TYPECLASS_AGGRESSIVE_CACHE` (17+ sites across cache short-circuits).
  Tests that flip this setting via `@override_settings` now actually
  exercise both code paths.

- [`evennia/typeclasses/models.py`](evennia/typeclasses/models.py):
  `PERMISSION_HIERARCHY` (originally a `[p.lower() for p in ...]`
  transform snapshotted at import) now rebuilds per call in
  `check_permstring`. 5-element list, sub-microsecond, irrelevant
  against the DB query already in the function.

- [`evennia/commands/default/*`](evennia/commands/default/):
  `MAX_NR_CHARACTERS`, `AUTO_PUPPET_ON_LOGIN`, `CLIENT_DEFAULT_WIDTH`
  (9 sites across `comms.py`/`building.py`),
  `BROADCAST_SERVER_RESTART_MESSAGES`.

- [`evennia/objects/object.py`](evennia/objects/object.py):
  `MULTISESSION_MODE` + the derived `_SESSID_MAX` constant. The
  latter is now a `_sessid_max()` helper.

- [`evennia/server/webserver.py`](evennia/server/webserver.py)
  + [`evennia/server/portal/webclient.py`](evennia/server/portal/webclient.py)
  + [`evennia/server/portal/webclient_ajax.py`](evennia/server/portal/webclient_ajax.py):
  `UPSTREAM_IPS`, `DEBUG`, `SERVERNAME`.

- [`evennia/help/filehelp.py`](evennia/help/filehelp.py):
  `DEFAULT_HELP_CATEGORY`.

- Contrib: `character_creator` (`MAX_NR_CHARACTERS`), `menu_login`
  (`CONNECTION_SCREEN_MODULE`, `GUEST_ENABLED`), `building_menu`
  (removed unused snapshot), `ingame_map_display` (`BASIC_MAP_SIZE`,
  `MAX_MAP_SIZE` via helpers; default-arg pattern in `Map.__init__`
  reworked).

### Tests — patch sites updated

Four existing tests had to monkey-patch the module-level snapshots to
exercise overrides. They now use `self.settings(...)` /
`@override_settings` as intended:

- [`evennia/commands/default/tests.py`](evennia/commands/default/tests.py)
  `test_ooc_look`: three nested `patch` blocks collapsed to one
  `self.settings(...)`.
- [`evennia/accounts/tests.py`](evennia/accounts/tests.py)
  `test_puppet_success`: `patch` → `self.settings`.
- [`evennia/typeclasses/tests/test_typeclasses.py`](evennia/typeclasses/tests/test_typeclasses.py)
  `test_attrhandler_nocache`: dropped redundant module-constant
  `patch`, kept `@override_settings`.
- [`evennia/utils/tests/test_funcparser.py`](evennia/utils/tests/test_funcparser.py)
  max-nesting test: now mutates `self.parser.max_nesting` directly
  (matches the bug fix that moved this onto the instance).

### Guideline

The "read settings at the call site" pattern is documented in
[`.agents/docs/code-style.md`](.agents/docs/code-style.md) under
"Settings reads", with the rationale and the carve-out for true
boot constants (paths, crypto issuer, encodings — eight remaining
sites are deliberately left as import-time snapshots). No
automated guard: catching this in review is enough.

### Migration notes

- **Downstream code that imports any of the removed `_X` module
  constants** (e.g. `from evennia.accounts.accounts import _MULTISESSION_MODE`)
  must read `from django.conf import settings; settings.X` instead.
  Most consumers wouldn't import these since the underscore prefix
  signals "private to module", but worth checking.
- **`FuncParser` subclasses that overrode `_MAX_NESTING`** at module
  level no longer affect the depth check. Override
  `self.max_nesting` after `super().__init__()` instead, or pass
  `max_nesting=N` to `__init__`.
- **`_HELP_TEXT` width display in `eveditor.py`** is still
  formatted at import time (the f-string in the help text is
  cosmetic, not runtime-critical). Editor width math elsewhere
  in the module now reads live.

---

## 6.0.0+underspire.33 — Tier 1 security/correctness fixes from fleet-review audit

Targeted pass over the highest-signal findings from the fleet-review
engine audit. Five concrete fixes plus a defense-in-depth invariant
test. Touched-area test suites (`evennia.utils.tests.test_text2html`,
`evennia.server.tests`, `evennia.web`, `evennia.events`) go 143/143
green, including the previously stale `test__server_maintenance_reset`
left over from `.29`'s `_runtime_config_row` optimization.

### Security

- [`evennia/server/evennia_launcher.py`](evennia/server/evennia_launcher.py)
  `_check_database` move-existing-superuser confirmation prompt
  replaces `eval(input("Continue [Y]/N: "))` with `input(...).strip()`.
  The `eval` was a latent footgun (empty input would `SyntaxError`)
  with no purpose at that prompt; the loop logic still works because
  it operates on strings.

- [`evennia/server/portal/amp.py`](evennia/server/portal/amp.py)
  `AMPConnection.data_in` legacy pickle fallback is now gated on
  `AMP_SESSION_ACCEPT_LEGACY_PICKLE`. The docstring already documented
  this as the contract; previously the code accepted legacy pickle
  unconditionally regardless of setting. Aligns admin-channel
  behavior with the matching gate already in
  [`amp_serde.accept_legacy_session_pickle`](evennia/server/amp_serde.py).

- [`evennia/server/portal/amp.py`](evennia/server/portal/amp.py)
  `receive_functioncall` allowlist is now fail-closed. Empty
  `AMP_FUNCTIONCALL_MODULES` disables FunctionCall entirely (matching
  the [`settings_default.py`](evennia/settings_default.py) docstring's
  "an empty tuple disables FunctionCall entirely"); previously empty
  meant "allow any module", which inverted the safer default. Vanilla
  ships a populated tuple so this only affects deployments that
  explicitly emptied the setting — those were relying on a fail-open
  behavior bug.

- [`evennia/server/portal/webclient.py`](evennia/server/portal/webclient.py)
  `_send_text_legacy` adds a sharp warning comment on the
  `client_raw=True` opt-out so a future contributor doesn't
  accidentally route player-influenced content through it.
  No behavior change.

- [`evennia/utils/tests/test_text2html.py`](evennia/utils/tests/test_text2html.py)
  New `test_parse_html_escapes_user_html` pins the XSS invariant
  that the fleet-review audit had flagged. `parse_html` is the trust
  boundary for the webclient (default_out plugin renders its output
  via `.html()`/string concat), so raw `<`, `>`, `&` from upstream
  text must always come out as entities before any tag generation.
  This was already true via `re_string` → `sub_text` running first
  in [`text2html.TextToHTMLparser.parse`](evennia/utils/text2html.py);
  the test exists so a future refactor can't silently regress it.

### Correctness

- [`evennia/web/api/views.py`](evennia/web/api/views.py)
  `ObjectDBViewSet.set_attribute` no longer treats falsy `db_value`
  (`0`, `False`, `""`) as a delete request. Now keys deletion off
  `value is None`, so REST clients can store legitimate zero/empty
  values without them being silently removed.

- [`evennia/events/bus.py`](evennia/events/bus.py)
  `emit` stores `actor.dbref` (e.g. `"#42"`) instead of `str(actor)`
  (which was usually the account/object name). The docstring already
  advertised "stored as dbref string only"; this brings the code
  into line so actor refs survive renames and remain stable for
  audit joins.

### Tests

- [`evennia/server/tests/test_server.py`](evennia/server/tests/test_server.py)
  `test__server_maintenance_reset` was asserting the pre-`.29`
  contract (`conf("runtime", value)` called every tick). Since
  `.29`'s `_runtime_config_row` optimization, the first maintenance
  tick reads via `conf("runtime", default=0.0)` and subsequent ticks
  persist directly to the cached `ServerConfig` row. Test updated to
  match the new contract.

### Migration notes

- **REST clients**: any tooling that relies on PUT-ing
  `{"db_value": 0}` or `{"db_value": ""}` to delete an attribute via
  the REST API must now send no `db_value` field (or explicit `null`)
  to delete. The old falsy-delete behavior was a bug.

- **`AMP_FUNCTIONCALL_MODULES`**: if your `settings.py` explicitly
  sets this to an empty tuple, FunctionCall is now disabled instead
  of allowing any module. Restore the default by removing the
  override or by populating it with `("evennia.server.portal.amp_server",
  "evennia.server.amp_client")`.

- **`AMP_SESSION_ACCEPT_LEGACY_PICKLE`**: if you have two processes
  mid-rolling-restart with the legacy pickle wire format on the
  *admin* channel (separate from the session channel which was
  already gated), set this to `True` for the restart window. Vanilla
  installs and any fully-upgraded fork are not affected.

- **`GameEvent.actor_ref` format**: new event rows store dbrefs
  (`"#42"`) instead of names. Old rows keep their existing format.
  Anything that filters or joins on `actor_ref` will see mixed
  formats across the changeover boundary.

---

## 6.0.0+underspire.32 — Cmdset cache invalidation, stale typeclass paths, `id()` cache keys

Follow-up cleanup pass closing out the remaining fleet-review batches.
No API surface change — downstream consumers should be able to bump
straight from `.31` and run. No new migrations.

Full test suite goes from 3 failing on `.31` to 2 (the last two are
a wilderness contrib that's broken at import-time and one upstream
test stale from `.29`'s `_runtime_config_row` optimization).

### Engine — cmdset cache subsystem (A1, A2, A3, T2-7)

- [`evennia/objects/mixins/movement.py`](evennia/objects/mixins/movement.py)
  `MovementMixin.move_to`: after a successful move, bumps the
  `_cmdset_generation` counter on both the source and destination
  locations via `evennia.commands.location_cmdset_cache.bump_cmdset_generation`.
  The location-cmdset cache key is keyed by location generation, so this
  is what actually invalidates it when room contents change.
- [`evennia/objects/mixins/lifecycle.py`](evennia/objects/mixins/lifecycle.py)
  `LifecycleMixin.delete`: bumps the location's generation before
  nulling `self.location`. Object deletion now invalidates the
  containing room's cached cmdset set.

  These two together fix the long-standing "after the first cache fill,
  players entering a room see the original cmdset forever" bug —
  `bump_cmdset_generation` was previously only fired on cmdset *stack*
  mutations, not on the movements/lifecycle events that change what
  the cache aggregates over.

- [`evennia/commands/cmdhandler.py`](evennia/commands/cmdhandler.py)
  Local-cmdset cache restructure. Now caches the filtered object list
  (post-`access('call')` filter, the expensive per-caller work) instead
  of the final `cmdset_stack` list. On each dispatch we re-run
  `at_cmdset_get(caller=caller)` per object and re-read each object's
  `cmdset.cmdset_stack`. `at_cmdset_get` is documented as a per-request
  dynamic hook for game code that mutates the cmdset stack live; the
  old cache silenced those mutations from the 2nd dispatch onward.
- `cmdhandler.py` also shallow-copies the gathered csets before
  applying the room-gather `duplicates` rule (treat `None` as `True`).
  The previous in-place mutation on shared cset references raced
  concurrent dispatches that interleaved through `yield` points in
  the merge loop, occasionally leaking `duplicates=True` past the
  intended restore. The restore loop is gone — copies are per-call,
  nothing's shared with the next dispatch, originals stay `None`.
- [`evennia/commands/cmdsethandler.py`](evennia/commands/cmdsethandler.py)
  `_invalidate_cmd_access_caches` no longer swallows exceptions
  silently; a failure here would leak stale permission decisions
  (cached `cmd.access` results returning True for commands whose
  cmdset was just revoked).

### Engine — stale `evennia.objects.objects` typeclass paths

After the underspire object-module refactor, `DefaultObject` and
friends live at `evennia.objects.object.DefaultObject` (singular,
since each class moved into its own module). The old
`evennia.objects.objects` path remains only as a compatibility shim
that re-exports the new classes. `__class__.__module__` always returns
the new singular path.

- [`evennia/locks/lockfuncs.py`](evennia/locks/lockfuncs.py): three
  `utils.inherits_from` calls (in `perm`, `perm_above` via `perm`,
  and `_to_account`) passed the stale plural string, making the
  `DefaultObject` check unconditionally return False for puppeted
  characters. Puppet/quell/perm-above lockfuncs all then routed
  through the no-account branch and ignored the account's permissions
  — including the security-relevant "puppet escalation prevention"
  path. Updated to the new singular path.
- [`evennia/objects/tests/test_objects.py`](evennia/objects/tests/test_objects.py),
  [`evennia/prototypes/tests.py`](evennia/prototypes/tests.py),
  [`evennia/commands/default/tests.py`](evennia/commands/default/tests.py):
  fixtures and assertions all updated. The spawner's
  `prototype_from_object` normalises typeclass paths via
  `class_from_module(...).__module__`, so it emits the new singular
  form. The tests now match.

### Engine — settings-blind regex caches

- [`evennia/commands/cmdparser.py`](evennia/commands/cmdparser.py) and
  [`evennia/objects/manager.py`](evennia/objects/manager.py) both
  module-cached `re.compile(settings.SEARCH_MULTIMATCH_REGEX)` at
  import time. Tests and downstream code using `@override_settings`
  or runtime mutation could not affect the cached value. Both now go
  through `evennia.utils.multimatch._multimatch_regex()` which compiles
  freshly from current settings each call (already the pattern used
  elsewhere in the multimatch module). Compile cost is negligible.

### Engine — `id()` cache keys replaced with stable identifiers

CPython recycles `id()` after GC. A freed object plus a freshly-allocated
object at the same address would silently alias under any cache that
used `id()` as part of its key — returning the prior object's result.

- [`evennia/utils/display_name_cache.py`](evennia/utils/display_name_cache.py):
  the per-looker ndb cache keyed on `(id(obj), id(looker), ...)`.
  `looker` is already implicit in the per-looker ndb store, so removing
  `id(looker)` from the key is a tidiness fix. `id(obj)` swapped for
  `obj.pk` (falling back to `id(obj)` only for unsaved transient
  objects, which can't outlive their dispatch anyway).
- [`evennia/commands/cmd_access_cache.py`](evennia/commands/cmd_access_cache.py):
  `id(session)` in the lookup key swapped for `session.sessid`.
- [`evennia/locks/lockhandler.py`](evennia/locks/lockhandler.py): the
  per-caller lock check cache keyed on `(id(self.obj), ..., id(session))`
  for Command instances (which have no pk). Now keys on
  `(class.__module__, class.__name__, cmd.obj.pk, ..., session.sessid)`.
  Two Command instances of the same class bound to the same DB object
  share lock decisions (which is correct — the lockstring is class-level
  and `holds()`-style lockfuncs check the bound object). Commands without
  a bound `cmd.obj` or sessions without `sessid` now bypass the cache
  rather than aliasing on `id()`.

### Tests

- [`evennia/objects/tests/test_objects.py`](evennia/objects/tests/test_objects.py)
  `test_search_autopick`, `test_search_ordinal_last`,
  `test_search_location_scope`: updated to expect the documented
  `quiet=True` returns a list contract. The previous assertions
  documented an alternate "autopick fires in quiet mode" contract
  that conflicted with `test_search_by_tag_kwarg`'s also-pre-existing
  expectation and that would have silently broken downstream callers
  iterating quiet-mode results as a list. Autopick semantics are
  preserved via the non-quiet path.
- [`evennia/commands/tests.py`](evennia/commands/tests.py) removed
  the stale `test_num_differentiators`. It exercised a suffix-N
  regex form ("look me-3") that was removed when the counting
  feature was reworked. `test_num_differentiators_hyphenated_names`
  below covers the current prefix-N syntax.
- [`evennia/typeclasses/tests/test_typeclasses.py`](evennia/typeclasses/tests/test_typeclasses.py):
  one dotted-path string in `test_typeclass_search__inputs` still
  pointed at the old single-module location of the test fixture
  class (before `tests.py` was moved into the `tests/` package).
  Updated to the new path.

### Migration

None. No schema changes, no settings changes, no API surface change.
Downstream consumers should be able to bump from `.31` to `.32` and
run directly.

---

## 6.0.0+underspire.31 — Make `0020_remove_redundant_tag_index` tolerate phantom-applied history

[`evennia/typeclasses/migrations/0020_remove_redundant_tag_index.py`](evennia/typeclasses/migrations/0020_remove_redundant_tag_index.py)
raised on databases whose history was recorded against the earlier
no-op revision of `0017_use_index_instead_of_index_together_in_tags`
(its docstring at line 12 acknowledges the original `0017` "never ran
any ops on fresh databases"). On those DBs Django records `0017`
applied but the index `typeclasses_db_key_be0c81_idx` was never
created; the subsequent `0018` `RenameIndex` is also recorded but
silently renames nothing; then `0020`'s `RemoveIndex` blows up trying
to drop an index that isn't there.

Production PG was unaffected because either it was initialized after
`0017` was rewritten to be non-empty, or the rename failure was
resolved at the time. Downstream consumers with longer migration
histories hit the failure on upgrade to `.30`.

### Engine

- [`evennia/typeclasses/migrations/0020_remove_redundant_tag_index.py`](evennia/typeclasses/migrations/0020_remove_redundant_tag_index.py):
  swap the bare `RemoveIndex` op for a `SeparateDatabaseAndState` block.
  - `database_operations` runs `DROP INDEX IF EXISTS
    typeclasses_db_key_be0c81_idx` so the DDL succeeds on both
    DBs-that-have-the-index and DBs-that-never-did. `reverse_sql`
    matches with `CREATE INDEX IF NOT EXISTS`.
  - `state_operations` keeps the original `RemoveIndex` so Django's
    model state stays aligned regardless of which database path
    actually ran. Subsequent migrations and `makemigrations` won't
    see a phantom index.

### Migration

No behavior change for fresh databases or PG production. DBs that
hit the failure on `.30` upgrade can re-run `evennia migrate` after
upgrading to `.31` and the index drop will silently no-op.

---

## 6.0.0+underspire.30 — Fleet review pass: attribute typed-column stabilization, shutdown/Discord/cmdset fixes

A multi-batch correctness pass driven by a fleet-review of the
underspire fork against the engine surface. The headline fix unblocks
in-place mutation of container attributes on the typed-column path —
the regression that silently broke `self.db.dct[k] = v` everywhere
the JSON branch was taken (149 xyzgrid tests failing among others).
Plus a stack of smaller correctness fixes across the shutdown, Discord,
sessionhandler, scene-resolution, attribute query/cache, job queue,
channel cache, event bus, and script-pause paths.

Net effect: full test suite goes from **262 failing → 20 failing**.
The remaining 20 are pre-existing issues in unrelated areas (search
edge cases, prototypes, lock-system corners) that already failed on
`.29`; none are regressions from this release.

### Engine — attribute typed-column refactor

- [`evennia/typeclasses/attributes.py`](evennia/typeclasses/attributes.py)
  value getter, JSON branch: deserialized container values are now wrapped
  in `_Saver*` proxies via `from_pickle(raw, db_obj=self)`. Without this,
  every `attr.value` read returned a fresh `json.loads()` dict/list and
  in-place mutations (`self.db.map_data[zcoord] = mapdata`, etc.) were
  silently lost. Pre-refactor pickle storage masked this because `Saver*`
  wrappers wrote through the value setter; the JSON path missed it.
- `_classify_value` / `_is_json_safe`: tuples (and any container holding
  a tuple anywhere in its tree) now route to pickle instead of JSON to
  preserve type. Tuples have no JSON type and were silently demoted to
  lists on read.
- `_classify_value`: ints outside the signed 64-bit range now route to
  pickle. `db_int_val` is a `BigIntegerField`; oversize values raised
  `DataError`/`OverflowError` at INSERT before.
- `_classify_value`, value getter JSON branch: `json.loads(None)` or
  malformed JSON now logs and returns `None`, matching the pickle
  branch's existing corruption-recovery behavior.
- `value_query_filter`: container queries now route to `db_str_val`
  with `db_val_type='json'` (was returning zero rows by querying the
  empty `db_value`). Every primitive branch now pins `db_val_type` so
  a string search can't collide with a JSON row whose serialized text
  matches. Oversize-int branch routes to the pickle column.
- [`evennia/objects/manager.py`](evennia/objects/manager.py)
  `get_objs_with_attr_value`: the str branch is now exact-match instead
  of `__iexact`, aligning with int/float/bool/none branches and
  `value_query_filter`. **Behavior change**: callers that relied on
  case-insensitive string attribute search will now miss. If you need
  case-insensitive, filter on `db_attributes__db_str_val__iexact`
  explicitly.
- [`evennia/typeclasses/attributes.py`](evennia/typeclasses/attributes.py)
  `ModelAttributeBackend.flush_dirty`: clears `_dirty_attrs` *after*
  `bulk_update` succeeds (via `difference_update`, so concurrent dirty
  marks survive). On failure, dirty entries stay queued and the backend
  stays in `_DIRTY_BACKENDS` so the next maintenance tick retries.
  Returns the flushed list so the Redis subclass can publish exactly
  what was written.
- [`evennia/typeclasses/redis_attr_cache.py`](evennia/typeclasses/redis_attr_cache.py)
  `_cache_set` gains `nx_only=True`. `query_key` uses it after PG read
  so a concurrent writer's newer Redis value isn't overwritten with the
  stale PG snapshot (TOCTOU close). `flush_dirty` now consumes the
  flushed list returned from super so it never re-publishes
  unflushed attrs.
- New migration
  [`0022_attribute_typed_value_indexes`](evennia/typeclasses/migrations/0022_attribute_typed_value_indexes.py):
  composite indexes on `(db_val_type, db_int_val)` and `(db_val_type,
  db_float_val)`. Every value-equality query pins `db_val_type` so the
  discriminator-first composite lets the planner do an index range scan
  instead of a sequential Attribute-table scan. `db_str_val` deliberately
  unindexed for now (PG btree page-overflow risk on long values; revisit
  with a partial/hash index if a real callsite warrants it).

### Engine — server / sessionhandler / Discord

- [`evennia/server/service.py`](evennia/server/service.py)
  `shutdown()` now awaits hook Deferreds via a new `_await_hooks` helper
  (was discarding them via list-comprehension-for-side-effects). User
  hooks returning Deferreds — `at_server_reload`, `at_server_shutdown`,
  `unpuppet_all` — are now actually awaited; the reactor no longer stops
  with writes in flight. Per-instance hook errors are caught and logged
  so one bad hook doesn't abort the rest.
- `shutdown()` re-entry guard for concurrent SRELOAD/SRESET/SSHUTD.
  Coexists with the SIGINT handler's pre-set flag by skipping the guard
  when `_reactor_stopping=True`.
- `server_maintenance` attribute-flush failures: now `log_err` with a
  consecutive-failure counter, escalating to a `CRITICAL` log after 3
  consecutive failures. Was silently `log_trace`-only; the only safety
  net for the write-behind cache could fail invisibly.
- [`evennia/server/portal/discord.py`](evennia/server/portal/discord.py)
  `resume()`: was referencing an undefined `self.sequence_id` (the
  attribute is `self.last_sequence`) and lacked a `return` after the
  `identify()` fallback. Calls raised `AttributeError`, killing the
  websocket. Fixed both.
- `get_gateway_url`: added an errback, and both the non-200 branch and
  the new error path now reset `is_connecting=False` so the
  `ReconnectingClientFactory` can actually retry. A failed gateway HTTP
  fetch previously left the bot wedged forever.
- [`evennia/server/sessionhandler.py`](evennia/server/sessionhandler.py)
  `_flush_outbuf`: was merging every kwarg with last-wins for non-text
  keys, silently dropping concurrent OOB/GMCP/prompt payloads on
  overlapping `data_out` calls in the same reactor tick. Pure-text
  messages still coalesce; any message carrying non-text kwargs now
  ships as its own AMP frame, restoring pre-batcher one-frame-per-call
  semantics.

### Engine — scene_index removal

The Redis-backed room membership cache for `msg_contents` recipient
resolution was deleted. Its mutation API (`on_move`, `add_to_room`,
`remove_from_room`) was never wired into `MovementMixin.move_to`,
`LifecycleMixin.delete`, `Character.at_pre_puppet/at_post_unpuppet`,
or `ObjectDB.at_db_location_postsave`. Once a room's Redis set was
populated lazily, it never refreshed — characters' movements,
deletions, and unpuppets all left stale membership, so `msg_contents`
served wrong recipient lists indefinitely. Additionally,
`resolve_recipients` filtered by `ROOM_SCENE_INDEX_TYPECLASS_PATHS`
which silently dropped any non-Character recipient (scripted props,
broadcast relays, items overriding `at_msg_receive`).

- Removed: `evennia/objects/scene_index.py`,
  `evennia/objects/tests/test_scene_index.py`.
- [`evennia/objects/mixins/messaging.py`](evennia/objects/mixins/messaging.py)
  `get_message_recipients` reverted to direct contents walk.
- [`evennia/settings_default.py`](evennia/settings_default.py):
  `ROOM_SCENE_INDEX_ENABLED`, `ROOM_SCENE_INDEX_REDIS_ALIAS`, and
  `ROOM_SCENE_INDEX_TYPECLASS_PATHS` removed.

### Engine — misc correctness

- [`evennia/jobs/queue.py`](evennia/jobs/queue.py) `_dequeue_db`: wraps
  the claim in `transaction.atomic()` with `SELECT FOR UPDATE SKIP
  LOCKED`. Two concurrent workers no longer claim the same pending row
  and run the job twice. Falls back to a plain SELECT on SQLite.
- [`evennia/comms/channel_subscriber_cache.py`](evennia/comms/channel_subscriber_cache.py)
  `sync_channel_subscribers`: pipeline now uses `transaction=True` so
  the DELETE+SADD pair is atomic. A concurrent `add_subscriber` /
  `remove_subscriber` is no longer silently overwritten.
- [`evennia/comms/models.py`](evennia/comms/models.py)
  `SubscriptionHandler.add`: the `add_subscriber` cache call sat outside
  the loop, referencing the leaked loop variable. Only the last
  subscriber was registered when multiple were added at once. Fixed.
- [`evennia/events/bus.py`](evennia/events/bus.py): per-call
  `persist=False` now wins over the backend default. The previous
  `backend in (...) or _should_persist(...)` ordering made the opt-out
  unreachable when `EVENT_BUS_BACKEND` forced postgres/both.
- [`evennia/scripts/scripts.py`](evennia/scripts/scripts.py)
  `_pause_task`: added a `self.pk is None` guard before
  `save(update_fields=...)`. Test fixtures and pre-start lifecycle
  paths that called `pause()` on unsaved Scripts no longer raise
  `ValueError: Cannot force an update in save() with no primary key`.
- [`evennia/scripts/taskhandler.py`](evennia/scripts/taskhandler.py)
  `TaskHandler.add`: scans for the lowest free ID (pre-refactor
  behavior) instead of monotonically growing forever. Freed IDs are
  reused, restoring downstream expectations.
- [`evennia/scripts/ondemandhandler.py`](evennia/scripts/ondemandhandler.py)
  `save()`: validates each task entry individually so a single
  unpicklable/recursive task only purges itself instead of bailing the
  whole save and losing all good timer state.

### Migration

- **Apply `evennia migrate`** — three new migrations:
  - `typeclasses/0022_attribute_typed_value_indexes` (additive
    `AddIndex`; online on PG 11+).
  - `scripts/0019_backfill_paused_state_from_attributes` (RunPython
    data migration; copies the old Attribute-based pause state into
    the columns introduced by `0018` and removes the orphan
    Attribute rows).
- **Settings cleanup (optional)**: remove `ROOM_SCENE_INDEX_*` from
  `server/conf/settings.py` if set. Harmless if left — Python ignores
  unknown settings — but they're dead config now.
- **Behavior change to verify**:
  `get_objs_with_attr_value(name, "<string>")` is now exact-match. If
  you have search code relying on the previous case-insensitive
  behavior, replace with an explicit
  `Q(db_attributes__db_str_val__iexact=value)` filter.
- **Transparent improvements (no code change required)**: in-place
  container mutations (`obj.db.dct[k] = v`) now persist, tuples come
  back as tuples, large ints don't crash, container-value queries
  actually match, hook-returning-Deferred users get awaited, OOB/GMCP
  payloads in the same tick no longer overwrite each other.

### Tests

- New `_classify_value` coverage in
  [`tests/test_attribute_fork.py`](evennia/typeclasses/tests/test_attribute_fork.py)
  for tuple-in-container demotion, int overflow, JSON-path classification.
- Fixed stale `attr:v1:*` SCAN pattern assertion (production is
  `attr:v2:*`).
- Moved [`evennia/typeclasses/tests.py`](evennia/typeclasses/tests/test_typeclasses.py)
  into the `tests/` package. The file was being silently shadowed by
  the `tests/` package and never ran; Python 3.14 strict discovery
  also refused to walk past the duplicate name. The 489 lines of legacy
  typeclass tests are now running again.

---

## 6.0.0+underspire.29 — Admin AMP sessiondata serde fixes

Backfilled: bug fixes on `evennia/server/amp_serde.py` for the admin
sessiondata map. See `0aa7b3f92`, `36cff5abc`, `7db8c1536`, `c2699aa81`.

---

## 6.0.0+underspire.28 — Replace pickle RCE vectors with JSON serde

Backfilled: security work replacing pickle deserialization on AMP
channels with a JSON-only serde. See `9df79df85`, `820b9c439`.

---

## 6.0.0+underspire.27 — Fix `CmdSetHandler.clear()` storage corruption

Backfilled: cmdset storage preserved list type on clear. See `2c320f00e`,
`226d0450f`.

---

## 6.0.0+underspire.26 — CSP fix, language handler guard, engine migration

Backfilled. See `6bf7402b6`, `606e8cf38`.

---

## 6.0.0+underspire.25 — Fix `TypeError` on shutdown: `clear_all_sessids()` is sync

Backfilled. See `f071b3bc5`, `429e0fd91`, `7f248e7fe`.

---

## 6.0.0+underspire.24 — Fix `evennia` launcher infinite recursion in non-TTY shells

[`evennia/server/evennia_launcher.py:1549-1551`](evennia/server/evennia_launcher.py)
`check_database`'s "no Account#1 found" branch unconditionally
recursed into itself after calling `create_superuser()`. In
interactive shells `createsuperuser` blocks for input and the recursion
exits naturally once an account exists. In **non-interactive shells**
(CI runners, scripted tooling, `evennia makemigrations` piped through
anything), Django's `createsuperuser` skips silently with a
"Superuser creation skipped due to not running in a TTY" notice. The
account remains absent. The recursion has no termination condition
and burns CPU until something kills it.

This bug isn't new — long-standing in the launcher — but it became
visible during the `.22`/`.23` migration-drift investigation when
`evennia makemigrations` was attempted from agent shells. Caught
during follow-up review.

### Engine

- [`evennia/server/evennia_launcher.py`](evennia/server/evennia_launcher.py):
  replaced the recursion with a direct `AccountDB.objects.filter(id=1).exists()`
  check after `create_superuser()`. If the account still doesn't exist
  (the create silently no-op'd), print the same `ERROR_DATABASE`
  template the other failure paths use, with a body explaining the
  non-interactive-shell case and pointing at the
  `EVENNIA_SUPERUSER_USERNAME/EMAIL/PASSWORD` env-var alternative.
  `always_return=True` callers still get a `False` return instead of
  exiting, matching the existing contract.

### Migration

None. Pure bug fix; behaviour in interactive shells is unchanged
(Django's interactive `createsuperuser` still blocks, account gets
created, function returns).

---

## 6.0.0+underspire.23 — Pin Django exactly to stop hash-drift migration churn

Downstream review of `.22` surfaced unrecorded model changes in two
engine apps (`server`, `typeclasses`). Investigation showed two
distinct drift causes:

1. **Django version drift on auto-generated index names.** Django's
   `_create_index_name` derives a 6-char hash from `(table, fields)`
   that has not been stable across Django minor versions. Migrations
   generated on Django 5.x baked in one hash; running on 6.0.4 today
   computes a different one. The autodetector sees the mismatch and
   keeps wanting to rename indexes that already exist on disk under
   the old name — pure noise, but it surfaces as drift on every
   release.
2. **`AutoField` vs `BigAutoField` in old migrations.** The
   `server` app's migration `0004_gameevent_enginejob` was written
   before [`settings_default.py:381`](evennia/settings_default.py)
   set `DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"`, so the
   recorded `id` field types disagree with what the project default
   would generate today. Pure drift, no behavioural impact.

This release addresses cause (1) for the future and documents what
remains manual.

### Engine

- [`pyproject.toml`](pyproject.toml): tightened the Django pin from
  `django >= 6.0.2, < 6.1` to `django == 6.0.4` (the version that
  generated the most recent migrations, including the typeclasses
  `0018_rename_tag_…` index rename). With an exact pin, hash names
  are stable across upgrades and a future Django minor bump cannot
  silently re-introduce this class of drift. Bumping Django becomes
  a deliberate engine release with a regenerated set of migrations,
  not a transparent dependency update.
- [`uv.lock`](uv.lock): regenerated to lock to Django 6.0.4.

### Migration

Downstream consumers should re-resolve their lockfile after pulling
this release. Anyone who had been resolving to a 6.0.x newer than
6.0.4 will pin back to 6.0.4 — no schema impact, but the resolver
output will change. Add this to the consumer's CI to catch any
*future* drift at PR time rather than at deploy:

```yaml
- name: Check for migration drift
  run: evennia makemigrations --check --dry-run
```

This is recommended in the consumer's CI, not the engine's, because
the engine ships against the default settings while consumers ship
against their own. The check only catches drift against the
configuration that will actually run in production.

### Known unfixed (deferred to a future release)

The pin closes the door on new drift but doesn't retroactively fix
the existing mismatches surfaced by `.22` review. Two engine
migrations still need to be written and shipped:

- **`server`**: `AlterField id` on `gameevent` and `enginejob`
  (`AutoField → BigAutoField`), plus `RenameIndex` on
  `gameevent.subject_created_at` from `_0e8f0d_idx` to
  `_40e896_idx`. Safe to land via straight `makemigrations` output.
- **`typeclasses`**: an index reconciliation on `tag`. The
  autodetector proposes `RemoveIndex` of
  `typeclasses_tag_db_key_db_category_db_tagtype_db_model_idx`, but
  blindly accepting would drop the index on deployed DBs. Correct
  fix requires confirming the actual on-disk index name on
  production (`SELECT indexname FROM pg_indexes WHERE tablename =
  'typeclasses_tag'`) and writing a `SeparateDatabaseAndState`
  migration that aligns recorded state without touching schema.

Both are tracked for a follow-up release. The downstream `world`
app likely needs its own `makemigrations` pass (separate concern,
consumer-side fix).

---

## 6.0.0+underspire.22 — Help prefix rip-out, test-suite regression fix

The Phase-3 `@`-prefix convention says `@kudos` and `kudos` are distinct
commands and the parser does not strip the prefix
([code-style.md Command Naming](.agents/docs/code-style.md)). The help
command's display layer was still papering over that convention by
hiding the `@` whenever no non-prefixed twin existed, leaving users
reading `kudos` in the index and getting "did you mean @kudos?" when
they typed it. The whole prefix-tolerance layer is removed.

### Engine

- [`evennia/commands/command.py`](evennia/commands/command.py): dropped
  `_HELP_PREFIX_CHARS` constant and the `stripped_key`/`stripped_aliases`
  block that built the `no_prefix` field in `search_index_entry`. The
  index entry no longer carries a parallel unprefixed variant.
- [`evennia/commands/default/help.py`](evennia/commands/default/help.py):
  dropped the duplicate `_HELP_PREFIX_CHARS`, the `no_prefix` field on
  `HelpCategory.search_index_entry`, the `strip_prefix` local function
  in `do_search`, the `no_prefix` lunr search field (boost 6), the two
  rerank loops that treated prefixed/unprefixed matches as equivalent,
  the `strip_cmd_prefix` method, and its five call sites (index listing,
  text-search suggestions, topic header, alias list, suggestion list).
  Help now displays the registered key verbatim — `@kudos` shows as
  `@kudos`. Want `help kudos` to also resolve? Add `kudos` as an alias
  on the command; aliases are still indexed.
- [`evennia/help/filehelp.py`](evennia/help/filehelp.py),
  [`evennia/help/models.py`](evennia/help/models.py): dropped the empty
  `"no_prefix": ""` placeholders in `search_index_entry` for schema
  consistency now that the lunr field is gone.
- [`evennia/objects/character.py`](evennia/objects/character.py):
  re-applied the `+underspire.21` `_last_puppet` fix here, since the
  recent objects-module split moved `DefaultCharacter` to its own
  file and the new override did not carry the fix from the old
  monolithic `objects.py:3371`. Without it `@ic` between characters
  would silently regress on `.22+`.

### Behaviour delta

- Exact `help <unprefixed-key>` no longer resolves to a prefixed
  command via the `no_prefix` indexed alias. The fuzzy suggester
  (`COMMAND_FUZZY_SUGGESTIONS_MAX_DIST = 2`) still surfaces `@kudos`
  when you type `kudos` (edit distance 1) as a "did you mean…"
  suggestion, so users are not stranded — they just stop being
  silently auto-routed.
- Considered bumping `COMMAND_FUZZY_SUGGESTIONS_MAX_DIST` to 3 to
  "make room for prefixes"; rejected. Distance-3 on short tokens
  (`look`, `get`, `say`) produces explosive false positives that kill
  the suggestion UX. The rip-out alone covers the prefix case.

### Tests

- Renamed [`evennia/objects/tests.py`](evennia/objects/tests.py) →
  `evennia/objects/tests/test_objects.py`. The `tests/` directory was
  introduced in `+underspire.21` with `__init__.py` so Django could
  discover `test_scene_index.py` and the new `_last_puppet` test, but
  Python's package-over-module precedence then silently shadowed the
  950-line `tests.py` — `evennia.objects.tests` resolved to the new
  empty package and the legacy suite went dark in CI. Migrating the
  file into the package restores discovery without churning callers
  (`evennia.objects.tests.SomeTest` still resolves). This is a `.21`
  regression that should have been caught in that release.

### Known pre-existing failures (not introduced here)

The full `evennia.objects` suite now reports 6 failures, all in the
multimatch tests that landed in `+underspire.20` (`f2511a5d4`):

- `DefaultObjectTest.test_search_autopick`
- `DefaultObjectTest.test_search_ordinal_last`
- `DefaultObjectTest.test_search_location_scope`
- `TestObjectManager.test_get_objs_with_key_and_typeclass`
- `TestObjectManager.test_get_objs_with_key_or_alias`
- `TestObjectManager.test_search_object`

These were dark in CI for the same `tests.py`-shadowing reason as
above, so they were never observed at PR time. The root cause is a
mismatch between the multimatch test expectations and
[`evennia/objects/object.py:729-731`](evennia/objects/object.py)
`at_search_result`'s `quiet=True` branch: tests expect autopick /
single-unwrap to fire under `quiet=True`, but the implementation
short-circuits to `return list(results)` first. Left for a follow-up
release because the fix is a deliberate semantic choice (whose contract
wins, `quiet=True` documented behaviour or `try_autopick` ergonomics).

### Migration

No downstream changes required. Games that relied on the help-display
prefix-strip to make admin commands appear "unprefixed" in the help
index should add explicit unprefixed aliases on those commands — that
was always the right shape under the Phase-3 convention.

---

## 6.0.0+underspire.21 — Engine bug fixes (swap_typeclass hooks, character _last_puppet)

Two genuine engine bugs caught during a five-item bug audit (three of
the five turned out to be false positives on re-read; see the release
commit body for the dispositions).

### Engine

- [`evennia/typeclasses/models.py:684`](evennia/typeclasses/models.py):
  `swap_typeclass(..., run_start_hooks="hook_a hook_b")` raised
  `AttributeError` when given multiple space-separated hook names.
  The loop variable was `start_hook` but the call used the original
  `run_start_hooks` string, so `getattr(self, "hook_a hook_b")` was
  attempted on every iteration. Single-name strings happened to work
  because `split()` yields one token equal to the original. Fixed to
  `getattr(self, start_hook)()`. Effect: multi-hook callers (anything
  passing more than one name to `run_start_hooks`) now actually run
  each named hook once instead of erroring on the first iteration.
- [`evennia/objects/objects.py`](evennia/objects/objects.py)
  `DefaultCharacter.at_post_puppet` did not update
  `account.db._last_puppet`. `DefaultObject.at_post_puppet` sets it,
  but the character override didn't call `super()` and didn't set
  the attribute itself, so the value only got written at character
  creation ([`accounts.py:987`](evennia/accounts/accounts.py)),
  initial setup, and the admin path. Switching `@ic` between
  characters left `_last_puppet` stale, so reconnect / auto-puppet
  flows ([`accounts.py:1738`](evennia/accounts/accounts.py),
  [`accounts.py:2075`](evennia/accounts/accounts.py)) could re-attach
  the wrong character. Fixed by setting `_last_puppet` directly in
  the character override rather than chaining `super()` (the parent
  also emits a "You become …" message which would have duplicated
  the character's own message).

### Tests

- Added [`evennia/typeclasses/tests/test_swap_typeclass_hooks.py`](evennia/typeclasses/tests/test_swap_typeclass_hooks.py)
  — attaches two `Mock` methods, calls `swap_typeclass` with both
  names, asserts each fires exactly once. Without the fix the test
  raises `AttributeError` on the bad `getattr`.
- Added [`evennia/objects/tests/test_character_post_puppet.py`](evennia/objects/tests/test_character_post_puppet.py)
  — clears `account.db._last_puppet`, calls `char1.at_post_puppet()`,
  asserts the attribute points at the character. Without the fix it
  stays `None`.
- Added [`evennia/objects/tests/__init__.py`](evennia/objects/tests/__init__.py).
  The directory was missing the package marker, so Django's test
  loader silently skipped both the new test and the pre-existing
  [`test_scene_index.py`](evennia/objects/tests/test_scene_index.py)
  (which had been dark in CI since it was added). Both now discover
  and pass.

### Migration

Downstream should not need changes. The hook-loop fix only affects
callers passing multi-name `run_start_hooks` strings, which would
previously have hard-errored — no working code depended on the
broken behavior. The `_last_puppet` fix restores the documented
contract; any downstream override of `DefaultCharacter.at_post_puppet`
that needs to preserve the new behavior should either call `super()`
or set `self.account.db._last_puppet = self` itself.

---

## 6.0.0+underspire.20 — Multimatch UX (ordinals, location scope, auto-pick)

Natural-language object multimatch: display and input use `first` / `second` /
`last` / `other` (two matches only), plus `my` / `here` / `worn` location scopes
and conservative inventory/room auto-pick. Numeric `1-sword` prefix form remains
supported.

### Engine

- New [`evennia/utils/multimatch.py`](evennia/utils/multimatch.py) — shared
  parsing, labels, location hints, auto-pick.
- [`evennia/settings_default.py`](evennia/settings_default.py) — default
  `SEARCH_MULTIMATCH_REGEX` (prefix numeric), `SEARCH_MULTIMATCH_TEMPLATE`
  (`{label}`), `SEARCH_MULTIMATCH_INPUT`, `SEARCH_MULTIMATCH_AUTOPICK`,
  `SEARCH_MULTIMATCH_LOCATION_PREFIXES`, `SEARCH_MULTIMATCH_WORN_FILTER`.
- [`evennia/objects/manager.py`](evennia/objects/manager.py),
  [`evennia/objects/objects.py`](evennia/objects/objects.py),
  [`evennia/utils/utils.py`](evennia/utils/utils.py) `at_search_result`,
  [`evennia/commands/cmdparser.py`](evennia/commands/cmdparser.py) — wired to
  multimatch helpers.
- [`evennia/typeclasses/models.py`](evennia/typeclasses/models.py) —
  `get_extra_info` delegates to `location_hint`.

### Migration

- Multimatch **display** changes from `sword-1` to `first sword` (etc.). Input
  `1-sword` still works.
- Auto-pick is **on** by default (`SEARCH_MULTIMATCH_AUTOPICK = True`). Set
  `False` to always show the multimatch prompt.
- Pose/emote targeting in game code is unchanged (separate `emote.py` parser).

### Tests

- [`evennia/utils/tests/test_multimatch.py`](evennia/utils/tests/test_multimatch.py)
- Updates to [`evennia/utils/tests/test_utils.py`](evennia/utils/tests/test_utils.py),
  [`evennia/objects/tests.py`](evennia/objects/tests.py),
  [`evennia/commands/tests.py`](evennia/commands/tests.py).

---

## 6.0.0+underspire.19 — Deprecation breadcrumb cleanup (F2, F12)

Engine-side cleanup of pre-1.0 upstream cargo and a doc clarification.

### Engine

- [`evennia/server/deprecations.py`](evennia/server/deprecations.py)
  shrunk from 188 to 88 LOC. The launcher-time `check_errors` hook
  used to raise on ~17 setting names renamed pre-1.0 upstream
  (`CMDSET_DEFAULT`, `CMDSET_OOC`, `BASE_COMM_TYPECLASS`,
  `COMM_TYPECLASS_PATHS`, `CHARACTER_DEFAULT_HOME`,
  `OBJECT/SCRIPT/ACCOUNT/CHANNEL_TYPECLASS_PATHS`,
  `SEARCH_MULTIMATCH_SEPARATOR`, `INLINEFUNC_ENABLED`,
  `INLINEFUNC_STACK_MAXSIZE`, `INLINEFUNC_MODULES`,
  `PROTFUNC_MODULES`, `TIME_*_PER_*` (six total),
  `GAME_DIRECTORY_LISTING`, `AMP_ENABLED`, `CYCLE_LOGFILES`,
  `CHANNEL_COMMAND_CLASS`, `CHANNEL_HANDLER_CLASS`). Underspire
  never set any of these (grep clean across `evennia/` and
  `newmoo/`); the entire block was breadcrumbs for a migration
  that already happened.

  Surviving checks still defend against real foot-guns on the
  current schema and stay: `WEBSERVER_PORTS` tuple-shape,
  `CHANNEL_CONNECTINFO` type-shape, `template_overrides` and
  `static_overrides` directory-rename guards, and the
  `MULTISESSION_MODE` vs `MAX_NR_SIMULTANEOUS_PUPPETS` coherence
  check. `check_warnings` (dev-mode prod-readiness signals:
  `DEBUG`, `IN_GAME_ERRORS`, `ALLOWED_HOSTS=*`,
  `SERVER_HOSTNAME=localhost`, psycopg2 deprecation) is unchanged.
- [`evennia/prototypes/prototypes.py`](evennia/prototypes/prototypes.py)
  docstring fix: stale reference to `settings.PROTFUNC_MODULES`
  renamed to `settings.FUNCPARSER_PROTOTYPE_VALUE_MODULES` (the
  pre-1.0 rename whose deprecation breadcrumb was just removed).

### Tests

[`evennia/server/tests/test_misc.py`](evennia/server/tests/test_misc.py)
`TestDeprecations` rewritten. The old test enumerated the 17
pre-1.0 names that no longer exist as gates, and only worked
because each pre-1.0 check raised before the next one would have
AttributeErrored on the single-field MockSettings. Replaced with
four targeted cases against the surviving gates: WEBSERVER_PORTS
tuple-shape (fail + pass), CHANNEL_CONNECTINFO type, and
MULTISESSION coherence. MockSettings is now a defaults-class.

Two pre-existing server-test failures that surfaced along the F2
test path (reproduce on the prior underspire HEAD) were fixed in
the same release rather than carried forward as "known fail":

- [`evennia/server/portal/tests.py`](evennia/server/portal/tests.py)`::TestAMPServer::test_amp_in`
  asserted a hand-baked pickle byte literal for the
  `MsgPortal2Server` wire, but that command path uses the JSON
  session-serde envelope (`dumps_session` /
  `pack_session_message`), not pickle. Structurally wrong, not
  just version-fragile. Rewrote to assert the transport saw the
  right AMP frame and that `dumps_session` / `loads_session`
  round-trip the payload.
- [`evennia/server/tests/test_at_init_scheduler.py`](evennia/server/tests/test_at_init_scheduler.py)
  forced `AT_INIT_BATCH_SIZE=2` against 3 entities; the third
  entity's `at_init` was scheduled through `reactor.callLater`,
  which never fires under Django `TestCase` (same class as the
  `test_worker_pool` failure fixed in `+underspire.17`). Patched
  `reactor.callLater` with a synchronous stand-in.

### Docs

[`core-beliefs.md`](.agents/docs/core-beliefs.md): the lane-2 test
("a game that didn't want it can opt out cleanly via a setting")
was strict about settings as the opt-out shape. Practice already
accepts subclass-override as a clean opt-out for structural or
naming conventions that travel with a class (the Phase 3
`@`-prefix command convention being the canonical case). Wording
adjusted to two sentences acknowledging both shapes without
re-litigating the prefix design (F12).

### Migration

Downstream should not need changes. Underspire was not setting any
of the removed pre-1.0 names, and the launcher will no longer raise
on them if a downstream consumer happens to set one — it will
silently accept and ignore, which is the same as having never been
there. If anyone needs the old breadcrumb messages, they're
recoverable from git history.

### Hygiene backlog

F2 and F12 → Shipped. F11 (Phase 2 caller-derived-state sweep)
removed from Open as won't-fix: the backlog entry assumed
`_normalize_account_command_caller` applied to all Command subclasses
but the `+underspire.3` changelog explicitly states it's a no-op
outside AccountCommand subclasses, so the cited `caller.account`
reads in default object commands are correct as-is.

---

## 6.0.0+underspire.18 — Typed-exception sweep (F4)

Four bare `except:` clauses in the engine narrowed to typed exception
clauses. Hygiene-backlog finding F4. The wider 83-site
`except *: pass` audit remains deferred.

### Engine changes

- [`evennia/web/website/views/help.py`](evennia/web/website/views/help.py)
  (`HelpDetailView.get_context_data`): two bare-except blocks wrapping
  prev/next-topic lookup narrowed to `(AssertionError, IndexError)`.
  Drops a latent `NameError` swallow that would have masked an empty
  category set as a real bug.
- [`evennia/utils/utils.py`](evennia/utils/utils.py) (`str2int`): two
  consecutive bare-except blocks around `int(number)` and
  `int(number[:-2])` narrowed to `ValueError`. The backlog entry
  listed only line 2991, but there are actually two sites; both fixed.

### Migration

Downstream code should not need changes. The narrowing is invisible
under normal load. If something previously hidden behind the bare
except was raising an unexpected exception type, that exception will
now propagate — but in those cases the prior silent-swallow was the
bug, not a feature.

### Tests

`evennia.utils evennia.web evennia.help` — 714 pass, 2 pre-existing
skips. No regressions.

### Hygiene backlog

F4 moves from Open → Shipped. No new findings.

---

## 6.0.0+underspire.17 — Stale TODO sweep (F5)

Five past-due TODO markers in engine code, all scheduled for upstream
1.0 or 5.0 removal. Fork is on 6.0. Hygiene-backlog finding F5.

### Engine deletions

- **`Channel.*` deprecation stubs** ([`comms/comms.py`](evennia/comms/comms.py)):
  eight methods on `DefaultChannel` that raised `RuntimeError` pointing
  callers at their 1.0+ replacements — `message_transform`,
  `distribute_message`, `format_senders`, `pose_transform`,
  `format_external`, `format_message`, `pre_send_message`,
  `post_send_message`. All removed, along with the dead docstring
  references in the class docstring and the matching paste in
  [`game_template/typeclasses/channels.py`](evennia/game_template/typeclasses/channels.py).
  No engine callers; pure tombstones.

- **`at_pre_drop` missing-lock escape hatch**
  ([`objects/objects.py`](evennia/objects/objects.py)): the three-line
  guard `if not self.locks.get("drop"): return True` is removed. Every
  object that goes through `basetype_setup` gets `drop:holds()` by
  default (objects.py:2057). Downstream edge case: any object created
  without `basetype_setup` and without an explicit drop lock now fails
  `at_pre_drop` (consistent with the lock system's fail-closed belief).

- **`caller.ndb._menutree` deprecation alias**
  ([`utils/evmenu.py`](evennia/utils/evmenu.py)): the alias that
  shadowed `caller.ndb._evmenu` for backwards compat is removed at both
  the `__init__` write site and the `close_menu` cleanup. Internal
  `self._menutree` (the parsed menudata dict) and the persistent
  `_menutree_saved` attribute key are unrelated and stay.

  In-tree consumer migrations bundled in the same release:
  [`prototypes/menus.py`](evennia/prototypes/menus.py) (~25 sites; the
  default prototype OLC menu, lane-2 engine code),
  [`prototypes/tests.py`](evennia/prototypes/tests.py),
  [`commands/default/tests.py`](evennia/commands/default/tests.py),
  [`contrib/full_systems/evscaperoom/menu.py`](evennia/contrib/full_systems/evscaperoom/menu.py),
  [`contrib/utils/fieldfill/fieldfill.py`](evennia/contrib/utils/fieldfill/fieldfill.py)
  (only the `ndb` branch — a separate broken `caller.db._menutree`
  branch is left untouched as a pre-existing bug),
  [`contrib/utils/tree_select/tree_select.py`](evennia/contrib/utils/tree_select/tree_select.py),
  [`contrib/rpg/character_creator/tests.py`](evennia/contrib/rpg/character_creator/tests.py),
  [`utils/tests/test_evmenu.py`](evennia/utils/tests/test_evmenu.py),
  [`utils/tests/data/evmenu_example.py`](evennia/utils/tests/data/evmenu_example.py).

### Comment-only fix (no behavior change)

- **`building.py` exec gate comment** ([`commands/default/building.py`](evennia/commands/default/building.py)):
  the TODO at building.py:4275 claimed "Exec support is deprecated.
  Remove completely for 1.0" — but exec support is still live in
  [`prototypes/spawner.py`](evennia/prototypes/spawner.py) (`exec(code, ...)`
  on prototype `exec` strings at spawn time). The if-check the comment
  guards is the actual privilege barrier blocking non-Developer staff
  from arbitrary code execution. Comment rewritten to describe what the
  check does. Removing exec support entirely is tracked as a new
  backlog item (out of scope for F5).

### Migration (required)

Downstream code that still reads `caller.ndb._menutree` must rename to
`caller.ndb._evmenu`. Newmoo grepped clean before merge. The alias has
been deprecated since pre-1.0 upstream; this just lands the removal
that was already scheduled.

The eight `Channel.*` stub methods were never callable (they raised on
invocation). If downstream code overrode any of them, the override
won't be inherited from `DefaultChannel` any more, but since the base
implementations raised, any inheriting override was already shadowing
a dead method.

### Tests

All 91 tests in
`evennia.utils.tests.test_evmenu evennia.comms evennia.objects evennia.prototypes`
pass after the F5 changes.

Six failures that surfaced during the F5 sweep but were pre-existing
on the prior underspire HEAD were also fixed in this release (commits
land after the `release:` commit, in the same branch):

- **`test_display_name_cache` (3 tests)**: test bug. The mock was on
  `self.char1.get_display_name` (the looker) while
  `cached_get_display_name(self.char2, self.char1)` calls the
  *object's* method (`self.char2`), so the mock never registered.
  Patch target swapped to `self.char2`; args left intact so
  `invalidate_display_name_cache` and `bump_recog_generation` still
  hit the looker (`self.char1`).

- **`test_search` (2 tests)**: real engine bug surfaced by the recent
  typed-column optimization in
  [`typeclasses/attributes.py`](evennia/typeclasses/attributes.py).
  Primitive Attribute values now live in `db_int_val` /
  `db_float_val` / `db_str_val` with `db_value` NULL; the search
  managers still filtered on `db_attributes__db_value=value` and
  silently returned no matches for any primitive search. Added
  `value_query_filter(value, prefix)` in `attributes.py` mirroring
  the writer's dispatch (str → `db_str_val`, bool → `db_int_val` +
  `db_val_type="bool"` to disambiguate from int 0/1, etc.) and wired
  both `get_attribute` and `get_by_attribute` in
  [`typeclasses/managers.py`](evennia/typeclasses/managers.py) through
  it. Affects every `search_*_attribute` helper.

- **`test_worker_pool` (1 test)**: test/framework mismatch. The test
  drove the real `twisted.internet.threads.deferToThread` and waited
  on a `threading.Event`, but `deferToThread` schedules its callback
  via `reactor.callFromThread`; Django's `TestCase` doesn't run a
  reactor, so the callback queued and the event never set — a 5s
  hang on every run. Rewritten as four targeted unit tests that
  patch `deferToThread` and assert the wrapper's contract (returns
  Deferred, wraps args, chains callback/errback, raises on disabled
  pool, fires elapsed-time warn past threshold).

Migration impact: code that called any `search_*_attribute` with a
primitive `value=` argument was silently returning no results. After
this release it returns matches.

### Hygiene backlog

F5 moves from Open → Shipped. No new findings opened here; the
backlog gained F18/F19 in a parallel doc commit on this same branch.

---

## 6.0.0+underspire.16 — Contrib extraction: move six contribs into downstream

Six `evennia.contrib.*` packages moved to downstream Underspire as
ordinary game code. Each was either used as-is via a single
`TypeProperty` (cooldowns, traits), used by one consumer at
module-level (name_generator), or so deeply extended by the game that
the engine was hosting a system the consumer had already built on top
of (components, buffs). The `rpsystem` package goes the same way: only
`rplanguage` was imported; the rest of the system has long been ported
into game code (`world/rp_features.py`, `RecogHandler`, helmet/mask
display rules) so the contrib delete is total.

Core-belief rationale: "toolkit not game." Underspire is the sole
consumer of this fork; carrying these in the engine added no toolkit
value past what the game now hosts directly. Mirrors the
`+underspire.10` methodology (extended_room, puzzles) at larger scale.

### Engine deletions

| Contrib | LOC | Downstream home |
|---|---|---|
| `evennia.contrib.game_systems.cooldowns` | 419 | `world.cooldowns` |
| `evennia.contrib.utils.name_generator` | 32 033 (mostly data files) | `world.name_generator` |
| `evennia.contrib.rpg.traits` | 3 303 | `world.traits` |
| `evennia.contrib.base_systems.components` | 1 655 | `world.components` |
| `evennia.contrib.rpg.buffs` | 2 222 | `world.buffs_base` |
| `evennia.contrib.rpg.rpsystem` | 3 036 | only `rplanguage` → `world.rpg.rplanguage` |

Each contrib's tests travel with the code. Engine `evennia.contrib`
test count drops accordingly. No engine non-test code referenced any
of these modules; verified by grep before each commit.

### Migration (required)

Downstream import sweep:

| Old import | New import |
|---|---|
| `from evennia.contrib.game_systems.cooldowns import CooldownHandler` | `from world.cooldowns import CooldownHandler` |
| `from evennia.contrib.utils.name_generator import namegen` | `from world.name_generator import namegen` |
| `from evennia.contrib.rpg.traits import TraitHandler` | `from world.traits import TraitHandler` |
| `from evennia.contrib.base_systems.components import Component, ComponentHolderMixin, ComponentProperty, DBField` | `from world.components import ...` |
| `from evennia.contrib.rpg.buffs.buff import BaseBuff, BuffHandler, Mod` | `from world.buffs_base import ...` |
| `from evennia.contrib.rpg.rpsystem.rplanguage import add_language, obfuscate_language` | `from world.rpg.rplanguage import add_language, obfuscate_language` |

Newmoo partner branch: `engine-16-adopt-contribs`. Engine release ships
only after the partner PR is ready against the engine pin.

`evennia.contrib.grid.xyzgrid` and `evennia.contrib.grid.wilderness`
remain in the engine per prior decision; both are generic spatial
infrastructure and still match the toolkit shape.

### Hygiene backlog

This release also lands [`.agents/docs/hygiene-backlog.md`](.agents/docs/hygiene-backlog.md):
catalogues findings from the hygiene pass that weren't actioned in
this release (upstream-deprecations cargo, doc rot in
`docs/source/`, bare excepts, past-due TODO markers, settings-prefix
split, CMDSET_REFACTOR §8 verifications, cache-coherence audit, big-
module audit, and six deferred audits). Future hygiene passes start
from this file. Indexed in [`AGENTS.md`](AGENTS.md).

### Tests

- `evennia.commands`: 284/284 pass.
- `evennia.contrib`: 466 → fewer tests as the contrib surface
  shrinks; all remaining contrib tests pass.

---

## 6.0.0+underspire.15 — Cmdset refactor followups: switch case + docs

Three small followups from the downstream alignment pass. One latent
bug fix shipped as a behavior default flip, one doc-of-record update,
one downstream wrapper recorded as redundant for the next game-side
cleanup.

### Latent fix: `Command.parse` lowercases switches by default

`Command.parse` previously stored user-supplied switches on
`self.switches` with case preserved. Two problems:

1. **Latent validation bug.** `switch_options` is class-lowercased at
   parse time
   ([`command.py:565`](evennia/commands/command.py)), but user input
   was compared raw at line 579, so a command declaring
   `switch_options=("del",)` would silently reject `/Del` as an
   "unused" switch and warn the user — even though that's obviously
   what the user meant. Nobody noticed because the warning text just
   said "Extra switch /Del ignored" and the user assumed the command
   was case-sensitive.
2. **Per-consumer rework.** Real-world consumers compare against
   lowercase literals (`if "del" in self.switches`); case preservation
   meant every consumer had to re-implement the same `[s.lower() for
   s in self.switches]` step.

New class flag `parse_lowercase_switches = True` (default `True`).
When `True`, `parse` lowercases user input before storing on
`self.switches` *and* before `switch_options` validation, so both the
bug above and the per-consumer lowercasing go away.

Set `parse_lowercase_switches = False` on subclasses that genuinely
need case-sensitive switch handling.

**Migration:** tiny breaking change for any consumer that compared
`self.switches` against uppercase literals. The flag exists for them
to flip. Downstream Underspire kept its own one-line lowercase wrapper
during the deprecation window; it can drop the wrapper now that the
engine handles it.

Three new tests in `TestAccountCommandNormalization` cover the
default, the `switch_options` regression, and the opt-out path. 284/284
commands tests pass.

### Docs: `CMDSET_REFACTOR.md` Phase 5 callout

Adds an explicit "lesson learned" callout to the Phase 5 section of
[`CMDSET_REFACTOR.md`](CMDSET_REFACTOR.md): most of the enumerated
Phase 5 cleanup items got removed incrementally during downstream
Phase 0-4 alignment commits, not deferred to a final pass. The actual
Phase 5 PR ended up tiny. Future fork migrations should expect the
same shape — clean up as the API lands, not as a big final-cleanup
commit. The Phase 5 list now reads as a backstop catalog rather than
a planned final commit.

### Recorded: redundant downstream patch

Underspire's `commands/staff_admin_wrappers.py:97-103` patches
`CmdNick.func` to recognize `@nicks` as triggering list mode. The
engine already does this since `b78cf2e88` (Phase 3 step 4,
[+underspire.8](#600underspire8--phase-3-token-boundary-matching--drop-cmd_ignore_prefixes))
— the wrapper predates the engine fix and was never compared back.
No engine commit needed. Downstream cleanup: delete the wrapper, verify
`@nicks` still triggers list mode in stock behavior. Recorded here so
the next downstream cleanup pass knows to look for it.

---

## 6.0.0+underspire.14 — Phase 4 polish: integration tests + ergonomics

Small post-Phase-4 release driven by downstream alignment feedback from
the Underspire migration. Three changes, all additive.

### Pose passthrough integration tests (`evennia/commands/tests.py`)

New `TestPosePassthroughIntegration` exercises the full cmdhandler
dispatch path against a fixture `CmdNoMatch`. Asserts that:

- `.pose smiles`, `;nods`, and `,grins` (leading punctuation not
  aliased by any engine default) reach `CmdNoMatch.func()` with
  `self.raw_string` equal to the original input — no trie shortcut
  intercepts them, no abbrev rewrite mangles the string.
- When a custom `CMD_NOMATCH` is registered, the cmdhandler's built-in
  fuzzy fallback (`"Maybe you meant ...?"`) is short-circuited and
  `fuzzy_command_suggestions` is never invoked. This is the guarantee
  that lets downstream pose/emote handlers run without the engine
  preempting them with a typo suggestion.

`:` is intentionally not covered: it's an explicit alias of the engine's
default `CmdPose` (with `arg_regex = None`), so `:waves` legitimately
matches CmdPose rather than falling through. The dot/semicolon/comma
cases are the ones the trie+fuzzy work needed to keep clean.

Downstreams inherit this safety net rather than each writing their own
smoke test.

### `try_num_differentiators` re-exported from `cmdparser_trie`

Parser wrappers that subclass or compose on `cmdparser_trie` previously
had to reach across to `evennia.commands.cmdparser` for
`try_num_differentiators` (the `2-ball` multimatch separator parser).
That's now re-exported so:

```python
from evennia.commands.cmdparser_trie import (
    cmdparser,
    trie_build_matches,
    create_match,
    try_num_differentiators,
    fuzzy_command_suggestions,
)
```

works without crossing modules. `__all__` is declared on
`cmdparser_trie` to make the public surface explicit. The original
`evennia.commands.cmdparser.try_num_differentiators` import path
continues to work unchanged.

### Migration doc clarifications (`CMDSET_MIGRATION.md` § Phase 4)

Two clarifications added in response to downstream feedback:

- **"Wrapper still earns its keep" path.** The optional-cleanup section
  used to read as "delete the `COMMAND_PARSER` override." Now it
  explicitly calls out the case where the override does work other
  than parser selection (per-caller access caching, alias gating,
  telemetry, outer LRU) and instructs you to rebase that wrapper on
  `trie_build_matches` instead of deleting it.
- **Worked example of fuzzy suggestions inside a custom `CmdNoMatch`.**
  The engine's fuzzy fallback is *skipped* when a custom CMD_NOMATCH
  is registered (the common case for any non-trivial game), so a
  literal read of "fuzzy is the default" misleads downstreams. The
  migration doc now ships a copy-pasteable `CmdNoMatch.func()` body
  showing the `fuzzy_command_suggestions(raw, self.cmdset)` call,
  including the rule that the call must come *after* any
  leading-punctuation pose/emote interception so the fuzzy hint
  doesn't preempt intended emote input.

### Migration

None required. All changes are additive (new test class, additive
re-exports, doc clarifications).

---

## 6.0.0+underspire.13 — Phase 4: trie parser default + fuzzy suggestions + Phase 3 cleanup bundle

Promotes the trie-backed command parser into the engine as the default
`COMMAND_PARSER`, bundles Levenshtein-based fuzzy suggestions on the
no-match fallback, and finishes the Phase 3 leftovers (`include_prefixes`
kwarg removal, `CMD_IGNORE_PREFIXES` setting deletion).

### Trie parser (`evennia/commands/cmdparser_trie.py`, new module)

- Ported from Underspire's `world/parsing/trie_parser.py`. Public
  callable `cmdparser(raw_string, cmdset, caller, match_index=None,
  session=None, **kwargs)` matches the existing `COMMAND_PARSER`
  contract; `trie_build_matches(raw_string, cmdset)` mirrors
  `cmdparser.build_matches`'s output.
- Collects candidate commands via a token-prefix trie keyed on each
  command's key/aliases (multi-word keys like `go shard` live at the
  appropriate token depth and are also surfaced from the first token).
- Unambiguous first-token abbreviation expansion: when the typed first
  token is not a trie edge but uniquely prefixes one root key, the input
  is rewritten to the shortest matching canonical key so `create_match`
  slices `args` correctly. Gated on `COMMAND_PARSER_TRIE_ABBREV` (default
  `True`).
- Exact-match fast path skips `cmd.match()` for the sole candidate when
  the class did not override `match` and is not an exit. Honors
  `arg_regex`. Gated on `COMMAND_PARSER_TRIE_FASTPATH` (default `True`).
- Two-tier cache on the merged cmdset (`_trie_command_trie` +
  `_trie_command_trie_cheap` + `_trie_command_trie_sig`): the cheap key
  (length + id-sum of commands) short-circuits the O(n) structure
  signature on every parse. Trie rebuilds only when the cheap key
  drifts (cmdset membership change) or the full sig drifts (in-place
  key/alias mutation on an existing cmd). Production code rebuilds the
  containing cmdset on any cmd change, so the cheap key catches the
  common case.
- Falls back to the linear `cmdparser.build_matches` when the trie
  produces zero candidates, so commands whose `match()` overrides use
  non-prefix logic still get evaluated.

### Default flip + opt-out (`evennia/settings_default.py`)

- `COMMAND_PARSER` default flipped to
  `evennia.commands.cmdparser_trie.cmdparser`. The linear
  `evennia.commands.cmdparser.cmdparser` stays available for opt-out
  (set the setting back if a downstream needs the old behavior).
- New settings: `COMMAND_PARSER_TRIE_FASTPATH` (default `True`),
  `COMMAND_PARSER_TRIE_ABBREV` (default `True`),
  `COMMAND_FUZZY_SUGGESTIONS_ENABLED` (default `True`),
  `COMMAND_FUZZY_SUGGESTIONS_MAX_DIST` (default `2`),
  `COMMAND_FUZZY_SUGGESTIONS_LIMIT` (default `3`).

### Performance baseline

Synthetic 500-cmd cmdset, 2000 parses across 8 unique inputs:
- linear: ~72 µs/parse
- trie:   ~45 µs/parse  (**1.59× faster**)

Synthetic 20-cmd cmdset:
- linear: ~3.6 µs/parse
- trie:   ~6.8 µs/parse  (3 µs absolute regression on tiny cmdsets;
  well below user-perceptible thresholds, and gameplay cmdsets are
  typically 30–80 commands)

### Fuzzy command suggestions (`evennia/commands/cmdhandler.py`)

- The no-match fallback in the cmdhandler now offers Levenshtein-based
  suggestions ("Maybe you meant ...?") via
  `cmdparser_trie.fuzzy_command_suggestions`. Replaces the previous
  `difflib`-based `string_suggestions` call.
- Gated on `COMMAND_FUZZY_SUGGESTIONS_ENABLED` (default `True`).
- **Only fires when no custom `CMD_NOMATCH` command is registered.**
  Downstream code that registers a `CmdNoMatch` (e.g. emote/pose
  parsing on leading punctuation) is unaffected: the cmdhandler
  delegates the entire no-match branch to the override and never
  reaches the fallback text.

### Phase 3 cleanup bundle

- `Command.match()` and `cmdparser.build_matches()` lose the no-op
  `include_prefixes` kwarg (both signatures documented as ignored since
  `+underspire.8`; engine-wide audit found no surviving call sites
  passing it).
- `settings.CMD_IGNORE_PREFIXES` is **deleted** from
  `settings_default.py`. The startup warning in
  `evennia/__init__.py:_init()` is removed.
- Two remaining help-system consumers (help-search index in
  `commands/command.py` and help lookup in `commands/default/help.py`)
  inline `_HELP_PREFIX_CHARS = "@&/+"` as a module constant. Purely a
  help-search convenience (`help @open` and `help open` resolve to the
  same entry when only one exists); no longer user-configurable.

### Migration

- Downstreams setting `COMMAND_PARSER` to a custom parser keep working;
  the new default only applies when the setting is unset.
- Downstreams that referenced `settings.CMD_IGNORE_PREFIXES` get an
  `AttributeError` at import. The setting was a no-op since
  `+underspire.8`; remove the reference. If a custom help-search needs
  the prefix-strip behavior, inline the constant locally.
- `Command.match` and `cmdparser.build_matches` accept no `include_prefixes`
  kwarg. Downstream subclasses overriding `match(self, cmdname,
  include_prefixes=True)` should drop the kwarg.

See [`CMDSET_MIGRATION.md`](CMDSET_MIGRATION.md) Phase 4 for the full
downstream migration walkthrough.

---

## 6.0.0+underspire.12 — `redis_attr_cache` value serialisation fix

Single-commit release (`ec39b3028`). `PickledObjectField` always holds a
decoded Python object in memory, not raw bytes. Calling
`base64.b64encode()` on a dict/list raised `TypeError`. The cache now
serialises `db_value` via `dbsafe_encode` / `dbsafe_decode` so complex
attribute values round-trip through the Redis L2 cache correctly.
Cache schema bumped `v1` → `v2` to invalidate stale entries from before
the fix.

---

## 6.0.0+underspire.11 — Discord portal: interaction routing, remove_role, slash-command registration

Three additions to the Discord portal layer enabling full Discord interactions
(slash commands + typing indicators) and non-blocking role removal.

### Portal (`evennia/server/portal/discord.py`)

- **Fix: `INTERACTION_CREATE` type key no longer clobbered.**
  The `else` branch in `DiscordClient.data_in` previously called
  `keywords.update(data["d"])`, which silently overwrote
  `keywords["type"] = "INTERACTION_CREATE"` with the integer `2` from
  the Discord payload's own `type` field.  The inner dict is now copied,
  its `type` popped and stored as `keywords["interaction_type"]`, and
  only then merged into `keywords`.  The routing string in
  `keywords["type"]` is preserved for `DiscordBot.execute_cmd` dispatch.
  Same protection applies to any future Gateway event whose `d` payload
  contains a `type` integer (e.g. `TYPING_START` channel type).

- **New outputfunc `send_remove_role(role_id, guild_id, user_id)`.**
  Issues a non-blocking REST `DELETE /guilds/{guild_id}/members/{user_id}/roles/{role_id}`.
  Use via `session.msg(remove_role=(role_id, guild_id, user_id))`.

- **New outputfunc `send_interaction_reply(content, interaction_id, token)`.**
  Posts an interaction callback (type 4 = `CHANNEL_MESSAGE_WITH_SOURCE`)
  to `POST /interactions/{interaction_id}/{token}/callback`.
  Use via `session.msg(interaction_reply=(content, interaction_id, token))`.

- **New outputfunc `send_register_commands(commands, app_id, guild_id)`.**
  Bulk-overwrites guild slash commands via
  `PUT /applications/{app_id}/guilds/{guild_id}/commands`.
  Use via `session.msg(register_commands=(commands_list, app_id, guild_id))`.

### Bot base class (`evennia/accounts/bots.py`)

- **`DiscordBot.remove_role(role_id, guild_id, user_id)`** — thin wrapper
  calling `super().msg(remove_role=(...))`.
- **`DiscordBot.interaction_reply(content, interaction_id, token)`** —
  thin wrapper calling `super().msg(interaction_reply=(...))`.
- **`DiscordBot.register_guild_commands(commands, app_id, guild_id)`** —
  thin wrapper calling `super().msg(register_commands=(...))`.

### Migration

No breaking changes.  Downstream `DiscordBot` subclasses that previously
used `reactor.callInThread(requests.delete, ...)` for role removal can
switch to `self.remove_role(...)` (or `super().msg(remove_role=...)`
directly).  The old thread-based path will continue to work; this is an
additive improvement.

---

## 6.0.0+underspire.10 — Phase 2 cleanup + unused contrib removal

Two pre-existing Phase 2 bugs surfaced during the Phase 3 sweep and
the +underspire.9 follow-up; fixed in a single commit since both are
small and tightly scoped to the same migration. Bundled with deletion
of two unused contribs to cut test-sweep noise from code Underspire
doesn't run.

### Engine

- **`CmdPerm.func` case-insensitive duplicate check.**
  `obj.permissions.all()` returns lowercased strings, but the
  "already defined" check compared against the input verbatim. Typing
  `@perm Obj = Builder` against a target that already had `builder`
  fell through to re-add and reported `"given"` instead of
  `"already defined"`. After +underspire.9 this also caused a
  redundant `cmd_access_cache` flush and a spurious
  `permissions_changed` signal fire. Fix: build a lowercased set
  once and compare case-insensitively.

- **`test_resources.call()` hardcoded `providers["account"]`.** Tests
  that passed `caller=self.account2` (or a different character) had
  their account silently rewritten to `self.account` inside the
  `account_command_caller` normalisation branch, masking real
  behavior. Same for `cmdobj.account`. Pre-existing since
  +underspire.6 when `_normalize_account_command_caller` was added.
  Fix: derive `cmd_account` from caller (caller-if-Account →
  caller.account → self.account fallback) and use it for both
  `cmdobj.account` and `providers["account"]`.

### Test fallout

`evennia.contrib.game_systems.mail.tests.TestMail.test_mail` now
passes — it was failing on the second assertion (`caller=self.account2`
sending to `TestAccount2`) because the account hardcoding produced
`"from TestAccount"` instead of `"from TestAccount2"`.

`TestPermissionsChangedSignal.test_cmd_perm_no_op_does_not_fire`
restored (was dropped in +underspire.9 because the case-insensitivity
bug made it untestable).

All 263 evennia.commands + evennia.commands.default.tests +
evennia.contrib.game_systems.mail.tests pass.

### Contrib removals

`evennia/contrib/grid/extended_room/` and
`evennia/contrib/game_systems/puzzles/` deleted outright. Neither has
any engine consumer; both are opt-in features Underspire doesn't use,
and both had broken tests in the +underspire.9 sweep that were pure
noise for this fork. The typeclass-path remap entries in
`evennia/accounts/migrations/0011_*`,
`evennia/objects/migrations/0012_*`, and
`evennia/scripts/migrations/0015_*` are kept as-is — they reference
the modules by string for DB-side path normalisation and don't import
them, so anyone migrating an old DB still gets the remap.

### Migration

No downstream action required for the bug fixes. Behaviour changes:

- `@perm Obj = Builder` against an obj that already has the
  permission now correctly reports "already defined" and does not
  fire `permissions_changed` (downstream subscribers see fewer false
  signals).
- Tests that called `self.call(..., caller=self.account2, ...)` or
  similar will now see the correct account on `cmdobj.account` and
  in the normalisation providers. If a test was inadvertently relying
  on the old "everything is self.account" behavior, it will need to
  update its expected output.
- Importing `evennia.contrib.grid.extended_room` or
  `evennia.contrib.game_systems.puzzles` raises ImportError. Anyone
  relying on these contribs in this fork needs to vendor them in
  their game tree.

---

## 6.0.0+underspire.9 — Engine-owned permission cache invalidation

Phase 2 follow-up. `CmdPerm` and `CmdQuell` now invalidate the
`cmd_access_cache` and `lock_cache` for the affected entities
themselves, and fire a new `permissions_changed` signal so downstream
consumers can subscribe instead of monkey-patching the command
classes.

### Engine

- New signal `evennia.commands.signals.permissions_changed`. Kwargs:
  `target` (the mutated entity), `added` (tuple of perm strings),
  `removed` (tuple of perm strings), `actor` (the caller), and
  `account_mode` (bool). For `@quell` / `@unquell` both `added` and
  `removed` are empty since the raw permission list isn't changing
  — only the effective permission stack flips. Sender is the
  concrete Command class (`CmdPerm`, `CmdQuell`).
- `CmdPerm.func` (`evennia/commands/default/admin.py`): tracks the
  actually-added and actually-removed perm strings inside the
  existing loops, then after the mutations calls
  `invalidate_cmd_access_cache(obj)` and `invalidate_lock_cache(obj)`
  for the target, and fires `permissions_changed` exactly once with
  the cumulative sets. Both invalidation and signal are skipped if
  the dispatch was a no-op (e.g. trying to add an already-present
  permission).
- `CmdQuell.func` (`evennia/commands/default/account.py`): on
  successful quell or unquell, invalidates `cmd_access_cache` and
  `lock_cache` for both the account and the active puppet (if any),
  then fires `permissions_changed` once with `target=account`,
  `added=()`, `removed=()`, `account_mode=True`. Already-quelled
  `@quell` and already-unquelled `@unquell` no-ops do not fire.
- Drive-by fix in `CmdQuell.func`: the `self.cmdstring in
  ("@unquell", "@unquell")` test had a duplicated literal from the
  +underspire.8 re-key sweep; collapsed to an equality comparison.

### Invalidate-then-fire ordering

The engine flushes its own caches *before* firing the signal so that
downstream subscribers observe consistent engine state. A
signal-driven invalidation pattern (engine listener subscribed first)
was considered and rejected: django.dispatch ordering is import-
order-dependent, which is too brittle for a correctness-critical
ordering invariant.

### Tests

`evennia/commands/tests.py:TestPermissionsChangedSignal` covers:

- `@perm <obj> = <perm>`: cache cleared on the target, signal fired
  with `added=(perm,)` / `removed=()`.
- `@perm/del <obj> = <perm>`: cache cleared, signal fired with
  `added=()` / `removed=(perm,)`.
- `@perm` against an already-set permission: no-op, no cache flush,
  no signal.
- `@quell` from unquelled state: cache cleared on account and
  active puppet, signal fired with empty sets and
  `account_mode=True`.
- `@unquell` from quelled state: same shape.

### Migration

Downstream code that monkey-patched `CmdPerm.func` / `CmdQuell.func`
to invalidate caches (or to run audit hooks) can subscribe to
`permissions_changed` instead. Example:

```python
from django.dispatch import receiver
from evennia.commands.signals import permissions_changed

@receiver(permissions_changed)
def audit_perm_change(sender, target, added, removed, actor,
                      account_mode, **kwargs):
    audit_event(actor, target, added, removed, account_mode)
```

The engine's own invalidation runs before any subscriber observes
the signal, so subscribers can derive their own state from the
target without worrying about cache coherence.

---

## 6.0.0+underspire.8 — Phase 3: token-boundary matching + drop CMD_IGNORE_PREFIXES

The big semantic break. After this release, `@open` and `open` are
distinct command keys. `CMD_IGNORE_PREFIXES` no longer strips prefix
characters at parse time, so prefix characters are load-bearing parts
of the key.

Couples with a re-key sweep across the engine default cmdsets to align
with the IC/OOC convention (no prefix for character actions, `@` for
account/OOC actions). See [`.agents/docs/code-style.md`](.agents/docs/code-style.md)
(Command Naming) for the rule and [`PHASE3_AUDIT.md`](PHASE3_AUDIT.md)
for the full inventory.

**Breaking:** any command keyed without `@` that downstream callers
were reaching via `@`-prefix (or vice versa) will stop matching. The
re-key list below covers every engine-default that moved.

### Engine matching changes

- `evennia/commands/command.py`:
  - `Command._optimize()` no longer builds `_noprefix_aliases`. The
    mapping from stripped-key → original-key is gone; no external
    callers (engine, contrib, tests) read it.
  - `Command.match()` is now single-pass, token-boundary only. The
    `include_prefixes` kwarg is retained on the signature for
    backward compatibility with any custom subclass that still passes
    it, but it is ignored. Token boundary is enforced via the
    existing `arg_regex` default (`r"^[ /]|\n|$"`).
- `evennia/commands/cmdparser.py`:
  - `build_matches()` no longer calls `raw_string.lstrip(
    _CMD_IGNORE_PREFIXES)`. The `include_prefixes` kwarg is retained
    on the signature but ignored.
  - `cmdparser()` no longer makes a second
    `build_matches(..., include_prefixes=False)` fallback pass on
    no-match. Matching is single-shot.
- `evennia/__init__.py:_init()` emits a `logger.log_warn` once at
  server startup if `settings.CMD_IGNORE_PREFIXES` is non-empty,
  noting that the setting is a no-op since +underspire.8 and will be
  deleted in a future release.

### Engine re-key sweep

Every command listed below received a new `@`-prefixed key (and its
aliases were re-prefixed to match). Help-text Usage lines and inline
`Usage:` error messages were updated. Cross-references inside
docstrings (e.g. "Use unquell" → "Use @unquell") were updated.

- **admin.py**: `ban` → `@ban` (`bans` → `@bans`), `unban` → `@unban`,
  `boot` → `@boot`, `emit` → `@emit` (`pemit`, `remit` → `@pemit`,
  `@remit`), `force` → `@force`, `perm` → `@perm` (`setperm` →
  `@setperm`), `wall` → `@wall`, `userpassword` → `@userpassword`.
- **batchprocess.py**: `batchcommands` → `@batchcommands`
  (`batchcommand`, `batchcmd` → `@batchcommand`, `@batchcmd`),
  `batchcode` → `@batchcode` (`batchcodes` → `@batchcodes`).
- **help.py**: `sethelp` → `@sethelp`. `help` (the player-facing
  meta command) intentionally stays unprefixed.
- **building.py**: `unlink` → `@unlink` (lone straggler; every other
  builder command was already `@`-prefixed in the engine).
- **account.py**: OOC `look` (CmdOOCLook) → `@look` (aliases `l`,
  `ls` → `@l`, `@ls`); IC `look` in `general.py` is unchanged.
  `charcreate` → `@charcreate`, `chardelete` → `@chardelete`,
  `ic` → `@ic` (`puppet` → `@puppet`), `ooc` → `@ooc`
  (`unpuppet` → `@unpuppet`), `sessions` → `@sessions`,
  `who` → `@who` (`doing` → `@doing`), `option` → `@option`
  (`options` → `@options`), `password` → `@password`,
  `quit` → `@quit`, `color` → `@color`, `style` → `@style`,
  `quell` → `@quell` (`unquell` → `@unquell`).
- **comms.py**: `page` → `@page` (`tell` → `@tell`),
  `irc2chan` → `@irc2chan`, `ircstatus` → `@ircstatus`,
  `rss2chan` → `@rss2chan`, `grapevine2chan` → `@grapevine2chan`,
  `discord2chan` → `@discord2chan` (`discord` → `@discord`).
- **general.py**: `nick` → `@nick` (`nickname`, `nicks` → `@nickname`,
  `@nicks`), `access` → `@access` (`groups`, `hierarchy` → `@groups`,
  `@hierarchy`).

Engine call sites that issue these commands via `execute_cmd` were
also updated:

- `CmdMvAttr.func` issues `@cpattr` / `@cpattr/move` (was unprefixed).
- `contrib/rpg/character_creator`: dispatches `@charcreate`, `@ic`,
  `@look` (was unprefixed).
- `contrib/tutorials/tutorial_world/rooms`: dispatches `@quell` (was
  unprefixed).
- `contrib/tutorials/batchprocessor/example_batch_cmds*.ev`: the
  example batch files now use `@create`, `@set`, `@teleport`.
- `prototypes/tests`: test dispatches `@spawn/list` (was unprefixed).

`UnloggedinCmdSet` is unchanged — pre-login commands (`connect`,
`create`, `quit`, etc.) sit outside the IC/OOC distinction.

### Tests

`evennia/commands/tests.py:TestCmdParser.test_build_matches`
rewritten for the new semantics:

- Plain key matches verbatim (`test1 rock` → `test1`).
- `@another command ...` no longer matches the unprefixed
  `another command` key.
- A `&`-keyed command requires the `&` in the input.

All other test failures from the re-key sweep were either expected-
output string updates (e.g. `Use unban` → `Use @unban`) or call-site
fixes (the engine sites listed above).

### Rationale

`CMD_IGNORE_PREFIXES` made the `@`-prefix semantically meaningless:
any command keyed `@open` was also reachable as `open`, and any
command keyed `open` could be reached as `@open`. Downstream games
(Underspire/newmoo) ended up shipping `safe_remove(EngineCmd) +
GameCmd()` pairs to forcibly delete the engine command so the strip
couldn't reach it. This phase removes the strip entirely so the
prefix carries meaning, then re-keys the engine defaults to the
IC/OOC convention so the keys ship right out of the box.

### Migration

- Downstream `CmdAt*` wrapper classes that only added the `@`-prefix
  (no behavior changes) can be deleted in favor of the engine
  defaults. The `safe_remove(EngineCmd) + add(WrapperCmd())` pairs
  go with them.
- Custom commands that subclassed `Command` and overrode `match()`
  with custom prefix-strip logic should drop the prefix-strip — it's
  a no-op now.
- Game code that called `caller.execute_cmd("ban ...")` or similar
  must update to the new keys (`@ban`, etc.).
- If you want to preserve the old behavior temporarily for a
  custom command, add the unprefixed name as an alias on your
  Command subclass. The engine no longer does this for you.
- `CMD_IGNORE_PREFIXES` setting is preserved in `settings_default.py`
  for one release with a startup warning if non-empty. Slated for
  removal in a follow-up.

---

## 6.0.0+underspire.7 — ftfy normalisation in cmdhandler

Moves `ftfy.fix_text` into the engine. Every dispatched raw command
string is repaired at the cmdhandler entry, ahead of cmdset merge,
parser, signal payloads, and the final `cmd.raw_string`. Completes
Phase 2 of the cmdset refactor.

### Engine

- `evennia/commands/cmdhandler.py`: import `ftfy.fix_text` at module
  scope and apply it to `raw_string` at the top of `cmdhandler()`,
  before `generate_cmdset_providers` / `_resolve_signal_session` /
  the cmdset merge. Gated on `INPUT_FTFY_NORMALIZE` (default `True`).
  Skipped for non-str `raw_string` (sentinel paths).
- `evennia/settings_default.py`: new `INPUT_FTFY_NORMALIZE = True`
  setting next to `CMD_IGNORE_PREFIXES`.
- `pyproject.toml`: `ftfy == 6.3.1` is now a hard dependency (was
  not previously declared, direct or transitive). `uv.lock` updated.

### Rationale

The game already ftfy'd input at the parser layer. Promoting the
pass into the engine means downstream consumers (signal receivers,
`cmd.raw_string` readers, the cmdset merge cache, the parser) all
observe normalised text from a single point, instead of relying on
every consumer to ftfy independently. `ftfy` becomes a hard dep
rather than optional, so the import is unconditional; if it is
missing the engine refuses to start, which is the correct contract.

### Migration

- Downstream code that ftfy'd input itself can drop that pass. No
  other action required.
- Set `INPUT_FTFY_NORMALIZE = False` in `server.conf.settings` to
  opt out of the per-dispatch normalisation cost.

---

## 6.0.0+underspire.6 — Delete MuxCommand / MuxAccountCommand

Removes `MuxCommand` and `MuxAccountCommand` entirely. Engine-internal
subclasses (default commands, contribs, test patches) all converted
to `Command` / `AccountCommand` in the same release.

**Breaking:** importing `evennia.commands.default.muxcommand` or
`evennia.default_cmds.MuxCommand` / `MuxAccountCommand` raises
`ImportError`. Subclassing these classes is no longer possible. The
module file `evennia/commands/default/muxcommand.py` is deleted.

### Engine

- `settings_default.COMMAND_DEFAULT_CLASS` switched from
  `evennia.commands.default.muxcommand.MuxCommand` to
  `evennia.commands.command.Command`. Default commands that use
  `COMMAND_DEFAULT_CLASS` now inherit `Command` (switch parsing
  preserved via `Command.parse` since `+underspire.5`).
- `evennia/commands/default/account.py`: every account command now
  subclasses `AccountCommand` directly. All `account_caller =
  True` attributes dropped — redundant on `AccountCommand`, which
  carries the engine flag `account_command_caller = True`.
- `evennia/contrib/*`: every MuxCommand / MuxAccountCommand
  subclass converted to `Command` / `AccountCommand`, covering
  both direct-import (`from evennia.commands.default.muxcommand
  import MuxCommand`) and `default_cmds.MuxCommand` patterns. The
  single contrib `account_caller = True` usage
  (`ingame_reports.CmdReport`) becomes `account_command_caller =
  True`.
- `evennia/commands/default/comms.py`: `CmdChannel` and `CmdPage`
  swapped `account_caller = True` for `account_command_caller =
  True`. `CmdObjectChannel` (character-context channel command)
  overrides with `account_command_caller = False` to disable the
  engine pre-parse normalisation. Test helpers that runtime-toggled
  `cmd.account_caller = False` for character-context dispatch
  swapped to `cmd.account_command_caller = False`.
- `evennia/__init__.py`: `default_cmds` API extended with
  `Command` and `AccountCommand` shortcuts so contrib code can
  subclass them via the public path (`default_cmds.Command`)
  rather than reaching into `evennia.commands.command`.
- `evennia/utils/test_resources.py`: test patches of
  `COMMAND_DEFAULT_CLASS` swapped from `MuxCommand` to `Command`.
- Engine tests that previously exercised `MuxCommand` /
  `MuxAccountCommand` behaviour deleted: the legacy `account_caller`
  parse-time block is gone (no path to test); the engine
  normalisation path is already covered by `AccountCommand` tests;
  the deprecation-warning machinery is gone with the classes.
  `test_mux_command` in `commands/default/tests.py` redirected to
  use `Command` directly (still tests E2E switch handling via
  `.call()`).

### Rationale

After `+underspire.5`, `MuxCommand` no longer carried unique
behaviour beyond the legacy `account_caller` parse-time
normalisation block. Once the engine sweep had no remaining
internal subclasses, the classes were dead weight and the legacy
block had no consumers worth preserving. Delete instead of
deprecate; downstream pins `+underspire.5` if they need migration
time.

### Migration

- **`from evennia.commands.default.muxcommand import MuxCommand`**:
  → `from evennia.commands.command import Command`. Switch parsing
  keeps working (now in `Command.parse`). If your `parse` override
  called `super().parse()` expecting the historical no-op, add
  `parse_mux_syntax = False` to the class — see `+underspire.5`
  migration notes.
- **`from evennia.commands.default.muxcommand import
  MuxAccountCommand`**: → `from evennia.commands.command import
  AccountCommand`. Engine pre-parse normalisation already gives the
  same `caller`/`account`/`character` shape via the
  `account_command_caller = True` flag.
- **`default_cmds.MuxCommand` / `default_cmds.MuxAccountCommand`**:
  → `default_cmds.Command` / `default_cmds.AccountCommand` (added
  to the API in this release).
- **Third-party subclasses with `account_caller = True`** (without
  the engine flag): the parse-time normalisation block is gone.
  Switch the base to `AccountCommand` (cleanest) or set
  `account_command_caller = True` directly on the class.

### Tests

All `evennia.commands`, `evennia.commands.default`, and
`evennia.contrib` tests pass (modulo pre-existing failures in
`puzzles`, `mail`, and `extended_room` that exist on
`+underspire.5` and are unrelated to this change).

### Bug fix: Redis L2 attribute cache + idmapper teardown

`RedisCachedModelAttributeBackend` was not wired into the idmapper
`flush_cache` path. CI runs with persistent Redis saw stale
`Attribute` rows leak from one test's writes into a later test's
reads; local runs (Redis backend disabled by default) never tripped
it. New `evennia.typeclasses.redis_attr_cache.flush_all_keys()`
scans `attr:<version>:*` and deletes via `SCAN` (non-blocking on
large keyspaces); `evennia.utils.idmapper.models.flush_cache` calls
it after the in-process idmapper flush. No-op when
`ATTRIBUTE_REDIS_CACHE_ENABLED` is off or Redis is unreachable,
wrapped so any Redis hiccup never breaks idmapper flush.

Covers all three flush call-sites by free: test `tearDown`,
`post_migrate` signal, and the `@reload/flush` admin command.

Three new tests in `test_attribute_fork.TestRedisAttrCache`:
`flush_cache` drives the scan+delete; disabled flag is a no-op
without opening a connection; unreachable Redis is a no-op without
raising.

---

## 6.0.0+underspire.5 — Switch parsing in Command.parse

MuxCommand's switch / lhs / rhs parsing is promoted into the base
`Command.parse`. Sets up the `MuxCommand = Command` collapse coming
in a follow-up release.

### Engine

- `Command.parse` now splits `self.args` into `switches`, `lhs`,
  `rhs`, `lhslist`, `rhslist`, `arglist`, and stashes the original
  on `self.raw`. Honours optional class attrs `switch_options`
  (validates supplied switches, abbreviation match, warns on
  unknown/ambiguous via `self.msg`) and `rhs_split` (delimiter or
  iterable of delimiters, default `"="`).
- `Command.parse_mux_syntax = True` class flag gates the new
  behaviour. Subclasses that want `super().parse()` to behave like
  the historical no-op set `parse_mux_syntax = False` — one line,
  no need to override `parse`.
- `MuxCommand.parse` reduced to `super().parse()` + the legacy
  `account_caller` normalisation block. The block stays for
  third-party subclasses that set `account_caller = True` without
  the engine `account_command_caller` flag; it short-circuits when
  the engine flag is set (unchanged from `+underspire.3.1`).

### Backwards compatibility

- Existing `MuxCommand` subclasses: no change. `MuxCommand.parse`
  still produces the same attribute shape (via `super().parse()` now
  instead of inline).
- Existing `Command` subclasses that override `parse` without
  calling `super`: no change.
- Existing `Command` subclasses that *do* call `super().parse()`
  expecting the historical no-op: **breaking** — they now get switch
  parsing applied to `self.args`. Fix: add `parse_mux_syntax =
  False` to the subclass. The pattern of calling `super` on a no-op
  is unusual but exists; flagged loudly here.

### Migration

If `super().parse()` calls in your `Command` subclasses produced
unexpected `self.args` mutation (stripped, post-switch), add:

```python
class MyCmd(Command):
    parse_mux_syntax = False
    def parse(self):
        super().parse()  # no-op shape, as before
        ...
```

If you want the new switch parsing in a `Command` subclass that
previously had its own parse logic, drop the override and let
`Command.parse` handle it (or call `super().parse()` first and add
your own logic after).

### Tests

`evennia.commands.tests.TestAccountCommandNormalization` extended
with three cases: base `Command.parse` produces MuxCommand-style
attrs; `parse_mux_syntax = False` opts out; `MuxCommand.parse`
delegates to `super` then runs the legacy `account_caller` block.

---

## 6.0.0+underspire.4 — Drop at_pre_cmd subclass guard

The `__init_subclass__` guard introduced in `+underspire.2` is
removed. The post-parse `at_pre_cmd` hook is now freely
subclass-able: override it for input validation that needs parsed
state (`self.args`, `self.switches`, `self.character`) or for
gating that depends on parse results.

### Engine

- `Command.__init_subclass__` deleted. There was no other logic in
  it; the guard was its sole purpose.
- `Command.at_pre_cmd` docstring updated to describe its role
  ("preferred home for post-parse, pre-dispatch logic") and to
  distinguish it from `at_pre_parse`.

### Dispatch order (unchanged from `+underspire.2`)

```
at_pre_parse → parse → at_pre_cmd → func → at_post_cmd
```

Both `at_pre_parse` and `at_pre_cmd` return-truthy-to-abort. Truthy
abort from `at_pre_cmd` skips `func` and `at_post_cmd`.

### Migration

No required action. Downstream that previously moved post-parse
logic into `func` (or into a `parse` override) to dodge the guard
can now move it into `at_pre_cmd` if that reads better. No code
that ran on `+underspire.3.1` breaks on `+underspire.4`.

### Tests

`TestAtPreCmdRename.test_subclassing_at_pre_cmd_raises_typeerror_at_import`
deleted. Replaced with
`test_at_pre_cmd_override_runs_after_parse_before_func` and
`test_at_pre_cmd_truthy_return_aborts_after_parse_before_func`,
which exercise the now-allowed override path.

---

## 6.0.0+underspire.3.1 — MuxAccountCommand opts into engine normalisation

Targeted follow-up to `+underspire.3` driven by downstream feedback.
Unifies the detection contract for "this command is account-context"
so a single flag covers both engine `AccountCommand` and stock
`MuxAccountCommand` subclasses.

### Engine

- `evennia.commands.default.muxcommand.MuxAccountCommand` now sets
  `account_command_caller = True`. Engine pre-parse normalisation
  (`cmdhandler._normalize_account_command_caller`) therefore runs for
  every `MuxAccountCommand` subclass, including stock commands like
  `CmdIC`, `CmdOOC`, and the account-context `CmdHelp`.
- `MuxCommand.parse`'s legacy `account_caller` normalisation block
  short-circuits when `account_command_caller` is truthy on the
  instance. The engine path produces the same
  `self.caller` / `self.account` / `self.character` shape, so the
  legacy block would just re-call `get_puppet` for no benefit. Pure
  `account_caller`-only third-party subclasses (no engine flag) keep
  the legacy path during the deprecation window.

### Downstream detection contract

Use the flag, not `isinstance`:

```python
def _is_account_command(cmd):
    return getattr(cmd, "account_command_caller", False)
```

This covers both `evennia.commands.command.AccountCommand` and
`MuxAccountCommand` subclasses uniformly. A pure
`isinstance(matched, AccountCommand)` check is **not** safe yet:
stock `MuxAccountCommand` does not inherit from engine
`AccountCommand`. The flag-based check stays correct across the
forthcoming MuxCommand sweep too.

### Migration

No breaking changes. Downstream that previously fell back to
`getattr(cmd, "account_caller", False)` because the engine flag missed
stock account commands can drop the fallback and rely on
`account_command_caller` alone.

### Note on tagging

Local version uses an extra dot (`+underspire.3.1`), which is
permitted by PEP 440 local-segment rules and orders strictly above
`+underspire.3`. Tagged on a `--no-ff` merge into `underspire`.

---

## 6.0.0+underspire.3 — Phase 2 step 2: AccountCommand + caller normalisation

Adds the first-class engine class for Account-level commands and the
cmdhandler normalisation that guarantees a consistent
``caller``/``account``/``character`` shape before any hook runs.

### Engine

- New ``evennia.commands.command.AccountCommand``: sibling of
  ``Command`` with class flag ``account_command_caller = True``. No
  metaclass tricks; subclasses just inherit.
- ``Command.account_command_caller = False`` added on the base so the
  attribute always exists.
- ``cmdhandler._normalize_account_command_caller(cmd, caller,
  cmdset_providers)``: invoked inside ``_run_command`` after the
  existing runtime-attr block and *before* the ``_testing`` early
  return and ``at_pre_parse``. For commands with
  ``account_command_caller`` truthy:
  - ``cmd.caller`` becomes the Account (from
    ``cmdset_providers["account"]``, falling back to
    ``caller.account``).
  - ``cmd.account`` becomes the same Account (alias of ``cmd.caller``).
  - ``cmd.character`` becomes the puppet for the dispatching session
    (``cmdset_providers.get("object")``), or ``None`` if OOC.
  No-op for ordinary ``Command`` subclasses; their ``cmd.caller`` is
  untouched and no ``character`` attribute is set on the instance.
- ``EvenniaCommandTestMixin.call`` mirrors the normalisation so
  ``BaseEvenniaCommandTest``-based tests of ``AccountCommand``
  subclasses observe the same attribute shape as live dispatch.

### Tracing / caches (unchanged on purpose)

- ``command_trace.begin_command_trace`` continues to receive the
  pre-normalisation ``caller`` (the dispatch origin). The trace_id
  surfaces puppet→account routing in error logs without rewriting
  caller identity.
- ``cmd_access_cache`` keys on the pre-normalisation ``caller``
  (resolved during ``_COMMAND_PARSER``). Access is still gated against
  the puppeted Character; normalisation only affects ``cmd.caller``
  after the match, which is the right boundary. ``_cmd_identity``
  differentiates ``Command`` and ``AccountCommand`` subclasses
  naturally via class name, so cache keys do not collide.

### Migration

No breaking changes. Downstream ``AccountCommand`` shims can subclass
``evennia.commands.command.AccountCommand`` and drop their own
``_normalize_account_caller`` step. See
``CMDSET_MIGRATION.md`` §"Phase 2 — Step 2".

---

## 6.0.0+underspire.2 — Phase 2 step 1: at_pre_cmd rename + dispatch reorder

First slice of the Phase 2 cmdset refactor. Renames the pre-parse hook
and introduces a (currently engine-only) post-parse hook. Pure rename
under a hard-error guard: no semantic change yet, no auto-alias.

### Breaking

- **`Command.at_pre_cmd` renamed to `Command.at_pre_parse`.** Same
  semantics (runs before `parse()`, return truthy to abort).
- **`Command.__init_subclass__` hard-error guard.** Any subclass that
  defines `at_pre_cmd` raises `TypeError` at class-creation pointing at
  the file/class with a migration message. The new post-parse
  `at_pre_cmd` exists but is engine-only during this window;
  subclassing it is forbidden so legacy overrides cannot silently
  no-op.
- **Cmdhandler dispatch order changed** to
  `at_pre_parse → parse → at_pre_cmd → func → at_post_cmd`. The new
  `at_pre_cmd` runs *after* parse. The base class's `at_pre_cmd` is a
  no-op; it does nothing observable to current code.
- `evennia.utils.test_resources.BaseEvenniaCommandTest.call` mirrors
  the new dispatch order. Tests that subclass `Command` and override
  the old hook must rename to `at_pre_parse`.

### Migration

If you see `TypeError: <Module>.<Class> defines at_pre_cmd, which was
renamed to at_pre_parse...` at import:

1. Rename the method to `at_pre_parse`.
2. Update any `super().at_pre_cmd()` calls to `super().at_pre_parse()`.
3. Re-import; the guard accepts the new name.

The post-parse `at_pre_cmd` will become subclass-able in a future
release (target: `6.0.0+underspire.4`) once the guard is removed.

### Engine renames in this commit

`evennia/contrib/game_systems/storage/storage.py`,
`evennia/contrib/base_systems/email_login/email_login.py`,
`evennia/contrib/base_systems/ingame_reports/reports.py`,
`evennia/commands/default/unloggedin.py`. The no-op
`MuxCommand.at_pre_cmd` stub was deleted (it inherited the base
no-op). Test fixtures in `commands/default/tests.py` and
`contrib/base_systems/ingame_reports/tests.py` renamed.

### Signals

`on_command_pre.elapsed_ms` window unchanged in semantics but
documented relative to `at_pre_parse` rather than the old name. No
signal contract changes.

### PostgreSQL session init via `connection_created`

`apply_postgres_engine_defaults` no longer injects
`OPTIONS["options"] = "-c statement_timeout=..."` and
`build_read_replica_entry` no longer injects
`-c default_transaction_read_only=on`. PgBouncer in transaction-pool
mode rejects the `options` startup parameter at the protocol level
(`FATAL: unsupported startup parameter in options: ...`), so the
previous defaults broke pooled deployments out of the box.

Replacement: a `connection_created` receiver issues `SET
statement_timeout` on the `default` alias and `SET
default_transaction_read_only = on` on aliases registered by
`build_read_replica_entry`. Works through PgBouncer. Caveat for pure
transaction-pool deployments: session-level `SET` may not persist
across backend rebinding — set at the role level (`ALTER ROLE ... SET
statement_timeout = '30s'`) for hard guarantees, and treat the signal
receiver as best-effort on top.

`build_read_replica_entry(primary, name=...)` now has a side effect:
the `name` argument is registered in
`evennia.server.database_postgres._READ_REPLICA_ALIASES` so the
receiver knows which aliases get the read-only flag. The caller must
still assign the returned dict at `DATABASES[name]`.

---

## 6.0.0+underspire.1 — initial fork version mark

> **⚠ Packaging caveat.** The tagged commit (`02063d4e6`) bumped
> `evennia/VERSION.txt` to `6.0.0+underspire.1` but `pyproject.toml`
> still read `6.0.0`. A follow-up commit (`607ecc4fa`) fixed
> `pyproject.toml`, but it landed after the tag. Pip-installing from
> exactly `underspire.1` therefore records the installed version as
> `6.0.0` (no local segment), even though `evennia.__version__` at
> runtime reads `6.0.0+underspire.1` from `VERSION.txt`.
>
> Consumers that gate on `pkg_resources.get_distribution("evennia").version`
> (or equivalent) should pin **`>= 6.0.0+underspire.2`** instead. Runtime
> code reading `evennia.__version__` is unaffected.
>
> Tags from `underspire.2` onward bundle both files in the same commit.


First release tagged after the fork diverged meaningfully from upstream
`6.0.0`. Base = upstream `6.0.0`; everything below is fork-only.

### Engine

**Cmdset refactor (phases 0 + 1, partial)**
- Phase 0 hygiene: `CmdSet.remove()` is idempotent and returns `bool`;
  `CmdSet.replace(old, new)` added. Removes the need for the downstream
  `cmdset_utils.py` helper.
- Phase 1 signals (`evennia.commands.signals`): `on_command_pre`,
  `on_command_post`, `on_command_error`, plus the companion
  `on_cmdset_merge_error` for the three merge-error sites in
  `get_and_merge_cmdsets`. `send_robust` semantics so receivers cannot
  break dispatch.
- `ErrorReported` carries `.trace_id`, populated from
  `command_trace.get_trace_id()` when the exception is raised inside an
  active command trace.
- Session-proxy contract on `cmdhandler.cmdhandler(session=...)`: any
  object with `get_cmdset_providers()` works. Multipuppet relays can
  drop their `Command` monkey-patch.
- Signal payload + `cmd.session` always carry the real `ServerSession`
  when one is involved. Proxies expose the wrapped real session via a
  `real_session` attribute. `callertype="session"` no longer leaks a
  `None` session into signal kwargs.

See `CMDSET_REFACTOR.md` and `CMDSET_MIGRATION.md` for the full plan
(phases 2–4 pending).

**Attribute system**
- Typed value columns on `Attribute` (`db_val_type`, `db_int_val`,
  `db_float_val`, `db_str_val`) for fast-path reads of simple Python
  types; pickle path retained for complex values and back-compat.
- Write-behind dirty queue for attribute updates: `do_update_attribute`
  marks the attr dirty instead of saving immediately; `flush_all_dirty()`
  bulk-writes from the server tick.
- **ORM query correctness fix:** `TypedObjectManager.get_queryset` and
  a new `AttributeManager.get_queryset` flush pending writes before any
  query, so `.filter(db_attributes__db_value=...)` and
  `get_by_attribute(value=obj)` see committed values rather than stale
  rows. Reentrance guarded.
- Redis-backed attribute cache backend added (`redis_attr_cache.py`).

**Command tracing / profiling**
- `evennia.utils.command_trace` provides a thread-local trace-id around
  each dispatched command, surfaced on `ErrorReported` and signal kwargs.
- `evennia.server.prometheus_metrics` exposes per-command and
  attribute-flush counters/gauges.
- `evennia.typeclasses.attribute_metrics` records pending/flushed/duration
  for the write-behind queue.

**Cmdset performance / caching**
- `commands/location_cmdset_cache.py` caches local-obj cmdset stacks
  per-location keyed by the merge fingerprint.
- `commands/cmd_access_cache.py` caches per-caller cmd access results.

**Object resolution**
- `evennia/objects/scene_index.py` added for fast recipient resolution in
  `DefaultObject.get_msg_recipients()` (formerly silent-fallback wrapped;
  now hard errors propagate so misuse is visible).

**At-init scheduler / signal lifecycle**
- `evennia/server/at_init_scheduler.py` defers `at_init` hooks to avoid
  reload races.

**Other**
- Python 3.13/3.14 syntax warning fixes.
- IntFlag serialization in `dbserialize.to_pickle` honors the enum value.
- ContentType-based dbobj packing handles defaultdict misses cleanly.

### Migration / breaking

- Settings: new `INPUT_FTFY_NORMALIZE`, `COMMAND_TRACE_ENABLED`,
  `ATTRIBUTE_FLUSH_*`, `ATTRIBUTE_BACKEND_CLASS` (see
  `evennia/settings_default.py`).
- `do_update_attribute` no longer saves synchronously. Code paths that
  expected an immediate DB write should call `flush_all_dirty()` or rely
  on the manager-level flush in `get_queryset`.
- `on_command_error` does NOT fire for cmdset-merge failures; subscribe
  to `on_cmdset_merge_error` for those.

### Known gaps

- Cmdset refactor phases 2–4 not yet started. Tracked in
  `CMDSET_REFACTOR.md` §5.
- `pyproject.toml` version field tracks upstream `6.0.0`; update to
  `6.0.0+underspire.1` to match `VERSION.txt`.
