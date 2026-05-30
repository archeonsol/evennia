# Typeclass Hooks Reference

Companion reference tables for
[`typeclass-hooks.md`](typeclass-hooks.md). Three sections:

- §3 return-value contracts
- §4 override discipline
- §5 object-state-at-firing

Scope and naming taxonomy live in the main doc. Misshapen-hook
explanations live in the main doc's §6; this doc cross-references
by hook name.

In every table:

- "Class" is the defining class. Hooks defined on a mixin name the
  mixin; hooks defined on `TypedObject` (the shared parent of
  Object/Account/Channel/Script) name `TypedObject`.
- "Where" is `file:line` of the definition.
- "Misshapen" is a checkmark if the hook is listed in §6 of the
  main doc; click through there for the issue.

## §3. Return-value contracts

Four categories, plus one "transform" subcase (§3.5) for hooks that
both veto AND modify the value flowing through the chain.

### 3.1 Veto pre-hooks

Hook returns a value evaluated by `is_veto`
(`evennia/utils/utils.py:66`). Rule: falsy AND not None aborts;
`None`, `True`, and truthy non-False allows.

| Hook | Class | Where | Veto effect | Misshapen |
|---|---|---|---|---|
| `at_pre_move` | `MovementMixin` | mixins/movement.py:261 | Abort the move. No mover-side or location-side post-hooks fire. | |
| `at_pre_leave` | `MovementMixin` | mixins/movement.py:284 | Abort the move. Mover-side `at_pre_move` already passed; no further hooks fire. | |
| `at_pre_arrive` | `MovementMixin` | mixins/movement.py:308 | Abort the move. Both mover-side and source-side pre-hooks already passed; no further hooks fire. | |
| `at_pre_traverse` | `MovementMixin` | mixins/movement.py:548 | Abort traversal. `at_failed_traverse` fires on the exit. Move chain does NOT fire. | |
| `at_pre_puppet` | `LifecycleMixin` | mixins/lifecycle.py:437 | Abort puppet attach. Session is left unpuppeted. Engine emits no message. | |
| `at_pre_say` | `AppearanceMixin` | mixins/appearance.py:644 | See §3.5 (transform variant). | |
| `at_pre_get` | `AppearanceMixin` | mixins/appearance.py:516 | Abort the get. Move does not run. | |
| `at_pre_give` | `AppearanceMixin` | mixins/appearance.py:557 | Abort the give. Move does not run. | |
| `at_pre_drop` | `AppearanceMixin` | mixins/appearance.py:600 | Abort the drop. Move does not run. | |
| `at_pre_rename` | `TypedObject` | typeclasses/models.py:910 | Abort the rename. `db_key` is not written. | |
| `at_pre_delete` | `LifecycleMixin` (Object), `DefaultScript` | mixins/lifecycle.py:376; scripts/scripts.py:548, :890 | Returning `False` aborts `delete()`. Default returns `True`. | |

### 3.2 Veto event-hooks (misshapen naming)

Hooks named `at_<event>` that nevertheless implement the veto
contract. Listed separately so the contract is clear; renaming
follow-up tracked in §6 of the main doc.

| Hook | Class | Where | Veto effect | Misshapen |
|---|---|---|---|---|
| `at_msg_send` | `DefaultAccount` | accounts/accounts.py:1907 | Falsy-not-None aborts the send. | ✓ |
| `at_msg_receive` | `DefaultAccount` | accounts/accounts.py:1877 | Falsy-not-None aborts delivery. | ✓ |
| `at_idmapper_flush` | `TypedObject` | typeclasses/models.py:501 | `True` allows the cache flush; `False` declines (object stays cached). Default returns `False` when non-persistent attrs would be lost. | ✓ |

### 3.3 Content providers and renderers

Hook return value IS the content. Type per hook.

