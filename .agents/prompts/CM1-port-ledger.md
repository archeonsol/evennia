# CM1 Cmdset-Elimination Burndown Ledger

Authoritative tracker for the standing directive: **remove every trace of
cmdsets-as-code from both the engine and the game, replaced entirely by the
typed-action + rule-phase engine.** Nothing stays "legacy" permanently;
coexistence (bridge returns `False` → legacy handles) is a *transitional*
mechanic only.

Companion to `CM1-action-system-roadmap.md` (the design). This doc answers
"what is left, and has anything slipped?" Update it in the same commit as each
port batch.

## Status legend

- `[ ]` not started
- `[~]` partial — core verb ported, deferred special-case branches still on legacy
- `[~]b` **bridge** — engine routes the verb (`@action` + `requires=`), but `carry_out` still calls legacy `Command.func()` / `invoke_legacy_cmd`; legacy command class **not** deletable
- `[x]` ported — every code path on the engine, legacy command deletable
- `[D]` deleted — legacy command/cmdset class removed from the **cmdset path**; the module file may remain as an empty stub or re-export shell until Phase 8 tree deletion (see §8)
- `[R]` deferred-branch reconciled — a special case carved off during an earlier
  partial port has been ported and removed from the deferred list

A module is not "done" until every command in it is `[x]`/`[D]` **and** every
deferred branch tracked below is `[R]`.

**Two columns matter for §C:** *routed* = off cmdset, dispatches via action engine;
*deletable* = no `Command.func()` bridge and no required legacy Command class.
Only `[x]` satisfies both.

---

## A. Engine substrate prerequisites

These are not commands; they are the dispatch capabilities a command family
needs before it can port. Build each before (or with) its dependent family.

| capability | module | status | needed before |
|---|---|---|---|
| Typed actions / `@rule` phases / `RuleResult` | `engine.py`, `action.py`, `rule.py`, `result.py` | `[x]` | everything |
| Parser + verb trie + symbol prefixes + multi-word phrase match (longest exact, then prefix) | `parser.py`, `registry.py` | `[x]` | everything |
| Bridge seam (`try_action_dispatch`, cmdhandler:902) | `dispatch.py` | `[x]` | everything |
| Actor / context / providers | `actor.py`, `context.py` | `[x]` | everything |
| Account provider when `callertype="account"` (OOC staff verbs while puppeted) | `context.py`, `dispatch.py`, `engine.py` | `[x]` | `__primary_handler__` inserts Account on session dispatch while puppeted |
| States (EvMenu, Disambiguation) | `menus.py`, `state.py` | `[x]` | menu-driven verbs |
| **Recog/sdesc-safe disambiguation** | `actor.py`, `menus.py`, `dispatch.py` | `[x]` | every targeted verb |
| Permission/lock gating (`requires`, lockstring) | `permission.py`, `predicate.py` | `[x]` | staff/builder/admin |
| `@interactive` yield / deferred input | `engine.py::_drive_generator` | `[x]` | chargen, editors, surgery, rent/programdoor, @airroom/grid |
| **`_unresolved` skip-check guard** | `action.py` (first-class field), `engine.py` (skip `check` when set) | `[x]` | every targeted parse; search miss no longer footguns observer rules |
| **`Action.block()` / typed `block_reason` IntFlags** | `action.py` (`block()`, `block_reason` field) | `[x]` | every gated verb family; `actor.explain()` surfaces failing check |
| Engine ownership of `__noinput__`/`__nomatch__`/`__loginstart__` | `dispatch.py` + `world/actions/general/nomatch.py` + `evennia/actions/default/loginstart.py` | `[x]` | engine path owns all three; unlogged connect/create/help on ``LoginSessionMixin`` |
| Channel-send routing (dynamic verb/nick) | `world/comms/bus.py`, `world/actions/account/comms.py` | `[x]` | player `x*` speak on engine; staff channel cmds on engine |
| Help-from-registry (`available_for`) | roadmap 7c | `[~]` | `HelpSearch` native; bare `help` uses `world/help/engine.py` registry index + file/db topics (2026-06 fix) |
| Multipuppet relay (`relay=False` on RuleSpec) | roadmap 7b | `[~]` | player slot verbs on engine via `world/multipuppet/account_shell.py` |
| Exit providers (move via rule) | roadmap 6d | `[x]` | movement family |
| **Activities** (sustained, interruptible, re-validating processes) | `process.py` | `[x]` | walking, autopilot, follow/escort |
| **Typed events + `@subscribe`** (event-scoped provider resolution) | `events.py`, `engine.py::emit` | `[x]` | reactive movement, ambient |
| **DynamicVerbResolver** (trie-miss resolver chain) | `parser.py` | `[x]` | dynamic exit-name verbs |
| **Default shipped actions** (movement substrate) | `default/movement.py`, `default/events.py` | `[x]` | exit traversal out of the box; game layers gates on top |
| **Narrative emote substrate** (Tier 2–3) | `evennia/narrative/{protocols,emote,delivery,linguistics.yaml}`; `default/roleplay.py`, `default/emote_nomatch.py` | `[x]` | optional stock `.`/`,` pose + key-based targeting; games override via `NameResolver`/`EmoteDelivery` |

