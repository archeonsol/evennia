# R1 first slice: structured emote delivery (+ W1 web consumer)

Status: **built (v0), pending live verification.** The first real R1 seam
*beyond* the scoped emote plan, carrying a concrete W1 payoff. Deliberately
narrow. See [committed.md](committed.md) for R1/W1 targets.

Shipped: `evennia/narrative/rendernode.py` (`RenderNode` + `deliver_node`),
`narrative_client` inputfunc, adoption in `DefaultEmoteDelivery.deliver` and in
mootest `world/roleplay/helpers.py::_deliver_emote` (guarded import for engine-pin
safety), and the webclient consumer (`custom-client.js` `narrative` handler +
`CLIENT_NARRATIVE` announce). Tests: `test_rendernode.py` (parity + structured +
mixed), engine narrative suite, mootest rp-action suite. Deploy needs `@reload` +
`collectstatic`, and an `EVENNIA_REF` bump for production (the guarded import
keeps prod safe until then).

## North star: resolve per-viewer at *view* time, not emit time

The real reason to do R1 is not "web gets nicer emotes." It is to fix a
structural flaw in the whole IC-perception system: **names are baked in at the
moment of emit, then frozen.**

Today every surface flattens per-viewer text immediately and throws the structure
away:

- a pose builds a string per current viewer;
- a recording captures `camera_text` — a **single** frozen string, not even
  per-viewer;
- photos / IC broadcasts snapshot already-resolved text.

So a recording made now, replayed later by someone who has *since recognized* a
character, still shows the old sdesc. The name was baked at capture. Frozen
strings cannot re-resolve.

**The fix:** keep a **`CharacterRef`** (a *reference* — char id + role — not a
name) inside the structure all the way to delivery. A single
`render(node, viewer)` resolves refs to names via the sdesc/recog resolver, as
the *last* step. Then:

1. **Consistency (code).** Naming logic is currently duplicated across
   `build_emote_for_viewer`, `format_emote_message`, `format_ic_character_name`,
   `return_appearance`, `resolve_room_pose_for_viewer`, and camera_text. All of
   them collapse into **one** ref resolver. sdesc / recog / skin-tone lives in one
   place, applied everywhere; subsystem naming drift disappears.
2. **Storable + replayable (the functional win).** Recordings, photos, and
   broadcasts store the **node tree** (refs unresolved) and resolve per whoever
   views, *when* they view. Recog applies retroactively, for free, everywhere.
   Impossible with frozen strings.
3. **Composable per-viewer transforms.** Language garbling, blindness/darkness,
   disguise, distance, drunk-slur, voice tags — today tangled string surgery
   inside one emote builder — become ordered render passes over the tree:
   `resolve_names -> apply_language -> apply_perception -> flatten`. Add or
   reorder cleanly.

Design richness strings cannot express: a photo can mark *appearance* frozen
(clothing/skin at capture) while *name* stays live (recog at view). Per-span
freeze-vs-live is a property of the node.

### v0 is a wrapper, not the real thing

The shipped v0 `RenderNode` still carries `body` = a pre-flattened per-viewer
string, with `refs` as a parallel list. It proves the pipeline and hands the web
client type + refs, but **names are still baked at emit.** The wins above need the
**span decomposition**: `build_emote_for_viewer` stops resolving names inline and
emits `[Text, CharacterRef, Text, Speech, ...]`; resolution moves into
`render(node, viewer)`.

### CharacterRef span shape (v1, sketch)

```
RenderNode(kind="emote", msg_type=..., from_id=..., spans=[
    TextSpan("smiles at "),
    CharacterRef(char_id=42, role="target"),     # resolved to a name per viewer
    TextSpan(" and says "),
    SpeechSpan(text="hello", lang="cant"),        # garbled per viewer's languages
])
```
`flatten(node, viewer)` walks spans, resolving `CharacterRef` via the sdesc/recog
resolver and `SpeechSpan` via the language passes, producing today's exact string
(parity). A rich client renders each span as a real node (clickable target, etc.).

### Parity finding (slice 1 builder)