| Hook | Class | Where | Returns | Notes |
|---|---|---|---|---|
| `get_display_name` | `AppearanceMixin` | mixins/appearance.py:49 | `str` | Per-looker formatted name. Most-overridden hook in the surface (122+ call sites). |
| `get_extra_display_name_info` | `AppearanceMixin` | mixins/appearance.py:67 | `str` | Injected next to the display name; empty default. |
| `get_numbered_name` | `AppearanceMixin` | mixins/appearance.py:87 | `(singular: str, plural: str)` tuple | Used by `get_display_things` for stacking. |
| `get_display_header` | `AppearanceMixin` | mixins/appearance.py:148 | `str` | Empty default. |
| `get_display_desc` | `AppearanceMixin` | mixins/appearance.py:161 | `str` | Reads `desc` attribute by default. |
| `get_display_exits` | `AppearanceMixin` | mixins/appearance.py:174 | `str` | Rendered exits block. |
| `get_content_group_label` | `AppearanceMixin` | mixins/appearance.py:215 | `str` | Per-group label injected into `get_display_*` listings. |
| `get_display_characters` | `AppearanceMixin` | mixins/appearance.py:238 | `str` | Rendered characters block. |
| `get_display_things` | `AppearanceMixin` | mixins/appearance.py:261 | `str` | Rendered things block, with `get_numbered_name` stacking. |
| `get_display_footer` | `AppearanceMixin` | mixins/appearance.py:291 | `str` | Empty default. |
| `get_extra_display_state` | `AppearanceMixin` | mixins/appearance.py:304 | `str` | Injected into the `{extra_state}` template slot. |
| `return_appearance` | `AppearanceMixin` | mixins/appearance.py:340 | `str` | Composite renderer. See §2.10. |
| `at_look` | `AppearanceMixin` | mixins/appearance.py:462 | `str` | Returns the description; the caller messages it to the looker. Composite, both side-effect (calls `at_desc` on target) and renderer. |
| `Account.at_look` | `DefaultAccount` | accounts/accounts.py:1961 | `str` | OOC character picker; different contract from Object `at_look` despite the shared name. |
| `get_self_pronoun` | `AppearanceMixin` | mixins/appearance.py:742 | `str` | Pronoun resolved from the perspective of the passed `looker`. Default returns `"You"` when `looker is self`. |
| `get_say_template_self` | `AppearanceMixin` | mixins/appearance.py:684 | `str` | `str.format`-ready template, used inside `at_say` for the self echo. |
| `get_say_template_location` | `AppearanceMixin` | mixins/appearance.py:704 | `str` | Template for the location broadcast. |
| `get_say_template_receivers` | `AppearanceMixin` | mixins/appearance.py:723 | `str` | Template for per-receiver delivery (whispers, directed says). |
| `get_extra_info` | `TypedObject` | typeclasses/models.py:886 | `str` | Disambiguation info; consumed by multimatch resolver. |
| `get_log_filename` | `DefaultChannel` | comms/comms.py:177 | `str` | Channel log file path. |
| `get_default_lockstring` | `LifecycleMixin` (Object) | mixins/lifecycle.py:16; also `Character` (character.py:40) | `str` | Per-class lockstring used during `basetype_setup`. |
| `get_message_recipients` | `MessagingMixin` | mixins/messaging.py:138 | iterable of objects | Resolves the recipient set for a `msg()` call. |
| `get_visible_contents` | `AppearanceMixin` | mixins/appearance.py:388 | `dict[str, list]` | DEPRECATED. |
| `get_content_names` | `AppearanceMixin` | mixins/appearance.py:415 | `dict[str, list[str]]` | DEPRECATED. |
| `get_return_exit` | `DefaultExit` | objects/exit.py:319 | Exit or queryset | No callers in engine; see main doc §6. |

### 3.4 Pure side-effect (return ignored)

Return values discarded by the engine. Overrides may return
anything (typically `None`).

