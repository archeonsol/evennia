# Fork changelog

This file tracks divergences in this fork from upstream Evennia. Upstream's
own `CHANGELOG.md` is preserved unchanged; this is the fork-only history.

Versions use [PEP 440 local segments](https://peps.python.org/pep-0440/#local-version-identifiers):
`<upstream-version>+underspire.<n>`, where the trailing integer increments
on each tagged fork release. The `+local` suffix means `pip` will install
this build correctly and it will never collide with a published upstream
release of the same base version.

Read `evennia.__version__` at runtime; it returns the full string with the
current git rev appended.

## Maintenance

**Don't compress individual release entries.** Forensic value ("why does X
work this way?") comes from the original detail; compression destroys it.
Git history and `grep` cover any browsing needs.

**Rotation policy: by upstream major, not by file size.** When this document hits 1000 lines, delete old entries until it is under the limit again.

See [`.agents/docs/releases.md`](.agents/docs/releases.md) for the
matching release procedure.

---

## 6.0.0+underspire.336: Admin verbs check their own keys

`engine.world.build` gated `@force`, `@emit`, `@policy` and `@scope` as well as building, and
`engine.moderation.manage` gated `@wall` and `@userpassword`. A builder limited to one area
could still rewrite who may edit what, and every moderator could reset passwords. Each of
those verbs now checks a key of its own.

### Authorization

- **New keys** ([`capabilities.py`](evennia/authorization/capabilities.py)):

  | Key | Gates |
  |---|---|
  | `engine.object.force` | `@force` |
  | `engine.authorization.manage` | `@policy`, `@scope` |
  | `engine.world.announce` | `@emit`, `@remit`, `@pemit`, `@wall` |
  | `engine.account.password` | `@userpassword` |
  | `engine.world.search` | matching a `#dbref` outside the searcher's reach |

- **`engine.world.build` admits the building verbs only.** Each object's own policy
  (`edit`, `delete` and the rest) still decides which objects a builder may change, so a
  builder can be limited to an area with label-scoped object grants.
- **Bundles:** `world_builder` adds `engine.world.announce` and `engine.world.search`.
  `moderator` adds `engine.world.announce` and `engine.account.password`.
  `runtime_operator` adds all five. `engine.object.force` and `engine.authorization.manage`
  are in `runtime_operator` only.

### Migration

- **`server.0013_split_engine_keys`** grants the new keys to every principal with an active
  `engine.world.build` grant (force, authorization, announce, search) or
  `engine.moderation.manage` grant (announce, password). Each new grant copies its source's
  scope, constraints and expiry, and gets a `grant_created` audit event from
  `system:migration`. A key that one source gives is a child of it, so revoking the source
  revokes the key. A key that two sources give (an admin's build and moderation grants both
  give announce) is a grant of its own with the expiry of the longer-lasting source, so
  revoking one source keeps it. Reversing the migration deletes the grants it made and every
  grant delegated from them.
- **A delegated `engine.world.build` grant gives only `engine.world.search`.** The other new
  keys cannot be delegated, so a builder who held build by delegation loses `@force`,
  `@policy`, `@scope` and `@emit`. Every other holder keeps every verb; revoke the new keys
  by hand where a builder should not keep them.

### Fixes to underspire.335

- **The check that refuses a higher target compares world authority**
  (`capabilities_beyond` in [`service.py`](evennia/authorization/service.py), with the new
  `world_capabilities` and `principal_is_superuser` in
  [`storage.py`](evennia/authorization/storage.py)):
  - Only grants at `world:*` count. A grant for one area or one message gives no rank, so a
    player who has paged someone, or a builder of one area, no longer blocks `@force`.
  - A character nobody puppets counts its owner account. A builder could `@force` a
    logged-off admin's character, and the command ran with the admin's account grants.
  - An account counts grants on the characters it owns, on both sides. A moderator could
    reset the password of an admin whose grants sat on their character.
  - The caller counts what it can use when it acts: its live driver, never its body's
    owner, and only grants without constraints. A world grant limited by a label, a time
    window or a session counted as a full one.
  - A Django superuser outranks an account that is not one (`"superuser"` in the result).
  - World break-glass without constraints covers everything.
- **Revoking a grant reaches every generation delegated from it**
  ([`storage.py`](evennia/authorization/storage.py)). It stopped at eight.
- **Refreshing a grant carries its expiry and constraints to the keys split from it**: every
  live child grant held by the same principal. Extending or narrowing build or moderation
  now changes the keys the migration split from it. A grant delegated to someone else keeps
  its own terms.
- **A line break in a verb's arguments becomes a space** (`fold_line_breaks` in
  [`parser.py`](evennia/actions/parser.py)): a typed newline, vertical tab, form feed or the
  `|/` markup. 335 let them through to every verb, so `say hi|/A tall man says...` showed a
  second line as if someone else had spoken it. `||/` stays literal. An action that needs
  line breaks sets `keeps_newlines = True` ([`action.py`](evennia/actions/action.py));
  `@py`, `@sethelp`, `@set`, `@emit` and `@wall` do.
- **A prompt answer is one line** ([`menus.py`](evennia/actions/menus.py)). `get_input` and
  `yield` prompts fold line breaks the same way. The line editor and the pager do not.
- **`@set` view quotes a string that would paste back as something else**
  (`pasteable` in [`attributes.py`](evennia/actions/default/building/attributes.py)):
  `'123'`, `'None'`, `'$dbref(#1)'`, an empty string, and text with edge spaces or newlines.
- **String attributes** (`strattr=True`) show in `@set` and `/edit`, `@cpattr` copies them,
  and `/edit` saves them as string attributes. Before, each read them as `None`.
- **`@cpattr` reads every source before it writes** (`stored_slot` in
  [`attributes.py`](evennia/actions/default/building/attributes.py)). A swap
  (`@cpattr/move Obj/x/y = Obj/y/x`) or a chain (`a/b = b/c`) lost a value.
- **`@set` shows bytes as `b'...'` and an infinity as `1e999`**, which paste back as the
  same value, also inside a list, tuple, set or dict.
- **`@cpattr/move` to the same slot in another category case keeps the value.** Categories
  are lowercased when parsed, as the attribute handler stores them.
- **The `@set/edit` conversion prompt continues only on Enter, `y` or `yes`.**
- **The `@nick/list` table shows stored markup as typed, and its columns line up.**
- **`@emit/r` means `/room` again.** `/rooms` made the prefix ambiguous.
- **The audit log masks `@us`, `@user` and `@pas`, with or without a switch.** The
  typo-tolerant `create` masks need a leading `c`, so `treat` and `trace` are not masked.
  `mask_sensitive_input(..., logged_in=True)` skips the `connect` and `create` masks, which
  only matter before login; the fail-closed security log passes it for a logged-in actor,
  so an in-game `create place` is logged as typed.
- **`render_help` folds subtopic case itself**
  ([`renderer.py`](evennia/help/renderer.py)), so `help test/More` works without
  `CmdHelp.parse`.
- **A password set in-game before 335 with a run of spaces was saved with one space.** The
  player must type it with one space, or reset it.

### Known gaps

- `as_typed` escapes MXP links (`|lc...|lt...|le`) at the ANSI level only. The ANSI parser
  collapses the escape before MXP runs, so a stored link still renders as a link.
- With `FUNCPARSER_PARSE_OUTGOING_MESSAGES_ENABLED` on, the outgoing pass eats backslashes in
  a value `as_typed` shows.
- A key the migration gave as a grant of its own (two sources) keeps the expiry it got. A
  later change to either source does not reach it.

### Migration notes

- A game that grants `engine.world.build` to new staff and expects them to `@force`,
  `@policy`, `@scope` or `@emit` must now grant those keys too.
- A game that grants `engine.moderation.manage` and expects `@wall` or `@userpassword` must
  grant `engine.world.announce` or `engine.account.password`.
- A game whose verbs need line breaks (multi-line poses, descriptions, notes, code) sets
  `keeps_newlines = True` on those action classes. Without it, `|/` in their text becomes a
  space.
- `authority_scopes` is replaced by `world_capabilities` and `principal_is_superuser`.
- Dbref search with `use_dbref` unset checks `engine.world.search`, not
  `engine.world.build`.

## 6.0.0+underspire.335: Default actions match the old commands they replace

A review of every action that replaces a command in `evennia/commands/default/` found
security holes, silent data loss and behavior lost in the port. This release fixes them, and
ports the old command tests that had no action test, so that the old tree can be deleted.

### Security

- **`@force` and `@userpassword` refuse a target with more authority than the caller**
  ([`admin.py`](evennia/actions/default/admin.py),
  [`account.py`](evennia/actions/default/account.py)). A builder could `@force` an admin to
  `@grant` them `runtime_operator`, and a moderator could reset an operator's password. The
  new `capabilities_beyond(principal, other)`
  ([`service.py`](evennia/authorization/service.py)) returns the capabilities `principal`
  holds that `other` lacks, ignoring quell; both verbs refuse when it is not empty.
- **`@userpassword` works.** It crashed on every use (an Account has no `search_account`). It
  now searches accounts with `evennia.utils.search.search_account`, reports a missing or
  ambiguous name, and validates the new password against the target account.
- **`@password old = new,pass` sets `new,pass`.** It read the comma as a list separator.
- **The audit log masks passwords that contain symbols**, and abbreviated verbs
  (`AUDIT_MASKS` in [`settings_default.py`](evennia/settings_default.py)).

### Parser

- **Arguments keep the spacing they were typed with**
  ([`parser.py`](evennia/actions/parser.py)). The parser rebuilt arguments by joining split
  words, so `say` text lost its spacing, `@py` code lost indentation and string contents,
  `@sethelp` lost newlines, and a password with a double space could not be typed.
- **Switches are lowercased, and a unique prefix expands to the full switch**
  ([`muxargs.py`](evennia/actions/muxargs.py)). `ArgAction.switch_options` declares a verb's
  switches; `normalize_switches(switches, options)` does the work. Before, `@scripts/del`
  printed usage, `@option/SAVE` was ignored, `@tasks/<typo>` then `yes` raised a TypeError,
  and `@grant/remove` granted. `@tasks`, `@grant` and `@cpattr` now refuse an unknown switch.
- **`:` is a pose verb again** ([`roleplay.py`](evennia/actions/default/roleplay.py)).

### Building

- **No more silent data loss** ([`rules.py`](evennia/actions/default/building/rules.py),
  [`attributes.py`](evennia/actions/default/building/attributes.py)):
  - `@wipe obj:cat` wiped every attribute, and `@wipe obj/attr:cat` ignored the category.
  - `@cpattr/move` used to swap two attributes removed both.
  - `@alias/delete obj` with no name cleared every alias, and matched case-sensitively.
  - `@set` stored the error text as the value of an unparseable literal, crashed on a list
    index past the end, and aborted on Enter at a `[Y]/N` prompt.
- **Viewing an attribute with `@set <obj>/<attr>` escapes its markup and shows the whole value**
  ([`attributes.py`](evennia/actions/default/building/attributes.py)).
  `AttributeOperations.view_attr` escapes it with `as_typed` (below), as `@examine` does, but
  does not crop it. A value of `|rred|n $you()` now reads
  `|rred|n $you()`. Before, the client rendered the color and the outgoing funcparser filled in
  `$you()`, so a builder could not copy a colored desc to another object. `@examine` shows the
  same value cropped. Nested lookups (`obj/attr[0]`) are escaped the same way.
- **Stored markup shows as typed** in `@set`, its confirmation, `@examine`, `@cpattr` and
  `@nick`. The new `as_typed(value)` in `attributes.py` escapes color codes, and escapes
  `$func()` calls only when `FUNCPARSER_PARSE_OUTGOING_MESSAGES_ENABLED` is on, so a game with
  outgoing parsing off does not see `\$you()`. The `@nick/list` table still renders color:
  EvTable output is parsed again on its way out.
- **`@examine/script` and `@examine/channel` work.** Scripts and channels had no examine
  policy, and a missing policy denies; both now require `engine.object.examine`
  ([`scripts.py`](evennia/scripts/scripts.py), [`comms.py`](evennia/comms/comms.py)).
- **`@unlink` with no argument shows its own usage line.**

### Tests

- Action tests now cover what the old `evennia/commands/default/tests.py` checked for pose,
  `@nick`, get/drop/give counts, `@tasks`, `@py`, `@scripts`, `@policy`, `@examine`,
  `@alias`, `@copy`, `@wipe`, `@set`, `@cpattr`, `@link`, `@sethome` and help
  ([`help/tests/test_renderer.py`](evennia/help/tests/test_renderer.py)).

### Migration notes

- A game that registers its own `ArgAction` subclasses gets lowercased switches with no
  change. To get prefix expansion, declare `switch_options` on the class.
- A game that relied on arguments with collapsed whitespace must strip or split them itself.

## 6.0.0+underspire.334: A stall line names the system that held the reactor

Before this, a reactor stall named the frame the sampler caught, and that frame is often a bystander:
the cost sits earlier in the same body, or in a system that ran just before. Nothing timed a system
body, so a stall hunt on the live game on 2026-10-17 took hours.

### Systems

- **The scheduler times every system body**
  ([`systems.py`](evennia/utils/systems.py)). `SystemDriver` times each stretch of a system body that
  runs on the reactor, with the driver's own perf clock. A synchronous body is one turn. An `async def`
  body has one turn for each `await` it passes, and the time it spends suspended is not counted. A body
  that raises or is cancelled is timed too. A Deferred or Future a body returns is not timed past the
  call. The numbers stay on the `System`: `turns`, `busy_seconds`, `last_turn_seconds`,
  `worst_turn_seconds` and `slow_turns`. A fault in the timing itself is logged once and never reaches
  the body or the driver.
- **A slow turn is named.** A turn at or over `SYSTEM_BODY_WARN_MS` (100; 0 turns the line off) logs one
  line naming the system and the milliseconds, at most once every `SYSTEM_BODY_WARN_EVERY` seconds (60; 0
  logs every one) for each system, with how many turns went over since the last line.
- **`systems.running_body()`** returns `(name, seconds into the turn)` for the body on the reactor now.
  It reads one reference and is safe from any thread.
- **`@systems` shows the average and the worst turn.** Both listings of the scheduler, the `@systems`
  action ([`system.py`](evennia/actions/default/system.py)) and the older command
  ([`system.py`](evennia/commands/default/system.py)), get two columns, `avg ms` and `worst ms`, from
  `systems.describe_turns`. A system that has not run in this process shows dashes, and so does a listed
  object that lacks the numbers.

### Watchdog

- **A stall line names the system on the reactor**
  ([`reactor_watchdog.py`](evennia/utils/reactor_watchdog.py)). The watchdog reads `running_body()` at
  the same moment it reads the loop's frame: `at world/butcher/lair.py:149 residents (system
  'butcher-rite', 148ms into its turn)`. The milliseconds tell a body that caused the stall, which had
  been running about as long as the stall is long, from one that began late in it after something else
  held the loop. The system is named in the ordinary stall line, the collection line, the GIL line and
  the live stack sample, and only with a sampled place. A stall with no system body running reads as
  before.

### Metrics

- **`evennia_system_body_seconds{system}`**
  ([`prometheus_metrics.py`](evennia/server/prometheus_metrics.py)). A histogram of each turn. Its count
  is the turns and its sum is the time the system held the reactor. The buckets are the ones the GC pause
  histogram uses (12), so a system adds 14 series to `/metrics`.

### Settings

- `SYSTEM_BODY_WARN_MS = 100` and `SYSTEM_BODY_WARN_EVERY = 60` are declared in `settings_default.py`
  beside `SYSTEM_TICK_SLICE_MS`.

### Migration notes

None. The two settings have defaults, the new `@systems` columns need nothing from a third-party
`System`, and the console runtime panel is unchanged (it can read the new numbers off the `System`).

### Tests

`TestBodyTiming` in `evennia/utils/tests/test_systems.py`, `TestStallNamesTheSystem` in
`evennia/utils/tests/test_reactor_watchdog.py`, and one test for each `@systems` listing.

## 6.0.0+underspire.333: Legacy `@set` command shows an attribute's markup as it was set

### Commands

- **Viewing an attribute with the legacy `CmdSetAttribute` escapes its markup**
  ([`building.py`](evennia/commands/default/building.py)). `CmdSetAttribute.view_attr` passes the
  value through `ansi.raw` and an escaping funcparser pass, as `examine` already does. The `@set`
  action, which games on the action dispatch run, was not changed until underspire.335.
  Nested lookups (`obj/attr[0]`) are escaped the same way.

## 6.0.0+underspire.332: Fewer stalls and tracebacks on the live game

### Objects

- **A dbref that names nothing finds nothing** ([`manager.py`](evennia/objects/manager.py)).
  `search_object("#123")` for an object that was gone fell through to a search for the name
  `"#123"`: a scan of every object and every alias tag. On the live game that was 43 ms a call
  (154 at most), it never found anything, and it ran 79,485 times in 11 days: 38% of all database
  time. A miss, or a hit outside the candidates, now returns nothing after the one primary key read.
  `search_script` and `search_account` had the same fall-through. `use_dbref=False` still treats the
  text as a name.

### Actions

- **A rule whose provider changed class during the dispatch is skipped.** A character that dies
  becomes a corpse; the rule was looked up by name on its turn and raised `AttributeError` in the
  report phase (8 tracebacks in 11 days).
- **`Actor.search` on an effective object with no `search`** (a session on the connect screen)
  finds nothing instead of raising `AttributeError`.
- **`ensure_authorization` and `prewarm_authorization` return False under a Twisted-driven
  coroutine**, where awaiting the shared prewarm task raised "await wasn't used with future". The
  caller reads inline, as it does when a fetch fails.

### Portal

- **The web proxy answers a restarting Server without a traceback.** While the Server was down,
  every request failed with a full httpx traceback (about 100 in 8 days). It is one short log line
  now (at most one every 10 seconds, the rest counted, no query string), and the browser gets 503,
  502 or 504 with `Retry-After: 5`. A failure after the response began adds no second page. Errors
  that are not "the Server is not there" keep their traceback.
- **`_write` asks whether the socket is closing first.** A browser that left mid-response raised
  `RuntimeError` under uvloop, not `OSError` (8 in 8 days); that now counts as the client leaving.
- **Shutdown with the Server already gone logs one info line** (`TransportUnavailable`, 16 tracebacks
  in 8 days).
- **`PortalSessionHandler.disconnect_all` loops over a copy**, guards each disconnect and always
  clears the flag that stops `disconnect` deleting entries. Before, sessions removed entries from the
  dict under the loop ("dictionary changed size during iteration"), which left the flag set and the
  remaining sessions open.
- **Bad telnet bytes are one info line per client per minute**, naming the client and counting the
  rest (about 50 error lines in 3.6 days).
- **Discord: an edit to an archived thread is retried once** with `archived: false` (error 50083,
  "Thread is archived"; a player reply that reopens a closed ticket).

### Performance

- **A flush asks the spool for less** ([`jsonb_handler.py`](evennia/typeclasses/jsonb_handler.py)).
  A flush listed the spool twice per dirty row and the async flush a third time per adopted row, and
  every row lock probed its two directories before opening its file. `mark_clean(pending=)` takes
  the answer from a caller that holds the lock and has just listed the queue; an async adoption
  makes one listing (`_SpoolListing`); a lock opens its file first and makes directories only when
  the open fails. The check under the lock stays once per row, because another process may queue an
  intent. A flush of 40 rows went from 360 calls into the spool directory to 80 and from 110 ms to
  67 ms on Windows; building the row state of a loaded object went from 7 calls to 2.
- **The watchdog notes where the loop is from half the warning threshold.** The sampler woke every
  25 ms but looked at the loop only after a stall passed 200 ms, so about a third of logged stalls
  said "work not sampled". It notes the place from 100 ms and keeps the last one. A stall that ends
  under the threshold is still not reported.

### Webclient

- Ticket toasts no longer name who holds the ticket (carried from `55e372334`), and the shell is
  rebuilt to include it.
- Unreleased since `.331`: `fix(actions): keep markup out of give and put help` (`186230453`).

### Migration

None.

## 6.0.0+underspire.331: Put and give refuse what does not hold or receive

### Actions

- **`put` asks the container for `put_in`** ([`objects.py`](evennia/actions/default/objects.py)).
  `CharacterObjectRules.check_put_character` refuses with "You can't put anything there." unless
  `container.access(caller, "put_in")`. The rule runs on the actor, so a character, an exit or any
  object with no put rules answers too. Before, the only gate was `get` access on the container
  (`ContainerPutRules.check_put_container`), and every object grants `get`, so anything you could
  pick up took anything you carried: a knife went into a pistol.
- **`give` asks the target for `receive`.** `check_give` refuses with "You can't give anything to
  <target>." unless `target.access(caller, "receive")`. It also searches only what stands in the
  room (`_give_candidates`): exits, the room itself ("here") and what the giver carries are no
  longer targets. Before, any object in reach took the gift, and one given to an exit landed in the
  exit's contents.
- **Neither operation has a default on `DefaultObject`**, so both fail closed.
  `DefaultCharacter.authorization_policies` grants `"receive": Always()`.
- `ContainerPutRules.check_put_container` no longer reads `get` access, `db.allow_put_while_get_false`
  or `fixture_allows_put_without_get`. A fixture that cannot be picked up holds things when it
  grants `put_in`.

### Migration

A game must grant `put_in` on every typeclass that holds things, or `put` refuses all of them. A
class-level `authorization_policies` entry works, or a policy template bound to the object kind
(the containers contrib's `get_from` pattern). A non-character that takes gifts needs `receive`.
Drop any `allow_put_while_get_false` and `fixture_allows_put_without_get` flags; nothing reads
them.

## 6.0.0+underspire.330: Toasts stay long enough to read

### Webclient

- **A toast stays 20 seconds** ([`toasts.svelte.ts`](evennia/web/webclient/client/src/lib/toasts.svelte.ts)).
  Before, ticket toasts left after 12 seconds and every other toast (kudos, milestone, mention,
  popup blocked, channel error) after 7, and players reported missing kudos. Every kind now shares one
  duration.
- **The countdown pauses while the tab is hidden.** A toast that arrives in a background tab, or is
  counting when the player switches away, waits until the tab is visible again. Hover and focus still
  pause it as before.
- **At most five toasts show.** A sixth drops the oldest, so a tab left hidden for hours does not come
  back to a stack up the whole screen.
- `toasts.push()` no longer takes a duration: `push(kind, title, body, speak = true, open?)`.

### Migration

None for the engine. Shell code that passed a duration to `toasts.push()` must drop that argument.

## 6.0.0+underspire.329: Nous hails and staff PMs in Activity

### Webclient

- **A Nous category in the Activity tab** ([`activity.svelte.ts`](evennia/web/webclient/client/src/lib/activity.svelte.ts),
  [`ActivityPanel.svelte`](evennia/web/webclient/client/src/components/ActivityPanel.svelte)). The event
  kinds `nous.hail` and `nous.pm` are labelled "Nous" and filtered under it. Before, any kind the client
  did not know was labelled "NPC".
- **A row with no actor names its `meta.sender`**, and the text filter searches it. A staff PM is sent
  by an account, which is not a reference the feed can watch, so the game sends the account name in
  `sender` and no actor. The row read "Unknown" before.
- The empty feed reads "New activity will appear here.", since staff PMs are not player activity.

### Migration

None for the engine. A game that sends `nous.*` events or `sender` meta needs this client to label them.

## 6.0.0+underspire.328: The webclient keeps its log between visits

### Portal

- **The hello reply names the account** ([`webclient.py`](evennia/server/portal/webclient.py)). The
  reply carries `account`, the id the browser is signed in as, or null when it is not. A client that
  could not resume a session used to empty its screen whoever came back. It can now tell the same
  person from the next one to sit down.
- **Every frame carries `ts`**, the Portal's clock when the frame was made, and the hello reply
  carries it too so the client can set its clock against the Portal's. A replayed frame is the
  buffered string, so it keeps its original time. `e6cd7eeb9`.
- **`resume.replay: false`** takes a session over and sends nothing back. It is for a second tab of
  an account that holds a saved log it did not write the cursor for.

### Webclient

- **The log is kept in IndexedDB** (`logstore.ts`, `savedlog.svelte.ts`, `5a7980855`). Lines are saved
  under `[account, k]` and the place the connection had reached is saved in the same transaction as
  the lines it covers. A reloaded page presents that place as its resume cursor, so the Portal sends
  exactly the gap: no line twice and none missing.
- A log is shown to its account only, after the hello reply names it. A same-account reconnect that
  could not resume now keeps the log the page held, with a line saying the connection broke.
- One tab saves at a time per account (a Web Lock). A second tab takes its session over with
  `replay: false`.
- Quit and being booted delete the saved copy. Sign-out, a closed tab and an idle timeout keep it.
  Limits: 20,000 lines, 24 MB, 3 accounts, 7 idle days.
- **Settings, Backup:** "Save scrollback on this device" (on by default) and "Delete saved
  scrollback". Reading older lines: an "Earlier" button, or scrolling to the top.
- The shell is rebuilt (`ebd860fe1`).

### Docs

- New: [`webclient-saved-log.md`](.agents/docs/engine-architecture/webclient-saved-log.md). The
  protocol and continuity docs name the `account` and `ts` fields and `resume.replay`.

### Migration

- A game that budgets the size of a frame has to reserve the new `ts` field, as it already does for
  `s`. The game's `world/activity/hub.py` `wire_size` does, from the game commit that comes with
  this pin.
- The Portal code changed, so a deploy that picks this release restarts the Portal.
- Tests: vitest 1,153, Portal 310, the browser suites, and `npm run test:e2e` (new: the whole shell
  against a fake Portal). `fake-indexeddb` is a new dev dependency.

## 6.0.0+underspire.327: Speech that opens with a slash, and a reload keeps the session

### Actions

- **A line that opens with the say quote and a slash is speech** ([`parser.py`](evennia/actions/parser.py)).
  `"/Fast/ things.` was split at the first slash as the say quote with a switch named `Fast`, so
  the speech lost its first word and both slashes. A verb with no letter or digit in it (the say
  quotes) is no longer split at a slash, and the glued-symbol match reads the line whole. A word
  verb still takes switches (`say/loud hello`). Tests:
  `test_slash_after_the_quote_is_speech_not_a_switch` and `test_a_word_verb_still_takes_switches`
  in [`test_parser.py`](evennia/actions/tests/test_parser.py). `8a58f4f4a`.

### Portal

- **A reloaded page resumes its session** ([`webclient.py`](evennia/server/portal/webclient.py),
  [`settings_default.py`](evennia/settings_default.py)). A browser closes with 1001 for a closed
  tab and for a reload, and the Portal ended the session on 1001 at once, so a reload logged the
  character out and back in and the page came back with an empty log. A 1001 now holds the session
  for `WEBCLIENT_LEAVE_GRACE` seconds (default 15). A reloaded page claims it and is sent the replay
  window; a closed tab ends when the hold runs out and signs the browser out, as before. This
  landed on `underspire` after `.326` without a tag (`f838ee1e2`, Kristen Metcalfe) and ships here.
  Tests are in [`test_webclient_resume.py`](evennia/server/portal/test_webclient_resume.py), and
  the design note is
  [`webclient-session-continuity.md`](.agents/docs/engine-architecture/webclient-session-continuity.md).

### Migration

- New setting `WEBCLIENT_LEAVE_GRACE` (seconds, default 15, 0 ends the session at once as before).
  No game change is required.
- The Portal code changed, so a deploy that picks this release restarts the Portal.
- No webclient source changed, so the shell is not rebuilt.

## 6.0.0+underspire.326: Quit no longer freezes the tab

### Webclient

- **`quit` no longer freezes the tab for players with the Help panel**
  ([`evennia.svelte.ts`](evennia/web/webclient/client/src/lib/evennia.svelte.ts)). Since
  underspire.316 (`38fc718ed`) a logout empties the help store. `markLoggedOut()` closed the socket
  but left `connection.state` at `"open"` until the close event. A mounted
  [`HelpPanel`](evennia/web/webclient/client/src/components/HelpPanel.svelte) reloads its index
  whenever it has no page and the connection reads open, so it asked again, failed at once against
  the closing socket, and asked again, each turn a microtask. The close event, painting and input
  never ran. The dock keeps panels mounted in background tabs and screen reader mode keeps every
  view mounted, so anyone who had opened Help in the panel was affected. `markLoggedOut()` now sets
  the state to `"closed"` before it closes the socket. `88b03da7f`.
- The shell is rebuilt (`shell.js`).

## 6.0.0+underspire.325: Present drops whoever leaves

### Webclient

- **The Present list drops a character who leaves** ([`scene-ops.ts`](evennia/web/webclient/client/src/lib/scene-ops.ts),
  [`puppets.svelte.ts`](evennia/web/webclient/client/src/lib/puppets.svelte.ts)). On a departure the
  game sends the whole remaining occupant list (`{op: "set", path: "/occupants", value: [...]}`), so
  a leave cannot tie a character's disguise handles together. Neither the scene reducer nor the
  puppet pane reducer handled that op, so a character who walked out stayed in Present until the
  next look. Both now replace the list. Games that send `del /occupants` by handle are unaffected.
  `8c5e0538f`.
- The shell is rebuilt (`shell.js`).

## 6.0.0+underspire.324: Staff can sort the Assist queue

### Webclient

- **A Sort picker on the staff Assist queue** ([`AssistQueue.svelte`](evennia/web/webclient/client/src/components/AssistQueue.svelte),
  [`ticketSort.ts`](evennia/web/webclient/client/src/lib/ticketSort.ts)). The rebuild of the ticket
  panels (`32bcdbfaf`) dropped the Newest / Last activity / Oldest / Priority picker from
  `678833053`. It is back as a dropdown beside the kind picker, with **Queue order** (the server's
  order, the one telnet, the board and Discord show) as the default. A picked order reorders only
  that viewer's open list, after the kind and the search narrow it; j and k follow it, and it is
  kept in `localStorage` (`underspire.tickets.sort.v1`, so a choice saved before the rebuild still
  applies). History keeps the server's order. `19e9391a7`.
- The shell is rebuilt (`shell.js`, `shell.css`).

### Tests

- [`ticketSort.test.ts`](evennia/web/webclient/client/src/lib/ticketSort.test.ts) covers every order;
  [`tests/tickets.ts`](evennia/web/webclient/client/tests/tickets.ts) checks the default, Oldest, the
  saved choice and the return to Queue order in the browser.

---

## 6.0.0+underspire.323: The staff Tickets list keeps its share of the panel, and its counts follow the kind picked

### Webclient

- **A long ticket title no longer pushes the open ticket off the panel.** On a wide panel the list
  and the open ticket sit side by side, the list taking 40% of the width up to 420px. Neither pane
  could be narrower than its widest line, and a row's title does not wrap, so one long title set
  the width of the list. The list kept that width and the open ticket was pushed past the right
  edge of the panel, where only a strip of it showed (on production the list took about 830px of a
  950px panel). [`tickets.css`](evennia/web/webclient/client/src/styles/tickets.css) now lets the
  two panes (`min-width: 0`) and a row's single column (`minmax(0, 1fr)`) be narrower than their
  content, so a long title is cut with an ellipsis, the list keeps its 40% and the open ticket has
  the rest. My requests uses the same row.
- **The view counts follow the kind picked.** Mine, Unanswered, Answered, Online and All counted the
  whole queue whichever kind the picker showed, so a kind with three tickets sat under an All of
  113. They now count the kind picked, and All kinds counts every ticket. The count in the header
  still counts the whole queue. [`ofKind`](evennia/web/webclient/client/src/lib/ticketModel.ts)
  narrows rows to a kind, for the list and for the counts.
- The shell is rebuilt (`shell.js`, `shell.css`).

### Tests

- [`tests/tickets.ts`](evennia/web/webclient/client/tests/tickets.ts): a ticket with a very long
  title is added beside an open ticket on a wide panel. The list keeps no more than 40% of the
  panel, the open ticket stays inside it, and the list does not scroll sideways (the first two
  checks fail without the change). The kind picker is used too: the counts follow the kind, the
  header does not, and the counts read every kind again when the last ticket of that kind leaves.
- [`ticketModel.test.ts`](evennia/web/webclient/client/src/lib/ticketModel.test.ts): `ofKind`, and
  the counts of one kind and of every kind.

---

## 6.0.0+underspire.322: The ticket form browser test reads the contact field by its label

### Tests

- [`tests/tickets.ts`](evennia/web/webclient/client/tests/tickets.ts): the NPC request
  check looked for the contact field by its "handset ID" placeholder, which 320 removed
  (`0ad089bb2`), so `npm run test:browser` failed on every release since. It now finds the
  field by its "Contact information" label (`fd6d2163c`). No runtime change.

---

## 6.0.0+underspire.321: Shell plumbing off the command line

### Webclient

- **The compose preview, channel sync and unread marker are RPCs** ([`compose.svelte.ts`](evennia/web/webclient/client/src/lib/compose.svelte.ts),
  [`compose-modes.ts`](evennia/web/webclient/client/src/lib/compose-modes.ts), [`chat.svelte.ts`](evennia/web/webclient/client/src/lib/chat.svelte.ts),
  [`main.ts`](evennia/web/webclient/client/src/main.ts)). The shell sent
  `@preview_rp <mode> <text>` on every pause in typing, `@sync_channels` on
  connect and `@clear_unread <key>` on a channel tab click as typed command
  lines. An open prompt or menu (a bug report, the handset menu, a choice
  prompt) took the line as its answer, and the game counted it as an act of
  the character, which dropped hiding. They are now requests:
  `compose:compose_preview {mode, text}` answered with `{you, room, error}`,
  `channels:sync_channels`, and `channels:clear_unread {key}`. Only the reply to
  the latest preview request is shown, so a slow reply, or one that lands
  after the draft was sent, no longer replaces a newer preview. A failed
  preview reads "Preview unavailable."; a failed sync shows a toast
  (`754531db9`).
- `composeToPreview` returns `{mode, text}` or `null` in place of the
  `@preview_rp` line, and `compose.setPreviewSender` is now
  `setPreviewRequest`, taking a function that returns the reply.

### Narrative

- **`CharRef.standalone`** ([`render.py`](evennia/narrative/render.py)). A
  possessive mention with nothing possessed after it (`"holding Kade's."`)
  can be marked so the person it names reads "yours", not "your". Defaults to
  `False` (`f8f50354d`).

### Protocol

- The `compose_preview` OOB event is gone from the generated
  [`oob-events.ts`](evennia/web/webclient/client/src/lib/oob-events.ts); the
  preview arrives as an RPC reply.

### Migration

- A game must serve `compose_preview`, `sync_channels` and `clear_unread` as
  inputfuncs and list `compose:compose_preview`, `channels:sync_channels` and
  `channels:clear_unread` in `AZABAN_PUBLIC_ACTIONS`. Without them the pad
  shows "Preview unavailable." and channel tabs do not fill on connect.
- A tab still running the old shell after a deploy sends the old command
  lines until it is reloaded.

### Tests

- [`compose.test.ts`](evennia/web/webclient/client/src/lib/compose.test.ts): the request
  carries the draft's mode and text, a stale reply and a reply after the send
  are dropped, and a failed request reads as unavailable.
- [`chat.test.ts`](evennia/web/webclient/client/src/lib/chat.test.ts): the sync and the
  unread marker go out as requests and never on the command line.

---

## 6.0.0+underspire.320: A release's web client reaches production, and the request forms lose their example text

This release is 319 plus two changes. The first is why 317, 318 and 319 never reached the players who
loaded the game: on underspire.net the web client of 316 was still being served after all three were deployed.

### Static files

- **A release that changed only the web client was never collected.** The launcher skips `collectstatic` when the
  fingerprint of the static sources is unchanged, and
  [`compute_static_fingerprint`](evennia/server/collectstatic_cache.py) hashed `STATICFILES_DIRS`, which holds the
  game's own `web/static`. The engine's web client is an app's static folder (`evennia/web/static`, read by the
  app-directories finder), so a release that changed only the client left the fingerprint as it was, `collectstatic`
  did not run, and nginx kept serving the old `shell.js` from `STATIC_ROOT`. 316 was collected only because a file
  of the game's own static folder changed in the same deploy.
- **The fix.** The fingerprint now covers the `static` folder of every installed app as well, each read once. A
  `(prefix, path)` pair in `STATICFILES_DIRS` is read by its path; it was read by its prefix, which is not a folder,
  so it was skipped. The first start on this release sees a different fingerprint and collects once, which is what
  puts the client of 317 to 320 in front of players.

### Webclient

