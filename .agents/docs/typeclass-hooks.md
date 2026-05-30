# Typeclass Hooks Contracts

Runtime contracts for the typeclass hook surface. Sibling to
[`command-system.md`](command-system.md), which covers cmdset / command
hooks. Companion reference tables (return contracts, override
discipline, object-state-at-firing) live in
[`typeclass-hooks-reference.md`](typeclass-hooks-reference.md).

Scope: `DefaultObject`, `DefaultCharacter`, `DefaultRoom`,
`DefaultExit`, `DefaultAccount` (+ `DefaultGuest`), `DefaultChannel`,
`DefaultScript`, the typeclass mixins (`LifecycleMixin`,
`MovementMixin`, `AppearanceMixin`, `MessagingMixin`, `SearchMixin`),
and the `ServerSession` puppet/sync hooks. Out of scope: cmdset
internals (see `command-system.md`); web / protocol hooks (deferred to
the architecture doc's W1).

This doc is the design predecessor to **H1** (hook registry) in
[`engine-api-architecture.md`](engine-api-architecture.md). Schema
choices here inform the registry's decorator surface. Misshapen hooks
flagged in §6 are Phase C / H1 cleanup inputs.

## Sequence-diagram legend

```
caller.method(args)       a method call; arrow points from caller to callee
veto?                     pre-hook gate; falsy-not-None aborts the chain
                          (see is_veto in evennia/utils/utils.py)
[short-circuit]           branch taken when the preceding gate fires
*N                        repeated N times (e.g. one per receiver)
```

Where the chain crosses actors (mover / source room / destination,
or session / account / puppet), a small swim-lane table is used; flat
single-actor chains use inline arrows.

## §1. Naming taxonomy

Hook names today carry semantics by prefix, with two important
violations called out below. Section 6 lists every hook whose name
does not match its behavior.

### Prefixes

| Prefix | Semantic | Return contract | Override safety | Example |
|---|---|---|---|---|
| `at_pre_<event>` | Veto gate, fires before the operation. | Falsy-not-None aborts; `None`/`True`/truthy allows. | Public override. | `at_pre_move`, `at_pre_puppet`, `at_pre_say` |
| `at_post_<event>` | Notification, fires after the operation completed (or after the relevant phase). | Ignored. | Public override; engine never reads the return. | `at_post_move`, `at_post_puppet`, `at_post_arrive` |
| `at_<event>` (no pre/post) | Lifecycle mutator or composite action hook. State and contract are per-hook; see §2 for the firing event and §3 for return contract. | Mixed. Some return content (e.g. `at_look`); most return `None`. | Mostly public override; a few are engine-internal coordination points (e.g. `at_first_save`, `at_post_load`). | `at_object_creation`, `at_first_save`, `at_say`, `at_look`, `at_desc`, `at_rename` |
| `at_failed_<event>` | Branch fired when the matching `at_pre_<event>` vetoed (or the operation otherwise failed). | Ignored. | Public override. | `at_failed_traverse`, `at_failed_login` |
| `get_display_<part>` | Content provider for a named slot of `return_appearance`. | Returns a string (the rendered slot). | Public override; this is the primary customization surface for appearance. | `get_display_name`, `get_display_desc`, `get_display_exits`, `get_display_things` |
| `get_<other>` | State query or configuration provider. Reads state, never mutates. | Returns a value; type per hook. | Public override for query hooks; some are internal plumbing (e.g. `get_cmdset_providers`). | `get_numbered_name`, `get_search_candidates`, `get_default_lockstring`, `get_log_filename` |
| `return_<event>` | Composite renderer. Assembles a slot-by-slot string by calling the matching `get_display_*` providers and returning the joined result. | Returns the rendered output. | Public override, but most games override the `get_display_*` providers instead. | `return_appearance` |

`at_*` and `get_*` are the only conventional prefixes. `return_*` is
attested only by `return_appearance` and exists for historical
reasons; new composite renderers should use a less ambiguous prefix
(see §6).

### Prefix-by-prefix rules

**`at_pre_*`**: every name in this prefix MUST be a veto gate using
the `is_veto` rule (falsy-not-None aborts). Returning `None` is the
default-safe case; overrides that forget to `return True` still
allow the action. The veto consumer is expected to message the
caller; the engine never auto-messages on veto.

**`at_post_*`**: every name in this prefix MUST be a notification.
Return values are ignored. The post-hook runs after the visible
side effects of the event are committed; subsequent engine code
does not consult its return.

**`at_<event>`**: this prefix is overloaded. It covers
(a) one-shot lifecycle mutators that run exactly once per object
(`at_object_creation`, `at_account_creation`, `at_channel_creation`,
`at_script_creation`),
(b) idempotent re-init hooks that run on every cache load
(`at_post_load`, `at_first_save` on `TypedObject`),
(c) composite action hooks that both perform an operation and
return its output (`at_look`, `at_say`),
(d) failure branches (`at_failed_traverse`, `at_failed_login`).
The overload is real; see §6 for the misshapen-hooks tally.

**`get_display_*`**: content providers consumed by
`return_appearance`. These are pure functions of `(self, looker,
**kwargs)`. The override surface for appearance customization lives
here, not on `return_appearance`. Adding a new appearance slot means
adding a `get_display_<slot>` plus referencing it from
`appearance_template`; existing slots should not be repurposed.

**`get_<other>`**: state queries or configuration providers.
Examples: `get_numbered_name` (pluralization), `get_search_candidates`
(search-pipeline stage), `get_default_lockstring` (per-class lock
defaults), `get_cmdset_providers` (cmdset-handler plumbing).
Heterogeneous by return type; see §3 reference table.

**`return_*`**: a composite renderer that joins `get_display_*`
slots. Today only `return_appearance` uses this prefix; treat the
prefix as deprecated for new hooks.

**`at_<noun>_added` / `at_<noun>_removed`**: set-membership
notifications. Today this shape appears only on Account
(`at_puppet_added`, `at_puppet_removed`, `at_post_add_character`,
`at_post_remove_character`); the `at_post_*` variant is the
character-relationship form, the `_added`/`_removed` variant is the
puppet-set form. See §2.7 and §6.

### Aliases preserved for compatibility

These names are kept as aliases of newer names, defined at module
level on the mixin. New code should use the canonical name.

| Alias | Canonical |
|---|---|
| `at_before_move` | `at_pre_move` |
| `at_after_move` | `at_post_move` |
| `at_after_traverse` | `at_post_traverse` |

The aliases are simple class-attribute assignments
(`at_before_move = at_pre_move` etc.); overriding the canonical
in a subclass updates the alias only if the override happens before
class-body evaluation finishes. Game-side overrides should bind to
the canonical name to avoid surprises.

## §2. Calling order

Per-event subsections. Each documents the hook sequence, short-circuit
rules, and cross-actor dispatch (mover / source / destination, or
account / session / puppet). State-at-firing details live in §5 of
[`typeclass-hooks-reference.md`](typeclass-hooks-reference.md); return
contracts in §3, override discipline in §4.

### 2.1 Object creation (`DefaultObject`)

Driver: Django's `post_save` signal, connected in
`evennia/typeclasses/models.py:166`. The signal receiver
`call_at_first_save` (models.py:69) fires `instance.at_first_save()`
only when `created=True`.

```
post_save(created=True)
  └─ obj.at_first_save()                           [LifecycleMixin]
       ├─ obj.basetype_setup()                     (locks default)
       ├─ obj.at_object_creation()                 [PUBLIC OVERRIDE POINT]
       ├─ obj.init_evennia_properties()
       ├─ (if _createdict) apply create kwargs:
       │     ├─ db_key / db_location / db_home / db_destination
       │     ├─ permissions / locks / aliases / tags / attributes
       │     └─ if cdict.location:
       │           ├─ location.at_post_arrive(self, None)
       │           └─ self.at_post_move(None)
       ├─ obj.at_object_post_creation()            [PUBLIC OVERRIDE POINT]
       └─ obj.basetype_posthook_setup()
```

After `at_first_save` returns, `evennia/prototypes/spawner.py` may
call `obj.at_object_post_spawn(prototype=...)` if the object was
created via the spawner (spawner.py:803 and :872; two call sites,
covering spawn-new and update-existing paths).

Notes:

- `at_object_creation` fires after locks but before
  `_createdict`-supplied attributes; overrides must not assume
  passed-in attributes are present. `at_object_post_creation` is the
  hook to use when an override needs to see kwargs/prototype data.
- The `at_post_arrive` / `at_post_move` calls inside `at_first_save`
  fire ONLY when an initial `location` is supplied via `_createdict`.
  Objects created without a location skip the move chain entirely.
  These calls fire `at_post_arrive` and `at_post_move` only; the
  `at_pre_*` / `at_post_leave` halves are skipped on first placement.
- `at_object_post_spawn` is spawner-specific. Objects created with
  `evennia.create_object` directly do NOT fire it. See §6.

### 2.2 Object load / idmapper cache rehydration

Driver: `TypedObject` initialization through the idmapper. `at_post_load`
fires once per cache-load, both on first DB fetch and after every
server reload.

```
idmapper.cache_object(instance)
  └─ instance.at_post_load()           [PUBLIC OVERRIDE POINT]
```

Override chain (multiple defining classes, MRO-resolved):

1. `TypedObject.at_post_load` (typeclasses/models.py:540): stub.
2. `LifecycleMixin.at_post_load` (mixins/lifecycle.py:395): stub.
3. `DefaultExit.at_post_load` (objects/exit.py:261): clears the
   exit's default cmdset so a renamed exit rebuilds its command.
4. `DefaultChannel.at_post_load` (comms/comms.py:775): subscriber
   cache attach.
5. `DefaultAccount.at_post_load` (accounts/accounts.py:1590): stub.
6. `DefaultScript.at_post_load` (scripts/scripts.py:533): stub.

Distinct from `at_idmapper_flush`, which fires when the idmapper
DROPS the object from cache (typeclasses/models.py:501); see §2.20.

Despite a docstring on `TypedObject.at_post_load` (models.py:546)
referring to "the historical name `at_init`", no `at_init` method
exists in this fork. Older Evennia docs that mention `at_init` are
stale; the rename to `at_post_load` is complete.

### 2.3 Object delete

Driver: `LifecycleMixin.delete()` (mixins/lifecycle.py:159).

```
obj.delete()
  ├─ obj.at_object_delete()  ─── returns False?  [veto]  ─→ abort
  │
  ├─ msg + unpuppet all sessions
  ├─ remove from account.characters
  ├─ delete owned scripts
  ├─ obj.clear_exits()
  ├─ obj.clear_contents()
  ├─ obj.attributes.clear() / nicks / aliases
  ├─ bump_cmdset_generation(self.location)
  ├─ self.location = None  (fires contents_cache update on old loc)
  └─ super().delete()       (Django delete; `post_delete` signals fire)
```

`at_object_delete` is a veto hook by behavior (returning `False`
aborts) but is named `at_<event>` rather than `at_pre_delete`. The
default returns `True`, matching the "non-None truthy allows" rule
from `is_veto`. See §6.

No `at_post_delete` hook exists on the typeclass; downstream
notification rides Django's `post_delete` signal or a manual
override of `delete()`.

### 2.4 Rename

Driver: `TypedObject.key` setter and the `@name` command. The
typeclass exposes both halves on `TypedObject`
(typeclasses/models.py:910), with `at_rename` separately re-defined
on `AppearanceMixin` (mixins/appearance.py:907).

```
rename_caller
  └─ obj.at_pre_rename(oldname, newname)  ─── falsy-not-None?  ─→ abort
  ├─ obj.key = newname   (db_key write; post_save fires)
  └─ obj.at_rename(oldname, newname)
```

`at_rename` is defined twice (TypedObject and AppearanceMixin) with
identical signatures. MRO resolves to `AppearanceMixin.at_rename`
for `DefaultObject` and descendants. See §6.

### 2.5 Move

Driver: `MovementMixin.move_to()` (mixins/movement.py:64; signature
documented in the method docstring lines 64 to 82). Three actors:
**mover** (self), **source** (current location), **destination**.

```
mover                  source                destination
─────                  ──────                ───────────
move_to()
  is_veto check:
  at_pre_move      ─→ falsy-not-None? abort
                         source.at_pre_leave    ─→ falsy-not-None? abort
                                                    destination.at_pre_arrive ─→ falsy abort
  ─── lock & access checks ───
  self.location = destination     (single assignment; contents_cache
                                   on both source and destination updates)
                         source.at_post_leave     destination.at_post_arrive
  at_post_move                                    (the two at_post_*-side
                                                   hooks are unordered
                                                   relative to each other
                                                   and both fire BEFORE
                                                   at_post_move)
```

Detailed sequence (movement.py:64 to :199):

1. `mover.at_pre_move(destination, move_type=...)` veto check.
2. `source.at_pre_leave(mover, destination, move_type=...)` veto
   check. Skipped if `source is None` (e.g. first placement).
3. `destination.at_pre_arrive(mover, source, move_type=...)` veto
   check.
4. Lock check (`getfrom`, `traverse`, `enter`; see movement.py),
   permission check.
5. `mover.location = destination`. Side effect: Django save and
   `contents_cache` invalidation on both old and new location.
6. `source.at_post_leave(mover, destination, move_type=...)`.
   Skipped if `source is None`.
7. `destination.at_post_arrive(mover, source, move_type=...)`.
8. `mover.at_post_move(source, move_type=...)`.

Steps 6 and 7 are unordered relative to each other; both complete
before step 8. The mover-side invariant from
[`engine-boundary-migration.md`](engine-boundary-migration.md)
("mover is in destination before followers are scheduled") holds:
by step 6, `mover.location` is already `destination`.

Errors from any hook are caught inside `move_to` and logged via
`logerr`; they do not abort subsequent hooks. This is a deliberate
isolation choice and a behavior subtle enough to surprise
overriders; see §6.

Per-hook `**kwargs` is the same dict passed to `move_to`, so
`move_type` and any custom kwargs are propagated to every hook.

### 2.6 Exit traversal

Driver: `DefaultExit.do_traverse()` (objects/exit.py:270).

```
exit.do_traverse(mover, target_location)
  ├─ exit.at_pre_traverse(mover, target_location)
  │      ─── falsy-not-None?  ─→  exit.at_failed_traverse(mover); return
  ├─ source_location = mover.location
  ├─ mover.move_to(target_location, move_type="traverse", exit_obj=exit)
  │      (fires the full §2.5 move chain)
  │
  ├─ if move succeeded:  exit.at_post_traverse(mover, source_location)
  └─ if move failed:
        ├─ if exit.db.err_traverse: mover.msg(err_traverse); skip hook
        └─ else: exit.at_failed_traverse(mover)
```

Traversal-level hooks (`at_pre_traverse`, `at_post_traverse`,
`at_failed_traverse`) are exit-side; the inner move chain fires the
mover/source/destination hooks of §2.5. A vetoed
`at_pre_traverse` does NOT fire any move-chain hooks. A vetoed
`at_pre_move` from within the inner move chain DOES fire
`at_failed_traverse` afterwards (because `move_to` returns False
without raising).

The `at_failed_traverse` branch has two fire paths (pre-veto and
post-move-failure). Overrides should be idempotent in case both
fire.

### 2.7 Puppet

Driver: `DefaultAccount.puppet_object()` (accounts/accounts.py:500
onward). Three actors: **account**, **session**, **puppet object**.

```
account                session             puppet
───────                ──────              ──────
puppet_object(session, obj)
  guard: already-puppeting? msg + return
  was_already_owned = (puppet.account == account
                       and puppet.sessions.count() > 0)
  access('puppet') check  ─→ deny: msg + return
  takeover handling for existing puppet (MULTISESSION_MODE)
  if session.puppet: unpuppet_object(session)   (fires §2.8)
  max-puppet check
                                            puppet.at_pre_puppet(
                                              account, session=session)
                                            ──── falsy-not-None?
                                                 ─→ return (no msg)
                                            tags.add("puppeted", "account")
                                            sessions.add(session)
                                            account = account
                       session.puid = puppet.id
                       session.puppet = puppet
                                            puppet.locks.cache_lock_bypass
                                            puppet.at_post_puppet()
  SIGNAL_OBJECT_POST_PUPPET.send(sender=puppet, ...)
  schedule_cmdset_merge_warmup_for_character(puppet)   (best-effort)
  if not was_already_owned:
    account.at_puppet_added(puppet, session=session)   (best-effort)
```

`at_puppet_added` fires once per FIRST-attach of `puppet` to
`account` (not once per session). A session swap from one of the
account's own sessions to another does NOT re-fire it. The
membership-change semantic is documented in
`DefaultAccount.at_puppet_added` (accounts.py:388).

`at_pre_puppet` veto leaves the session unpuppeted but the engine
emits no user-visible message; the override is expected to message
the caller before returning falsy.

`Character.at_pre_puppet` / `at_post_puppet`
(objects/character.py:241, :264) override the lifecycle-mixin
versions to add session-binding side effects (channel subscribe,
unconnected-character cleanup, default echoes). The reattach
suppression discussed in §2.9 is in
`Character.at_post_puppet`.

### 2.8 Unpuppet

Driver: `DefaultAccount.unpuppet_object()` (accounts/accounts.py:614).

```
unpuppet_object(session)
  for session in iter(session):
    obj = session.puppet
    if obj:
      obj.at_pre_unpuppet()
      obj.sessions.remove(session)
      last_session = (obj.sessions.count() == 0)
      if last_session: del obj.account
      obj.at_post_unpuppet(account, session=session)
      obj.tags.remove("puppeted", "account")
      SIGNAL_OBJECT_POST_UNPUPPET.send(sender=obj, ...)
      if last_session:
        account.at_puppet_removed(obj, session=session)
    session.puppet = None
    session.puid = None
```

`at_pre_unpuppet` has the `at_pre_*` name but the engine does NOT
honor its return value as a veto; the call is unconditionally
followed by detach. See §6.

`at_puppet_removed` fires on LAST-detach, mirroring
`at_puppet_added`'s FIRST-attach semantics.

`Character.at_post_unpuppet` (objects/character.py:298) overrides
the base to handle channel unsubscribe and default-echo emission.

### 2.9 Session re-attach (at_sync)

Driver: `SessionHandler` after a server reload. Each session whose
portal connection survived is re-synced via
`ServerSession.at_sync()` (serversession.py:95).

Phase A item A4 corrected the order to fire the puppet hooks so
non-persistent cmdset state rebuilds. Both
`at_pre_puppet` and `at_post_puppet` fire with `reattach=True`.

```
sessionhandler  (post-reload sync)
  └─ session.at_sync()                          [serversession.py:95]
       ├─ super().at_sync()  (portal-side sync, base class)
       ├─ if not logged_in: load CMDSET_UNLOGGEDIN
       ├─ self.cmdset.update(init_mode=True)
       └─ if self.puid:
            obj = ObjectDB.objects.get(id=self.puid)
            obj.at_pre_puppet(self.account, session=self, reattach=True)
              ─── falsy-not-None? puid=None, puppet=None; return
            obj.sessions.add(self)
            obj.account = self.account
            self.puid = obj.id
            self.puppet = obj
            obj.locks.cache_lock_bypass(obj)
            obj.at_post_puppet(reattach=True)
```

Differences from §2.7:

- Access check is SKIPPED. The session was authenticated pre-reload.
- `SIGNAL_OBJECT_POST_PUPPET` is NOT sent (it's a fresh-attach
  signal, not a re-attach signal). `at_puppet_added` is also NOT
  fired.
- `cmdset_merge_warmup` is NOT scheduled.
- The `reattach=True` kwarg propagates into both hooks. Default
  Character overrides use this to suppress entry/exit echoes; game
  overrides that reset non-persistent state should re-do that work
  on reattach.

If `at_pre_puppet` vetoes with `reattach=True`, the session is left
unpuppeted; user-visible explanation is the override's
responsibility.

### 2.10 Appearance render

Driver: `AppearanceMixin.at_look()` (mixins/appearance.py:462),
typically called from the `look` command via
`Character.at_look(target)`.

```
caller (looker)                target (looked-at)
───────────────                ──────────────────
caller.at_look(target)
  ├─ access(target, "view") check     ─→ deny: return "Could not view '<name>'."
  ├─ (if LOOK_ATTR_PREFETCH_ENABLED)
  │     target.attributes.get_all()   (prefetch; errors logged + swallowed)
  │
  ├──────────────────────────────────→ target.return_appearance(caller)
  │                                       ├─ get_display_name(caller)
  │                                       ├─ get_extra_display_name_info(caller)
  │                                       ├─ get_extra_display_state(caller)
  │                                       ├─ get_display_desc(caller)
  │                                       ├─ get_display_header(caller)
  │                                       ├─ get_display_footer(caller)
  │                                       ├─ get_display_exits(caller)
  │                                       │     └─ filter_visible(exits, caller)
  │                                       │     └─ for each: get_display_name(caller)
  │                                       ├─ get_display_characters(caller)
  │                                       │     └─ filter_visible(chars, caller)
  │                                       │     └─ for each: get_display_name(caller)
  │                                       │     └─ get_content_group_label('characters', caller)
  │                                       ├─ get_display_things(caller)
  │                                       │     └─ filter_visible(things, caller)
  │                                       │     └─ group + get_numbered_name(count, caller)
  │                                       │     └─ get_content_group_label('things', caller)
  │                                       └─ format_appearance(template_result, caller)
  │
  ├──────────────────────────────────→ target.at_desc(looker=caller)
  └─ returns description string
```

Notes:

- `return_appearance` is the only `return_*`-prefixed hook in the
  in-scope surface (see §1, §6). It returns a string; overriders
  who want a different shape are expected to override the
  `get_display_*` providers, not `return_appearance` itself.
- The `appearance_template` class attribute (a `str.format` template
  with named slots) is the friendly customization point: change the
  template, leave the providers alone, and the layout shifts. The
  template's named slots MUST stay in sync with the `get_display_*`
  set (see §6 for the implicit coupling).
- `get_visible_contents` and `get_content_names` (appearance.py:388,
  :415) are marked DEPRECATED in their docstrings. `get_content_names`
  has no callers in the appearance chain above; it's a vestige. See §6.
- `get_extra_display_name_info` and `get_extra_display_state`
  (appearance.py:67, :304) are hook-shaped extension points consumed
  inside the template (`{extra_name_info}`, `{extra_state}` keys).
  Empty default; override to inject status indicators, etc.
- `at_desc` fires on the looked-at object after `return_appearance`
  has been collected. The comment in `at_look` (appearance.py:499)
  notes "must be the last reference to target so it may delete
  itself when acted on" so overrides of `at_desc` may legitimately
  delete `self`.

Account-side `at_look` (accounts/accounts.py:1961) is a different
hook: it composes a multi-character picker rather than calling
`return_appearance` on a single target. The collision in name
between `Object.at_look` and `Account.at_look` is intentional
duck-typing for OOC look. See §6.

### 2.11 Say / whisper

Driver: the `say` / `whisper` commands invoke
`speaker.at_pre_say(message, ...)` then
`speaker.at_say(message, ...)` (mixins/appearance.py:644, :763).

```
speaker
───────
at_pre_say(message, **kwargs)
  default: returns the (possibly transformed) message; falsy-not-None
           aborts. Truthy non-string is treated as the original
           message (transform rule, see §3).

at_say(message, msg_self, msg_location, receivers, msg_receivers, **kwargs)
  whisper = kwargs.pop("whisper", False)
  msg_type = "whisper" or "say"

  if whisper:
    if msg_self is True:
       msg_self = speaker.get_say_template_self(whisper=True, **kwargs)
    if not msg_receivers:
       msg_receivers = speaker.get_say_template_receivers(whisper=True)
    msg_location = None
  else:
    if msg_self is True:
       msg_self = speaker.get_say_template_self(whisper=False, **kwargs)
    if not msg_location:
       msg_location = speaker.get_say_template_location(whisper=False)
    if not msg_receivers:
       msg_receivers = speaker.get_say_template_receivers(whisper=False)

  if msg_self:
    self_mapping = {
      "self": speaker.get_self_pronoun(speaker, **kwargs),
      "object": speaker.get_display_name(speaker),
      "location": location.get_display_name(speaker) if location else None,
      "receiver": None,
      "all_receivers": ", ".join(r.get_display_name(speaker)
                                  for r in receivers) if receivers else None,
      "speech": message,
    }
    self.msg(text=(msg_self.format_map(self_mapping), {"type": msg_type}),
             from_obj=speaker)

  if receivers and msg_receivers:
    for receiver in receivers:
      individual_mapping = {
        "self": speaker.get_self_pronoun(receiver, **kwargs),
        "object": speaker.get_display_name(receiver),
        "location": location.get_display_name(receiver),
        "receiver": receiver.get_display_name(receiver),
        "all_receivers": ", ".join(r.get_display_name(r)
                                    for r in receivers) if receivers else None,
        "speech": message,
      }
      receiver.msg(text=(msg_receivers.format_map(...), {"type": msg_type}),
                   from_obj=speaker)

  if location and msg_location:
    location_mapping = {
      "self": speaker.get_self_pronoun(location, **kwargs),
      "object": speaker, "location": location,
      "all_receivers": ", ".join(str(r) for r in receivers) if receivers else None,
      "receiver": None, "speech": message,
    }
    exclude = [speaker] if msg_self else []
    if receivers: exclude.extend(receivers)
    location.msg_contents(text=(msg_location, {"type": msg_type}),
                          from_obj=speaker, exclude=exclude,
                          mapping=location_mapping)
```

Each branch calls `get_display_name` and `get_self_pronoun` from
the PERSPECTIVE of the eventual viewer (self / receiver / location).
This is the only place in the typeclass surface where a hook is
called multiple times per event with different `looker` semantics;
see §6.

Templates resolved by `get_say_template_self`,
`get_say_template_location`, `get_say_template_receivers`
(appearance.py:684, :704, :723) are state-query hooks returning
`str.format`-ready strings. The bool-or-string `msg_self` kwarg of
`at_say` overloads the parameter shape (see §3, §6).

### 2.12 Object interactions

Three matching pre/post pairs on `AppearanceMixin`:

```
getter.get(obj):
  obj.at_pre_get(getter)      ─── falsy-not-None? abort
  (call site moves obj into getter via move_to; §2.5)
  obj.at_get(getter)
```

```
giver.give(obj, getter):
  obj.at_pre_give(giver, getter)   ─── falsy-not-None? abort
  (call site moves obj into getter via move_to)
  obj.at_give(giver, getter)
```

```
dropper.drop(obj):
  obj.at_pre_drop(dropper)         ─── falsy-not-None? abort
  (call site moves obj out of dropper, typically to dropper.location)
  obj.at_drop(dropper)
```

All six hooks are public override points. The fork's default
implementations are stubs (empty/`pass`). The interaction commands
themselves (`get`, `give`, `drop`) live in the default cmdset; they
own the call sites for these hooks. The naming inconsistency
(`at_get` vs `at_post_get`) is documented in §6.

### 2.13 Description set

Driver: `AppearanceMixin.at_desc()` (mixins/appearance.py:504),
called from `at_look` after `return_appearance` (§2.10). Also fired
by some game commands that "describe" an object without going
through the look chain.

```
target.at_desc(looker=caller, **kwargs)
```

The name is misleading: `at_desc` is "called whenever someone looks
at this object", not "called when this object's description is set".
There is no `at_pre_desc` or `at_post_desc`. See §6.

### 2.14 Account login chain

Driver: `SessionHandler.login()` (server/sessionhandler.py:498
onward).

```
sessionhandler.login(session, account)
  ├─ account.is_connected = True
  ├─ session.at_login(account)
  │     ├─ session.account = account
  │     ├─ session.uid / uname / logged_in / conn_time set
  │     ├─ session.cmdset_storage = CMDSET_SESSION
  │     └─ session.cmdset = CmdSetHandler(session, True)
  ├─ account.at_post_load()
  ├─ if account.db.FIRST_LOGIN:
  │     account.at_first_login()
  │     del account.db.FIRST_LOGIN
  ├─ account.at_pre_login()             [no veto contract; see §6]
  ├─ MULTISESSION_MODE==0: disconnect duplicate sessions
  ├─ session.logged_in = True
  ├─ portal AMP sync
  ├─ account.at_post_login(session=session)
  ├─ if nsess < 2: SIGNAL_ACCOUNT_POST_FIRST_LOGIN.send(...)
  └─ SIGNAL_ACCOUNT_POST_LOGIN.send(...)
```

Ordering quirks:

- `at_first_login` fires BEFORE `at_pre_login`. The name suggests
  "first login", which it is, but readers expect `at_pre_*` to fire
  before any `at_<event>` for the same event. Documented at
  accounts.py:1714. See §6.
- `at_pre_login` is named `at_pre_*` but does not honor a veto
  return; the engine ignores its return value and proceeds to
  `at_post_login` unconditionally. See §6.
- `at_post_load` here is NOT the cache-load hook (§2.2): same name,
  separately overridden on `DefaultAccount` (accounts.py:1590) as a
  stub. Sharing the name is intentional (it's the same idmapper
  hook, called from login because the account may be cold).

`at_failed_login(session)` fires from the auth path
(accounts.py:833) when credentials are rejected; it does not feed
the chain above.

### 2.15 Account creation and password change

```
create_account(...)
  └─ account.at_first_save()                      [accounts.py:1609]
       ├─ account.basetype_setup()                 (lockstring, CMDSET_ACCOUNT)
       ├─ account.at_account_creation()           [PUBLIC OVERRIDE POINT]
       ├─ account.init_evennia_properties()
       ├─ (if _createdict) apply create kwargs
       └─ permissions.batch_add(...)

account.set_password(new_password)
  └─ account.at_password_change()                 [stub by default]

account.create_character(...)
  └─ create_object(...)
  └─ account.at_post_create_character(character, ip=...)
                                                  [accounts.py:1042; stub]
```

`at_account_creation` mirrors `at_object_creation` (§2.1) in
position and semantics. The Account does NOT have an
`at_account_post_creation` analog; account-side post-init lives in
`basetype_setup` and there is no public override point between
"locks set" and `init_evennia_properties`. See §6.

`PlayableCharactersList` (accounts.py:155, :167) is a custom list
type that fires `at_post_add_character` / `at_post_remove_character`
when items are appended/removed:

```
account.characters.append(char)
  └─ account.at_post_add_character(char)

account.characters.remove(char)
  └─ account.at_post_remove_character(char)
```

These are DIFFERENT from `at_puppet_added` / `at_puppet_removed`
(§2.7, §2.8). The pair `at_post_add_character` / `at_post_remove_character`
fires on the persistent characters list ("which characters does this
account have access to"); the pair `at_puppet_added` /
`at_puppet_removed` fires on the live puppet set ("which characters
is this account currently controlling"). The four hooks share a
naming convention but track orthogonal state.

### 2.16 Account msg routing

```
account.msg(text, from_obj, session, options, **kwargs)
  ├─ account.at_msg_send(text=text, to_obj=account, **kwargs)  (pre)
  │     [accounts.py:1907; stub; falsy-not-None aborts the send]
  └─ session(s).data_out(...)
        recipient.at_msg_receive(text=text, from_obj=from_obj, **kwargs)
        [accounts.py:1877; falsy-not-None aborts delivery]
```

Notes:

- The pair has reversed prefix order vs. other pre/post pairs:
  `at_msg_send` is the pre-hook (on the sender) and `at_msg_receive`
  is per-recipient. Neither is named `at_pre_*`. Both honor a veto
  return. See §6.
- These hooks exist on Account but not on Object. Objects that
  receive `msg()` go through the inherited `ObjectDB.msg()` which
  does NOT fire these hooks. See §6.

`Account.at_look(target=characters, session=...)` (accounts.py:1961)
is the OOC look hook called from the `look` command when no puppet
is attached. It composes a character picker; the chain it drives is
distinct from §2.10 even though it shares the hook name.

### 2.17 Channel send / receive

Driver: `DefaultChannel.msg()` (comms/comms.py:611).

```
channel.msg(message, senders=..., bypass_mute=..., **kwargs)
  ├─ message = resolve_transform(
  │     channel.at_pre_msg(message, **send_kwargs), message
  │   )
  │   [comms.py:578; transform rule: falsy-not-None aborts;
  │    None/True returns original; non-string truthy returns original;
  │    string returns the new value]
  │
  ├─ for receiver in subscribers (filtered for muted):
  │     recv_message = resolve_transform(
  │       receiver.at_pre_channel_msg(message, channel, **send_kwargs),
  │       message
  │     )
  │     [accounts.py:1329; per-recipient transform; same rule]
  │     receiver.channel_msg(recv_message, channel, **send_kwargs)
  │     receiver.at_post_channel_msg(recv_message, channel, **send_kwargs)
  │     [accounts.py:1411; post-hook, return ignored]
  │
  └─ channel.at_post_msg(message, **send_kwargs)
     [comms.py:691; post-hook, return ignored]
```

The channel-level chain uses the transform rule (`resolve_transform`):
a hook may return a modified message; falsy-not-None aborts; `None`
returns the original. This is a SUPERSET of the veto contract used
by `at_pre_move` and friends. Documented in
[`engine-boundary-migration-archive.md`](engine-boundary-migration-archive.md)
Bundle 1.5.

The `channel_msg` call inside the loop is the delivery primitive
(it routes to `Account.msg()` and onward to `Object`/Session). The
DOCSTRING at comms.py:636 incorrectly names this hook
`at_channel_msg`; no such method exists. See §6.

### 2.18 Channel lifecycle

```
create_channel(...)
  └─ channel.at_first_save()                      [comms.py:122]
       ├─ channel.basetype_setup()                 (lockstring, log rotation)
       │     └─ channel.get_log_filename()         (read-only)
       ├─ channel.at_channel_creation()           [PUBLIC OVERRIDE POINT]
       ├─ channel.init_evennia_properties()
       └─ (if _createdict) apply create kwargs
```

`get_log_filename` is consulted from `basetype_setup` (during
`at_first_save`) and on every log write; overriders should keep it
deterministic per channel. The cached `_log_file` class attribute is
populated lazily on first call.

### 2.19 Script lifecycle

Driver: `DefaultScript`. Two creation paths
(`evennia.create_script` and `ScriptDB.objects.create`), both ending
in `at_first_save`.

```
create_script(...)
  └─ script.at_first_save()                       [scripts.py:435]
       ├─ script.basetype_setup()                  (stub by default)
       ├─ script.at_script_creation()             [PUBLIC OVERRIDE POINT]
       ├─ script.init_evennia_properties()
       └─ (if _createdict) apply kwargs
            └─ if cdict.autostart: script._start_task(force_restart=True)
                  └─ script.at_start()
```

Timer chain:

```
script.start(interval, start_delay, repeats, **kwargs)
  └─ _start_task → at_start(**kwargs)

(every interval seconds, while running):
  └─ at_repeat(**kwargs)
       (preceded internally by is_valid() check;
        is_valid() returning False stops the timer without firing at_repeat)

script.pause(manual_pause=True)
  └─ at_pause(manual_pause=True)

script.unpause()
  └─ at_start(**kwargs)   (re-fires; at_start is also the "resume" hook)

script.stop()
  └─ at_stop(**kwargs)
```

Delete:

```
script.delete()
  ├─ script.at_script_delete()       ─── False?  ─→ abort
  ├─ _stop_task()                    (without firing at_stop;
  │                                   delete bypasses the stop hook)
  └─ super().delete()
```

`at_script_delete` mirrors `at_object_delete` (§2.3) in naming
inconsistency: the name suggests post-event, but it is a veto pre-hook.
See §6.

`at_start` is the resume hook AS WELL as the initial-start hook;
there is no separate `at_resume`. After a server reload, persistent
timed scripts replay through `at_start` rather than restoring mid-
interval state. Pause-from-reload is signaled via
`manual_pause=False` in `at_pause`, distinguishing it from a
user-initiated pause.

### 2.20 Server lifecycle

Driver: `evennia/server/service.py` reload/start/shutdown paths.

```
reload sequence:
  ├─ ObjectDB.get_all_cached_instances():
  │     for each obj: obj.at_server_reload()      [service.py:598]
  ├─ AccountDB.get_all_cached_instances():
  │     for each acct: acct.at_server_reload()    [service.py:600]
  └─ for each script: script.at_server_reload()   [service.py:608]

full shutdown sequence:
  ├─ obj.at_server_shutdown()                     [analogous]
  ├─ acct.at_server_shutdown()
  └─ script.at_server_shutdown()

post-start (scripts only):
  └─ script.at_server_start()                     [scripts.py:916]
```

`at_server_reload_start` / `at_server_reload_stop` are GAME-side
hooks (in `server/conf/at_server_startstop.py`, defined per game)
called by `service.py:729` and `:787`; they fire ONCE per reload,
not per-instance. The pluralized per-instance versions
(`at_server_reload` on Object/Account/Script) fire ONCE PER CACHED
INSTANCE.

`at_idmapper_flush` (typeclasses/models.py:501) fires when the
idmapper drops the object from cache. Its return value controls
flush behavior: `True` allows the normal flush; `False` declines
the flush and the object stays cached. Default returns `False` when
the object has non-persistent attributes (which would otherwise be
lost). This is the ONLY hook in the surface whose return value gates
cache eviction; see §6.

### 2.21 Cmdset assembly (cross-reference)

`at_cmdset_get`, `get_cmdsets`, `get_cmdset_providers` participate
in cmdset assembly. The hook order is owned by
[`command-system.md`](command-system.md); see that doc for
`at_pre_parse`, `at_pre_cmd`, `at_post_cmd`, the AccountCommand
normalization table, and merge-cache invariants.

Calling sites in the typeclasses:

- `LifecycleMixin.at_cmdset_get` (mixins/lifecycle.py:406): default
  stub. Override point for last-second mutation of an object's
  merged cmdset.
- `LifecycleMixin.get_cmdsets` (mixins/lifecycle.py:423): default
  returns the cmdset stack. The merge happens in
  `evennia/commands/cmdsethandler.py`.
- `DefaultObject.get_cmdset_providers` (objects/object.py:467):
  returns the provider dict consumed by the cmdhandler
  (`object`, `account`, `session` keys). See `command-system.md`
  "Session-proxy contract".
- `DefaultAccount.get_cmdset_providers` (accounts.py:353): analog
  for the Account side.
- `ServerSession.get_cmdset_providers` (serversession.py:72):
  session-side. The `cmdhandler` duck-types this method on whatever
  is passed as `session=...`.
- `DefaultExit.at_cmdset_get` (objects/exit.py:241): rebuilds the
  exit's default cmdset on demand.

### 2.22 Access checks

Driver: `LifecycleMixin.access()` (mixins/lifecycle.py:221).

```
obj.access(accessing_obj, access_type, default, no_superuser_bypass, **kwargs)
  ├─ result = super().access(accessing_obj, access_type, ...)
  │     (TypedObject.access; consults lock handler)
  ├─ obj.at_access(result, accessing_obj, access_type, **kwargs)
  │     [LifecycleMixin.at_access; mixins/lifecycle.py:542]
  └─ return result
```

`at_access` fires after the lock result is computed and BEFORE
`access()` returns. Its return value is ignored (so it cannot
change the access decision); it's a notification hook for logging,
trace, or analytics. See §6 for the naming concern (the prefix
suggests pre/post but it is strictly post).

Account `at_access` (accounts.py:1655) is the analog for
account-side access checks.

### 2.23 Search pipeline

Driver: `SearchMixin` (objects/mixins/search.py). Override points
inside the object's `search()` and `account.search_account()`
implementations:

```
self.search(query, **kwargs)
  ├─ query = self.get_search_query_replacement(query, **kwargs)
  │     [search.py:19; nick / alias expansion; returns possibly-modified query]
  ├─ direct = self.get_search_direct_match(query, **kwargs)
  │     [search.py:38; "is this a dbref / self / here / me"; returns Object or None]
  │     ─→ if direct is not None: return direct (short-circuit)
  ├─ candidates = self.get_search_candidates(query, **kwargs)
  │     [search.py:64; returns iterable of candidates to score]
  ├─ result = self.get_search_result(query, candidates, **kwargs)
  │     [search.py:121; ranks/filters/returns Object or iterable]
  └─ return self.get_stacked_results(result, **kwargs)
        [search.py:162; applies stacking rules; returns final result]
```

All five hooks are public override points. The pipeline is the
primary customization surface for search behavior; overriding
`search()` itself is supported but rarely necessary.

### 2.24 Container copy

```
self.copy(new_key=None, **kwargs)
  ├─ new_obj = ObjectDB.objects.copy_object(self, new_key=..., **kwargs)
  └─ self.at_object_post_copy(new_obj, **kwargs)
                                         [mixins/lifecycle.py:148; stub]
```

`at_object_post_copy` is called on the SOURCE object, not the new
copy. Its purpose is to migrate state not handled by
`copy_object` itself (e.g. handler-managed data). No `at_pre_copy`
exists.

## §6. Misshapen hooks

Running tally maintained while writing §1 to §5. Each entry names
a hook (or pair), the issue, and the section where the symptom
surfaces. These are inputs to Phase C and to H1; H1 should NOT
register the current shape verbatim.

### Naming / semantics mismatches

- **`at_object_delete`** (§2.3) is a veto pre-hook (returning
  `False` aborts) but is named without the `at_pre_` prefix. Should
  be `at_pre_delete`. The `at_<event>` form should be a notification
  or composite, not a veto.
- **`at_script_delete`** (§2.19) has the same shape mismatch as
  `at_object_delete`.
- **`at_pre_unpuppet`** (§2.8) has the `at_pre_*` name but the
  engine never honors its return value as a veto. Either the engine
  should consult `is_veto` on its return (matching the prefix) or
  the hook should be renamed `at_unpuppet` to clarify it is a
  notification.
- **`at_pre_login`** (§2.14) is named `at_pre_*` but its return
  value is ignored. Same fix as `at_pre_unpuppet`: honor the veto
  or rename.
- **`at_first_login`** (§2.14) fires BEFORE `at_pre_login` despite
  conventional ordering (`at_pre_*` before any `at_<event>` for
  the same event-family). Either rename to make the order obvious
  (`at_account_first_login`?) or rewrite the call sequence so
  `at_pre_login` truly leads.
- **`at_access`** (§2.22) is a post-event notification; the prefix
  `at_<event>` without `pre`/`post` reads as composite/lifecycle.
  Rename to `at_post_access`.
- **`at_desc`** (§2.13) is named as if it were a post-event hook
  for setting a description, but it fires on every look. Either
  rename (`at_being_looked_at`?) or move the look-time semantics
  into `at_look` / `return_appearance`.
- **`at_get` / `at_give` / `at_drop`** (§2.12) lack the `at_post_`
  prefix despite being notifications. The pre-hooks are correctly
  `at_pre_get` / `at_pre_give` / `at_pre_drop`. Renaming to
  `at_post_get` / `at_post_give` / `at_post_drop` would make the
  pairs symmetric.
- **`at_msg_send` / `at_msg_receive`** (§2.16) are veto-on-falsy
  hooks named without the `at_pre_*` prefix despite firing before
  send/delivery. The send/receive pair is a route, not a pre/post
  bracket; consider `at_pre_msg_out` / `at_pre_msg_in`.
- **`return_appearance`** (§2.10) uses the `return_*` prefix; no
  other in-scope hook does. New composite renderers should use a
  consistent prefix (or fold the slot composition into the registry
  schema).

### Signature drift across same-named hooks

- **`at_first_save`** has three definitions with different
  signatures: `LifecycleMixin.at_first_save(self)`,
  `DefaultAccount.at_first_save(self)`,
  `DefaultChannel.at_first_save(self)`,
  `DefaultScript.at_first_save(self, **kwargs)`. The Script version
  takes `**kwargs`; the others don't. Either standardize on
  `**kwargs` (allows future extension) or document that the Script
  version is intentionally distinct.
- **`at_post_unpuppet`** on `LifecycleMixin` has signature
  `(self, account=None, session=None, **kwargs)`; the override on
  `Character` (objects/character.py:298) matches, but the call site
  (`account.unpuppet_object`) passes positional `(self, session=...)`,
  not kwarg `account=`. The signature appears correct only because
  Python coerces positional to keyword. Consider explicit positional
  in the signature so the contract is obvious.
- **`at_object_creation` vs `at_account_creation` vs
  `at_channel_creation` vs `at_script_creation`**: parallel hooks,
  but Account lacks the `at_<noun>_post_creation` analog that
  Object has (§2.15). Either add it on Account/Channel/Script for
  symmetry, or document the asymmetry.

### Defined but never called

- **`get_return_exit`** on `DefaultExit` (exit.py:319): no callers
  in the engine, but has dedicated test coverage in
  `evennia/objects/tests/test_objects.py:106`. Reclassified to H
  (defer to H1): tested public surface counts as API by intent;
  H1 should decide whether this belongs in the engine API.
- ~~**`get_content_names`** on `AppearanceMixin` (appearance.py:415)~~:
  deleted as part of §7 D-bucket. Docstring said DEPRECATED; zero
  callers in engine, tests, or game tree.
- ~~**`get_visible_contents`** on `AppearanceMixin` (appearance.py:388)~~:
  deleted alongside `get_content_names` (its only caller).
- ~~**`get_puppet_or_account`** on `ServerSession` (serversession.py:207)~~:
  deleted as part of §7 D-bucket. One-line method, zero callers.

### Implicit coupling not enforced by signature

- **`appearance_template` slots vs `get_display_*` methods**
  (§2.10): the template is a `str.format` string with named slots
  (`{name}`, `{desc}`, `{exits}`, etc.); the provider methods are
  named to match the slots. Adding a new slot requires editing both
  the template and adding a method; the type system does not
  enforce the match. A schema-driven H1 should make this explicit
  (template slot → declared provider).
- **`at_channel_msg` named in a docstring but absent from code**
  (§2.17): comms.py:636 documents the per-receiver delivery hook
  as `at_channel_msg`; the actual call is `channel_msg` (no
  `at_` prefix). One of the two needs to change.
- **`get_say_template_*` plus `at_say` `msg_self` overload**
  (§2.11): the `msg_self` parameter is bool-or-string. When True,
  the template lookup runs; when a string, it's used verbatim. This
  is a poly-typed parameter; a clearer shape splits "use template"
  from "use this string".

### Conditional / partial fire

- **`at_object_post_spawn`** (§2.1): fires only when the spawner
  creates/updates the object. Objects created via
  `evennia.create_object` directly do NOT fire it. The naming
  doesn't suggest the spawner-only constraint. Either rename
  (`at_prototype_spawn`?) or also fire from `create_object` for
  consistency.
- **`at_post_arrive` / `at_post_move` at first placement** (§2.1):
  these fire ONLY when `_createdict` supplies a location at
  creation time. The pre/leave halves of the move chain are
  skipped (no source to leave from). Calling code that uniformly
  expects "every move fires every move-chain hook" will miss this
  edge case.
- **`at_start` is also the resume hook** (§2.19): no separate
  `at_resume`. Game overrides that want "fire only on first start,
  not on reload-resume" must track that themselves.
- **`at_pause(manual_pause=...)`** (§2.19): the kwarg
  distinguishes user-pause from reload-pause. Hidden control flow
  via a flag rather than separate hooks.

### Hook fires multiple times per event with different perspective

- **`get_display_name` / `get_self_pronoun`** inside `at_say`
  (§2.11): each broadcast computes these from the perspective of
  the eventual viewer (self / each receiver / location). One "say"
  fires N display-name lookups where N is `1 + len(receivers) + 1`.
  Override authors who do expensive work in `get_display_name`
  should know this. Documented as a contract, not a bug, but worth
  flagging for H1's docs.

### Error handling swallows in chain

- **`MovementMixin.move_to`** (§2.5): wraps every hook call in
  try/except and continues on error. Hook errors do not abort the
  move. This is intentional isolation, but overrides that rely on
  hook-side veto-by-exception will be surprised.
- **`Account.puppet_object`** (§2.7): `at_puppet_added` errors are
  log-traced and swallowed; the puppet attach still succeeds. Same
  pattern, same caveat.

### Cross-class hook split

- **Rename hooks** (§2.4): `at_pre_rename` defined on `TypedObject`
  (typeclasses/models.py:910), `at_rename` defined on BOTH
  `TypedObject` (models.py:931) and `AppearanceMixin`
  (appearance.py:907). MRO resolves to the mixin for Objects, the
  base for everything else. Two implementations of the same hook
  is a maintenance hazard.
- **`at_look` overloaded across Object and Account** (§2.10, §2.16):
  same name, different contract (single target vs character
  picker). Intentional duck-typing for OOC look, but worth
  flagging so H1's registry doesn't collapse the two into one
  entry.

### Set-membership vs lifecycle pair naming

- **`at_puppet_added` / `at_puppet_removed`** (§2.7, §2.8) vs
  **`at_post_add_character` / `at_post_remove_character`** (§2.15):
  parallel concepts (set-membership change notifications) named
  with two different conventions on the same class. Either pick
  `at_<set>_added` / `at_<set>_removed` everywhere or
  `at_post_add_<noun>` / `at_post_remove_<noun>` everywhere.

## §7. Cleanup triage

Disposition for each §6 entry. Single-fork, no deprecation cycle:
rename-only changes land as one engine + game commit. Buckets:

- **R**: pure rename (default body unchanged, no semantic shift). Do anytime.
- **S**: semantic fix (engine behavior change required). Pre-H1, since H1 should register the corrected shape, not the broken one.
- **D**: dead code. Delete.
- **H**: defer to H1 (registry-level decision; fixing in isolation prejudges schema).

### Bucket R: rename-only

| Entry | Action | Notes |
|---|---|---|
| `at_object_delete` | Rename → `at_pre_delete`. | Default returns True; veto contract preserved. |
| `at_script_delete` | Rename → `at_pre_delete` (Script version). | Same. |
| `at_access` | Rename → `at_post_access`. | Notification, not composite. Two definitions (Object and Account); rename both. |
| `at_get` / `at_give` / `at_drop` | Rename → `at_post_get` / `at_post_give` / `at_post_drop`. | Pre/post symmetry with existing `at_pre_*` halves. |
| `at_first_save` signature drift | Add `**kwargs` to `LifecycleMixin`, `DefaultAccount`, `DefaultChannel` versions. | Matches Script signature; allows future extension without re-touching every site. |
| `at_post_unpuppet` positional clarity | Make signature `(self, account, session=None, **kwargs)` (drop the `account=None` default). | Call site always passes account positionally; default exists only because nobody noticed. |
| `at_channel_msg` docstring | Fix `Channel.msg` docstring (comms.py:636) to name `channel_msg`, not `at_channel_msg`. | Docstring lie. |
| `at_post_arrive`/`at_post_move` at first placement | Doc-only. Update §2.1 in this doc with a "first-placement skips pre/leave" note (already present). No code change. | Already documented; flag as resolved. |
| `get_display_name` / `get_self_pronoun` fan-out in `at_say` | Doc-only. The per-perspective fan-out is intentional; flag the perf contract in the override-discipline table. | Already in §4.5. |
| `move_to` swallows hook errors | Doc-only. Behavior is intentional. | Already in §2.5 and §4.2. |
| `puppet_object` swallows `at_puppet_added` errors | Doc-only. Behavior is intentional. | Already in §2.7. |
| `at_rename` double-definition | Delete `TypedObject.at_rename` (typeclasses/models.py:931); keep `AppearanceMixin.at_rename`. | The TypedObject version is unreachable for Object/Character because MRO resolves to the mixin. Account/Channel/Script never call `at_rename` today, so removal is safe. Verify by grep before deleting. |
| `at_puppet_added` / `at_puppet_removed` vs `at_post_add_character` / `at_post_remove_character` | Rename the character-list pair → `at_character_added` / `at_character_removed`. | Aligns the two pairs on the `at_<set>_added/removed` convention. The puppet pair already uses it. |

### Bucket S: semantic fix

| Entry | Action | Notes |
|---|---|---|
| `at_pre_unpuppet` | Honor the veto: `Account.unpuppet_object` should `is_veto`-check its return and abort detach. | The name already promises this. Game-side overrides currently see returns ignored; honoring the promise is what overriders expect. |
| `at_pre_login` | Honor the veto: `SessionHandler.login` should `is_veto`-check its return and abort. Decide what happens on abort (drop connection? send to OOC?). | Less clear-cut than `at_pre_unpuppet`; needs a call about login-abort UX before implementing. |
| `at_first_login` ordering | Move the call AFTER `at_pre_login`. | Today: `at_first_login` → `at_pre_login` → `at_post_login`. Target: `at_pre_login` → `at_first_login` → `at_post_login`. The "first time" semantic is preserved (still gated on `db.FIRST_LOGIN`), but the ordering finally matches the prefix convention. |
| `at_msg_send` / `at_msg_receive` route | Rename + extend to Object. Today: Account-only, named without `at_pre_*`. Target: `at_pre_msg_out` (on sender) and `at_pre_msg_in` (on recipient), defined on both `DefaultObject` and `DefaultAccount`. Same veto-on-falsy contract. | Touching both account.msg and object.msg; medium churn. Punt to immediately after H1 if H1 wants to redefine message routing anyway. |
| `at_object_post_spawn` spawner-only fire | Rename → `at_prototype_spawn`. Don't extend to `create_object`. | The hook's semantic IS "after the spawner did its work"; widening it confuses the meaning. Rename clarifies. |
| `at_start` is also resume | Add `at_resume(**kwargs)`. Default body in `DefaultScript` calls `self.at_start(**kwargs)` so existing overrides still fire. Documented as: "override `at_resume` for resume-only logic; override `at_start` for initial-start-only logic; the default chains them." | Optional. If nobody currently distinguishes the two cases in this fork, leave alone. |
| `at_pause(manual_pause=...)` flag | Leave alone. Splitting into separate hooks (`at_pause_manual` / `at_pause_reload`) doubles the override surface for a binary signal. Doc the flag clearly. | Doc-only; reclassify to R. |
| `at_say` `msg_self` bool-or-string | Split parameter. `msg_self: bool` controls echo; `msg_self_template: str | None` overrides the template. Default behavior preserved. | Touches `at_say` signature; coordinate with overriders in the game. |

### Bucket D: dead code

Shipped. Engine + game-tree (`../newmoo`) grep confirmed zero
callers; tests grepped and found callers only for `get_return_exit`,
which was reclassified to H.

| Entry | Action | Notes |
|---|---|---|
| ~~`get_return_exit` (exit.py:319)~~ | Reclassified → H. | Test coverage exists in `test_objects.py:106`. Tested public surface is API by intent; H1 decides whether this belongs in the engine API. |
| `get_content_names` (appearance.py:415) | Deleted. | Docstring said DEPRECATED; zero callers. |
| `get_visible_contents` (appearance.py:388) | Deleted (with `get_content_names`). | Only called from `get_content_names`; transitively dead. |
| `get_puppet_or_account` (serversession.py:207) | Deleted. | Zero callers anywhere. |

### Bucket H: defer to H1

| Entry | Why deferred |
|---|---|
| `at_desc` (fires on look, not on description set) | The name is misleading but the semantic is well-defined and game-side overrides commonly use it. Renaming risks churn that H1 should drive: H1 will register hooks under their canonical names, and that's the moment to rename. |
| `return_appearance` prefix uniqueness | H1 will define the prefix convention for composite renderers; renaming `return_appearance` in isolation prejudges that decision. |
| `at_<noun>_creation` lacks `_post_creation` analog on Account/Channel/Script | Symmetry across creation chains is an H1 schema concern. Adding the analog now means committing to a contract that H1 may want to shape differently. |
| `appearance_template` ↔ `get_display_*` implicit coupling | H1's hook registry should make the template-slot ↔ provider-method relationship declarative. Manually fixing the coupling pre-H1 is wasted work. |
| `at_look` overloaded on Object vs Account | H1 needs to decide whether overloaded names collapse to one registry entry or stay distinct; the renamer follows that decision. |
| `get_return_exit` (exit.py:319) | No engine callers but has dedicated test coverage. H1 decides whether to register, deprecate, or hoist game-side. |

### Suggested execution order

1. **D bucket first** (one PR). Smallest blast radius; clears noise from the surface before any renames. Pre-deletion grep across the game tree.
2. **R bucket as one PR per file or one PR total.** Pure mechanical renames + signature additions; easy to review. Hold off on the `at_rename` double-definition removal until you've grepped for engine-side and game-side callers (low risk but worth confirming).
3. **S bucket selectively**:
   - `at_first_login` reorder + the `at_pre_unpuppet` / `at_pre_login` veto honoring can go together as a "promise-keeping" PR.
   - `at_msg_send`/`at_msg_receive` redesign waits until H1 has touched message routing.
   - `at_object_post_spawn` → `at_prototype_spawn` rename can fold into the R bucket if you commit to the rename.
   - `at_say` `msg_self` split waits if no game-side code currently misuses the overload.
4. **H bucket: nothing now.** Flag these as H1 inputs in the architecture doc.

The R+D buckets together are probably 100 to 200 LOC of mechanical change. The S bucket is medium churn but each item is self-contained.
