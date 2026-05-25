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
