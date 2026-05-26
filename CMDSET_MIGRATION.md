# Cmdset Refactor — Downstream Migration Guide

Step-by-step notes for game-side updates as each phase of
[CMDSET_REFACTOR.md](CMDSET_REFACTOR.md) lands. Sections fill in as phases
merge.

For each phase: **Required** = must do or things break. **Auto** = engine
handles it but logs a warning. **Optional cleanup** = deletions you can do
once the new API is in.

---

## Phase 0 — CmdSet hygiene

**Required:** none. Phase 0 is purely additive.

**Auto:** none.

**Optional cleanup:**

- Replace any custom `safe_remove(cmdset, cmd)` helper with
  `cmdset.remove(cmd)` (now idempotent by default; returns `bool`).
- Replace `replace_command(cmdset, OldCmd, NewCmd())` helpers with
  `cmdset.replace(OldCmd, NewCmd())`.
- `cmdset.has(cmd_or_key)` is available for clarity over `cmd in cmdset`
  when checking by key string.

**Behavior notes:**

- `CmdSet.remove(missing_key)` no longer crashes when the key starts with
  `__` (was a latent `AttributeError`). Now returns `False`.
- `CmdSet.remove(cmd, strict=True)` is opt-in for the old raise-on-missing
  behavior.

**Side fix shipped with Phase 0:** `evennia.game_template.typeclasses.objects`
no longer registers a duplicate `Object` Django model. The
template-customizable `ObjectParent` mixin moved to
`evennia/game_template/typeclasses/object_parent.py`. Existing user game dirs
are unaffected — their `typeclasses/objects.py` keeps its own inline
`ObjectParent`. Tests against a `.test_game_dir/` adjacent to the engine
source now run without the `Conflicting 'object' models` error.

---

## Phase 1 — Engine hooks (kill monkey-patches)

**Required:** none. Phase 1 is purely additive.

**Auto:** none. Existing monkey-patches keep working unchanged.

**Optional cleanup:**

- Replace `world.profiling.patch_evennia_command` with a Django-signal
  receiver. See pattern below.
- Replace multipuppet's `patch_evennia_command` usage with a session-proxy
  object passed as `session=` to `cmdhandler.cmdhandler`.
- Drop `_patch_cmdhandler_error_capture` from
  `server/conf/at_server_startstop.py`; subscribe to `on_command_error`
  for per-command failures and `on_cmdset_merge_error` for cmdset
  build/merge failures.

### Signals (`evennia.commands.signals`)

Four `django.dispatch.Signal` instances, fired with `send_robust` (a bad
receiver returns the exception in the response list but cannot break
dispatch for other receivers or for the user). The per-command signals
carry `sender=type(cmd)` so receivers can filter by Command class;
`on_cmdset_merge_error` has no `cmd` and uses `sender=type(caller)`
instead.

| Signal | Fires | Kwargs |
|---|---|---|
| `on_command_pre` | After cmd's runtime attrs are set, before `at_pre_cmd()` | `cmd, caller, session, trace_id` |
| `on_command_post` | After `at_post_cmd()` (sync or generator path) | `cmd, caller, session, trace_id, elapsed_ms` |
| `on_command_error` | Inside `_run_command`'s `except Exception:` block when `func()` raised | `cmd, caller, session, trace_id, exc, traceback_text` |
| `on_cmdset_merge_error` | At the three merge-error sites in `get_and_merge_cmdsets` (`_get_local_obj_cmdsets`, `_get_cmdsets`, outer except) | `caller, session, raw_string, trace_id, exc, traceback_text` |

`trace_id` is the per-command id from
`evennia.utils.command_trace.get_trace_id()`. The same id is now also
attached to `cmdhandler.ErrorReported` instances as `.trace_id` so
external error handlers can correlate.

`on_command_pre` is observer-only. To abort a command before `func()`
runs, override `at_pre_cmd` (return truthy) — signals do not gate
dispatch.

Receivers must accept `**kwargs` so kwargs can be extended later without
breaking subscribers.

#### Profiling subscriber pattern