**Disambiguation note:** `Actor.search` probes `effective.search_for` and raises
`AmbiguousTarget` on multi-match (stock `.search` printed + returned `None`,
which the engine never saw). Candidate labels render via
`get_display_name(looker)` so a character's real key is never leaked/accepted —
sdesc/recog targeting is honored end-to-end. Covered by
`world/tests/test_disambiguation_recog.py`.

**Architectural alignment (Phase 6/7a ported modules):** complete. Every ported
domain uses typed ``action.block()`` / domain ``IntFlag`` vocabularies
(``MoveBlock``, ``WieldBlock``, ``LookBlock``, …), provider self-guards
(``if self is not actor.character: return SKIP``), and a single domain mixin
aggregating topical ``@rule`` providers. Phase-7 deploy wiring lives in
``world/actions/wiring.py`` and is mixed into production ``Character``,
``Exit``, and ``Room`` typeclasses (``ActionCharacterMixin``,
``ExitMovementMixin``/``ExitObservationMixin``, ``RoomInteractionMixin``).
``ACTION_ENGINE_ENABLED`` is on in production settings.

**Engine test coverage (movement substrate batch):** `evennia/actions/tests/`
— `test_process.py` (Activity lifecycle/cancel/exclusive), `test_events.py`
(emit + `@subscribe` + provider scoping), `test_parser.py` (DynamicVerbResolver
chain + symbol prefixes), `test_engine.py` (Phase 1g `@interactive` generator
driver). **213** tests green in the full `evennia.actions.tests` package.

**Narrative test coverage (Pre-Phase 8 emote upstream):** `evennia/narrative/tests/test_emote.py`
— segment split, `first_to_third`/`first_to_second`, segment plans with stub resolver.
Game regression: `world/rpg/tests/test_emote_segments.py`, `world/tests/test_rp_actions.py`,
`world/tests/test_room_pose_resolve.py` (mootest uses `SdescNameResolver` in
`world/rpg/emote_resolver.py`; generic linguistics from `evennia.narrative.emote`).

---

## F. Pre-Phase 8 polish (complete — 2026-06)

Hybrid cleanups before cmdset deletion. Independent of §8 family ports.

| track | status | outcome |
|---|---|---|
| **A1 cmdset dedup** | `[x]` | Stock Evennia duplicates removed from `CharacterCmdSet` (`get`, `drop`, `give`, `setdesc`, `say`, `pose`, `inventory`); ~50 stale imports pruned; duplicate `CmdPose`/`CmdEmote` off cmdset |
| **A2 wire `@interactive`** | `[x]` | `world/economy/wire_flow.py` + `world/actions/economy/wire.py` (`EconomyMixin`); `CmdWire`/`CmdWireConfirm` off cmdset; `yes` wire/`AcceptServe` collision resolved |
| **A2 rune `@interactive`** | `[x]` | `world/runes/carve_flow.py` + `world/actions/runes/carve.py` (`RunesMixin`); `CmdCarve` off cmdset; `handle_pending_rune_input` shim → `False` |
| **A2 pending_dispatch** | `[x]` | `_HANDLERS = []` — wire/rune/rentable rows removed; rent/programdoor already `@interactive` |
| **A3 native `@open`** | `[x]` | `world/building/open.py` — standalone `run_open` + `_create_exit` (ObjManip parse only); Matrix guard, `MatrixExit` default, `CityExit` coord tags; no `CmdOpen` subclass |
| **B1–B3 upstream emote** | `[x]` | `evennia/narrative/` + optional `default/roleplay.py` + `DefaultEmoteNoMatchRules`; lazy export in `default/__init__.py` avoids verb conflict with mootest `Pose`/`Emote` |
| **B4 mootest migration** | `[x]` | `world/rpg/emote_resolver.py` (`SdescNameResolver`); `world/rpg/emote.py` thinned — upstream compiler + game delivery/formatting/skin/language |

**Still legacy (not blockers for Phase 8):** `CmdWire`/`CmdWireConfirm`/`CmdCarve` classes remain for non-engine fallback and `commands/command.py` re-exports; `handle_pending_food_input` / `handle_pending_cosmetic_input` dead code (not registered) until recipe/color wizards port to `@interactive`.