| Hook | Class | Where | Notes |
|---|---|---|---|
| `at_first_save` | `LifecycleMixin` / `DefaultAccount` / `DefaultChannel` / `DefaultScript` | various | One-shot init driver. Override the inner `at_<noun>_creation` hooks instead. |
| `at_object_creation` | `LifecycleMixin` | mixins/lifecycle.py:359 | |
| `at_object_post_creation` | `LifecycleMixin` | mixins/lifecycle.py:367 | |
| `at_object_post_spawn` | `LifecycleMixin` | mixins/lifecycle.py:385 | Spawner-only; see §2.1. |
| `at_object_post_copy` | `LifecycleMixin` | mixins/lifecycle.py:148 | Fired on the SOURCE, not the new copy. |
| `at_post_load` | multiple | various | Cache-load hook. |
| `at_idmapper_flush` | `TypedObject` | typeclasses/models.py:501 | LISTED IN §3.2 (return IS consulted); included here only by name to remind readers it's the exception. |
| `at_post_move` | `MovementMixin` | mixins/movement.py:465 | |
| `at_post_leave` | `MovementMixin` | mixins/movement.py:486 | |
| `at_post_arrive` | `MovementMixin` | mixins/movement.py:508 | |
| `at_post_traverse` | `MovementMixin` | mixins/movement.py:570 | |
| `at_failed_traverse` | `MovementMixin` (default) / `DefaultExit` (override) | mixins/movement.py:590; exit.py:302 | Default Exit override messages `"You cannot go there."`. |
| `at_post_puppet` | `LifecycleMixin` / `Character` | mixins/lifecycle.py:461; character.py:264 | Character override emits default echoes (suppressed on `reattach=True`). |
| `at_pre_unpuppet` | `LifecycleMixin` | mixins/lifecycle.py:486 | Return ignored despite `at_pre_*` name. See main doc §6. |
| `at_post_unpuppet` | `LifecycleMixin` / `Character` | mixins/lifecycle.py:509; character.py:298 | |
| `at_puppet_added` | `DefaultAccount` | accounts/accounts.py:388 | First-attach notification. |
| `at_puppet_removed` | `DefaultAccount` | accounts/accounts.py:412 | Last-detach notification. |
| `at_character_added` | `DefaultAccount` | accounts/accounts.py:366 | Characters-list mutation. |
| `at_character_removed` | `DefaultAccount` | accounts/accounts.py:377 | Characters-list mutation. |
| `at_post_create_character` | `DefaultAccount` | accounts/accounts.py:1042 | Per character-creation event. |
| `at_account_creation` | `DefaultAccount` | accounts/accounts.py:1577 | |
| `at_post_password_change` | `DefaultAccount` | accounts/accounts.py:1730 | |
| `at_first_login` | `DefaultAccount` | accounts/accounts.py:1711 | Fires BEFORE `at_pre_login`. See main doc §6. |
| `at_pre_login` | `DefaultAccount` | accounts/accounts.py:1737 | Return ignored despite `at_pre_*` name. See main doc §6. |
| `at_post_login` | `DefaultAccount` / `DefaultGuest` | accounts/accounts.py:1793, :2143 | |
| `at_failed_login` | `DefaultAccount` | accounts/accounts.py:1832 | |
| `at_disconnect` | `DefaultAccount` / `ServerSession` | accounts/accounts.py:1845; serversession.py:164 | |
| `at_post_disconnect` | `DefaultAccount` / `DefaultGuest` | accounts/accounts.py:1862, :2167 | |
| `at_post_get` / `at_post_give` / `at_post_drop` | `AppearanceMixin` | mixins/appearance.py:540, :582, :627 | Notifications. |
| `at_say` | `AppearanceMixin` | mixins/appearance.py:763 | Side-effect; emits via `msg`/`msg_contents`. |
| `at_desc` | `AppearanceMixin` | mixins/appearance.py:504 | Notification. May delete `self`. |
| `at_post_rename` | `TypedObject` / `AppearanceMixin` | typeclasses/models.py:931; appearance.py:833 | Two definitions serve distinct purposes: base is a stub for Account/Channel/Script; mixin override clears Object plural aliases. MRO resolves to mixin for Objects. |
| `at_post_access` | `LifecycleMixin` / `DefaultAccount` | mixins/lifecycle.py:542; accounts.py:1655 | Notification; the access decision is already made. |
| `at_post_channel_msg` | `DefaultAccount` | accounts/accounts.py:1411 | |
| `at_channel_creation` | `DefaultChannel` | comms/comms.py:166 | |
| `at_post_msg` | `DefaultChannel` | comms/comms.py:691 | |
| `at_script_creation` | `DefaultScript` | scripts/scripts.py:541, :825 | Two-layer definition (mixin + DefaultScript). |
| `at_start` | `DefaultScript` | scripts/scripts.py:577, :840 | Resume hook too; see main doc §6. |
| `at_repeat` | `DefaultScript` | scripts/scripts.py:565, :854 | |
| `at_pause` | `DefaultScript` | scripts/scripts.py:580, :865 | `manual_pause` kwarg. |
| `at_stop` | `DefaultScript` | scripts/scripts.py:583, :877 | Not fired on `delete()`. |
| `at_server_reload` | various | service.py callers | |
| `at_server_shutdown` | various | service.py callers | |
| `at_server_start` | `DefaultScript` | scripts/scripts.py:916 | |
| `at_login` | `ServerSession` | serversession.py:140 | Session-side login init. |
| `at_sync` | `ServerSession` | serversession.py:95 | Post-reload reattach driver. |
| `at_cmdset_get` | `LifecycleMixin` / `DefaultAccount` / `ServerSession` / `DefaultExit` | various | Cross-ref `command-system.md`. |
| `at_failed_traverse` | `DefaultExit` (override) | exit.py:302 | |

### 3.5 Transform pre-hooks

Pre-hooks that may MUTATE the value flowing through the chain
rather than just veto. Same is_veto rule applies for abort; in
addition, a string return replaces the next stage's input.
Resolved by `resolve_transform`
(`evennia/utils/utils.py`).

| Hook | Class | Where | Transform target | Notes |
|---|---|---|---|---|
| `at_pre_say` | `AppearanceMixin` | mixins/appearance.py:644 | The spoken `message` string. | Falsy-not-None aborts the say. |
| `Channel.at_pre_msg` | `DefaultChannel` | comms/comms.py:578 | The broadcast `message` string. | Falsy-not-None aborts the channel send. |
| `Account.at_pre_channel_msg` | `DefaultAccount` | accounts/accounts.py:1329 | The per-recipient delivered message. | Falsy-not-None aborts delivery to this recipient. |

