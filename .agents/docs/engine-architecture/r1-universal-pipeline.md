# R1 completion: the universal render/deliver pipeline

Status: **design; the keystone.** The seams (emote / recording / photo) proved
the model. This finishes it: **every** viewer-facing output path becomes sugar
over `render(obj, viewer) -> RenderNode -> deliver(node, viewer)`. We are
replacing the inherited string-append delivery wholesale — this is an engine
change that benefits the game as a whole (robust per-viewer naming, perception,
psychosis, recordings, photos, and any future consumer), independent of any
client.

## The problem with inherited delivery

Output today is built *and* flattened in one step, per call site: `msg`,
`at_say`, `return_appearance`, `get_display_name`, scene broadcast each produce a
finished **string**, with names/perception/psychosis already baked in via
scattered regex/NLP. There is no "render to a structured tree for viewer X" step
separate from "send to viewer X." That is why identity is lost at the boundary,
why psychosis distortions string-match and silently fail, why photos re-parse
rendered text, and why every subsystem re-implements per-viewer naming.

## The seam

Two functions, one contract:

```
render(obj, viewer, *, kind, **ctx)  -> RenderNode      # structured, viewer-invariant refs
deliver(node, viewer, session=None)  -> None            # resolve per viewer, flatten per protocol, send
```

- **`render`** builds a `RenderNode`: a tree of blocks and inline spans holding
  *references* (character, exit, item, speech), not resolved names. Viewer-shaped
  facts (recog, disguise) are *not* pre-applied here.
- **`deliver`** runs the per-viewer resolver + passes (identity → perception →
  psychosis → language), then flattens to the target protocol (ANSI text for
  telnet, structured payload for the shell, plain for AI/accessibility, stored
  tree for recordings/photos). This is the single point where names become text.

`RenderNode` structured **all the way to the delivery boundary** — never
flattened mid-pipeline. That constraint is what makes one render feed many
consumers.

## The node model (generalize the span work)

Today's emote spans (`TextSpan / CharRef / PronounRef / SpeechSpan` in
`evennia/narrative/render.py`) are the inline vocabulary. Generalize to:

- **Block level:** `Line`, `Paragraph`, `Section` (room desc), `ListBlock`
  (exits, contents), `SystemBlock`. A `RenderNode` is a block tree.
- **Inline level:** the existing spans + `ExitRef`, `ItemRef`, `SelfRef`. New
  ref kinds resolve through the same resolver/pass machinery.

The resolver + pass pipeline (`perceivable`, psychosis `distort_display_name` /
`distort_narration`, language) already exist and stay — they just get applied by
`deliver` uniformly instead of per-surface.

## The string API becomes sugar

Backward-compatible facades keep every existing call site working:

```
obj.msg(text)            -> deliver(Line(TextSpan(text)), viewer)      # or pass-through
obj.at_say(...)          -> deliver(render(speaker, viewer, kind="say"), ...)
room.return_appearance() -> flatten(render(room, viewer, kind="look"), viewer)  # returns str
get_display_name(viewer) -> flatten(resolve CharRef(obj) for viewer)            # returns str
```

Legacy string callers get a flattened string; structured consumers (shell, store)
get the node. No call site is forced to change. Overrides that build strings keep
working until their surface is migrated.

## Strangler order (each surface parity-gated, then legacy retired)

Reuse the exact discipline that worked for emotes: build the node producer +
resolver reuse, gate on a byte-parity canary against the legacy string, flip to
span-sourced, retire the legacy path.

1. **`say` / `whisper`** — smallest non-emote surface. Proves the pattern beyond
   poses; makes psychosis name-distortion structured here (retires
   `_swap_speaker_identity` for says). **Room `say`: done** —
   `render_say_for_viewer` (`world/rpg/emote_spans.py`); byte-parity by
   construction (plain + voice), `SpeechSpan.quoted=False` so the template owns
   the quotes; the msg is tagged `psychosis_spanned` so `PsychosisFilter` skips
   its name swap/horror (semantic rot preserved). Tests:
   `world/tests/test_say_spans.py`. **Whisper: done** —
   `render_whisper_for_viewer` for the default template (unformatted speaker name
   via `CharRef.formatted=False`); custom templates/mappings fall back. Real-char
   regression (`test_roleplay_mixin.py`) green.