## B. Cmdset classes + engine-internal (the deletion endpoints)

`commands/default_cmdsets.py` — the substrate to delete in Phase 8.

| class | status | notes |
|---|---|---|
| `CharacterCmdSet` | `[~]` | **empty** (Phase 4–5); all IC verbs on action engine; merge anchor until cmdset substrate deleted |
| `AccountCmdSet` | `[~]` | **empty** (Phase 4–5); OOC/account verbs on action engine |
| `StaffAccountCmdSet` | `[~]` | staff channel suite on engine (`world/actions/staff/channels.py`); OOC shell adds are no-ops |
| `StaffCharacterCmdSet` | `[x]` | economy/freight/faction/multipuppet/typeclass on engine; cmdset empty |
| `UnloggedinCmdSet` | `[x]` | empty — ``world/actions/login/`` on ``ServerSession`` |
| `SessionCmdSet` | `[x]` | empty — ``@sessions`` on ``AccountShellMixin`` |
| `GameCmdTypeclass` | `[~]` | off cmdset; class kept for `run_typeclass` shell only |
| Inline silent `@`-cmds (CmdAtSyncChannels/AtClearUnread/AtPreviewRp/AtSyncContext/AtCmdsetDebug) | `[x]` | ported → `world/actions/webclient/` |
| Engine: `evennia/commands/cmdset.py`, `cmdsethandler.py` | `[ ]` |
| Engine: cmdhandler dual-dispatch branch / merge cache | `[ ]` |
| Engine: `CmdSet` export in `evennia/__init__.py` | `[ ]` |

---

## C. Command modules (370 commands / 69 modules)

Grouped by Phase 7a port order. Count = `class Cmd*`/`*Command` in the module.

### 1. Roleplay / observation (high-use, clear ownership)
| module | n | status | notes |
|---|---|---|---|
| `base_cmds.py` | 13 | `[~]` | look (+photo/directional/detail), examine, stop, stopwalking, go, time ported (→ general/observation, general/activity, general/info). get/drop/give/put/enter ported incl. §D deferred branches (cash-pile, get-from-container + corpse/unconscious/logged-off gating, stacked/numbered pickup & transfer, `_split_count` helper). Remaining: Command/AccountCommand base infra (Phase 8); flatlined/dead `at_pre_parse` state gate (Phase-8 firewall/allowlist) |
| `roleplay_cmds.py` | 27 | `[x]` | 25/27 ported (→ roleplay/{expression,description,recognition,places,posture,senses}): say/pose/emote/looc/tease, @setdesc(@dmas), @voice, @setscent(@setsmell), @naked, @sdesc, @pronoun, recog/recognize/forget, language, memorize, memory, @lp(@look_place/@standing/@roompose), @tp(@temp_place), @sleepplace(@sleep_place/@logout_pose), @wakemsg(@wake_up/@wakeupmsg/@loginmsg), @flatlinemsg(@flatline/@deathmsg/@dyingmsg), @sp(@setplace), sit, lie, getup(stand/getoff/getofftable), smell, count. Dropped two-word aliases (@describe me as, @sleep place, sit on, lie down/lie on, stand up). **CmdNoMatch** → `[R]` `world/actions/general/nomatch.py` + engine dispatch; legacy class is empty stub. **CmdPending** → `[x]` `world/actions/staff/pending.py` (staff `@pending`; was on AccountCmdSet). |
| `inventory_cmds.py` | 12 | `[x]` | all 12 ported (→ inventory/{inventory,equipment,ammo,clothing}): inventory, wield/hold, unwield/stash, fh/freehands, reload, unload, ammo/mag/rounds, wear/don, remove/doff, toggle/adjust, strip, frisk/patdown. Dropped two-word aliases (put on/take off/eject mag/check ammo/check mag). |

### 2. Movement (6d exit providers + engine primitives now in place)

6d landed as a full rewrite, not a thin `Traverse`-delegate. The **generic core
ships with the engine** in `evennia/actions/default/` (the action-engine analogue
of `evennia/commands/default/`): the `Move` action type, the baseline
`ExitTraversalRules` (destination + `traverse`-lock `check` and the
`move_to`/Locomotion `carry_out`), `CharacterMovementRules` (emits the events), the
`Departed`/`Arrived`/`Moved` events, the sustained `Locomotion` **Activity** (with
overridable `step_delay`/`announce`/`resolve_step` hooks), and the default
`exit_resolver` (the concrete `DynamicVerbResolver`). A game gets working exit
traversal out of the box and layers its rules on top.