### 3.6 Heterogeneous configuration / query hooks

State queries with hook-specific return types. Not part of the
pre/post pattern.

| Hook | Class | Where | Returns | Notes |
|---|---|---|---|---|
| `get_cmdsets` | `LifecycleMixin` / `DefaultAccount` / `ServerSession` | various | `(current, cmdsets)` tuple | Cross-ref `command-system.md`. |
| `get_cmdset_providers` | `DefaultObject` / `DefaultAccount` / `ServerSession` | objects/object.py:467; accounts.py:353; serversession.py:72 | `dict[str, CmdSetProvider]` | Cross-ref `command-system.md`. |
| `get_search_query_replacement` | `SearchMixin` | mixins/search.py:19 | `str` | Pipeline stage 1. |
| `get_search_direct_match` | `SearchMixin` | mixins/search.py:38 | `Object` or `None` | Short-circuits the pipeline on non-None. |
| `get_search_candidates` | `SearchMixin` | mixins/search.py:64 | iterable | Pipeline stage 3. |
| `get_search_result` | `SearchMixin` | mixins/search.py:121 | `Object` or iterable | Pipeline stage 4. |
| `get_stacked_results` | `SearchMixin` | mixins/search.py:162 | `Object` or iterable | Pipeline stage 5. |
| `get_puppet` | `DefaultAccount` / `ServerSession` | accounts.py:655; serversession.py:195 | `Object` or `None` | |
| `get_all_puppets` | `DefaultAccount` | accounts/accounts.py:670 | `list[Object]` | |
| `get_account` | `ServerSession` | serversession.py:185 | `Account` or `None` | |
| `get_client_size` | `ServerSession` | serversession.py:239 | `(width, height)` | |
| `get_character_slots` | `DefaultAccount` | accounts/accounts.py:953 | `int` or `None` | Game-side puppet quota. |
| `get_available_character_slots` | `DefaultAccount` | accounts/accounts.py:967 | `int` or `None` | |
| `get_username_validators` | `DefaultAccount` | accounts/accounts.py:731 | iterable of validators | Class method. |
| `get_puppet_or_account` | `ServerSession` | serversession.py:207 | `Object` or `Account` | No callers; see main doc §6. |
| `get_return_exit` | `DefaultExit` | objects/exit.py:319 | `Exit` or queryset | No callers; see main doc §6. |

## §4. Override discipline

Three categories per hook:

- **Public override**: hook exists for game-side customization. Document the contract honestly; engine never reaches around it.
- **Internal**: engine uses this hook to drive behavior; games shouldn't override without knowing what depends on its semantics.
- **Mixed**: some aspects are public, some internal. Documented in the "what depends" column.

### 4.1 Lifecycle hooks

| Hook | Discipline | What depends on its semantics | Override safety notes |
|---|---|---|---|
| `at_object_creation` / `at_account_creation` / `at_channel_creation` / `at_script_creation` | Public override | Engine reads no state from this; pure customization point. | Safe. Don't call `super()` if you want full replacement of defaults; in practice, do call super to pick up base setup. |
| `at_object_post_creation` | Public override | Same. Sees `_createdict`-supplied attrs. | Safe. |
| `at_object_post_spawn` | Public override | Spawner reads no return value. | Spawner-only fire path; see §2.1. |
| `at_first_save` | Internal | Drives the entire creation chain. Overriding it without calling super skips `basetype_setup`, `init_evennia_properties`, the `_createdict` application, and the public-override hooks above. | Override `at_object_creation` etc., not this. |
| `basetype_setup` / `basetype_posthook_setup` | Internal | Run from `at_first_save`. Set engine-required state (locks, cmdsets). | Override at your own risk; only for classes that redefine "what kind of object is this". |
| `at_post_load` | Public override | None directly; engine fires this to let games initialize cache-load state. | Safe. Idempotent: fires on every cache load, not just first-load. |
| `at_idmapper_flush` | Internal | Controls cache eviction. Returning `False` keeps the object cached. | Default carefully handles the non-persistent-attribute case. Custom overrides risk memory leaks or stale state. |
| `at_pre_delete` (Object and Script) | Mixed | Engine consults return value for veto. Game side may add cleanup. | When overriding, return `True` to allow deletion (or `False` to veto). Default returns `True`. |
| `at_object_post_copy` | Public override | None. | Safe. |

### 4.2 Movement and traversal

