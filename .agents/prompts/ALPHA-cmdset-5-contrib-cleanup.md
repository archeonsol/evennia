# ALPHA cmdset retirement — chunk 5: contrib cmdset cleanup

Status: todo. Independent of chunks 1-4 (can run in parallel). **Gates chunk 6**
(the final substrate cannot be deleted while contrib imports `CmdSet`/`Command`/
the `.cmdset` handler). Fifth of six chunks; see
[`ALPHA-cmdset-retirement-audit.md`](ALPHA-cmdset-retirement-audit.md).

## Why

~16 contrib modules still import `CmdSet`/`Command` and several call
`obj.cmdset.add(...)` at runtime. They are **already non-functional on the
dispatch path** (the engine bridge bypasses every merged cmdset since `.73`) —
zombies that still import. They are the sole external surface keeping
`CmdSet`/`Command`/`cmdsethandler` alive. Contrib is the last consumer tier.

`CmdSet`/`Command` consumers in contrib (re-grep for the live set; this is the
2026-06 snapshot): `base_systems/building_menu`, `base_systems/email_login`,
`base_systems/mux_comms_cmds`, `base_systems/unixcommand`,
`game_systems/achievements`, `game_systems/clothing`, `game_systems/containers`,
`game_systems/crafting`, `game_systems/gendersub`, `game_systems/storage`,
`grid/ingame_map_display`, `grid/mapbuilder`, `grid/simpledoor`,
`grid/slow_exit`, `rpg/dice`, `rpg/llm`,
`tutorials/red_button`, `utils/debugpy`, `utils/git_integration`.

## Goal

Triage every cmdset-dependent contrib so that, when chunk 6 deletes the
substrate, nothing in `contrib/` imports it. Per-module, one of:

1. **Delete** — the contrib is command-only and its verbs are stock/dead with no
   engine-action equivalent worth preserving (precedent: `.85` deleted 9
   EvMenu-dependent contribs outright).
2. **Strip the command layer** — the contrib has real non-command value (e.g.
   `clothing`'s wear model, `crafting`'s recipes, `xyzgrid`'s map) — keep the
   data/logic, remove the `CmdSet`/`Command` surface, and (if the verbs still
   matter) re-expose them as engine actions in the contrib.

This chunk is **workflow-shaped**: one agent per contrib for the triage verdict
(delete vs strip vs port), then the edits in isolated worktrees. Decide the
overall policy (how aggressively to delete vs preserve) with the user first; it
intersects [`F13-contribs-policy.md`](F13-contribs-policy.md).

## Scope boundary

- **In scope:** removing every `CmdSet`/`Command`/`.cmdset.add` reference from
  `contrib/`.
- **Out of scope:** the engine substrate deletion itself (chunk 6); the
  broader contrib half-policy beyond what unblocks chunk 6.

## Done means

`grep -rln "CmdSet\|MuxCommand\|(Command)\|\.cmdset\.add" evennia/contrib` is
empty (or only matches re-exposed engine actions); each surviving contrib's
tests green; deleted contribs' docs + cross-links removed (the `.85` pattern:
code, docs, and inbound links go together).
