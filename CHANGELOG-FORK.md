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