| Hook | Discipline | What depends | Notes |
|---|---|---|---|
| `at_pre_move` | Public override (veto) | None engine-side; veto stops the move. | Game-side authorization. Don't forget to message on veto. |
| `at_pre_leave` / `at_pre_arrive` | Public override (veto) | Same. | Run on the location, not the mover. Use for room-side restrictions. |
| `at_post_move` / `at_post_leave` / `at_post_arrive` | Public override | Default cmdset cache invalidation and `contents_cache` updates already happened by step 5 of §2.5; these hooks fire after that. | Safe. |
| `at_pre_traverse` / `at_post_traverse` / `at_failed_traverse` | Public override | Default Exit `at_failed_traverse` is the user-visible "you can't go there" message; overrides should preserve some feedback. | Idempotent overrides recommended for `at_failed_traverse` (two fire paths). |

### 4.3 Puppet / unpuppet / session

| Hook | Discipline | What depends | Notes |
|---|---|---|---|
| `at_pre_puppet` | Public override (veto) | Engine: aborts attach if vetoed. Game: typical use is permission / state check. | The reattach path (§2.9) passes `reattach=True`; overrides that block on game-state may want to allow reattach. |
| `at_post_puppet` | Public override | Character override emits entry message and rebuilds channels. The reattach path suppresses this via `reattach=True`. | When overriding `Character.at_post_puppet`, call super or replicate the channel rebuild yourself. |
| `at_pre_unpuppet` | Public override (NOTIFICATION despite name) | Return is ignored. | Misshapen; see main doc §6. |
| `at_post_unpuppet` | Public override | Character override emits exit message and rebuilds channels. | |
| `at_puppet_added` / `at_puppet_removed` | Public override | Engine: nothing. | Set-membership semantics; fires once per first-attach / last-detach. |
| `at_login` (Session) | Internal | Drives session init (cmdset, conn_time, etc.). | Override discouraged; use `at_pre_login` / `at_post_login` on Account instead. |
| `at_sync` (Session) | Internal | Drives the reattach chain (§2.9). | Override only if you understand the reload flow. |
| `at_disconnect` (Session and Account) | Mixed | Session version cascades into Account.`at_disconnect`. | When overriding the Account version, super is recommended; the chain handles `is_connected` and `at_post_disconnect`. |
| `at_post_disconnect` (Account) | Public override | Engine: nothing. | |

### 4.4 Appearance

| Hook | Discipline | What depends | Notes |
|---|---|---|---|
| `get_display_name` | Public override | Called from many places (122+ call sites). Performance-sensitive; overrides should avoid DB hits. | The single most-overridden hook. |
| `get_display_*` (rest) | Public override | Consumed by `return_appearance` via the `appearance_template`. | Adding a new slot also requires editing `appearance_template`. |
| `get_numbered_name` | Public override | Pluralization for stacked things. | English-only default; future language plugin owns localization. |
| `get_content_group_label` | Public override | Per-group label injection inside `get_display_*`. | |
| `get_extra_display_name_info` / `get_extra_display_state` | Public override | Empty default; consumed by `appearance_template` slots `{extra_name_info}` / `{extra_state}`. | |
| `return_appearance` | Public override | Default composes the `get_display_*` slots into `appearance_template`. | Most games override `get_display_*` instead. |
| `at_look` | Mixed | Default calls `return_appearance` and `at_desc`; returns the rendered string. Caller messages it. | Overrides that want to message directly should still return a string (callers `msg(...)` the return). |
| `at_desc` | Public override | None engine-side; the comment in default `at_look` notes the target may delete itself in this hook. | Surprising name; see main doc §6. |
| `get_visible_contents` / `get_content_names` | Deprecated | No live callers. | Don't override; not part of the modern chain. |

### 4.5 Say / messaging

| Hook | Discipline | What depends | Notes |
|---|---|---|---|
| `at_pre_say` | Public override (transform) | May modify or veto the spoken message. | |
| `at_say` | Public override | Default emits self / location / receiver messages with the template chain. | Heavy hook; replacing it loses all message routing. Prefer overriding the templates. |
| `get_say_template_self` / `get_say_template_location` / `get_say_template_receivers` | Public override | Consumed only by default `at_say`. | The friendly customization point for say wording. |
| `get_self_pronoun` | Public override | Called from `at_say` and any custom code that wants per-looker pronouns. | |
| `at_msg_send` / `at_msg_receive` | Public override (veto-on-falsy, misshapen naming) | Engine consults return value as veto. | Account-only; objects don't fire these. See main doc §6. |
| `Channel.at_pre_msg` / `Account.at_pre_channel_msg` | Public override (transform) | Engine consults return; transform rule. | |
| `Channel.at_post_msg` / `Account.at_post_channel_msg` | Public override | Engine: nothing. | Logging, audit, etc. |
| `get_log_filename` (Channel) | Public override | Default reads `log_file` attribute or formats from key. | Keep deterministic per channel. |
| `get_message_recipients` | Public override | Consumed by Object `msg()`. | |

### 4.6 Object interaction

