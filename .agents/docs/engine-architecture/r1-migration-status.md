# R1 migration status: strangler order + per-surface state

This tracks R1's surface migration. See
[r1-universal-pipeline.md](r1-universal-pipeline.md) for the stable design and
[r1-view-time-resolution.md](r1-view-time-resolution.md) for its rationale.

## Two statuses, not one

"R1" names two things, and conflating them has hidden real work:

- **Engine substrate — shipped.** The `RenderPlan` → `resolve(plan, viewer)` →
  `RenderNode` seam, the reference vocabulary, one delivery service with ordered
  transforms, client capability tiers, canonical + delivery timelines, storage.
- **Game semantic migration — shipped.** Identity-bearing game surfaces build
  canonical plans. Compatibility code is now confined to readers for persisted
  legacy media and the public string facades.

Delivering `RenderNode`s is not the same as being migrated: resolved text in a
node is structured delivery, not view-time resolution. The test is whether the
surface can hand its event to a second viewer and get a different, correct
rendering.

## Strangler order (each surface parity-gated, then legacy retired)

The emote discipline: build the producer, gate on a byte-parity canary, flip to
span-sourced, retire the legacy path. `get_display_name` stays the identity base
namer throughout and deliberately does *not* run psychosis/perception (identity
≠ display). OOC names are out of R1 scope.

## Engine substrate: what closure added

- **`narrative/plan.py`**: immutable span blocks with no canonical `body`;
  `resolve(plan, viewer)` is where references become delivered text.
- **One source of truth**: resolution fills span leaves and derives `body`;
  `RenderNode` rejects mismatches. `map_text()` transforms the tree and
  re-derives the body, so structured and telnet clients cannot diverge.
- **Vocabulary completed**: `ObjectRef` (+ `ExitRef`/`ItemRef`), `SelfRef`,
  `SenderRef`, with resolver methods, namers, serialization. A resolver
  predating a kind renders it via the engine default rather than raising.
  `CharRef.role` selects the *naming policy* (emote, mover, attacker, defender,
  room), so one resolver serves every surface.
- **One delivery service**: `deliver(plan, viewer)` publishes, resolves, and runs
  every transform once; `deliver_to(plan, viewers)` publishes once before the
  viewer loop; `deliver_resolved(node, viewer)` gives unmigrated surfaces the
  same boundary. Publication is idempotent by `plan_id`; relay perspectives
  explicitly do not republish the source event.
- **Cost model**: producers compile once; personalized perception remains
  O(recipients), then one resolved node fans out to all of a viewer's sessions.
  Entity refs use Evennia's idmapper cache rather than an ORM query per viewer.
  Stored media resolves only at playback; relays reuse the source event.
- **Every message travels the core**: object/account `msg()` normalize strings
  and nodes while preserving send/receive hooks exactly once.
- **`msg_contents` builds one event**: a `{key}` template + mapping parses into a
  span plan (`_template_plan`) — identity survives delivery for most call sites
  with no caller change. `$You/$conj` stays per-receiver, same boundary.
- **Client tiers**: `narrative_mode(session)` → `off`/`nodes`/`both`. `both`
  gives a third-party client its text line *and* a structured sidecar, so opting
  in cannot leave a player staring at silence. MSDP-only degrades to text (flat
  encoding cannot carry a node tree; GMCP's JSON can).
- **Canonical timeline**: sinks receive the pre-resolution event; one
  correlation ID is carried by every viewer's node.
- **Deterministic installation**: Django and server initialization install the
  resolver, lookup, transforms, and producers before gameplay.
- **Canonical producers**: `say`/`whisper` and recording capture use
  viewer-invariant `plan_for()` factories.

## Game surface closure

- **Speech and emotes:** `say`, `whisper`, normal poses and literal-third poses
  publish canonical plans. Voice-sensitive wording is still split by audience,
  while cameras and remote viewpoints receive the unresolved source event.
  The live emote loop no longer runs the legacy per-viewer body builder.
- **Rooms and movement:** room looks, exits, occupants, grapple pose fragments,
  departures and arrivals retain references. Exit flicker is an object-reference
  pass with one decision per resolution; the finished-string exit rebuild is no
  longer a writer. Plain `msg_contents` broadcasts are one text plan rather than
  one unrelated node per recipient.
- **Combat:** attacks select prose once and resolve roles per viewer. Grenade
  launch attribution, grapple witness lines, combat-state announcements and
  sniper shots now enter through canonical templates/plans. Catalogue-driven
  mounted-weapon prose also compiles once; vehicle cabins and overlooks receive
  that same combat event without a second crew formatter or republishing it.
- **Network:** player and NPC broadcasts carry `SenderRef`; structural
  misattribution suppresses the old regex attribution pass while retaining
  network degradation. Hails, staff notices and self-confirmations remain
  identity-free text plans.
- **Relays:** overlook, enclosed vehicle cabins, emote relay and Shared Eye all
  re-resolve the source plan. The perspective hook runs before protocol fan-out,
  so `nodes`/`both` clients neither bypass nor duplicate Shared Eye. Multipuppet
  deliberately remains an OOC resolved feed and does not import the puppeteer's
  IC knowledge into the NPC viewpoint.
- **Media:** recordings, live feeds, physical photographs, handset photographs
  and selfies write `renderplan.v1`. Still images freeze authored content but
  resolve entity references for the current viewer. Old `spans`, placeholder
  and frozen-text artifacts remain readable; no new writer emits them.

## Compatibility boundary

The public string APIs remain sugar over the universal delivery boundary.
Persisted legacy media readers remain until their data is migrated or expires.
They are not accepted as new writer formats. Identity distortion belongs to
reference passes; finished-text psychosis is limited to effects that actually
operate on prose (semantic rot and signal degradation).