The **game-specific** parts stay in `world/actions/movement/`: `precheck_exit_traversal`
decomposed into typed `Move` `check` predicates (`MoveBlock` IntFlag reasons) on the
Character/Exit providers, an RP-paced `Locomotion` subclass, and the door/bulkhead/
rentable/autopilot/citygrid verbs. `staggered_movement` is superseded by the engine
Locomotion; follow/escort/shadow react to the `Departed`/`Arrived` events.

**Engine modules (generic, shipped):**

| module | what |
|---|---|
| `evennia/actions/process.py` | `Activity`, `ActivityManager`, generator driver |
| `evennia/actions/events.py` | `Event`, `@subscribe`, `engine.emit()` |
| `evennia/actions/parser.py` | `DynamicVerbResolver` protocol + resolver chain |
| `evennia/actions/default/movement.py` | `Move`, `ExitTraversalRules`, `CharacterMovementRules`, `Locomotion`, `exit_resolver` |
| `evennia/actions/default/events.py` | `Moved`, `Departed`, `Arrived` |

**Game modules (this game):**

| module | what |
|---|---|
| `world/actions/movement/move.py` | `MoveBlock` + game `check` gates (Character/Exit/Room) |
| `world/actions/movement/locomotion.py` | RP-paced `Locomotion` subclass (hooks: delay/announce/resolve) |
| `world/actions/movement/social.py` | follow/shadow/escort + `@subscribe(Departed)` reactions |
| `world/actions/movement/doors.py` | open/close/unlockdoor/verify/knock |
| `world/actions/movement/bulkhead.py` | seal/unseal/bulkhead/@bulkhead |
| `world/actions/movement/rentable.py` | rent/payrent/push/programdoor/check/@rentset (`@interactive`) |
| `world/actions/movement/autopilot.py` | autopilot (tunnel FSM engage/disengage) |
| `world/actions/movement/citygrid.py` | 7 builder @-commands |
| `world/actions/movement/__init__.py` | `MovementMixin`, `ExitMovementMixin`; `register_exit_resolver()` at import |

All ports below are engine-complete and test-covered
(`world/tests/test_movement_action.py`, **41** cases). **`@interactive` verified**
against real multi-step flows: rent master-code entry, programdoor auth+menu,
@airroom/grid confirmation prompt.

**Phase-7 deploy complete (2026-06):** `MovementMixin`/`ExitMovementMixin` mixed into production `Character`/`Exit`; `ACTION_ENGINE_ENABLED = True`; traversal uses engine `Move` + `Locomotion` compat shim (`movement/compat.py`), not legacy `ExitCmdSet`.

