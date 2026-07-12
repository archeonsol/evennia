# R1 migration status: strangler order + per-surface state

The accreting status log for R1's surface-by-surface migration. The stable design
(seam, node model, sugar facades, guardrails) is in
[r1-universal-pipeline.md](r1-universal-pipeline.md); the arc-wide rationale in
[r1-view-time-resolution.md](r1-view-time-resolution.md). Surface work is built in
the downstream game unless noted; this doc tracks which surfaces have flipped.

## Strangler order (each surface parity-gated, then legacy retired)

Reuse the discipline that worked for emotes: build the node producer, gate on a
byte-parity canary against the legacy string, flip to span-sourced, retire the
legacy path.

1. **`say` / `whisper`** (done). Smallest non-emote surface; proves the pattern
   beyond poses and makes psychosis name-distortion structured. Byte-parity by
   construction (plain + voice); the msg is tagged so the psychosis filter skips its
   name swap (semantic rot preserved). Whisper routes the default template; custom
   templates fall back.
2. **`return_appearance` / room look** (the minefield, ~17 game overrides), behind a
   compat facade. Phased:
   - **Structural seam** (done): a `build_room_view(looker)` producer returns a typed
     `RoomView` (header/desc/atmosphere/things/characters/exits/…) that
     `return_appearance` flattens byte-identically. Presence names route through the
     resolver; overrides customize the section methods and flow through unchanged,
     sidestepping the minefield. This is the seam every consumer below hooks into.
   - **Senses / weather / density gates** (done): applied at the seam (darkness
     degrades to perceivable others + ambient; flagged rooms drop atmosphere; density
     selects sections). Byte-identical at baseline; old flat-string early returns
     retired.
   - **Photo capture + look-at-character** (done): capture emits `<<CHAR:id>>`
     placeholders straight from the pipeline, so frozen photo detail re-resolves per
     viewer (retroactive recog + psychosis); the fragile reverse name-match is retired.
   - **Structured room view (done):** the same `RoomView` yields `render.v1`
     sections and perception-safe scene patches. Knowledge-layered descriptions
     remain game content rather than an R1 engine dependency.
3. **`get_display_name`** stays the identity **base namer**; it must *not* run
   psychosis/perception (identity ≠ display; mechanics use the real name).
   "Migration" means viewer-facing surfaces render through the resolver, not that
   `get_display_name` itself changes. Satisfied already.
4. **Remaining name-bearing surfaces:** all physical (sdesc/recog) paths and recog
   broadcast (live camera feed) done; network-handle (comms/SM) keeps string alias
   misattribution for now (span version deferred); OOC account names are out of R1
   scope.
5. **`msg` facade (done):** object/account `msg()` normalizes text through the
   universal node core only for capable sessions; recursive delivery is marked so
   hooks fire once. Game relay/perception hooks retain their established order and
   telnet continues through the parity text path.

## Retirement (the payoff), gated on prod soak

The span paths only recently reached prod. Until they soak, the string paths remain
the byte-parity fallback; removing them now trades a guaranteed-no-regression state
for risk. Defer until validated: legacy emote body builder, string psychosis
name-distortions (as say/room-look/msg migrate), per-subsystem string name-building
(collapses into the one resolver).
