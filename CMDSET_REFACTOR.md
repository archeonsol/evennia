# Cmdset / Command Refactor Plan

Fork-internal plan for fixing the command + cmdset subsystem in the Underspire
fork of Evennia 6.x. Lives at the repo root next to `ENGINE.md`; archive after
the refactor lands.

Target consumer: Underspire (`../newmoo`). No other downstream consumers.

---

## 1. Goals

1. **Delete the monkey-patches.** Replace `_patch_cmdset_sys_cmd_dedup` (already
   done upstream), `_patch_cmdhandler_error_capture`, `patch_evennia_command`,
   and `patch_relay_default` with real engine extension points.
2. **Make `AccountCommand` first-class.** Engine guarantees `self.caller =
   Account`, `self.character = puppet` before the user's hooks run.
3. **Fix the hook ordering.** `parse()` runs before any "ready to dispatch"
   hook, so subclasses don't have to redo work in two places.
4. **Kill prefix-strip + `startswith` matching.** `@open` and `open` are
   distinct keys; no `CMD_IGNORE_PREFIXES` global stripping. Resolution is
   token-boundary based.
5. **Promote the trie parser.** Underspire's `world/parsing/trie_parser.py`
   becomes the engine default.
6. **Smaller cmdset surface.** `CmdSet.replace()`, idempotent `remove()`,
   `has()`; retire `safe_remove`/`replace_command` helpers in the game.
7. **Land without losing the perf work.** The performance overhaul in commit
   `3d41cb97c` (location cmdset cache, cmd access cache, command trace,
   write-behind attrs) is orthogonal and must continue to function.

## 2. Non-goals

- Multipuppet relay design. It stays in the game; the engine just stops
  forcing it to monkey-patch `Command`.
- Reworking `CmdSetHandler` storage / persistence.
- Help system rewrite. `CmdSetHelp`-style edge cases are out of scope.
- Channel command rework. Channels keep their current cmdset-injection
  approach.
- Touching the webclient input pipeline.

## 3. Current state (post-merge `3d41cb97c`)

### Already in the engine

| Item | Location | Status |
|---|---|---|
| CmdSet `__add__` sys-cmd dedup | `evennia/commands/cmdset.py:530` | **Done** |
| Per-command trace context | `evennia/utils/command_trace.py` | **Done**, wired in `cmdhandler.cmdhandler` |
| Location cmdset cache | `evennia/commands/location_cmdset_cache.py` | **Done**, opt-out via `LOCATION_CMDSET_CACHE_ENABLED` (default on) |
| Cmd access cache | `evennia/commands/cmd_access_cache.py` | **Done**, opt-in via `CMD_ACCESS_CACHE_ENABLED` |
| Display name / look prefetch / lock cache | `evennia/utils/display_name_cache.py`, etc. | **Done** |
| Worker pool, at_init scheduler, write-behind attrs | various | **Done** |

### Still broken / pending

| Pain point | File (current) | Phase that fixes it |
|---|---|---|
| `Command.match` is `startswith` | `evennia/commands/command.py:359` | 3 |
| `CMD_IGNORE_PREFIXES` global strip | `evennia/commands/command.py:21`, `cmdparser.py:16` | 3 |
| No `AccountCommand` in engine | game has its own in `commands/base_cmds.py` | 2 |
| `at_pre_cmd` runs before `parse` | `cmdhandler.py:660` region | 2 |
| `CmdSet.remove(missing)` raises | `evennia/commands/cmdset.py` | 0 |
| No `replace()` / `has()` on CmdSet | same | 0 |
| `ErrorReported` traceback capture has no hook | `cmdhandler.py:682` | 1 |
| Profiling needs to monkey-patch `Command` | game-side `world/profiling.py` | 1 |
| Multipuppet relay patches base `Command` | game-side `world/multipuppet.py` | 1 |
| No trie parser in engine | `world/parsing/trie_parser.py` (game) | 4 |
| `ftfy.fix_text` normalization done in game | `commands/base_cmds.parse_game_cmdline` | 2 |

