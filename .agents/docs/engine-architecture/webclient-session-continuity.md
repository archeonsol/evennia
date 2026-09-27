# Azaban: session continuity

How a web session survives its socket. Part of the runtime contract in
[`webclient-protocol-runtime.md`](webclient-protocol-runtime.md), split out
because it is the part with the most ways to be wrong.

Code: `evennia/server/portal/webclient.py` (hold, takeover, keepalive),
`evennia/server/portal/portalsessionhandler.py` (`rebind`),
`evennia/web/webclient/client/src/lib/evennia.svelte.ts` (client).

## Holding and takeover

A socket and a game session are different things. Only an explicit end finishes
a session: the browser closing with 1000/1001 (tab closed, navigation), the
client's `websocket_close`, or the Server disconnecting it. Any other loss (no
close frame, another close code, a write that finds the socket dead, a keepalive
timeout) **holds** the session for `WEBCLIENT_RESUME_GRACE` seconds (default 90).
The Portal keeps it under its sessid and keeps recording Server output into its
replay buffer. The Server is not told.

A reconnect that proves ownership **takes the session over in place**
(`WebSocketClient._adopt`, `PortalSessionHandler.rebind`): same sessid, same bus
socket incarnation (`_bus_socket_id`), the handler entry swapped. The Server sees
no disconnect and no connect, so no logout or login hooks run. Only the socket
facts that changed (address, headers, fingerprint flags) go across, as a
`PCONNSYNC`. The client is replayed what it missed. An unclaimed hold ends as a
normal disconnect when the grace runs out.

Properties, each wrong under the obvious implementation:

- **Ownership takes two proofs.** The browser's signed-in account (the Django
  session the handshake presents) must be the account the held session is logged
  in as, so no one can claim, or read the output of, another account's session.
  The client must also present the token the Portal issued to that connection:
  an account alone cannot tell two tabs apart. Anonymous sessions are not held.
- **The token is issued by the Portal and replaced on every takeover.** It lives
  in the tab's `sessionStorage`, which is per tab and survives a reload. A copied
  tab carries a copy of it; the first tab to use it gets the session and a new
  token, so the copy cannot take it back and forth.
- **A socket that still looks open can be taken over.** A phone that changed
  network reconnects before the old socket is known to be dead. That socket is
  sent close code 4001 (`CLOSE_SUPERSEDED`) and aborted. The shell shows its quit
  screen on 4001 instead of reconnecting, which would take the session back.
- **A replaced socket changes nothing.** Its late close, frames, and timers are
  ignored, and the session handler refuses to disconnect a sessid on behalf of
  anything but its current holder.
- **Losing the link is not a logout.** A held session keeps the browser's
  auto-login stamp. An expired hold keeps it too: the browser never asked to
  leave. Before this, a write that raised `Disconnected` on a half-closed socket
  ran a full `disconnect()`, which cleared the stamp, so the reconnect came back
  signed out.
- **A socket that started its own session does not adopt another.** If a
  `hello` arrives after the wait expired, adopting would orphan that session.

## Replay buffer

Outgoing frames carry a monotonic `s` and are buffered per connection. A takeover
moves the buffer with the session; the client presents its cursor and gets back
the frames above it. State events are idempotent, so re-pushes are safe.

- **Replay storage has count and byte caps.** Each connection retains at most
  400 frames and `WEBSOCKET_RESUME_BYTES` (default 32 MiB). Held sessions
  additionally share `WEBSOCKET_RESUME_STASH_BYTES` (default 128 MiB) and a
  512-hold cap (`RESUME_HOLD_MAX`); past either, the oldest hold ends. Per-connection
  pressure evicts oldest whole frames. Sequence counters retain the highest sent
  value. When evicted frames fall inside the client's gap, the `hello` reply
  carries `gap: true` and the shell says output was lost.
- **`resume_reset` clears the buffer** (the shell's "clear log"), so a reload
  does not replay what the player cleared. The counter runs on.

## Keepalive

The Portal pings every open socket every `WEBCLIENT_PING_DELAY` seconds (default
20; 0 disables) from one Portal-wide loop. A browser answers a protocol ping
without running page code, so this keeps idle connections open through NATs and
proxies and works for a throttled background tab. A socket with no inbound
traffic for `WEBCLIENT_PING_TIMEOUT` seconds (default 60, never below two pings)
is aborted, not closed (a graceful close waits to flush to a peer that is not
reading), and its session is held.

A page cannot see protocol pings, so the shell checks the other direction itself.
After 25 seconds with nothing received it sends `{"t": "ping"}`; the Portal
answers `{"t": "pong"}` directly (never stamped, buffered, replayed, or sent to
the Server). No answer in 10 seconds: the shell gives the socket up with close
code 4000, which is not 1000/1001 and so holds the session, and reconnects at
once. It also probes, or cuts a reconnect backoff short, when the tab becomes
visible, the network comes back, or the page returns from the back-forward cache.

The host's TCP keepalive does not reach the browser behind a reverse proxy: the
Portal's socket is the proxy's local upstream connection. Liveness has to be
WebSocket-level.

## Close logging

Every socket loss logs one line: the cause, the peer's close code (or none, for a
link that just died), whether the closing handshake completed, the connection's
age, how long since the peer was last heard, and the outcome (held, ended,
dropped before hello). Takeovers log the offline time and frames replayed;
refused claims log a warning; expired holds log that they ended.
