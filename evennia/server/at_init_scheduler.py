"""
Batch ``at_init()`` calls across reactor turns so reload/startup does not block the portal.
"""

from __future__ import annotations

from django.conf import settings

from evennia.utils import logger


def _batch_size() -> int:
    return max(1, int(getattr(settings, "AT_INIT_BATCH_SIZE", 50) or 50))


def _batch_delay() -> float:
    return max(0.0, float(getattr(settings, "AT_INIT_BATCH_DELAY", 0) or 0))


def _defer_on_reload() -> bool:
    return bool(getattr(settings, "AT_INIT_DEFER_ON_RELOAD", True))


def _collect_cached_entities():
    from evennia.typeclasses.models import TypedObject

    entities = []
    for typeclass_db in TypedObject.__subclasses__():
        try:
            entities.extend(typeclass_db.get_all_cached_instances())
        except Exception:
            logger.log_trace("at_init_scheduler: failed to list cached instances")
    return entities


def run_cached_at_init_burst(mode: str) -> None:
    """
    Run ``at_init()`` on all idmapper-cached entities, optionally deferred and batched.

    Args:
        mode: ``reload``, ``reset``, or ``shutdown`` (startup uses ``shutdown`` file mode).
    """
    entities = _collect_cached_entities()
    if not entities:
        return

    batch_size = _batch_size()
    delay = _batch_delay()

    def _run_batch(start: int) -> None:
        end = min(start + batch_size, len(entities))
        for entity in entities[start:end]:
            try:
                entity.at_init()
            except Exception:
                logger.log_trace("at_init_scheduler: entity.at_init failed")
        if end < len(entities):
            from twisted.internet import reactor

            reactor.callLater(delay, _run_batch, end)

    def _start():
        _run_batch(0)

    if mode == "reload" and _defer_on_reload():
        from twisted.internet import reactor

        reactor.callLater(0, _start)
    else:
        _start()


def schedule_lazy_global_scripts(scripts) -> None:
    """
    Start non-critical global scripts on later reactor ticks (batched).
    """
    if not scripts:
        return
    batch_size = max(1, int(getattr(settings, "GLOBAL_SCRIPTS_LAZY_BATCH_SIZE", 5) or 5))
    delay = max(0.0, float(getattr(settings, "GLOBAL_SCRIPTS_LAZY_DELAY", 0) or 0))
    defer = bool(getattr(settings, "GLOBAL_SCRIPTS_DEFER_LAZY_START", True))
    if not defer:
        for script in scripts:
            try:
                script.start()
            except Exception:
                logger.log_trace("at_init_scheduler: lazy global script start failed")
        return

    def _start_batch(index: int) -> None:
        end = min(index + batch_size, len(scripts))
        for script in scripts[index:end]:
            try:
                script.start()
            except Exception:
                logger.log_trace("at_init_scheduler: lazy global script start failed")
        if end < len(scripts):
            from twisted.internet import reactor

            reactor.callLater(delay, _start_batch, end)

    from twisted.internet import reactor

    reactor.callLater(0, _start_batch, 0)