## 4. Decisions locked in

- **`ftfy` normalization:** Move into the engine, **on by default** in this
  fork's `settings_default.py`. Driven by `INPUT_FTFY_NORMALIZE` (default
  `True`). `ftfy` becomes a regular dep, not optional.
- **`MuxCommand` / `MuxAccountCommand`:** Keep as **deprecated compat
  aliases**. Issue `DeprecationWarning` on first instantiation. Remove in a
  follow-up release after the game migrates.
- **Hook naming:** **Rename current `at_pre_cmd` to `at_pre_parse`**. Introduce
  a new `at_pre_cmd` that runs **after** `parse()`. Cmdhandler order:
  `at_pre_parse -> parse -> at_pre_cmd -> func -> at_post_cmd`. This is a
  loud breaking change inside the engine *and* the game; grep is the migration
  tool.
- **Performance caches:** `CMD_ACCESS_CACHE_ENABLED` flipped to **`True`** in
  the fork default. `LOCATION_CMDSET_CACHE_ENABLED` stays `True`. Tests that
  break with caching get fixed, not disabled wholesale.
- **Plan doc home:** `CMDSET_REFACTOR.md` at repo root.

## 5. Phasing

Each phase is independently shippable. The game (Underspire) can take phases
in order; each phase has a "game-side cleanup" section listing what becomes
deletable.

### Phase 0 — CmdSet hygiene

Pure API additions. No behavior change for stock callers.

**Engine changes:**
- `CmdSet.remove(cmd_or_key, strict=False)` — default behavior becomes
  idempotent. Pass `strict=True` for old raise-on-missing behavior. Existing
  `CmdSet.remove` callers in the engine that depend on the raise must opt in.
- New `CmdSet.replace(old, new)` — removes `old` (idempotent) and adds `new`.
  Returns `True` if `old` was found, `False` otherwise.
- New `CmdSet.has(cmd_or_key) -> bool`.
- New `CmdSet.get(key) -> Command | None` for introspection (separate from
  internal `_commands_by_key` access).

**Tests:** `evennia/commands/tests.py` — round-trip `add`/`remove`/`replace`/
`has`/`get`; idempotent `remove` on missing key; `replace` returning correct
truthy/falsy.

**Game-side cleanup:** delete `commands/cmdset_utils.py`; replace call sites
in `commands/default_cmdsets.py` with direct `self.replace(...)` and
`self.remove(...)`.

**Risk:** essentially none. The signature change on `remove` defaults to the
safer behavior; nothing in the engine currently catches the old
`KeyError`-on-missing case for control flow that I've found, but a grep pass
during implementation will confirm.

**Rollback:** revert single commit.

---

### Phase 1 — Engine hooks (kill monkey-patches)

Adds extension points so the game can stop patching engine internals. Does
not yet change command semantics.

**Engine changes:**

1. **`cmdhandler.on_command_error` signal** (Django signal or simple registry
   in `cmdhandler`). Fires inside the `except` block that currently raises
   `ErrorReported`. Receivers get
   `(cmd, caller, session, exc, traceback_text, trace_id)`. `trace_id` comes
   from `command_trace.get_trace_id()`.

   **Companion signal `on_cmdset_merge_error`** covers the three merge-error
   sites in `get_and_merge_cmdsets` where `ErrorReported` is raised without a
   `cmd` instance (cmdset-getter and merge failures). Receivers get
   `(caller, session, raw_string, exc, traceback_text, trace_id)`. Sender is
   `type(caller)` since no command exists yet.

2. **`cmdhandler.on_command_pre` / `on_command_post` signals**. Fire around
   `func()` invocation. Receivers get the same context plus `elapsed_ms` on
   the post signal. Profiling subscribes to these instead of patching
   `Command`.

3. **Session proxy support in `cmdhandler.cmdhandler`.** Accept either a real
   `ServerSession` or any object with `get_cmdset_providers()` /
   `get_puppet()`. Documented contract. Multipuppet relay constructs a proxy
   and passes it in instead of patching the base `Command`.