```python
# server/conf/at_server_startstop.py
from django.dispatch import receiver
from evennia.commands.signals import on_command_post

@receiver(on_command_post)
def _record_command_timing(sender, cmd, elapsed_ms, trace_id, **kwargs):
    if elapsed_ms > 50:
        logger.log_warn(
            f"[trace:{trace_id}] {cmd.key} took {elapsed_ms:.1f}ms"
        )
```

Wire it at `at_server_start()`; Django keeps a strong reference via the
`receiver` decorator. Use `dispatch_uid` if you need idempotent
re-registration across reloads.

#### Error-capture subscriber pattern

```python
from evennia.commands.signals import on_cmdset_merge_error, on_command_error

@receiver(on_command_error, dispatch_uid="capture-command-error")
def _capture_command_error(sender, cmd, exc, traceback_text, trace_id, **kwargs):
    sentry_or_whatever.report(
        cmd_key=getattr(cmd, "key", "?"),
        trace_id=trace_id,
        exc=exc,
        traceback_text=traceback_text,
    )

@receiver(on_cmdset_merge_error, dispatch_uid="capture-merge-error")
def _capture_merge_error(sender, caller, raw_string, exc, traceback_text, trace_id, **kwargs):
    sentry_or_whatever.report(
        cmd_key="<cmdset-merge>",
        raw_string=raw_string,
        trace_id=trace_id,
        exc=exc,
        traceback_text=traceback_text,
    )
```

Use `dispatch_uid` on receivers if you re-register at reload so duplicate
subscriptions don't accumulate.

### Session-proxy contract

`cmdhandler.cmdhandler(called_by, raw_string, session=...)` already
duck-types the `session` argument: it only requires
`get_cmdset_providers()` returning a `{cmdset_provider_type: provider}`
dict, with the same shape as `ServerSession.get_cmdset_providers()`.
Downstream callers may also read `puppet`, `account`, and `sessid` when
present.

This means the multipuppet relay can drop its `Command` monkey-patch and
instead build a small proxy object that aggregates the desired providers
and pass it as `session=` to `cmdhandler.cmdhandler`. No engine change
is needed beyond documentation (already in `cmdhandler.cmdhandler`'s
docstring) — Phase 1's contribution is to make that contract official
and tested.

**Signal payload contract.** Signal kwargs (`session=...`) and
`cmd.session` always carry the real `ServerSession` if any session is
involved, or `None`. To make this work, a session-proxy should expose
the underlying real session via a `real_session` attribute; the
cmdhandler resolves once at the top using:

```python
real = getattr(session, "real_session", session)
```

If a proxy does not set `real_session`, the proxy itself is what
receivers see — that's fine for fully synthetic sessions, but receivers
that filter by session identity will treat the proxy as opaque. Set
`real_session` whenever the proxy wraps a real session.

**Receiver-side guidance.** Because synthetic proxies can pass through,
receivers that read session-specific attributes (`sessid`,
`protocol_flags`, `address`, etc.) should either tolerate duck-typed
sessions or `isinstance(session, ServerSession)` before dereferencing.
For plain identity comparisons (`session is some_known_session`) no
check is needed.

The same resolution closes the previous footgun where
`callertype="session"` left `session=None` in signal kwargs even though
`called_by` *was* a session — that path now exposes the real session
too.

**Scope note for `on_command_error`:** the signal fires only at the
`_run_command` error site (where there is a real `cmd` instance to hand
to receivers). Cmdset build/merge failures fire `on_cmdset_merge_error`
instead — subscribe to both if the previous monkey-patched error capture
needed to see merge errors too. Both signals carry the same `trace_id`
shape, and `ErrorReported` exposes `.trace_id` on raises from either
path, so correlation across signals + logs works uniformly.

---

## Phase 2 — Account/Character split + hook reorder

### Step 1 (shipped in `6.0.0+underspire.2`): `at_pre_cmd` rename + dispatch reorder

- **Hook rename.** `Command.at_pre_cmd` (pre-parse early gate) is now
  `Command.at_pre_parse`. Same semantics: runs before `parse()`,
  return truthy to abort.
- **New dispatch order:**
  `at_pre_parse → parse → at_pre_cmd → func → at_post_cmd`. The new
  `at_pre_cmd` runs *after* parse.