The byte-parity harness (`world/rpg/tests/test_emote_spans_parity.py`) surfaced a
latent legacy quirk: `build_emote_for_viewer` **double-wraps** a possessive name
when the name wrapper's leading char is a *non-word* char, because the second
`matched_name` sub re-matches the just-substituted inner name. Production names
are wrapped in ANSI codes (`|c...|n`, leading char is a word char), so the
re-match never fires and there is no double-wrap. The span renderer is
single-wrap (correct) and matches production. **Before flipping a live surface to
spans, gate on a parity test using real characters + real
`skin_tones`/`rp_features`** (not stubs), to confirm the ANSI-adjacency
assumption for every naming path; the span path is an improvement (no double-wrap)
only where names are word-char-adjacent, which is the production case.

### Staging (strangler; each slice ships end-to-end)

1. **Emote span decomposition.** `CharacterRef`/`SpeechSpan`, one resolver,
   resolution at render. Poses/emotes only. Flatten reproduces today's strings
   byte-for-byte (parity). Contained to the emote builders (narrative +
   `world/rpg/emote.py`), *not* `return_appearance`.
2. **Recordings / photos / broadcasts store the tree.** Persist the node, resolve
   at playback. This is where retroactive recog becomes visible. Needs a node
   serialization + a migration path for existing stored strings.
3. **Room look (`return_appearance`).** Last, behind a compat facade. The override
   minefield (~17 `return_appearance` overrides in mootest).

   **Finding (before building slice 3):** unlike emotes/recordings, room look is
   *already* fully per-viewer -- `typeclasses/rooms/narrative.py` resolves every
   character name through `get_display_name(looker)` live. And **photos already
   re-resolve** names per viewer, via a `<<CHAR:id>>` string-placeholder hack in
   `typeclasses/broadcast.py` (`take_photograph` writes placeholders;
   `Photograph.return_appearance` resolves them to the reader's
   `get_display_name`). So the two payoffs that motivated slices 1-2 (per-viewer
   live output; retroactive recognition in stored/replayed content) **already
   exist here.**

   Therefore span-ifying `return_appearance` is **high-risk, low-marginal-value**
   right now: it would refactor the most-overridden path for a robustness/
   unification gain, not a new capability. **Recommendation: do not big-bang the
   minefield.** Two bounded alternatives instead:

   - **(a) Harden photos onto the render primitive.** `take_photograph` already
     iterates the visible characters (ids + names), so it can emit `CharRef`
     spans instead of regex `<<CHAR:id>>` placeholders, and `Photograph` renders
     them through `render_spans`. Contained to the photo code, no
     `return_appearance` change. Gains robustness (no fragile name-replace
     fallback) and unlocks perception/disguise/freeze-vs-live for photos. Optional
     polish.
   - **(b) Defer full `return_appearance` spans to the webclient rebuild.** The
     real reason to make room look a span tree is a **structured consumer**: the
     React client rendering clickable characters/exits/items. Do it *then*, driven
     by that consumer (W1), not speculatively now. This is the disciplined "no
     refactor without a consumer" call and avoids cracking the minefield for a
     marginal gain.

Guardrail unchanged: if a slice needs touching N existing overrides, it is the
wrong slice.

## The one rule for this whole arc

R1 blows up the codebase if done as a refactor. `msg`, `return_appearance`,
`get_display_name`, `at_say` are the most-called and most-*overridden* paths in
engine + game (in mootest, `return_appearance`/`get_display_name` are overridden
across sdesc, recog, disguise, cyberware, matrix, npc, creatures). Changing their
contract breaks every override at once.

So R1 is a **strangler**, never a rewrite:

- New path is **additive**: `render() -> RenderNode -> deliver()`.
- The string API (`msg`, `return_appearance`) keeps returning strings, unchanged.
  Structured delivery is layered *underneath/alongside*, gated on client
  capability.
- Migrate **one surface at a time**, each shippable end-to-end.
- **Guardrail:** if a slice needs touching N existing overrides, it is the wrong
  slice. Back out and pick a smaller one.

This doc is one slice. It touches **zero** existing overrides.

## Why emotes first

The narrative package already did the hard half. `DefaultEmoteDelivery`
([delivery.py](../../../evennia/narrative/delivery.py)) is:

```
build_plan(caller, text) -> EmotePlan      # structured, viewer-INVARIANT
deliver(plan)            -> per viewer: flatten to a STRING -> viewer.msg((str, {type}))
```

The plan is already structured. The only place it collapses is the final
per-viewer flatten in `deliver`. That flatten is exactly the R1 boundary. And the
data thrown away there is exactly what a rich client wants: **per-viewer target
references** (which character each resolved name/sdesc points to), the speaker,
and the message type. Plain text + `type` (already sent today) cannot express
"this word is a link to character #42, shown to *you* as 'the tall man'."