4. **`ErrorReported` carries `trace_id`** as an attribute, so external
   handlers can correlate.

**Tests:**
- `evennia/commands/tests/test_command_signals.py` — register a receiver,
  trigger a command that raises, assert receiver got
  `(cmd, caller, session, exc, tb, trace_id)`.
- Pre/post signal ordering: `on_command_pre` fires before `func`,
  `on_command_post` fires after `at_post_cmd`, both receive the same
  `trace_id`.
- Session proxy: pass a duck-typed object, assert command dispatch works.

**Game-side cleanup:**
- Delete `_patch_cmdhandler_error_capture` from `server/conf/at_server_startstop.py`.
- Rewrite `world/profiling.py::patch_evennia_command` as a signal receiver
  registered at server start.
- Rewrite multipuppet relay to construct a session proxy and pass it to
  `cmdhandler.cmdhandler` directly; drop `patch_evennia_command` for relay
  purposes.

**Risk:** low. Signals are additive. Session proxy support is additive
(existing real sessions still work). The only subtle risk is signal
receivers throwing; document and wrap in try/except inside the dispatcher
so a bad receiver can't break command processing.

**Rollback:** revert; underspire restores monkey-patches.

---

### Phase 2 — Account/Character split + hook reorder

The breaking change inside the engine. Once done, `at_pre_cmd` means
"command is parsed, about to dispatch."

**Engine changes:**

1. **Rename `Command.at_pre_cmd` to `Command.at_pre_parse`** wherever it
   exists in the engine. Grep all `at_pre_cmd` definitions in
   `evennia/commands/default/*.py` and rename.

2. **Add new `Command.at_pre_cmd` that runs after `parse()`.** Default
   implementation returns `None`. Cmdhandler dispatch becomes:

   ```python
   if cmd.at_pre_parse():     # was: at_pre_cmd
       return
   cmd.parse()
   if cmd.at_pre_cmd():       # NEW
       return
   yield from cmd.func()
   cmd.at_post_cmd()
   ```

3. **Move `ftfy.fix_text` into `cmdhandler`** before the cmdset merge, gated
   on `INPUT_FTFY_NORMALIZE` (default `True` in fork). The normalized
   `raw_string` is the one stored on `cmd.raw_string`.

4. **`evennia.commands.command.AccountCommand`** — new sibling class of
   `Command`. Cmdhandler detects it via an attribute
   (`account_command_caller = True` class flag, or `isinstance` check;
   leaning toward flag to avoid import cycles). When dispatching an
   `AccountCommand`:
   - `self.caller` is set to the Account.
   - `self.character` is set to the puppet for `self.session`, or `None`.
   - `self.account` is an alias for `self.caller`.
   - This normalization happens **before** `at_pre_parse`, so both hooks
     see consistent state.

5. **`MuxCommand = Command`** and **`MuxAccountCommand = AccountCommand`** as
   module-level aliases in `evennia/commands/default/muxcommand.py`. First
   subclass instantiation emits `DeprecationWarning` (use a metaclass hook
   or `__init_subclass__`).

6. **Switch parsing built into `Command.parse`** with the MuxCommand syntax
   (`cmd/switch/switch args`). Same parser used today, just promoted.

**Tests:**
- `at_pre_parse` runs before `parse`; `at_pre_cmd` after. Use a command
  that sets a marker in each hook and assert order.
- `AccountCommand`: dispatching via a session with a puppet sets `caller` to
  Account, `character` to puppet; with no puppet, `character` is `None`.
- `ftfy` normalization: mojibake input arrives at `cmd.raw_string` clean.
  Disable via setting, raw input passes through.
- Mux deprecation warning fires once per subclass.