- **Hard-error guard.** Subclasses defining `at_pre_cmd` raise
  `TypeError` at class-creation. The new post-parse `at_pre_cmd` is
  engine-only during the deprecation window; the guard is removed in
  `6.0.0+underspire.4` (shipped).

**Migration (required):**

1. If you see `TypeError: <Module>.<Class> defines at_pre_cmd, which
   was renamed to at_pre_parse...` at import time, rename the method
   body to `at_pre_parse`.
2. Update `super().at_pre_cmd()` calls to `super().at_pre_parse()`.
3. Per-subclass audit: an override that *reads* parsed args
   (`self.args`, `self.switches`, `self.character`) is a latent bug
   today because it ran before `parse`. Today the fix is to move that
   logic into `func` (or override `parse`). Once the post-parse
   `at_pre_cmd` becomes subclass-able, those overrides should target
   the new hook.

### Step 2 (shipped in `6.0.0+underspire.3`): `AccountCommand` + caller normalisation

- **New class** `evennia.commands.command.AccountCommand`. Sibling of
  `Command`. Detected by the cmdhandler via the class flag
  `account_command_caller = True` (no metaclass, no `isinstance`
  import cycle). `Command.account_command_caller` is `False`.
- **Cmdhandler normalisation** in `_run_command`, before the
  `_testing` early return and before `at_pre_parse`, so all hooks see
  the same shape:
  - `self.caller` = the Account, from
    `cmdset_providers["account"]` (falls back to
    `getattr(caller, "account", None)`).
  - `self.account` = same Account (alias).
  - `self.character` = the puppet for `self.session` (from
    `cmdset_providers["object"]`), or `None` if OOC.
  - `self.session` keeps its usual real-`ServerSession`-or-`None`
    value.
- If no Account is resolvable (defensive: ill-formed dispatch),
  `self.caller` is left untouched and `self.character` is set to
  `None` so the attribute always exists.
- **Trace + access cache unchanged on purpose.**
  `command_trace.begin_command_trace` keeps the pre-normalisation
  caller (the dispatch origin). `cmd_access_cache` keys on the
  pre-normalisation caller too, so the lock check still gates against
  the puppeted Character; normalisation only rewrites `cmd.caller`
  after the match.

**Migration (optional cleanup):**

- Downstream `AccountCommand` classes that previously did
  `_normalize_account_caller` (or equivalent) can drop that logic
  and subclass `evennia.commands.command.AccountCommand` directly.
- Game-side tests that instantiate `AccountCommand` subclasses via
  `BaseEvenniaCommandTest.call` automatically get the normalised
  shape; manual `cmdobj.caller = ...` assignments around such calls
  can be removed.

**Known gap during the deprecation window.** Two parallel flags
exist:

- `account_command_caller` (new, engine `Command`): drives pre-parse
  normalisation in `cmdhandler._run_command`. Set on
  `evennia.commands.command.AccountCommand`.
- `account_caller` (legacy, `MuxCommand`): drives parse-time
  normalisation inside `MuxCommand.parse`. Set on
  `MuxAccountCommand`.

Until the MuxCommand/MuxAccountCommand sweep ships, stock
`MuxAccountCommand` subclasses (`CmdIC`, `CmdOOC`, account-context
`CmdHelp`, etc.) are normalised via the legacy parse-time path and do
**not** carry `account_command_caller = True`. Downstream code that
wants a unified detection for "is this an account-context command"
should prefer the flag-based check **and** an attribute fallback:

```python
def _is_account_command(cmd):
    # account_command_caller is the engine's chosen detection mechanism.
    # account_caller is the MuxCommand legacy flag; check both during
    # the deprecation window.
    return (
        getattr(cmd, "account_command_caller", False)
        or getattr(cmd, "account_caller", False)
    )
```

A pure `isinstance(matched, AccountCommand)` check is **not** safe
yet: stock `MuxAccountCommand` does not inherit from engine
`AccountCommand`. Prefer the flag-based check above; it stays correct
across the sweep.

### Step 3 (shipped in `6.0.0+underspire.4`): drop the `at_pre_cmd` subclass guard

- The `__init_subclass__` hard-error guard from
  `+underspire.2` is removed. `at_pre_cmd` is now freely
  subclass-able and fires post-parse, pre-`func`.
