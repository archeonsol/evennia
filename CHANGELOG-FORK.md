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
