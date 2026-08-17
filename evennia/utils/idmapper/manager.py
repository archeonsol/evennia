"""
IDmapper extension to the default manager.
"""

from django.db.models import QuerySet
from django.db.models.manager import Manager

from evennia.utils import clock


class SharedMemoryOwnershipError(RuntimeError):
    """A caller attempted to mutate canonical model state off its owner."""


class SharedMemoryQuerySet(QuerySet):
    """QuerySet whose persistence operations respect runtime ownership."""

    @staticmethod
    def _require_owner(operation):
        if not clock.is_io_owner():
            raise SharedMemoryOwnershipError(
                f"SharedMemory QuerySet.{operation}() requires the Evennia IO owner"
            )

    def delete(self):
        self._require_owner("delete")
        return super().delete()

    def bulk_create(self, objs, *args, **kwargs):
        self._require_owner("bulk_create")
        return super().bulk_create(objs, *args, **kwargs)

    def bulk_update(self, objs, fields, *args, **kwargs):
        self._require_owner("bulk_update")
        return super().bulk_update(objs, fields, *args, **kwargs)


class SharedMemoryManager(Manager.from_queryset(SharedMemoryQuerySet)):
    # TODO: improve on this implementation
    # We need a way to handle reverse lookups so that this model can
    # still use the singleton cache, but the active model isn't required
    # to be a SharedMemoryModel.
    def get(self, *args, **kwargs):
        """
        Data entity lookup.
        """
        items = list(kwargs)
        inst = None
        if len(items) == 1:
            # CL: support __exact
            key = items[0]
            if key.endswith("__exact"):
                key = key[: -len("__exact")]
            if key in ("pk", self.model._meta.pk.attname):
                try:
                    inst = self.model.get_cached_instance(kwargs[items[0]])
                    # we got the item from cache, but if this is a fk, check it's ours
                    if getattr(inst, str(self.field).split(".")[-1]) != self.instance:
                        inst = None
                except Exception:
                    pass
        if inst is None:
            inst = super().get(*args, **kwargs)
        return inst
