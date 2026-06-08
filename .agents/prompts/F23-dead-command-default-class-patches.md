# F23: dead `COMMAND_DEFAULT_CLASS` test patches

Status: todo (discussion-first)

## Goal

`BaseEvenniaCommandTest` (`evennia/utils/test_resources.py`) carries nine
stacked `@patch("evennia.commands.<name>.COMMAND_DEFAULT_CLASS", Command)`
decorators (account, admin, building, comms, general, help, syscommands,
system, unloggedin). They are **doubly dead** and were left as path-strings by
the F17 sweep (they cannot be rewritten to `patch.object` because the target
module does not exist). Decide fix-vs-delete.

Two independent reasons nothing has caught this:

1. **Stale path.** `evennia.commands.account` does not exist; the real module
   is `evennia.commands.default.account`. All nine paths are wrong the same way.
   (`COMMAND_DEFAULT_CLASS` *does* exist in each `evennia.commands.default.*`
   module, so the corrected path would resolve.)
2. **Dormant decorator.** The decorators sit on `BaseEvenniaCommandTest`, which
   has no `test_*` methods of its own and none inherited. `mock.patch` as a
   class decorator only wraps test methods present on the decorated class at
   decoration time, so it wraps nothing and the patches never start. Even with a
   corrected path they would not engage on subclass test methods.

Because they never start, the stale path never raised — the command suite is
green at HEAD. If they were ever made live, every command test would run with
`COMMAND_DEFAULT_CLASS` forced to the bare `Command` base in all nine default
modules.

## The decision to make

Pick one, deliberately:

1. **Delete.** They have never done anything; remove the nine decorators (and
   the explanatory NOTE). Simplest, zero behavior change.
2. **Fix + activate.** Correct the paths to `evennia.commands.default.<name>`
   and move the decorators onto a class/mixin that actually has the command test
   methods (or apply per-method), so the override engages. This is a real
   behavior change: run the full command suite and triage whatever breaks or
   changes. Only worth it if the override was protecting against something
   (e.g. a game-overridden `COMMAND_DEFAULT_CLASS` leaking into engine command
   tests).

Recommendation leans (1) unless someone can name what the override was meant to
guarantee. Surface the call before acting.

## How to re-confirm

A resolver that flags any string-literal `patch(...)` whose path does not
resolve under a configured Django environment found exactly these nine (the
other surviving string-patches are correct: builtins like
`evennia_launcher.print`, the `evennia.SESSION_HANDLER` runtime singleton,
stdlib `time`/`os`, Django `.objects` managers, and twisted `amp.AMP`). Re-run
such a scan with `django.setup()` first; without it, app-module imports and the
`SESSION_HANDLER` singleton produce false positives.

## Scope boundary

- **In scope:** the nine `COMMAND_DEFAULT_CLASS` decorators on
  `BaseEvenniaCommandTest`.
- **Out of scope:** the rest of the F17 leave-as-is set (all correct); any
  broader command-test-base refactor.

## Existing code to study

- `evennia/utils/test_resources.py` — the decorators + the NOTE comment.
- `evennia/commands/default/*.py` — where `COMMAND_DEFAULT_CLASS` actually lives.
- `evennia/commands/default/tests.py` — the subclasses that hold the real
  command test methods.

## Done means

- A decision: the nine decorators are deleted, or fixed-and-activated with the
  command suite re-triaged green.
- The NOTE in `test_resources.py` is removed (decision made) either way.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).