- Override `at_pre_cmd` for input validation that needs parsed
  state (`self.args`, `self.switches`, `self.character`). Override
  `at_pre_parse` for raw-string checks or gating that runs before
  parse.
- Truthy return from `at_pre_cmd` aborts dispatch: `func` and
  `at_post_cmd` are skipped.

**Migration:** none required. Code that worked on `+underspire.3.1`
keeps working. Game-side post-parse logic currently jammed into
`func` (or a `parse` override) can now move into `at_pre_cmd` if
that reads better.

### Step 4 (shipped in `6.0.0+underspire.5`): switch parsing in `Command.parse`

- `Command.parse` now parses MuxCommand-style switches and
  `lhs`/`rhs` by default. Sets `self.switches`, `self.lhs`,
  `self.rhs`, `self.lhslist`, `self.rhslist`, `self.arglist`,
  `self.raw`. Honours `switch_options` and `rhs_split` class attrs
  (same semantics MuxCommand has always had).
- `MuxCommand.parse` reduced to `super().parse()` + the legacy
  `account_caller` normalisation block. Existing `MuxCommand`
  subclasses keep working unchanged.
- New class flag `Command.parse_mux_syntax = True` gates the
  behaviour. Set it `False` on subclasses that want
  `super().parse()` to be a no-op (matches the historical Command
  shape pre-`+underspire.5`).

**Migration (required only in one edge case):**

If you have `Command` subclasses that override `parse` and call
`super().parse()` expecting the historical no-op, `super` now
mutates `self.args` (strips, removes switches). Two fixes:

```python
# Option A: opt out, keep the no-op super
class MyCmd(Command):
    parse_mux_syntax = False
    def parse(self):
        super().parse()  # no-op shape, as before
        ...

# Option B: embrace the parse, drop your manual switch handling
class MyCmd(Command):
    def parse(self):
        super().parse()  # gives you self.switches / self.lhs / self.rhs
        ...
```

If your `Command` subclass overrides `parse` *without* calling
`super`, nothing changes.

**Migration (optional cleanup):**

- Game-side custom switch / lhs-rhs parsers can be deleted if they
  reproduced MuxCommand semantics. Use `Command.parse` directly.
- `MuxCommand` subclasses can be reclassified as plain `Command`
  subclasses if they only used MuxCommand for switch parsing (no
  reliance on `account_caller`). The `MuxCommand = Command`
  collapse coming in a later release will make this implicit; doing
  it now is purely a clarity move.

### Step 5 (shipped in `6.0.0+underspire.6`): Delete MuxCommand / MuxAccountCommand

`MuxCommand` and `MuxAccountCommand` are gone. `evennia/commands/default/muxcommand.py`
is deleted. Importing the classes raises `ImportError`; subclassing
is no longer possible. Engine sweep converted every internal
subclass to `Command` / `AccountCommand` in the same release.

**Engine sweep done in the same release:**

- `settings_default.COMMAND_DEFAULT_CLASS` switched from
  `evennia.commands.default.muxcommand.MuxCommand` to
  `evennia.commands.command.Command`. Stock default commands that
  use `COMMAND_DEFAULT_CLASS` now inherit `Command` (which carries
  switch parsing since `+underspire.5`).
- `evennia/commands/default/account.py`: every account command now
  subclasses `AccountCommand` directly. All `account_caller = True`
  attributes dropped (redundant — `AccountCommand` carries
  `account_command_caller = True`, and the engine pre-parse
  normalisation handles caller/account/character).
- `evennia/contrib/*`: every contrib MuxCommand / MuxAccountCommand
  subclass converted to `Command` / `AccountCommand` (both
  direct-import and `default_cmds.MuxCommand` patterns). The one
  `account_caller = True` contrib usage
  (`ingame_reports.CmdReport`) becomes `account_command_caller =
  True` so the engine flag handles normalisation.
- `evennia/commands/default/comms.py`: `CmdChannel` and `CmdPage`
  swapped `account_caller = True` for `account_command_caller =
  True`. `CmdObjectChannel` (character-context channel command)
  now overrides with `account_command_caller = False`.