**Game-side cleanup:**
- Underspire's `commands/base_cmds.py::AccountCommand` becomes a re-export of
  the engine class (or thin subclass adding `ProfilingCommandMixin` if the
  profiling hook isn't enough).
- `parse_game_cmdline` keeps **only** the switch-parsing logic (or is
  deleted entirely if engine's `Command.parse` matches).
- `_normalize_account_caller` deleted; engine does it.
- Every `at_pre_cmd` override in the game that relied on running before
  parse gets renamed to `at_pre_parse`. Grep target.
- `skip_character_state_gate` becomes a documented contract on
  `AccountCommand` (it's always `True`), or — cleaner — the game's
  gatekeeper checks `isinstance(cmd, AccountCommand)` directly.

**Risk:** **highest in the plan.** Every `at_pre_cmd` override in the engine,
the game, and any tests becomes a rename. Mitigation:
1. Grep audit before merging: produce a complete list of `def at_pre_cmd`
   sites and confirm each is renamed.
2. Make `Command.at_pre_cmd` raise a clear `RuntimeError` if subclasses
   define `at_pre_cmd` with the old signature AND set a sentinel attribute
   `_legacy_at_pre_cmd = True` — gives a transition window. Optional;
   probably not worth it given small downstream.
3. Tests must run green on the engine before touching the game.

**Rollback:** revert is messy because of the rename. Keep this on its own
branch; cherry-pick Phase 0/1 onto a recovery branch if Phase 2 needs to be
held.

---

### Phase 3 — Token-boundary matching + drop prefix-strip

The big semantic change. After this, `@open` and `open` are unrelated keys,
matching is by whitespace/end-of-input boundary, and `CMD_IGNORE_PREFIXES`
is gone.

**Engine changes:**

1. **Rewrite `Command.match`** (`evennia/commands/command.py:359`):
   ```python
   def match(self, cmdname, include_prefixes=True):
       # cmdname is the full input string, lowercased.
       # Match against _keyaliases. A match is valid iff cmdname[len(key)]
       # is end-of-string or whitespace (no arg_regex override).
       for key in self._keyaliases:
           if cmdname == key:
               return key, key
           if cmdname.startswith(key) and cmdname[len(key)].isspace():
               return key, key
       return None, None
   ```
   - `arg_regex` still honored if explicitly set — gives commands the
     escape hatch for non-whitespace argument separators.
   - `include_prefixes` argument is kept on the signature for backward compat
     but ignored. Removed in a future cleanup pass.

2. **Delete `CMD_IGNORE_PREFIXES` handling** in `cmdparser.build_matches` and
   `Command._optimize` (`_noprefix_aliases` map). The setting can remain in
   `settings_default.py` for one release with a startup warning if non-empty;
   removed after.

3. **Engine default commands keyed for the new world.**
   `evennia/commands/default/building.py`: builder commands keep `@dig`,
   `@open`, etc. as **their actual key**, not as aliases stripped at parse
   time. Re-key every default builder/admin command. List:
   - `CmdOpen` → key `@open` (currently aliased as `@open` with key `open`)
   - `CmdDig` → key `@dig`
   - `CmdCreate` → key `@create`
   - `CmdDestroy` → key `@destroy`
   - `CmdName` → key `@name`
   - `CmdDesc` → key `@desc`
   - `CmdTag` → key `@tag`
   - `CmdTeleport` → key `@tel` (current alias `@tel`/`@teleport`)
   - …full list compiled during implementation.
   Player commands stay unprefixed.

4. **Cmdparser cleanup.** `cmdparser.cmdparser` calls `build_matches` twice
   today — once with `include_prefixes=True`, once with
   `include_prefixes=False`. Drop the second pass; matching is now
   single-shot.

**Tests:**
- `match`: `look` matches `CmdLook` (key `look`); `looker` does not.
- `match`: `look here` matches with `args == " here"`.
- `match`: `@open foo = bar` matches `CmdOpen` (key `@open`); plain `open foo`
  does **not** match the builder command.
- `match`: aliases work the same (longest-first walk).
- Resolution test: a room containing exit `out` and a `CmdOut` builder
  command — both should resolve cleanly under the new rules; this is the
  scenario that motivated Underspire's trie abbrev expansion.
- Cmdparser test: only one matching pass; `include_prefixes` no longer
  affects results.

**Game-side cleanup:**
- Delete `commands/staff_admin_wrappers.py` (the `CmdAt*` ban/wall/perm/etc.
  layer). Engine commands now have the right keys.
- Drop `safe_remove(default_building.CmdOpen)` / `CmdAtOpen()` workaround in
  `CharacterCmdSet.at_cmdset_creation`.
- Drop `replace_command(self, CmdGet, CmdGet())` and related grapple-after-
  get ordering hacks; the alias collisions they worked around are gone.
- Remove `GameCmdTypeclass` if its only purpose was avoiding the
  `@typeclasses` EvMore-list alias collision.

**Risk:** **highest external risk.** Anyone (else) using the fork with custom
commands that relied on prefix-strip will break. We are the only consumer,
so this is acceptable.

Internal risk: missing a builder/admin command in the re-key pass. Mitigation:
write a one-shot script that lints engine default cmdsets for commands whose
key does not start with a `CMD_IGNORE_PREFIXES` char but whose aliases do
(or vice versa). Run before merging.

**Rollback:** revert. Game side gets the wrappers back. No data migration
needed.

---

### Phase 4 — Trie parser into engine

Replace `cmdparser.build_matches` with the trie-based collector. Cache the
trie inside the location cmdset cache so build cost is amortized.

**Engine changes:**

1. **Move** `world/parsing/trie_parser.py` (Underspire) to
   `evennia/commands/cmdparser_trie.py`. Drop the prefix-strip code path
   (Phase 3 removed the need).

2. **New default `COMMAND_PARSER`** points at `cmdparser_trie.cmdparser`.
   Old `cmdparser.cmdparser` retained as `cmdparser_linear` for users who
   want it; deprecated.

3. **Cache the trie on the merged cmdset object** built by `cmdsethandler`,
   keyed by the same generation used by `location_cmdset_cache`. Built
   lazily on first parse against a given merged cmdset.

4. **Settings:**
   - `COMMAND_PARSER_TRIE_FASTPATH` (default `True`) — single-candidate
     fast path, same semantics as Underspire's flag.
   - `COMMAND_PARSER_TRIE_ABBREV` (default `True`) — unambiguous
     first-token abbreviation expansion. Documented behavior.

**Tests:**
- Port Underspire's trie tests verbatim (or expand) into
  `evennia/commands/tests/test_cmdparser_trie.py`.
- Cache invalidation: build a trie, add a command to the cmdset, assert next
  parse rebuilds.
- Fastpath: command with custom `match()` does not take the fastpath.
- Abbrev: `o` matching `out` or `o` matching `open` resolves correctly per
  the shortest-key tie-break rule.

**Game-side cleanup:** delete `world/parsing/trie_parser.py` and any
`COMMAND_PARSER` setting override pointing at it.

**Risk:** medium. The trie code is fairly battle-tested in Underspire; the
risk is in the move + cache-integration step. Performance regression risk
mitigated by tests measuring parse time on a synthetic 200-command cmdset.

**Rollback:** revert setting default to `cmdparser_linear.cmdparser`.

---

### Phase 5 — Final game-side cleanup

Bookkeeping after engine work is merged. Tracked as a single PR against
Underspire, not the engine.

- Delete `commands/cmdset_utils.py`, `commands/staff_admin_wrappers.py`,
  `world/parsing/trie_parser.py`.
- Reduce `server/conf/at_server_startstop.py` monkey-patch block to just
  `_patch_run_init_hooks_defer_at_init_on_reload` and
  `_patch_evennia_logfile_closed_handle_guard` (if those still apply).
- Simplify `commands/base_cmds.py::Command` / `AccountCommand` to subclasses
  that add `ProfilingCommandMixin` and call into the new engine hooks.
- Remove `parse_game_cmdline`, `_normalize_account_caller`,
  `ooc_shell_at_pre_cmd`.
- Audit `commands/default_cmdsets.py` for now-unnecessary `safe_remove` calls.

## 6. Testing strategy

- Per-phase tests as listed above.
- **Cross-phase integration test:** a fresh tutorial-world game dir (no
  Underspire) boots, `look`/`get`/`drop`/`@dig` all work, builder commands
  do not match unprefixed input, account-level commands work in OOC.
- **Underspire integration:** after each phase merges, run the game's full
  pytest suite (`uv run pytest .agents/tools/tests/ -v` plus the game-side
  tests). No phase advances to "merged" until Underspire tests pass on the
  branch.
- **Performance regression check** after Phase 4: parse latency on a
  synthetic merged cmdset of N=500 commands should be ≤ current numbers.
  Add a `pytest-benchmark` test or a one-shot script.

## 7. Migration notes for Underspire

When each phase lands in the engine, the game side has a corresponding
cleanup commit. Order:

1. After **Phase 0**: delete `cmdset_utils.py`, update callers.
2. After **Phase 1**: rewrite profiling + multipuppet relay to use signals
   and session proxies.
3. After **Phase 2**:
   - Mass-rename `at_pre_cmd` → `at_pre_parse` in game commands that ran
     before parse. Audit each by hand: an override that *used* parsed args
     (`self.args`, `self.switches`, `self.character`) was already a latent
     bug and should stay (or move to) the new `at_pre_cmd`.
   - Delete game-side `AccountCommand`; subclass engine's.
   - Delete `_normalize_account_caller`, `parse_game_cmdline` if
     replaced fully.
4. After **Phase 3**: delete `staff_admin_wrappers.py`; remove
   `replace_command` / `safe_remove` calls that were specifically
   working around prefix-strip collisions.
5. After **Phase 4**: delete `world/parsing/trie_parser.py`; remove
   `COMMAND_PARSER` setting override.

## 8. Open items (to revisit during implementation)

These are not blocking the plan but need decisions during the relevant
phase:

- **CMD_NOMATCH integration:** Underspire's `CmdNoMatch` does emote parsing.
  After Phase 3, the no-match path is unchanged; `CmdNoMatch` continues to
  work. Nothing to do.
- **Account-cmd access cache:** With Phase 2, an `AccountCommand` has
  `self.caller = Account`. The `cmd_access_cache` keys on `caller.ndb`
  generation — for accounts this lives on Account ndb. Confirm cache hit
  rate is reasonable and `_invalidate_cmd_access_caches` correctly walks
  to puppeted characters when an account-level cmdset changes (logic
  already exists at `cmd_access_cache.py:136-143`; verify under the new
  caller assignment).
- **`arg_regex` semantics under token-boundary match:** Phase 3 keeps
  `arg_regex` as an escape hatch. We may find that no engine command needs
  it any more once whitespace boundary is the default. If so, deprecate in
  a follow-up.
- **EvMore "q" interaction:** the system-cmd dedup fix in `cmdset.py:530`
  is what made `q` stop multi-matching. Re-test after Phase 3 in case
  prefix-strip removal opens a new ambiguity.
- **Webclient OOB / sessionless commands:** confirm `cmd_access_cache` and
  the new signals behave when `session is None` (some internal calls into
  `cmdhandler`). Already partly handled; verify in Phase 1 tests.

## 9. Sequencing

Suggested merge order, branch-per-phase:

1. Phase 0 — small, merge first to build momentum.
2. Phase 1 — adds the hooks the game needs.
3. Game-side cleanup commit landing Phase 0+1 deletions in Underspire.
4. Phase 2 — the loud rename. Run on a dedicated branch for at least a
   few days of Underspire dev use before merge.
5. Game-side cleanup for Phase 2.
6. Phase 3 — token-boundary matching. Same caution as Phase 2.
7. Game-side cleanup for Phase 3 (large: delete `staff_admin_wrappers.py`).
8. Phase 4 — trie parser move. Lowest game-side risk because it's a
   like-for-like move of code already running in production.
9. Phase 5 — final game-side cleanup pass.

No timelines; each phase merges when its tests are green on both engine
and game.