2. **`return_appearance` / room look** — the minefield (17 overrides), behind a
   compat facade. Phased:
   - **Phase 1: done** — character presence *names* route through the resolver
     (`render_room_char_name`, `world/rpg/emote_spans.py`); byte-parity at
     baseline; psychosis applies to room-look names (room look is not a social
     msg type, so no `PsychosisFilter` overlap). The unified foundation.
   - **Phase 2 (structural seam): done** — `Room.build_room_view(looker)` returns
     a `RoomView` (typed sections: header/desc/atmosphere/ambient/things/furniture/
     characters/exits/footer); `return_appearance` flattens it byte-identically
     (`RoomView.flatten`). Overrides customize the `get_display_*` section methods,
     which flow through unchanged — the minefield is sidestepped. This is the seam
     every consumer below hooks into. (`typeclasses/rooms/{base,narrative}.py`.)
   - **Phase 3 (senses gate): done** — `Room._apply_senses(view, looker)` at the
     `build_room_view` seam degrades to what the looker perceives; staff/sighted
     unchanged, darkness → the canonical dark line + presence of *perceivable*
     others + ambient (dark → shapes). The old flat-string early return retired;
     byte-identical when alone. Extend here for blindness / netrunner AR.
     (`world/tests/test_room_senses_gate.py`.)
   - **Phase + (weather gate): done** — `Room.suppresses_weather(looker)` drops the
     atmosphere section for flagged / `hermetic` rooms at the seam (per-axis
     exposure still handles sealed axes inside the producer). Non-regressive;
     `enclosed` keeps its by-design faint line. (`test_room_weather_gate.py`.)
   - **Phase 7 (density): done** — `Room.LOOK_DENSITIES` + `_apply_density`; one
     render, density selects sections (`brief` = header/characters/exits).
     Honors `looker.db.look_density`; a toggle command is a small follow-up in the
     actions framework. (`test_room_density.py`.)
   - **Phase 4 (pending — needs a knowledge model):** knowledge-layered desc
     (faction/lore/discovered) as desc sub-blocks.
   - **Phase 5 (pending — build with the client):** structured `room_view` payload
     over the narrative OOB path (clickable / AI-perceivable). Gated on a consumer
     (the Svelte shell) so it is not speculative; `RenderNode.spans`/`payload` +
     the named-core producer are the seam.
   - **Phase 6 (photo capture): done** — `take_photograph` captures via
     `build_room_view(caller, include_looker=False, _capture=True)`; in capture
     mode the room render emits `<<CHAR:id>>` placeholders itself (via
     `_ic_room_char_name` / the pose path in `get_display_characters`), so the
     photo span tree comes straight from the pipeline. The fragile capture-time
     reverse name-matching regex is retired; `build_photo_spans` still tokenizes
     and `PhotoResolver` still resolves per viewer (retroactive recog + psychosis).
     (`world/tests/test_photo_capture.py`.)
   - **Look-at a character: done** — `Character.return_appearance` routes the
     subject's name through the resolver, so psychosis/perception apply on
     look-at as in room look/emotes (was undistorted before). `_capture=True`
     emits the name as a `<<CHAR:id>>` placeholder, so the photo's frozen char
     *detail* resolves per viewer too — the reverse name-swap in `take_photograph`
     is retired. (`world/tests/test_look_at_character.py`.)
3. **`get_display_name`** — stays the identity **base namer** (delegates to
   `cached_display_name_for_viewer`), which the resolver already wraps. It must
   *not* itself run psychosis/perception passes (identity ≠ display; mechanics use
   the real name). "Migration" = ensure viewer-facing surfaces render *through*
   the resolver, not that `get_display_name` changes. Effectively satisfied.
4. **Remaining name-bearing surfaces (by identity axis):**
   - *Physical (sdesc/recog):* room look, emotes, say/whisper, recordings, photos,
     **look-at** — all done. look-at-object / exit lines carry ~no character
     names (near-zero value).
   - *Recog broadcast:* **live camera feed — done** (routes through the recording
     span path; per-watcher recog, `test_live_broadcast_recog.py`).
   - *Network handle:* comms/SM — **alias misattribution already exists**
     (`PsychosisFilter` network path → `_apply_sm_who_alias_misattribute`; a
     psychotic viewer sees a wrong/decoy callsign). String-based; a span-based
     version is part of the deferred network-span work.
   - *OOC:* account channels use real account names — out of R1 scope.
5. **`msg` itself** — NOT "thin-able": `Character.msg` carries multi-puppet relay,
   traceback capture, drug color-shift, shared-eye, *then* `PsychosisFilter`.
   Wholesale rerouting is the forbidden big-bang. The realistic endgame is
   retiring the msg-level `PsychosisFilter` string distortions (below), gated on
   prod soak.

## Retirement (the payoff) — GATED ON PROD SOAK

The span paths for room look / say / look-at / photos only reached prod at
`EVENNIA_REF underspire.99` (this cycle). Until they have soaked, the string
paths remain the byte-parity fallback — removing them now trades a
guaranteed-no-regression state for risk. Defer until validated:

- Legacy `_build_viewer_body` (emote) — after clean prod play.
- String `PsychosisFilter` name-distortions — as say/room-look/msg migrate.
- Per-subsystem string name-building — collapses into the one resolver.

## Guardrails (unchanged, load-bearing)

- **Additive + parity-gated.** No surface flips until its node render is
  byte-identical to legacy for the baseline (non-modified) viewer. Modifier-active
  viewers (psychosis/perception) are span-authoritative, as now.
- **Identity ≠ display.** Refs carry real ids to the boundary; passes are
  cosmetic; mechanics never see distorted output.
- **No big bang.** One surface at a time; if a slice needs touching N unrelated
  overrides, it is the wrong slice.
- **`EVENNIA_REF`** must ship the seam symbols to prod; guarded imports keep it
  dormant until pinned.

## What exists vs what's new

- **Exists:** the node substrate (`render.py`), the resolver + pass pipeline,
  `deliver_node` (emote), recordings/photos, byte-parity harness pattern.
- **New here:** the general block/inline node model, `render`/`deliver` as the
  named core, the string-API-as-sugar facades, and migrating `say` → room look →
  `get_display_name` → the rest → `msg`.
