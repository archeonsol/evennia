# F18: unused engine handlers

Status: todo

## Goal

Two handlers (`MONITOR_HANDLER`, `ON_DEMAND_HANDLER`) persist and
restore empty state every reload. MSDP inputfuncs load but no
Underspire client speaks MSDP.

Decide for each: **settings-gate** (load only when explicitly
enabled) or **document** (kept for future webclient/MSDP work,
explain why the silent persistence is fine).

## Approach

**Start with discussion.** Three handlers, two paths each = small
decision space, but the right answer for each may differ:

- `MONITOR_HANDLER`: who uses it? Is the empty-state persistence
  cheap enough to ignore?
- `ON_DEMAND_HANDLER`: same questions.
- MSDP inputfuncs: does any planned client (webclient roadmap)
  need them? Are they cheap to load?

1. Read each handler's source and trace callers. Confirm "no
   current consumer" claim from the backlog entry.
2. Estimate cost per reload of the persist/restore cycle. If
   <1ms total across all three, "document" is the right
   answer.
3. For MSDP specifically: check the webclient roadmap. If MSDP
   is intended future work, document. If it's not, settings-gate.
4. **Present findings and recommendation to user**; get
   decision before settings-gating anything.

## Scope boundary

- **In scope**: the three handlers/inputfuncs named.
- **Out of scope**: broader handler-loading refactor; new
  protocol support; webclient overhaul.

## Existing code to study

- `evennia/scripts/monitorhandler.py` (or similar).
- `evennia/scripts/ondemandhandler.py` (or similar).
- `evennia/server/inputfuncs.py` and MSDP inputfuncs location.
- Server start/stop persistence path that calls `save()` / `load()`.

## Done means

- For each of the three: gated or documented. Explicit decision
  in commit message.
- If gated: setting defaults preserve current behavior; flip
  documented in release notes.
- If documented: a short comment at the handler entry point
  explaining why it loads despite zero current consumers.

## Repo conventions

See [AGENTS.md](../../AGENTS.md).

## Ask before

- Settings-gating without measuring cost (might be a
  premature optimization).
- Removing any of them outright (not on the table here; that's
  a bigger conversation).
- Touching the broader handler-registry shape.
