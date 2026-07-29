# ALPHA cmdset retirement — chunk 3: delete the commands/default/ tree

Status: todo. **Gated on chunk 1** (help formatters extracted) and the native
default-action prerequisite ports below. Third of six
chunks; see [`ALPHA-cmdset-retirement-audit.md`](ALPHA-cmdset-retirement-audit.md).

## Why

`evennia/commands/default/` (~14.2k lines: `account.py`, `admin.py`,
`building.py` at 4.6k, `comms.py`, `general.py`, `help.py`, `system.py`,
`syscommands.py`, `unloggedin.py`, + the `cmdset_*.py` anchors) is the stock MUX
command suite. It is not safe to delete merely because `cmdhandler` tries
`try_action_dispatch` first: the old claim that every default verb was already
native was false. Login is engine-owned (`.95`), but deletion must wait until
the useful generic defaults have native action/rule implementations and their
downstream providers are installed. Nothing relocates during chunk 3 itself;
the prerequisite ports happen before it.

The `.179` prerequisite port added canonical `say`/`whisper` roleplay,
`@set`, `@alias`, `@copy`, `@cpattr`, `@link`, `@unlink`, `@sethome`, `@wipe`,
cmdset-free `@examine`, `@objects`, and storage-only `@scripts` under
`evennia/actions/default/`. `@cmdsets` is intentionally **not** ported: it
inspects the substrate this six-chunk sequence retires. Do not recreate it as
an action.

The single live carve-out (`help.py`'s formatters) is removed by **chunk 1**;
confirm that landed and that downstream games have installed the `.179`
roleplay/building/system providers before starting.

## Goal

Delete the `commands/default/` tree and its flat-API / settings advertisements:

- The `commands/default/*.py` modules (after chunk 1 emptied `help.py` of live
  consumers) and `commands/default/tests.py`.
- `evennia/__init__.py`: the `default_cmds` container (`:333-374`) and its four
  `cmdset_*` imports; **also** the `CmdSet` flat export at `:74` is deferred to
  chunk 6 (still has consumers). The `building` module is double-registered in
  the container (`:369` and `:371`) — both go.
- `settings_default.py`: `CMDSET_CHARACTER/ACCOUNT/SESSION/UNLOGGEDIN` default
  values (`:669-672`) point at the deleted modules. Decide: repoint to an empty
  built-in anchor, or remove the settings if the session no longer reads them
  (coordinate with chunk 6, which removes the `.cmdset` handler that consumes
  them). Until chunk 6, the session still calls `self.cmdset.update()`, so these
  must resolve to *something* — point them at an empty `CmdSet` anchor for now.

## Scope boundary

- **In scope:** the `default/` tree, the `default_cmds` container, the
  `CMDSET_*` default repoint/removal.
- **Out of scope:** `CmdSet`/`Command`/`cmdsethandler`/the `.cmdset` handler
  (chunks 4/6); contrib (chunk 5).
- **Do not** break the still-live `self.cmdset.update()` call in
  `serversession.py:160` — leave it pointing at a valid (empty) cmdset until
  chunk 6 removes the handler.

## Done means

`evennia/commands/default/` is gone; `evennia.default_cmds` no longer exists;
`grep -rn "commands.default" evennia --include="*.py"` (excl. contrib) returns
nothing live; the server boots and a session connects (the empty-anchor
`CMDSET_*` resolve); full suite green excluding the contrib modules tracked in
chunk 5.