- `evennia/__init__.py`: `default_cmds` API extended with
  `Command` and `AccountCommand` shortcuts. Contrib code can
  subclass `default_cmds.Command` / `default_cmds.AccountCommand`
  via the public path.
- `evennia/utils/test_resources.py`: test patches of
  `COMMAND_DEFAULT_CLASS` swapped from `MuxCommand` to `Command`.
- No engine code (core, default, contrib, tests) subclasses
  `MuxCommand` or `MuxAccountCommand` any more, except the
  `MuxAccountCommand` class definition itself (skipped via a
  qualname check in `MuxCommand.__init_subclass__`) and a handful
  of test fixtures that deliberately exercise the deprecated
  classes (each wrapped in `warnings.catch_warnings`).

**Migration (required) for downstream code:**

| You have | Switch to | Notes |
|---|---|---|
| `from evennia.commands.default.muxcommand import MuxCommand` | `from evennia.commands.command import Command` | Module is deleted; import raises ImportError. |
| `class X(MuxCommand)` (plain) | `class X(Command)` | Switch parsing now in `Command.parse`. |
| `class X(MuxCommand)` with `parse` override calling `super` expecting no-op | `class X(Command)` + `parse_mux_syntax = False` | See `+underspire.5` migration. |
| `class X(MuxCommand)` with `account_caller = True` (no engine flag) | `class X(AccountCommand)` | Legacy parse-time block is gone. Cleanest path: subclass `AccountCommand` (engine flag handles it). |
| `class X(MuxAccountCommand)` | `class X(AccountCommand)` | One-for-one swap. |
| `default_cmds.MuxCommand` / `default_cmds.MuxAccountCommand` | `default_cmds.Command` / `default_cmds.AccountCommand` | Added to the public API in this release. |

### Step 6 (shipped in `6.0.0+underspire.7`): ftfy normalisation in cmdhandler

`ftfy.fix_text` is now applied to every dispatched `raw_string` at
the top of `cmdhandler()`, before `generate_cmdset_providers`,
`_resolve_signal_session`, and the cmdset merge. Gated on
`INPUT_FTFY_NORMALIZE` (default `True`). `ftfy == 6.3.1` is a hard
dep on this fork.

**Migration (optional cleanup):**

- Game-side ftfy passes on player input can be dropped; the engine
  guarantees `cmd.raw_string` and signal payloads see repaired text.
- Set `INPUT_FTFY_NORMALIZE = False` to opt out of the per-dispatch
  cost (e.g. if your front-end already guarantees clean UTF-8).

Phase 2 is complete with this release. Phase 3 (token-boundary
matching, drop `CMD_IGNORE_PREFIXES`) is the next planned chunk.

### Phase 2 game-side cleanup

Once Phase 2 is shipped, the following game-side cleanups are
available:

- **Optional cleanup (planned):** delete game-side `AccountCommand`
  normalization (`_normalize_account_caller`, etc.) once the engine
  guarantees `self.caller`/`self.character` shape. (Available now
  for `evennia.commands.command.AccountCommand` subclasses; waiting
  on the MuxAccountCommand unification for `MuxAccountCommand`
  subclasses.)

- **Can drop now** (independently of any further engine step):
  the `Command.allow_multipuppet_relay = True` half of any game-side
  `patch_relay_default`. Downstream relay code already reads
  `getattr(matched, "allow_multipuppet_relay", True)`, so the
  explicit default on `Command` is redundant.

- **Wait-for-sweep:** the
  `MuxAccountCommand.allow_multipuppet_relay = False` half is still
  load-bearing for stock `MuxAccountCommand` subclasses until the
  MuxCommand/MuxAccountCommand sweep replaces the per-class default
  with a flag-based runtime check. Keep this half of
  `patch_relay_default` until then.

- **Relay-gate swap (wait-for-sweep):** replacing the runtime
  `getattr(matched, "allow_multipuppet_relay", True)` check with a
  pure `isinstance(matched, AccountCommand)` check regresses today
  because stock `MuxAccountCommand` subclasses do not inherit from
  engine `AccountCommand`. Two safe options until the sweep ships:

  1. **Recommended:** swap to the flag-based check
     `getattr(matched, "account_command_caller", False)` (covers
     engine `AccountCommand` today; covers `MuxAccountCommand` once
     the unification step lands).
  2. Keep `isinstance(matched, AccountCommand)` **or** the
     attribute fallback during the deprecation window.

