"""
Engine-side systems for the unified System Scheduler.

Recurring work whose subject is engine infrastructure registers here; this
module is always loaded by `evennia.utils.systems.load_system_modules`,
before any game module, so engine systems cannot be dropped by a game
overriding `SYSTEM_MODULES`. Game systems never live here — they register
from game modules declared in `settings.SYSTEM_MODULES`.

The first such system is `flush-attributes`: the write-behind attribute
flush, recurring work over the engine-owned L1 cache. Database persistence
runs from immutable snapshots in a worker; adoption returns to the IO owner.
Its cadence comes from
`settings.ATTRIBUTE_FLUSH_INTERVAL` (seconds; 0 disables). The query
barriers (`_flush_attr_writes` in the typeclass/object managers) are
read-your-writes correctness and stay untouched regardless — they are what
makes a missed flush run safe. The driver going down stops proactive
flushing, so the body's failure path logs loudly and escalates after
consecutive failures rather than relying solely on the pending-dirty
threshold warning; a final drain also runs in the server shutdown path.
"""

from django.conf import settings

from evennia.typeclasses.attribute_metrics import maybe_log_flush_metrics, maybe_warn_pending_dirty
from evennia.typeclasses.attributes import flush_all_dirty_async as _flush_all_dirty_async
from evennia.utils import logger, systems

# Plain module-global ints are safe because the scheduler's overlap guard
# serializes async flush fires and all counter mutation runs on the IO owner.
_consecutive_flush_failures = 0
_flush_fire_count = 0

#: Escalate at this many consecutive failures, then repeat the CRITICAL
#: line only every Nth failure so a long outage doesn't flood the log.
_CRITICAL_THRESHOLD = 3
_CRITICAL_REPEAT_EVERY = 10


async def _run_flush(ctx):
    """
    Body of the `flush-attributes` system: drain the write-behind cache.

    Returned undurable rows and exceptions share one failure streak.
    Durably spooled rows count as success. Exceptions can also originate in
    monitoring helpers, so the critical alert refers to the preceding errors.
    Past the threshold the CRITICAL line repeats only every
    `_CRITICAL_REPEAT_EVERY` failures to avoid flooding.

    Args:
        ctx (SystemContext): The per-fire context.

    """
    global _consecutive_flush_failures, _flush_fire_count
    _flush_fire_count += 1
    try:
        stats = await _flush_all_dirty_async()
        maybe_log_flush_metrics(stats, _flush_fire_count)
        maybe_warn_pending_dirty(stats, _flush_fire_count)
        if stats["failed"] <= 0:
            _consecutive_flush_failures = 0
            return
        detail = f"{stats['failed']} undurable rows"
    except Exception:
        logger.log_trace("flush-attributes system")
        detail = "exception; see traceback"
    _consecutive_flush_failures += 1
    logger.log_err(
        f"flush-attributes failed (consecutive failure #{_consecutive_flush_failures}): {detail}"
    )
    failures_past_threshold = _consecutive_flush_failures - _CRITICAL_THRESHOLD
    if failures_past_threshold >= 0 and (failures_past_threshold % _CRITICAL_REPEAT_EVERY == 0):
        logger.log_err(
            f"CRITICAL: attribute flush has failed "
            f"{_consecutive_flush_failures} consecutive fires; inspect preceding errors "
            "for persistence or monitoring failures."
        )


def register_systems():
    """
    Register engine systems. Called by `systems.load_system_modules`.

    Registers `flush-attributes` unless `ATTRIBUTE_FLUSH_INTERVAL` is 0.

    """
    global _consecutive_flush_failures, _flush_fire_count
    _consecutive_flush_failures = 0
    _flush_fire_count = 0
    interval = getattr(settings, "ATTRIBUTE_FLUSH_INTERVAL", 60) or 0
    if interval <= 0:
        logger.log_info("flush-attributes system disabled (ATTRIBUTE_FLUSH_INTERVAL is 0).")
        return
    systems.register(
        name="flush-attributes",
        cadence=systems.every(interval),
        scope=systems.global_scope(),
        run=_run_flush,
    )
