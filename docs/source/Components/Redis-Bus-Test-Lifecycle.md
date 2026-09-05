# Redis Bus Test Lifecycle

The bus and reload integration fixtures use fakeredis and queued main-thread frame
handoff. They exercise real IPC handlers and wire formats without running full
Portal and Server processes.

`_BusTestResources` owns each fixture's service globals, Redis patch, private
asyncio loop, pending startup callback list, and delayed callback handles.
The handoff patch covers setup, test execution, and cleanup. Tests pump owned
frame callbacks on the main thread before inspecting protocol state. The
private loop stays stopped while reader threads can schedule work. If a worker
survives stop, cleanup reports failure and does not drive that loop.

Cleanup stops both transports and verifies their workers exited before settling
scheduled work. It cancels owned timers and tasks, waits for task cancellation,
and reports unexpected coroutine cleanup errors. Patches and service globals
are restored even when a cleanup operation fails. Ambient loops, tasks, and
pending startup callbacks belong to their original owners and remain untouched.

Startup hooks are mocked in these transport fixtures so discovery does not start
unrelated maintenance or scheduler loops. Tests still assert that confirmed synchronization calls
the startup hook with the expected restart mode. Startup-hook behavior has its
own coverage in the Server tests.

The Azaban reload test verifies connection survival, session synchronization,
capability preservation, and an outbound `render` envelope containing one
`render.v1` text node with the expected body. Generated node IDs and incidental
HTML are outside that assertion.

## Real Redis fault tests

Set `EVENNIA_REDIS_SERVER` to a local Redis executable to opt into these modules:

- `evennia.server.tests.test_redis_transport_live`: real socket reply loss for
  actions and lifecycle requests, count/byte saturation, bounded stop, and trimming.
- `evennia.server.tests.test_redis_bus_processes`: actual bus and handshake in
  separate spawned processes, startup ordering, process death, reconnect, retained
  session state, stale input, and final snapshot application acknowledgment.
- `evennia.server.tests.test_bus_cutover`: the exact operator maintenance block,
  the previous reader after rollback, atomic job transitions with lost replies,
  unrelated-state preservation, and forced child cleanup.

Every suite starts a test-owned Redis instance with persistence disabled. It uses
an isolated endpoint and process handles for cleanup, so it needs no configured
Redis service. Local socket permissions are required. The cutover suite also needs
the `underspire.227` git tag to inspect the previous reader implementation.

The TCP proxy consumes a real successful XADD reply before closing the client's
socket. An independent Redis client checks the committed stream contents. The job
test injects an exception after successful real Lua execution and independently
checks the destination list. These distinguish publication uncertainty from
command completion and from job side-effect guarantees.

The spawned peers use actual transport, scheduling, handshake, and IPC with small
observable session handlers. They do not run the full application bootstrap or
establish production capacity. Engine session/lifecycle tests and downstream game
tests provide the corresponding application coverage.