- **Optional cleanup (planned):** sweep `MuxCommand` /
  `MuxAccountCommand` subclasses to silence the `DeprecationWarning`
  added in Phase 2. Schedule this with the rest of the Phase 2 cleanup,
  not after.

---

## Phase 3 — Token-boundary matching + drop prefix-strip

Shipped across `+underspire.8`. Couples the engine matching change with
the re-key sweep so the engine ships internally consistent.

### Recon (shipped on the same branch, pre-engine commit)

- `.agents/docs/code-style.md` gained the Command Naming section
  codifying the IC/OOC convention: no prefix for character actions,
  `@` for account/OOC actions, channel-chat carve-out.
- `PHASE3_AUDIT.md` is the frozen committed inventory of every default
  cmdset's keys/aliases. Regenerate with
  `.agents/tools/cmdset_prefix_audit.py --write`. CI runs the audit in
  `--check` mode via `TestCmdsetPrefixAuditDrift` in
  `evennia/commands/tests.py`.

### Engine match change (shipped in `6.0.0+underspire.8`)

- `Command._optimize` no longer builds `_noprefix_aliases`.
- `Command.match` is single-pass token-boundary matching. The
  `include_prefixes` kwarg is retained on the signature but ignored.
- `cmdparser.build_matches` no longer strips `CMD_IGNORE_PREFIXES`
  from `raw_string`. The `include_prefixes` kwarg is retained but
  ignored.
- `cmdparser.cmdparser` is single-pass — no more second-pass fallback
  with prefix-strip.
- `settings.CMD_IGNORE_PREFIXES` stays defined for one release with a
  startup warning if non-empty; removal slated for a follow-up.

### Engine re-key sweep (shipped in `6.0.0+underspire.8`)

Every staff/OOC engine default that was unprefixed now carries `@`.
See [`CHANGELOG-FORK.md`](CHANGELOG-FORK.md) for the full list and
[`PHASE3_AUDIT.md`](PHASE3_AUDIT.md) for the inventory diff
downstream consumers should reconcile against.

### Migration (required) for downstream code

- **`CMD_IGNORE_PREFIXES` is a no-op.** If your `server.conf.settings`
  still sets it, you'll see a startup warning. Removing the setting is
  safe.
- **Subclassed `Command.match` with custom prefix-strip logic** —
  delete the strip; matching now does the boundary check via
  `arg_regex` and treats the prefix character as part of the key.
- **`execute_cmd("<unprefixed staff command> ...")` calls** must
  update to the new keys (`@ban`, `@open`, etc.). Engine call sites
  were updated in this release; downstream must sweep their own.
- **`CmdAt*` wrapper classes** that only added `@`-prefix (no
  behavior changes) can be deleted in favor of the engine defaults.
  The `safe_remove(EngineCmd) + add(WrapperCmd())` pairs go with
  them.
- **Genuine custom overrides** — when sweeping
  `safe_remove`/`replace_command`, distinguish:
  - *Collision workarounds* (`safe_remove(EngineCmd) + GameCmd()`
    where `GameCmd` only differs by `@`-prefix): delete entirely.
  - *Genuine custom overrides* (`replace_command(EngineCmd, MyCmd())`
    where `MyCmd` adds real behavior — e.g. a `CmdGet` subclass with
    grapple-after-get logic): keep, but swap to the Phase 0 method
    form (`self.replace(MyCmd())`). Do not delete.

  Before deleting any `replace_command(EngineCmd, X())`, confirm `X`
  is `EngineCmd()` plus only an alias change. Any difference in
  `func`, `parse`, or hooks means it's a genuine override and must
  become `self.replace`, not a delete.

---

## Phase 4 — Trie parser into engine

Shipped in `+underspire.13` along with the Phase 3 leftovers
(`include_prefixes` kwarg removal, `CMD_IGNORE_PREFIXES` setting
deletion) and Levenshtein-based no-match suggestions.

### Engine changes (`+underspire.13`)

