# Engine minimal-set inventory (what's actually left)

Companion to [`ALPHA-cmdset-retirement-audit.md`](ALPHA-cmdset-retirement-audit.md).
That prompt is the *removal plan*; this is the *inventory finding* that answers
"is there game-shaped code hiding in the engine we need to move out, beyond the
dead command tree?" Answer, as of 2026-06: **no.** The engine modules are clean;
the one large removable thing is the dead command tree, already tracked.

Scope: `evennia/` only (contribs excluded). Verdicts use the placement rule from
[core-beliefs](../docs/core-beliefs.md): if a second consumer game could not
reasonably re-implement it from scratch, it's engine; opinionated defaults are
engine-OK when a game can opt out via setting or subclass.

## Command layer

Two command systems coexist:

- **`evennia/actions/`** (~6.7k lines) — the typed-action + rule-phase engine.
  Sole player-input dispatch path since `.73`. Game-import-clean (verified: zero
  `world.`/`mootest`/`underspire` imports). Ships generic defaults in
  `actions/default/` (movement, get/drop/give/put/enter, stock pose/emote). **This
  is the minimal command substrate.**
- **`evennia/commands/default/`** (~12k lines; `building.py` is 4,494) — stock MUX
  suite. Cmdsets still wired in `cmdset_character.py`/`cmdset_account.py` but
  **dead**: `cmdhandler` always tries `try_action_dispatch` first. Replaced, not
  relocated — the game reimplemented its verbs on the action engine
  (`world/actions/`, see [`CM1-port-ledger.md`](CM1-port-ledger.md)), so this is a
  **delete**, nothing moves to the game.
- **`evennia/commands/` dispatch machinery** (`cmdhandler`, `cmdset*`,
  `cmdparser*`, `syscommands`, merge caches) — hot path bypassed; alive only for
  help/DB merge + `cmdobj=` injection.

**Verified live consumers of `commands/default/`** (the only blockers to deletion,
contribs excluded):

1. `evennia/__init__.py:341-356` — flat-API cmdset exports + `default_cmds`
   container (advertises dead commands; `building` double-registered).
2. `settings_default.py:572-669` — `CMDSET_*` default to
   `commands.default_cmdsets.*` (fresh-game default; the game overrides to
   `world/cmdsets/anchors.py`).
3. `server/inputfuncs.py:329` — imports `create_normal_account` from
   `unloggedin.py` (login carve-out).
4. `help/renderer.py:47,53` — imports `CmdHelp`/`HelpCategory` **formatters** from
   `commands/default/help.py` (help carve-out).

Deletion is the [ALPHA-cmdset-retirement](ALPHA-cmdset-retirement-audit.md) track
(now chunked into six PRs). Its gates are all cleared: EvMore (done, 367626c8b),
EvEditor (`.82`), EvMenu (**deleted** in `.85`, not migrated), and login
(engine-owned, `.95`).

## Module layer

Fork additions are infrastructure, not game logic. All engine-generic unless noted.

| package | fork-added substrate | verdict |
|---|---|---|
| `typeclasses/` | JSONB attr storage (`jsonb_handler`/`jsonb_util`/`attribute_metrics`), `redis_attr_cache`, `typed_attr` migration | ENGINE — storage refactor |
| `accounts/` | `ControlBinding` model + `CharactersHandler` (ownership decoupled from active driver) | ENGINE — multi-session substrate |
| `comms/` | `channel_subscriber_cache` (Redis fan-out index) | ENGINE — perf cache |
| `scripts/` | `ondemandhandler` (lazy gradual-state eval) | ENGINE — generic lazy-eval |
| `help/` | `catalog.py` (help from action registry, not merged cmdsets) | ENGINE — dispatch integration |
| `events/`, `hooks/`, `jobs/` | whole packages: event bus, hook registry/linter, job queue | ENGINE — pure infra, registry-extensible |
| `prototypes/`, `locks/`, `web/`, most of `server/`+`utils/` | — | untouched stock infra |

**Two opinionated-defaults — engine-OK, the only arguably game-shaped items. Not
dead, not slated. Would move only on a deliberate "maximally generic" direction
change (which contradicts the current core-belief).**

- `narrative/delivery.py` `DefaultEmoteDelivery` — bakes in `db.pronoun`,
  `contents_get(content_type="character")`, hardcoded color codes. Clean opt-out
  via `NameResolver`/`EmoteDelivery` protocols (the game uses `SdescNameResolver`);
  linguistics core is generic.
- `objects/character.py:68` `DefaultCharacter.create()` — slot-limit + IP logging;
  `validate_name`/`normalize_name` homograph guard. Override hooks present.

**Not game-shaped (corrects a tempting misread):** `server/models.py`
`GameEvent`/`EngineJob` are persistence backing for the generic `events`/`jobs`
packages; `utils/verb_conjugation/` is generic English linguistics. All ENGINE.
