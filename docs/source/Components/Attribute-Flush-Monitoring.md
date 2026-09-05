# Attribute Flush Monitoring

The `flush-attributes` engine system runs at `ATTRIBUTE_FLUSH_INTERVAL`.
It calls `flush_all_dirty()` and passes each returned batch to the flush metrics
and pending-backlog log helpers, in that order.

A result with `failed > 0` counts as one failed fire, regardless of the number
of failed rows or other rows successfully persisted. These failed rows are
neither persisted nor durably spooled. They remain undurable; this does not prove
that data has already been lost.

A result with no failed rows resets the consecutive failure count. Empty batches
and batches saved entirely to the durable spool are successful for this purpose.

Exceptions from flushing or either monitoring helper also count as one failed
fire and log a traceback. Returned failures log their undurable row count without
an artificial traceback. Both failure paths share the same consecutive count.
Since exceptions can come from monitoring, the critical alert directs operators
to the preceding errors rather than asserting that every write failed.

Every failed fire emits an error. Critical alerts occur at consecutive failure
3 and every ten failures thereafter: 13, 23, and so on. Successful fires break
that sequence. System registration resets both the failure and fire counters.

This monitor observes scheduled flushes. Query barriers, persistence, durable
spool recovery, and the shutdown drain retain their own behavior. A clean
scheduled result does not certify that every persistence path is healthy.

Tests in `evennia.server.tests.test_engine_systems` cover classification, metrics,
exception handling, and escalation without a database. Scheduler integration
coverage lives in `evennia.utils.tests.test_systems.TestFlushAttributesSystem`.