| module | n | status | notes |
|---|---|---|---|
| `follow_cmds.py` | 3 | `[x]` | follow/shadow/escort → `world/actions/movement/social.py` (event-subscriber Activities on `Departed`/`Arrived`); stop following/escorting already in general/activity |
| `door_cmds.py` | 5 | `[x]` | open/close/unlockdoor/verify/knock(ring) → `movement/doors.py`; toggles the same `exit.db.door_*` state the `Move` gates read |
| `rentable_door_cmds.py` | 6 | `[x]` | rent/payrent/push/programdoor(pdoor)/check/@rentset(rentset) → `movement/rentable.py`; rent + programdoor `ndb._pending_*` flows became `@interactive` generator carry_out |
| `bulkhead_cmds.py` | 4 | `[x]` | seal/unseal/bulkhead(gate)/@bulkhead(requires=Builder) → `movement/bulkhead.py`; sealing drives the `check_gate_bulkhead` Move gate |
| `tunnel_cmds.py` | 1 | `[x]` | autopilot(auto/ap) → `movement/autopilot.py`; engages/disengages the tunnel FSM Activity (vehicle driver loop kept in `world.movement.tunnels`) |
| `city_grid_cmds.py` | 7 | `[x]` | @citymap/@citylevel/@citycoord/@setcoord(/check//repair)/@airroom(+buildairshaft, /grid//force)/@airroomrefresh/@shaftconnect → `movement/citygrid.py`, all requires=Builder; @airroom/grid is an `@interactive` paced generator |

### 3. Object interaction (target-centric)

**Game modules (this game):**

| module | what |
|---|---|
| `world/actions/interaction/hygiene.py` | ``shower`` (+ ``RoomShowerRules`` on ``RoomInteractionMixin``) |
| `world/actions/interaction/food.py` | ``menu``/``newrecipe``/``prepare``/``serve``/``rate``/``delrecipe`` |
| `world/actions/interaction/cosmetic.py` | ``ink``/``tattoo``, ``remove tattoo``, ``tattoos``, ``apply``, ``wipe``, ``color`` |
| `world/actions/interaction/__init__.py` | ``InteractionMixin``, ``RoomInteractionMixin`` |

All ports below are engine-complete and test-covered
(`world/tests/test_interaction_action.py`). **Architectural alignment pass**
complete (typed ``UseBlock``/``action.block()``, provider self-guards, domain
mixins). ``food_cmds`` wizard / ``serve`` accept-decline still call legacy helpers
via ``handle_pending_food_input`` until Phase-8 ``__nomatch__`` ownership.

**Phase-7 deploy complete (2026-06):** `InteractionMixin`/`RoomInteractionMixin`/`ActionCharacterMixin` mixed into production typeclasses; interaction verbs dispatch via engine only.

| module | n | status | notes |
|---|---|---|---|
| `base_cmds.py` (get/drop/give/put/enter) | — | `[x]` | core + §D deferred branches ported (→ general/objects): cash-pile pickup, get-from-container w/ corpse/unconscious/logged-off gating, stacked/numbered pickup & transfer, `_split_count` shared parse helper. Only Phase-8 flatlined/dead state gate remains (firewall/allowlist, not a per-verb branch). Tests: world.tests.test_object_actions (+ updated test_get_drop_action / test_give_put_action) |
| `use_cmds.py` | 1 | `[x]` | CM1-aligned — ``use`` → `world/actions/interaction/use/` (decomposed dispatcher: ``CharacterUseRules`` + pill bottle, cyberware station, perfume, medical tools, networked object providers); tests: `world.tests.test_interaction_action` |
| `cosmetic_cmds.py` | 6 | `[x]` | CM1-aligned — ink/tattoo, remove tattoo, tattoos, apply (cosmetic + medical fallback), wipe/remove makeup, color → `world/actions/interaction/cosmetic.py`; ink/color `@interactive` via ``ink_flow``; tests: `world.tests.test_interaction_action` |
| `food_cmds.py` | 6 | `[x]` | CM1-aligned — all 6 → `world/actions/interaction/food.py`; recipe wizard + pending-input helpers still imported from legacy module (Phase-8 ``__nomatch__``); tests: `world.tests.test_interaction_action` |
| `hygiene_cmds.py` | 1 | `[x]` | CM1-aligned — ``shower`` → `world/actions/interaction/hygiene.py`; room gates on ``RoomInteractionMixin``; tests: `world.tests.test_interaction_action` |

### 4. Combat (CombatState)
| module | n | status |
|---|---|---|
| `combat_cmds.py` | 14 | `[x]` | full engine port: attack, flee, grapple intents, stance/coup/letgo; legacy cmds removed from CharacterCmdSet |
| `vehicle_combat_cmds.py` | 9 | `[x]` | fire/ram/dislodge, gunner, mount install/reload → `world/actions/combat/vehicle_*.py`; legacy cmds removed from CharacterCmdSet; eject deferred (slate owns) |
| `CombatState` | — | `[x]` | `world/combat/states.py` wired to `start_combat_ticker` / `remove_both_combat_tickers` |
| `stealth_cmds.py` | 4 | `[x]` | hide, search, sneak, unhide on engine; hide-pending catch-all on StealthMixin |
| `death_cmds.py` | 7 | `[x]` | @ooc, pod verbs, go shard/light on engine; puppeted @ic bridged via CmdReturnIC; unpuppeted @ic stays account cmdset |

### 5. Medical (target = patient)
| module | n | status |
|---|---|---|
| `medical_cmds.py` | 13 | `[x]` | ht/assess/triage, stabilize/treat/transfuse, cpr/defib, patient/sedate/wake, operate → `world/actions/medical/`; apply stays on cosmetic `Apply` via `shared.do_medical_apply`; legacy cmds removed from CharacterCmdSet |
| `cmd_assist_surgeon.py` | 1 | `[x]` | merged into `world/actions/medical/surgery.py` (`Assist`); `get_assistant_bonus` in `shared.py` |

### 6. Matrix / modal (MatrixState)
| module | n | status |
|---|---|---|
| `matrix_cmds.py` | 7 | `[x]` | all native in `world/actions/matrix/` incl. handset |
| `diskette_cmds.py` | 7 | `[x]` |
| `handset_cmds.py` | 1 | `[x]` | logic in `world/actions/matrix/handset_impl.py`; legacy file thin cmdset shim |
| `network_cmds.py` | 4 | `[x]` |
| `slate_cmds.py` | 3 | `[x]` |

### 7. Staff / builder / admin (actor_type=Account, requires=Builder)
| module | n | routed | deletable | status | notes |
|---|---|---|---|---|---|
| `staff_cmds.py` | 29 | yes | stub | `[x]` | native → `world/actions/staff/{sheet,moderation,account}.py`; legacy Command classes remain for `commands/command.py` re-exports until Phase 8 |
| `builder_commands.py` | 19 | yes | stub | `[x]` | native → `world/actions/staff/{building,builder_runners}.py`; `@open` → native `world/building/open.py` (Matrix/CityExit hooks, no `CmdOpen` subclass) |
| `staff_vehicle_cmds.py` | 6 | yes | stub | `[x]` | native → `world/actions/staff/vehicle.py` |
| `staff_spawn_cmds.py` | 6 | yes | stub | `[x]` | native → `world/actions/staff/spawn.py` |
| `staff_npc_cmds.py` | 4 | yes | stub | `[x]` | native → `world/actions/staff/npc.py` |
| `staff_admin_wrappers.py` | 12 | yes | stub | `[x]` | `@ban/@unban/@charcreate/@chardelete` → `wrappers.py`; `@perm/@emit/@wall/@force/@nick/@access/@batch*` → `evennia_admin.py`; `@option/@password/@userpassword/@quell` → `account/{settings,quell}.py` |
| `staff_notes_cmds.py` | 1 | yes | stub | `[x]` | native → `world/actions/staff/notes.py` |
| `audit_cmds.py` | 1 | yes | yes | `[x]` | `AUDITS` in `world/audit/registry.py`; legacy thin re-export |
| `lock_cmds.py` | 2 | yes | yes | `[x]` | `do_lock`/`do_unlock` in `world/actions/vehicle/lock_impl.py`; legacy thin shim |

### 8. Remaining player commands (CM1 §8 — 2026-06)
| module | n | status | | module | n | status |
|---|---|---|---|---|---|---|
| `vehicle_cmds.py` | 18 | `[D]` | | `economy_cmds.py` | 13 | `[~]` | player verbs → `world/actions/economy`; staff `@shop*`/`@freight` native (`world/staff/economy.py`, `world/staff/freight.py`) |
| `alchemy_cmds.py` | 12 | `[D]` | | `robot_cmds.py` | 7 | `[D]` | `robot-register` on engine (`world/actions/robot/robot.py`) |
| `vehicle_security_cmds.py` | 6 | `[D]` | | `scavenge_cmds.py` | 5 | `[D]` |
| `cyberware_cmds.py` | 4 | `[D]` | | `who_cmds.py` | 3 | `[x]` | inlined → `world/actions/account/who.py` |
| `trust_cmds.py` | 3 | `[D]` | | `multipuppet_cmds.py` | 3 | `[~]` | player + staff on engine; relay helpers still in legacy module |
| `media_cmds.py` | 3 | `[D]` | | `dj_audio_cmds.py` | 3 | `[D]` | `photo recog` on engine (`world/actions/media/media.py`) |
| `crafting_cmds.py` | 3 | `[D]` | | `survival_cmds.py` | 2 | `[D]` |
| `rune_cmds.py` | 2 | `[x]` | carve + ignite on engine; helpers in ``world/runes/ritual_helpers.py`` | `document_cmds.py` | 2 | `[D]` |
| `discord_link_cmds.py` | 2 | `[x]` | → `world/actions/account/discord.py` | `bar_mgmt_cmds.py` | 2 | `[D]` |
| `audio_cmds.py` | 2 | `[D]` | | `appearance_cmds.py` | 2 | `[D]` |
| `salvage_cmds.py` | 1 | `[D]` | | `player_cmds.py` | 1 | `[x]` | `@xp` → `world/rpg/xp_shell.py` |
| `performance_cmds.py` | 1 | `[D]` | | `notes_cmds.py` | 1 | `[D]` |
| `freight_cmds.py` | 1 | `[x]` | on engine (`world/actions/staff/world_cmds.py`) | `edit_cmds.py` | 1 | `[x]` | `@webedit` → `world/actions/edit/webedit_flow.py` |
| `bug_report.py` | 1 | `[x]` | → `world/actions/account/bug.py` | `artistry_cmds.py` | 1 | `[D]` |
| `sheet_cmds.py` | 1 | `[x]` | `@stats` on engine (`world/actions/account/stats.py`) | `use_cmds.py` | 1 | `[x]` |
| `death_cmds.py` | — | `[~]` | `@ic`/`@ooc` lounge transit on engine; unpuppeted re-puppet path deleted; `DeathLobbyCmdSet` removed |

### Channels / help / session (need substrate from §A)
| module | n | status | blocked on |
|---|---|---|---|
| `channel_cmds.py` | 22 | `[~]` | player speak on engine; staff suite + community on engine; ``@channel`` list native in ``world/actions/account/channels.py`` |
| `cmd_account_channel.py` | 1 | `[x]` | superseded by native ``ChannelList`` action |
| `help_cmds.py` | 1 | `[x]` | `Help`/`helpsearch` → `world/actions/account/help.py` + `world/help/engine.py` (registry cmd index + file/db topics) |
| `help_search_cmd.py` | 1 | `[x]` | → `world/actions/account/help.py` |
| `session_lifecycle_cmds.py` | 1 | `[x]` | `quit` → `world/actions/account/lifecycle.py` |
| `unloggedin.py` | 0 | `[x]` | retired — create/connect on ``world/actions/login/`` |

---

## D. Deferred special-case branches (must be reconciled = `[R]`)

Carved off during partial ports. Each must become its own rule before its
module is "done".

| origin | deferred branch | status |
|---|---|---|
| `CmdLook` | `look <target> in <photograph>` close-up | `[R]` |
| `CmdLook` | directional peek-through (`look north`) | `[R]` |
| `CmdLook` | room-detail lookups | `[R]` |
| `CmdGet` | cash-pile pickup | `[R]` |
| `CmdGet` | get-from-container | `[R]` |
| `CmdGet` | corpse / unconscious / logged-off gating | `[R]` |
| `CmdGet` | stacked / numbered pickup | `[R]` |
| `CmdDrop`/`CmdGive` | stacked / "aren't carrying" pre-check | `[R]` |
| `CmdGet`/`CmdDrop`/`CmdGive` | shared numbered-search parse helper (`_split_count`) | `[R]` |
| movement family | production typeclass wiring (`MovementMixin`/`ExitMovementMixin` on Character/Exit) | `[x]` Phase-7 deploy |
| movement family | retire `staggered_movement.py` callers + `Exit.do_traverse` override | `[x]` Phase-7 deploy (`movement/compat.py`, engine shim) |
| movement family | rich per-tier staggered narration (crawl/drag templates, custom `move_leave_*`) | `[R]` via ``announce_staggered_departure()`` + ``Locomotion.announce`` |
| interaction family | production typeclass wiring (`InteractionMixin`/`RoomInteractionMixin`/`ActionCharacterMixin`) | `[x]` Phase-7 deploy |
| interaction family | ``food_cmds`` recipe wizard pending input (legacy ``handle_pending_food_input`` — unregistered dead code) | `[ ]` Phase-8 optional — ``serve`` accept/decline on engine via ``AcceptServe``; cosmetic ink/tattoo/color on ``@interactive`` |
| Pre-Phase 8 pending | wire / rune / rentable nomatch pending rows | `[R]` wire + rune → ``@interactive``; rentable already ``@interactive``; ``pending_dispatch._HANDLERS`` empty |
| Sprint 3 helpers | ``rentable_format`` / ``vehicles.targets`` / ``utils.tokens``; ``world/actions`` import cleanup | `[R]` |
| Sprint 4 nomatch | ``dispatch.py`` NoMatch dispatch; ``world/input/pending_dispatch`` (now empty); ``NoMatchRules`` on ``GeneralMixin``; ``CmdNoMatch`` off cmdset | `[R]` |
| §7 staff / builder legacy modules | ``staff_cmds`` / ``staff_spawn`` / ``staff_npc`` / ``staff_vehicle`` / ``staff_notes`` / ``staff_admin_wrappers`` / ``audit_cmds`` / ``builder_commands`` gutted | `[R]` |
| §7 staff / builder | ``legacy_bridge.port_legacy_command`` / ``invoke_legacy_cmd`` | `[R]` native modules in `world/actions/staff/`; `legacy_bridge.py`/`ports.py` deleted |
| `lock_cmds` / `handset_cmds` | func-bridge shims | `[R]` inlined → `lock_impl.py`, `handset_impl.py` |
| `inventory_cmds` | ``remove`` tattoo branch | `[R]` `perform_remove_tattoo()` shared with cosmetic action |

---

## E. Phase 8 final-removal checklist

### E.1 Phase-7 deploy (typeclass wiring before flag flip)

**Movement**

- [x] Mix `MovementMixin` into production `Character`; mix `ExitMovementMixin` into production `Exit`
- [x] Set `Move.__primary_handler__ = Exit` (via `world/actions/wiring.py`)
- [x] Import `world.actions.wiring` at startup (registers `exit_resolver` on shared parser)
- [x] Retire `world/rpg/staggered_movement` callers for player walk interrupt (Locomotion compat in `movement/compat.py`)
- [x] Retire `Exit.do_traverse` staggered path when `ACTION_ENGINE_ENABLED` (instant `super()` shim)
- [x] Dual-path prod smoke: bare exit name, door block, follow, rent `@interactive` (manual checklist)

**Observation / interaction**

- [x] Mix `ExitObservationMixin` into production `Exit` (directional look / closed-door blocks)
- [x] Mix `InteractionMixin` into production `Character` (via `ActionCharacterMixin`)
- [x] Mix `RoomInteractionMixin` into production `Room` (``shower`` room gates)
- [x] Mix `ActionCharacterMixin` + `StealthRevealMixin` into production `Character`

**Flag flip**

- [x] Bump `EVENNIA_REF` to `underspire.63`; flip `ACTION_ENGINE_ENABLED`

### E.2 Phase 8 cmdset elimination

**Phase 8 (2026-06):** status gates on engine `StateProvider` + room rules; cmdhandler always bridges to `try_action_dispatch`.

- [x] Per-status firewall retired — `check_command_gated` no-op; `GatekeeperCheckRules` removed; gates in `world/status/{room_gates,character_states,global_gates}.py` + `FlatlinedState` / `CombatState`
- [x] Zero legacy cmdset dispatches for puppet input — cmdhandler always calls `try_action_dispatch` first; all player cmdsets empty (`test_zero_cmdset_dispatch`, `test_action_registry_coverage`)
- [x] `pending_dispatch` empty (Pre-Phase 8 A2)
- [x] Stock Evennia verb duplicates removed from cmdset (Pre-Phase 8 A1 + Sprint E `_strip_stock_*`)
- [x] Pure CM1 burndown (2026-06): zero `from commands.*` in `world/actions/`; Sprint 6 `do_*` flows wired directly (no `flow_runner`); staff economy/world/typeclass/multipuppet native; account help/xp/channel/multipuppet native; `flow_runner.py` deleted
- [x] Engine owns `__nomatch__` (game `NoMatchRules` + default feedback; cmdset `CmdNoMatch` removed)
- [x] Engine owns `__noinput__` (``NoInputRules`` on ``GeneralMixin``; legacy ``SystemNoInput`` bypassed when bridge on)
- [x] Engine owns `__loginstart__` (``DefaultLoginStartRules``; unlogged verbs on ``LoginSessionMixin``)
- [x] Legacy ``at_pre_parse`` gatekeeper path removed from ``base_cmds._apply_character_gates``
- [~] `evennia/commands/cmdset.py`, `cmdsethandler.py` — still present for help/DB merge; hot path bypassed (cmdobj injection only)
- [x] cmdhandler `ACTION_ENGINE_ENABLED` gate removed — bridge always runs for normal input
- [ ] `CmdSet` export removed from `evennia/__init__.py` (deferred)
- [x] Staff action `requires=` uses transpiled predicates (`from_lockstring` / `HasCapability`); persisted lock DSL retained
- [x] `default_cmdsets.py` — moved to `world/cmdsets/anchors.py`; `CMDSET_*` settings updated; legacy storage paths migrated at puppet attach
- [x] `commands/*_cmds` tree deleted (Phase 8 hardening); `commands/` package is a stub

---

## G. Command coverage audit (2026-06)

Automated diff so tier-1 verbs are not silently dropped (the `@quell` failure mode).

| artifact | path |
|---|---|
| Audit package | `mootest/world/command_coverage/` |
| Test + report generator | `mootest/world/tests/audit_command_coverage.py` |
| CI checklist | `mootest/world/tests/test_action_registry_coverage.py` |
| Markdown report | `mootest/world/command-coverage-report.md` |
| CSV export | `mootest/world/command-coverage-report.csv` |
| Docs | `mootest/docs/writing_commands.md` (Testing) |

**Run:** `evennia test --settings settings.py world.tests.audit_command_coverage`

**Legacy baseline:** static parse of `mootest-server/underspire/commands/default_cmdsets.py` + account OOC shell, merged with Evennia default Character/Account cmdsets (underspire tree cannot be imported on the current fork).

**Diff columns:** `verb | tier | legacy_cmdset | legacy_source | engine_action | cmdset_fallback | ledger_module | ledger_status | verdict`

**Verdicts (tier-1 blockers):** `MISSING` fails CI. `REMAPPED_OK`, `DROPPED_ALIAS`, and `RETIRED_OK` are documented absences — maintain lists in `world/command_coverage/constants.py` (`TIER1_REMAP`, `TIER1_DROPPED_ALIASES`, `TIER1_RETIRED`).

**Tier-2:** Evennia stock verbs absent from the engine are listed in the report appendix only (not CI blockers unless promoted to tier-1).

### Retired verbs (documented absence — not ported)

| verb(s) | was on | status | notes |
|---|---|---|---|
| `cover`, leave cover, `peek`, `suppress`, `expose` | `cover_commands.py` / CharacterCmdSet | **RETIRED** | Cover combat subsystem removed from mootest; `expose` was a cover alias. Not reimplemented on engine. |
