# ALPHA cmdset retirement — chunk 6: delete the substrate (the finish line)

Status: todo. **Gated on chunks 4 and 5** (no engine or contrib consumer left).
Sixth and final chunk; see
[`ALPHA-cmdset-retirement-audit.md`](ALPHA-cmdset-retirement-audit.md).

## Why

Once the default tree (3), dispatch machinery (4), and contrib (5) are gone,
`CmdSet`, `cmdsethandler`, the per-typeclass `.cmdset` handler, and the
`db_cmdset_storage` column have **zero consumers**. This is the deletion the
whole track exists for.

## Goal

Delete the cmdset substrate and its persistence:

- `evennia/commands/cmdset.py` (`CmdSet`) and `evennia/commands/cmdsethandler.py`
  (`CmdSetHandler`).
- The `.cmdset` handler attribute and its plumbing:
  `objects/object.py:452-453`, `accounts/accounts.py:373`,
  `server/serversession.py:68/160/221`, the `get_cmdset_providers`-style
  accessors (`objects/mixins/lifecycle.py:533`, `serversession.py:548`,
  `accounts.py:2196`), and `typeclasses/models.py:686` (`self.cmdset.clear()`).
- `evennia/commands/signals.py` cmdset-merge signals if they have no remaining
  producer (`on_cmdset_merge_error`, etc.).
- The `CmdSet` flat export in `evennia/__init__.py:74` and any residual
  `CMDSET_*` settings left after chunk 3.

**Fork A — drop the `db_cmdset_storage` column (DECIDED: yes).** Add a migration
on `ObjectDB` and `AccountDB` dropping `db_cmdset_storage`
(`objects/models.py:277`, `accounts/models.py:98`), remove the
`cmdset_storage` property accessors on both models + `serversession.py:91-97`,
remove the web-admin form fields (`web/admin/accounts.py`,
`web/admin/objects.py`), and drop the `db_cmdset_storage` rename-handling in
`server/service.py:379-384`. **Coordinate with
[`ALPHA-migration-squash.md`](ALPHA-migration-squash.md)**: this is a schema
change on two apps, so either land its migration before the squash or fold the
column-drop into the squash. Flag to the user which.

**`Command` (last).** `Command` (`commands/command.py`) is still named by
`COMMAND_DEFAULT_CLASS` (`settings_default.py`) and may have residual references.
After chunk 5, confirm whether anything still subclasses it (engine + contrib).
If truly dead, delete it and the setting; if a thin base is still wanted for the
arg-parsing idiom `actions/muxargs.py` mirrors, decide explicitly. This is the
true end of the track — treat `Command` removal as its own verification gate.

## Scope boundary

- **In scope:** `CmdSet`, `cmdsethandler`, the `.cmdset` handler attr, the
  `db_cmdset_storage` column + accessors + admin fields, the flat-API export,
  `Command` (if dead).
- **Ask before:** running the schema migration (irreversible column drop) —
  confirm the squash-coordination decision first.

## Done means

`grep -rn "CmdSet\|cmdsethandler\|\.cmdset\b\|db_cmdset_storage" evennia
--include="*.py"` (excl. migrations) is empty; `evennia.CmdSet` no longer
imports; the `db_cmdset_storage` column is dropped (or folded into the squash);
the server boots, sessions connect, all input dispatches through the engine; full
suite green. The cmdset machinery is gone.
