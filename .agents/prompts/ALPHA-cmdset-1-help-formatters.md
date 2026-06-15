# ALPHA cmdset retirement — chunk 1: extract live help formatters

Status: todo. Independent (startable now). First of six chunks; see
[`ALPHA-cmdset-retirement-audit.md`](ALPHA-cmdset-retirement-audit.md) for the
sequence. **Unblocks chunk 3** (the `commands/default/` tree cannot be deleted
until its one live carve-out is rehomed).

## Why

`commands/default/help.py` is the only *live* module in the otherwise-dead
`commands/default/` tree. Its formatter surface is consumed by **engine code
that has already replaced the rest of the tree**:

- `evennia/help/renderer.py:52-53` imports `CmdHelp`, `HelpCategory` (and builds
  a throwaway `CmdSet()` to satisfy `CmdHelp.cmdset`).
- `evennia/actions/default/general.py:309` (`Help` action) instantiates
  `CmdHelp()` to reuse its formatters; `:355` imports `HelpCategory`,
  `_loadhelp`, `_quithelp`, `_savehelp` (the EvEditor callbacks the `SetHelp`
  action reuses for `@sethelp/edit`).

This is a layering inversion: `actions/` (the replacement) reaches back into
`commands/default/` (the thing being deleted). The formatters are generic help
rendering, not command-dispatch machinery, so they belong under `evennia/help/`.

## Goal

Move the live help-formatting surface out of `commands/default/help.py` into
`evennia/help/` (a new `help/formatters.py` is the natural home; or fold into
`help/renderer.py` if cleaner). Repoint both consumers. After this lands,
nothing outside `commands/default/` imports from `commands/default/help.py`, and
the help render path no longer constructs a `CmdSet`.

Live symbols to rehome (confirm the full set by grep before moving):
`CmdHelp`'s formatter methods (`format_help_index`, `format_help_entry`,
`do_search`, `collect_topics`, `clickable_topics`, `subtopic_separator_char`),
`HelpCategory`, and the `_loadhelp` / `_quithelp` / `_savehelp` EvEditor
callbacks. Decide whether the formatters stay as methods on a lightweight class
or become plain functions (the `CmdSet()` dependency at `renderer.py:69` exists
only to populate `CmdHelp.cmdset`; dropping the `Command` shape removes it).

## Scope boundary

- **In scope:** the extraction, repointing `help/renderer.py` and
  `actions/default/general.py`, removing the `CmdSet()` construction in the help
  path.
- **Out of scope:** deleting `commands/default/help.py` itself (that is chunk 3,
  once nothing imports it) and any other `commands/default/` module.
- Do **not** retire `Command`/`CmdSet` here; they have many other consumers
  (chunks 5-6).

## Done means

`grep -rn "from evennia.commands.default.help" evennia` returns only
`commands/default/` internal references (and tests, which move with the code);
the help render path builds no `CmdSet`; `actions/default/general.py` imports its
formatters/callbacks from `evennia/help/`; all help + `@sethelp` tests green
(TDD: the `help/renderer` and `test_default_general` help suites must stay green
through the move).
