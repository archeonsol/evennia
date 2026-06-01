"""
DEPRECATED — Redis L2 attribute cache (Phase 2, now retired).

The JSONB backend (``evennia.typeclasses.jsonb_handler.JsonbAttributeBackend``)
supersedes this module.  All attribute data lives in ``db_attrs`` on the object
row; an in-process L1 dict provides the same read-speed guarantee Redis did, with
no network hop and no serialization overhead.

Free functions below are kept as no-ops because engine call sites import them
conditionally (gated by ``ATTRIBUTE_REDIS_CACHE_ENABLED``) or inside
``try/except`` blocks.  Remove them once all call sites are cleaned up (Phase 4).

Do NOT set ``ATTRIBUTE_BACKEND_CLASS`` to ``RedisCachedModelAttributeBackend``—
it will raise ``ImproperlyConfigured`` at instantiation.
"""

from django.core.exceptions import ImproperlyConfigured

from evennia.typeclasses.attributes import ModelAttributeBackend


# ---------------------------------------------------------------------------
# No-op stubs — call sites are try/except-wrapped or gated by the disabled
# ATTRIBUTE_REDIS_CACHE_ENABLED setting.  These prevent ImportError while
# the call sites are still present.
# ---------------------------------------------------------------------------


def flush_all_keys() -> int:
    """No-op. Redis L2 has been retired."""
    return 0


def invalidate_attrs(attrs) -> None:
    """No-op. Redis L2 has been retired."""


def drop_owner_keys(model_name: str, obj_id: int) -> None:
    """No-op. Redis L2 has been retired."""


# ---------------------------------------------------------------------------
# Removed backend — raises on instantiation so misconfigured deployments
# fail loudly instead of silently writing to a dead code path.
# ---------------------------------------------------------------------------


class RedisCachedModelAttributeBackend(ModelAttributeBackend):
    """Removed. See module docstring."""

    def __init__(self, *args, **kwargs):
        raise ImproperlyConfigured(
            "RedisCachedModelAttributeBackend has been removed. "
            "Set ATTRIBUTE_BACKEND_CLASS = "
            "'evennia.typeclasses.jsonb_handler.JsonbAttributeBackend' "
            "in settings.py."
        )
