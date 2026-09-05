# Redis Bus Shutdown

`stop_bus()` requests an abort of local transport work. It does not drain or
confirm delivery. The stopping caller waits for at most one three-second budget
for worker exit and client cleanup. Thread scheduling and logging add ordinary
execution overhead to that wait.

The writer checks its stop event through timed queue reads and interruptible
retry waits. Shutdown does not need a free outbound queue slot. Reader checks
cover pending reclaim, normal responses, and dispatch between entries.

Stop is cooperative. An in-flight Redis write may finish, and a dispatch that
passed its final stop check may still hand its callback to the reactor. Already
scheduled callbacks are not canceled. Redis stream history and the existing
consumer-group recovery behavior are independent of this local abort.

A single daemon cleanup worker joins the captured writer and reader, then closes
their captured Redis client. A blocked worker or blocked client close may outlive
the caller's deadline. Handles remain available, warnings identify survivors,
and restart is rejected while any old worker or cleanup worker is alive.
Cleanup errors are logged. Repeated stops reuse the same cleanup worker.

Publication after stop raises `RuntimeError`, so it cannot be reported as
accepted through `callRemote()`. Publication before the first start retains the
existing queue behavior. After a completed explicit stop, restart discards unsent
local frames and opens admission for fresh work. Existing queue overflow behavior
is unchanged.

An already-running healthy worker pair makes start a no-op. A partially surviving
pair rejects start. The Server runs initial setup once before discovery can apply sessions. A failed
setup cannot be skipped by a repeated start. Transport recovery uses the
[confirmed handshake](Redis-Bus-Recovery.md) without rerunning process setup.
Lifecycle calls belong to the reactor; publication admission is protected by a
short lock and can be called from other threads.

These methods do not add a shutdown call to the production lifecycle. Tests in
`evennia.server.tests.test_redis_bus` cover full queues, controlled blocked workers,
client ownership, read and retry interruption, and completed stop/restart. They
use real threads with controlled calls and fakeredis, not separate live Redis
and Portal/Server processes.
