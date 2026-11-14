# Redis Bus Shutdown

## Intentional Server stop

Reload and reset close player admission before cleanup. Current-generation output
and control traffic retain FIFO ordering. Configured game `at_server_stop` hooks
may return awaitables, and Server awaits them before the web worker drain and final
session snapshot. In the configured game, this includes a combat Redis mirror
drain with its own five-second wait. A timeout logs an unclean transition and
leaves the active worker to finish; it does not cancel that worker. Server then
sends its final session snapshot and waits for Portal to acknowledge application.
Redis publication alone does not confirm session preservation.

The synchronization budget is five seconds from final snapshot creation, including
queued publication and application acknowledgment. Earlier cleanup hooks do not
consume that budget. The cleanup hooks, web worker drain, final synchronization,
and transport shutdown do not share one five-second process deadline. Failure or
timeout logs an unclean transition and continues shutdown. The bus remains able
to answer current-peer heartbeats during this drain. It cannot accept a new
session reconciliation while stopping.

Portal applies final state only to matching socket incarnations. Protocol flags
negotiated since the Server's last snapshot remain authoritative. New sockets are
not disconnected by an older final snapshot.

Launcher restart callbacks and restart mode changes require successful lifecycle
publication. A rejected request does not arm them. An admitted request with an
uncertain publication inhibits the watchdog: the request may already have stopped
Server. A fresh confirmed connection clears that inhibition. If Server actually
exited, an operator must inspect the outcome and explicitly start it. Lifecycle
requests are never automatically republished.

## Local transport abort

`stop_bus()` aborts local transport work after the lifecycle drain. It invalidates
queued callbacks, rejects outstanding results, and waits at most one three-second
budget for worker exit and client cleanup. Thread scheduling and logging add
ordinary execution overhead. Already running actions and in-flight Redis writes
may finish; abort does not roll them back.

Shutdown uses a stop event and timed waits, so it needs neither an outbound queue
slot nor event-loop progress. The bus settles the final failure before loop exit.
Publication after stop returns a rejected `PublicationResult`.

A single daemon cleanup worker joins the captured writer and reader before closing
their captured client. Blocked workers or client cleanup may outlive the caller's
deadline. Handles remain available, warnings identify survivors, and restart is
rejected while any old worker or cleanup worker remains alive. Repeated stops reuse
that cleanup worker. Failure settlement must finish before restarting the transport.

An already running worker pair makes start a no-op. A partially surviving pair
rejects start. Server process initialization remains separate from transport
[recovery](Redis-Bus-Recovery.md).