| Hook | Discipline | What depends | Notes |
|---|---|---|---|
| `at_pre_get` / `at_pre_give` / `at_pre_drop` | Public override (veto) | Engine consults return. | The pre-side veto is the right place for game-side restrictions. |
| `at_post_get` / `at_post_give` / `at_post_drop` | Public override (notification) | Engine: nothing. | |
| `at_rename` / `at_pre_rename` | Public override | `at_pre_rename` vetoes; `at_rename` notifies. Two definitions of `at_rename` exist; MRO matters. | When overriding for an Object, override the mixin version (`AppearanceMixin.at_rename`). |

### 4.7 Account-side lifecycle and login

| Hook | Discipline | What depends | Notes |
|---|---|---|---|
| `at_account_creation` | Public override | None engine-side. | Mirrors `at_object_creation`. |
| `at_first_login` | Public override | Fires once per account, on the very first login. Cleared from `db.FIRST_LOGIN`. | Misshapen ordering; see main doc §6. |
| `at_pre_login` | Public override (NOTIFICATION despite name) | Return ignored. | Misshapen; see main doc §6. |
| `at_post_login` | Public override | Default `DefaultGuest` override calls `disconnect` after a delay; the `DefaultAccount` default is "send last-login message and welcome screen". | If overriding for a guest subclass, preserve the cleanup-on-disconnect path. |
| `at_failed_login` | Public override | None engine-side. | |
| `at_post_password_change` | Public override | None engine-side. | Audit/log point. |
| `at_post_create_character` | Public override | None engine-side. | |
| `at_character_added` / `at_character_removed` | Public override | Fired by `PlayableCharactersList`. | Distinct from `at_puppet_added`; see §2.7 vs §2.15. |
| `at_look` (Account) | Public override | OOC character picker. Different contract from Object `at_look`. | |
| `at_msg_send` / `at_msg_receive` (Account) | Public override (veto-on-falsy) | See §4.5. | |

### 4.8 Channel

| Hook | Discipline | What depends |
|---|---|---|
| `at_channel_creation` | Public override | None engine-side. |
| `at_first_save` (Channel) | Internal | Drives `at_channel_creation`, locks, `_createdict`. |
| `at_pre_msg` / `at_post_msg` | Public override | Channel-level send/notify. |
| `get_log_filename` | Public override | Consumed by `basetype_setup` and log writes. |
| `at_post_load` (Channel) | Internal | Re-attaches subscriber cache after cache reload. | Override only if you understand the subscriber-cache contract. |

### 4.9 Script

| Hook | Discipline | What depends |
|---|---|---|
| `at_script_creation` | Public override | None engine-side. Customization point. |
| `at_first_save` (Script) | Internal | Drives creation chain. |
| `at_start` / `at_repeat` / `at_pause` / `at_stop` | Public override | Timer lifecycle. Distinguish reload-pause (`manual_pause=False`) from user-pause. |
| `at_pre_delete` (Script) | Mixed | Veto-on-False; default returns True. |
| `at_server_reload` / `at_server_shutdown` / `at_server_start` | Public override | Server lifecycle. |
| `is_valid` | Public override | Consulted before each `at_repeat`; False stops the timer. |

### 4.10 Search

| Hook | Discipline | What depends |
|---|---|---|
| `get_search_query_replacement` / `_direct_match` / `_candidates` / `_result` / `_stacked_results` | Public override | Pipeline stages. Customization point. |
| `search` (the method that drives them) | Public override | Drives the chain; override only when wholesale replacement is needed. |

### 4.11 Cmdset / access

| Hook | Discipline | What depends |
|---|---|---|
| `at_cmdset_get` | Public override | Last-second mutation of an object's merged cmdset. Cross-ref `command-system.md`. |
| `get_cmdsets` | Public override | Returns the per-class cmdset stack. Cross-ref `command-system.md`. |
| `get_cmdset_providers` | Internal | Duck-typed by cmdhandler. Override only if you understand the session-proxy contract (`command-system.md`). |
| `at_post_access` | Public override (notification) | Engine: nothing (return ignored). | Audit point. |
| `get_default_lockstring` | Public override | Consumed by `basetype_setup`. |

### 4.12 Server-side scripts and the `at_server_*` hooks

The three `at_server_*` lifecycle hooks (`reload`, `shutdown`,
`start`) fire ONCE PER CACHED INSTANCE during their respective
phases. Game-side overrides should be cheap (the call is fanned
out across every cached object/account/script). Use the GAME-side
`at_server_reload_start` / `_stop` / `at_server_cold_start` etc.
hooks in `server/conf/at_server_startstop.py` for once-per-reload
work.

## §5. Object-state-at-firing

For each lifecycle hook, the object's state at the moment of firing.
Columns:

