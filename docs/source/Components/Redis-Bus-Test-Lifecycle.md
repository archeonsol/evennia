# Redis Bus Test Lifecycle

The bus and reload integration fixtures use fakeredis and synchronous frame
handoff. They exercise real IPC handlers and wire formats without running full
Portal and Server processes.

`_BusTestResources` owns each fixture's service globals, Redis patch, private
asyncio loop, pending startup callback list, and delayed callback handles.
The private loop stays stopped while reader threads can schedule work.

Cleanup stops both transports and verifies their workers exited before settling
scheduled work. It cancels owned timers and tasks, waits for task cancellation,
and reports unexpected coroutine cleanup errors. Patches and service globals
are restored even when a cleanup operation fails. Ambient loops, tasks, and
pending startup callbacks belong to their original owners and remain untouched.

Startup hooks are mocked in these transport fixtures so a PSYNC does not start
unrelated maintenance or scheduler loops. Tests still assert that PSYNC calls
the startup hook with the expected restart mode. Startup-hook behavior has its
own coverage in the Server tests.

The Azaban reload test verifies connection survival, session synchronization,
capability preservation, and an outbound `render` envelope containing one
`render.v1` text node with the expected body. Generated node IDs and incidental
HTML are outside that assertion.
