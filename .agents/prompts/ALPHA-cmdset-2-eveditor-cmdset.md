# ALPHA cmdset retirement — chunk 2: migrate EvEditor off CmdSet/cmdparser

Status: todo. Independent (startable now). Second of six chunks; see
[`ALPHA-cmdset-retirement-audit.md`](ALPHA-cmdset-retirement-audit.md). **Gates
chunk 4** (which deletes `cmdparser.py`).

## Why

`EvEditorState` (`evennia/utils/eveditor.py`) is already an engine
`StateProvider` since `.82` — input capture is engine-routed, no cmdset is
merged into dispatch. But the editor's `:`-command matching still runs on the
legacy machinery:

- `from evennia import CmdSet` (`:48`) + `EvEditorCmdSet` (`:857`) as a
  **match-only** instance (`self._cmdset`, `:944`).
- `cmdparser.build_matches(raw, self._cmdset)` (`:1027`) — the linear
  parser's matcher, the *only* live caller of `cmdparser.py`.
- `CmdEditorBase(_COMMAND_DEFAULT_CLASS)` (`:237`) — the `:`-commands are
  `Command` subclasses.
- `cmdhandler.CMD_NOMATCH` reused as the buffer-insert command key (`:435`).

So EvEditor transitively pins `CmdSet`, `cmdparser`, `Command`, and a `CMD_*`
constant — all targeted for deletion. `cmdparser.py` cannot go (chunk 4) until
this is rehomed.

## Goal

Replace the `EvEditorCmdSet` + `cmdparser.build_matches` + `Command`-subclass
machinery with a self-contained `:`-command dispatch inside `eveditor.py`: a
small registry/dict of editor commands keyed by their `:`-token, with the same
prefix-match behavior `build_matches` provided, and a fall-through to
buffer-insert (the former `CMD_NOMATCH` path). The capture path (`EvEditorState`)
already works and does not change.

After this, `eveditor.py` imports no `CmdSet`, no `cmdparser`, no
`COMMAND_DEFAULT_CLASS`, and no `CMD_*` constant.

## Scope boundary

- **In scope:** `eveditor.py`'s internal `:`-command matching/dispatch.
- **Out of scope:** the capture path (already migrated), and deleting
  `cmdparser.py`/`CmdSet`/`Command` (chunks 4/6).
- Preserve every `:`-command and its prefix/abbreviation behavior; the editor's
  user-facing command surface must not change.

## Done means

`grep -n "CmdSet\|cmdparser\|COMMAND_DEFAULT_CLASS\|CMD_NOMATCH" evennia/utils/eveditor.py`
is empty; `cmdparser.build_matches` has zero callers outside the (now-deletable)
machinery; the EvEditor engine-path test
(`TestEvEditorEngineRouted.test_line_captured_through_real_cmdhandler_bridge`)
and the `:`-command tests stay green (TDD: add a test asserting a `:`-command and
its prefix both resolve through the new matcher before deleting the old one).
