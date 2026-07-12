# Azaban: the shell wire protocol (design)

Status: **R1 render and scene phases shipped.** **Azaban** is our own WebSocket
wire protocol (subprotocol token `azaban.v1`), purpose-built for the Svelte shell
and carrying **structured R1 RenderNodes** instead of pre-baked HTML. The name is
engine-generic (the platform brand, not game-specific); the downstream game is
just its first consumer. It slots into the existing subprotocol seam at
`evennia/server/portal/wire_formats/` (RFC-6455 negotiation, a clean `WireFormat`
base) and coexists with the MUD-standard and telnet formats. A stub `AzabanFormat`
lives at `evennia/server/portal/wire_formats/azaban.py`.

## Landscape

Formats today, alongside which `azaban.v1` is additive:

- `evennia_v1.py`: JSON arrays `[cmd, args, kwargs]`; text is **HTML**. Legacy,
  the un-baked-ANSI path R1 moves past. *(The current shell speaks this.)*
- `json_standard.py`: binary ANSI + JSON envelope, OOB as GMCP-in-JSON. For
  **third-party standard WS clients**.
- `gmcp_standard.py`, `terminal.py`: telnet/Mudlet (raw ANSI + GMCP/MSDP).

R1 already has `RenderNode` + span serialization (`RenderNode.payload()` in
`evennia/narrative/render.py`), and `deliver_node` ships a `narrative` OOB to
capable sessions.

## Why a *new* format (not reuse the MUD standard)

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
- `hello`: `{ server, session, caps }`. Sent on open; announces capabilities.
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
1. **Azaban core:** `hello`/`text`/`prompt`/`cmd`/typed `oob`; portal
   `AzabanFormat` + shell negotiation (`render` reserved).
2. **`render` (done):** `caps.rendersNodes` selects immutable `render.v1` nodes;
   all capable sessions receive them and remaining text normalizes at Azaban.
3. **Scene model + `patch` (done):** room snapshots and occupant deltas use opaque
   viewer handles; raw database identity is rejected at the wire boundary.
4. **Asset channel:** `asset` + binary/URL media.
