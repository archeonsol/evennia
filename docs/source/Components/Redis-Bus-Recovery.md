# Redis Bus Recovery

Portal and Server distinguish transport readiness from process liveness. The
transport states are `disconnected`, `synchronizing`, `ready`, and `stopping`.
Launcher PID status continues to describe process liveness.

Portal initiates discovery with a fresh challenge. Server answers with its own
challenge and the process and transport identities of both peers. A snapshot,
application acknowledgment, reciprocal confirmation, and final Server state
complete the exchange. Portal admits input only after that final state is applied.
Discovery alone cannot replace a ready generation. Pending exchanges have a
four-second deadline; healthy peers exchange heartbeats once per second.

A Portal membership revision covers socket and negotiation changes. Portal checks
it again before applying final Server state. If it changed during synchronization,
a fresh exchange captures the current sockets. Confirmations from an expired
exchange cannot authorize the replacement generation.

## Session authority

Socket identity consists of Portal process identity, numeric session ID, and a
random socket incarnation. A replacement WebSocket may reuse its numeric ID, so
that number alone cannot identify a surviving socket.

Portal owns socket membership and negotiated protocol metadata. Server owns
account authentication, control bindings, command counters, and runtime objects.
Same-process reconciliation retains each matching Server session object. It does
not call login, puppet attachment, menu initialization, or startup restoration.
Changed Portal protocol flags are merged against the previous Portal snapshot;
an unchanged stale flag cannot reverse a Server-side change.

Absent sockets follow normal Server disconnect cleanup. New sockets follow normal
connection handling. Protocol authentication captured before the first Server
mirror preserves HTTP session sharing and SSH authentication. A stale mirrored
`uid` cannot authenticate a new socket. Server disconnect records remain until
Portal confirms socket absence, preventing an uncertain disconnect from reviving
the socket's initial authentication.

Server sends current authentication back only for matching socket incarnations.
This update does not invoke Portal login hooks or disconnect unrelated sockets.
Ordinary PCONN handling also confirms Server application so those sockets can be
restored during a later process reload.

## Process startup

Initial setup and session restoration run once per Server process. Failed partial
startup requires a process restart; later discovery cannot rerun completed hooks.
Sockets confirmed by the previous Server are reconstructed using the supported
reload state. Never-admitted sockets use normal connection handling instead.
Startup hooks receive the actual restart mode. Portal consumes that mode once
per newly ready Server process, not on a surviving-process transport reconnect.

## Client state

Portal retains validated Azaban, editor, and narrative capability declarations
before the input readiness gate. Telnet negotiation continues to update local
protocol metadata. Player actions, including editor saves, are not retained for
later input delivery. Browser output resume remains independent of bus recovery.

`ServerSession.at_transport_reconnect()` runs after Server sends the final
confirmation. The default hook reopens an active web editor. Games can override
it to send current scene or UI state to that session without commands, movement,
login, or puppet hooks. Call the base hook to preserve editor refresh.

The recovery protocol and subsequent delivery changes form one coordinated
engine/game release. Mixed transport versions are unsupported.
