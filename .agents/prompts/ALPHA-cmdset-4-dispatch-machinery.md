# ALPHA cmdset retirement — chunk 4: delete the legacy dispatch machinery

Status: todo. **Gated on chunks 2 and 3** (EvEditor off `cmdparser`; default
tree gone). Fourth of six chunks; see
[`ALPHA-cmdset-retirement-audit.md`](ALPHA-cmdset-retirement-audit.md).

## Why

The legacy cmdset merge/match block in `cmdhandler` is reachable only via
`cmdobj=` injection. Login landed engine-owned in `.95`; the **only** remaining
`cmdobj=` callers are two tests (`commands/tests.py:2125`,
`commands/default/tests.py:2169`). The parser/syscommand/warmup substrate is
reachable only from inside that block.

## Goal

Delete the `cmdobj=`-only dispatch path and the substrate it pulls in:

- The legacy merge/match block in `evennia/commands/cmdhandler.py` (~`:905-1026`)
  and the `cmdobj=`/`cmdobj_key=` parameters; collapse `cmdhandler` to the
  `try_action_dispatch` bridge. Keep the signal wiring the bridge relies on.
- `evennia/commands/cmdparser.py` (linear parser) and
  `evennia/commands/cmdparser_trie.py` — both reachable only from the block
  (and, until chunk 2, EvEditor; confirm chunk 2 landed). Update the
  `COMMAND_PARSER` setting (`settings_default.py:417`) accordingly.
- `evennia/commands/default/syscommands.py` — moved with the chunk-3 tree if not
  already gone; the `SystemNoInput/NoMatch/Multimatch` commands are reached only
  inside the dead block. Retire the `CMD_*` constants
  (`CMD_NOMATCH`/`CMD_NOINPUT`/`CMD_MULTIMATCH`/`CMD_CHANNEL`/`CMD_LOGINSTART`)
  once nothing references them.
- `evennia/commands/cmd_access_cache.py` and
  `evennia/commands/cmdset_merge_warmup.py` (+ its callers in `service.py:787`
  and `accounts.py:732-738`) and `evennia/commands/location_cmdset_cache.py` —
  all prime/serve a merge cache nothing on the hot path reads.
- The two `cmdobj=` tests.

## Scope boundary

- **In scope:** the `cmdobj=` block, the parsers, syscommands, `CMD_*`
  constants, the merge-warmup + access + location caches.
- **Out of scope:** `CmdSet`/`cmdsethandler`/the `.cmdset` handler attribute and
  `Command` (chunk 6); contrib (chunk 5). `cmdhandler` itself survives as the
  thin bridge entry point — this slims it, it does not delete it.
- **Ask before deleting the block** if any non-test `cmdobj=` caller has appeared
  since this prompt was written (re-grep).

## Done means

`grep -rn "cmdobj=" evennia --include="*.py"` shows no production caller;
`cmdparser`/`cmdparser_trie`/`syscommands`/`cmdset_merge_warmup`/`cmd_access_cache`/
`location_cmdset_cache` are gone; no live `CMD_*` constant reference remains
(contrib excluded); `cmdhandler` is the bridge only; the suite is green driving
input **through `cmdhandler`** (not via removed `cmdobj=` test paths).
