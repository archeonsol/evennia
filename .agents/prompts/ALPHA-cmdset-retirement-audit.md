# ALPHA: audit cmdset removal + retire cmdset machinery

Status: todo (unblocked — all gates cleared)

The CM1 finish line, now ready to start. Its gates are all done:
[`CM1-action-system-roadmap.md`](CM1-action-system-roadmap.md) (Phase 1-8
shipped), [`CM1-input-capture-migration.md`](CM1-input-capture-migration.md)
(EvMore/EvEditor StateProvider migration shipped), EvMenu removal landed in
`.85`, and login is engine-owned as of `6.0.0+underspire.95`. This prompt
**audits** that every cmdset-dependent subsystem is actually gone, then removes
the cmdset machinery itself.

## Goal

Verify there are no remaining consumers of the legacy cmdset dispatch/capture
machinery, then delete it entirely: `CmdSet`, `cmdsethandler`, the legacy
merge/match block in `cmdhandler`, `cmdparser.py`, `syscommands.py`, the
`CMD_*` constants, the `cmdset_*.py` anchors, and the `CmdSet` export from
`evennia/__init__.py`. The action engine has been the sole player-input
dispatcher since `.73`; this removes the dead substrate it replaced.

## Audit checklist (each must be true before any deletion)

1. **EvMenu removal landed (`.85`).** Confirm: `EvMenuCmdSet` is
   gone from [`evennia/utils/evmenu.py`](../../evennia/utils/evmenu.py) (was at
   `:450`, `:693`, `:1003`) and EvMenu either routes through an engine
   `StateProvider` or is gone. The orphaned `EvMenuState` scaffold
   ([`evennia/actions/menus.py:283`](../../evennia/actions/menus.py)) is either
   wired as EvMenu's substrate or deleted (its docstring promises a "thin
   wrapper" on EvMenu that was never written). The `TestEvMenuState` suite must
   now drive input **through `cmdhandler`**, not via direct `enter_state` (the
   current tests are green-but-bypassed: green ≠ working).
2. **EvMore / EvEditor migrated** per `CM1-input-capture-migration.md`. Confirm
   `CmdSetMore`, the EvEditor cmdset, and `CmdSaveYesNo` are deleted and capture
   is engine-routed (proven by a `cmdhandler`-driven test).
3. **No remaining `CMD_NOMATCH` / `CMD_NOINPUT` / `cmdset.add` capture** anywhere
   in the engine (grep; contrib excluded). The bridge never merges these, so any
   survivor is silently broken, not working.
4. **`cmdobj=` injection callers rehomed.** The legacy block in `cmdhandler` is
   reachable only via `cmdobj=`. Login **landed engine-owned in
   `6.0.0+underspire.95`** (prompt removed; see git log):
   `connect`/`create` resolve through `SessionLoginRules`, not `cmdobj=`, so they
   are no longer callers. Audit any other `cmdobj=` caller (e.g. menu command run
   directly) before deleting the block. Note: a downstream game that registered
   its own `look`/`quit` `@action` must convert those to *rules on* the engine
   `Look`/`Quit` (the engine owns those canonical verbs as of `.95`).

## Then remove (verified-dead surfaces from the alpha audit)

- **`evennia/commands/default/` tree** is unreachable from player input
  (~15k lines incl. `building.py` at 4,641). **One carve-out that is still
  live and must NOT be deleted blindly:** `CmdHelp`'s formatters in `help.py`
  are reused by the help renderer (`help.py` is half-live). (The web/REST path's
  former dependency on `unloggedin.create_normal_account` is gone as of `.95`;
  that helper was deleted and `server/inputfuncs.py` now routes through the
  engine `login_session`.) Extract the live formatter helpers, then delete the
  rest.
- **`default_cmds` flat-API container** (`evennia/__init__.py:334-380`)
  advertises dead commands and double-registers `building` (`:369` and `:371`,
  copy-paste). Slim to the still-live help formatters or remove.
- **`cmdparser.py`** (linear parser) — only a settings-comment fallback +
  one test patch (`commands/tests.py:1239`); the default is `cmdparser_trie`.
- **`syscommands.py`** (SystemNoInput/NoMatch/Multimatch) — reached only inside
  the dead `cmdobj`-only block; the engine owns no-input/no-match.
- **`cmdset_merge_warmup`** primes a merge cache nothing on the hot path reads
  (medium/small INEFFICIENT) — dies with the cmdset machinery.

## Approach

Design/discussion first (folder convention). Audit (checklist above) before
touching anything; report what is and isn't clear. Removal is large and
high-blast-radius: stage it (extract live help formatters → delete default tree
→ remove `cmdobj=`-only block once login lands → delete `CmdSet`/handler/parser/
constants/anchors → drop the `evennia/__init__.py` export). One reviewable PR
per stage.

## Scope boundary

- **In scope:** the audit, then cmdset-machinery removal and the verified-dead
  surfaces above.
- **Out of scope:** the EvMenu/EvMore/EvEditor migrations themselves (their own
  tracks); login rehoming (shipped in `.95`; prompt removed).
- **Ask before:** deleting the `cmdobj=`-reachable block (needs login landed +
  sign-off).

## Done means

cmdset capture and dispatch have zero engine consumers; `CmdSet`, the handler,
the legacy parser/syscommands, the `CMD_*` constants, and the anchors are gone;
`evennia.default_cmds` / `CmdSet` no longer in the flat API; live help
formatters preserved; the suite is green driving input through `cmdhandler`.
