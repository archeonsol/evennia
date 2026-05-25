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
  `6.0.0+underspire.4` (target).

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

### Remaining Phase 2 steps (planned)

These ship under later `+underspire.N` versions.

- `AccountCommand` engine class + cmdhandler caller-normalization.
- `MuxCommand` / `MuxAccountCommand` deprecation aliases.
- `ftfy.fix_text` into cmdhandler (gated on `INPUT_FTFY_NORMALIZE`).
- Switch parsing into `Command.parse`.
- **Hard-error guard removal** (target `+underspire.4`): the
  post-parse `at_pre_cmd` becomes available for subclassing.

Game-side cleanup that lands once those steps are in:

- **Optional cleanup (planned):** delete game-side `AccountCommand`
  normalization (`_normalize_account_caller`, etc.) once the engine
  guarantees `self.caller`/`self.character` shape.
- **Optional cleanup (planned):** delete any game-side
  `patch_relay_default` that sets `allow_multipuppet_relay` defaults on
  `Command` / `MuxAccountCommand`. Replace the runtime check in the
  multipuppet relay with `isinstance(matched, AccountCommand)`. The
  `Command.allow_multipuppet_relay = True` half is already redundant
  today and can be removed independently.
- **Optional cleanup (planned):** sweep `MuxCommand` /
  `MuxAccountCommand` subclasses to silence the `DeprecationWarning`
  added in Phase 2. Schedule this with the rest of the Phase 2 cleanup,
  not after.

---

## Phase 3 — Token-boundary matching + drop prefix-strip

_Pending. Anticipated migration:_

- **Auto (planned):** `CMD_IGNORE_PREFIXES` honored for one release with a
  startup `DeprecationWarning` listing affected commands.
- **Required (planned):** cmdsets that worked around the `@cmd`/`cmd` alias
  collision (e.g. `safe_remove(CmdOpen)` + `CmdAtOpen()`) drop the
  workaround. Engine default builder commands are keyed `@open`/`@dig`/etc.
  directly. The complete re-key list will ship with this release; diff it
  against your own `safe_remove` / wrapper list before merging.
- **Distinguish two kinds of call site** when sweeping
  `safe_remove`/`replace_command`:
  - *Collision workarounds* (`safe_remove(EngineCmd) + GameCmd()` where
    `GameCmd` only differs by `@`-prefixing): delete entirely.
  - *Genuine custom overrides* (`replace_command(EngineCmd, MyCmd())`
    where `MyCmd` adds real behavior — e.g. a `CmdGet` subclass with
    grapple-after-get logic): keep, but swap to the Phase 0 method form
    (`self.replace(MyCmd())`). Do not delete.

  Before deleting any `replace_command(EngineCmd, X())`, confirm `X` is
  `EngineCmd()` plus only an alias change. Any difference in `func`,
  `parse`, or hooks means it's a genuine override and must become
  `self.replace`, not a delete.

---

## Phase 4 — Trie parser into engine

_Pending. Anticipated migration:_

- **Auto (planned):** `COMMAND_PARSER` default flips to the trie parser.
  Opt-out via setting to `evennia.commands.cmdparser_linear.cmdparser` if
  needed.
- **Optional cleanup (planned):** delete any game-side trie parser copy.