- **New module** `evennia/commands/cmdparser_trie.py` with public
  `cmdparser(raw_string, cmdset, caller, match_index=None,
  session=None, **kwargs)` and `trie_build_matches(raw_string,
  cmdset)`. Matches the `COMMAND_PARSER` contract and `build_matches`
  output shape respectively.
- **`COMMAND_PARSER` default flipped** to
  `evennia.commands.cmdparser_trie.cmdparser`. The linear
  `evennia.commands.cmdparser.cmdparser` is preserved as the opt-out
  path.
- New settings:
  - `COMMAND_PARSER_TRIE_FASTPATH` (default `True`) — single-candidate
    fast path inlining the `Command.match` boundary check when the
    class did not override `match` and is not an exit.
  - `COMMAND_PARSER_TRIE_ABBREV` (default `True`) — unambiguous
    first-token abbreviation expansion.
  - `COMMAND_FUZZY_SUGGESTIONS_ENABLED` (default `True`),
    `COMMAND_FUZZY_SUGGESTIONS_MAX_DIST` (default `2`),
    `COMMAND_FUZZY_SUGGESTIONS_LIMIT` (default `3`) — Levenshtein
    suggestions on the no-match fallback path.
- **`CMD_IGNORE_PREFIXES` deleted entirely.** The startup warning is
  gone; the setting itself is no longer defined in
  `settings_default.py`. Help-system convenience constants live inline
  as `_HELP_PREFIX_CHARS = "@&/+"` in
  `evennia/commands/command.py` and `evennia/commands/default/help.py`.
- **`include_prefixes` kwarg dropped** from `Command.match()` and
  `cmdparser.build_matches()`. Both were documented as no-ops since
  `+underspire.8`.

### Migration (required)

- **`settings.CMD_IGNORE_PREFIXES` references will `AttributeError`.**
  If your `server/conf/settings.py` (or any code) still reads it,
  delete the reference. It was a no-op since `+underspire.8`; nothing
  in the engine consumes it any more.
- **`Command.match` overrides with `include_prefixes` in the
  signature** will raise `TypeError` when the engine calls
  `cmd.match(search_string)` and the override expects two positional
  args. Drop the kwarg from the override signature.
- **Custom `COMMAND_PARSER`** settings continue to work unchanged; the
  new default only applies when the setting is unset.

### Migration (optional cleanup)

- Delete game-side trie parser copies (e.g.
  `world/parsing/trie_parser.py`) and the `COMMAND_PARSER` setting
  override that pointed at them.
- The engine's `fuzzy_command_suggestions` is now importable as
  `from evennia.commands.cmdparser_trie import fuzzy_command_suggestions`
  if a custom `CmdNoMatch` wants to surface the same hint.
- If a downstream `CmdNoMatch` override was previously surfacing
  difflib suggestions via `evennia.utils.utils.string_suggestions`, the
  helper is still available but new code can prefer the Levenshtein
  variant for consistency with the engine fallback.

### Performance

Synthetic 500-cmd cmdset, 2000 parses across 8 unique inputs:
- linear: ~72 µs/parse
- trie:   ~45 µs/parse  (**1.59× faster**)

Synthetic 20-cmd cmdset, same parses: ~3 µs absolute regression on
trie (~3.6 → ~6.8 µs/parse). Well below user-perceptible thresholds
and offset by the 500-cmd win for any nontrivial game cmdset.

### Trie cache invalidation contract

The merged cmdset carries `_trie_command_trie` +
`_trie_command_trie_cheap` + `_trie_command_trie_sig` after first
parse. Two-tier invalidation:

- **Cheap key** (length + id-sum of `cmdset.commands`) catches the
  common case of cmdset membership change without paying the O(n)
  signature cost.
- **Full sig** (per-cmd key/aliases/`is_exit` tuple) is recomputed
  only when the cheap key drifts; this catches in-place mutation of
  an existing cmd's key/aliases (where the cmd's identity is unchanged
  but its match keys are not).

Edge case: if you mutate a cached cmd's `aliases` in place via
`set_aliases` (or `set_key`) while reusing the same merged cmdset
object, the cheap-key check will skip the sig recompute and the trie
will stay stale. Production code rebuilds the containing cmdset on
any cmd change, so this is exotic; if you hit it, clear
`cmdset._trie_command_trie` to force a rebuild.