- **PK?**: Does the object have a database primary key (`pk`/`dbid`) set?
- **db row?**: Has the row been INSERTed in the DB?
- **typeclass init?**: Has the typeclass `__init__` chain finished?
- **`_createdict`?**: Is the `_createdict` temporary attribute present?
- **post-creation hooks?**: Have `at_object_creation` /
  `at_object_post_creation` already fired?
- **location attached?**: Is `self.location` populated to its final value?
- **cache state**: Is the object known to be a fresh construct or a cache rehydrate?
- **mid-transaction?**: Is the engine partway through a multi-step
  operation when this fires?

### 5.1 Object creation chain

The Django `post_save` signal fires `at_first_save` only when
`created=True`. By the time the signal handler runs, the INSERT has
completed and the object has a pk.

| Hook | PK? | db row? | typeclass init? | `_createdict`? | post-creation hooks? | location attached? | cache state | mid-tx? |
|---|---|---|---|---|---|---|---|---|
| `basetype_setup` | yes | yes | yes | yes (if create-helper used) | no | from `_createdict` only if applied; not yet at this point | fresh | no |
| `at_object_creation` | yes | yes | yes | yes | no (this IS one of the post-creation hooks) | no (location not yet applied from `_createdict`) | fresh | YES (locks added; attributes/tags about to be batch-added; location about to be assigned) |
| `at_object_post_creation` | yes | yes | yes | yes (about to be deleted) | yes (`at_object_creation` already fired; `_createdict` already applied; location set if supplied) | yes (location set from `_createdict` if any) | fresh | mostly no; `_createdict` cleanup happens right after this returns |
| `at_object_post_spawn` | yes | yes | yes | no (already cleaned up) | yes (full creation chain complete) | yes | fresh | no |

Notes:

- `at_object_creation` runs BEFORE `_createdict` is applied. Code in
  `at_object_creation` cannot read kwargs/tags/attributes supplied
  via `create_object(...)`. To see those, override
  `at_object_post_creation` or `at_object_post_spawn`.
- The `init_evennia_properties()` call between `at_object_creation`
  and `_createdict` application sets up Attribute/Tag descriptors.
  Before this call, `obj.db.foo` reads will work (Attribute Manager
  is lazy) but TagProperty / AttributeProperty descriptors may not
  yet be wired.
- The `_createdict`-driven `at_post_arrive` and `at_post_move`
  (mover-side and destination-side only) fire DURING
  `at_first_save`, before `at_object_post_creation`. The pre/leave
  halves of the move chain are skipped.

### 5.2 Object load / cache rehydration

`at_post_load` fires on every idmapper cache load (initial fetch
AND every cache miss after eviction or reload).

| Hook | PK? | db row? | typeclass init? | `_createdict`? | post-creation hooks? | location attached? | cache state | mid-tx? |
|---|---|---|---|---|---|---|---|---|
| `at_post_load` | yes | yes | yes | no | yes (long ago) | yes | rehydrated from cache or DB | no |

The cache load vs cold load distinction:

- After a server reload, every object that was cached pre-reload
  may be cold-loaded again on first access. `at_post_load` fires.
- During normal runtime, after `at_idmapper_flush` evicts an
  object, the next access reloads from DB. `at_post_load` fires.
- During normal runtime, every direct `ObjectDB.objects.get(...)`
  may return either a cached instance (no fire) or a freshly-loaded
  instance (fires). The instance pointer identity is preserved by
  the idmapper, so `is` comparisons across loads work as expected.

`at_post_load` overrides should be idempotent. The engine fires it
multiple times across the object's lifetime; "first ever load" and
"100th re-cache" both see the same hook.

### 5.3 Object delete

| Hook | PK? | db row? | typeclass init? | location attached? | cache state | mid-tx? |
|---|---|---|---|---|---|---|
| `at_pre_delete` | yes | yes | yes | yes (still in old location) | rehydrated | YES (deletion partway through; `delete()` has not yet detached sessions, cleared exits, or nulled the location) |

After `at_pre_delete` returns truthy, the rest of `delete()`
runs in this order: msg sessions, unpuppet, remove from
`account.characters`, null `db_account` / `db_home`, delete owned
scripts, `clear_exits`, `clear_contents`, clear attributes / nicks
/ aliases, bump cmdset cache, null `self.location`, super delete.
None of these have hooks.

### 5.4 Rename

| Hook | object state |
|---|---|
| `at_pre_rename(oldname, newname)` | object still has old key; `db_key` not yet written. |
| `at_post_rename(oldname, newname)` | object has new key (instance attribute set); `db_key` written and `post_save` signal already fired (which does NOT re-fire `at_first_save` because `created=False`). |

### 5.5 Move