- **No example text in the request forms.** The fields of the New request forms carry no placeholder (no "Crash when
  I look at the market board"), so a label says what a field is. The `placeholders` block is gone from
  [`ticketForm.ts`](evennia/web/webclient/client/src/lib/ticketForm.ts) and from what the client reads of the game's
  answer.
- **Plainer words.** The severity line reads "It helps us sort bugs." and the NPC choice reads "After requesting, you can
  leave and go on with your RP while you wait." The game's own words (the `tickets.form` narratives) change the same
  way, and the client keeps a copy so the two stay the same.
- The shell is rebuilt (`shell.js`, `shell.css`).

### Migration notes

Deploy the engine pin and the game together. A client of 317 to 319 that is still open merges the game's words over its
own defaults, so it keeps its example text and its longer severity line until the page is reloaded.

### Tests

- [`test_collectstatic_cache.py`](evennia/server/tests/test_collectstatic_cache.py): a change in an app's static folder
  busts the cache, the engine's own web client is among the sources, a `(prefix, path)` pair is read by its path, and a
  folder listed twice is read once.
- [`ticketForm.test.ts`](evennia/web/webclient/client/src/lib/ticketForm.test.ts): the severity line, the NPC choice,
  and that no field carries example text.
- The forms were also run in a browser against the real component: no field has a placeholder, the bug form shows the
  one severity line, and the NPC choice shows the new words.

---

## 6.0.0+underspire.319: The game can open a request form in the Assist panel

This release is 318 plus one client change. A bare `@request`, `@bug`, `@report` or `@puppetrequest` in the web
client printed a text menu or a guided flow in the terminal, though the Assist panel has the same choices as forms,
with help topics that may answer a question first and the game's own words. The game can now ask the client to open
the form, and the panel opens it.

### Webclient

- **`ticket_compose {kind?}`.** A new event handled by [`tickets.svelte.ts`](evennia/web/webclient/client/src/lib/tickets.svelte.ts).
  `composeRequest(kind)` brings the Assist panel forward on My requests and sets `wantsNewRequest` and `composeKind`;
  [`AssistMine.svelte`](evennia/web/webclient/client/src/components/AssistMine.svelte) reacts by leaving any request that
  is open and showing the form of the kind the game named (`request`, `bug`, `report` or `puppet`), or the picker when
  none is named or the name is not one it has a form for, then clears the flags. They are kept until the component
  exists, so an event that arrives before the panel is on screen still shows the form when the panel mounts. A staff
  member on the Tickets tab is taken to My requests.
- **`ticket_hello` says so.** The hello request carries `{compose: true}`, so a game sends `ticket_compose` only to a
  client that acts on it. A game that does not know the field ignores it.
- **One note is shorter.** The question form's note read "Staff answer as they can. Writing that it is urgent does not
  move it up." It now reads "Staff answer as they can." The game's own words (the `tickets.form` narratives) change the
  same way, and [`ticketForm.ts`](evennia/web/webclient/client/src/lib/ticketForm.ts) keeps a copy of them, so the two
  stay the same.
- The shell is rebuilt (`shell.js`, `shell.css`).

### Event catalog

[`oob-events.ts`](evennia/web/webclient/client/src/lib/oob-events.ts) is regenerated from the game's registry:
`ticket_compose` is new, with an optional `kind`.

### Migration notes

Deploy the engine pin and the game together. An older game never sends `ticket_compose`, and an older client never says
it can show the form, so the side that lacks the feature keeps working: the menu and the guided flows stay in the
terminal.

### Tests

- [`tickets.test.ts`](evennia/web/webclient/client/src/lib/tickets.test.ts): hello says the client can open the form; the
  event brings the panel forward on My requests with the picker, or with the form of each kind it names; a name it has no
  form for shows the picker; a staff member leaves the queue tab; what was asked is forgotten at a new login.
- [`ticketForm.test.ts`](evennia/web/webclient/client/src/lib/ticketForm.test.ts): the shorter note.
- The panel was also run in a browser against the real component: with the panel open, and with the event arriving
  before it mounted, for a player and for a staff member, and for each kind, an unknown kind and none.

---

## 6.0.0+underspire.318: An idle console tab no longer floods the server with 500s

This release is 317 plus one fix. A console tab left open past `CONSOLE_IDLE_TIMEOUT` made
`/api/console/feed/` answer 500 about every two seconds, each with a traceback. On production that
was about 3,700 errors on one day, 11 percent of all web requests, and it kept filling
`web_errors.log`.

### Console

- **The cause.** `ConsoleIdle` and `ConsoleInsecure` derive from DRF's `PermissionDenied`, and
  [`stream.py`](evennia/web/console/stream.py) caught Django's. The two are unrelated classes, so the
  `except` never matched and the error escaped the plain async view. The client then retried every
  second, because it treated any reply that was not OK as a fault.
- **Server.** The feed answers an idle or insecure session with a 403 that carries the reason and
  `reauthenticate`, in the shape the REST views already use. An open stream whose recheck finds the
  session idle closes with that reason instead of raising. Revoked access still closes, now with a
  sentence the console can show.
- **Client** ([`feed.svelte.ts`](evennia/web/console/client/src/lib/feed.svelte.ts),
  [`Strip.svelte`](evennia/web/console/client/src/components/Strip.svelte)). A 401 or 403 ends the
  loop, and the reason is kept in `live.refusal` and shown as the LIVE lamp's title. Other failures
  back off from one second to thirty instead of retrying every second. A `closed` frame keeps its
  reason and is not retried. The console app is rebuilt.

### Migration notes

None. The webclient shell of 317 is unchanged.

---

## 6.0.0+underspire.317: The tickets workbench and My requests, inside the Assist panel

The Assist panel of 316 keeps its shape and gains a new inside. Its two tabs are now **My requests**
(every player) and **Tickets** (staff), and both are rebuilt around one store that hears only what
changed. Staff get a workbench with the queue on one side and the open ticket on the other, and a
player gets one form for the four things they can ask staff for. The words on that form belong to
the game, so a change of wording is never a client release. This release is 316 plus that work; the
queue's order is now decided by the game and is not sorted in the client.

### Webclient

- **One store for tickets** ([`tickets.svelte.ts`](evennia/web/webclient/client/src/lib/tickets.svelte.ts),
  [`ticketModel.ts`](evennia/web/webclient/client/src/lib/ticketModel.ts),
  [`ticketForm.ts`](evennia/web/webclient/client/src/lib/ticketForm.ts)). The ticket state left
  [`chat.svelte.ts`](evennia/web/webclient/client/src/lib/chat.svelte.ts), which keeps `staff`,
  `staffKnown` and `account` as readers of the store and empties it in `resetForLogin` and `logout`,
  so the 316 rule that every session end forgets the account holds for tickets too. Ticket events
  (`ticket_*`) are handed to the store. On every connection it asks the game for the lean protocol
  with `connection.request("tickets", "ticket_hello")` (and again when `ticket_role` says staff
  arrived and it has not begun), and from then on applies `ticket_upsert`, `ticket_remove` and
  `ticket_presence` instead of replacing the whole queue after every change. A game that does not
  answer keeps the whole-queue `ticket_inbox` push, which still works. `ticketModel.ts` is pure
  functions (views, search, the words for a clock or a dot), so the rules are tested without a
  browser.
- **The queue's order is the game's.** Every row carries a `sort` key and the client never sorts,
  so the puppet request of a player standing with the NPC is first on every screen. The Sort row of
  316 (Newest, Last activity, Oldest, Priority) and `ticketSort.ts` are removed with it: a client
  that sorts by priority was the reported bug, and the game now ranks by who is waiting in a scene.
  The views (Mine, Unanswered, Answered, Online, All) and the search narrow the list and do not
  reorder it.
- **Tickets, the staff workbench** ([`AssistQueue.svelte`](evennia/web/webclient/client/src/components/AssistQueue.svelte),
  [`tickets.css`](evennia/web/webclient/client/src/styles/tickets.css)). It was the Queue tab. One
  flat list with the ticket open beside it on a wide panel and in place of it on a narrow one (a
  container query at 760 px, so a docked half-width panel behaves like a phone). The views are Mine
  (the tickets the staff member holds), Unanswered, Answered, Online and All, with a kind filter and
  a search that narrows as staff type; History is a view for staff who may read the record and
  searches on the server. A dot shows who is around on a puppet request, a clock shows a ticket that
  is overdue, and the facts beside a ticket (character, place, who is present, a bug's traceback
  behind a fold) sit with the conversation. Staff can claim or take, release, assign, set the
  priority, close, reopen, approve or deny with a reason, merge, tag, and use **Puppet** on a puppet
  request, which claims it and says where the NPC is. The reply box names who a reply goes to (or
  says a note is for staff only), and a closed ticket shows "Reopen it to reply" in its place, as in
  316. A kind filter whose last ticket left reads as All. Saved replies open with Ctrl+K and fill
  the box so they can be edited before sending. Enter sends, Shift+Enter is a new line; j and k move
  down and up the list, `/` searches, `c` claims, `r` replies. Every button is a request that
  answers with the ticket as it now stands, so nothing types a command and no confirmation lands in
  the terminal. An action that leaves the ticket unreadable to the staff member (a decision on an
  application, for someone who may not read the record) answers with `ticket: null` and the panel
  stops showing it.
- **My requests** ([`AssistMine.svelte`](evennia/web/webclient/client/src/components/AssistMine.svelte)).
  It was the Mine tab. It lists a player's requests in the game's order, marks the ones staff have
  answered since the player last looked (kept by the game, so a reply read on one device is not new
  on the next, and the Assist tab badge counts them as soon as the account is known), and searches
  as the player types. A request opens as a thread with a reply box that says it goes to staff,
  withdraws behind a second press, and reopens when the player replies to a closed one. **New
  request** is a form for asking staff a question (with help topics that may answer it first),
  reporting a bug (with a severity and what each severity means), reporting a player to senior
  staff, and asking for an NPC to be puppeted (with contact information, so staff can reach the
  player if they leave to wait). A player, who has no tab bar, sees the heading "My requests".
- **The form's words come from the game** (`connection.request("tickets", "ticket_form")`).
  [`ticketForm.ts`](evennia/web/webclient/client/src/lib/ticketForm.ts) keeps a copy to show until
  the game has answered and when the game is older, and `mergeForm` lays the game's words over it,
  so a missing word is never a blank label.
- **Per account, as in 316.** The queue badge's marks are kept under `underspire.queue.seen.v2:<account>`,
  the tab and the open threads are rebuilt for a new account, an answer that lands after the account
  left is dropped, a ticket is appended only to the open thread of its audience (an owner line to
  My requests, any other to the queue, so a staff note never reaches the owner's view of a staff
  member's own request), and a thread names its `view` (`staff` or `owner`) when the game says. A
  thread that names none opens by the session's role. Losing the staff role (`@quell`) drops the
  queue already held. The player's own seen marks (`underspire.tickets.seen.v2:<account>`) are no
  longer written, because the game keeps that mark; a config export still leaves the key out.
- The shell is rebuilt (`shell.js`, `shell.css`).

### Event catalog

[`oob-events.ts`](evennia/web/webclient/client/src/lib/oob-events.ts) is regenerated from the
registry of the game that carries the 316 line and this work together. New: `ticket_upsert`,
`ticket_remove`, `ticket_presence`, `ticket_unread`. `ticket_role` gains `duty`, `ticket_msg` gains
`number`, `ref`, `title` and `news` (316 already had `short_id`, `label`, `subject`, `origin` and
`audience`), and `ticket_alert` gains `ref`, `title`, `level` and `held`. `assist_inbox` and
`assist_thread` are gone with the game's Assist channel, and `channel_msg.own` is declared, so the
terminal echo of 316 works with that game. A game that still sends the two Assist events is
ignored by the client, as in 316.

### Migration

Deploy the engine pin and the game together. The panels make requests that only the matching
game answers (`tickets:ticket_hello`, `ticket_get`, `ticket_form`, `ticket_open`,
`ticket_suggest` and `ticket_replies`), and each must be on the game's
`AZABAN_PUBLIC_ACTIONS`. The game must send `ticket_role` with `account` (316 already requires it).
The game can ship first: a session still running an earlier client never says hello, so it keeps
receiving the whole-queue `ticket_inbox` push and works as before. A new client against a game
that does not know `ticket_hello` carries on with whole queues, but the workbench needs
`ticket_get` to open a ticket.

### Tests

- [`ticketModel.test.ts`](evennia/web/webclient/client/src/lib/ticketModel.test.ts): the game's
  order (keys compare element by element, a changed row moves where its key says), a player coming
  and going, the views and their counts, search by number, name, summary and kind, how long in
  words, who is around in words, which rows fade and which are hot, and stepping through the list.
- [`tickets.test.ts`](evennia/web/webclient/client/src/lib/tickets.test.ts): who the game says the
  tab is, the lean protocol and the fallback to whole queues, a change to the queue and when staff
  are told, what counts as news and what does not, alerts and the login line, every staff action and
  the ticket it answers with, a player's own requests, the words of the form, the badge on the queue
  tab, a lost role, which view a thread opens in, a line reaching only the view of its audience,
  state kept per account and forgotten at a new login, and answers that land late.
- [`ticketForm.test.ts`](evennia/web/webclient/client/src/lib/ticketForm.test.ts): the words the
  form starts with (the order of the kinds, a brief summary and details, a meaning for every
  severity, and that none of the old labels or an em dash gets in), and the game's words laid
  over them.
- [`chat.test.ts`](evennia/web/webclient/client/src/lib/chat.test.ts) and
  [`accountSession.test.ts`](evennia/web/webclient/client/src/lib/accountSession.test.ts): the
  terminal echo and the layout migration of 316 as they were, and a quit or a second login leaving
  nothing of the last account in the ticket store.
- [`tests/tickets.ts`](evennia/web/webclient/client/tests/tickets.ts),
  [`tests/tickets.html`](evennia/web/webclient/client/tests/tickets.html) and
  [`tests/workspace.ts`](evennia/web/webclient/client/tests/workspace.ts): the real panels and stores
  run in a browser against a stubbed connection that records every request and every typed
  command. The checks are the order, the words, the sizes at a wide and a narrow panel, the
  recipient line and the closed ticket, and what each button asks the game (`npm run test:browser`).

---

## 6.0.0+underspire.316: One Assist tab, and forget the account at every session end

### Webclient

- **The staff ticket queue is newest first** ([`AssistQueue.svelte`](evennia/web/webclient/client/src/components/AssistQueue.svelte),
  [`ticketSort.ts`](evennia/web/webclient/client/src/lib/ticketSort.ts)).
  The panel re-sorted the server's newest-first `ticket_inbox` by priority,
  so a new ticket sank below every older prioritized one. The open queue now
  has a Sort row: Newest (the default), Last activity, Oldest and Priority,
  remembered per browser in `localStorage`. The age column reads the time the
  list is sorted by (filed, or last activity). The history keeps the server's
  order, latest decision first (`678833053`).
- **A stale kind filter no longer hides the queue.** A kind filter whose last
  ticket closed hid every row, and the filter bar that could clear it is not
  drawn below two kinds. A filter for a kind not in the list now reads as All.
  The header count reads `shown / loaded` while a filter or search hides rows
  (`678833053`).
- **One Assist panel replaces Ticket Queue and My Tickets** ([`AssistPanel.svelte`](evennia/web/webclient/client/src/components/AssistPanel.svelte),
  [`AssistMine.svelte`](evennia/web/webclient/client/src/components/AssistMine.svelte), [`AssistQueue.svelte`](evennia/web/webclient/client/src/components/AssistQueue.svelte)).
  Every player has Mine, their own tickets. Staff also have Queue, the
  staff ticket queue, as a second tab. Both tabs stay mounted, so a draft or
  a filter survives a switch. The legacy `AssistPanel` (the `@assist` help
  desk) is gone. The tab badge flags news on either tab. Staff are given the
  panel at every login; a player is given it once per account
  (`underspire.assist.added:<account>`), so one who closes it keeps it closed
  and a second player on the same browser still gets it. A saved layout that
  already holds the panel marks it given. The tabs are rebuilt for each
  account. The screen-reader layout has Assist as a base view (`85792d7e7`,
  `d8dcc7394`, `1c78858ed`).
- **Saved layouts move to the Assist panel** ([`dock.svelte.ts`](evennia/web/webclient/client/src/lib/dock.svelte.ts),
  [`panelRegistry.ts`](evennia/web/webclient/client/src/lib/panelRegistry.ts)). `migrateAssistPanels` puts
  one Assist panel where a saved layout or named preset had `tickets` or
  `mytickets`, and closes those. Both old component names stay registered
  as Assist, because a restore that names an unknown component fails and
  resets the whole layout (`85792d7e7`).
- **A ticket opens in the view the server built** ([`chat.svelte.ts`](evennia/web/webclient/client/src/lib/chat.svelte.ts)).
  `ticket_thread` routes by the row's `view` field (`staff` or `owner`), not
  by whether the session is staff, so a staff member's own ticket never
  opens in the queue, and opening one switches to the Mine tab. A
  `ticket_msg` is appended only to the open thread of its audience: an owner
  line to Mine, any other to the queue. Opening a queue row uses the
  `ticket_act` RPC (action `view`) in place of typing `@ticket`, so its
  errors show in the panel. A late `view` answer for a ticket that is no
  longer open is dropped, and so is any staff thread after the staff role is
  lost. A refreshed action with no ticket closes the open thread. `ticket_msg`
  handling is typed from the event catalog (`85792d7e7`, `d8dcc7394`,
  `1c78858ed`).
- **Ticket state is per account** ([`chat.svelte.ts`](evennia/web/webclient/client/src/lib/chat.svelte.ts), [`main.ts`](evennia/web/webclient/client/src/main.ts)).
  `ticket_role` now carries the account id. The seen marks for Mine and the
  queue are stored under that id (`underspire.tickets.seen.v2:<id>`,
  `underspire.queue.seen.v2:<id>`), so two people who share a browser do not
  mark each other's tickets read or learn their ids. The old shared `v1` keys
  are removed at login. Losing the staff role (for example `@quell`) drops
  the queue already held, and a stray `ticket_inbox` for a non-staff session
  is ignored (`85792d7e7`, `d8dcc7394`).
- **The reply box names its recipient** ([`AssistQueue.svelte`](evennia/web/webclient/client/src/components/AssistQueue.svelte),
  [`AssistMine.svelte`](evennia/web/webclient/client/src/components/AssistMine.svelte)). Staff see
  "To <player> · #id", or "Note to staff only · #id" with note on; the
  owner sees "To staff · #id". A closed staff-view ticket shows "Reopen to
  reply." in place of the reply box (`85792d7e7`).
- **A speak verb typed in the terminal shows its channel line there**
  ([`CommandInput.svelte`](evennia/web/webclient/client/src/components/CommandInput.svelte), [`main.ts`](evennia/web/webclient/client/src/main.ts)).
  With channel echo off, `xooc hi` went only to the Channels panel. The
  typed line, aliases expanded, now arms an echo: the next line on that
  channel within 10 seconds that the server marks as the player's own
  (`channel_msg.own`) is echoed once. Another player's line is never echoed
  as the player's own, and a line the server refused never arrives, so
  nothing unsaid is shown as said (`85792d7e7`, `d8dcc7394`, `1c78858ed`).
- **Every session end forgets the account** ([`accountSession.ts`](evennia/web/webclient/client/src/lib/accountSession.ts)).
  The shell stays drawn under the quit screen, so the next person at a shared
  browser could read what the last account left. A `logout` OOB, a `hello`
  handshake that cannot resume a signed-in page (an idle timeout or a menu
  sign-out sends no `logout`), and a second login without a logout between
  all forget it. Forgetting empties the terminal, channels with their mutes
  and read marks, tickets, threads, drafts, searches, seen maps, pins,
  command history, the compose draft, routed feeds, puppets, the scene, the
  help page, notifications and an open lore note, and closes every web page
  panel. It sends `resume_reset` while the socket is still open, so the
  portal drops its replay window and a reload cannot bring the scrollback
  back (`d8dcc7394`, `9facc3288`, `38fc718ed`, `441d0f7a9`).
- **The quit screen is opaque, and Reconnect reloads the page**
  ([`QuitOverlay.svelte`](evennia/web/webclient/client/src/components/QuitOverlay.svelte)).
  Every store starts empty, so no panel the last account had open survives
  into the next login. A page refresh closes web page panels a saved layout
  would reopen. `connection.reconnect()` is removed; nothing calls it
  (`9facc3288`, `38fc718ed`).
- **Command history and the compose draft are per account**
  ([`commands.svelte.ts`](evennia/web/webclient/client/src/lib/commands.svelte.ts),
  [`compose.svelte.ts`](evennia/web/webclient/client/src/lib/compose.svelte.ts)).
  They are stored as `underspire.history.v2:<account>` and
  `underspire.compose.draft.v2:<account>`, keyed by the account
  `ticket_role` names. Nothing is recorded before login, so a typed
  `connect <name> <password>` is never kept, and a line typed before login
  is not echoed. The shared `v1` keys are removed at startup (`9facc3288`,
  `38fc718ed`).
- **A config export and import skip account records** ([`backup.ts`](evennia/web/webclient/client/src/lib/backup.ts)).
  History, drafts, ticket seen maps (per account and the old shared ones)
  and the per-account Assist mark are left out (`9facc3288`, `1c78858ed`).

### Protocol

- **A tab on an older shell is told to reload** ([`inputfuncs.py`](evennia/server/inputfuncs.py),
  [`evennia.svelte.ts`](evennia/web/webclient/client/src/lib/evennia.svelte.ts)).
  A deploy reconnects open tabs without reloading them, so they keep the old
  bundle. The shell now sends `shell` (its generation) in `hello`
  capabilities, and a session whose generation is missing or below
  `SHELL_GENERATION` is asked to reload. Every tab open before this release
  sees the message once (`05027062b`).

### Authorization

- **`holds_capability(principal, capability, *, resource=None)`**
  ([`service.py`](evennia/authorization/service.py), [`storage.py`](evennia/authorization/storage.py))
  answers with quell set aside, for decisions about who someone is (a quelled
  staff member's own ticket is still a staff member's). It reads an uncached
  snapshot, so ordinary checks still see quell, and break-glass does not
  count. `load_grants` takes `ignore_quell` (`7dd96e161`).

### Migration

- The game must send `ticket_role` with `account`, a `view` field on every
  ticket row, and `own` on `channel_msg`, and must stop sending
  `assist_inbox` / `assist_thread` (the client no longer handles them).
  Regenerate `oob-events.ts` from the game's catalog.

### Tests

- [`ticketSort.test.ts`](evennia/web/webclient/client/src/lib/ticketSort.test.ts):
  a new low-priority ticket leads a parked high-priority one under Newest, and
  each order ranks a fixed set.
- [`chat.test.ts`](evennia/web/webclient/client/src/lib/chat.test.ts): view routing, audience-checked
  appends, role loss, login reset, per-account seen marks, terminal echo,
  late view answers, and layout migration.
- [`accountSession.test.ts`](evennia/web/webclient/client/src/lib/accountSession.test.ts):
  every way a session ends leaves no store holding the last account's data;
  a resumed reconnect keeps the scrollback.
- [`account-records.test.ts`](evennia/web/webclient/client/src/lib/account-records.test.ts):
  per-account history and drafts, and an export that leaves them out.
- [`tests/workspace.ts`](evennia/web/webclient/client/tests/workspace.ts),
  [`tests/tickets.ts`](evennia/web/webclient/client/tests/tickets.ts) and
  [`tests/notify.ts`](evennia/web/webclient/client/tests/notify.ts):
  the Assist panel and its tabs in a live dockview, an old layout's
  migration, the recipient line, queue rows opened through the RPC, and
  notifications closed at session end.
- [`test_inputfuncs.py`](evennia/server/tests/test_inputfuncs.py): the reload
  message for an older shell, and none for the current one.
- [`test_storage.py`](evennia/authorization/tests/test_storage.py): grants read
  with quell set aside.

---

## 6.0.0+underspire.312: Reactor stalls: the frozen-object gauge, the thaw clock and the stall watchdog

Production on underspire.310 with `ENGINE_GC_REFREEZE` on logged 885 reactor stalls an
hour, median half a second, about 12% of wall time. A py-spy sample of the GIL holders
found two causes, and the watchdog could not name either.

### Engine

- **The frozen-object gauge no longer walks the frozen heap**
  ([`prometheus_metrics.py`](evennia/server/prometheus_metrics.py),
  [`gc_policy.py`](evennia/utils/gc_policy.py)). `evennia_gc_frozen_objects` called
  `gc.get_freeze_count()`, which walks every frozen object with the GIL held. That is
  about 100 ns each on the host, so 4.7 million objects kept the game loop waiting half a
  second, on every Prometheus scrape and every five seconds per open console stream. A
  py-spy sample put 80% of the GIL holders on that one line. The deep clean's log line
  made the same call, which is why a 1.6 s collection showed as a 2 to 2.8 s stall. The
  count is now taken at the boot freeze and in each thaw, where a full pass over the heap
  is already being paid for, and everything else reads it with `gc_policy.frozen_count()`.
  A new gauge, `evennia_gc_blocks_floor`, is the blocks held right after the last full
  collection. It costs nothing to read and shows growth between thaws (`7a8ae7d15`).
- **The backstop thaw runs** ([`gc_policy.py`](evennia/utils/gc_policy.py)).
  `deep_clean_if_due()` counted its interval from the last full collection of any kind,
  and growth cleans (every 15 to 160 minutes) restarted it: 57 cleans in 2.4 days, all of
  them "growth". A cycle frozen alive and dead since was never freed, so the frozen heap
  grew from 1.7 to 4.8 million objects, the process from 0.9 to 2.1 GB, and the growth
  clean's pause from 0.6 to 1.65 s. With `ENGINE_GC_REFREEZE` the interval now counts from
  the last thaw. A floor that has risen `ENGINE_GC_THAW_FLOOR_FACTOR` (1.5) times above
  where the last thaw left it makes the thaw due at the next moment with no one connected.
  With players connected it keeps to the schedule (`7a8ae7d15`).
- **The growth a clean waits for is capped** at `ENGINE_GC_RECLAIM_GROWTH_MAX_BLOCKS`
  (750,000; 0 = no cap). A clean costs about what the garbage it finds costs (1.8 us an
  object, 2.5 blocks to an object on the host), so 15% of a heap that had grown made every
  pause longer than the last (`7a8ae7d15`).
- **The live console feed collects only the engine's metrics, once for every stream**
  ([`feed.py`](evennia/console/feed.py)). Each open stream called `REGISTRY.collect()`
  every five seconds, which runs every collector in the process with the GIL held: 70% of
  the GIL-held time in a 60 second py-spy sample, because one of those collectors was the
  frozen-object gauge. `MetricsProducer` now asks a restricted registry for the collectors
  that provide `evennia_` names, and the streams that are open share one sample for half an
  interval. A registry that cannot list its names is collected whole and filtered, as
  before (`2b9711763`).
- **The stall watchdog names the thread that held the GIL**
  ([`reactor_watchdog.py`](evennia/utils/reactor_watchdog.py)). A thread that holds the GIL
  through one long C call leaves the loop thread waiting at whatever frame last let go of
  it, usually the event loop's own idle wait, so every stall in the production logs said
  `loop.run_forever()` and "work not sampled". The watchdog now also reads the CPU every
  thread has used (the operating system's per-thread clocks, 24 us a reading with 31
  threads, taken at each heartbeat). A stall in which the loop used under 30% of the window
  and one other thread used at least half of it is reported as a GIL wait, names the
  busiest threads and where each is now, and is counted as cause `gil` in
  `evennia_reactor_stall_seconds`. A stall the collector accounts for still names the
  collection first. The live stack sample also dumps the stacks of the threads that used a
  quarter of the window or more, beside the loop's own. Where the platform keeps no
  per-thread clock (Windows) the reading is empty and the warnings read as before
  (`c770adf00`).

### Migration

None required. `ENGINE_GC_RECLAIM_GROWTH_MAX_BLOCKS` and `ENGINE_GC_THAW_FLOOR_FACTOR` are
new settings with defaults in [`settings_default.py`](evennia/settings_default.py). A
dashboard gains the gauge `evennia_gc_blocks_floor`, and `evennia_reactor_stall_seconds`
accepts `cause="gil"`. `evennia_gc_frozen_objects` now shows the count from the last boot
freeze or thaw, not a live walk.

### Tests

- [`test_gc_policy.py`](evennia/utils/tests/test_gc_policy.py): a growth clean does not walk
  the frozen heap, a thaw counts what it froze, the thaw clock counts from the last thaw and
  growth cleans leave it alone, players connected still defer it, a floor over the factor
  thaws at a quiet moment and never forces a thaw on connected players ahead of the schedule,
  the growth cap, and the gauges read a stored count and never walk the heap.
- [`test_reactor_watchdog.py`](evennia/utils/tests/test_reactor_watchdog.py): a thread
  holding the CPU while the loop waits is named, the loop doing the work itself is not
  blamed on the GIL, a loop blocked on I/O with every other thread idle is not the GIL, the
  collector still takes precedence, a platform without per-thread clocks leaves the message
  as it was, and a probe that raises never breaks the heartbeat.
- [`test_feed.py`](evennia/console/tests/test_feed.py): a restricted collection returns the
  same series as a full one, only the engine's own collectors are asked, streams share one
  sample inside half an interval, and concurrent streams do not collect twice.

---

## 6.0.0+underspire.311: Lore tooltips in the web client

### Webclient

- **Lore tooltips** ([`lore.ts`](evennia/web/webclient/client/src/lib/lore.ts),
  [`lore.svelte.ts`](evennia/web/webclient/client/src/lib/lore.svelte.ts),
  [`LoreTip.svelte`](evennia/web/webclient/client/src/components/LoreTip.svelte)).
  On every connect the shell asks the game for its lore words with the typed
  request `connection.request("lore", "lore_tips")`, answered `{"tips": [...]}`.
  A line is marked as it lands in the log (`lore.wrap` in
  [`session.svelte.ts`](evennia/web/webclient/client/src/lib/session.svelte.ts)),
  so a feed that copies the line shows the underline too. Resting the pointer on
  an underlined word, or tapping it, shows a short note with a "Read more"
  button that opens the help page behind it. The note is placed over the word
  when there is room, under it when there is not, and never past the window's
  edge. `parseTips` drops any tip that is not well formed, and caps a reply at
  500 tips. A game that does not answer the request simply has no underlines, so
  the game side can ship first.
- **A setting to turn them off** ([`settings.svelte.ts`](evennia/web/webclient/client/src/lib/settings.svelte.ts),
  [`SettingsPanel.svelte`](evennia/web/webclient/client/src/components/SettingsPanel.svelte)).
  "Lore tooltips" is on by default. It is off whenever the screen reader mode is
  on, because a note that only a pointer can open would be an underline that
  does nothing: the words are not marked at all there.
- The shell is rebuilt (`shell.js`, `shell.css`); the note is sized small enough
  to sit over a line of the log without hiding the line after it.

### Migration

None. The game reads the request in `lore_tips` (`server/conf/inputfuncs.py`) and
allows it as `lore:lore_tips`; a game on an earlier engine tag is unaffected.

### Tests

- [`lore.test.ts`](evennia/web/webclient/client/src/lib/lore.test.ts): the
  wrapper takes the longest spelling at a word, is exact about case unless the
  tip says any case, needs a whole word, skips a word that starts a compound,
  and leaves a plain-English word alone where it opens a sentence; it touches
  only text (tags, attributes, script and style stay as they were); `parseTips`
  keeps a well formed reply and drops the rest; in the log a word is marked as
  its line lands and in a feed's copy, a media line and the echo of a typed
  command are left alone, and nothing is marked in screen reader mode or when
  the game sends no words.
- [`tests/lore.ts`](evennia/web/webclient/client/tests/lore.ts) and
  [`tests/lore.html`](evennia/web/webclient/client/tests/lore.html): the browser
  preview shows the underline and the note (`npm run test:browser`).

---

## 6.0.0+underspire.310: Closed flows end their action, labelled answers

### Engine

- **Dropped command log names the game caller** ([`sessionhandler.py`](evennia/server/sessionhandler.py)).
  For a text send, the six frames above `data_out` were all engine message
  internals (`ServerSession.data_out`, both `msg` passes, `deliver_node`,
  `deliver_resolved`), so the log never reached game code. It now skips
  frames in `server/session.py`, `server/serversession.py`,
  `server/sessionhandler.py`, `narrative/` and `objects/mixins/messaging.py`
  and names six frames from the first one outside them. A game `msg`
  override is one of the six (`21c29ce48`).
- **Labelled answers at a which-one prompt** ([`menus.py`](evennia/actions/menus.py)).
  `2-goblin`, `goblin-2` and the prompt's own line `2: goblin` echoed back
  choose candidate 2. The label must match that candidate's shown label,
  ignoring case, or be left out (`2-`). A line led by a number that names no
  candidate cancels: `9-goblin` out of range, or `2: orc` when candidate 2 is
  a goblin. A `label-N` line whose label names no candidate, or whose number
  is out of range, runs as a new command, since `say t-1000` has the same
  shape (`c9859c1f4`).

- **A closed flow ends its action** ([`engine.py`](evennia/actions/engine.py)).
  `close_captures` now ends the action of a flow it closes. A suspended
  `@interactive` rule closed at a prompt, a menu, or a yielded `Deferred`
  that fires with `INPUT_CLOSED` stops its dispatch: no later `carry_out`
  rule and no report runs, and the trace outcome is `aborted` (the rule is
  recorded as `SILENT_FAIL`). Before, the closed rule read as `PASS`, so
  lower rules ran without the answer (`63103e393`).
- **`EvEditor.close()` and `closefunc`** ([`eveditor.py`](evennia/utils/eveditor.py)).
  `close()` ends the editor with no save and no `quitfunc`, dismisses the web
  panel, removes the persisted buffer, then calls the optional
  `closefunc(caller)`. `EvEditorState.close_capture` uses it, so
  `close_captures` no longer runs an editor's `quitfunc`. `quit()` is
  unchanged. A web editor installs no capture state, so `close_captures`
  does not close it unless its caller installs one (`e8b3caa80`).
- **Closing a pager drops its handle** ([`evmore.py`](evennia/utils/evmore.py)).
  Closing an EvMore pager with `close_captures` now clears
  `caller.ndb._more` (`ab1609080`).

### Tests

- [`test_sessionhandler_outbuf.py`](evennia/server/tests/test_sessionhandler_outbuf.py):
  a stray flag sent through a real `msg()` logs the test's own caller and no
  `deliver_node` frame. It binds the real `data_out`, so a handler left
  patched by an earlier test does not hide the log (`21c29ce48`, `0ae3b3f8d`).
- [`test_dispatch.py`](evennia/actions/tests/test_dispatch.py): labelled
  answer shapes choose, number-led answers that name no candidate cancel, and
  `kick t-1000` still runs (`c9859c1f4`).
- [`test_state.py`](evennia/actions/tests/test_state.py): the `pending_action`
  style lets a new command carry out and reads the echoed prompt line
  (`c5673ae14`, `c9859c1f4`).
- [`test_messaging_templates.py`](evennia/objects/tests/test_messaging_templates.py):
  a `$func` template keeps `$you(target)` lowercase mid-sentence and
  capitalizes the opening `{target}` (`5844d4ebb`).
- [`test_close_captures.py`](evennia/actions/tests/test_close_captures.py):
  a closed flow ends `aborted` with no later `carry_out` or report, a flow
  waiting on a `Deferred` closes, real line, persistent and web editors close
  without their `quitfunc`, and a closed pager clears `ndb._more`
  (`63103e393`, `e8b3caa80`, `ab1609080`).

---

## 6.0.0+underspire.309: Loud event modules, traceable drops, closing captures

Follow-ups to the `.307` rule that the server sends only declared commands.

### Engine

- **Event modules load at start** ([`protocol/__init__.py`](evennia/server/protocol/__init__.py),
  [`service.py`](evennia/server/service.py)). New `load_event_modules()`.
  A `PROTOCOL_EVENT_MODULES` entry that fails to import raises and stops
  server start, instead of logging once and leaving every name it declares
  undeclared for the process. A failed load is retried on the next call
  (`d91321541`).
- **Dropped command log names the sender** ([`sessionhandler.py`](evennia/server/sessionhandler.py)).
  The once-per-name `Dropped undeclared client command` line ends with the
  six frames above `data_out` (`2af70be18`).
- **`unmonitor` ignores the reply name** ([`inputfuncs.py`](evennia/server/inputfuncs.py)).
  Only starting a monitor needs a declared `outputfunc_name`; removal never
  reads it (`13bf09e53`).
- **Opening name capitals on the per-receiver path** ([`messaging.py`](evennia/objects/mixins/messaging.py)).
  A `msg_contents` template with a `$func` or a non-entity mapping value
  capitalized every use of a key that opened any sentence, and handed the
  capital to the funcparser too. Only the opening use takes it now
  (`b780d2ea1`).
- **Numbered answers at a which-one prompt** ([`menus.py`](evennia/actions/menus.py)).
  `2:`, `2.`, `2)` and `#2` choose like `2`; out of range they cancel. Before,
  they ran as new commands and read as an unknown verb (`cc1d0eabf`).
- **`close_captures(holder)`** ([`state.py`](evennia/actions/state.py),
  [`menus.py`](evennia/actions/menus.py), [`engine.py`](evennia/actions/engine.py),
  [`evmore.py`](evennia/utils/evmore.py), [`eveditor.py`](evennia/utils/eveditor.py)).
  Ends every input capture on a holder and returns whether any was open. A
  state that captures input defines `close_capture(holder)`: `get_input`,
  `ask_yes_no` and the pager exit without running a callback, the editor
  quits without saving, and a suspended `@interactive` flow receives
  `INPUT_CLOSED`, which makes the driver close the generator at its `yield`
  (`GeneratorExit`) and return `None`. Exported from `evennia.actions`.

### Known issues

- **`get(pk=)` can return a row a rolled-back transaction never kept**
  ([`managers.py`](evennia/typeclasses/managers.py)). The idmapper caches an
  instance on `post_save` and nothing evicts it when the enclosing `atomic`
  rolls back. Since `4773383d0`, a typeclass manager `get(pk=)` reads that
  cache, as `ObjectDB.objects.get(pk=)` and the other idmapper managers
  already did. No caller is known to create, roll back and then look up by
  pk. A fix needs rollback eviction in the idmapper, which Django offers no
  hook for. Until then, after catching a rolled-back `atomic` that created
  typeclassed rows, flush them with `instance.flush_from_cache(force=True)`.

### Migration

- A game whose `PROTOCOL_EVENT_MODULES` entry fails to import no longer
  starts. Fix the import error shown in the server log.

### Tests

- [`test_protocol_catalog.py`](evennia/server/tests/test_protocol_catalog.py):
  a broken module raises and a failed load is retried.
- [`test_server_shutdown.py`](evennia/server/tests/test_server_shutdown.py):
  a broken event module fails `run_init_hooks` before the system driver starts.
- [`test_sessionhandler_outbuf.py`](evennia/server/tests/test_sessionhandler_outbuf.py):
  the drop log names the sending function.
- [`test_inputfuncs.py`](evennia/server/tests/test_inputfuncs.py): `unmonitor`
  removes a monitor whatever reply name it carries.
- [`test_messaging_templates.py`](evennia/objects/tests/test_messaging_templates.py):
  a repeated name is capitalized only where it opens a sentence.
- [`test_dispatch.py`](evennia/actions/tests/test_dispatch.py): numbered
  answer shapes choose; an out-of-range one cancels.
- [`test_state.py`](evennia/actions/tests/test_state.py): the rule-path test
  uses the `pending_raw` style the dispatch bridge installs (`334614daa`).
- [`test_close_captures.py`](evennia/actions/tests/test_close_captures.py):
  each capture kind closes; a suspended text or menu flow is closed and its
  dispatch finishes without a rule error.

---

## 6.0.0+underspire.308: The give line opens with a capital

The default `give` told the receiver "a tall woman gives you a handset." The
line is built by hand, so it missed the sentence-start capital that
`msg_contents` templates gained in `.307`.

### Engine

- **Give receiver line** ([`objects.py`](evennia/actions/default/objects.py)).
  The giver's name passes through `capitalize_lead` (`87c8bc828`).

### Migration

- None.

### Tests

- [`test_default_objects.py`](evennia/actions/tests/test_default_objects.py):
  a lower-case short description opens the receiver's line with a capital.

---

## 6.0.0+underspire.307: Server-side data stays on the server

Every `msg()` keyword is a command for the client, but the engine and games also
used `msg()` keywords as server-side routing flags. Nothing removed them, so
`_narrative_relayed` (set on every plan delivery) and a game's
`_perception_relay` reached sessions. Telnet clients that negotiated MSDP or
GMCP received them as raw subnegotiations, and a client that handled those
badly showed garbled, spliced text. Routing now travels in a `route` parameter
that never leaves the server, and the server sends only declared commands.
Also ships the untagged work since `.306`: steady web panels, `text_done`, a
which-one prompt fix, capitalized names at sentence start, two manager fixes,
and command-line spellcheck.

### Engine

- **`route` parameter** ([`route.py`](evennia/narrative/route.py),
  [`plan.py`](evennia/narrative/plan.py),
  [`rendernode.py`](evennia/narrative/rendernode.py),
  [`messaging.py`](evennia/objects/mixins/messaging.py),
  [`accounts.py`](evennia/accounts/accounts.py),
  [`serversession.py`](evennia/server/serversession.py)). `DeliveryRoute` holds
  `perception_relay` (skip the `at_narrative_plan` and `at_narrative_delivery`
  relay hooks) and `rendered` (the node already ran its hooks and transforms).
  `msg`, `deliver`, `deliver_resolved` and `deliver_node` take it as a named
  keyword, so it is never left in `**kwargs`. `_narrative_relayed` is no longer
  set. `_render_delivery` is replaced by `route.rendered`
  (`127895454`, `a22bb7e18`).
- **Only declared commands are sent** ([`sessionhandler.py`](evennia/server/sessionhandler.py),
  [`protocol/__init__.py`](evennia/server/protocol/__init__.py),
  [`core_outputfuncs.py`](evennia/server/protocol/core_outputfuncs.py)).
  `ServerSessionHandler.data_out` drops any keyword that is not a registered
  event or outputfunc, logs each dropped name once per process, and skips a
  frame left with only `options`. The check runs before the output buffer, so
  it also lets plan-delivered `narrative` frames merge again. New
  `is_client_command(name)`. The commands the engine already sent are now
  registered as outputfuncs: `narrative`, `patch`, `res`, the inputfunc
  replies, and the IRC, Grapevine and Discord bot commands. `monitor` ignores
  an undeclared `outputfunc_name` (`74f5294b4`).
- **Steady web panels, and a side size for `web_panel`**
  ([`dock.svelte.ts`](evennia/web/webclient/client/src/lib/dock.svelte.ts),
  [`iframePanels.ts`](evennia/web/webclient/client/src/lib/iframePanels.ts)).
  A page panel stays mounted while hidden, so switching tabs no longer reloads
  its iframe, and the iframe loads over the shell background. `web_panel`
  takes an optional `size`: `wide` (default) or `side`, a help-sized float at
  the right (`08330f001`).
- **`text_done` inputfunc** ([`inputfuncs.py`](evennia/server/inputfuncs.py)).
  A scripted client sends a token with a command line and receives a
  `text_done` event with that token when the command finishes (`ab8c97be4`).
- **Which-one prompt** ([`dispatch.py`](evennia/actions/dispatch.py),
  [`menus.py`](evennia/actions/menus.py)). A line that names no candidate and
  is not a bare number closes the prompt and runs as a new command, instead of
  "Invalid choice. Cancelled." (`d5f8cdf2f`).
- **Capitalized names at sentence start** ([`render.py`](evennia/narrative/render.py)).
  `CharRef` and `ObjectRef` gain `capitalize`, so `"{name} sits down."` no
  longer opens with a lower-case short description (`6e2f45c69`).
- **Managers** ([`managers.py`](evennia/typeclasses/managers.py)).
  `get(pk=)` reads the idmapper first (`4773383d0`); a related manager keeps
  its filter in `get` (`69d11e7ec`).
- **Break-glass grants** ([`service.py`](evennia/authorization/service.py)).
  The break-glass loop no longer rebinds the decision-scope memo, which made
  `finish()` raise `TypeError` inside a `decision_scope` (`8332614a2`).
- **Spellcheck** ([`CommandInput.svelte`](evennia/web/webclient/client/src/components/CommandInput.svelte)).
  The command line spellchecks by default; a "Spellcheck while typing" setting
  turns it off for the command line and the compose pad (`cf7e96b05`).

### Migration

- Pass `route=DeliveryRoute(perception_relay=True)` where a game passed
  `_perception_relay=True`. Read `route.rendered` where it read
  `_render_delivery`. Drop any check for `_narrative_relayed`.
- Register every custom client command with `register_event` (shell events)
  or `register_outputfunc` (anything else), or the server drops it and logs
  `Dropped undeclared client command`.

### Tests

- [`test_plan.py`](evennia/narrative/tests/test_plan.py): no routing keyword on
  delivery; `route` reaches `msg` rendered and keeps `perception_relay`; relay
  hooks run for a direct delivery and are skipped for a relayed one.
- [`test_sessionhandler_outbuf.py`](evennia/server/tests/test_sessionhandler_outbuf.py):
  undeclared keywords dropped and logged once; options-only frame skipped;
  declared commands kept; narrative frames merge after a flag is dropped.
- [`test_inputfuncs.py`](evennia/server/tests/test_inputfuncs.py): `monitor`
  refuses an undeclared reply name.

---

## 6.0.0+underspire.306: Web panels follow their base and can be pinned

A page the server opens in a web panel now loads into that base's panel instead
of only focusing whatever the panel last showed, so a second `web_panel` event
for the same `id` changes the page. A Pin button keeps a panel's page; the next
open for that base gets a new panel beside it.

### Engine

- **Panels follow their base** ([`dock.svelte.ts`](evennia/web/webclient/client/src/lib/dock.svelte.ts),
  [`iframePanels.ts`](evennia/web/webclient/client/src/lib/iframePanels.ts),
  [`simpleLayout.svelte.ts`](evennia/web/webclient/client/src/lib/simpleLayout.svelte.ts)).
  `openIframe` reuses the base's unpinned panel and loads the new `url` and
  `title` into it with `updateParameters`. Panel params now carry `base` and
  `panelId`, so the pin survives a saved layout. The phone layout updates an open
  view's params the same way.
- **Pin and Unpin** ([`IFramePanel.svelte`](evennia/web/webclient/client/src/components/IFramePanel.svelte)).
  A pinned panel keeps its page; `togglePin(panelId)` flips it.
- **Reactive panel props** ([`dockAdapter.svelte.ts`](evennia/web/webclient/client/src/lib/dockAdapter.svelte.ts)).
  The dockview adapter keeps props as `$state` and applies `update(event)` in
  place, so a parameter change re-renders the mounted component. The file is
  renamed from `dockAdapter.ts` for the rune. The prebuilt shell is rebuilt
  ([`shell.js`](evennia/web/static/webclient/shell/shell.js)).

### Migration

- None for games. A game that sends `web_panel` with a stable `id` gets the
  follow behavior with no change.

### Tests

- [`iframePanels.test.ts`](evennia/web/webclient/client/src/lib/iframePanels.test.ts):
  reuse picks the base's unpinned panel; a pinned base gets a fresh id.
- [`simpleLayout.test.ts`](evennia/web/webclient/client/src/lib/simpleLayout.test.ts):
  opening an open view updates its params.

---

## 6.0.0+underspire.305: The Reading client, and a Server stop that keeps connections

The webclient gains the Reading surface (a Reading page in Settings, an OOC log
filter, multi-line compose, announcements no filter hides), the narrative
delivery pipeline keeps its transform order instead of re-sorting per message,
the launcher gains `rstop` (stop only the Server in reload mode, the Portal
holding every connection until `sstart`), and two client handshake stubs stop
filling the log with errors.

### Engine

- **The Reading client** ([`reading.ts`](evennia/web/webclient/client/src/lib/reading.ts),
  [`reading.svelte.ts`](evennia/web/webclient/client/src/lib/reading.svelte.ts),
  [`logcats.ts`](evennia/web/webclient/client/src/lib/logcats.ts),
  [`compose-modes.ts`](evennia/web/webclient/client/src/lib/compose-modes.ts),
  [`SettingsPanel.svelte`](evennia/web/webclient/client/src/components/SettingsPanel.svelte)).
  A Reading page in Settings reads and writes the game's reading settings; an OOC
  log filter groups messages from people under Comms; a pose typed over several
  lines in the compose pad keeps its lines; an announcement is a notice that no
  log filter hides. The prebuilt shell is rebuilt for the changes
  ([`shell.js`](evennia/web/static/webclient/shell/shell.js)).
- **`rstop`** ([`evennia_launcher.py`](evennia/server/evennia_launcher.py)).
  Stops only the Server in reload mode and leaves it stopped: the Server runs its
  reload hooks and final flush while the Portal keeps every client connection, and
  `sstart` (or `reload`) starts it again. `sstop` is not that: it stops in shutdown
  mode, which unpuppets every character. Safe to repeat; a Portal that is not
  running fails the launcher.
- **Client handshake stubs** ([`inputfuncs.py`](evennia/server/inputfuncs.py)).
  A bare `ping` (sent about once a minute by some clients) and GMCP
  `Core.Supports.Add`/`Core.Supports.Remove` (which amend the package list
  `Core.Supports.Set` already provided) were each logged as an unknown-inputfunc
  ERROR. They now answer `_not_implemented` and log nothing.

### Performance

- **Transform order is kept** ([`pipeline.py`](evennia/narrative/pipeline.py)).
  `transforms()` sorted the registry for every node of every delivery; the ordered
  tuple is now built once and dropped when a transform is registered or removed.
  `transforms()` still returns the same deterministic order.

### Tests

- [`test_launcher.py`](evennia/server/tests/test_launcher.py): `rstop` stops in
  reload mode, repeats safely, and fails when the Portal is not running.
- [`test_inputfunc_stubs.py`](evennia/server/tests/test_inputfunc_stubs.py): the
  stubs are declared and quiet.
- [`test_transform_order.py`](evennia/narrative/tests/test_transform_order.py):
  the order tuple is stable until the registry changes.

---

## 6.0.0+underspire.304: Device tokens land; attribute writes go heap-only

Two broken fingerprints are repaired (the device token never verified, and no
session counted its commands), session-wire losses and shutdown noise are
closed, the GIN index that made every attribute flush rewrite its row is
retired, and the managed collector gains the option to freeze the survivors of
a deep clean.

### Engine

- **Percent-encoded device cookie** ([`device.py`](evennia/moderation/device.py)).
  The page copies the signed token back with `encodeURIComponent`, which turns
  the colon between value and signature into `%3A`, and the portal compared
  that encoded form against a signature that wants a literal colon, so every
  session recorded an empty `device_token` (all 5,321 sessions in seven days).
  Cookie values are percent-decoded before verification. The raw form the
  server writes has no percent sign and reads the same; a forged token still
  fails its signature.
- **Command counts on the typed bridge** ([`cmdhandler.py`](evennia/commands/cmdhandler.py)).
  `ServerSession.record_command` was called on the cmdset path only; when
  player input moved onto the action bridge nothing called it, so moderation
  showed zero commands for every session. The bridge now calls the hook, when
  the session has one, with the first word of the line; a hook that raises is
  logged and the command still runs.
- **Non-string output keys ship** ([`amp_serde.py`](evennia/server/amp_serde.py)).
  A payload dict with a non-str key raised `TypeError` and the sender dropped
  the whole frame (13 times in two days from one reconnecting client). Keys
  that JSON can write (`int`, `bool`, `None`, finite `float`) are converted to
  the spelling `json.dumps` gives them; two keys that collide after conversion,
  or a key JSON cannot write, are still refused. The first conversion in each
  ten minutes logs one warning naming the key's type and value and the frame's
  top-level keys.
- **Monitors on JSONB Attributes** ([`monitorhandler.py`](evennia/scripts/monitorhandler.py)).
  An Attribute holds a weakref, so registering a monitor on one logged
  "cannot pickle 'weakref.ReferenceType'" plus an invalid-definition traceback
  for every webclient connect that asked for saved options and every MSDP/GMCP
  report of "desc". `add()` now returns early for such objects and says so
  once per name; `remove()`/`all()` no longer leave defaultdict entries behind,
  and `remove()` passes the category so categorised monitors can be removed.
- **Cancelled prewarm stays cancelled** ([`storage.py`](evennia/authorization/storage.py),
  [`prometheus_metrics.py`](evennia/server/prometheus_metrics.py)).
  `CancelledError` is not an `Exception`, so a prewarm cancelled mid-wait
  reached the `finally` block with `outcome` unset and logged
  `UnboundLocalError: cannot access local variable 'outcome'` at every restart.
  The outcome now starts as `"cancelled"`, the metric accepts that label, and
  the cancellation propagates.

### Migration notes

- **The `db_attrs` GIN indexes are retired** (objects
  [`0020_drop_objectdb_db_attrs_gin`](evennia/objects/migrations/0020_drop_objectdb_db_attrs_gin.py),
  accounts `0020`, comms `0028`, scripts `0027`). PostgreSQL only; each is
  `DROP INDEX CONCURRENTLY IF EXISTS` in a non-atomic migration, because
  `CONCURRENTLY` refuses a transaction. The reverse recreates the index as the
  original migration made it. Other backends never had the index and no-op.
  Deploys run `evennia migrate` automatically; no manual SQL.
  `objects_objectdb`'s index was 328 MB against a 7 MB heap and had been
  scanned 52 times in its lifetime; without it the handful of attribute
  lookups that used it are sequential scans of about ten milliseconds.

### Performance

- **Every attribute flush can be heap-only again.** While an index covers
  `db_attrs`, an update to that column cannot be a hot update: each flush
  rewrites the row, adds an index entry, and logs full-page images. A
  synthetic run of the production flush pattern (20,000 transactions of 8
  updates over 11,000 rows) measured:

  | variant | p99 | p99.9 | WAL/txn | HOT updates |
  |---|---|---|---|---|
  | GIN (before) | 83 ms | 987 ms | 47.9 KB | 0% |
  | GIN, fastupdate=off | 22 ms | 770 ms | 82.5 KB | 0% |
  | no GIN (after) | 5.96 ms | 202 ms | 12.9 KB | 96.6% |

  Expected on production: the 328 MB index and the write amplification it fed
  go away; the writes stop paying for an index reads almost never touched.
- **`ENGINE_GC_REFREEZE`** ([`gc_policy.py`](evennia/utils/gc_policy.py)).
  A full collection marks the whole unfrozen heap (0.3–1.3 s on production,
  about 50 growth cleans a day and 24% of stall seconds). With the option on,
  each deep clean freezes its survivors, so the next marks only what was
  allocated since: a 3.0M-object heap measured 313 ms before, 12 ms after.
  The `scheduled` and `overdue` backstop cleans thaw first, collect
  everything, and freeze again, so a cycle frozen alive and dead since is
  freed at the next backstop rather than never.

### Settings

- New: `ENGINE_GC_REFREEZE = False` ([`settings_default.py`](evennia/settings_default.py)).
  Leave off if the idmapper evicts often (a small `IDMAPPER_CACHE_MAXSIZE`),
  since an evicted instance that was frozen waits for a backstop.

### Tests

- `test_device.py`: an encoded cookie verifies; a raw cookie is unchanged; a
  forged signature still fails.
- `tests.py` (commands): the bridge calls `record_command` once with the first
  word; a raising hook does not cost the command.
- `test_amp_serde.py`: int/bool/None/float keys convert as `json.dumps` would;
  colliding and unwritable keys are refused; the warning fires once per window.
- `tests.py` (scripts): a monitor on an Attribute is ignored once, field
  monitors still register, logout leaves no entry, categorised monitors
  remove.
- `test_storage.py` (authorization): a cancelled prewarm increments `cancelled`
  and propagates.
- `test_attrs_gin_migrations.py`: each migration drops `CONCURRENTLY`, reverses
  to the original name, is non-atomic, and no-ops on non-PostgreSQL.
- `test_gc_policy.py`: the refreeze call order against a patched collector;
  a frozen cycle survives an ordinary clean and is freed by a backstop.

---

## 6.0.0+underspire.303: Subnegotiation frames keep their bytes

A subnegotiation payload could contain a raw `IAC` (0xFF) byte, and every
payload passed through the text line-ending rewrite on its way to the
transport. A strict telnet parser reads the unescaped `IAC` as the start of
the frame terminator, swallows the `SE` that follows it, and stays out of
sync until the next subnegotiation's terminator arrives; a payload byte 0x0A
was rewritten to CRLF mid-frame. Mudlet, with the strictest parser, showed
this as commands being dropped until the next `IAC SB` completed.

### Engine

- **`_write_subneg` / `_write_binary`** ([`telnet_parser.py`](evennia/server/portal/telnet_parser.py),
  [`telnet.py`](evennia/server/portal/telnet.py)). `Telnet._write_subneg(option,
  payload)` writes one RFC 854 frame: `IAC SB <option> <payload> IAC SE`, with
  `IAC` doubled inside the payload. It writes through `_write_binary`, which
  bypasses the `\n`-to-`\r\n` rewrite in `TelnetProtocol._write` but keeps MCCP
  compression. `requestNegotiation` now delegates to it.
- **OOB output uses it** ([`telnet_oob.py`](evennia/server/portal/telnet_oob.py)).
  `TelnetOOB.data_out` ships MSDP and GMCP through `_write_subneg` instead of
  hand-building frames and sending them through the text `_write`.
- **Client handshake GMCP stubs** ([`inputfuncs.py`](evennia/server/inputfuncs.py)).
  `client_name`, `client_version`, and `external_discord_get` are no-op stubs
  next to the existing `client_gui` / `external_discord_hello`; before this,
  every Mudlet login logged an unknown-inputfunc ERROR for them.

### Migration notes

None. The wire format only changes for payloads that previously produced
invalid frames (raw `IAC`, raw 0x0A); GMCP's JSON encoder is already
ASCII-escaped, MSDP was the reachable path.

### Tests

`evennia/server/portal/tests.py`: `_write_subneg` preserves a raw newline and
doubles `IAC` in the payload; `data_out` ships a well-formed GMCP frame.

## 6.0.0+underspire.302: Menu Back and Quit are separate words

One `q` used to end only the level it was typed at, and a menu that wanted
both "up one level" and "leave" had to declare its own `b` and `q` options.
Back and Quit are now engine words with fixed meanings, so every menu reads
and answers the same way.

### Engine

- **Back and Quit split** ([`menus.py`](evennia/actions/menus.py),
  [`engine.py`](evennia/actions/engine.py)). `BACK_WORDS` (`b`, `back`,
  `cancel`, or a blank line) resume the flow with `None`: up one level.
  `QUIT_WORDS` (`q`, `quit`, `exit`) throw the new `MenuQuit` into the flow
  at its `yield`. It rises through every `yield from` level, so one `q`
  leaves the whole menu; a level that holds unsaved work or says a leaving
  line catches it. An uncaught `MenuQuit` ends the flow with `None`.
  `MenuQuit` is exported from `evennia.actions`.
- **The foot is fixed.** `format_menu_prompt` always draws
  `b: Back   q: Quit` (plus `l: Look` when allowed). `MenuPrompt` raises
  `ValueError` when an option key is a Back word, or a Quit word while
  `allow_quit` is on.
- **`MenuPrompt(accept_text=True)`.** A line that names no option resumes
  the flow with the stripped line instead of "Invalid option", so a screen
  that takes names or commands shares the engine foot and words. Back and
  Quit words keep their meaning.
- `confirm()`: going back is `False`; quitting leaves the whole flow.

### Migration notes

- **Breaking.** A game menu that declares a `b`, `back`, `cancel`, `q`,
  `quit` or `exit` option key now raises `ValueError` when it opens. Drop
  those options; the foot offers them.
- A flow that treated `None` from a `MenuPrompt` as "quit" now gets `None`
  only on Back. Catch `MenuQuit` where the flow must save work or print a
  leaving line on quit.

### Settings

None.

### Tests

- `test_engine.py`: Back resumes with `None`, Quit throws through nested
  `yield from` levels and is caught or ends the flow, reserved keys raise,
  the foot is fixed, and `accept_text` returns the typed line while Back and
  Quit keep their meaning.

---

## 6.0.0+underspire.301: Alias order is insertion order; tabs badge news

Two changes that make author intent visible: which alias of an object reads
as its display name, and which tabs flag that something waits behind them.

### Engine

- **Aliases keep insertion order** ([`tags.py`](evennia/typeclasses/tags.py)).
  `TagHandler.all()` sorted every tag alphabetically, so the first alias of an
  object was whatever came first alphabetically, whatever the author set.
  The first alias is the display alias (exit short names in look lines:
  `There are exits to the N110 (northwest)` where the author wants `(nw)`).
  New class attribute `TagHandler._sort_all` (default `True`; tags and
  permissions are unchanged) and `AliasHandler._sort_all = False`. Both fetch
  paths now order the through-table by pk, so cache, cold reads and
  `prime_tag_caches` agree: `TagHandler._query_all` and
  `TypedObjectManager.get_tags_for_objects` ([`managers.py`](evennia/typeclasses/managers.py)).

### Migration notes

- **Aliases that were alphabetized by accident.** Existing rows keep their
  historical add order, which for old objects is whatever the loader did. To
  choose the display alias now: clear and re-set in the wanted order
  (`@alias <obj> =` then `@alias <obj> = nw,northwest`). No data migration:
  the stored rows are already ordered by add time; only the read changed.
- Any caller that treated `aliases.all()` output as alphabetically sorted
  must sort explicitly. A sweep of both this repo and the game found no such
  caller (all use membership or sort for display).

### Webclient

- **Tab news badges** ([`Workspace.svelte`](evennia/web/webclient/client/src/components/Workspace.svelte)).
  A panel whose feed holds something unread carries `[!!]` in its tab title
  (Puppets, Channels, Ticket Queue), so news on another tab is visible from
  this one. The staff queue badge flags tickets new or changed since the
  viewer last focused the queue panel (per browser,
  `underspire.queue.seen.v1`); focusing the panel counts the news as seen.
  Replaces the old `Puppets (N)` count: the marker carries no number. The
  screen-reader layout reads the queue tab `(new)`.
- **Ticket queue leads with the newest** ([`TicketsPanel.svelte`](evennia/web/webclient/client/src/components/TicketsPanel.svelte)).
  Priority still leads, but within a priority the newest ticket is first;
  oldest-first buried incoming urgent tickets behind stale ones nobody could
  action. Games should pair this with `-created_at` inside the priority tier
  server-side (`world/tickets/core.py` in the game).

### Tests

- `TestAliasOrder` ([`test_typeclasses.py`](evennia/typeclasses/tests/test_typeclasses.py)):
  warm, cold-reload and primed-cache order, re-add moves to the end, tags
  stay sorted. Client vitest: badge derivation (arrival, update,
  focused-arrival, player never flagged, channel totals).

---

## 6.0.0+underspire.300: Reachable verbs one class at a time

A game that asks which of the registry's verbs an actor can reach (the webclient's
Tab-completion lexicon, built on every reconnect) was holding the loop for the whole
answer: several hundred verbs, each running the gates its rules carry, which on
production took half a second of unbroken loop time for a character in a busy room.

### Engine

- **`iter_reachable_actions(action_classes, actor)`** ([`parser.py`](evennia/actions/parser.py)). The generator form of `reachable_actions`, with the same answer in the same order. The actor's states, equipment and room are read once, when the first class is asked for; after that each class costs only its own gates, so a caller takes a few per loop turn and carries on the next. `reachable_actions` is now `list()` over it, so the two cannot drift. Fail-closed as before: an actor the provider context cannot be built for yields nothing.

### Settings

None.

### Tests

- `test_parser.py` (`TestIterReachableActions`): the same answer and order as the list form, nothing read before the first class is taken, the room read once however many are taken, a gate evaluated only when its class is reached, and the fail-closed result.

---

## 6.0.0+underspire.299: The managed collector frees what young passes cannot

`underspire.298` fixed the reactor stalls and introduced a memory leak. Its managed
collection policy collected only the young generation, so nothing freed a reference
cycle once it had survived a young pass, and every instance the idmapper drops is
one: an instance and its handlers (attributes, tags, ndb and the rest) refer to one
another, so removing the cache's reference to an evicted or deleted instance does not
free it. Of 200 evicted instances, reference counting freed none and a full
collection freed all of them (about 56 containers each). On production the process
grew about 5 MB a minute after the `.298` restart (663 MB to 1.5 GB in two and a half
hours), where earlier runs flattened at 850 to 980 MB. Nothing was lost; the garbage
was waiting for the 24 hour deep clean, and the machine would have run out of memory first.

**Correction to the `.298` entry.** It said the old-generation passes "found almost
nothing: 7.5 thousand objects in two hours against 4.6 million from young
collections". That compared `python_gc_*` generation labels as if they meant the same
thing on Python 3.14. The automatic collector's passes are generation 1 (an
increment); generation 0 is only what `gc.collect(0)` runs, and generation 2 is a
full pass. In the 4.25 hours before `.298` the increments collected 10.5 million
objects, most of them evicted instances. The stall fixes in `.298` stand: no
collection marks the whole heap while players wait. What was missing is a way to free
the cycles young passes cannot.

