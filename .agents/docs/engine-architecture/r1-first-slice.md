# R1 first slice: structured emote delivery (+ W1 web consumer)

Status: **built (v0), pending live verification.** The first real R1 seam *beyond*
the scoped emote plan, carrying a concrete W1 payoff. Deliberately narrow. The
arc-wide rationale (why resolve per-viewer at view time) lives in
[r1-view-time-resolution.md](r1-view-time-resolution.md); finishing R1 across every
surface is [r1-universal-pipeline.md](r1-universal-pipeline.md); targets are in
[committed.md](committed.md).

Shipped: `evennia/narrative/rendernode.py` (`RenderNode` + `deliver_node`), a
`narrative_client` inputfunc, adoption in `DefaultEmoteDelivery.deliver`, a
downstream game emote-delivery adopter (guarded import for engine-pin safety), and
a webclient consumer (a `narrative` handler + a `CLIENT_NARRATIVE` announce).
Tests: `test_rendernode.py` (parity + structured + mixed) plus the engine narrative
suite. Deploy needs `@reload` + `collectstatic` and an `EVENNIA_REF` bump for
production (the guarded import keeps prod safe until then).

## This slice touches zero overrides

R1 is a **strangler** (see the [rationale doc](r1-view-time-resolution.md)): the new
`render() → RenderNode → deliver()` path is additive, the string API keeps returning
strings unchanged, and each surface migrates one at a time. This doc is one slice,
and it touches **zero** existing overrides.

## Why emotes first

The narrative package already did the hard half.
[`DefaultEmoteDelivery`](../../../evennia/narrative/delivery.py) builds a
structured, viewer-invariant `EmotePlan`; the only place it collapses is the final
per-viewer flatten in `deliver`, exactly the R1 boundary. The data thrown away there
(which character each resolved name points to, the speaker, the message type) is
exactly what a rich client wants. Emotes are also the safest slice: the package is
new, self-contained, and has **no legacy overrides** of its delivery. Contrast with
`return_appearance` (the override minefield), which this slice does not touch.

## What ships (RenderNode v0)

A per-viewer `RenderNode` produced in emote `deliver`, intentionally minimal
(`kind`, `msg_type`, `from_id`, `body` = the per-viewer flattened string, `refs` =
target references, `self_echo`), plus a `deliver_node` step with two consumers:

- **telnet / any un-upgraded client**: flatten the node to the *same* ANSI string
  as today and send via `viewer.msg`. Byte-for-byte parity is a test requirement.
- **capable web client (W1)**: send the node as structured OOB; the webclient
  renders it richly (styled by type, target names carrying character ids for
  hover/click). Capability is negotiated exactly like the editor's `CLIENT_EDITOR`.

`body` is the existing per-viewer string, so the telnet flatten is trivially the
current output. **Deeper span decomposition** (splitting `body` into typed inline
spans so names resolve at render, not emit) is a *later* slice, generalized in
[r1-universal-pipeline.md](r1-universal-pipeline.md).

## Scope fence + tests

Out of scope by design: no change to `msg` / `return_appearance` /
`get_display_name` / `at_say` contracts; no touching any existing override (the
game's `NameResolver` is reused as-is); no full RenderNode taxonomy; no inline-span
decomposition yet; say/pose only. If a slice starts requiring any of the above,
stop: wrong slice.

Tests gate three things: **parity** (no capability flag → delivered bytes identical
to today's emote output), **structured** (a capable session receives a `narrative`
OOB with the correct `body`/`msg_type`/`from_id`/per-viewer `refs`), and **resolver
reuse** (a custom sdesc-style `NameResolver` drives both the body and the `refs`,
proving no override changed). Ship this slice end-to-end before any second slice;
`return_appearance` stays last.