Emotes are also the safest slice: the package is new, self-contained, and has
**no legacy overrides** of its delivery. Contrast with `return_appearance` (the
override minefield), which this slice explicitly does not touch.

## What ships

A per-viewer **RenderNode v0** produced in emote `deliver`, and a `deliver_node`
step with two consumers:

- **telnet / any un-upgraded client**: flatten the node to the *same* ANSI string
  as today and send via `viewer.msg`. Byte-for-byte parity is a test requirement.
- **capable web client (W1)**: send the node as structured OOB; the webclient
  renders it richly (styled by type, target names carrying character ids for
  hover/click later).

Capability is negotiated exactly like the editor's `CLIENT_EDITOR`: the client
announces it renders narrative nodes; until then, everyone gets the flattened
string. Zero regression by construction.

### RenderNode v0 shape (intentionally minimal)

Not the full engine node taxonomy. Just enough for emotes, room to grow into
spans later:

```
RenderNode(
    kind="emote",
    msg_type="pose" | "say" | ...,
    from_id=<dbref of speaker>,
    body=<the per-viewer flattened string>,      # what telnet uses verbatim
    refs=[{span or name, char_id, display_name}] # per-viewer target references
    self_echo=<bool>,
)
```

`body` is the existing per-viewer string, so the telnet flatten is trivially the
current output (parity). `refs` is the new structured payload (target character
ids + the name this viewer saw). **Deeper span decomposition** (splitting `body`
into typed inline spans so names are true nodes, not a parallel `refs` list) is a
*later* slice, not this one. v0 wraps; it does not yet dismantle the string.

### Delivery seam

```
render_emote_for_viewer(plan, viewer) -> RenderNode      # uses existing per-viewer resolution
deliver_node(node, viewer):
    if viewer session announces narrative-render support:
        session.msg(narrative=(node_payload, {}))         # W1 structured path
    else:
        viewer.msg((node.body, {"type": node.msg_type}), from_obj=speaker)   # unchanged
```

`viewer.msg` stays the flatten facade. The structured branch is additive and
capability-gated.

## Explicit non-goals (scope fence)

Out of scope for this slice, by design:

- **No change to `msg` / `return_appearance` / `get_display_name` / `at_say`
  contracts.** They keep returning strings.
- **No touching any existing override.** The `NameResolver` the game already
  plugs in (sdesc/recog) is reused as-is to fill `display_name`/`char_id`.
- **No full RenderNode taxonomy.** v0 is emote-shaped only. Rooms, prompts,
  system messages come in their own later slices.
- **No inline-span decomposition** of the body yet (see v0 note above).
- **Say/pose only.** Room descriptions (`return_appearance`) are the last slice,
  behind a compat facade, precisely because they are the override minefield.

If implementing this slice starts requiring any of the above, stop: wrong slice.

## W1 client half (mootest webclient)

- The webclient (and later Mudlet) announces narrative-render support on connect,
  mirroring the editor's `editor_client` handshake. A new inputfunc sets a
  session flag the engine reads in `deliver_node`.
- A new OOB handler renders the `narrative` payload: the body styled by
  `msg_type`, target names wrapped with their `char_id` (data attribute) for
  hover/click affordances later. Falls back to the plain `text` line if
  unsupported.
- This is additive to the custom webclient (same pattern as the editor overlay),
  and slots into a future React client's RenderNode consumer unchanged. It is the
  first real W1 payload.

## Tests

- **Parity:** with no capability flag, the bytes delivered to a viewer are
  identical to today's emote output (drive the existing emote tests unchanged;
  they must stay green).
- **Structured:** a session with the capability flag receives a `narrative` OOB
  payload with the correct `body`, `msg_type`, `from_id`, and per-viewer `refs`
  (each target's `char_id` + the name that viewer saw).
- **Resolver reuse:** a custom `NameResolver` (sdesc-style) drives both the
  flattened body and the `refs` display names, proving no override changed.

## Sequencing

Ship this slice end-to-end (engine `deliver_node` + parity + webclient consumer)
before any second slice. Only once emote nodes are live and stable does the next
slice (candidate: `say` speech spans, or prompts) begin. `return_appearance`
stays last.

The webclient rebuild (see `mootest/docs/webclient_revamp.md`) consumes these
nodes through its output adapter, so R1 slices and the client rebuild reinforce
each other without either blocking the other.