### Engine

- **Full collections when they are worth it** ([`gc_policy.py`](evennia/utils/gc_policy.py)). Under `ENGINE_GC_POLICY = "managed"` a full collection of the unfrozen heap (the frozen boot heap is still never scanned) now runs when the interpreter's allocated blocks have grown `ENGINE_GC_RECLAIM_GROWTH_PERCENT` (15) since the last one, when code calls `gc_policy.request_reclaim()`, and as a backstop from the scheduler (`ENGINE_GC_DEEP_CLEAN_INTERVAL`, now 21600 s, and `ENGINE_GC_DEEP_CLEAN_MAX_DEFER`, now 43200 s; they were 24 h and 72 h). Growth is counted in blocks, not process size: freed memory goes back to the allocator's pool rather than to the operating system, so the process stops growing while garbage refills the pool, and a trigger on its size would wait ever longer. A clean that finds under 20,000 objects (the growth was live data, such as the cache filling) doubles the growth the next one waits for, up to eight times, and no two requested cleans run within `ENGINE_GC_RECLAIM_MIN_INTERVAL` (300 s). Each logs what it found with the allocated blocks and process size either side.
- **Telemetry.** `evennia_gc_reclaim_total{reason}` (`growth`, `requested`, `scheduled`, `overdue`, `manual`) and `evennia_gc_reclaimed_objects_total` count the cleans; the scrape-time gauges `evennia_idmapper_cached_instances`, `evennia_gc_frozen_objects` and `evennia_python_allocated_blocks` answer where the memory went without a debugger. `evennia_gc_pause_seconds{generation="2"}` still times each clean.
- **`evennia.utils.process_memory`** ([`process_memory.py`](evennia/utils/process_memory.py)). The current-resident-size reader the idmapper's pressure check used (psutil, then `/proc/self/statm`, then the peak) moves here so the collector shares it. `idmapper.models._current_rss_mb` remains as an alias.

### Settings

`ENGINE_GC_RECLAIM_GROWTH_PERCENT` (15; 0 turns the trigger off), `ENGINE_GC_RECLAIM_MIN_INTERVAL` (300). `ENGINE_GC_DEEP_CLEAN_INTERVAL` and `ENGINE_GC_DEEP_CLEAN_MAX_DEFER` change their defaults (see above). No game changes are required. A game whose whole world fits in memory gains most by raising `IDMAPPER_CACHE_MAXSIZE` (default 400 MB) above its steady-state size: the pressure check compares the process's resident size, not the cache's, with that limit, so a process that is already over it sweeps and evicts whenever it grows 10%, and each eviction is garbage a full collection must then free.

### Measured

On a real asyncio loop with a 3.7 million object frozen heap and a cache of 4,000 cyclic instances, a third of which is evicted and reloaded every four seconds, the `.298` policy grew 789 MB in 60 s without levelling off; with the trigger the process levelled off at 1,379 MB after four full collections. A full collection took about 0.8 s on that synthetic heap: the pause is set by the unfrozen live objects, not by the garbage, so it falls with the number of instances kept in the cache.

### Tests

- `test_gc_policy.py`: the request path and its spacing; the growth trigger in blocks, including garbage that refills the allocator's pool while the process stays the same size; the backoff, its cap and its reset; the real collector freeing a cycle a young pass cannot; the gauges.
- `test_gc_evicted_instances.py`: real typeclass instances are cyclic garbage after eviction and a deep clean frees them.
- `test_process_memory.py`: the moved reader.

---

## 6.0.0+underspire.298: Reactor stalls cut at the source

Production reactor stalls came from costs that all landed on the loop: Python
3.14's incremental collector marking the whole heap in one step, an idmapper
pressure check that swept a healthy cache every few minutes, dbref lookups that
asked the database for objects already in memory, a system tick that ran ten due
systems back to back, and a fresh PostgreSQL connection for every unit of work.
Each is fixed where it starts.

### Engine

