# Command System Contracts

Runtime contracts for the cmdset / command subsystem. Per-release
history is in [`CHANGELOG-FORK.md`](../../CHANGELOG-FORK.md).

## Hook dispatch order

```
at_pre_parse → parse → at_pre_cmd → func → at_post_cmd
```

- `at_pre_parse` — pre-parse gate, truthy to abort. Cannot read parsed args.
- `at_pre_cmd` — post-parse gate, truthy to abort. Args-aware preconditions go here.
- `at_post_cmd` — runs after `func`, sync or generator path.

The old pre-parse `at_pre_cmd` was renamed to `at_pre_parse`.

## AccountCommand

`evennia.commands.command.AccountCommand`. Detected via class flag
`account_command_caller = True`. Before any hook runs, `_run_command`
normalises:

| Attr | Value | Source |
|---|---|---|
| `self.caller` / `self.account` | the Account | `cmdset_providers["account"]` → `getattr(caller, "account", None)` |
| `self.character` | puppet for `self.session`, or `None` if OOC | `cmdset_providers["object"]` |
| `self.session` | real `ServerSession` or `None` | unchanged |

If no Account resolves, `self.caller` is untouched; `self.character`
is always set (possibly `None`). Trace and access cache key on the
pre-normalisation caller, so locks gate against the puppeted Character.

## Signals (`evennia.commands.signals`)

Four `django.dispatch.Signal`s, fired with `send_robust`. Per-command
signals use `sender=type(cmd)`; `on_cmdset_merge_error` uses
`sender=type(caller)`.

| Signal | Fires | Kwargs |
|---|---|---|
| `on_command_pre` | After cmd runtime attrs set, before `at_pre_cmd()` | `cmd, caller, session, trace_id` |
| `on_command_post` | After `at_post_cmd()` | `cmd, caller, session, trace_id, elapsed_ms` |
| `on_command_error` | `_run_command`'s `except Exception:` when `func()` raised | `cmd, caller, session, trace_id, exc, traceback_text` |
| `on_cmdset_merge_error` | Merge-error sites in `get_and_merge_cmdsets` | `caller, session, raw_string, trace_id, exc, traceback_text` |

- `trace_id` from `evennia.utils.command_trace.get_trace_id()`; also
  attached to `cmdhandler.ErrorReported` as `.trace_id`.
- `on_command_pre` is observer-only; to abort, override `at_pre_cmd`.
- Receivers must accept `**kwargs`. Use `dispatch_uid` for reload-safe re-registration.
- `on_command_error` requires a real `cmd`; merge/build failures fire
  `on_cmdset_merge_error` instead. Subscribe to both for full coverage.

## Session-proxy contract

`cmdhandler.cmdhandler(called_by, raw_string, session=...)` duck-types
`session`: requires `get_cmdset_providers()` returning a
`{cmdset_provider_type: provider}` dict (same shape as
`ServerSession.get_cmdset_providers()`). `puppet`, `account`, `sessid`
may also be read when present.

Signal kwargs (`session=...`) and `cmd.session` always carry the real
`ServerSession` if any session is involved, else `None`. A proxy that
wraps a real session exposes it via `real_session`; cmdhandler resolves
with `getattr(session, "real_session", session)`. Without it, the proxy
itself reaches receivers (fine for synthetic sessions). Receivers that
read session-specific attrs should tolerate duck-typed sessions.

## Command matching

Token-boundary, single pass. `@` prefix is load-bearing: `@open` and
`open` are distinct. No global prefix strip; `CMD_IGNORE_PREFIXES`
removed in `+underspire.13`. `arg_regex` remains the per-command
escape hatch. See [code-style.md](code-style.md) "Command Naming".

## Trie parser (`evennia.commands.cmdparser_trie`)

Default `COMMAND_PARSER`. Public surface:

- `cmdparser(raw_string, cmdset, caller, match_index=None, session=None, **kwargs)`
- `trie_build_matches(raw_string, cmdset)` — matches `build_matches` shape.
- `fuzzy_command_suggestions(raw_string, cmdset)` — Levenshtein suggestions.

The linear parser in `evennia.commands.cmdparser` is the opt-out;
point `COMMAND_PARSER` at its `cmdparser` function.

Settings: `COMMAND_PARSER_TRIE_FASTPATH` (default `True`),
`COMMAND_PARSER_TRIE_ABBREV` (default `True`),
`COMMAND_FUZZY_SUGGESTIONS_ENABLED` (default `True`),
`COMMAND_FUZZY_SUGGESTIONS_MAX_DIST` (default `2`),
`COMMAND_FUZZY_SUGGESTIONS_LIMIT` (default `3`).

The cmdhandler's built-in "Maybe you meant ...?" fallback is **skipped**
when a custom `CMD_NOMATCH` is registered. To restore inside the
override, call `fuzzy_command_suggestions(self.raw_string, self.cmdset)`
after any leading-punctuation interception (pose/emote/channel chat) so
deliberate emote input doesn't trigger suggestions.

### Trie cache invalidation

Merged cmdset carries `_trie_command_trie` + `_trie_command_trie_cheap`
+ `_trie_command_trie_sig` after first parse. Two-tier invalidation:

- **Cheap key** (length + id-sum of `cmdset.commands`) catches cmdset
  membership change without the O(n) signature cost.
- **Full sig** (per-cmd `key`/`aliases`/`is_exit` tuple) recomputes only
  when the cheap key drifts; catches in-place mutation of an existing
  cmd's match keys. In-place `set_aliases`/`set_key` on a cached cmd
  while reusing the same merged cmdset object will stay stale — clear
  `cmdset._trie_command_trie` if hit.

## CmdSet API

- `remove(cmd_or_key, strict=False)` — idempotent by default, returns
  `bool`. `strict=True` for old raise-on-missing.
- `replace(old, new)` — idempotent remove + add. Returns `True` if
  `old` was found.
- `has(cmd_or_key) -> bool`.
- `get(key) -> Command | None` — introspection.