| Hook | mover state | source state | dest state |
|---|---|---|---|
| `at_pre_move` | still in source | unchanged | unchanged |
| `at_pre_leave` | still in source | mover still in `.contents` | unchanged |
| `at_pre_arrive` | still in source | unchanged | mover not yet in `.contents` |
| (move commit: `self.location = destination`) | location attribute updated; both contents caches updated atomically | mover no longer in `.contents` | mover in `.contents` |
| `at_post_leave` | already in destination | mover gone from `.contents` | mover in `.contents` |
| `at_post_arrive` | already in destination | mover gone from `.contents` | mover in `.contents` |
| `at_post_move` | already in destination | mover gone from `.contents` | mover in `.contents` |

The location swap is a SINGLE assignment (`self.location =
destination`). `contents_cache` invalidation on both source and
destination is driven by the location setter on `ObjectDB`. There
is no observable state in which the mover is in both `.contents`
sets or neither.

### 5.6 Puppet / unpuppet / sync

| Hook | puppet state | session state | account state |
|---|---|---|---|
| `at_pre_puppet` (fresh attach) | `obj.account` not yet pointing to account (or pointing to None); `obj.sessions` empty | session.puppet still None | account.is_connected may already be True |
| `at_post_puppet` (fresh attach) | `obj.account = account`; `obj.sessions` contains session; `puppeted` tag set; locks re-cached | session.puppet = obj; session.puid = obj.id | account knows about the puppet via tags/sessions |
| `at_pre_puppet` (reattach, §2.9) | session.puid was pre-set from reload; obj loaded fresh from DB | session.account already set | account state restored from cache |
| `at_post_puppet` (reattach) | obj.sessions contains session; obj.account = self.account; locks re-cached | session.puppet = obj | same |
| `at_pre_unpuppet` | obj.account = account; obj.sessions contains session | session.puppet = obj | same |
| `at_post_unpuppet` | obj.sessions no longer contains session; if last session, `obj.account` is deleted; `puppeted` tag still present (removed after this hook returns) | session.puppet still set (cleared right after) | same |
| `at_puppet_added` | full attach state (after `at_post_puppet`) | same | account.characters and puppet set both reflect membership |
| `at_puppet_removed` | full detach state (after `at_post_unpuppet`) and the puppet tag has been removed; if last session, `obj.account` is gone | session.puppet = None; session.puid = None | account state reflects puppet removal |

### 5.7 Account login chain

| Hook | account state | session state |
|---|---|---|
| `session.at_login(account)` | account.is_connected = True | session being primed; cmdset_storage about to be set |
| `at_post_load` (account) | freshly cached (or already cached) | session logged_in not yet flagged |
| `at_first_login` | `db.FIRST_LOGIN` still True at entry; deleted right after | session not yet logged_in (it's flagged True after `at_pre_login`) |
| `at_pre_login` | account ready; `last_login` updated by `at_login` | session not yet logged_in |
| `at_post_login` | account fully connected; session.logged_in = True; portal sync already done | full login state |
| `at_failed_login` | NOT in the login flow above; called from the auth path on credential rejection | session is not associated with the account at this point |

### 5.8 Channel send

| Hook | channel state | message |
|---|---|---|
| `Channel.at_pre_msg` | channel fully initialized | original `message` |
| `Account.at_pre_channel_msg` (per-receiver) | recipient is iterable subscriber | message after channel-level transform |
| `Account.at_post_channel_msg` (per-receiver) | recipient has been delivered to | the delivered message |
| `Channel.at_post_msg` | message fully broadcast | original `message` (post-transform if mutated) |

### 5.9 Script lifecycle

| Hook | script state |
|---|---|
| `at_first_save` | row inserted; basetype_setup and at_script_creation are about to run via the driver. |
| `at_script_creation` | basetype_setup done; init_evennia_properties not yet run; `_createdict` may still be present. |
| `at_post_load` | cached. May fire on every reload. |
| `at_start` | timer about to begin (or resuming). |
| `at_repeat` | timer interval has elapsed; `is_valid()` returned True. |
| `at_pause` | timer about to be paused. `manual_pause=False` indicates reload-pause. |
| `at_stop` | timer about to be stopped (not fired on `delete()`). |
| `at_pre_delete` (Script) | full state; deletion pending on truthy return. |
| `at_server_reload` / `at_server_shutdown` | reload/shutdown in progress; persist any non-persistent state here. |
| `at_server_start` | post-startup; the script may or may not have an active timer. |

### 5.10 `at_idmapper_flush`

| Hook | object state |
|---|---|
| `at_idmapper_flush` | About to be evicted from cache. `obj.nattributes.all()` reads return current values (the override may consult them). Returning `False` keeps the object cached; returning `True` proceeds with eviction. |

This is the only hook in the surface whose return value directly
controls a cache-management decision. Default returns `False` if
non-persistent attributes are present (which would otherwise be
lost). Custom overrides should preserve that invariant or
explicitly accept the loss.