- **Managed collection policy** ([`gc_policy.py`](evennia/utils/gc_policy.py), `2f22f2765`). On Python 3.14 the incremental collector marks the whole heap at the start of each old-generation cycle — about every six minutes, in one 0.5–1.3 s step — and scans an increment of the old generation on most other collections, finding almost nothing (7.5 thousand objects in two hours against 4.6 million from young collections). `ENGINE_GC_POLICY = "managed"` (the new default) disables automatic collection after boot, runs one full collection with the survivors frozen (`ENGINE_GC_FREEZE_AT_START`), drives young-generation passes from a loop timer (`ENGINE_GC_YOUNG_THRESHOLD`, `ENGINE_GC_INTERVAL_MS`) and takes a full "deep clean" from the system scheduler when no one is connected (`ENGINE_GC_DEEP_CLEAN_INTERVAL`, `ENGINE_GC_DEEP_CLEAN_MAX_DEFER`). On a 9.7M-object heap the max pause fell from 700–1500 ms to 11 ms; a deep clean of the frozen heap took 49 ms against 2.1 s. `"default"` restores the interpreter's collector.
- **Collection telemetry and stall attribution** ([`reactor_watchdog.py`](evennia/utils/reactor_watchdog.py), [`prometheus_metrics.py`](evennia/server/prometheus_metrics.py)). Every collection is timed; pauses feed `evennia_gc_pause_seconds` and long ones log (`ENGINE_GC_PAUSE_WARN_MS`). A stall the watchdog blames on a collection now says so instead of naming the frame the collection landed in, stall durations export as `evennia_reactor_stall_seconds{cause}`, and the sampler wakes every 25 ms (100 ms missed 35% of stalls).
- **Idmapper pressure reads current memory and counts caches once** ([`idmapper/models.py`](evennia/utils/idmapper/models.py), `d88b6a2e6`). `conditional_flush` read `ru_maxrss`, the process's peak, which never falls after a spike; with the default 400 MB limit and 790 MB resident it swept the whole cache every five to ten minutes (about 1.1 thousand evictions and 230 worker queries per sweep, each on a fresh connection). It now reads current resident memory (psutil, then `/proc/self/statm`, then the peak), treats "over the limit" as pressure on its own, and stays quiet until memory has grown 10% past where the previous sweep started. `cache_size()` no longer adds one database class's shared cache once per typeclass reading it, which had made a few thousand objects count as tens of thousands.
- **`search_object("#id")` answers from the idmapper** ([`objects/manager.py`](evennia/objects/manager.py), `5c158b99a`). A dbref lookup cost a query for an object already in memory, and two for a missing one (the miss fell through to a name search). Game code looks objects up by stored id constantly (214 call sites, a courier tick that resolves hundreds). The cached match returns as a pre-evaluated queryset, so indexing, `bool`, `count` and chaining behave as before; `candidates`, `use_dbref=False` and non-IO threads are unchanged.
- **The system scheduler yields between due systems** ([`systems.py`](evennia/utils/systems.py), `610bf276c`). `SystemDriver.tick_sliced` (the loop's tick) yields to the event loop once `SYSTEM_TICK_SLICE_MS` (25) of wall time has passed since its last yield, so systems that line up on the five-minute marks cost several short turns instead of one 130–270 ms turn. Admission, order, `ctx.now` and overlap rules are unchanged; a body is never interrupted; `tick()` is the same tick without pauses.
- **Optional PostgreSQL connection pool** ([`db_pool.py`](evennia/server/db_pool.py), [`db_backend/`](evennia/server/db_backend), `9402051d7`). A Django wrapper still belongs to one task and still closes at its end, but `ENGINE_DATABASE_POOL=True` parks the raw connection in a bounded process-wide pool and hands it to the next wrapper. A connection is parked only when demonstrably clean (open, idle, no database error, autocommit as Django set it) and reused only when one socket poll shows the server has not hung up; the age counts from the physical connect (`ENGINE_DATABASE_POOL_MAX_AGE`, default 1800 s), the pool is bounded (`ENGINE_DATABASE_POOL_MAX_IDLE`, default 16), a forked child drops what it inherits, and the session-initialisation receiver skips a recycled connection. Off by default; with psycopg 3, which has its own pool, the setting does nothing. Production was opening about five short-lived connections a second, roughly 12% of reactor work.

### Settings

All new, all with defaults; no game changes required. `ENGINE_GC_POLICY` (`"managed"` — behavior change: CPython's automatic collector is now off by default), `ENGINE_GC_YOUNG_THRESHOLD`, `ENGINE_GC_INTERVAL_MS`, `ENGINE_GC_FREEZE_AT_START`, `ENGINE_GC_DEEP_CLEAN_INTERVAL`, `ENGINE_GC_DEEP_CLEAN_MAX_DEFER`, `ENGINE_GC_PAUSE_WARN_MS`; `SYSTEM_TICK_SLICE_MS` (25); `ENGINE_DATABASE_POOL` (off), `ENGINE_DATABASE_POOL_MAX_IDLE` (16), `ENGINE_DATABASE_POOL_MAX_AGE` (1800). Set `ENGINE_GC_POLICY = "default"` to restore the interpreter's collector.

### Tests

- `test_gc_policy.py`, `test_reactor_watchdog.py`, `test_engine_systems.py`: policy behavior, pause telemetry, stall attribution and deep-clean scheduling.
- `test_idmapper_pressure.py`: one count per shared cache, the RSS fallback chain and the sweep hysteresis.
- `test_objects.py`: a cached dbref costs no query, a miss still reaches the database, candidates and queryset chaining behave.
- `test_systems.py`: slicing, priority order, shared `ctx.now`, and cancellation between slices leaving the rest due.
- `test_db_pool.py`, `test_db_backend_pool.py`, `test_database_postgres.py`: pool rules without a database, plus the real backend against PostgreSQL (the same server session returns, a killed connection is replaced, and an open transaction, a database error or an atomic block is never parked).

---

## 6.0.0+underspire.297: Contents reads follow the idmapper's live instance

An idmapper-evicted room can be re-cached by a later `save()` while viewers still hold the reloaded instance. Moves reconciled only the held and currently-cached instances (`.293`), so the orphaned view never learned of arrivals: people who walked in stayed out of the room description and off target lists, while their speech and emotes still reached the room. This is the invisibility regression reported in production after `.293`.

### Engine

- **`ContentsHandler.get` reads through the idmapper's live instance** ([`objects/models.py`](evennia/objects/models.py)). When `self.obj` is no longer the instance the idmapper holds for its row, the read delegates to the live instance's handler, whose cache receives move reconciliations. An evicted row with no live replacement keeps the existing rebuild path. Reads through any pre-existing orphan self-heal without a reload.

### Diagnostics

- **Reactor-stall warnings name the work that blocked the loop** ([`reactor_watchdog.py`](evennia/utils/reactor_watchdog.py), `ba6e496c9`). A warned stall now labels the game frames on the stack as `inner via outer` (the innermost frame is where the time went; the outermost is the entry point), or names the innermost engine frame when no game frame is involved, relative to the game and engine checkouts; library and stdlib paths fall back to the full path. The watchdog also wakes at half the warning threshold so a warned stall can be located before it ends.

### Performance

- The common path adds one idmapper dictionary lookup per `contents` read: 74 ns against a 2.6 µs read of a populated room (~3%), with no query and no `load()` fallback. Delegation runs only for an orphaned instance, and builds the live instance's handler at most once. The `.293` stall fix's synchronous rebuild pressure is not reintroduced.

### Tests

- `test_arrival_reaches_a_view_through_an_orphaned_instance` in [`test_objects.py`](evennia/objects/tests/test_objects.py): evict a room from the idmapper, reload it, re-cache the stale instance with a save, move a newcomer in, and assert the viewer holding the reloaded instance sees them.

---

## 6.0.0+underspire.296: Back and quit share a foot line; a Redis outage no longer blocks login

A login could be aborted by a Redis blip: `LOGIN_THROTTLE.check` read the `throttle` cache unguarded, so a stale pooled connection (`Error 32 while writing to socket. Broken pipe.`) raised `ConnectionInterrupted` through `Account.aauthenticate` into the login action, which logged an untrapped error and dropped the connection. Separately, menus spent a full row on each way out, so `b: Back` and `q: Quit` read like two more choices.

### Engine

- **`format_menu_prompt` collects the exit keys into a foot line** ([`menus.py`](evennia/actions/menus.py)). Options whose key is `b` or `q` (case-insensitively) render once, in that order, joined with three spaces, after the ordinary option rows; `allow_quit` still supplies `q: Quit` when the menu has no exit option of its own, and `allow_look` appends `l: Look` to the same line. A menu that labels `b` or `q` itself keeps its label; a second option on the same key stays a row, so no choice is dropped. The keys keep their ordinary option semantics for `parse_menu_choice`; only the rendering changes.

### Server

- **`Throttle.check`/`update` fail open when storage raises** ([`throttle.py`](evennia/server/throttle.py)). A cache backend that raises (a downed Redis, a stale pooled socket) used to propagate through `LOGIN_THROTTLE.check` and abort the login as an untrapped error. `check` now logs once and returns `False` (not throttled); `update` logs once and becomes a no-op. Throttling is degraded, never fatal.
- Games on django_redis can additionally set `"IGNORE_EXCEPTIONS": True` (and `"DJANGO_REDIS_LOG_IGNORED_EXCEPTIONS": True`) in each Redis cache's `OPTIONS` so the cache returns defaults instead of raising; the engine no longer depends on it. `"CONNECTION_POOL_KWARGS": {"health_check_interval": 30}` prunes stale pooled sockets before use.

### Tests

- `TestFormatMenuPrompt` in [`test_engine.py`](evennia/actions/tests/test_engine.py): back and quit share the last line while the choices stay rows; a menu-labeled quit key keeps its label in the foot; a foot-only menu is body, blank line, foot; a repeated key stays a row; look joins the foot; and `parse_menu_choice` still returns `b` and `q` as themselves.
- `test_check_fails_open_when_storage_unavailable` and `test_update_swallows_storage_errors` in [`test_misc.py`](evennia/server/tests/test_misc.py): with a storage stand-in whose every operation raises `ConnectionError`, `check` returns `False` and `update` does not raise.

---

## 6.0.0+underspire.295: Slow JSONB flush preparation names its cause

A production reactor stall (~432ms) landed in `_prepare_async_row_flushes`, which deep-copies each dirty row's whole attribute document three times on the reactor before the worker writes it. Nothing said whether the cost came from many dirty rows, one large document, or the copy itself.

### Attributes

- **Slow flush preparation logging** ([`jsonb_handler.py`](evennia/typeclasses/jsonb_handler.py)). A preparation of 100ms or more (`_SLOW_FLUSH_PREPARATION`) logs one warning with the total time, row count, total snapshot copy time, and the slowest row's model, pk, copy time, and serialized size, e.g. `Slow JSONB flush preparation: 432ms for 180 row(s), snapshot copies 410ms; slowest objects.ObjectDB #14830 9ms, 31121 bytes`. Only the slowest row is serialized, and only when the threshold is crossed; a fast preparation pays two `perf_counter` calls per row.

### Tests

- `test_slow_flush_preparation_names_row_count_and_slowest_row` in [`test_jsonb.py`](evennia/typeclasses/tests/test_jsonb.py).

---

## 6.0.0+underspire.294: Dropped bus frames log one line per burst

A Server reload logged two `[!!]` lines for every frame queued before the first
handshake: `redis bus errback (MsgServer2Portal): connection generation replaced`
followed by a bogus `NoneType: None` traceback. One reload produced over a hundred.

### Server

- **`errback` no longer calls `log_trace`** ([`redis_bus.py`](evennia/server/redis_bus.py)). It runs as a publication observer, outside any `except` block, so `format_exc()` returned `NoneType: None` and the log showed a fake traceback for each frame.
- **`TransportUnavailable` rejections are summarized per burst.** A generation change rejects every queued frame in one loop callback (`RedisTransport._reject_stale`). The first rejection schedules `_report_dropped_frames` on the next loop turn, which logs one warning with the total and a count per command key, e.g. `redis bus dropped 3 frames (AdminServer2Portal x1, MsgServer2Portal x2): connection generation replaced`. Other errors log once each with `log_err`.
- The frames are still dropped; only their logging changed.

### Tests

- `test_dropped_frame_burst_logs_one_warning_without_traceback` in [`test_bus_recovery.py`](evennia/server/tests/test_bus_recovery.py).

---

## 6.0.0+underspire.293: Moves update the live room instance

Backfilled; shipped as tag `underspire.293` (`eb43580bc`) without a version bump.

### Objects

- **Moves update the current cached location** ([`objects/models.py`](evennia/objects/models.py)). An object could hold a location instance the idmapper had since evicted and reloaded. Moves updated only that stale instance, so the reloaded room kept a ghost contents entry and later logged `contents cache failed`. Moves now also update the current cached instance, and a contents rebuild after eviction drops stale entries and logs them.

### Tests

- New cases in [`test_objects.py`](evennia/objects/tests/test_objects.py).

---

## 6.0.0+underspire.292: Slow outbuf turns name their slowest sends

Backfilled; shipped as tag `underspire.292` (`4a0dd08aa`) without a version bump.

### Server

- **Slow outbuf flush logging** ([`sessionhandler.py`](evennia/server/sessionhandler.py)). A flush turn of 100ms or more logs one warning with the total time, send and session counts, prepare and clean time, and the five slowest sends named by frame command keys and recipient count. Message content is never logged.

### Tests

- New cases in [`test_sessionhandler_outbuf.py`](evennia/server/tests/test_sessionhandler_outbuf.py).

---

## 6.0.0+underspire.291: Line terminators no longer leak into echoed input

A telnet client sending `up\r\n` got `Command 'up\r\n' is not available. ...`
back: the line terminator reached the game as part of the command text.

### Server

- **The `text` inputfunc drops line terminators** ([`inputfuncs.py`](evennia/server/inputfuncs.py)). The telnet portal relays every completed wire line with a single trailing `\n` (`applicationDataReceived` appends it after conch normalizes CR LF and CR NUL), and it used to travel into `cmdhandler` intact. Since the action-engine bridge replaced the legacy cmdset path for normal input, the line reaches `action.raw_string` verbatim (the legacy path stripped it; it is now only reachable via `cmdobj=` injection), so anything echoing the raw line, the nomatch message above, sent the `\n` back to the client, where telnet output encoding turned it into `\r\n` mid-message. The inputfunc now `rstrip`s `\r\n` from the text before the idle check, MXP strip, nick replacement, and the `cmdhandler` handoff, the same place `ssh_asyncio.py` already did it. It is a no-op for protocols that deliver terminator-free text (webclient, bots). Leading and trailing spaces are deliberately kept: they are content for commands that echo the raw line (emotes, poses).
- The stale note in `get_input`'s docstring ([`menus.py`](evennia/actions/menus.py)), which told callers the result keeps the client's trailing newline, now states the real contract. The defensive strips in consumers (`eveditor.py`, the nick inputline pattern suffix) are left in place and are inert.

### Tests

- `TestTextLineTerminators` in [`test_inputfuncs.py`](evennia/server/tests/test_inputfuncs.py): `"up\n"` reaches `cmdhandler` as `"up"`, terminator-free input is unchanged, and trailing spaces survive.
- `TestTelnet.test_line_relayed_with_single_trailing_newline` in [`portal/tests.py`](evennia/server/portal/tests.py): pins the portal contract (unchanged behavior) that `b"up\r\n"` relays once as `text=b"up\n"`.

---

## 6.0.0+underspire.290: Staff Activity panel in the web client

### Web client

- **New Activity panel for staff with observe authority** ([`ActivityPanel.svelte`](evennia/web/webclient/client/src/components/ActivityPanel.svelte), [`activity.svelte.ts`](evennia/web/webclient/client/src/lib/activity.svelte.ts)). A live feed of player texts (`handset.direct`, `handset.group`), LOOC and NPC actions, filterable by category, by free text, and by a Global or Watched scope. Staff can watch any character, NPC or location found by name or `#id`, including ones not yet in the feed, and add an NPC from an event to their puppets without taking control of it. The feed is virtualized, follows the latest event unless the reader scrolls up, and can be paused at a fixed cutoff. A Latest button counts the new arrivals that match the current filters. The screen-reader layout renders every row without virtualization.
- The panel appears when the game sends `activity_role` with `allowed: true`, in both the docked and the screen-reader layout, and is removed when a role update revokes it. The panel list and `dock.openView` hide it from anyone without the role.
- **Privacy:** feed events, watches and search results are held in memory only; nothing is written to browser storage. They are erased on revocation, on closing the panel (which also sends `activity_unsubscribe`), on `logout`, and on `logged_in`. On `logged_in` the client erases the data but keeps the panel: the `activity_role` that follows decides whether it stays, so staff keep the panel where they placed it across logins. Replies from an older stream, subscription or search are discarded.
- **Stream integrity:** a subscription snapshot and live `activity_batch` messages are merged by event id and sequence number. Batches that arrive during a subscribe are queued and replayed. A hole in the sequence shows "Some activity was missed." A new `stream_id` (hub restart) resets the feed. History is capped at 1000 events.
- The Puppets panel is now added inactive, so a new puppet no longer takes focus from the panel that added it.

### Protocol

- **`logged_in` is a registered OOB event** ([`core_events.py`](evennia/server/protocol/core_events.py)). `Account.at_post_login` already sent it; registering it puts it in the generated [`oob-events.ts`](evennia/web/webclient/client/src/lib/oob-events.ts) so the client can route it. The client uses it to erase Activity data from the previous login.
- **Game-side contract:** the game must register and send `activity_role` (`allowed`, `can_puppet`) and `activity_batch` (`stream_id`, `first_seq`, `last_seq`, `events`). Send `activity_role` to every web session after `logged_in`, with `allowed: false` for accounts without observe authority; a layout saved by a staff account keeps its Activity panel until that message arrives. On revocation, drop the subscription on the server, because the client sends no `activity_unsubscribe` once the role is gone. Answer the `activity` request namespace (`activity_subscribe`, `activity_unsubscribe`, `activity_search`, `activity_watch`, `activity_unwatch`) plus `puppets/puppet_add`. The engine ships no server side for these.

### Commands

- **`CmdIC` logs the traceback when a puppet fails** ([`account.py`](evennia/commands/default/account.py)), backfilled from `e5a1574bc`. The handler logged only the exception message, so a `RuntimeError` from deep in the puppet path (for example `StaleAttributeValueError`) gave no raise site.

### Tests

- [`activity.test.ts`](evennia/web/webclient/client/src/lib/activity.test.ts): request coalescing, replay dedupe, history cap, gap detection, stream reset, pause cutoff, watch filters, stale reply rejection, state erasure on close and revocation, and a Latest count that respects filters while paused.
- Browser pages: [`tests/activity.ts`](evennia/web/webclient/client/tests/activity.ts) (bounded DOM for 1000 events, follow and reading position, pause, watch of an absent location, puppet denial) and new cases in [`tests/workspace.ts`](evennia/web/webclient/client/tests/workspace.ts) (panel add, close, reopen and revocation in both layouts; the same panel survives `logged_in` followed by the role; closing in the simple layout sends `activity_unsubscribe`).

---

## 6.0.0+underspire.289: The first write after a flush no longer copies the row

A production stall ended in `JsonbRowState.mark_dirty` deep-copying a character's attribute document, reached from a per-minute cyberware heat tick that writes to every chromed character.

### Performance

- **`mark_dirty` shares `durable_document` as the merge baseline instead of copying it** ([`jsonb_handler.py`](evennia/typeclasses/jsonb_handler.py)). A row going from clean to dirty set `volatile_baseline = deepcopy(durable_document)`, so the first Attribute write after every flush paid a full deep copy of the row's document on the calling thread. Every path that changes `durable_document` or `volatile_baseline` assigns a new object, and the merges (`_three_way_merge`, `_apply_local_delta`), the spool writer and the flush read them without editing them. Only `visible_document` is edited in place, and it is always its own copy. Sharing the object is therefore safe, and the baseline keeps the pre-write document after `durable_document` is later replaced. The `JsonbRowState` docstring now states the rule this depends on: only `visible_document` is mutated in place.
- Not measured on production documents. The cost removed is one deep copy per row per flush cycle, sized by the row's whole attribute document.
- `JsonbRowState.__init__` still makes five copies of the document and a successful flush four. Under the same rule, all but `visible_document` could share one copy; left for a measured follow-up.

### Tests

- `TestDirtyBaseline` in [`test_jsonb.py`](evennia/typeclasses/tests/test_jsonb.py): `mark_dirty` on a clean row makes no deep copy and shares `durable_document` (fails before the fix), and after a write the baseline still holds the value from before it.

---

## 6.0.0+underspire.288: Object checks stop when they have already told the player

Production logged `check rule 'check_drop' on Character returned 'claim'; the check phase must be a pure predicate ... Result ignored.` for every `drop` of something the player was not carrying.

### Actions

- **Object check rules return `SILENT_FAIL` where they returned `CLAIM`** ([`objects.py`](evennia/actions/default/objects.py)). `check_get_plain`, `check_drop`, `check_give`, `check_put_character`, `check_put_container`, `check_enter_bare` and `check_enterable` returned `CLAIM` after `caller.search(...)` had already sent its not-found or ambiguity message, or after an `at_pre_*` hook had vetoed. `CLAIM` is not a check-phase result: the engine logged a contract warning, ignored it, and ran `carry_out`. For a missed search the carry-out found nothing stored and stopped quietly. For a container's `at_pre_arrive` veto, `carry_out_put` tried the move again, `move_to` ran the hook a second time, and the player got the hook's refusal followed by "You can't put that in there." `SILENT_FAIL` blocks the action and sends nothing more, so the one message the player saw stays the only one and `carry_out` never runs. The `_unresolved` branches in `check_put_character`, `check_enter_bare` and `check_enterable` changed the same way; dispatch skips the check phase for an unresolved action, so they only matter to a direct call.
- The search inside these checks still messages the player directly, which is itself outside the check contract (a check must not message). Under `explain` a missed search in these checks still reaches the character.

### Tests

- `TestCheckStopsAfterItsOwnMessage` in [`test_default_objects.py`](evennia/actions/tests/test_default_objects.py) dispatches a `drop` of something not carried, a `drop` vetoed by `at_pre_drop`, a `give` to a missing target, and a `put` into a container whose `at_pre_arrive` refuses. Each gives exactly one message, runs no `carry_out` rule, and logs no warning. Without the fix all four log the contract warning.

## 6.0.0+underspire.287: Overlapping authorization publishes no longer flush every cache

Production logged `authorization invalidation reconciled (gap)` every minute or two, and each one was followed by reactor stalls from cold capability checks (`is_staff` in a surface tick, among others).

### Authorization

- **`publish_generation` takes the revision and appends the event in one Lua script** ([`invalidation.py`](evennia/authorization/invalidation.py)). It ran `INCR` and then `XADD` as two commands, and `storage._publish_generation` runs publishes on `defer.background` pool threads. When two publishes overlapped, the second could append revision N+2 before the first appended N+1. Every reader then saw N+2 while at N, took it for a lost event, and ran `reconcile("gap")`, which flushes all local authorization caches; every check after that read the database inline until the caches refilled. The script (`_PUBLISH_SCRIPT`) makes stream order equal revision order. Real gaps (trimmed or failed appends) still reconcile as before. The job queue already runs `EVAL` in production, so no new Redis capability is needed.

### Tests

- `PublishOrderTest` in [`test_invalidation.py`](evennia/authorization/tests/test_invalidation.py) runs a second publish between the first publish's Redis commands. It checks the stream holds revisions in order, and that a reader at the previous revision applies both without reconciling. Without the fix the stream reads `[2, 1]` and the reader reconciles.

## 6.0.0+underspire.286: Verb reachability for a whole list in one pass

A game that builds a player's full verb list (a Tab-completion lexicon, say) rebuilt the provider context once per registered action. In production one list cost about 270ms, and a Portal reconnect built one for every session in a single reactor turn (an 854ms stall).

### Actions

- **`reachable_actions(action_classes, actor)`** ([`parser.py`](evennia/actions/parser.py)) returns the classes whose verbs may be offered to the actor, in input order. It gives the same answer as `_verb_reachable` for each class and fails closed the same way (an actor the context cannot be built for gets `[]`). The per-verb test now runs over a prebuilt context through `_context_reachable`, which both paths share.
- **`ActionContextBuilder.build_many(actor, action_types)`** ([`context.py`](evennia/actions/context.py)), exported as `build_contexts`, returns one context per action type. It reads the actor's states, equipment, room providers and room contents once; each type then gets its own account slot (from `__primary_handler__`) and its own B2 prefilter. `build` is `build_many` over one type, so dispatch keeps its exact provider order.

### Tests

- `TestReachableActions` in [`test_parser.py`](evennia/actions/tests/test_parser.py) checks the batch answer against `_verb_reachable` for a player, a builder, and a player whose room holds an ungated provider for a gated verb; checks the room's contents are read once for the whole list; and checks an unshaped actor gets `[]`.

## 6.0.0+underspire.285: Saved protocol flags no longer break the Portal sync

A player whose saved options held a dict-valued flag broke every full session sync, so output to every player dropped each time the bus peer reset.

### Server

- **Saved protocol flags reach a session as plain data** ([`serversession.py`](evennia/server/serversession.py), [`session.py`](evennia/server/session.py)). `_saved_protocol_flags` reads back from its Attribute as a `_SaverDict`, and a dict-valued flag inside it (`SCREENWIDTH` and `SCREENHEIGHT` are `{0: n}`) as a nested `_SaverDict`. `ServerSession.update_flags` (login restore in `DefaultAccount.at_post_login` and game overrides) and `Session.at_sync` (every reload) copied those into `protocol_flags` unconverted. The admin serializer in [`amp_serde.py`](evennia/server/amp_serde.py) accepts only plain containers, so the next `SSYNC` for that session raised `TypeError: unsupported AMP session type: _SaverDict`. Raised inside `update_flags`, it aborted login part way (no `logged_in` message, no auto-puppet, and the error text reached the client). Raised on a later sync, it failed the frame, dropped the Portal peer (`peer is not ready` on every player's output), and on reconnect resynced every session's scene in one reactor turn (a 2.3s stall in production). The poisoned session failed again on each later full sync, about every 12 seconds in production. Both entry points now pass the flags through `dbserialize.deserialize`. The serializer is unchanged: it stays strict.
- A nested `_SaverDict` in a session's flags also wrote session-side changes back to the account's saved flags; the plain copy ends that.

### Tests

- [`test_saved_protocol_flags.py`](evennia/server/tests/test_saved_protocol_flags.py) restores `{"SCREENWIDTH": {0: 100}}` from a real Attribute through `update_flags` and `at_sync`, packs the real `{sessid: get_sync_data()}` admin frame, and changes the nested value to check the account attribute is untouched. Without the fix, two tests raise the production `TypeError` and the third fails on the write-back.

## 6.0.0+underspire.284 — `put` finds a container you carry

Players could take an instrument out of a surgical kit in their hands but never put it back: `put scalpel in kit` answered "Could not find 'kit'." unless the kit sat on the floor.

### Actions

- **`put <item> in <container>` looks for the container among what the body carries, then the room** ([`objects.py`](evennia/actions/default/objects.py)). `Put.parse` searched only `actor.location`, so a bag on your back, a kit in your hands or a worn pouch could never be the container, although `look in <container>` searches the inventory and a game's `get <item> from <container>` can too. The new `_reachable_containers(actor)` passes the carried objects and then the room's contents as the search candidates, so a name that matches one of each still asks which you mean. The item is still taken from the hands (or from the room for a container that sets `accepts_room_character_put`), and the container's own rules still decide whether it accepts it.

### Migration

- A container rule that must only act on a container standing in the room now has to check the container's location itself, since a carried container of that type can be named. Underspire's confinement cages already do (`self.location is not caller.location`), and fixtures such as drop-off slots cannot be carried.

### Tests

- [`test_default_objects.py`](evennia/actions/tests/test_default_objects.py) `TestPutFindsItsContainer` covers a carried container, a container in the room, and an item that is not in hand. The carried-container test fails on the `underspire.282` source.
## 6.0.0+underspire.283 — The scrollback copies whole, and reads back a line at a time

The virtualized log mounts only the rows around the viewport. Copying a passage lost the middle that had unmounted, a selection died when its rows scrolled away, and Chromium emitted no line break between absolutely positioned rows, so even the lines a copy caught ran together. The review keys could re-read one of the last nine lines and go no further back.

### Webclient

- **A selection holds its rows** ([`logcopy.ts`](evennia/web/webclient/client/src/lib/logcopy.ts), [`logvirtual.ts`](evennia/web/webclient/client/src/lib/logvirtual.ts), [`GameLog.svelte`](evennia/web/webclient/client/src/components/GameLog.svelte)). A live selection's two end rows stay mounted wherever the reader scrolls (`getPinned` feeds the virtualizer's range extractor); the rows that mount between them sit between them in DOM order and paint selected. The extractor is replaced only when the held set changes, so the window stays memoized through appends. Before this, scrolling a row out of the window dropped the selection with it.
- **Copy comes from the scrollback** ([`logcopy.ts`](evennia/web/webclient/client/src/lib/logcopy.ts), [`transcript.ts`](evennia/web/webclient/client/src/lib/transcript.ts)). A copy that spans lines is assembled from the log lines, not from whichever rows are mounted: plain text is one line per log line (the same projection search and the downloads use), and the HTML carries palette colours resolved inline against the live document, the log's own background and a system mono stack, so a paste into a document keeps its shape. The two ends are cut from their lines' own HTML at the selected character, a line still typing copies whole, and client-only links (MXP `href="#"`, scripts) paste as their text. A selection inside one line, or one reaching outside the log, is left to the browser's own copy, which is exact.
- **Select all is the scrollback** ([`GameLog.svelte`](evennia/web/webclient/client/src/components/GameLog.svelte)). Ctrl+A with focus in the output holds the first and last line, then selects between them; a copy takes every line the log is showing.
- **A review cursor walks the scrollback** ([`review.ts`](evennia/web/webclient/client/src/lib/review.ts), [`App.svelte`](evennia/web/webclient/client/src/App.svelte), [`keybinds.svelte.ts`](evennia/web/webclient/client/src/lib/keybinds.svelte.ts)). Alt+1..9 still read one of the last nine lines; Alt+Up and Alt+Down now step a line at a time from there, and Alt+Shift+Up and Alt+Shift+Down jump to the oldest and newest line. The cursor holds a line id, not a row, so new output and the scrollback trim do not move it; it follows the log's category filters and skips media rows and blank lines; sending a command puts it back below the newest line. The four arrows are new rebindable bindings, listed for capture in the settings key view and shown as "Alt+Up" (`displayCombo`).
- [`text.ts`](evennia/web/webclient/client/src/lib/text.ts) splits the text projection so an already-parsed fragment (`nodesToText`) projects exactly as a markup string does; the copied parts use it.
- The shell bundle ([`shell.js`](evennia/web/static/webclient/shell/shell.js)) is rebuilt from the source.

### Tests

- [`review.test.ts`](evennia/web/webclient/client/src/lib/review.test.ts) covers walking by line id in both directions, the top/bottom edges, filters and blank lines, lines trimmed out from under the cursor, new output, and reset.
- [`logvirtual.test.ts`](evennia/web/webclient/client/src/lib/logvirtual.test.ts) covers `withPinned` (ascending, unique, deduped against the window) and a pinned row surviving appends.
- [`keybinds.test.ts`](evennia/web/webclient/client/src/lib/keybinds.test.ts) covers the new defaults and `displayCombo`'s "Alt+Up" labels.
- [`tests/log.ts`](evennia/web/webclient/client/tests/log.ts) runs in a real Chromium and now asserts a selection surviving a scroll across three thousand lines, the copy of that span as text and HTML, and select-all. The log page is 63 checks; the vitest suite is 726 tests.

## 6.0.0+underspire.282 — A looping room track keeps looping

Players heard a room's looping track once, then a long silence before it came back.

### Webclient

- **A looping room track wraps its sync position at the track's length** ([`youtube-bgm.svelte.ts`](evennia/web/webclient/client/src/lib/youtube-bgm.svelte.ts)). The `play_yt` offset counts from when the room's track was set (the game's `bgm_start_time`) and the server never learns how long the track is; the client then advances that offset with the wall clock. Once a looping track had played through, the position lay past its end: the loop-back on `ENDED` seeked to 0, the sync check on `PLAYING` seeked past the end, and the video ended again. `expectedSyncSeconds()` now takes the position modulo `getDuration()` when the room loops and the loaded video is the active one (right after `loadVideoById` the player still reports the previous video, so that length is never used). The sync tolerance counts across the loop point, so a track that ends a moment before the wall clock does is not dragged back to its last second. A join, refresh or reconnect long after the track was set now lands inside the track as soon as its length is known.
- The shell bundle ([`shell.js`](evennia/web/static/webclient/shell/shell.js)) is rebuilt from the source; the source at `underspire.281` rebuilds byte-identical to the bundle it replaces.

### Tests

- [`room-bgm.test.ts`](evennia/web/webclient/client/src/lib/room-bgm.test.ts) covers the wrapped start offset, the loop-back after `ENDED`, the loop-point tolerance, and two guards: a track that does not loop, and a new video whose length has not loaded, are not wrapped. The three wrap tests fail on the `underspire.281` source.

## 6.0.0+underspire.281 — One Server per game, and an unreplayable spool intent can no longer wedge a boot

Two architectural guards close the failure class behind a production incident: a lifecycle race (a service restart racing a manual `evennia reload`) started a second Server, and the conflicting write-behind documents it produced were spooled at shutdown as intents that could never replay — every later boot blocked those rows, the Portal<->Server handshake died on a blocked read, and the unit restarted forever.

### Server

- **A second Server for the same game refuses to start** ([`server_lock.py`](evennia/server/server_lock.py), [`asyncio_bootstrap.py`](evennia/server/asyncio_bootstrap.py)). The Server takes an exclusive advisory lock beside its pidfile (`<pidfile>.lock`, byte 0, held for the process lifetime) before any game or database work. A second Server logs `FATAL: another Server process (pid N) already owns ...` and exits 1. Advisory locks die with the process, so a crash can never leave the game permanently locked.
- **The Portal and the launcher probe the lock before spawning** ([`amp_server.py`](evennia/server/portal/amp_server.py), [`evennia_launcher.py`](evennia/server/evennia_launcher.py)). A held lock turns a duplicate start into an immediate, named refusal instead of two writers and a readiness timeout; `start`/`reload` no longer wait out the timeout when a Server is already alive.

### Attributes

- **Spool reclamation cannot block a boot** ([`jsonb_handler.py`](evennia/typeclasses/jsonb_handler.py)). `reclaim_spooled_writes()` now returns a `SpoolReclaimReport` and, by default (`quarantine_conflicts=True`), moves an intent that can never replay — a three-way merge conflict with committed data, or an unreadable payload — to a sibling `jsonb_spool_quarantine_<UTC>_<pid>/` directory with a `manifest.jsonl`, logs `[EE] jsonb spool: quarantined ...`, and lets the boot proceed. The row serves committed truth; the preserved files stay for operator reconciliation. `PREPARED_BLOCKING` witnesses keep their operator-resolution contract and are never quarantined.
- **New public helpers**: `SpoolReclaimReport`, `QuarantinedIntent`, `spool_quarantine_dirs()`.
- **Migration notes**: `reclaim_spooled_writes()` returns a report, not an int; read `.reclaimed`. Callers that must keep the old leave-in-place behavior pass `quarantine_conflicts=False`.

### Docs

- [`docs/source/Components/Attributes.md`](docs/source/Components/Attributes.md) states the single-Server invariant and the quarantine contract; [`evennia.server.server_lock`](docs/source/api/evennia.server.server_lock.md) joins the API tree.

### Tests

- [`test_server_lock.py`](evennia/server/tests/test_server_lock.py) covers holder recording, contention refusal, stale lock files, pidfile parsing, refusal reporting, and the Portal spawn guard.
- [`test_jsonb.py`](evennia/typeclasses/tests/test_jsonb.py) covers conflict quarantine (row freed, manifest written, committed truth served), the opt-out, and unreadable payloads.

## 6.0.0+underspire.280 — A dropped web socket no longer logs the player out; the web client gets one console look, opens help in a panel and keeps the typed line; tickets act in place; telnet negotiates UTF-8

Two lines of this work were each prepared as `underspire.279` and never shipped: `.279` was cut on `underspire` in the meantime (silent rule shadowing now logs). This entry covers both lines and the session hold that followed them.

### Webclient and Portal

- **A dropped socket holds the session instead of ending it** ([`webclient.py`](evennia/server/portal/webclient.py), [`portalsessionhandler.py`](evennia/server/portal/portalsessionhandler.py)). Players on phones were logged out every few minutes. Every reconnect after an unclean drop found the old socket by `csessid`, disconnected its Server session and connected a new one, so the Server logged `Logged out` and `Logged in` a few seconds apart and the character dropped and came back. Worse, a peer close frame with a code other than 1000/1001 left the socket half-closed; the next line of room output raised `Disconnected`, which ran a full `disconnect()` that also cleared the browser's auto-login stamp. The reconnect then came back signed out, and the resume check logged `resume token presented by a different uid; not replaying`. Now only an explicit end finishes a session: the browser closing with 1000/1001, the client's `websocket_close`, or a Server disconnect. Any other loss holds the session for `WEBCLIENT_RESUME_GRACE` seconds (default 90). The Portal keeps it under its sessid, keeps recording output, and does not tell the Server. A reconnect from the same tab, signed in as the same account, takes it over in place: same sessid, same bus socket incarnation, the handler entry swapped, only changed socket facts synced (`PCONNSYNC`), missed output replayed. No logout or login hooks run. A hold nobody claims ends as an ordinary disconnect and leaves the browser signed in.
- **Resume tokens are issued by the Portal and replaced on every takeover.** The `hello` reply carries `token`; the shell keeps it in `sessionStorage` and presents it on reconnect. The browser's signed-in account must also be the held session's account, so a token alone cannot take over or read another account's session. A copied tab cannot bounce a session between windows: the first to use a token gets the session and a new token. A socket that still looks open (a phone that changed networks before the keepalive noticed) is taken over too, and is sent close code 4001 before it is aborted. The client-chosen token, the uid-stamped stash and its helpers are gone (`_RESUME_STASH`, `_stash_for_resume`, `_handle_resume`, `RESUME_STASH_MAX`); `RESUME_HOLD_MAX` (512) and `WEBSOCKET_RESUME_STASH_BYTES` now cap held sessions, oldest first.
- **Shell sockets reach the Server only after their `hello`** (at most 5 seconds), because a socket that resumes a session must never arrive as a new connection. Other wire formats connect on open, and on an unclean drop they now end at once instead of lingering until the same browser reconnects.
- **The Portal pings every web client and drops the silent ones** ([`ws_protocol.py`](evennia/server/portal/ws_protocol.py)). `WEBCLIENT_PING_DELAY` (default 20 seconds) was set by games and read by nothing. One Portal-wide loop now sends WebSocket pings, which browsers answer without page code, so idle connections stay open through carrier NATs and proxies. A socket with no inbound traffic for `WEBCLIENT_PING_TIMEOUT` (default 60, never below two pings) is aborted and its session held. The host's TCP keepalive cannot do this behind a reverse proxy: the Portal's socket is the proxy's local upstream connection.
- **The shell checks its own socket** ([`evennia.svelte.ts`](evennia/web/webclient/client/src/lib/evennia.svelte.ts)). A page never sees protocol pings, and a phone that changed networks can hold a dead socket "open" for minutes. After 25 seconds of silence the shell sends `{"t": "ping"}`; the Portal answers `pong` itself (never stamped, replayed, or forwarded). No answer in 10 seconds: the shell closes with 4000, which holds the session, and reconnects at once. Coming back to the tab, regaining the network, or restoring from the back-forward cache probes at once or cuts a reconnect backoff short. Close code 4001 raises the quit screen ("Your session has moved to another window.") instead of reconnecting. A resume that lost evicted output says so in the log.
- **Every socket loss is logged** with its cause, close code, whether the close handshake completed, connection age, time since last heard, and outcome (held, ended, dropped before hello). Takeovers log the time offline and frames replayed; refused claims log a warning.
- **A replaced socket changes nothing.** Its late close, frames and timers are ignored, and `PortalSessionHandler.disconnect`/`server_disconnect` no longer remove or disconnect a sessid on behalf of anything but its current holder. New `PortalSessionHandler.owns()` and `rebind()`; `_WSCore.sendPing()`, `abortConnection()` and `last_received`; `AsyncioTransportShim.abortConnection()`.

### Webclient

- **The terminal follows new lines again** ([`GameLog.svelte`](evennia/web/webclient/client/src/components/GameLog.svelte)). Following was delegated to the virtualizer's `followOnAppend`, which decides "at the end?" from the offset its last scroll *event* reported. Server messages are separate socket frames, so a second one routinely landed before the event for the first follow: the stale offset read as the reader having scrolled away, the follow was skipped, and the log never followed again, with no "new lines" bar because the log still believed it was pinned. The log's own `pinned` state (read from wheel and scroll input through `pinAfterScroll`) now decides, and every append and every re-measure (a line typing in, an image loading) ends at the bottom. A reader who scrolled up, by wheel or by dragging the scrollbar, is left in place.
- **Channels follow the same way** ([`ChannelView.svelte`](evennia/web/webclient/client/src/components/ChannelView.svelte)). Pinned state instead of a per-event "near the bottom" guess; a scrolled-up reader is anchored on the message they are reading, including when the 500-message cap drops the oldest under them; a "N new messages" bar; and a `ResizeObserver` for a panel shown again, the "transmitting" line taking a row, or content growing after it rendered.
- **The browser tab flashes when something new arrives while the player is away** ([`tabalert.ts`](evennia/web/webclient/client/src/lib/tabalert.ts), [`notify.svelte.ts`](evennia/web/webclient/client/src/lib/notify.svelte.ts)). New output blinks the title and badges the favicon; a message for the player is named and counted. Settings > Notifications > "Flash the browser tab": on anything new (default), on messages to me, never. Reduced motion and screen-reader mode hold a steady marker instead of blinking.
- **The staff ticket queue is staff-only on screen as well as on the wire** ([`Workspace.svelte`](evennia/web/webclient/client/src/components/Workspace.svelte), [`chat.svelte.ts`](evennia/web/webclient/client/src/lib/chat.svelte.ts)). The shell decided a session was staff when a `ticket_inbox` arrived and never unlearned it, and dockview layouts are saved per browser, so a layout saved from a staff session gave the next player on that browser the staff panel beside their own My Tickets. The server now states the role (`ticket_role`, a game event); the panel is removed for anyone it says is not staff, a stray inbox cannot overrule it, and the panel is renamed **Ticket Queue**.
- **Ticket panels act in place** ([`MyTicketsPanel.svelte`](evennia/web/webclient/client/src/components/MyTicketsPanel.svelte), [`TicketsPanel.svelte`](evennia/web/webclient/client/src/components/TicketsPanel.svelte)). Every panel button typed a command, and every command printed its confirmation ("Posted.", "Ticket #... closed.") into the terminal. Both panels now call the game's `ticket_act` / `my_ticket_act` / `my_ticket_open` RPCs and show the answer in the panel. My Tickets gains search, Open / Waiting on you / All, sorting, unread markers, filing a request in the panel, withdraw, and reply-to-reopen; the queue gains search, Needs reply / On player / Unclaimed, decision reasons and Reopen. A staff reply or status change for a player's ticket that is not in front of them raises a toast that opens it (toasts may now carry an `open` action).
- **The web client reports its terminal size, as telnet's NAWS does** ([`screensize.ts`](evennia/web/webclient/client/src/lib/screensize.ts), [`GameLog.svelte`](evennia/web/webclient/client/src/components/GameLog.svelte), [`main.ts`](evennia/web/webclient/client/src/main.ts)). The server lays out everything width-aware from the session's `SCREENWIDTH` / `SCREENHEIGHT` (help, tables, headers, `who`, EvMore paging), and a telnet client keeps those current with NAWS. The web client never reported them, so every web session was laid out for the 78x45 default whatever its window: tables broke across lines on a narrow window and sat in a strip on a wide one. The game log now measures the character grid it shows (its own font, less the timestamp gutter when timestamps are on) and sends it through the stock `client_options` inputfunc on connect and after each resize or font change, debounced to one report per change. A hidden panel reports nothing.
- **Help opens in its own panel beside the terminal** ([`HelpPanel.svelte`](evennia/web/webclient/client/src/components/HelpPanel.svelte), [`help.svelte.ts`](evennia/web/webclient/client/src/lib/help.svelte.ts), [`helpText.ts`](evennia/web/webclient/client/src/lib/helpText.ts)). A dockable Help panel shows the page from the game's `help_view` event, with back and forward, a search box and links between topics, and reflows to the panel's width. Browsing goes over the `help:help_view` and `help:help_search` RPCs, so reading help never prints into the terminal. F1 opens the panel. Settings > "Help in its own panel" sends help to the terminal instead; the choice goes to the game through `help:help_prefs` on connect and whenever it changes. The client advertises `helpPanel` in its capabilities, so a game sends the panel event only to clients that can show it. `help_view` joins the OOB event types ([`oob-events.ts`](evennia/web/webclient/client/src/lib/oob-events.ts)).
- **One console look, plain labels** ([`shell.css`](evennia/web/webclient/client/src/styles/shell.css) and every panel). The panels each drew their own outlined sentence-case buttons, pill chips with coloured dots and striped cards, and the staff queue used GitHub green and Radix red: it read as a web dashboard. Every control now comes from a few shared console pieces: `sh-cmd` (a `[ BRACKETED ]` command in capitals, inverse video on hover), `sh-toggle` (a square lamp, lit or dark, carrying `aria-pressed`/`aria-checked`), `sh-plate` (an inverse status block), `sh-count`, `sh-field`, `sh-row` and `sh-label`. Markup keeps words in sentence case and CSS sets the capitals, so a screen reader says "Request", not letters; the brackets and row markers are generated content with empty alt text. Copy is short and plain: My Tickets reads REQUEST / BACK / SEND / WITHDRAW / REOPEN with OPEN / WAITING / ALL filters and a NEWEST/OLDEST sort command; hint sentences, "e.g." placeholders and trailing ellipses are gone. The top-left link state reads CONNECTED / DISCONNECTED / CONNECTING / LINK ERROR beside a square lamp. Nothing in the client's own text role-plays a device any more: the intro says what the client is loading, the room panel says "No room yet", and the disconnect screen says "You have left Underspire.", or "Your session has moved to another window." when another tab has taken the session, with no icon and no hint line. The settings hub is a numbered menu, the channel prompt is the channel's name (`PUBLIC>`), the media panel's emoji volume glyph is a `VOL` label, and ember particles are off by default (the setting remains). The help panel uses the same pieces: a `HELP>` command line with BACK / FORWARD / TOPICS, the index set in columns under ruled category heads as a MUD prints its help list, ruled result rows, and section heads on a rule, with the chips, card rows and glyph buttons gone. `tests/preview.html` mounts the whole shell with a seeded session for design review (`?help=<topic>` opens the help panel on a recorded page).
- **Up and Down no longer throw away the line being typed, and the command line wraps** ([`history.ts`](evennia/web/webclient/client/src/lib/history.ts), [`textarea.ts`](evennia/web/webclient/client/src/lib/textarea.ts), [`CommandInput.svelte`](evennia/web/webclient/client/src/components/CommandInput.svelte)). Up replaced a half-typed line with the newest command, and Down past the newest set the line to empty, so reaching for an old command in the middle of a pose lost the pose. The line being typed is now the bottom slot of the history: Up keeps it, Down past the newest command brings it back, and sending a recalled command puts it back on the line. An edit to a recalled command is kept while the player walks the history, as readline keeps it, until something is sent; accepting a Ctrl+R match keeps the typed line the same way. The command line is a textarea that grows with its text to 40% of the window, so a long pose can be read whole while it is written. Enter sends and Shift+Enter starts a new line; the whole text goes as one command, as the compose pad sends it. Up and Down move the caret through a wrapped or multi-line text and walk the history from its first or last line; a recalled command left as it came keeps walking from anywhere in it. Settings > Text > "Up and Down recall commands" can keep typed text out of the walk instead ("only from an empty line"). Enter is left alone while an IME composes, including Safari's confirming keystroke; a line break with no Shift+Enter behind it sends, since some phone keyboards deliver Enter only that way; recalled commands are announced to screen readers; the field carries `enterkeyhint="send"`; and a multi-line paste can go into the line as text as well as run as separate commands.
- **The compose pad can stay open after sending** ([`CommandInput.svelte`](evennia/web/webclient/client/src/components/CommandInput.svelte), [`compose.svelte.ts`](evennia/web/webclient/client/src/lib/compose.svelte.ts)). The pad closed after every send, so a scene of poses meant reopening it each time. "Keep open" in the pad, the same setting as Settings > Text > "Keep compose open after sending", leaves it open, empty and in the same mode. Closing the pad hands the keys back to the command line. Opening the pad no longer overwrites a saved draft with the command line's text, and sending from the pad no longer clears the command line.

### Telnet

- **The portal asks telnet clients for UTF-8, and folds text for those that never confirm it** ([`charset.py`](evennia/server/portal/charset.py), [`textfold.py`](evennia/utils/textfold.py), [`telnet.py`](evennia/server/portal/telnet.py)). Game text is Unicode and always went out as UTF-8, but the portal never told a telnet client so, and many start in ASCII or Latin-1. Every box-drawing frame, bar and arrow then arrived as bytes the client could not decode: replacement diamonds in Mudlet, `â”€` mojibake elsewhere, a status screen turned to noise. The portal now offers UTF-8 through the telnet CHARSET option (RFC 2066) on connect; a client that accepts (Mudlet and tintin++ do, unprompted) is marked `UTF-8` and gets Unicode unchanged, as does one reporting the UTF-8 bit in MTTS or a player who sets `@option utf-8 = on`. The negotiation is one more telnet handshake (the count goes from 8 to 9), so the first screen still waits for the answer, and an acceptance that lands after the handshake timeout is synced to the server on its own. A session that never confirms UTF-8 gets its text (lines and prompts) folded by `evennia.utils.textfold.fold_text`: `─` to `-`, `│` to `|`, corners to `+`, `█` to `#`, `░` to `.`, `→` to `>`, `—` to `-`, `é` to `e`, anything else to `?`. Every character folds to exactly one, so frames and tables laid out one column per character stay aligned. A legacy encoding the player chose with `@option encoding` (such as `cp437`) keeps every character it can carry and folds only the rest. ASCII text is returned untouched after one `str.isascii()` check.
- **New setting `TELNET_ASCII_FALLBACK`** (default `True`). Set `False` to send Unicode to every telnet client as before.

### Server

- **`client_options` bounds screen sizes as NAWS does** ([`inputfuncs.py`](evennia/server/inputfuncs.py)). `screenwidth` / `screenheight` took any integer; a zero or negative width reached EvTable, which raises on it, and a huge one had help and headers build lines that long. Both are now held to 1-65535, the range a NAWS report can carry.

### Settings

- `WEBCLIENT_PING_DELAY` (20) is now read. New `WEBCLIENT_PING_TIMEOUT` (60) and `WEBCLIENT_RESUME_GRACE` (90; 0 turns holding off).

### Migration

- The `hello` reply gains `token` (and `gap` when replay lost output); clients may send `ping` and get `pong`. A shell bundle from before this release presents a token the Portal never issued, so it reconnects into a fresh session until the page reloads.
- A signed-in web session whose socket drops now stays connected on the Server for up to `WEBCLIENT_RESUME_GRACE` seconds. Game code that reacts to disconnects sees them that much later, and only if the player does not come back.
- Subclasses of `WebSocketClient` that overrode `onClose` or read `websocket_close_code` must move to `_link_down` / `_end_session`.
- The per-tab `sessionStorage` key (`underspire.client.token`) is unchanged.
- Telnet clients that have not confirmed UTF-8 now receive folded ASCII instead of raw UTF-8. A client that decodes UTF-8 without saying so (no CHARSET, no MTTS bit) sees plain frames until the player sets `@option/save utf-8 = on`; set `TELNET_ASCII_FALLBACK = False` to keep the old behaviour for everyone.
- A game with an `AZABAN_PUBLIC_ACTIONS` allowlist must add `client_options` for the web client's size report to reach the server; without it the report is dropped at the portal and web sessions stay at the default size.
- `compose.take()` no longer closes the compose pad. A caller that wants the old behaviour calls `compose.hide()` after it.
- The help panel needs the game to push `help_view` and serve `help:help_view`, `help:help_search` and `help:help_prefs` (allowlisted in `AZABAN_PUBLIC_ACTIONS`). Without them the panel shows its empty state and help stays in the terminal. UNDERSPIRE's `server/conf/inputfuncs.py` and `world/help/` implement all three.
- None otherwise for the engine. The game must register `ticket_role` and serve the ticket RPCs; without them the shell behaves as before (the queue panel follows the inbox, panel actions report the RPC as unavailable).

### Tests

- [`test_webclient_resume.py`](evennia/server/portal/test_webclient_resume.py) (45) drives real sessions through a real `PortalSessionHandler` and checks what the Server is told: holding, the half-closed-socket regression, expiry, caps, takeover without logout or login, token rotation, ownership refusals, superseded sockets, gaps, the deferred connect, the Portal-local heartbeat and the keepalive. [`test_ws_keepalive.py`](evennia/server/portal/test_ws_keepalive.py) (4) checks ping, pong and abort over a real wsproto handshake. [`connection.test.ts`](evennia/web/webclient/client/src/lib/connection.test.ts) (7) covers the shell side. `test_webclient_transport.py` keeps its buffer and hello tests; its stash tests moved to the new module.
- Browser pages ([`tests/`](evennia/web/webclient/client/tests)) for the log under socket-frame bursts, wrapped lines and the typewriter, readers scrolled up by wheel or drag; the channel view; the tab alert; the workspace's staff panel; and both ticket panels against a stubbed connection, asserting no panel action types a command. `BROWSER_PATH` selects a Chromium for `npm run test:browser`. Unit tests for the tab alert and the ticket role.
- [`history.test.ts`](evennia/web/webclient/client/src/lib/history.test.ts) (25) covers the history walk. [`tests/input.html`](evennia/web/webclient/client/tests/input.html) (83 checks) drives the command line and the compose pad in a real browser: the typed line kept and put back, edits kept, caret edges on wrapped and multi-line text, growth and its cap, "only from an empty line", "Keep command after sending", Ctrl+R, paste, IME Enter, the phone-keyboard line break, and the pad's keep-open, focus and draft handling.
- Screen size: unit tests for the grid measurement and report debounce ([`screensize.test.ts`](evennia/web/webclient/client/src/lib/screensize.test.ts)), a browser check of the reported columns across resizes, timestamps and font changes, and `client_options` bounds in [`test_inputfuncs.py`](evennia/server/tests/test_inputfuncs.py).
- Telnet: CHARSET offer, acceptance, rejection, late acceptance and client-initiated requests in [`portal/tests.py`](evennia/server/portal/tests.py); every fold mapping, width preservation and legacy-encoding pass-through in [`test_textfold.py`](evennia/utils/tests/test_textfold.py).
- Help panel: unit tests for the help text renderer ([`helpText.test.ts`](evennia/web/webclient/client/src/lib/helpText.test.ts)) and a browser page ([`tests/help.html`](evennia/web/webclient/client/tests/help.html)) driving the panel against recorded help views.

## 6.0.0+underspire.279 — Silent rule shadowing now logs

### Engine

- **A name collision between provider mixins can no longer delete rules in silence** ([`registry.py`](evennia/actions/registry.py), [`events.py`](evennia/actions/events.py)). `RuleRegistry.collect()` indexes rules by method name across the aggregate class's MRO, so an undecorated method with the same name on any provider in the mix silently drops another mixin's rules regardless of action type. The override-without-`@rule` contract is deliberate and heavily used (277 game overrides of default rules re-declare under the same name), but an unrelated-mixin collision is always a bug and was invisible: a game `alias` verb bound to a method named `carry_out_alias` was shadowed by the default building rules' `carry_out_alias` (bound to `SetObjAlias`), and the dispatch fell through to the fail-closed nomatch with only an empty phase bucket to show for it. `collect()`, and the parallel `EventRegistry.collect()`, now compare the specs the winner re-declares against the specs it drops: unrelated-mixin collisions log a warning naming the method, both classes, and the lost `(action_type, phase)` pairs; same-hierarchy overrides (the documented disable path) log at info level. Overrides that re-declare the same coverage keys stay silent, so existing trees produce no new log volume.

### Tests

- **The warning contract is pinned both directions** ([`test_registry.py`](evennia/actions/tests/test_registry.py), [`test_events.py`](evennia/actions/tests/test_events.py)). Unrelated shadow without re-declaration warns and the shadowed rule stays out of the index; re-declaration under the same name logs nothing; the same-hierarchy drop logs info.
- **The inline-fallback dispatch test patches the live seam** ([`test_dispatch.py`](evennia/actions/tests/test_dispatch.py)). Since push invalidation (`08ac4cd24`) dispatch consults `storage.ensure_authorization`, but the test still patched the older `prewarm_authorization`, so under push-enabled settings the real call succeeded and both fallback assertions failed. It now patches `ensure_authorization` with an `AsyncMock`, which is environment-independent either way.

### Downstream (game-side)

- No changes required; the logging is behavior-neutral. UNDERSPIRE renamed its colliding rules in `4958ed60` before this tag.

## 6.0.0+underspire.278 — Tab completes verbs and scope targets

### Webclient

- **Tab belongs to the command line once it has text** ([`CommandInput.svelte`](evennia/web/webclient/client/src/components/CommandInput.svelte), rebuilt into [`shell.js`](evennia/web/static/webclient/shell/shell.js)). Tab used to fall through to browser focus navigation whenever completion found no match, so typing `!nod` then Tab walked focus to the Compose button mid-word. Tab is now consumed on any non-blank line and still moves focus on an empty one, which keeps the escape hatch that freed keyboard users from the field focused on load.
- **First word completes from a server-pushed verb lexicon** ([`lexicon.svelte.ts`](evennia/web/webclient/client/src/lib/lexicon.svelte.ts)). The shell stores the `completion_lexicon` OOB payload (`{verbs}`) in a new reactive store and merges it ahead of the local history/CURATED/channel sources, so every verb the player can actually reach (and every `!social` token) completes with zero latency instead of the 16-entry curated list.
- **Target words complete from the actor's live scope** ([`CommandInput.svelte`](evennia/web/webclient/client/src/components/CommandInput.svelte)). A non-first word asks `connection.request("client", "complete", {word})` and cycles the returned names; a 250 ms debounce warms the cache while the word is still being typed, and Tab during the round trip completes when the reply lands (if the line has not moved on). Answers cache per word for 5 s and drop on a room change, so cycling is local and a room turnover is at most one request.
- **Downstream (game-side):** the shell degrades to the previous client-only lists until the game wires both ends. It must register a `completion_lexicon` event and push `{verbs}` at hello (reachable registry verbs plus `!social` tokens), and implement a `complete` inputfunc returning `{"names": [...]}` for the puppet's visible room/inventory names, allowlisted in `AZABAN_PUBLIC_ACTIONS`. UNDERSPIRE's `server/conf/inputfuncs.py` implements both. The catalog entry regenerates [`oob-events.ts`](evennia/web/webclient/client/src/lib/oob-events.ts) from the game directory (`python -m evennia.server.protocol.gen_ts`).

### Tests

- **Cache semantics are tested, not the DOM** ([`lexicon.test.ts`](evennia/web/webclient/client/src/lib/lexicon.test.ts)). Seven vitest cases: verb payload filtering, never-fetched versus fetched-empty, one request per word with concurrent dedupe, TTL expiry, failures caching empty without poisoning the word, and room-change reset. `npm test` 631 pass; `svelte-check` clean.

## 6.0.0+underspire.277 — Emote verbs conjugate from pyinflect's main table

### Engine

- **Third-person verb forms come from pyinflect's main table, not its override file** ([`inflection.py`](evennia/utils/inflection.py)). Every `VBZ` entry in pyinflect 0.5.1's `overrides.csv` is wrong, and overrides replace the main table (`infl.csv`) on every lookup. Emotes and poses therefore rendered "she stickes", "he knockes", "it abode", "they shod", and 18 more like them. `pyinflect_module()` now removes the `VBZ` overrides from the loaded pyinflect instance, so `getInflection(w, tag="VBZ")` returns the main-table form (sticks, knocks, abides, shoes, ...). The removal is on the shared instance, so it also covers game code that calls pyinflect after `warm()` or `pyinflect_module()` has run. Other override tags (`NNS`, `VBN`, ...) are unchanged and were not audited. Loading is still lazy. No setting change and no migration.
- **Downstream:** a game that added `irregular_verbs` entries to work around these forms (for example `stick: sticks`, `knock: knocks`) can remove them.

### Tests

- **Every overridden verb conjugates to its main-table form** ([`test_inflection.py`](evennia/utils/tests/test_inflection.py)). Covers all 22 `VBZ` override lemmas through both `pyinflect_module().getInflection` and emote `_conjugate`. The tests skip when pyinflect is not installed, and it is not an engine dependency, so they run only in an environment that has it.

### Webclient

- **Puppet unread badges count only room activity** ([`puppets.svelte.ts`](evennia/web/webclient/client/src/lib/puppets.svelte.ts), rebuilt into [`shell.js`](evennia/web/static/webclient/shell/shell.js); `2bd525cb7`). Every line added to an inactive puppet's feed raised its unread count, so network traffic and the puppet's own command feedback badged the tab as if something had happened in the room. A line now counts only when its `news` meta is `room`; a line with no `news` value counts as `room`. `network` and `self` lines still render in the feed. No server change.
- **Test:** [`puppets.test.ts`](evennia/web/webclient/client/src/lib/puppets.test.ts) adds one `network` and one `self` line to an inactive feed and asserts both render and unread stays 0.

## 6.0.0+underspire.276 — Primary connections clear a leaked read-only default

### Engine

- **A primary database connection is writable even when PgBouncer hands it a backend a replica left read-only** ([`database_postgres.py`](evennia/server/database_postgres.py)). Transaction pooling does not run `DISCARD ALL` unless `server_reset_query_always` is on, so `SET default_transaction_read_only = on` from a replica alias stays on that Postgres backend. The next borrower was the game's `default` alias, whose handshake only set `statement_timeout`. Writes then failed with `cannot execute INSERT/UPDATE/SELECT FOR UPDATE in a read-only transaction`: bug tickets did not save, attribute flushes stalled, and a bus resync could not load the account for a session already marked logged in. `at_disconnect` then raised `AttributeError` on the missing account and the portal dropped the client. The primary handshake now also runs `SET default_transaction_read_only = off` on every connect. Replica aliases still set the flag on. No setting rename and no migration.

### Tests

- **The session-init tests cover the clear** ([`test_database_postgres.py`](evennia/server/tests/test_database_postgres.py)). The default alias asserts `SET default_transaction_read_only = off` both with a statement timeout and when the timeout is disabled. A timeout of zero no longer skips the handshake.

## 6.0.0+underspire.275 — The screen-reader choice persists account-wide

### Account options

- **The webclient panel's screen-reader toggle now saves to the account**
  ([`inputfuncs.py`](evennia/server/inputfuncs.py)). `webclient_options` set
  only the in-RAM session flag, so the choice vanished on restart or reconnect
  unless the player also ran `@option/save`. It now mirrors `SCREENREADER` into
  `account.db._saved_protocol_flags`, the store every restore path reads
  (`at_post_login`, `Session.at_sync`). Only `True` is stored and off pops the
  key: a saved `False` would restore over a live `True` flag and leave the
  shell and the portal's output stripping out of step.
- **`@option screenreader` saves itself, in both the command and the
  action** ([`account.py`](evennia/commands/default/account.py),
  [`account.py`](evennia/actions/default/account.py)). A telnet player's
  choice reset at the next reconnect unless they knew the `/save` switch, the
  same gap the panel toggle just closed. `SCREENREADER` now auto-persists
  whether or not `/save` is given, off is stored as absence, and
  `@option/save`-all no longer stores an off `SCREENREADER` that would restore
  over a live `True`.

### Tests

- New coverage in `evennia/server/tests/test_inputfuncs.py`,
  `evennia/actions/tests/test_default_account.py`, and
  `evennia/commands/default/tests.py` for the auto-save, the pop-on-off rule,
  and `save`-all dropping a saved-off `SCREENREADER`.

## 6.0.0+underspire.274 — Best-effort rule notices and a scrollable ticket thread

### Actions

- **A rule-failure notice can no longer double-fault the engine**
  ([`engine.py`](evennia/actions/engine.py)). `RuleEngine._notify_rule_error`
  delivered through `_send`, which called `msg()` unguarded. If the target's
  JSONB Attribute row was unavailable — the reported case is a rule body that
  ran during dispatch and then deleted the actor, leaving the same dispatch's
  later phases firing against it — the delivery raised again and the player saw
  an "untrapped error occurred" with no usable notice. The notice is now
  best-effort: a failed send is logged and swallowed, so the original rule
  failure is what surfaces.

### Web client: tickets

- **A bug ticket's traceback can no longer bury the thread**
  ([`TicketsPanel.svelte`](evennia/web/webclient/client/src/components/TicketsPanel.svelte),
  [`shell.js`](evennia/web/static/webclient/shell/shell.js),
  [`shell.css`](evennia/web/static/webclient/shell/shell.css)). The payload
  carries `traceback` (up to 8KB) and `character_state`, and every payload entry
  rendered inline: the traceback arrived as one un-capped block whose newlines
  ordinary white-space handling collapsed, and the messages list was the
  detail's only scroll region, so a deep report collapsed it and pushed the
  reply bar out of the clipped group. Payload rows now skip the deep report
  fields — the on-demand `ticket_bug_detail` block already renders them bounded
  and pre-wrapped — and the conversation body (payload, bug report, messages)
  is one scroll region with the ticket header and reply bar pinned.

## 6.0.0+underspire.273 — Feed-routed lines kept in the saved log and restored on unhide

### Web client: feeds

- **A line a feed rule moves out of the terminal is kept in the saved log**
  ([`session.svelte.ts`](evennia/web/webclient/client/src/lib/session.svelte.ts),
  [`GameLog.svelte`](evennia/web/webclient/client/src/components/GameLog.svelte)).
  `session.lines` used to be the only buffer, so a routed-out line existed
  nowhere but the feed: Save wrote the terminal, and a hidden line was missing
  from the downloaded file. The session now keeps an `archive` of every
  non-gagged line in arrival order, and Save reads it — hiding is for play,
  not for the file.
- **Switching "Hide in terminal" off (or deleting the rule) brings the lines
  back.** `pruneMoved` reconciles the terminal with the rules in both
  directions: a line a move-route claims is filed into its feed and taken out
  of the terminal, and a line no rule hides any more returns from the record
  at its arrival position. The existing guarantees hold: a claimed line leaves
  the terminal only once every claiming feed verifiably holds it (by id), a
  full feed that trims it back out keeps the terminal copy, a feed the player
  cleared stays cleared, and the record and terminal trim together so the
  terminal stays a subset. Gagged lines remain excluded from the record.

### Tests

- `npm test`: 623 tests. New coverage: the record keeps a routed-out line, a
  rule switched off or deleted restores its hidden lines in arrival order, a
  cleared feed does not resurface, and the full-feed trim still keeps the
  terminal copy.

## 6.0.0+underspire.272 — Deferred capture rehydration out of caller transactions

### Actions / Attributes

- **Loading an object no longer fails because its JSONB Attribute row state is
  cold inside a caller-owned transaction** ([`state.py`](evennia/actions/state.py)).
  `at_post_load` probes every loaded object for persisted input-captures, which
  needs the holder's JSONB row state; a cold state may not be materialized
  inside `transaction.atomic()` (`AttributeUpdateUsageError: A new JSONB
  Attribute row state requires autocommit`). Any escrowed item resolved under a
  transaction — the reported one is the courier accept path (`accept_order` →
  `claim` → `MoveBatch.deliver` → `resolve_item`) — therefore failed its own
  load and aborted the service with an untrapped error, e.g. accepting any
  courier job after a restart. `rehydrate_captures` now catches that specific
  refusal and retries the probe through `transaction.on_commit`; a rolled-back
  transaction simply skips the probe, which is the correct outcome for
  non-persistent UI state. Loads outside a transaction are unchanged.

### Tests

- New regression test
  [`test_eveditor.py::TestEvEditorReload::test_capture_rehydration_defers_out_of_caller_transaction`](evennia/utils/tests/test_eveditor.py):
  forces a cold row state (after making the persisted marker durable with
  `force_flush`), loads the holder inside `transaction.atomic()` and asserts
  rehydration lands on commit.

## 6.0.0+underspire.271 — Virtualized terminal, command-line focus

### Web client: performance

- **The game log is virtualized** ([`logvirtual.ts`](evennia/web/webclient/client/src/lib/logvirtual.ts),
  [`GameLog.svelte`](evennia/web/webclient/client/src/components/GameLog.svelte)).
  The terminal used to mount every scrollback line as DOM — 5,000 lines with
  colour spans is tens of thousands of nodes, all re-reconciled and re-laid-out
  on every append. It now uses `@tanstack/virtual-core`: a spacer carries the
  full scroll height and only the rows around the viewport are mounted,
  roughly 40 nodes regardless of scrollback depth. A burst (combat spam, a
  help file, a room look) no longer pays for the whole buffer.
- **Anchored by line id, never by row number** (`getItemKey`,
  `anchorTo: "end"`, `followOnAppend`). Following the bottom keeps following
  through appends and trims; a reader scrolled up keeps the exact line and
  pixel offset through an append, a filter change, and the scrollback cap
  trimming 500 lines at once.
- **Typewriter honesty** ([`logreveal.ts`](evennia/web/webclient/client/src/lib/logreveal.ts),
  [`typewriter.ts`](evennia/web/webclient/client/src/lib/typewriter.ts)).
  A line types in only if it is fresh and was visible when it landed; the
  backlog and off-screen arrivals (scrolled up, hidden panel) render instantly
  instead of replaying when scrolled back to.
- **Search, filters and restores.** Search scrolls by row index; timestamps or
  font-metric changes remeasure mounted rows and re-pin the bottom when
  following; a hidden panel restores through the virtualizer.
- The in-memory scrollback cap is unchanged (5,000 lines, trimmed in blocks of
  500). Virtualization removed the DOM cost, not the memory or the portal
  replay window.

### Web client: command line

- **Typing always reaches the command line** ([`focus.ts`](evennia/web/webclient/client/src/lib/focus.ts),
  [`App.svelte`](evennia/web/webclient/client/src/App.svelte)). A plain
  character typed anywhere that is not a field or an open dialog is redirected
  to the command line, and a mouse click in the terminal (a log row, a filter
  chip, the toolbar) returns focus there. Reading or filtering no longer costs
  a second click before typing again, and Enter works after a click.
- A drag-select keeps its selection; a control activated from the keyboard
  (click `detail === 0`) keeps its focus.

### Tests

- `npm test`: 619 tests. New: 17 virtualizer tests (bounded window, follow,
  anchor through append/trim/filter, fold compensation, growth under the
  typewriter), 5 reveal tests, 8 focus tests.
- `npm run test:browser` (new): drives [`tests/log.html`](evennia/web/webclient/client/tests/log.html)
  (30 checks) and the existing `tests/display.html` (49 checks) in an installed
  Edge/Chrome via `playwright-core` (devDependency; downloads no browser).

### Dependencies

- Added `@tanstack/virtual-core` (runtime) and `playwright-core` (dev). No
  settings or API changes; no migration.

## 6.0.0+underspire.270: web client accessibility, feeds, tickets, visual pass

### Web client: accessibility

- **Screen reader mode** ([`SimpleWorkspace.svelte`](evennia/web/webclient/client/src/components/SimpleWorkspace.svelte),
  [`simpleLayout.svelte.ts`](evennia/web/webclient/client/src/lib/simpleLayout.svelte.ts)):
  one view at a time behind a tab list, no visual effects or intro, channel
  messages in the terminal, room music off. It is the first Tab stop on the
  page. Server-opened pages (the grid) open as a view tab and are announced.
- **One voice for speech** ([`announce.svelte.ts`](evennia/web/webclient/client/src/lib/announce.svelte.ts)):
  new output is spoken from an off-screen region, batched per burst and cut to
  the newest 15 lines. The visible log is no longer a live region; the
  typewriter used to make it speak fragments.
- **Keys** ([`keybinds.svelte.ts`](evennia/web/webclient/client/src/lib/keybinds.svelte.ts),
  [`regions.ts`](evennia/web/webclient/client/src/lib/regions.ts)): Alt+I/O/C/R
  jump to the command line, output, channels and scene; Alt+1 to Alt+9 read
  back recent lines; Page Up/Down page the output from the command line.
- **Keyboard fixes**: Tab only completes when there is a match (it trapped
  focus in the command line); key-capture fields let Tab and Escape out;
  dialogs trap focus, close on Escape and return focus
  ([`modal.ts`](evennia/web/webclient/client/src/lib/modal.ts)); the palette is
  a combobox; fake `role=menu` dropdowns are disclosures; channel messages are
  one Tab stop with arrow keys; cards, disconnects and completions are
  announced; controls are labelled.
- **Contrast**: `--fg-faint` and `--fg-dim` meet 4.5:1 in every theme; a global
  focus ring; 24px targets on the densest controls.
- **Server flag**: `webclient_options` now sets the session `SCREENREADER`
  protocol flag and syncs it to the portal
  ([`inputfuncs.py`](evennia/server/inputfuncs.py)), so game output is shaped
  for speech as on telnet. `ServerSession.update_flags` sends the new
  `screenreader_mode` event when the flag is set (a saved `@option` restored at
  login, or `@option`), and the shell turns its mode on to match
  ([`serversession.py`](evennia/server/serversession.py),
  [`core_events.py`](evennia/server/protocol/core_events.py)).

### Web client: feeds

- One pattern parser for feeds and triggers
  ([`pattern.ts`](evennia/web/webclient/client/src/lib/pattern.ts)): plain text
  is literal, `/regex/` is a regex. Broken or match-everything patterns are
  refused and shown in Settings. Saved bare regexes migrate to `/.../i`.
- Rules can be switched off and reordered; renaming a feed keeps its lines; a
  new or edited rule takes matching lines already in the terminal; replayed
  lines are filed once; buffers hold 1000 lines
  ([`routing.svelte.ts`](evennia/web/webclient/client/src/lib/routing.svelte.ts)).
- Gags hide lines from the terminal only; feeds still get them. "Hide in
  terminal" replaces COPY/MOVE.
- The Feeds panel follows new lines, shows a jump-to-latest bar when scrolled
  up, searches, shows times, and opens a feed in its own panel
  ([`SpawnsPanel.svelte`](evennia/web/webclient/client/src/components/SpawnsPanel.svelte)).
  Feed rules have their own Settings view. The terminal gets the same jump bar.

### Web client: tickets, channels, input

- My Tickets loads once the socket is open, shows load errors, refreshes when
  a ticket changes; a player's `@ticket` opens it.
- Channel history pushes merge with what the page holds; messages dedupe by
  id and use a client key.
- A bare Ctrl/Alt/Shift press is never captured as a bind; old
  "Ctrl+Control" binds are cleared on load.
- Settings: echo commands in the terminal; keep the command after sending.
- Web pages from the server open near full size instead of a 100px float, or
  in a browser window; embedded pages have "Open in new window".
- Floating panels no longer cover the Settings dialog.

### Web client: look

- dockview's inner shell carried its own blue theme class, which overrode the
  tab strip colours; the theme variables are now set there too.
- Plain labels on the header and toolbars; Settings opens on a simple list;
  squared sliders and selects.
- A fresh layout gives the scene/channels column a usable width; on a phone
  they are tabs beside the terminal.

### Engine

- `@option` renders structured protocol flags (HTTP_ORDER, HTTP_FP, TLS_FP)
  instead of raising on a bare `@option` from a websocket session.

### Migration notes

- `screenreader_mode` is a new OOB event; nothing needs to handle it.
  Regenerate `oob-events.ts` with `gen_ts` if a game keeps its own copy.
- `shell.js` / `shell.css` are rebuilt.

## 6.0.0+underspire.269 — F5 macro renamed to Stats

### Web client

- **The hotbar button and palette entry read "Stats", not "Score".** The F5
  macro sends `@stats`; its label now matches the verb instead of the legacy
  score convention
  ([`macros.svelte.ts`](evennia/web/webclient/client/src/lib/macros.svelte.ts),
  [`commands.svelte.ts`](evennia/web/webclient/client/src/lib/commands.svelte.ts)).
- **Existing browsers migrate the label too.** `Macros.init` relabels a
  persisted stock macro — the old `score` default or the interim `@stats` one
  — and saves; a player-renamed label or retargeted command is left alone.
  `shell.js` is rebuilt.

### Tests

- [`macros.test.ts`](evennia/web/webclient/client/src/lib/macros.test.ts)
  covers the default label, migration from both stock shapes, and that edited
  macros stay untouched. Client vitest suite — 556 tests OK.

## 6.0.0+underspire.268 — Ticket rows name the filing character

### Web client

- **The `TicketsPanel` names the character, not just the account.** The list
  row and the open-ticket header rendered `account_name`, so a staff reader
  saw the login handle even when the ticket carried the character it belongs
  to. Both now render `requester_name` when the server sends one, with the
  account as the fallback: the list keeps the account as a hover title and
  the open ticket shows it beside the character
  ([`TicketsPanel.svelte`](evennia/web/webclient/client/src/components/TicketsPanel.svelte)).
- **The new-ticket toast follows suit.**
  [`chat.svelte.ts`](evennia/web/webclient/client/src/lib/chat.svelte.ts)
  prefers `requester_name` too, so the first thing a staff member sees about
  an arriving ticket is who filed it. `shell.js`/`shell.css` rebuilt.

### Notes

- Attribution is the server's: the game records the filing session's
  character on `requester_id`, and this client renders `requester_name`
  whenever a ticket carries it. Account-only tickets (browser pages, OOC
  prompts) fall back to the login handle, unchanged.

### Tests

- Client vitest suite — 554 tests OK. No new client test: the panel renders
  the field the server provides, and the attribution rule itself is covered
  by the game's ticket tests.

## 6.0.0+underspire.267 — F5 macro and palette route to @stats

### Web client

- **The stock F5 "Score" macro sends `@stats`.** It shipped `score`, which
  matches no command on the server, so the hotbar button and F5 both returned
  `Huh?` instead of the character sheet. `@stats` is the canonical verb;
  `@sheet` and `@score` remain aliases. The macro default lives in
  [`macros.svelte.ts`](evennia/web/webclient/client/src/lib/macros.svelte.ts).
- **Existing browsers migrate.** `Macros.init` repoints a persisted stock
  macro (`id: "score"`, `command: "score"`) to `@stats` and saves, so a
  browser holding the old default in `localStorage` does not keep the broken
  binding. Player-edited macros are left alone. The command palette's
  "Score / sheet" entry
  ([`commands.svelte.ts`](evennia/web/webclient/client/src/lib/commands.svelte.ts))
  sends `@stats` too. `shell.js` is rebuilt.

### Tests

- [`macros.test.ts`](evennia/web/webclient/client/src/lib/macros.test.ts)
  covers the new default, the persisted-macro migration, and that an edited
  macro is untouched. Client vitest suite — 554 tests OK.

## 6.0.0+underspire.266 — Blank default descriptions

### Engine

- **Desc-less objects and exits render as just their name.** The engine
  defaults [`DefaultObject.default_description`](evennia/objects/object.py)
  ("You see nothing special.") and
  [`DefaultExit.default_description`](evennia/objects/exit.py) ("This is an
  exit.") are now empty. `get_display_desc` still falls back to them, so
  `return_appearance` gets an empty `{desc}` slot, which
  [`format_appearance`](evennia/objects/mixins/appearance.py) compresses
  away. Rooms and characters are unchanged — they keep their own defaults
  ("This is a room." / "This is a character.").

### Migration

- Typeclasses that relied on the old default strings (displaying or
  comparing them) should set their own `default_description` or `db.desc`.
  No API change; only the two class attribute values changed.

### Tests

- [`test_objects.py`](evennia/objects/tests/test_objects.py) pins
  `default_description == ""` and `get_display_desc() == ""` for desc-less
  `DefaultObject` and `DefaultExit`. `evennia.objects` — 121 tests OK in
  the engine test game dir.

## 6.0.0+underspire.265 — Single put echo

### Engine

- **`put` no longer echoes twice to the actor.**
  [`ContainerPutRules.carry_out_put`](evennia/actions/default/objects.py) sent
  the actor a direct line and then a room broadcast that included the actor, so
  `$You()` rendered "You put X in Y." a second time. The broadcast now excludes
  the actor; onlookers still get the third-person line. Fixes `4d59a88f7`.

### Tests

- [`test_default_objects.py`](evennia/actions/tests/test_default_objects.py)
  asserts the put room echo excludes the actor. `evennia.actions` — 505 tests
  OK in the engine test game dir.

## 6.0.0+underspire.264 — Session class re-resolve; webclient autoscroll fix

### Engine

- **`SERVER_SESSION_CLASS` changes take effect.** The class cached by
  `delayed_import` in [`sessionhandler.py`](evennia/server/sessionhandler.py)
  was resolved once and never revisited, so a later override — notably
  `override_settings` in test classes — silently kept handing every new
  session the previously cached class. The setting path is now stored next to
  the class and re-resolved when it differs (`class_from_module` is
  internally cached, so the check is cheap). Fixes `3d665f44a`.

### Web client

- **Log autoscroll no longer unpins on content growth.** The scroll handler
  re-derived pinned state from the bottom gap on every scroll event, but the
  events fired by the client's own `scrollTop` writes were delivered after
  more lines had landed, so a fast burst (or the typewriter reveal) read as
  "user scrolled up" and froze the view. Unpinning now requires evidence of
  intent: a wheel-up over the log (`onwheel`, read synchronously so a
  same-frame append cannot hide it), or a `scrollTop` drop below the top the
  code last wrote. Pure content growth can neither lower `scrollTop` nor move
  it below the recorded write, so it no longer unpins. Decision logic lives
  in [`autoscroll.ts`](evennia/web/webclient/client/src/lib/autoscroll.ts)
  with unit tests; `shell.js` is rebuilt. Fixes `e522d45af`.

### Tests

- `evennia.web` client — 551 tests OK (vitest), `svelte-check` clean.

## 6.0.0+underspire.263 — Fleet-review burndown: correctness, consolidation, closed-set metrics

Forty-seven findings from the engine perf-sweep fleet review (run-20260919),
burned down over 35 commits and rebased onto `.262`. Every commit landed with
a red-first falsifier test; the surviving tests are the regression net.

### Observability

- **`evennia_bus_rejected_total{reason}` labels are a closed set.** The three
  publish-rejection reasons now live in
  [`bus_result.py`](evennia/server/bus_result.py), shared by the
  [`redis_transport.py`](evennia/server/redis_transport.py) producer and the
  [`prometheus_metrics.py`](evennia/server/prometheus_metrics.py) recorder.
  Before, the 37-character `encoded frame exceeds transport limit` reason was
  emitted truncated to 32 characters (trailing space), and any other string
  created ad-hoc series. Out-of-set values now collapse to `unknown`; queries
  matching the old truncated value need updating.
- **Telemetry failures are logged, not swallowed.** Every metrics call on a
  hot path now goes through `best_effort()`
  ([`prometheus_metrics.py`](evennia/server/prometheus_metrics.py)) or a
  scoped `except` with `log_trace`, replacing bare `except Exception: pass`
  in authorization, sessionhandler, clock, narrative, and the server services.
  A broken recorder is now visible instead of silently blinding dashboards.
- **Bus publish counter batches.** `record_bus_publish(count)` takes the
  batch's published-frame count in one increment instead of one call per
  frame.

### Redis bus and transport

- **Group output is capability-gated (rollout-safe).** `send_MsgServer2PortalMany`
  on the server bus ([`redis_bus.py`](evennia/server/redis_bus.py)) falls back
  to per-session `MsgServer2Portal` frames unless the Portal declared the
  `MsgServer2PortalMany` capability in its handshake snapshot. A new server
  paired with an old Portal degrades to one frame per session; no rollout
  window drops output. An unexpected frame command now logs and renegotiates
  the transport instead of being dropped silently.
- **Handshake offers are keyed by portal identity.** Offers from distinct
  portal processes can no longer overwrite each other's in-flight challenge
  ([`bus_handshake.py`](evennia/server/bus_handshake.py)).
- **`BUS_HANDSHAKE_STALENESS` is a new setting.** Replay/staleness defenses
  (challenge acceptance, delayed final acks) are bounded by this knob, default
  4s; `BUS_HANDSHAKE_TIMEOUT` now governs only the liveness lease. Games that
  widened `BUS_HANDSHAKE_TIMEOUT` to tolerate long synchronous turns keep
  their lease; the replay window stops widening with it. The value is floored
  at the 1s heartbeat and capped at `BUS_HANDSHAKE_TIMEOUT`.
- **Writer hardening.** A published batch settles before telemetry is sampled
  (broken metrics can no longer abort settlement), per-command `XADD` error
  responses escalate via `fail()` with a warning like the pre-batching path
  did, and transport caps resolve once into a `BusLimits` object instead of
  per-publish settings lookups.
- **Settings resolution convention.** Engine tunables (bus caps, render
  bounds, authz flags) resolve through `cached_setting()` once per use site
  with a documented resolution order, replacing ad-hoc
  `getattr(settings, ...)` chains and None-sentinel handling
  ([`settings_default.py`](evennia/settings_default.py),
  [`utils.py`](evennia/utils/utils.py)).

### Authorization

- **One grant write path.** `grant_capability` is now the one-element case of
  `_apply_grants` ([`storage.py`](evennia/authorization/storage.py)): lock,
  upsert, audit, and generation bump exist once. `grant_capabilities` is one
  transaction and one generation bump per call regardless of capability
  count (staff promotion grants 57 capabilities; that was 57 transactions).
  **Behavior change:** bulk-granting to `group:<ref>` now raises `ValueError`
  for an unknown group instead of silently writing rows on a nonexistent
  group.
- **One snapshot pipeline.** The inline loaders and the off-loop prewarm
  installer build grant/resource/policy snapshots through shared builders and
  one staleness predicate, so the two paths cannot drift
  (`_grant_snapshot`, `_resource_snapshot`, `_policy_overrides`,
  `_snapshot_is_valid`, and a single snapshot-miss chokepoint). Stored scope
  labels are normalized identically on both paths.
- **Prewarm fault isolation.** A queued object whose typeclass fails to load
  is dropped with a log and flips the batch to not-ready instead of killing
  the shared flush task and raising inside every waiting action. Coalescing
  state is per event loop, which also fixes a crash when two loops (test
  runners, secondary servers) used the prewarm back-to-back; the push-
  invalidation cold-fact path (`ensure_authorization`) rides the same state.
- **Deferred generation publishes never lower the cached generation.**

### Movement and default commands

- **One movement commit/hook pipeline.** Sync `move_to` and async
  `move_to_async` share the veto gates, the compare-and-set commit
  (`ObjectDB.compare_and_set_location`, with the no-op branch verified by
  SELECT instead of a silent write), cache reconciliation, and the post-commit
  hook sequence ([`movement.py`](evennia/objects/mixins/movement.py),
  [`models.py`](evennia/objects/models.py)). All six move hooks' `fires_from`
  now list both drivers.
- **Async announce semantics changed (game hooks).** On `move_to_async`,
  `announce_move_from` fires *after* the location row commits — a lost
  compare-and-set no longer tells the source room the mover left — and
  receives a keyword-only `origin=`. Overrides of `announce_move_from` should
  accept `**kwargs`. The location-loop guard is shared with the sync driver
  (`check_location_loop`), closing the boundary disagreement where one driver
  rejected a through-exit move the other allowed.
- **`get`/`drop`/`give`/`put`/`home` commit via `move_to_async`**
  ([`actions/default/objects.py`](evennia/actions/default/objects.py),
  [`general.py`](evennia/actions/default/general.py)). **Migration note:**
  game-side `move_to` overrides are not consulted for these commands'
  commits; move-policy hooks still gate them. Games that customized `move_to`
  and rely on the default commands honoring it must override the
  corresponding rule or `move_to_async`.

### Attribute persistence

- **Chained async deltas spool instead of failing.** When an async flush finds
  a pending DELTA intent from a spooled earlier flush, it appends the chained
  delta ([`jsonb_handler.py`](evennia/typeclasses/jsonb_handler.py)) instead
  of quarantining the row as `blocked`; a genuine `PREPARED_BLOCKING` witness
  still quarantines, correctly.
- **Sync flushes yield to the async worker owning a row.** A per-row flush-
  claim registry lets `force_flush`/`flush_dirty` wait for the in-flight
  worker's outcome on that row instead of racing it or raising
  `AttributeUpdateUnavailable` spuriously.
- **The vestigial legacy dirty-backend registry is deleted**;
  `discard_dirty_backends()` is jsonb-only.

### Sessions

- **A duplicate-login kick now sends the `logout` OOB before disconnecting.**
  A bare close read as a network drop, so the resumable web shell
  auto-reconnected, browser-session auto-login re-hit the kick, and two tabs
  ping-ponged forever.

### Narrative, commands, misc fixes

- RenderNode validation is scoped to render construction (N1); the CM1
  completion-marker contract is documented where it is enforced; the
  per-object tag miss cache is bounded and invalidation is centralized in the
  tag cache; the dead legacy dirty-backend metric path is gone; the bus
  backpressure documentation is qualified to the outgoing path.

### Webclient

- **Feed data-loss trio fixed** (routing draft commits on blur/explicit
  action, id-keyed feeds with `holds()` as the filing truth, prune only when
  no feed claims the line, backfill merged/trimmed by `ts`). The committed
  bundle (`shell.js`) is rebuilt from the merged `.262` source.

### Tests

- Every burndown item ships its falsifier test; the declarative
  `evennia_fixtures` selection landed in the hot typeclasses suites.
  Full engine suite post-rebase: 4635 tests, OK (57 skipped), against
  `underspire.262`.

## 6.0.0+underspire.262 — Font-independent quit mark

### Changes

- **The quit overlay draws its power mark as inline SVG.** U+23FB (`⏻`) sits
  outside the shell-glyph fallback range, so the Latin-only webfont stacks
  rendered it as a missing-glyph box. `QuitOverlay.svelte` now inlines the mark
  as SVG, keeping it font-independent and carrying the glow through
  `drop-shadow`. The shipped shell bundle (`shell.js`, `shell.css`) is rebuilt
  from the new source.

### Tests

- `evennia.web` — 149 tests OK.

## 6.0.0+underspire.261 — Bus input-queue observability

### Changes

- **The Redis bus gauges its incoming queue.** Run 13 showed every command
  waiting ~1.4s end-to-end while per-command work stayed at 1–20ms, with the
  server process at 77–100% CPU and the portal, Redis and Postgres idle. The one
  unobserved link was the queue between the stream read and its reactor turn.
  `evennia_bus_incoming_wait_seconds` now records that wait per frame, and
  `evennia_bus_incoming_depth` / `evennia_bus_incoming_bytes` gauge what is
  still queued. On the server this is the portal→server command latency; on the
  portal it is the server→portal output latency.

### Game-side (same window)

- The load harness validates that bots are actually in the world before
  driving: a post-login `look` whose reply matches a login/chargen/menu prompt
  aborts the run (unless `--allow-unready`), so a prompt handler can never be
  mistaken for game load again.

### Tests

- Redis transport suites — 67 tests OK; harness and sampler tests — 13 OK.

## 6.0.0+underspire.260 — Run-13 diagnostics and metric honesty

### Changes

- **Idmapper sweeps report active reactor time.**
  `evennia_idmapper_flush_duration_seconds` is the sweep's wall span, which
  includes worker round-trips and scheduling gaps; in the impolite run it read
  415s and invited a wrong conclusion. A new
  `evennia_idmapper_flush_active_seconds` histogram records the reactor time
  actually spent applying turns, and `record_idmapper_flush` documents the
  distinction. The sweep itself is unchanged: it is pressure-driven and already
  bounded per turn.
- **Dispatch diagnostics.** `ACTION_SUSPENSION_WARN_MS` logs the rule body that
  holds a dispatch open past a threshold (generator and deferred suspensions),
  and `ACTION_DISPATCH_WARN_MS` logs bridge dispatches split into parse and
  dispatch. Both default to `0.0` (off); run 13 enables them at 5ms to
  attribute the fixed per-command wait that a CPU profile cannot see.

### Game-side (same window)

- The metrics sampler records host load, available memory, and the CPU share of
  server/portal/Redis/Postgres from `/proc` (no new dependency).
- Harness per-kind latency percentiles pool across shards (already committed).

### Tests

- `evennia.actions` — 497 tests OK; targeted diagnostics/idmapper tests — 112
  OK; sampler and harness tests — 11 OK.

### Measurement context

Run 12 (impolite, 4 harness shards, 100 bots): server p50 7.4ms / p99 29.8ms,
portal 5% busy, yet rtt p50 1,105ms with `bus_outgoing_depth` averaging 72.
The lag is server-side queueing — commands and completion markers wait behind
the reactor's turns — not portal CPU.

## 6.0.0+underspire.259 — Allocation churn and output-path stalls

### Changes

- **Hot dataclasses use slots.** `RuleResult`, `PhaseTrace`, `ActionTrace` and
  the bus `_Outgoing` frame are instantiated per rule, per dispatch and per
  published frame. py-spy labelled their generated `__init__` as
  `<string>:2` (dataclasses compile it under that filename) at ~5% of reactor
  CPU, and the same churn showed up as weakref/GC stalls. `slots=True` removes
  the per-instance dict and the GC traversal that came with it.
- **Movement activity dispatches stop recording phase traces.** `walk`-driven
  `Move` dispatches used `engine.dispatch` with the default
  `record_phases=True`, building a `PhaseTrace` per fired rule with no
  consumer; the production bridge already passes `record_phases=False`.
- **Command trace ids are a monotonic counter** instead of `uuid4().hex` per
  command, keeping the 16-character format for log correlation.
- **`inherits_from` memoizes the parent path** as well as the MRO path list.
- **Output flush bounds frame grouping.** Single-session turns skip the
  structural grouping key entirely (there is nothing to share), and the key
  builder raises past a depth budget instead of producing a truncated key that
  could group two different frames; that frame then ships alone.

### Game-side (same release window)

- Freight lifts resolve dbrefs through the idmapper with one indexed query as
  fallback, replacing `search_object("#id")` at six sites.
- The load harness gains `--processes N` sharding with pooled percentile
  merging, so one Python process is no longer the bottleneck under spam load.

### Measurement

Run 11 (100 bots, think 0.2–0.5s, 38,169 commands): server p50 9.6 / p95 20.4
/ p99 26.1ms, 0 errors, 0 timeouts — but end-to-end rtt p50 was 1,177ms with
`bus_outgoing_depth` peaking at 157 (normal ~5). The queueing is in the
server→bus→portal→socket path and in the single-process test client, not the
reactor; run 12 profiles the portal and shards the harness.

### Tests

- Engine sweeps green on an isolated test database: `evennia.authorization`,
  `evennia.actions`, `evennia.objects` — 733 tests; `evennia.typeclasses`,
  `evennia.utils`, `evennia.server` — 1822 tests (20 skips).
- Game: freight suites 45 tests OK; harness completion tests 4 OK. One
  pre-existing courier failure (`test_move_action_steps_toward_shop`)
  reproduces with these changes stashed and is not from this release.

## 6.0.0+underspire.258 — Action parser and authorization hot paths

### Changes

- **Verb suggestions serve from a deletion index.** `suggest_verbs` uses a
  SymSpell-style deletion index (lazy per max distance, invalidated on
  register) verified with a banded edit distance; the exact linear scan
  remains for distances past the index cap. On the live 1130-verb registry a
  typo dropped from ~19–38ms to 12–300µs with identical results. `Action.parse`
  compiles each class's field schema once instead of re-resolving type hints
  per call, and `RuleEngine` skips the awaitable probe for `RuleResult` and
  `None` — the two dominant rule shapes. The trie walk abandons the leaf scan
  at two leaves, and the combined no-space-prefix verb tuple is cached.
- **Attribute read path.** `_require_io_thread` no longer imports
  `evennia.utils.clock` on every call — the import machinery ran on every
  attribute read (py-spy: 5% of reactor CPU, including `importlib` frames).
  The read and mutation guards resolve deferred model-save outcomes once
  instead of twice, and `is_io_thread`/`is_io_owner` read the bound loop once
  and compare the recorded owner thread instead of probing asyncio per call.
- **Static settings are cached.** `cached_setting` memoizes a setting value
  and drops the cache when Django sends `setting_changed`, so
  `override_settings` behaves exactly as before. Applied to
  `TYPECLASS_AGGRESSIVE_CACHE` (read on every attribute access) and the
  authorization invalidation/snapshot settings.
- **`_generation_cache_key` memoizes its sha256** per `(namespace, key)`;
  the same fact is looked up on every authorization evaluation.
- **Render-scoped authorization memo.** `decision_scope()` caches
  `authorize()` and `has_capability()` answers for the duration of one render;
  `at_look_node` and `filter_visible` (engine and game) share the scope, so
  per-object visibility filtering stops re-evaluating identical questions.
  Entries pin their principal and resource, so a recycled `id()` can never
  alias; a scope closes on exit and closed scopes are ignored, so async tasks
  cannot inherit a dead memo. `evennia_authorization_decision_cache_hits_total`
  exposes the saving; the decision counters now measure evaluations rather
  than checks.

### Measurement

py-spy on the 100-bot run (540s, `MainThread` 383s busy) attributed reactor
CPU to: Django LazyObject settings reads 4.3%, verb suggestions 4.1%, JSONB
read-path guards ~5%, and per-object authorization inside visibility filtering
4–5%, plus ORM on the reactor 1.7%.

### Tests

- Engine sweeps green: `evennia.authorization`, `evennia.actions`,
  `evennia.objects` — 733 tests; `evennia.typeclasses`, `evennia.utils`,
  `evennia.server` — 1822 tests (20 skips).
- New: render-scope memoization tests (`test_service_scope.py`), cached-setting
  override coverage, and the existing override tests for
  `TYPECLASS_AGGRESSIVE_CACHE` still pass.

## 6.0.0+underspire.257 — Revert flush commit batching

### Changes

- **Reverts the `.256` flush commit batching.** Production measurement
  (`bench_attrs flush_batch`, 20 rows) showed no improvement — 25.33ms ±0.38
  before, 27.62ms ±1.44 after. Per-row commits were never the cost: the
  per-row `SELECT ... FOR UPDATE` round trip dominates the worker, and the
  flush fire's ~580ms is prepare/adopt on the reactor (three `deepcopy`s per
  row plus cache sync), which commit batching does not touch. The worker
  returns to one transaction per row, restoring per-row partial progress on
  a batch-level failure.
- The savepoint-isolation tests added alongside the batching remain as
  behavior coverage: a missing or conflicting row must not affect the rows
  persisted in the same worker call.

## 6.0.0+underspire.256 — Attribute and render hot-path performance

### Changes

- **`inherits_from` memoizes class MRO paths.**
  [`utils.py`](evennia/utils/utils.py) rebuilt module-qualified path strings
  by walking `__class__.mro()` on every call, and the helper runs per visible
  object in look rendering (`filter_visible`). Path tuples are now cached per
  class object. The mapping is immutable for a class's lifetime, so there is
  no invalidation story: a redefined class is a new key.
- **The write-behind flush worker commits once per batch.**
  [`jsonb_handler.py`](evennia/typeclasses/jsonb_handler.py) opened one
  transaction per dirty row in the async worker, paying commit latency per
  row. Snapshots sharing a database alias now commit together; each row's
  atomic block nests as a savepoint, so a single failed row rolls back alone
  while the batch keeps one commit. This is what the write-behind design
  promises and what the `bench_attrs flush_batch` scenario measures on
  production (20 rows).
- **`principal.controls_resource` is pure again.**
  [`engine.py`](evennia/authorization/engine.py) read `.account` on unloaded
  objects, fetching the related account row on every evaluation — caught by
  the predicate purity guard in `log` mode. It now resolves the live
  puppeteer and the `db_account_id` foreign-key column, falling back to
  attribute lookups only for duck-typed principals.

### Tests

- Engine sweep green: `evennia.utils.tests.test_utils`,
  `evennia.authorization.tests.test_engine`,
  `evennia.typeclasses.tests.test_jsonb`,
  `evennia.typeclasses.tests.test_attribute_metrics`,
  `evennia.server.tests.test_engine_systems` — 286 tests.
- New: `inherits_from` memoization coverage in `test_utils.py`.

## 6.0.0+underspire.255 — Authorization model hardening

### Features

- **Principal groups as grant subjects.**
  [`AuthorizationPrincipalGroup`](evennia/server/models.py) and
  [`AuthorizationGroupMembership`](evennia/server/models.py) add named,
  exact-membership sets; grants may target `group:<ref>`. Membership lives in
  the principal snapshot, so joining or leaving is one principal event and a
  grant edited on a group invalidates every member's snapshot without
  fan-out. Groups are provisioning conveniences over the flat capability
  evaluator: no rank thresholds, no transitivity, and rank stays data.
  Group mutations are audited; deleting a group revokes its grants so a
  recreated ref cannot resurrect authority.
- **Compiled policy bundles.**
  [`AuthorizationPolicyBundle`](evennia/server/models.py) compiles instance
  policy overrides into a versioned document. Activating a bundle is one
  atomic policy release (or rollback) and bumps a policy generation that
  clears every compiled package cache in every process. Bundles are opt-in:
  without an active bundle, live override rows remain authoritative exactly
  as before.
- **Grant constraint registry.**
  [`policy.py`](evennia/authorization/policy.py) replaces the `session_id`
  special case with a pure, engine-agnostic registry: `session_id`, `online`,
  `time_window` (server-local), and `requires_label`. Constraints are
  validated at write time and fail closed at read.
- **Predicate purity enforcement.** Predicate registration rejects
  async/generator providers, and a Django query guard plus
  `AUTHORIZATION_PREDICATE_PURITY` (`log` default, `error` for CI) catches
  predicates and constraints that perform I/O during evaluation.

### Behavior changes

- A cold principal snapshot now reads group membership plus grants (two
  indexed queries) and stores `group_refs` in `GrantSnapshot`.
- A cold policy load probes the active bundle before reading override rows
  (two indexed queries, then local).
- `time_window` constraints use server-local wall-clock time.
- Engine defaults are unchanged: push invalidation, bundles, and `error`
  purity mode are all opt-in.

### Migration

- New migration
  [`0012_authorization_groups_and_policy_bundle`](evennia/server/migrations/0012_authorization_groups_and_policy_bundle.py).
  No setting changes are required; existing grants, labels, and overrides
  keep working untouched.

### Tests

- Engine sweep green: `evennia.authorization`, `evennia.actions`,
  `evennia.objects` — 705 tests; `evennia.server` + `evennia.utils` — 1504
  tests (20 expected skips).
- New: 17 model-hardening tests (groups, bundles, constraints, purity) and 4
  hypothesis property/fuzz suites (policy serde round-trip, composition
  laws, malformed-data fail-closed, constraint validation totality).

## 6.0.0+underspire.254 — Push authorization invalidation

### Performance

- **Authorization invalidation is now push-based over a durable Redis
  Stream.** [`invalidation.py`](evennia/authorization/invalidation.py) appends
  one `(revision, namespace, ref, generation)` event per mutation and every
  authorization process applies events from its own persisted cursor. Warm
  decisions become local dictionary reads with no Redis polling; grant
  revocations, suspensions, policy edits, and scope changes propagate across
  Server and web processes in milliseconds instead of the two-second poll
  window; Redis traffic scales with actual mutations instead of command
  volume. Run 7's residual cost was prewarm TTL revalidation over the
  destination occupant set (18,400 waits, ~245 ms mean); with push enabled
  readiness no longer expires with a poll TTL, so commands stop waiting on
  generation polls.
- **Cold facts cost one coalesced off-loop fetch.**
  [`ensure_authorization`](evennia/authorization/storage.py) returns
  immediately when facts are local and otherwise awaits a single shared
  refresh, with the ordinary inline read as the counted last resort.

### Reliability

- The reader applies generations monotonically and drops affected snapshots.
  A revision gap, a revision that advanced without an event (failed append or
  dead reader), or a cursor lost to stream retention reconciles by flushing
  local facts, which then refill lazily; a slow anti-entropy check bounds
  staleness as disaster recovery. Baseline reads, cursor persistence, and the
  anti-entropy checks all run off the reactor, and the reader is stopped on
  Server/Portal shutdown.
- Plain pub/sub is deliberately not used (reloads and connection losses
  discard events) and neither are consumer groups (each process must observe
  the full invalidation sequence).

### Configuration

- `AUTHORIZATION_PUSH_INVALIDATION` (default `False`) plus
  `AUTHORIZATION_INVALIDATION_REDIS_ALIAS`, `..._STREAM`, `..._REVISION_KEY`,
  `..._CURSOR_KEY`, `..._CONSUMER`, `..._MAXLEN`, `..._READ_BLOCK_MS`, and
  `..._RECONCILE_SECONDS`. The polling path is unchanged when disabled.

### Observability

- `evennia_authorization_invalidation_events_total`,
  `evennia_authorization_invalidation_reconciles_total{reason}`,
  `evennia_authorization_invalidation_errors_total`, and
  `evennia_authorization_invalidation_revision`.

### Tests

- New [`test_invalidation.py`](evennia/authorization/tests/test_invalidation.py)
  (21 tests): revisioned publish, monotonic apply, snapshot drops, gap
  reconcile, malformed events, cursor persistence and replay, baseline
  selection, two-strike anti-entropy, reader thread apply/reconnect,
  two-process simulation, and the `ensure_authorization` ready/cold/failure
  paths.
- Engine sweep green on this change: `evennia.authorization`,
  `evennia.actions`, `evennia.objects` — 681 tests; `evennia.server` +
  `evennia.utils` — 1504 tests (20 expected skips).

## 6.0.0+underspire.253 — Authorization invalidation hot-path fixes

### Performance

- **Movement no longer invalidates authorization facts that did not change.**
  [`ResourceAdapter`](evennia/authorization/resources.py) gains
  `location_sensitive` (default `False`), and
  [`bump_resource_generation_after_move`](evennia/authorization/storage.py)
  invalidates only when a matching adapter derives labels from location.
  Run 6 recorded 4,157 moves whose unconditional resource bumps kept the
  prewarm barrier refetching occupant snapshots it already had; with this
  game's empty adapter registry those labels are stable, so the churn
  disappears. Adapter lookup failures fail safe by invalidating.
- **Destination prewarm expands only resolved action targets.**
  [`_authorization_resources`](evennia/actions/dispatch.py) previously scanned
  every provider for a `destination`, so ordinary `say`, `inventory`, and
  `who` commands warmed every exit in the source room, every adjacent room,
  and their occupants. Only a resolved target (the `Move` exit, a
  `GameObject`-typed action field) expands now; arrival hooks and the
  automatic destination look stay snapshot-only.

### Correctness

- **Prewarm completion is authoritative.**
  [`prewarm_authorization`](evennia/authorization/storage.py) checks readiness
  after the final bounded refresh instead of returning `False`
  unconditionally, and [`dispatch`](evennia/actions/dispatch.py) enters the
  snapshot scope only when prewarm succeeds. On failure the command evaluates
  through the ordinary read path rather than consuming possibly stale
  snapshots.

### Observability

- New counters `evennia_authorization_move_invalidation_total{outcome}` and
  `evennia_authorization_snapshot_scope_fallback_total`. Run 6's prewarm
  traffic (213 `ready`, 11,804 `waited`, 2,890 `failed`) should collapse to
  ready-dominated with fallbacks at zero; these metrics prove it.

### Migration

- No database migration or setting change. Games with resource adapters whose
  labels derive from location or containment must set
  `location_sensitive=True` (see
  [Authorization](docs/source/Components/Authorization.md)).

### Tests

- Engine sweep green: `evennia.authorization`, `evennia.actions`,
  `evennia.objects`, and `evennia.server` — 1291 tests (18 expected skips).

## 6.0.0+underspire.252 — Reactor hot-path follow-up

### Performance

- **The one-second Attribute maintenance flush no longer performs PostgreSQL
  work on the reactor.** [`attributes.py`](evennia/typeclasses/attributes.py)
  and [`jsonb_handler.py`](evennia/typeclasses/jsonb_handler.py) capture immutable
  dirty-row snapshots on the owner thread, persist them through the bounded
  worker pool, and reconcile outcomes back on the owner. Run 5 recorded 556
  flushes and 21.8 seconds of flush wall time over 600 seconds; the game sets
  `ATTRIBUTE_FLUSH_INTERVAL = 1`, so this moves almost all of that measured
  database work off the live loop. Query barriers and shutdown remain
  synchronous because their correctness contract requires durable completion.
- **Grouped byte-free output now shares base protocol cleaning.**
  [`sessionhandler.py`](evennia/server/sessionhandler.py) reuses one cleaned
  result for identical raw frames in the same reactor turn when outgoing
  FuncParser evaluation is disabled. Byte payloads, enabled inline functions,
  and custom cleaner overrides remain per session because their result may
  depend on session state. A homogeneous 100-recipient text broadcast now
  performs one base clean instead of 100 before the `.251` multicast step.
- **The process and ASGI web event loops prefer uvloop when it is installed.**
  [`loop_factory.py`](evennia/utils/loop_factory.py) centralizes the loop choice
  and is used by the Server/Portal bootstrap, the ASGI web thread, and the
  headless standalone bootstrap; a missing or unsupported install falls back to
  the stdlib loop (Windows dev) and each process logs the loop it chose. The
  game adds `uvloop>=0.21` for non-Windows deploys.
- **Hot-path runtime identifiers use a random process prefix plus a monotonic
  counter.** [`fast_ids.py`](evennia/utils/fast_ids.py) replaces repeated
  `uuid4()` calls for authorization decisions, narrative plans/nodes, and the
  downstream multipuppet event seam while preserving opaque, process-unique
  string IDs.
- **Authorization and control reads stay local.** Control-generation checks use
  the idmapper singleton rather than polling durable state, and action prewarm
  includes a resolved movement destination and its occupants so arrival hooks
  and the destination look remain inside the local snapshot scope. Dynamic
  game-defined `destination` and `contents` properties are guarded so a raising
  property cannot break dispatch.

### Output delivery

- One frame that cannot produce a hashable equality key no longer aborts a
  whole flush turn. [`sessionhandler.py`](evennia/server/sessionhandler.py)
  hash-tests structural keys and falls back to per-frame identity grouping, so
  the frame ships alone instead of dropping every session's buffered output
  after the buffers were already popped.

### JSONB correctness and cache API

- Async adoption now tracks every owner-side durable-head rewrite as well as
  ordinary dirty mutations. A worker result captured before a blocking update,
  synchronous barrier, rollback rebase, spool replay, or reload cannot replace
  the newer head or clear its dirty intent; the live document is rebased and a
  reconciling flush remains pending. Model-save rollback keeps the serial
  monotonic, and clean state is released only when no durable spool entry still
  owns the row.
- [`attribute_revision`](evennia/typeclasses/jsonb_handler.py) is the public
  engine contract for game-side view caches. It changes on visible document
  mutations and durable-head rewrites and returns `None` when no safe revision
  is available, which tells callers to skip caching rather than risk stale
  output.

### Observability

- Attribute flush runs and their duration histogram now report bounded `tick`,
  `barrier`, `shutdown`, and `manual` caller labels. Authorization metrics
  distinguish an already-ready snapshot from an action that awaited refresh.
  One in 64 render calls samples plan resolution, universal transforms, and
  output cleaning. Runtime callback counters distinguish output, Redis
  transport, and idmapper work from the former generic pool.

### Migration

- No database migration. Games importing `attribute_revision` require `.252`
  or newer; update the engine pin before deploying the corresponding room-cache
  change. Restart Server and Portal together as required by the `.251` grouped
  bus protocol.

### Tests

- Engine sweep green: `evennia.server` + `evennia.utils` 1504 tests (20
  expected skips), `evennia.typeclasses` + `evennia.actions` 767 tests.
  Coverage spans JSONB durability, authorization, narrative delivery, grouped
  output, Redis transport, scheduling, actions, runtime IDs, loop selection,
  and metrics.
- 278 downstream game tests pass across room rendering, perception, psychosis,
  movement, wilderness, and multipuppet relays. All touched Python files pass
  Ruff format and import checks.

## 6.0.0+underspire.251: Grouped Portal delivery

### Performance

- **Byte-identical final output now crosses the Redis bus once per recipient
  group.** [`sessionhandler.py`](evennia/server/sessionhandler.py) drains all
  session output for one reactor turn in one callback, performs the existing
  protocol cleanup separately for every session, and groups only the resulting
  frames that are exactly equal. The Portal expands each group to its local
  sockets. A synthetic 100-recipient broadcast therefore schedules one flush
  callback and publishes one multicast frame instead of 100 callbacks and 100
  frames. Viewer-specific output remains separate. Groups are capped at 1,024
  sessions, and payloads over the 8 MiB bus-frame ceiling fall back to ordinary
  per-session delivery.
- **This targets the dominant amplification measured in the 100-bot run.** The
  pre-release baseline completed 18,958 commands but produced 611,761 text
  deliveries, 633,333 Server-to-Portal frames, 14,084 Redis writer batches, and
  350,963 generic runtime-task completions. Homogeneous room broadcasts can now
  approach a 100:1 reduction in bus frames and flush callbacks; the actual
  reduction depends on how many final viewer renderings are identical.

### Protocol and correctness

- [`amp_serde.py`](evennia/server/amp_serde.py) and
  [`ipc_schema.py`](evennia/server/ipc_schema.py) add the typed `M1`
  `MsgServer2PortalMany` envelope. Grouping happens after visibility, naming,
  narrative transforms, hooks, options, encoding, and outgoing funcparser work,
  so no viewer-specific value is cached or shared. Round-based grouping
  preserves each session's message order, watches still observe every logical
  delivery, disconnect flushes remain immediate, and one broken session cleanup
  cannot strand output for the others.

### Migration

- No database migration or setting change. This release changes the Redis bus
  wire protocol, so stop and restart the Server and Portal together; mixed bus
  versions remain unsupported.

### Tests

- 101 focused serialization, ordering, disconnect, Redis round-trip, recovery,
  reload, and process-boundary tests pass (six expected skips).
- The complete `evennia.server` suite passes: 622 tests, 18 expected skips.

## 6.0.0+underspire.250: Off-loop authorization, async movement, load-test telemetry

### Performance

- **Capability checks no longer touch Redis on the action path.**
  [`authorization/storage.py`](evennia/authorization/storage.py) can prewarm
  grant, suspension, resource-label, and policy snapshots in the bounded worker
  pool before rule evaluation, and then require the dispatch to read those local
  snapshots only (`authorization_snapshot_scope`). Concurrent commands coalesce
  into one worker batch, so a cold burst (100 bots logging in) shares one
  refresh per generation window instead of one per command. Shared generation
  counters still publish cross-process invalidation; a local batch never lowers
  a generation another batch already observed, and the scope binds to the exact
  task that entered it so detached runtime roots and child callbacks copied from
  the dispatch context cannot inherit a requirement their snapshots do not
  cover. New settings: `AUTHORIZATION_OFFLOOP_SNAPSHOTS` (default `False`, game
  opt-in) and `AUTHORIZATION_SNAPSHOT_MISS_IS_ERROR` (default `False`).
- **A snapshot miss degrades to one inline read instead of an error.** A rule
  may legitimately check a principal or resource outside the prewarmed set.
  That read now falls back to the ordinary indexed query, increments
  `evennia_authorization_snapshot_miss_total{kind}`, and logs a bounded warning;
  setting `AUTHORIZATION_SNAPSHOT_MISS_IS_ERROR = True` restores the strict
  tripwire for CI. The prewarm retry is bounded at three attempts.
- **Quell stays authoritative with unflushed attribute state.** The owner-side
  suppression probe reads the live attribute handler (matching the inline path,
  including a `@quell` set moments earlier); only when the handler is
  unavailable does the worker read the stored `db_attrs` row itself, batching by
  model.
- **CM1 movement commits the location row off-loop.**
  [`objects/mixins/movement.py`](evennia/objects/mixins/movement.py) adds
  `move_to_async`, the live-loop variant of `move_to`: pre-hooks and
  announcements stay on the owner thread, one optimistic
  `UPDATE ... WHERE db_location = <expected>` runs in a worker, and the owner
  adopts the row into the contents caches afterwards. A lost update reports
  `failed_stage="location_conflict"` instead of overwriting a concurrent move.
  Veto gates, hook order, announcements, loop detection (same depth-10 give-up
  as the synchronous setter), and `MoveResult` semantics are unchanged; the
  CM1 `carry_out_move` rule now awaits it.
- **Command-completion markers and action-input metrics.**
  [`commands/cmdhandler.py`](evennia/commands/cmdhandler.py) can emit one
  out-of-band protocol record (`\x1eEV-COMMAND-DONE outcome=... elapsed_ms=...\x1f`)
  after the complete action lifecycle, gated by
  `COMMAND_COMPLETION_MARKERS_ENABLED` (default `False`; normal clients never
  see it) and only to a real session. `prometheus_metrics` records
  `evennia_action_input_total`/`_duration_seconds{outcome}` for every typed
  action and `evennia_authorization_prewarm_total`/`_duration_seconds` for the
  worker refreshes, so the next load run can separate server time from client
  silence and watch prewarm cost directly.

### Migration

- None required. `AUTHORIZATION_OFFLOOP_SNAPSHOTS` and
  `COMMAND_COMPLETION_MARKERS_ENABLED` default off; enabling them only changes
  where authorization I/O runs and adds the diagnostic marker stream.
- Rule providers that call `actor.move_to(...)` keep working; only the shipped
  CM1 traversal rule switched to `move_to_async`. Custom code that overrides
  `carry_out_move` must keep the async signature if it wraps the base rule.

### Tests

- Engine: 70 authorization tests (task-local scope, miss fallback and strict
  mode, bounded retry, out-of-order generation installs, live and stored quell
  resolution, worker coalescing) and 851 actions/commands/objects tests, all
  passing. The game-side look, staff, movement, teleport, wilderness, and
  multipuppet suites pass with the off-loop path enabled.
- The load harness now verifies marker support before driving and drains a late
  marker after a timeout, so a missing marker setting fails loudly and stale
  markers cannot be credited to the next command.

## 6.0.0+underspire.249: Webclient feed routing and refused-login notice

### Webclient

- **Feed tabs track routes live.** `Routing.labels()` read the non-reactive
  compiled-route cache, so the Feeds panel's `$derived` tab bar computed once
  at mount: routes added or renamed later got no tab until reload, which read
  as "the Feeds panel only supports one feed". It now derives from the
  reactive `routes` ([`routing.svelte.ts`](evennia/web/webclient/client/src/lib/routing.svelte.ts)).
- **Move-routing acts retroactively.** Routing gated only lines as they
  arrived, so a move-route added (or toggled) after the fact left every
  earlier copy in the terminal. `session.append` now records the feed labels
  each line was filed to; on every route change the scrollback is re-filed,
  moving claimed lines into their feeds and backfilling any feed they were
  not already filed to, so a claimed line is never dropped from the game
  ([`session.svelte.ts`](evennia/web/webclient/client/src/lib/session.svelte.ts)).
  Media lines stay exempt from pruning, as from routing, and a deleted or
  renamed route's stale buffer is dropped instead of leaking for the session.
- Rebuilt `webclient/shell/shell.js` carries the changes.

### Server

- **A refused login now tells the client.** The `at_pre_login` veto closed
  the socket with no notice, so a resumable shell read the close as a dropped
  connection and reconnect-looped through the veto forever, re-printing the
  refusal each cycle. `ServerSessionHandler.login` now sends the `logout` OOB
  to the vetoing session alone before disconnecting (which flushes it); an
  account-level send would wrongly reach the account's other live sessions
  ([`sessionhandler.py`](evennia/server/sessionhandler.py)).
- The `at_pre_login` docstrings in `accounts.py` and `utils.py` still claimed
  the hook was not vetoable after the veto contract returned; both now
  describe the actual behavior.

### Tests

- Four `session.test.ts` cases cover backfill on prune, no double-filing of
  already-filed lines, filing into a newly claimed feed, and the media
  exemption; `routing.test.ts` covers label reactivity, per-label filing
  dedup, claims, buffer drop on route deletion, and sync ordering (540 total
  vitest). Engine: new `TestLoginVeto` asserts the `logout` frame is sent to
  the vetoing session before `disconnect`.

### Migration

- None. Game-side vetoes need no change: the engine now emits the client
  notice. Games that added their own account-level `logout` send on veto
  should drop it, as it would reach the account's other live tabs.

## 6.0.0+underspire.248 — Release metadata alignment

### Packaging

- **The four release version sources now agree on `.248`.**
  [`pyproject.toml`](pyproject.toml), [`evennia/VERSION.txt`](evennia/VERSION.txt),
  and the generated [`uv.lock`](uv.lock) now identify the same package build as
  this changelog. This closes the metadata drift left by the `.245`–`.247`
  release entries; runtime behavior is unchanged from `.247`.

### Migration

- None.

### Tests

- Regenerated `uv.lock` with `uv lock` and verified all four release files carry
  `6.0.0+underspire.248`.

## 6.0.0+underspire.247: Tag miss caching

### Performance

- **`TagHandler` remembers authoritative misses.** `tags.has()` only cached
  positive hits, so every check for an absent tag — an untagged room on the
  contents path, a permission an object does not carry — issued a SQL query.
  During the 100-bot load run this was the single largest share of sampled
  reactor stalls (159 of 438 stacks, leaf `TagHandler._getcache`). Misses are
  now remembered in a `_misscache` cleared by `add`, `remove`, `clear`, and
  `reset_cache`, so a read stays free until a write invalidates it. Reads stay
  correct with `TYPECLASS_AGGRESSIVE_CACHE` off: the miss cache is ignored
  entirely in that mode.

### Tests

- Seven new tests cover miss caching, add/remove/clear/reset invalidation,
  prefetch-then-miss, and the cache-disabled path.

### Migration

- None.

## 6.0.0+underspire.246: Prompt discovery retry

### Reliability

- **An unanswered discovery probe is re-issued on the heartbeat cadence.**
  `underspire.245` raised the peer lease, which exposed a coupling: a probe
  published before the peer's reader subscribed (the `$` cursor) was only
  retried when the whole lease expired, so recovery latency grew with the
  lease. A challenge-less pending probe is now re-issued once per heartbeat
  while the original attempt keeps its lease deadline; a probe that already
  carries a challenge is an exchange in flight and keeps the full lease.

### Tests

- New `test_unanswered_probe_is_reissued`; the pending-exchange protection test
  now exercises an exchange that carries a challenge (the true in-flight
  state) instead of a bare unanswered probe.

### Migration

- None.

## 6.0.0+underspire.245: Peer lease tolerance

### Reliability

- **The bus peer lease is configurable (`BUS_HANDSHAKE_TIMEOUT`, default
  twelve seconds; previously hard-coded at four).** Liveness is answered by
  once-per-second heartbeats, so a peer whose reactor runs long synchronous
  game/DB work was declared dead even though the process was alive. During a
  100-bot run on `underspire.244`, turns of a few seconds produced continuous
  `peer synchronization lost` / `restored` churn and session reconciliation
  while both processes were healthy. Real process failure is detected through
  the Redis connection rather than the lease, so the lease only needs to bound
  silence, not deliver a prompt verdict.
- Deployments that prefer the original prompt veto can set
  `BUS_HANDSHAKE_TIMEOUT = 4.0`.

### Tests

- `test_peer_lease_expires` derives its deadline from the configured lease, and
  a new `test_timeout_honors_setting` covers the settings override.

### Migration

- None.

## 6.0.0+underspire.244: Bus telemetry and read-ahead

### Performance

- **The bus reader drains up to 32 frames per XREAD.** Reads previously
  requested a single frame per round trip; a fan-out burst now costs one read
  per batch. Tunable with `REDIS_BUS_READ_BATCH` (None keeps the module
  default). Per-frame sequence/origin validation is unchanged, so the
  fail-closed gap detection still runs for every entry.

### Observability

- **New `evennia_bus_*` Prometheus metrics.** Queue depth and retained bytes
  (`evennia_bus_outgoing_depth`, `evennia_bus_outgoing_bytes`), published and
  locally-rejected frame counters (`evennia_bus_published_total`,
  `evennia_bus_rejected_total{reason}`), and a write-batch histogram
  (`evennia_bus_write_batch_size`). A saturated or backpressured bus is now
  visible on the dashboard instead of only in logs.

### Tests

- A publish-flood regression test: a queue-full burst rejects locally, stays
  online, never fails the transport, and settles to zero frames/bytes once the
  writer drains. This is the exact shape of the `underspire.243` outage.

### Migration

- None.

## 6.0.0+underspire.243: Bus throughput and crowd rendering

### Performance

- **The Redis bus drains in batches and treats capacity as backpressure.**
  A 100-session load test (100 bots broadcasting `look`/`say`/movement in one
  hub) filled the outgoing queue to its data cap; the next handshake frame then
  hit the shared cap and [`RedisTransport.publish`](evennia/server/redis_transport.py)
  failed the whole transport. Recovery reset discovery and the session reconcile
  disconnected every live session — 192 sync-loss cycles in one run, each a
  player-visible outage. The writer now drains up to 64 admitted frames through
  one Redis pipeline per round trip instead of one XADD per frame; the caps are
  settings-tunable and raised (4096 entries / 64 MiB total, 3072 / 48 MiB data;
  previously 1024 / 32 / 992 / 24); and capacity overrun rejects the frame
  locally without failing the generation. Byte accounting is O(1) per admission
  instead of a queue-wide sum (which grew with the raised caps), and handshake
  frames are published in their own batch, so a failed data publication cannot
  trip the handshake errback that disconnects the peer. Control saturation still
  fails the generation for non-capacity reasons. Docs:
  [Redis-Bus-Delivery](docs/source/Components/Redis-Bus-Delivery.md); settings:
  `REDIS_BUS_MAX_ENTRIES`, `REDIS_BUS_MAX_BYTES`, `REDIS_BUS_DATA_ENTRIES`,
  `REDIS_BUS_DATA_BYTES`, `REDIS_BUS_WRITE_BATCH`.

- **Crowd rendering stops revalidating spans per viewer.**
  A room view carries a span per visible character per detail line; the
  renderer validated the full span tree on every viewer transform and walked
  every serialized span looking for raw identity. Field lists are now cached
  and validated without a dict per span, `map_text`/`prepend_text` trust the
  already-validated tree, and the payload check short-circuits per span type
  (a `CharRef` segment skips span serialization entirely). On a synthetic
  1,500-span look: trusted transform 0.5-0.9 ms (was ~3.0 ms), payload 1.5 ms
  (was 8.6 ms). `RENDER_MAX_REFS` (default 256) raises the bound for crowded
  hubs; Underspire sets 4096.

### Tests

- New [`test_redis_transport_writer.py`](evennia/server/tests/test_redis_transport_writer.py)
  covers batch order and capacity backpressure; transport and bus lifecycle
  tests updated to the batched writer.
- [`test_handles.py`](evennia/narrative/tests/test_handles.py) opacity check
  made deterministic (a 2-digit id flaked ~9% of runs inside a random hex
  handle).

### Migration

- None. The new settings default to the previous behavior except the raised
  bus caps, which are internal limits.

## 6.0.0+underspire.242: Focus lookups and the web thread

### Performance

- **Focus resolution is idmapper-cache-first.**
  [`ControlBinding._resolve`](evennia/accounts/models.py) ran an indexed
  primary-key query for the stack top on every call, and
  `_select_online_puppets` ([`utils/systems.py`](evennia/utils/systems.py))
  calls `session.get_puppet()` once per online session for every
  `online_puppets`-scoped system fire. A 60s `py-spy` profile of the
  production server (Python 3.14.4) attributed 91 samples (3.4%) to that
  path. Resolution now checks `get_cached_instance()` first and keeps the
  indexed query as the miss fallback (eviction, cold lookup, non-IO-owner
  context), so no new invalidation layer was added: the binding is a
  `SharedMemoryModel`, so the idmapper already owns identity and invalidation.
  Reads of the binding's focus stack are unchanged, as are the three-way merge
  and generation guards. Two new tests in
  [`accounts/tests.py`](evennia/accounts/tests.py) prove a hit issues no query
  and a miss still resolves through SQL. Cost scales with online sessions ×
  puppet-scoped systems, so the win grows with population; on the profiled
  idle server it removes the per-tick query (the fresh PgBouncer connect it
  used to force now lands on the next DB user of the tick).

- **The ASGI web thread prefers uvloop when installed.**
  [`UvicornWebService`](evennia/server/asgi_webserver.py) runs uvicorn in a
  background thread with its own event loop, separate from the game reactor.
  `_make_loop()` now returns `uvloop.new_event_loop()` when importable and
  falls back to `asyncio.new_event_loop()` otherwise (Windows dev, no wheel).
  Covered by two tests in
  [`server/tests/test_asgi_webserver.py`](evennia/server/tests/test_asgi_webserver.py).
  The game reactor's loop is untouched. This is opt-in: install `uvloop` in
  the environment to activate it.

### Migration

- None. `uvloop` is an optional runtime dependency; without it the web thread
  behaves exactly as before, and no settings, models, or APIs changed.

## 6.0.0+underspire.241: Test-suite and boot-path cost

### Performance

- **`inflect` and `pyinflect` import on first use.** Both were imported at
  module scope: `inflect` from [`object.py`](evennia/objects/object.py) and
  [`appearance.py`](evennia/objects/mixins/appearance.py), and both from
  [`emote.py`](evennia/narrative/emote.py). `inflect` drags in `typeguard`
  (~5.3s) and `pyinflect` imports `spacy` (~3.0s), and every one of those
  modules loads during `django.setup()`, so ~8s was spent on English grammar
  helpers by every process that touched the engine: each `migrate`, each
  `makemigrations --check`, each test process and each parallel test worker.
  A new [`evennia/utils/inflection.py`](evennia/utils/inflection.py) holds two
  cached accessors, `inflect_engine()` and `pyinflect_module()`, which import on
  the first call and cache the result including the missing-dependency case.
  Measured on the Underspire game directory, `django.setup()` drops from 18.2s
  to 5.5s and full boot (setup + `evennia._init()` + URL conf) from 23.0s to
  10.8s.
  The `_INFLECT` engine in [`object.py`](evennia/objects/object.py) had no
  remaining callers and is gone rather than made lazy.

  **A live server warms them at boot.** Laziness is right for a process that
  may never render prose, and wrong for the one process that always will: a
  server boots once and then serves players, so a first-use import would land
  inside whichever command rendered a numbered name or conjugated an emote
  first and block the IO thread for ~8s. `warm()` is called from
  `at_server_start` ([`service.py`](evennia/server/service.py)) before the game
  hooks, which puts the cost back on the boot path where it was. Games do not
  need to opt in.
- **Action-registry phrase metadata is rebuilt lazily.**
  `ActionRegistry.register` ([`registry.py`](evennia/actions/registry.py)) called
  `_rebuild_phrase_metadata()`, which walks every registered verb, once per
  registration. Registration was therefore quadratic in the verb count: ~630
  verbs over ~630 registrations meant ~399k scans at import time, every one of
  them discarded by the next registration, for ~1.5s per process.
  `_max_phrase_words` and `_multi_word_starters` are now properties over a
  cache that `register` invalidates, exactly as the verb trie beside them
  already did. The rebuild runs once, on the first read after a registration.
- **`grant_capabilities` grants a whole bundle in one transaction.** New in
  [`storage.py`](evennia/authorization/storage.py), beside the unchanged
  `grant_capability`. The single-grant call is six queries (principal
  `get_or_create`, `SELECT FOR UPDATE`, existing-grant lookup, grant write,
  audit write, plus the savepoint), so granting a bundle in a loop cost six
  queries per capability; `runtime_operator` alone is 57 of them. The bulk form
  takes the principal lock once, looks up all existing grants in one query, and
  uses `bulk_create`/`bulk_update` plus one `bulk_create` for the audit trail.
  Semantics are unchanged: every capability is resolved through the registry
  before anything is written, so an unknown capability rejects the whole call
  rather than leaving a half-granted principal.

  Note for callers writing their own bulk paths: `bulk_update` reads the
  attribute instead of calling `pre_save`, so an `auto_now` column such as
  `AuthorizationGrant.updated_at` stays stale unless it is set explicitly.
  `grant_capabilities` sets it.

### Tests

- **The standard fixture grants its capability bundle in bulk.**
  `EvenniaTestMixin.create_accounts`
  ([`test_resources.py`](evennia/utils/test_resources.py)) looped
  `grant_capability` over `expand_bundle("runtime_operator")`, which was ~340 of
  the ~770 queries every fixture-backed test issued, and re-resolved
  `principal_refs(self.account)[0]` on each pass. It now makes one
  `grant_capabilities` call.

  On the Underspire suite, where ~6.4k of ~10.5k tests build this fixture and
  profiling put ~77% of wall time in `setUp`, this change plus two game-side
  ones (a frozen boot heap, and MD5 password hashing under `TESTING`) took a
  representative 85-test module from 107.9s to 65.6s.
- Added `BulkGrantTest`
  ([`test_storage.py`](evennia/authorization/tests/test_storage.py)) covering
  parity with the single-grant path, refresh-not-duplicate on re-grant,
  duplicate collapsing, and that an unknown capability or an unsupported
  constraint writes nothing.
- Added [`test_inflection.py`](evennia/utils/tests/test_inflection.py), which
  checks the accessors and then walks the engine tree asserting that no
  non-test module imports `inflect` or `pyinflect` at module scope. That import
  is the whole regression, and it is invisible until someone profiles a boot.
- Added a registry test that reads the phrase cache, registers a further
  multi-word verb, and reads again. The previous coverage registered everything
  before its first read, so a cache that never invalidated would have passed it.
- **The standard fixture is declarative.** `EvenniaTestMixin.setUp`
  ([`test_resources.py`](evennia/utils/test_resources.py)) built two accounts, a
  capability bundle, two rooms, an exit, two objects, two characters, a script
  and a synthetic login for every test method. Measured on the Underspire suite,
  per test: characters 435ms, teardown 262ms, the login 195ms, rooms 168ms,
  accounts 159ms — and most tests use `room1` and `char1`.

  `evennia_fixtures` names what a class touches, and `resolve_fixtures` pulls in
  the dependencies, so `{"char1"}` also builds `room1` and `account`:

  ```python
  class MyTest(EvenniaTest):
      evennia_fixtures = {"char1", "room2"}
  ```

  Valid names are the attributes themselves: `account`, `account2`, `room1`,
  `room2`, `exit`, `obj1`, `obj2`, `char1`, `char2`, `script`, `session`. The
  default is every fixture, so a class that declares nothing behaves exactly as
  before and no existing test needs changing. An unknown name raises rather than
  quietly building nothing, which would otherwise surface much later as a
  missing attribute inside an unrelated assertion. `tearDown` no longer assumes
  a session exists.

  It is deliberately not called `fixtures`: that attribute belongs to Django's
  `TestCase` and names fixture files to load.

  Note for games narrowing their own suites: capability checks that resolve from
  a character to its account need `session`, because that link is established by
  the synthetic login rather than by `char.account`.

### Migration notes

Nothing to change downstream. `grant_capability` keeps its signature and
behaviour; `grant_capabilities` is additive; `evennia_fixtures` defaults to the
full fixture set. Game code that imported `inflect` or `pyinflect` at module
scope still works, but re-adds the ~8s to `django.setup()`, so route it through
`evennia.utils.inflection` instead.

---

## 6.0.0+underspire.240: Quiet Portal and bus recovery logs

### Engine

- **Launcher IPC polls hold one connection.** `wait_until_state`
  ([`launcher_ipc.py`](evennia/server/launcher_ipc.py)) reopened its
  `LauncherSession` after every poll that did not yet match the desired
  state, so a Server cold start opened roughly one TCP connection per
  second -- each logged as "Launcher IPC connected from 127.0.0.1" -- and
  threw away the status push the Portal sends on connect. The wait now
  keeps one channel for the whole wait and reopens only after a failed
  connect or exchange.
- **Requests racing Portal shutdown get a 503.** `ReverseProxy.stop()`
  closes the listener, then nulls `_client`; a connection accepted before
  the listener closed could reach `_forward_and_respond` afterwards and
  raise `AttributeError: 'NoneType' object has no attribute 'stream'`
  ([`web_proxy.py`](evennia/server/portal/web_proxy.py)), logged as a
  traceback on every Portal stop. The upstream reference is captured up
  front, and a missing one answers `503 Service Unavailable`.
- **One warning per bus outage, plus recovery.** The handshake lease
  fails every ~4s while the peer is away (correct fencing), so a single
  Server restart re-logged "redis bus unavailable: peer synchronization
  lost; interrupted work may have run" each cycle. The bus
  ([`redis_bus.py`](evennia/server/redis_bus.py)) now warns once per
  outage and logs "redis bus: peer synchronization restored" when
  readiness returns; player-facing notices are unchanged.

### Tests

- `test_launcher_ipc`: one connect across failed polls, reconnect after a
  broken exchange.
- `test_web_proxy_streaming`: no upstream client returns False and
  answers 503.
- `test_bus_recovery`: repeated failures warn once; recovery reports once.

No migration: no settings, API or wire-format changes.

---

## 6.0.0+underspire.239: Repair webclient glyph alignment, buffer clear and tab scroll

### Webclient

- **Rule-glyph fallback.** Google's `css2` subsets ship every monospace the
  shell offers as Latin only; of the seven, just Fira Code carries
  U+2500-259F, the range the game draws its frames, rules and tables from (a
  single U+2500 is ~50k of the ~61k non-Latin characters in game content).
  With any other font selected, every rule fell through to an OS monospace at
  that font's advance, tearing the column by 8.4% (IBM Plex Mono, JetBrains
  Mono), 10.2% (Space Mono) or 37.5% (VT323); Share Tech Mono missed by 1.8%
  and so looked right, which is why players reported exactly three of the
  seven. A 16KB DejaVu Sans Mono subset
  ([`glyph-fallback.css`](evennia/web/webclient/client/src/styles/glyph-fallback.css),
  [`shell-glyphs.woff2`](evennia/web/webclient/client/src/styles/shell-glyphs.woff2))
  now backs every stack, declared once per cell width with `size-adjust`
  scaling it onto that width exactly; primaries that already carry the glyphs
  keep their own. Measured tear afterwards is 0% on all seven.
- **Clear buffer survives refresh.** The portal keeps a replay window of every
  frame it has sent, and a reloaded page presents no cursor, so the whole
  window replayed into a log the player had just cleared. A portal-local
  `resume_reset` frame ([`webclient.py`](evennia/server/portal/webclient.py))
  now drops the window, which is the half that makes the clear survive.
- **Tab scroll.** dockview hides an inactive panel rather than destroying it,
  and a hidden element's `scrollTop` resets to 0; nothing put it back, so
  switching tabs and back showed the top of the buffer. A `ResizeObserver` in
  [`GameLog.svelte`](evennia/web/webclient/client/src/components/GameLog.svelte)
  restores the reading position on the way back -- the bottom if pinned, the
  exact offset otherwise.
- **Routing.** Routes now move as well as copy
  ([`routing.svelte.ts`](evennia/web/webclient/client/src/lib/routing.svelte.ts)).
  A move-route takes the line out of the terminal entirely, and moved lines
  are nowhere else, so feed tabs carry unread counts. The label field names a
  tab inside the Feeds panel, and the hint now says so.
- **Log export.** The buffer saves as standalone HTML (palette resolved
  against the live document, scripts and inline handlers stripped) or as ANSI
  (styling put back as SGR escapes), alongside the existing text
  ([`transcript.ts`](evennia/web/webclient/client/src/lib/transcript.ts)).
  Both walk the DOM rather than the HTML string, so MXP links that nest inside
  and across style spans keep their effective style.
- Keyboard SFX gain is a slider; the old fixed level is its 50%.
- vite `base` is relative, or the font asset emits a root-absolute URL that
  404s under `STATIC_URL`; vitest loads the svelte plugin (so rune-backed
  stores are constructible in a test at all) and processes CSS (so the palette
  parity test can read the generated stylesheet).

### Tests

- 529 node tests pass (13 new), 50 in the browser harness (16 new), and 54
  portal transport tests (5 new).

### Migration

- Portal and webclient only. No settings, database or API change.
- **Deploy must run `evennia collectstatic`**: `shell.woff2` is a new static
  asset. This is a portal change, so production needs a full service restart,
  and downstream games should bump `EVENNIA_REF` to `underspire.239`.

## 6.0.0+underspire.238: Restore redis reload test coverage

### Tests

- [`TestRedisReloadSurvival`](evennia/server/tests/test_redis_reload.py) starts
  its bus pair in production order. It previously started the Server bus before
  the Portal bus and asserted readiness after a single drain; the Server
  announces itself as soon as it starts, so it published into `s2p` before the
  Portal reader existed and that reader's `$` cursor skipped past the
  announcement. Discovery never completed, and all 8 tests in the module failed
  in `setUp` with `server_bus.ready` false and `last_sync_error` None -- no
  error, just a handshake that never happened. The bus fixture in
  [`test_redis_bus`](evennia/server/tests/test_redis_bus.py) already ordered
  this correctly and documented why; the reload fixture now mirrors it, starting
  the Portal bus first, letting its reader reach the first blocking `xread`,
  then starting the Server bus and forcing one probe.
- The module runs in 30.8s where it previously took 23.9s to fail, so reload
  survival, PSYNC reattachment and transport-outage recovery are actually
  exercised again rather than skipped by a fixture that never reached the
  test bodies.

### Migration

- Test-only. No engine, settings, database or API change. Games pinned to
  `underspire.237` behave identically; upgrade only to get the restored
  coverage when running the engine suite.

## 6.0.0+underspire.237: Repair launcher lifecycle and bot reconnection

### Engine

- [`launcher_handlers`](evennia/server/portal/launcher_handlers.py) publishes
  lifecycle operations over the Portal's Redis bus instead of the
  `_launcher_amp_protocol` stub. That protocol is never connected to a Server,
  and its `broadcast` fans out over `factory.broadcasts`, which stays empty
  because redis is the only Portal<->Server bus. Every `evennia reload`,
  `reset` and `shutdown` therefore resolved to an immediately-successful empty
  `DeferredList`: the Portal recorded `server_restart_mode` as applied, the
  Server was never told anything, and the launcher timed out waiting on a
  process that had no reason to exit. In-game `@reload` was unaffected, because
  it already arrives on the bus. Spawning the Server process, launcher status
  pushes and the factory connect callbacks stay on the protocol, which shares
  its factory with the bus.
- [`ServerSessionHandler.start_bot_session`](evennia/server/sessionhandler.py)
  keeps each bot connect request until its frame is published, and
  `retry_pending_bot_sessions` re-sends whatever did not land. Bots start from
  `run_init_hooks`, which the Server bus runs while the handshake is still
  synchronizing; `callRemote` deliberately admits `SCONN` in that window, but a
  peer generation turning over before the transport settles rejects the queued
  frame with `connection generation replaced`. The only observer was an errback
  that logs, and `BotStarter` is a one-shot script, so the bot stayed dead until
  the next Server start. [`RedisServerBus._ready`](evennia/server/redis_bus.py)
  drives the retry, so a request rides the next confirmed generation. A request
  is dropped rather than re-sent when the bot already holds a session, so a
  reconnect that beat the retry does not connect the bot twice.
- [`Bot.authorization_policies`](evennia/accounts/bots.py) is a class attribute
  again. `b9e13e675` replaced the runtime lockstring in `basetype_setup`, which
  carried `noidletimeout:true()`, with a policy dict that landed inside the
  class docstring. Import line and all, it has never been evaluated: every bot
  has since inherited `DefaultAccount`'s
  `noidletimeout: RequiresCapability("engine.world.build")`, which bots do not
  hold, so `process_idle_timeouts` disconnected them on each `IDLE_TIMEOUT`
  sweep. `msg` was likewise `Always()` instead of `Never()`.

### Tests

- [`test_launcher_lifecycle`](evennia/server/tests/test_launcher_lifecycle.py)
  asserts each launcher lifecycle operation reaches the bus transport carrying
  the right admin operation, that a rejected frame records no restart mode, and
  that a cold start still spawns the Server process locally.
- [`test_bus_lifecycle_validation`](evennia/server/tests/test_bus_lifecycle_validation.py)
  covers bot request republication after a discarded generation, suppression
  after a successful publication or a live session, and the bus readiness
  transition that drives the retry.
- [`accounts.tests`](evennia/accounts/tests.py) pins the Bot policies as class
  attributes and fails if policy source returns to the docstring.

### Migration

- No settings, database or API change. `start_bot_session` now returns the
  `PublicationResult` it previously discarded; every in-tree caller ignores it.
- Games on `underspire.236` or older should expect that bots were
  idle-disconnected about every `IDLE_TIMEOUT` seconds, and that launcher
  `reload`/`reset`/`shutdown` silently applied nothing while reporting a
  restart mode. Both are fixed by upgrading, with no game-side change.

## 6.0.0+underspire.236: Complete dynamic target choices

### Actions

- [`AmbiguousTarget`](evennia/actions/exceptions.py) accepts an optional
  candidate-to-action resolver for ambiguity discovered before an action exists.
  The [dispatch bridge](evennia/actions/dispatch.py) executes that resolved action
  directly instead of replaying parsing through an unrelated search override.
- The [default movement resolver](evennia/actions/default/movement.py) uses the
  new path for same-named exits and revalidates that the chosen exit is still in
  the actor's room before dispatch. A numbered choice now moves through the
  selected exit instead of reopening the same prompt.

### Tests

- Resolver and dispatch regressions cover successful numbered choices and exits
  that disappear before a choice is made. All 442 engine action tests pass.

### Migration

- Existing `AmbiguousTarget(candidates, original_raw)` callers remain compatible.
  Dynamic verb resolvers that can produce ambiguous candidates should pass
  `choice_resolver`; search-driven action parsing continues to use replay.
- Games adopting this behavior must pin the engine to `underspire.236` or newer.
  No database migration or settings change is required.

## 6.0.0+underspire.235: Separate field limits from transport capacity

### Narrative

- Validate display text and machine identifiers when constructing
  [narrative records](evennia/narrative/rendernode.py). Section titles, list
  items, and reference labels retain their accepted text through serialization.
  All display fields use the existing 131,072-character ceiling; the complete
  resolved body must also fit it.
- Share block and span validation between
  [RenderPlan](evennia/narrative/plan.py) and RenderNode. Validate authored
  fields before resolution and expanded fields afterward. Accepted block
  nesting no longer consumes the metadata depth budget.
- Reject oversized metadata strings, keys, and non-finite numbers during
  construction. Metadata keeps its existing per-container count and depth
  limits; serialization neither clips keys nor merges their values.

### Transport

- Separate incoming structural checks from outgoing scenes and narrative in
  [Azaban](evennia/server/portal/wire_formats/azaban.py). Room descriptions
  no longer inherit the incoming 8,192-character string limit. Incoming byte
  limits apply during [fragment accumulation](evennia/server/portal/ws_protocol.py)
  and again before JSON decoding.
- Bound [WebSocket output](evennia/server/portal/webclient.py), pending batches,
  and replay storage using encoded bytes. Oversized complete messages raise
  before sending or changing sequence and replay state. Batches divide between
  whole envelopes, and direct protocol replies drain prior queued output.
- Keep replay's existing count, expiry, and account-ownership rules. Evict
  oldest complete frames under per-session pressure and oldest-deadline stashes
  under global pressure. Replay remains best effort when data has been evicted.
- Synthetic sizing of a maximum-length emoji body plus its text block produces
  about 3 MiB of JSON, or 4.5 MiB with HTML. A 64-message burst previously could
  form a 192 MiB batch. The byte target sends those complete messages separately
  and bounds retained replay data without reducing their text.

### Migration and validation

- Producers that relied on serializer clipping must validate their fields or
  divide large content into complete messages. Machine tags and styles allow
  64 characters; handles and record identifiers allow 128; separators allow
  4,096; system levels allow 32. Reference affordances allow 32 entries of 64
  characters. List items require strings. Metadata requires string
  keys and finite numbers. Invalid values raise at construction.
- Optional settings are measured in bytes: `AZABAN_MAX_INCOMING_BYTES` defaults
  to 64 KiB; `WEBSOCKET_MAX_OUTGOING_BYTES` to 32 MiB;
  `WEBSOCKET_BATCH_BYTES` to 1 MiB; `WEBSOCKET_RESUME_BYTES` to 32 MiB per
  session; `WEBSOCKET_RESUME_STASH_BYTES` to 128 MiB globally. A legal complete
  message above the batch target travels alone. Valid individual fields can
  still exceed the aggregate transport capacity.
- All 338 selected engine tests and 64 downstream game tests passed. Coverage
  includes field boundaries, viewer expansion, Unicode byte accounting,
  fragmented input, batch admission and ordering, sequence rollover, and replay
  ownership and eviction. Formatting and agent-context checks passed.
- No database migration, dependency update, or client rebuild is required.

## 6.0.0+underspire.234: Preserve display text and client options

### Markup and webclient

- Serialize command strings before HTML attribute escaping in the
  [server parser](evennia/utils/text2html.py) and
  [client parser](evennia/web/webclient/client/src/lib/markup.ts). Backslashes,
  quotes, and entities remain command data; quoted labels display without
  extra backslashes.
- Render short and long hexadecimal foreground and background pipe colors
  in the client parser, including resets and literal pipe escaping.
- Honor per-message and session raw, colorless, and screenreader options on
  both text and narrative paths in
  [Azaban output](evennia/server/portal/wire_formats/azaban.py). Transformed
  HTML remains authoritative for clients that parse markup, including empty
  output, without mutating a payload shared by multiple recipients.
- Remove guessed name decoration from
  [log rendering](evennia/web/webclient/client/src/lib/render.ts), preventing
  substring and replacement-token corruption. Structured references and room
  panel occupants remain available.
- Use inert DOM parsing for
  [plain text](evennia/web/webclient/client/src/lib/text.ts), preserving
  decoded entities, explicit breaks, block boundaries, and preformatted text.
  Search, transcripts, triggers, and routing receive the same projection.
- Reveal complete graphemes across styled text nodes in the
  [typewriter](evennia/web/webclient/client/src/lib/typewriter.ts). Disabling
  animation restores active output immediately. Browsers without
  `Intl.Segmenter` display complete lines immediately.

### Narrative bounds

- Reject direct [RenderNode](evennia/narrative/rendernode.py) trees above
  the existing 256-block total or 256-item list limit. Accepted collections
  survive serialization in full. Iterative validation also rejects deep
  oversized trees before Python's recursion limit.

### Validation and migration

- All 270 selected engine tests passed. A subsequent node and plan run
  passed 54 tests, including the added deep-nesting regression; coverage
  overlaps. All 517 client tests, 32 actual-browser checks, Svelte checks,
  browser-test type checks, and the shell build passed.
- Run the browser checks by opening `tests/display.html` on the client Vite
  development server. They exercise production helpers without a game
  connection or an additional test dependency.
- Ship the included rebuilt shell with the Python parser changes. Direct
  RenderNode producers exceeding collection limits must split their output;
  those inputs raise `ValueError` instead of losing entries on the wire.
- No database migration, dependency change, or runtime setting is required.

## 6.0.0+underspire.233: Preserve identity and output through recovery

### Narrative

- Derive sender attribution from the subject's resolved visible reference,
  regardless of its semantic role. Omit attribution when the subject is not
  referenced. Movement and combat cannot expose a stable hidden handle across
  disguise changes. See [plan resolution](evennia/narrative/plan.py).
- Accept narrative bodies up to the RenderNode limit of 128 KiB in
  [Azaban output](evennia/server/portal/wire_formats/azaban.py). Valid output
  above 8 KiB no longer disappears; incoming limits remain unchanged.
- Preserve per-viewer formatting for mixed entity and literal mappings in
  [room messages](evennia/objects/mixins/messaging.py).
- Coalesce only contiguous text with matching options in the
  [output buffer](evennia/server/sessionhandler.py), preserving narrative,
  text, and OOB order.

### Recovery and database ownership

- Repair the browser authentication stamp during authoritative session
  recovery without repeating login hooks. The idempotent
  [auth synchronization hook](evennia/server/session.py) preserves a newer
  browser login when its nonce differs.
- Keep retained WebSocket replay buffers current after socket loss, preserving
  the original 90-second deadline, 400-frame capacity, and account binding.
  A replaced socket cannot update another socket's stash.
- Run native WebSocket receive and disconnect callbacks in owned database
  scopes so Django connections close when each callback returns. See the
  [WebSocket transport](evennia/server/portal/webclient.py).

### Validation and migration

- The broad engine regression run passed 229 tests; a final boundary run
  passed 67 tests, with overlapping coverage. A real WebSocket handshake
  against an isolated SQLite session database confirmed callback connection
  cleanup. No live PostgreSQL instance was exercised.
- Custom Portal protocols can implement `Session.at_auth_sync` to mirror
  authoritative authentication into protocol-owned client state. It must be
  idempotent and must not repeat login or character initialization. See
  [bus recovery](docs/source/Components/Redis-Bus-Recovery.md).
- No database migration, dependency change, or settings change is required.

## 6.0.0+underspire.232: Keep password hashing off the game loop

### Login

- Run direct game password verification and hash upgrades in the bounded engine
  worker pool. Account loading, login policy, and hooks stay on the game loop.
  The connect action, legacy connect command, and login inputfunc share the
  [async login operation](evennia/actions/default/unloggedin.py).
- Admit one pending login per session. Recheck session identity, the stored
  password, active status, bans, and throttles before completing authentication.
  Disconnected or replaced sessions cannot inherit a worker result.
- Preserve synchronous authentication for existing synchronous callers. Refuse
  sync-only custom authentication overrides on the async path so custom policy
  cannot be bypassed.

### Transport

- Log outgoing queue pressure above 200 pending frames, including stream,
  pending count, encoded bytes, and ordinary data capacity. Warnings are limited
  to once per minute per transport.
- Increase incoming and outgoing capacity to 1,024 entries, with ordinary data
  limited to 992 entries and 32 slots reserved for control/state. This admits
  a burst of 25 broadcasts to 25 recipients before callbacks settle. Encoded byte
  limits, the 32-frame drain batch, and saturation behavior remain unchanged.
  See [bus delivery](docs/source/Components/Redis-Bus-Delivery.md).

### Performance and tests

- A local Python 3.13 sample of 25 synchronous Argon2 verifications took 1.39
  seconds. Offloading removes that hashing time from the game loop; it does not
  reduce the hashing work. A regression holds verification pending and proves
  unrelated loop work continues before authentication completes.
- All 125 selected authentication, login action/input, legacy connect, and Redis
  transport tests pass. Coverage includes stale credentials, revoked access,
  disconnected sessions, duplicate attempts, legacy hash upgrades, and queue
  warning thresholds and rate limits.
- All 60 transport and bus tests pass after the capacity increase, including
  625-frame bursts in both directions and the 32-slot control reserve.

### Migration

- Await `login_session(session, name, password)`. Synchronous input handlers
  must schedule it through `evennia.utils.clock.run_coroutine`.
- Custom Account classes and subclasses of the shipped authentication backend
  that override `authenticate` must also implement `aauthenticate` for live
  login policy. Custom async backends must keep typeclass work on the IO owner.
  See the [login contract](docs/source/Components/Default-Actions.md#login).
- No database migration, dependency change, or settings change is required.

## 6.0.0+underspire.231: Preserve launcher status responses

### Launcher

- Preserve every decoded IPC frame when a socket read contains multiple
  responses. A status push arriving with a query response no longer discards
  the response and leaves the launcher waiting until a socket timeout.
- Return normally when a timed-out status query exhausts its wait deadline.
  The retry path no longer calls `sleep()` with a negative duration and aborts
  the outer startup wait early.

### Tests

- Regression tests reproduce coalesced status response loss and deadline
  overrun. Both failed before the fix; all 49 launcher tests pass on Python
  3.12 and 3.13.

### Migration

- No database migration or settings changes required.

## 6.0.0+underspire.230: Require security-patched DRF

### Security

- Require `djangorestframework>=3.17.2,<3.18` for core and game dependencies.
  Regenerate the lockfile with DRF 3.17.2. This excludes versions affected by
  CVE-2026-73228 (request body size enforcement) and CVE-2026-73229
  (AdminRenderer data disclosure). See the
  [upstream release notes](https://www.django-rest-framework.org/community/release-notes/#3172).
- Matching game requirements and the CI installation override use the same
  patched range. Upgrade the installed dependency with the coordinated release;
  an engine source checkout alone cannot replace an installed vulnerable wheel.

### Validation

- The game's `pip-audit -r requirements.txt` passes with no known vulnerabilities
  using Python 3.12. Combined engine and game dependency resolution selects
  Django 6.0.6 and DRF 3.17.2.
- With DRF 3.17.2, 69 engine API tests, 213 game API/Matrix tests, and three
  OpenAPI, ReDoc, and oversized JSON smoke checks pass.
- Game API groups run separately to avoid shared throttle cache exhaustion.
  The combined selection reproduced the same ten HTTP 429 failures with both
  DRF 3.16.1 and 3.17.2. No application throttle behavior changed.


## 6.0.0+underspire.229: Await game shutdown cleanup

### Shutdown

- [Server shutdown](evennia/server/service.py) awaits configured game
  `at_server_stop` hooks in order before final session synchronization and loop
  teardown. Existing synchronous configured hooks and service overrides remain
  supported. Code calling the service's default `at_server_stop()` directly must
  await its result.
- The matching game drains its ordered combat Redis mirror before the loop
  closes, preserving accepted instance saves and deletes during clean restart.
  The five-second drain logs an unclean transition on timeout and leaves the
  active worker uncancelled. This does not bound Redis I/O or total process exit.

### Deployment

- The normal game deployment already stops the service, installs the coordinated
  engine and game revisions, and starts the service. Obsolete Redis consumer
  group removal is optional on upgrade. Rolling back to the group-based reader
  still requires group recreation at current stream tails. See
  [cutover and rollback](docs/source/Components/Redis-Bus-Cutover.md).

### Validation

- Thirty-eight engine shutdown, service, bus, and reload regressions pass.
- Twenty-five game mirror, combat state, turn, and heap scheduler tests pass,
  including a real worker regression for queued instance save/delete ordering,
  failure handling, timeout, admission closure, and game stop-hook integration.
- A fresh Sol adversarial review found no remaining blocker. Full suites passed
  on `.228`; these focused regressions validate the subsequent shutdown change.


## 6.0.0+underspire.228: Confirm and bound bus recovery

### Session recovery

- [The bus handshake](evennia/server/bus_handshake.py) confirms both process
  identities, transport generations, and applied session state before admitting
  actions. Startup order and Redis reconnect no longer depend on one discovery
  message. Process initialization runs once; same-process recovery preserves
  existing session objects, authentication, bindings, menus, and editors.
- [Session reconciliation](evennia/server/bus_sessions.py) uses Portal socket
  membership and current protocol negotiation while retaining Server authority
  over authentication and control bindings. Per-socket incarnations prevent
  reused session IDs from adopting stale sessions.
- [The recovery hook](evennia/server/serversession.py) refreshes client state
  after a surviving connection recovers. Games can extend
  `at_transport_reconnect()` for scene, UI, and media snapshots. Browser output
  resume remains supported.

### Delivery and shutdown

- [Redis transport](evennia/server/redis_transport.py) reads forward from an
  explicit cursor. Failed generations discard unsent work and fence pending
  callbacks. The dedicated bus client does not retry ambiguous writes; retained
  commands and lifecycle requests are never reclaimed or republished.
- Incoming and outgoing queues each admit at most 256 entries and 32 MiB of
  encoded payload. Ordinary data is limited to 224 entries and 24 MiB, reserving
  capacity for control traffic. A serialized frame payload is limited to 8 MiB;
  queue accounting also includes metadata, in-flight writes, and active
  callbacks. A measured 4,096-session snapshot uses 2,586,481 bytes; these bounds are not a production capacity benchmark.
- [Publication results](evennia/server/bus_result.py) separate local admission
  from Redis publication and preserve errback and awaitable callers. Canceling
  one waiter does not cancel or hide the shared publication outcome.
- Portal rejects unavailable input locally with a rate-limited uncertainty
  notice. Lifecycle callers arm restart state only after successful publication.
  An uncertain lifecycle result inhibits watchdog restart until confirmed
  recovery. Operators can explicitly start Server if needed.
- [Reload and reset](docs/source/Components/Redis-Bus-Shutdown.md) close input,
  run cleanup hooks, then allow five seconds for final snapshot publication and
  Portal application acknowledgment. A failed transition reports uncertainty and
  continues process cleanup. Transport stop uses a shared three-second worker
  wait budget and retains ownership of surviving cleanup work. These bounds do
  not limit arbitrary game cleanup hooks or the complete web-worker drain.

### Migration notes

- **Coordinated cutover required.** Stop Portal and Server before installing the
  matching engine and game revisions. Mixed bus versions are unsupported.
  Follow [the cutover and rollback procedure](docs/source/Components/Redis-Bus-Cutover.md)
  for scoped consumer-group maintenance. Rollback must recreate the old expected
  groups at current stream tails to avoid delivering retained actions.
- An interrupted action may already have run. Publication and synchronization
  acknowledgment do not prove command completion, and no automatic command replay
  or exactly-once game-effect guarantee is added. Job retry behavior is retained.
- Games using readiness checks should require confirmed transport readiness.
  Recovery hooks must refresh session-scoped state without rerunning startup or
  puppet hooks. No new dependency or database migration is required.

### Validation

- Real Redis 7.2.5: 18 fault tests cover committed XADD reply loss, process death,
  Redis restart and data loss, queue saturation, retention gaps, delayed final
  application, scoped cutover, rollback through the previous reader, and atomic
  job transitions. Process tests use observable handlers rather than full game
  bootstrap; they do not establish Redis durability, TLS behavior, or production
  capacity.
- Full engine suite: 4,226 tests run successfully with 57 skips. The skipped
  tests include 18 opt-in Redis tests that pass separately.
- Full game suite: 10,307 tests run successfully with one skip. Registry
  compilation, 21 registry tests, and migration drift checks pass.
- Forty-nine focused tests pass on Python 3.12.13 and 3.14.4. Full local suites
  use Python 3.13.13 and SQLite. Remote CI and PostgreSQL/MySQL matrices were not
  run locally.
- [Parallel test workers](evennia/server/tests/testrunner.py) initialize the
  Server after Django assigns database clones and clear identity caches in the
  worker that owns each rolled-back transaction. Worker regression checks and
  database smoke tests pass; serial runner behavior is preserved. Synthetic
  fixture sessions skip background moderation capture only during setup, keeping
  configured capture behavior available to the test body.

## 6.0.0+underspire.227: Engine stability fixes

### Jobs and persistence

- [Redis job retries](evennia/jobs/queue.py) conditionally move the exact leased
  entry in one Lua script. Replacement serialization and key validation precede
  writes; destination append precedes source removal. Repeating a completed move
  does not duplicate a unique source entry, including after a lost reply.
- [Scheduled attribute flushing](evennia/server/engine_systems.py) counts returned
  undurable rows toward the existing consecutive failure alerts. Mixed batches
  count once; clean and durably spooled batches reset the streak.

### Transport and tests

- [Redis bus stop](evennia/server/redis_bus.py) uses one three-second wait budget
  without inserting a sentinel into a full queue. Worker and client cleanup
  ownership prevent restart while old threads survive. Stop rejects further
  publication; an explicit completed restart discards unsent local frames.
- Bus and reload fixtures own scheduled work through setup and cleanup. Azaban
  reload coverage checks the render envelope and text node body.
- Validation: 157 affected engine tests pass without pending-task or coroutine
  warnings. Regression tests cover lost retry replies, failure escalation,
  blocked shutdown, cleanup ownership, and setup handshake dispatch.

### Migration notes

- Redis job connections require EVAL plus TYPE, LRANGE, LPUSH, and LREM permission
  on the processing and destination keys. Script isolation does not provide
  rollback after a partial runtime error or exactly-once handler effects.
- The existing fakeredis dependency now includes its Lua extra. Install engine
  dependencies normally to obtain the test runtime.
- Bus stop is a cooperative abort, not a delivery drain. In-flight writes and
  callback handoffs can finish. No production shutdown caller was added, and
  retained Redis stream recovery behavior is unchanged.
- No database migration or game API change is required. Real Redis ACL and
  memory-pressure behavior were not exercised by these tests.

## 6.0.0+underspire.226 — Emit to a room reaches the room

### Actions

- [`admin.py`](evennia/actions/default/admin.py): a room target of
  `@emit` now always fans out to the room contents. A room holds no
  sessions of its own, so the previous `obj.msg(message)` delivered to
  nobody unless `/contents` or `@remit` set `send_to_contents`. Since a
  bare `@emit <message>` targets the caller's location, the plainest
  form of the verb emitted into the void and still reported
  `Emitted to <room>`. This restores the behaviour the action docstring
  already documented ("if the object is a room, send to its contents"),
  which the legacy `CmdEmit` also never implemented.

### Migration notes

- Non-room targets are unchanged, so `@pemit` and object-directed
  `@emit` keep their existing delivery and echo.
- A room target's caller echo changes from `Emitted to <room>:` to
  `Emitted to <room> and contents:`. Anything asserting on the former
  string for a room target needs updating.
- Games that worked around this by always typing `@remit` are
  unaffected; `@remit` still forces rooms plus contents.
- The legacy `CmdEmit` in
  [`commands/default/admin.py`](evennia/commands/default/admin.py) is
  deliberately left alone. Production input routes through the action
  registry, and the command tree is slated for removal.

---

## 6.0.0+underspire.225 — Shared focus transfer and failure feedback

### Accounts

- [`accounts.py`](evennia/accounts/accounts.py) /
  [`models.py`](evennia/accounts/models.py): shared identity bindings can now
  transfer every live session to a new focus as one operation. The durable
  focus stack is mutated once, then concrete runtime attachments are repaired,
  avoiding partial transitions when several sessions control the same identity.

### Actions

- [`process.py`](evennia/actions/process.py): unexpected Activity failures now
  terminate through one contained path that logs the captured exception,
  unregisters the Activity, invokes `on_error(error)` once, and gives the actor
  safe feedback. Construction, synchronous advancement, Future, and Deferred
  failures follow the same contract. Activity bodies must be generators and
  Activity instances are single-use.

- [`multimatch.py`](evennia/utils/multimatch.py) /
  [`models.py`](evennia/typeclasses/models.py) /
  [`utils.py`](evennia/utils/utils.py): global ambiguous searches no longer
  recurse through the base `get_extra_info()` hook and disappear without a
  result list. Stock location hints and direct hook compatibility remain intact;
  custom extra-info hooks are composed exactly once.

### Migration notes

- Callers that implement Activity bodies as coroutines or arbitrary
  `send()`-capable objects must convert them to generator bodies using the
  documented yield grammar. Reusing a finished Activity instance is rejected;
  create a new instance for another run.
- Custom `get_extra_info()` overrides keep the same return contract. Overrides
  may call `super()` without duplicating stock context.
- Focus-transfer consumers may call
  `DefaultAccount.transfer_focus_sessions(binding, target)` when every live
  session on a shared identity must move atomically. Existing push/pop APIs are
  unchanged.
- Activity scheduler and search-rendering changes are server-side and require a
  normal Server restart or deployment of this engine version.

### Tests

- [`accounts/tests.py`](evennia/accounts/tests.py) covers multi-session focus
  transfer and recovery from mixed runtime attachments.
- [`actions/tests/test_process.py`](evennia/actions/tests/test_process.py)
  covers generator validation, synchronous and asynchronous failures, safe
  notices, hook isolation, single-use enforcement, and terminal cleanup.
- [`utils/tests/test_utils.py`](evennia/utils/tests/test_utils.py) covers
  locationless global multimatches, direct base-hook behavior, stock context,
  and once-only custom composition.

---

## 6.0.0+underspire.224 — Transaction-pool connections and unbound bot frames

### Engine

- [`database_postgres.py`](evennia/server/database_postgres.py) /
  [`settings_default.py`](evennia/settings_default.py): new
  `ENGINE_DATABASE_TRANSACTION_POOLING` setting (default `False`). When
  enabled, PostgreSQL aliases get `CONN_MAX_AGE = 0` and
  `DISABLE_SERVER_SIDE_CURSORS = True`. Persistent connections and
  server-side cursors cannot outlive a PgBouncer transaction-pool
  assignment.

- [`inputfuncs.py`](evennia/server/inputfuncs.py): `bot_data_in` drops a
  frame when the Portal session has no bound Account (teardown /
  reconcile race) and logs one warning per session instead of raising
  on every frame.

- [`web_proxy.py`](evennia/server/portal/web_proxy.py): a closed browser
  socket during a proxied response is a hangup, not a fault. Long-lived
  streams (console feed) end this way whenever a tab closes, so the
  proxy now stops the exchange without a traceback.

### Migration notes

- Stock games are unchanged: the pooling flag defaults off, so
  `CONN_MAX_AGE` / health-check defaults still apply.
- PgBouncer transaction-pool deployments should set
  `ENGINE_DATABASE_TRANSACTION_POOLING = True` in game settings
  (Underspire production already does).
- `bot_data_in` call shape is unchanged. Bot transports that delivered
  one last frame during session teardown will now see a warning rather
  than a traceback.
- Portal web-proxy hangup handling needs a cold Portal restart, not
  `@reload`.

### Tests

- [`test_database_postgres.py`](evennia/server/tests/test_database_postgres.py)
  asserts pooling forces non-persistent connections and disabled
  server-side cursors.
- [`test_inputfuncs.py`](evennia/server/tests/test_inputfuncs.py) covers
  unbound drop-once logging and bound dispatch.
- Existing streaming coverage in
  [`test_web_proxy_streaming.py`](evennia/server/tests/test_web_proxy_streaming.py)
  still asserts the first SSE frame is forwarded while the upstream
  feed remains open.

---

## 6.0.0+underspire.223 — Console station depth + WebSocket Origin gate

### Portal

- [`webclient.py`](evennia/server/portal/webclient.py) /
  [`ws_protocol.py`](evennia/server/portal/ws_protocol.py): when
  `settings.WEBSOCKET_ALLOWED_ORIGINS` is a non-empty allowlist, a non-empty
  handshake `Origin` must match exactly or the upgrade is rejected with HTTP
  403 (`HandshakeDenied`). Empty/missing Origin stays allowed (same policy as
  nginx `/ws`). Unset/empty allowlist keeps stock behaviour.

### Console

- Records station: bulk edit, relations, tags/aliases, account create and
  password set, with preview and audited reasons
  ([`Records`](evennia/web/console/client/src/panels/Records.svelte) /
  console services authority path).
- Moderation station: URL-shareable filters and an exact-signal evidence map
  for triage (no raw identifiers, no scores)
  ([`Moderation.svelte`](evennia/web/console/client/src/panels/Moderation.svelte)).
- In-console `askText` dialog replaces `window.prompt` for consequential
  actions; command palette focus restore on close.
- Mutation outcomes: rejected/partial/fault no longer report as success;
  client reads `X-Console-Outcome`; loader cancellation and `untrack` stop
  stale overwrites and refresh loops.
- Narrow / coarse-pointer layout: flex `100dvh` shell, single scroll for
  virtual tables, 44px targets, iOS-safe input sizing.
- Console static bundle rebuilt for this release.

### Migration notes

- Deployments that want Portal Origin enforcement must set
  `WEBSOCKET_ALLOWED_ORIGINS` (Underspire sets it from `CSRF_TRUSTED_ORIGINS`
  in production settings). Portal code changes need a cold Evennia restart,
  not `@reload`.
- Records `bundles` / authority payloads already moved in prior console work;
  this release continues the console contract — ship the rebuilt static
  bundle with the engine tag.

### Tests

- Portal Origin allow/deny cases in
  [`test_wire_formats.py`](evennia/server/portal/test_wire_formats.py).
- Console client and panel coverage for outcomes, evidence map, and records
  bulk paths landed with the feature commits.

---

## 6.0.0+underspire.222 — Described authorization vocabulary

### Interface

- [`Authorization.svelte`](evennia/web/console/client/src/panels/Authorization.svelte)
  gains a described catalog. Capabilities and bundles are grouped by category,
  each with its description and any `sensitive`, `central only`, or lifecycle
  flag; a bundle's explicit capability list expands on demand. An operator no
  longer has to already know what a key means to grant it.

- Engine-owned and non-active vocabulary is hidden by default. The panel head
  reports the visible count against the total and offers a
  `SHOW SYSTEM + LEGACY` toggle, so a game's own grants are what an operator
  sees first. The capability datalist follows the same visibility.

### Engine

- [`capabilities.py`](evennia/authorization/capabilities.py) adds `category` and
  `status` to `CapabilityDefinition` and introduces `BundleDefinition`, so a
  bundle carries a description, category, and status alongside its capability
  set. `capability_registry.bundle_definitions()` returns them in deterministic
  key order. Both validate their metadata and accept only the `active`,
  `internal`, `legacy`, and `retired` lifecycle statuses. Every engine
  capability and bundle now registers a description and a category.

- Two narrow moderation capabilities join the engine defaults:
  `engine.console.moderation.address` (sanction one raw address or device
  token) and `engine.console.moderation.permanent` (issue a sanction that never
  expires). Both are sensitive, non-delegable, and deliberately sit outside
  every bundle, so they must be granted explicitly.

- [`authorization.py`](evennia/console/panels/authorization.py) serializes the
  new metadata and computes `default_visible` per entry. Bundles ship as a list
  of described objects rather than the previous `{name: [keys]}` map read off
  the registry's private `_bundles` attribute.

### Migration notes

- The panel's `bundles` payload changed shape: a sorted list of
  `{key, capabilities, description, category, status, default_visible}` objects
  replaces the sorted `(name, [capabilities])` pairs. Any downstream consumer of
  that field needs updating.

- `register_bundle()` accepts `description`, `category`, and `status` as
  keyword-only arguments and still defaults to `Uncategorized` / `active`, so
  existing call sites keep working. `expand_bundle()` is unchanged; bundle
  expansion behavior did not move.

### Tests

- Backend coverage checks metadata validation, lifecycle rejection, described
  bundle ordering, and the panel's serialized visibility flags.
  [`Authorization.test.ts`](evennia/web/console/client/src/panels/Authorization.test.ts)
  verifies the panel shows active game vocabulary first and reveals engine and
  legacy entries only after the toggle. `evennia.actions`, `evennia.authorization`,
  and `evennia.console` pass 1255 tests with one skip; the console client suite
  passes 87 tests.

---

## 6.0.0+underspire.221 — Glued action prefixes and room-target put

### Engine

- [`action.py`](evennia/actions/action.py), [`registry.py`](evennia/actions/registry.py),
  and [`parser.py`](evennia/actions/parser.py) let a word-bearing alias be typed
  directly against its arguments. Previously only all-punctuation verbs could do
  this (`.wave`, `"hi`); a game wanting `p.wave` had no way to register it.
  `@action("ppose", "p.", "p,", glued=("p.", "p,"))` marks the eligible aliases,
  `ActionRegistry.no_space_prefix_verbs` merges them with the punctuation set
  longest-first, and the parser tries that combined set. Ordinary word verbs are
  unaffected and still refuse to swallow glued input.

- [`objects.py`](evennia/actions/default/objects.py) fixes `put` for containers
  that declare `accepts_room_character_put`. Item resolution searched the actor's
  inventory unconditionally and `CharacterObjectRules` blocked anything the actor
  was not holding, so such a container could never receive a room object. Both
  now consult the resolved container's type first.

### Migration notes

- `glued` is opt-in and defaults to empty, so existing actions parse identically.
  A glued alias that is not also a registered verb now raises `ValueError` from
  `@action` at import time.

### Tests

- `TestExplicitGluedPrefix` in
  [`test_parser.py`](evennia/actions/tests/test_parser.py) covers the dot form,
  the comma form's preserved marker, and that an unmarked word alias stays
  unglued. `evennia.actions`, `evennia.authorization`, and `evennia.console`
  pass 1255 tests with one skip.

---

## 6.0.0+underspire.220 — Complete moderation connection dossiers

### Interface

- [`moderation.py`](evennia/console/panels/moderation.py) and
  [`Moderation.svelte`](evennia/web/console/client/src/panels/Moderation.svelte)
  turn recent connections into expandable dossiers. The compact list now shows
  account, protocol, client and terminal metadata, screen size, user agent,
  masked network provenance, GeoIP/ASN context, and the presence of every
  identity signal the server records.

- [`ConnectionDossier.svelte`](evennia/web/console/client/src/components/ConnectionDossier.svelte)
  exposes the complete stored `SessionRecord` to authorized senior staff on
  demand, including exact addresses, device and browser tokens, all client,
  Telnet, TLS, HTTP, and header-order fingerprints, browser-session identity,
  negotiation details, protocol flags, lifecycle timestamps, and purge state.
  The serializer derives its field inventory from the model so newly recorded
  fields cannot silently disappear from the panel.

- Empty moderation sections no longer consume most of the page or announce
  that no flag is waiting. Calm state proceeds directly to signal coverage and
  recent connections; flag, sanction, and permanent-ban sections appear only
  when they contain work.

### Security and privacy

- Connection summaries continue to mask direct identifiers. Exact values load
  only after an operator deliberately opens a dossier, and every such open is
  permanently recorded as a `moderation.connection_view` audit event.

### Performance

- The normal moderation refresh still returns bounded connection summaries.
  Complete records are fetched only for the one row an operator opens, avoiding
  bulk delivery and rendering of sensitive, high-cardinality field sets.

### Tests

- Backend coverage verifies every concrete `SessionRecord` field, exact
  sensitive values, audit creation, missing-record handling, readable client
  and GeoIP summaries, and all seven signal populations. The engine console
  suite passes 777 tests with one skip.

- Browser coverage verifies summary disclosure boundaries, complete dossier
  rendering, and the compact calm state. All 86 console-client tests pass, the
  production bundle builds, and Svelte reports no errors or warnings.

### Migration

- No database migration, setting change, or downstream API change. Deploy the
  rebuilt console assets and reload an open console page.

---

## 6.0.0+underspire.219 — Session watches become shadow terminals

### Interface

- [`watch.py`](evennia/console/watch.py) now turns the shared session traffic
  tap into a player-facing terminal stream. Narrative output is rendered through
  Evennia's ANSI/HTML path, prompts remain inline with the submitted command,
  and scene patches and other out-of-band protocol traffic are omitted. A staff
  operator therefore sees the terminal content delivered to telnet, the web
  client, Mudlet, SSH, or another session protocol instead of internal message
  names such as `narrative` and `perception_relay`.

- [`ShadowTerminal.svelte`](evennia/web/console/client/src/components/ShadowTerminal.svelte)
  gives each active watch its own terminal surface. Concurrent watches no longer
  interleave, new output follows the tail until the operator scrolls upward, and
  finished watch lifetimes cannot leak their transcript into a later watch. The
  production console JavaScript and stylesheet are rebuilt with the component.

### Security and privacy

- Submitted input is mirrored only while the watched client has local echo
  enabled. Passwords and other echo-disabled input are absent from the shadow
  terminal, matching what the player can see. Partially typed text, client-side
  aliases, triggers, extra Mudlet windows, and other client-local state remain
  outside the server's view.

- Transcript content remains private to the operator who started the watch,
  held only in bounded memory, and discarded when that watch ends. The existing
  console authorization, reauthentication, reason, lifetime, and audit controls
  are unchanged.

### Performance

- The no-watch path is unchanged from `.215`: each session message pays one
  empty-dictionary truth test, measured there at 4.57 ns in a synthetic CPython
  run. ANSI conversion and terminal-frame allocation occur only for watched
  sessions. Each server watch and the browser feed remain capped at 500 frames,
  so the richer rendering does not introduce unbounded storage or background
  polling.

### Tests

- Backend coverage exercises player-facing rendering, inline prompts,
  out-of-band suppression, echo-disabled input, and independent concurrent
  watches. Browser coverage exercises terminal grouping, transcript lifetime,
  safe rendered output, and scroll-follow behavior. The relevant engine suite
  passes 783 tests with one skip; all 84 console-client tests pass, and Svelte
  reports no errors or warnings.

### Migration

- No database migration, setting change, or downstream API change. Deploy the
  rebuilt console assets and reload an open console page.

---

## 6.0.0+underspire.218 — Portal proxy streams live responses

### Engine

- [`web_proxy.py`](evennia/server/portal/web_proxy.py) now forwards upstream
  response headers and body chunks as they arrive instead of buffering the
  complete response. Server-sent events and other long-lived streaming
  endpoints can therefore deliver their first frame while the connection stays
  open.

### Tests

- [`test_web_proxy_streaming.py`](evennia/server/tests/test_web_proxy_streaming.py)
  covers forwarding an SSE frame before the controlled upstream response closes.

### Migration

- No database migration, setting change, or downstream API change.

---

## 6.0.0+underspire.217 — Compose previews render game markup safely

### Interface

- [`CommandInput.svelte`](evennia/web/webclient/client/src/components/CommandInput.svelte)
  now converts the server's compose preview text through the web client's shared
  Evennia-markup renderer before displaying it. Colour tags now appear as their
  intended styled text instead of literal `|` codes in the compose preview.

- [`compose-preview.ts`](evennia/web/webclient/client/src/lib/compose-preview.ts)
  uses the existing safe `pipeToHtml` conversion path. Raw HTML in preview text
  remains escaped before Svelte inserts the result, so this visual fix does not
  turn the preview into an HTML injection surface. The production shell bundle
  is rebuilt with the renderer.

### Tests

- Browser regressions cover both pipe-colour rendering and HTML escaping. The
  web-client suite passes 478 tests with no Svelte diagnostics.

### Migration

- No database migration, setting change, or downstream game change. Deploy the
  rebuilt static shell asset and reload open web-client pages.

---

## 6.0.0+underspire.216 — The live console feed can authenticate

### Interface

- [`feed.svelte.ts`](evennia/web/console/client/src/lib/feed.svelte.ts) no
  longer opens the console's server-sent event stream with native
  `EventSource`. Every console endpoint requires the scoped
  `X-Evennia-Console` header, but the browser's `EventSource` API has no way to
  attach a custom header. The watch action used the ordinary authenticated API
  and therefore succeeded, while its separate live stream received HTTP 403.
  The Sessions panel could show an active watch indefinitely beside an empty
  transcript; health, metrics, and live log events were refused by the same
  fault.

- The client now uses a streaming `fetch` carrying the same console header as
  every other API request. It parses the SSE frames into the existing reactive
  stores, preserves the last sequence cursor across bounded reconnects, stops
  cleanly when access is revoked or the page closes, and keeps the existing
  limits on retained log and watch lines. The production console bundle is
  rebuilt with the corrected transport.

### Security

- The server-side authorization check is unchanged. The fix authenticates the
  long-lived request instead of exempting it from the scoped-header rule, so a
  watch transcript remains available only to the signed-in console operator
  whose actor ID owns it.

### Tests

- A browser-side regression test opens a real readable stream, asserts that
  the request carries `X-Evennia-Console`, and proves a decoded watch frame
  reaches the private transcript store. All 82 console-client tests pass,
  Svelte reports no errors or warnings, and all 31 engine feed tests pass.

### Migration

- No database migration, setting change, or downstream game change. Deploy the
  rebuilt static console asset and reload an already-open console page.

---

## 6.0.0+underspire.215 -- A session watch that actually watches

### Engine

- [`console/watch.py`](evennia/console/watch.py) and
  [`server/sessionhandler.py`](evennia/server/sessionhandler.py) implement the
  session watch withheld in `.214`. The tap is at the common Server/Portal
  session-handler boundary, after outbound batching and function parsing and
  before inbound commands are handled. It therefore works for telnet, the web
  client, Mudlet, SSH, and other session protocols without teaching the console
  about each client.

  What it shows is the server-side transcript: normalized output frames and
  complete submitted input payloads, explicitly labelled `OUTPUT` and `INPUT`.
  It cannot show partially typed text, client-side local echo, aliases,
  triggers, windows, or other UI state. Input is deliberately unredacted; an
  operator with this power can see a submitted password or other secret if it
  crosses the watched game session.

- Watches are private by content and shared by status. Only the operator who
  started a watch can drain its captured frames, while every operator with
  console access can see that the watch is running, who started it, which
  session it covers, and the stated reason. Each lifetime has a unique watch
  identifier, so ending and restarting a watch cannot make an old transcript
  recur.

- Captured content lives only in a bounded in-memory deque and is discarded
  when the watch ends. A server-clock deadline expires the watch even if the
  operator closes the browser; manual stops, session disconnects, and the
  fallback sweep cancel or close the same lifetime cleanly.

- [`console/feed.py`](evennia/console/feed.py) adds an actor-scoped producer for
  the private frames, and [`web/console/stream.py`](evennia/web/console/stream.py)
  binds that producer to the authenticated console operator rather than to a
  browser-supplied identity.

### Security and audit

- Starting a watch requires full console access, a fresh reauthentication, and
  a non-empty reason. The watched player is not notified. This is intentionally
  a senior-staff surveillance tool, equivalent in sensitivity to the existing
  console shell rather than a routine moderation control.

- Transcript content is never written to the database. Permanent audit rows
  record `watch.began` only when the first frame is actually delivered and
  `watch.ended` with the reason, elapsed lifetime, and frame count. A silent
  session therefore does not acquire a false record saying somebody watched
  traffic that did not exist.

### Interface

- [`WatchFeed.svelte`](evennia/web/console/client/src/components/WatchFeed.svelte)
  and the Sessions panel separate the global list of active watches from the
  signed-in operator's private transcript. Reasons remain visible, traffic has
  unambiguous direction labels, and the stop control ends only that operator's
  watch. The production console bundle is rebuilt with the feature.

### Settings

- `CONSOLE_WATCH_SECONDS` defaults to 1,800 seconds, `CONSOLE_WATCH_FRAMES` to
  500 retained frames per watch, and `CONSOLE_WATCH_LIMIT` to eight concurrent
  watches. Games may lower those bounds without changing engine code.

### Performance

- With no watch running, each inbound and outbound session message pays one
  empty-dictionary truth test before bypassing the capture code. A synthetic
  CPython run of 10 million iterations measured that added guard at 4.57 ns
  (`13.50 ns` including the loop body, best of seven); formatting and bounded
  deque work occur only while at least one watch exists for the session.

### Tests

- Backend coverage exercises watch ownership, audit timing, expiry, bounded
  retention, disconnect cleanup, input and output normalization, and the
  session-handler integration. The relevant engine suite passes 771 tests with
  one skip. The console client passes all 81 tests and Svelte reports no errors
  or warnings.

### Migration

- No database migration and no downstream game change. The three settings are
  optional overrides; their engine defaults activate the bounded behavior.

---

## 6.0.0+underspire.214 -- Two console readouts that were not telling the truth

### Engine

- [`console/panels/moderation.py`](evennia/console/panels/moderation.py): the
  signal coverage readout sorted connections by an exact match against three
  protocol names, and treated everything that did not match as a raw socket.

  The list missed the shipped web client, whose `protocol_key` is
  `webclient/websocket`, so on a stock game every browser connection was filed
  as a raw socket and the TLS handshake coverage read zero against a population
  of zero. This panel exists to say when a configured signal is silently
  absent; it would have reported a correct proxy as a broken one.

  "Everything else" then swept in the connections that are neither web nor
  telnet. A Discord relay negotiates no telnet option and terminates no TLS,
  so counting one as a raw socket that failed to negotiate measures nothing. A
  game whose players all use the web client saw `CLIENT NEGOTIATION 0 / 14
  ABSENT` in red, where the fourteen were its bot links.

  Protocols are now matched as substrings and sort into three groups: web,
  telnet, and neither. A connection carrying no identity signal is counted in
  no population and reported as `other_sessions`, so the sample and the two
  denominators add up on screen.

- [`console/panels/dangerous.py`](evennia/console/panels/dangerous.py): the
  session `watch` action refuses instead of pretending. The control, the reason
  prompt, the presence check and the permanent audit row were all built; the
  thing they describe was not, because nothing mirrors a session's output
  anywhere. The action wrote its audit row and returned `"watching": true`
  while the operator saw nothing.

  The missing feature is the smaller fault. The audit row is permanent, names
  the watched account, and is readable by that account -- that visibility is
  why it was designed to be permanent. The console was therefore writing a
  false statement about a real person into the one record meant to let them
  find out what was done to them. It now records nothing and says which of the
  two faults it is. The client control is removed rather than left to raise.

### Interface

- The withheld address on a connection row is labelled as the host rather than
  the address. The row prints the `/24` beside it and is meant to -- that is
  the coarse key operators correlate on -- so a chip reading `WITHHELD` next to
  a visible network read as a mask that was not working.

### Migration

- No migration and no settings change. A game that saw `CLIENT NEGOTIATION`
  reported as absent, or web coverage reported against a population of zero,
  needs only this release: the recorded rows were always right and the readout
  was grouping them wrongly.
