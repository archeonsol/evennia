# Azaban: the shell wire protocol (design)

Status: **R1 render and scene phases shipped.** **Azaban** is our own WebSocket
wire protocol (subprotocol token `azaban.v1`), purpose-built for the Svelte shell
and carrying **structured R1 RenderNodes** instead of pre-baked HTML. The name is
engine-generic (the platform brand, not game-specific); the downstream game is
just its first consumer. It slots into the existing subprotocol seam at
`evennia/server/portal/wire_formats/` (RFC-6455 negotiation, a clean `WireFormat`
base) and coexists with the MUD-standard and telnet formats. `AzabanFormat`
lives at `evennia/server/portal/wire_formats/azaban.py`.

The runtime half — handshake ordering, resume, frame encoding, batching, inbound
limits, the OOB event catalog — is
[`webclient-protocol-runtime.md`](webclient-protocol-runtime.md).

## Landscape, and why a *new* format

`azaban.v1` is additive alongside `evennia_v1.py` (legacy JSON arrays, HTML text
— the un-baked-ANSI path R1 moves past), `json_standard.py` (third-party
standard WS clients), and `gmcp_standard.py` / `terminal.py` (telnet/Mudlet).

The MUD-standard format is for **interop**: `data` is a stringified blob and OOB
is GMCP-wrapped, lossy for rich structured payloads. Our shell is **ours, both
ends**, so we want typed, per-viewer-resolved messages (RenderNodes), not GMCP
strings. Standard clients keep their formats; **telnet/Mudlet are unaffected**.

## Envelope

TEXT frames, one JSON object per frame, a **typed discriminated union** (`t` is
the type discriminant; `seq` a monotonic id for RPC correlation; `re` an "in reply
to" `seq`):

```jsonc
{ "t": "<type>", "seq"?: <int>, "re"?: <int>, /* ...typed payload... */ }
```

`permessage-deflate` WS compression is on at the transport layer (free size win on
room look / recordings). BINARY frames carry asset payloads, and msgpack envelopes
if that encoding is negotiated in `hello`.

## Message types

**Server → client**
- `hello`: `{ protocol, resumed }`. Sent in reply to the client's `hello`, after
  any replayed frames, and **stamped last** so its `s` sits above them. The
  client *assigns* its resume cursor from that `s` rather than taking a maximum:
  when the server could not resume the session it restarts its counter at zero,
  and a cursor that only ever climbs would sit permanently above anything the
  new connection will send, so replay would silently never fire again.
- `text`: `{ html, kind? }`. HTML log line for anything not yet a structured node.
- `prompt`: `{ html }`.
- `render`: `{ nodes: RenderNode[] }`. Structured R1 narrative (blocks + inline
  span refs, per-viewer resolved). Only to sessions announcing `caps.rendersNodes`;
  otherwise `deliver` flattens to `text`.
- `patch`: `{ target, ops }`. A delta to the client's structured **scene model**
  (room, occupants, exits, self/vitals, HUD). Applied reactively; no re-sent text.
- `asset`: `{ id, mime, meta, url? }` (+ optional BINARY frame). Media by `id`.
- `oob`: `{ ns, event, data }`. Typed namespaced events not covered above.
- `res`: `{ re, ok, data | error }`. RPC response.

**Client → server**
- `hello`: `{ client, caps: { rendersNodes, images, patches, assets, encoding } }`.
- `cmd`: `{ line }`. A command line.
- `req`: `{ seq, ns, action, data }`. RPC (autocomplete, history, channel ops).
- `oob`: `{ ns, action, data }`. Fire-and-forget actions.

## RenderNode on the wire + capability handshake

`deliver` builds **one** RenderNode per output. For an Azaban session it ships the
node (+ optional `html` fallback); for legacy/telnet it flattens to HTML/ANSI as
today. The shell renders the node natively (clickable character refs, structured
room look, perception/psychosis already resolved server-side) instead of un-baking
ANSI. This is the R1 payoff realised end to end.

Both sides exchange `hello.caps`, which **replaces the `CLIENT_NARRATIVE` flag**:
the server sees `caps.rendersNodes` and ships `render`; other caps (`patches`,
`assets`, `images`, `encoding`, `theme`) tailor delivery. Unknown caps are ignored
(forward-compatible), so features roll out per-cap.

## Scene model + patch updates (the experiential leap)

Model the world as a live per-viewer **structured world model** plus a narrative
log, not only a stream of text lines. The server keeps a scene per viewer (room
blocks, occupants, exits, self/vitals, HUD) and streams `patch` deltas as it
changes; the shell holds the model and renders it reactively. The **log**
(emotes/says/system) stays a `text`/`render` stream; the **scene** becomes synced
state that updates *without* spamming the log. Ops are minimal JSON-patch-like
(`set` / `del` / `add` on a path); the model shape is versioned in the handshake.
Telnet/Mudlet get the text stream only. Rich media (photos, portraits, maps,
audio) is referenced by **id** via the `asset` message, never inlined; bytes
follow as a BINARY frame or a fetchable `url`.

## Schema source of truth

Protocol types live in three mirrored places kept in sync by **shared round-trip
test vectors**: this doc, `protocol.ts` in the shell, and
`evennia/server/portal/wire_formats/azaban.py`. No codegen; revisit if it drifts.
The OOB *event catalog* is separate and is codegen — see the runtime doc.

## Coexistence, the telnet invariant, and open decisions

`azaban.v1` is purely additive on the subprotocol seam: telnet/Mudlet → terminal /
gmcp; standard WS → the MUD standard; legacy web → `evennia_v1.py` until the shell
replaces it. The `deliver` boundary flattens the one RenderNode per negotiated
format: **telnet output stays byte-correct** (explicit invariant, parity-gated).

Resolved defaults: node always ships, `html` fallback only when cheap
(degradation/logging, not the primary path); the envelope carries
`seq`/`re`/`req`/`res` from day one but specific RPCs land per-feature; WebSocket
now (WebTransport/HTTP/3 reserved, the format is transport-agnostic); JSON default
with msgpack a negotiated `caps.encoding` only if profiling shows parse cost.

## Build phases

Phases 1-3 **done**: Azaban core (`hello`/`text`/`prompt`/`cmd`/typed `oob` +
`AzabanFormat` and shell negotiation), `render` (`caps.rendersNodes` selects
immutable `render.v1` nodes), scene model + `patch` (room snapshots and occupant
deltas over opaque viewer handles, raw identity rejected at the wire boundary).
Phase 4, the **asset channel**, is not built — `caps.assets` is still `false`.
