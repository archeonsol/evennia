"""
Django ID mapper

Modified for Evennia by making sure that no model references
leave caching unexpectedly (no use of WeakRefs).

Also adds `cache_size()` for monitoring the size of the cache.
"""

import gc
import os
import threading
import time
from collections import defaultdict
from itertools import islice
from weakref import WeakValueDictionary

from django.conf import settings
from django.core.exceptions import FieldDoesNotExist, FieldError, ObjectDoesNotExist
from django.db import connections, router, transaction
from django.db.models.base import Model, ModelBase
from django.db.models.signals import post_migrate, post_save, pre_delete
from django.db.utils import DatabaseError
from django.utils.module_loading import import_string
from twisted.internet.reactor import callFromThread

from evennia.utils import clock, logger
from evennia.utils.utils import dbref, get_evennia_pids, to_str

from .manager import SharedMemoryManager

AUTO_FLUSH_MIN_INTERVAL = 60.0 * 5  # at least 5 mins between cache flushes

_GA = object.__getattribute__
_SA = object.__setattr__
_DA = object.__delattr__
_MONITOR_HANDLER = None

# References to db-updated objects are stored here so the
# main process can be informed to re-cache itself.
PROC_MODIFIED_COUNT = 0
PROC_MODIFIED_OBJS = WeakValueDictionary()

# get info about the current process and thread; determine if our
# current pid is different from the server PID (i.e.  # if we are in a
# subprocess or not)
_SELF_PID = os.getpid()
_SERVER_PID, _PORTAL_PID = get_evennia_pids()
_IS_SUBPROCESS = (_SERVER_PID and _PORTAL_PID) and _SELF_PID not in (_SERVER_PID, _PORTAL_PID)
_IS_MAIN_THREAD = threading.current_thread().name == "MainThread"

_JSONB_ATTRIBUTE_BACKEND = "evennia.typeclasses.jsonb_handler.JsonbAttributeBackend"


def _jsonb_attribute_backend_active():
    """Return whether model ``db_attrs`` fields are coordinator-owned."""
    backend = getattr(settings, "ATTRIBUTE_BACKEND_CLASS", _JSONB_ATTRIBUTE_BACKEND)
    if isinstance(backend, str):
        backend = import_string(backend)
    from evennia.typeclasses.jsonb_handler import JsonbAttributeBackend

    return isinstance(backend, type) and issubclass(backend, JsonbAttributeBackend)


def _coordinator_safe_save_kwargs(instance, kwargs):
    """Exclude coordinator-owned ``db_attrs`` from an existing-row model save."""
    save_kwargs = dict(kwargs)
    if instance._state.adding or not _jsonb_attribute_backend_active():
        return save_kwargs
    try:
        instance._meta.get_field("db_attrs")
    except FieldDoesNotExist:
        return save_kwargs
    requested_alias = save_kwargs.get("using")
    if requested_alias is not None and instance._state.db not in (None, requested_alias):
        from evennia.typeclasses.jsonb_handler import AttributeUpdateUsageError

        raise AttributeUpdateUsageError(
            "JSONB-owned model saves cannot override the instance database alias."
        )
    requested = save_kwargs.get("update_fields")
    if requested is not None:
        if "db_attrs" in requested:
            from evennia.typeclasses.jsonb_handler import AttributeUpdateUsageError

            raise AttributeUpdateUsageError(
                "db_attrs is owned by the JSONB Attribute coordinator; use Attribute APIs."
            )
        return save_kwargs
    deferred = instance.get_deferred_fields()
    save_kwargs["update_fields"] = tuple(
        field.name
        for field in instance._meta.concrete_fields
        if not field.primary_key
        and field.name != "db_attrs"
        and field.name not in deferred
        and field.attname not in deferred
    )
    return save_kwargs


def _preflight_model_delete(instance, using=None, positional=()):
    """Validate JSONB row deletion before public lifecycle side effects."""
    if instance.pk is None or not _jsonb_attribute_backend_active():
        return
    try:
        instance._meta.get_field("db_attrs")
    except FieldDoesNotExist:
        return
    if positional:
        from evennia.typeclasses.jsonb_handler import AttributeUpdateUsageError

        raise AttributeUpdateUsageError(
            "JSONB-owned model deletion requires keyword persistence arguments."
        )
    from evennia.typeclasses.jsonb_handler import _preflight_row_delete

    _preflight_row_delete(instance, using=using)


class SharedMemoryModelBase(ModelBase):
    # CL: upstream had a __new__ method that skipped ModelBase's __new__ if
    # SharedMemoryModelBase was not in the model class's ancestors. It's not
    # clear what was the intended purpose, but skipping ModelBase.__new__
    # broke things; in particular, default manager inheritance.

    def __call__(cls, *args, **kwargs):
        """
        this method will either create an instance (by calling the default implementation)
        or try to retrieve one from the class-wide cache by inferring the pk value from
        `args` and `kwargs`. If instance caching is enabled for this class, the cache is
        populated whenever possible (ie when it is possible to infer the pk value).

        """

        def new_instance():
            return super(SharedMemoryModelBase, cls).__call__(*args, **kwargs)

        instance_key = cls._get_cache_key(args, kwargs)
        # depending on the arguments, we might not be able to infer the PK, so in that case we
        # create a new instance
        if instance_key is None:
            return new_instance()
        cached_instance = cls.get_cached_instance(instance_key)
        if cached_instance is None:
            cached_instance = new_instance()
            cls.cache_instance(cached_instance, new=True)
        return cached_instance

    def _prepare(cls):
        """
        Prepare the cache, making sure that proxies of the same db base
        share the same cache.

        """
        # the dbmodel is either the proxy base or ourselves
        dbmodel = cls._meta.concrete_model if cls._meta.proxy else cls
        cls.__dbclass__ = dbmodel
        if not hasattr(dbmodel, "__instance_cache__"):
            # we store __instance_cache__ only on the dbmodel base
            dbmodel.__instance_cache__ = {}
        super()._prepare()

    def __new__(cls, name, bases, attrs):
        """
        Field shortcut creation:

        Takes field names `db_*` and creates property wrappers named
        without the `db_` prefix. So db_key -> key

        This wrapper happens on the class level, so there is no
        overhead when creating objects.  If a class already has a
        wrapper of the given name, the automatic creation is skipped.

        Notes:
            Remember to document this auto-wrapping in the class
            header, this could seem very much like magic to the user
            otherwise.
        """

        attrs["typename"] = cls.__name__
        attrs["path"] = "%s.%s" % (attrs["__module__"], name)
        attrs["_is_deleted"] = False

        # set up the typeclass handling only if a variable _is_typeclass is set on the class
        def create_wrapper(cls, fieldname, wrappername, editable=True, foreignkey=False):
            "Helper method to create property wrappers with unique names (must be in separate call)"

            def _get(cls, fname):
                "Wrapper for getting database field"
                if _GA(cls, "_is_deleted"):
                    raise ObjectDoesNotExist(
                        "Cannot access %s: Hosting object was already deleted." % fname
                    )
                return _GA(cls, fieldname)

            def _get_foreign(cls, fname):
                "Wrapper for returning foreignkey fields"
                if _GA(cls, "_is_deleted"):
                    raise ObjectDoesNotExist(
                        "Cannot access %s: Hosting object was already deleted." % fname
                    )
                return _GA(cls, fieldname)

            def _set_nonedit(cls, fname, value):
                "Wrapper for blocking editing of field"
                raise FieldError("Field %s cannot be edited." % fname)

            def _set(cls, fname, value):
                "Wrapper for setting database field"
                if _GA(cls, "_is_deleted"):
                    raise ObjectDoesNotExist(
                        "Cannot set %s to %s: Hosting object was already deleted!" % (fname, value)
                    )
                _SA(cls, fname, value)
                # only use explicit update_fields in save if we actually have a
                # primary key assigned already (won't be set when first creating object)
                update_fields = (
                    [fname] if _GA(cls, "_get_pk_val")(_GA(cls, "_meta")) is not None else None
                )
                _GA(cls, "save")(update_fields=update_fields)

            def _set_foreign(cls, fname, value):
                "Setter only used on foreign key relations, allows setting with #dbref"
                if _GA(cls, "_is_deleted"):
                    raise ObjectDoesNotExist(
                        "Cannot set %s to %s: Hosting object was already deleted!" % (fname, value)
                    )
                if isinstance(value, (str, int)):
                    value = to_str(value)
                    if value.isdigit() or value.startswith("#"):
                        # we also allow setting using dbrefs, if so we try to load the matching
                        # object. (we assume the object is of the same type as the class holding
                        # the field, if not a custom handler must be used for that field)
                        dbid = dbref(value, reqhash=False)
                        if dbid:
                            model = _GA(cls, "_meta").get_field(fname).model
                            try:
                                value = model._default_manager.get(id=dbid)
                            except ObjectDoesNotExist:
                                # maybe it is just a name that happens to look like a dbid
                                pass
                _SA(cls, fname, value)
                # only use explicit update_fields in save if we actually have a
                # primary key assigned already (won't be set when first creating object)
                update_fields = (
                    [fname] if _GA(cls, "_get_pk_val")(_GA(cls, "_meta")) is not None else None
                )
                _GA(cls, "save")(update_fields=update_fields)

            def _del_nonedit(cls, fname):
                "wrapper for not allowing deletion"
                raise FieldError("Field %s cannot be edited." % fname)

            def _del(cls, fname):
                "Wrapper for clearing database field - sets it to None"
                _SA(cls, fname, None)
                update_fields = (
                    [fname] if _GA(cls, "_get_pk_val")(_GA(cls, "_meta")) is not None else None
                )
                _GA(cls, "save")(update_fields=update_fields)

            # wrapper factories
            if not editable:

                def fget(cls):
                    return _get(cls, fieldname)

                def fset(cls, val):
                    return _set_nonedit(cls, fieldname, val)

            elif foreignkey:

                def fget(cls):
                    return _get_foreign(cls, fieldname)

                def fset(cls, val):
                    return _set_foreign(cls, fieldname, val)

            else:

                def fget(cls):
                    return _get(cls, fieldname)

                def fset(cls, val):
                    return _set(cls, fieldname, val)

            def fdel(cls):
                return _del(cls, fieldname) if editable else _del_nonedit(cls, fieldname)

            # set docstrings for auto-doc
            fget.__doc__ = "A wrapper for getting database field `%s`." % fieldname
            fset.__doc__ = "A wrapper for setting (and saving) database field `%s`." % fieldname
            fdel.__doc__ = "A wrapper for deleting database field `%s`." % fieldname
            # assigning
            attrs[wrappername] = property(fget, fset, fdel)
            # type(cls).__setattr__(cls, wrappername, property(fget, fset, fdel))#, doc))

        # exclude some models that should not auto-create wrapper fields
        if cls.__name__ in ("ServerConfig", "TypeNick"):
            return
        # dynamically create the wrapper properties for all fields not already handled
        # (manytomanyfields are always handlers)
        for fieldname, field in (
            (fname, field)
            for fname, field in list(attrs.items())
            if fname.startswith("db_") and type(field).__name__ != "ManyToManyField"
        ):
            foreignkey = type(field).__name__ == "ForeignKey"
            wrappername = "dbid" if fieldname == "id" else fieldname.replace("db_", "", 1)
            if wrappername not in attrs:
                # makes sure not to overload manually created wrappers on the model
                create_wrapper(
                    cls, fieldname, wrappername, editable=field.editable, foreignkey=foreignkey
                )

        return super().__new__(cls, name, bases, attrs)


class SharedMemoryModel(Model, metaclass=SharedMemoryModelBase):
    """
    Base class for idmapped objects. Inherit from `this`.
    """

    objects = SharedMemoryManager()

    class Meta(object):
        abstract = True

    @classmethod
    def _get_cache_key(cls, args, kwargs):
        """
        This method is used by the caching subsystem to infer the PK
        value from the constructor arguments.  It is used to decide if
        an instance has to be built or is already in the cache.

        """
        result = None
        # Quick hack for my composites work for now.
        if hasattr(cls._meta, "pks"):
            pk = cls._meta.pks[0]
        else:
            pk = cls._meta.pk
        # Cache pk_position per class to avoid repeated list.index() on every instantiation
        try:
            pk_position = cls.__dbclass__._pk_position_cache
        except AttributeError:
            pk_position = cls._meta.fields.index(pk)
            cls.__dbclass__._pk_position_cache = pk_position
        if len(args) > pk_position:
            # if it's in the args, we can get it easily by index
            result = args[pk_position]
        elif pk.attname in kwargs:
            # retrieve the pk value. Note that we use attname instead of name, to handle the case
            # where the pk is a a ForeignKey.
            result = kwargs[pk.attname]
        elif pk.name != pk.attname and pk.name in kwargs:
            # ok we couldn't find the value, but maybe it's a FK and we can find the corresponding
            # object instead
            result = kwargs[pk.name]

        if result is not None and isinstance(result, Model):
            # if the pk value happens to be a model instance (which can happen wich a FK), we'd
            # rather use its own pk as the key
            result = result._get_pk_val()
        return result

    @classmethod
    def get_cached_instance(cls, id):
        """
        Method to retrieve a cached instance by pk value. Returns None
        when not found (which will always be the case when caching is
        disabled for this class). Please note that the lookup will be
        done even when instance caching is disabled.

        """
        return cls.__dbclass__.__instance_cache__.get(id)

    @classmethod
    def cache_instance(cls, instance, new=False):
        """
        Method to store an instance in the cache.

        Args:
            instance (Class instance): the instance to cache.
            new (bool, optional): this is the first time this instance is
                cached (i.e. this is not an update operation like after a
                db save).

        """
        pk = instance._get_pk_val()
        if pk is not None:
            new = new or pk not in cls.__dbclass__.__instance_cache__
            cls.__dbclass__.__instance_cache__[pk] = instance
            if new:
                try:
                    # trigger the at_post_load hook only
                    # at first initialization
                    instance.at_post_load()
                except AttributeError:
                    # The at_post_load hook is not assigned to all entities
                    pass

    @classmethod
    def get_all_cached_instances(cls):
        """
        Return the objects so far cached by idmapper for this class.

        """
        return list(cls.__dbclass__.__instance_cache__.values())

    @classmethod
    def _flush_cached_by_key(cls, key, force=True):
        """
        Remove the cached reference.

        """
        try:
            if force or cls.at_idmapper_flush():
                del cls.__dbclass__.__instance_cache__[key]
            else:
                cls._dbclass__.__instance_cache__[key].refresh_from_db()
        except KeyError:
            # No need to remove if cache doesn't contain it already
            pass

    @classmethod
    def flush_cached_instance(cls, instance, force=True):
        """
        Method to flush an instance from the cache. The instance will
        always be flushed from the cache, since this is most likely
        called from delete(), and we want to make sure we don't cache
        dead objects.

        """
        cls._flush_cached_by_key(instance._get_pk_val(), force=force)

    # flush_cached_instance = classmethod(flush_cached_instance)

    @classmethod
    def flush_instance_cache(cls, force=False):
        """
        This will clean safe objects from the cache. Use `force`
        keyword to remove all objects, safe or not.

        """
        _cancel_active_cache_flush()
        if force:
            cls.__dbclass__.__instance_cache__ = {}
        else:
            sweep = _CacheFlushSweep(
                models=[cls.__dbclass__],
                batch_size=None,
                budget_ms=None,
                automatic=False,
            )
            sweep.run_to_completion()

    # flush_instance_cache = classmethod(flush_instance_cache)

    # per-instance methods

    def __eq__(self, other):
        return super().__eq__(other)

    def __hash__(self):
        # this is required to maintain hashing
        return super().__hash__()

    def at_idmapper_flush(self):
        """
        This is called when the idmapper cache is flushed and
        allows customized actions when this happens.

        Returns:
            do_flush (bool): If True, flush this object as normal. If
                False, don't flush and expect this object to handle
                the flushing on its own.
        """
        return True

    def flush_from_cache(self, force=False):
        """
        Flush this instance from the instance cache. Use
        `force` to override the result of at_idmapper_flush() for the object.

        """
        pk = self._get_pk_val()
        if pk:
            if force or self.at_idmapper_flush():
                self.__class__.__dbclass__.__instance_cache__.pop(pk, None)

    def delete(self, *args, **kwargs):
        """
        Delete the object, clearing cache.

        """
        from evennia.typeclasses.jsonb_handler import (
            _coordinate_row_delete,
            _retire_row_state_after_delete,
        )

        try:
            self._meta.get_field("db_attrs")
        except FieldDoesNotExist:
            jsonb_owned = False
        else:
            jsonb_owned = _jsonb_attribute_backend_active()
        if jsonb_owned:
            if args:
                from evennia.typeclasses.jsonb_handler import AttributeUpdateUsageError

                raise AttributeUpdateUsageError(
                    "JSONB-owned model deletion requires keyword persistence arguments."
                )
            with _coordinate_row_delete(self, using=kwargs.get("using")) as jsonb_row_state:
                self.flush_from_cache(force=True)
                super().delete(*args, **kwargs)
                self._is_deleted = True
                _retire_row_state_after_delete(jsonb_row_state)
            return
        self.flush_from_cache(force=True)
        super().delete(*args, **kwargs)
        self._is_deleted = True

    def save(self, *args, **kwargs):
        """
        Central database save operation.

        Notes:
            Arguments as per Django documentation.
            Calls `self.at_<fieldname>_postsave(new)`
            (this is a wrapper set by oobhandler:
            self._oob_at_<fieldname>_postsave())

        """
        global _MONITOR_HANDLER
        from evennia.typeclasses.attribute_context import model_save_context
        from evennia.typeclasses.jsonb_handler import (
            AttributeUpdateUsageError,
            _has_user_transaction,
            protected_mutation_active,
        )

        if protected_mutation_active():
            raise AttributeUpdateUsageError(
                "Model saves may not run inside a blocking_update callback."
            )

        if args and not self._state.adding and _jsonb_attribute_backend_active():
            try:
                self._meta.get_field("db_attrs")
            except FieldDoesNotExist:
                pass
            else:
                raise AttributeUpdateUsageError(
                    "JSONB-owned model saves require keyword persistence arguments."
                )

        save_kwargs = _coordinator_safe_save_kwargs(self, kwargs)

        def operation_alias(call_kwargs):
            """Resolve the database owning this save and its outcome callbacks."""
            return (
                call_kwargs.get("using")
                or self._state.db
                or router.db_for_write(self._meta.concrete_model, instance=self)
            )

        def finish_model_save(scope, call_kwargs):
            """Bind hook-local Attribute intent to the real transaction outcome."""
            alias = operation_alias(call_kwargs)
            if _has_user_transaction(connections[alias]):
                transaction.on_commit(scope.commit, using=alias)
            else:
                scope.commit()

        if not _MONITOR_HANDLER:
            from evennia.scripts.monitorhandler import MONITOR_HANDLER as _MONITOR_HANDLER

        if _IS_SUBPROCESS:
            # we keep a store of objects modified in subprocesses so
            # we know to update their caches in the central process
            global PROC_MODIFIED_COUNT, PROC_MODIFIED_OBJS
            PROC_MODIFIED_COUNT += 1
            PROC_MODIFIED_OBJS[PROC_MODIFIED_COUNT] = self

        if _IS_MAIN_THREAD:
            # in main thread - normal operation
            try:
                with model_save_context() as scope:
                    with transaction.atomic(using=operation_alias(save_kwargs)):
                        super().save(*args, **save_kwargs)
                finish_model_save(scope, save_kwargs)
            except DatabaseError:
                # we handle the 'update_fields did not update any rows' error that
                # may happen due to timing issues with attributes
                retry_kwargs = dict(kwargs)
                ufields_removed = retry_kwargs.pop("update_fields", None)
                if ufields_removed:
                    retry_kwargs = _coordinator_safe_save_kwargs(self, retry_kwargs)
                    with model_save_context() as scope:
                        with transaction.atomic(using=operation_alias(retry_kwargs)):
                            super().save(*args, **retry_kwargs)
                    finish_model_save(scope, retry_kwargs)
                else:
                    raise
        else:
            # in another thread; make sure to save in reactor thread
            def _save_callback(cls, *args, **kwargs):
                with model_save_context() as scope:
                    with transaction.atomic(using=operation_alias(kwargs)):
                        super(SharedMemoryModel, cls).save(*args, **kwargs)
                finish_model_save(scope, kwargs)

            callFromThread(_save_callback, self, *args, **save_kwargs)

        if not self.pk:
            # this can happen if some of the startup methods immediately
            # delete the object (an example are Scripts that start and die immediately)
            return

        # update field-update hooks and eventual OOB watchers
        new = False
        if "update_fields" in kwargs and kwargs["update_fields"]:
            # get field objects from their names
            update_fields = (
                self._meta.get_field(fieldname) for fieldname in kwargs.get("update_fields")
            )
        else:
            # meta.fields are already field objects; get them all
            new = True
            update_fields = self._meta.fields
        with model_save_context() as hook_scope:
            for field in update_fields:
                fieldname = field.name
                # trigger eventual monitors
                _MONITOR_HANDLER.at_update(self, fieldname)
                # if a hook is defined it must be named exactly on this form
                hookname = "at_%s_postsave" % fieldname
                if hasattr(self, hookname) and callable(_GA(self, hookname)):
                    _GA(self, hookname)(new)
        finish_model_save(hook_scope, save_kwargs)

        #            # if a trackerhandler is set on this object, update it with the
        #            # fieldname and the new value
        #            fieldtracker = "_oob_at_%s_postsave" % fieldname
        #            if hasattr(self, fieldtracker):
        #                _GA(self, fieldtracker)(fieldname)
        pass


class WeakSharedMemoryModelBase(SharedMemoryModelBase):
    """
    Uses a WeakValue dictionary for caching instead of a regular one.

    """

    def _prepare(cls):
        super()._prepare()
        cls.__dbclass__.__instance_cache__ = WeakValueDictionary()


class WeakSharedMemoryModel(SharedMemoryModel, metaclass=WeakSharedMemoryModelBase):
    """
    Uses a WeakValue dictionary for caching instead of a regular one

    """

    class Meta(object):
        abstract = True


_ACTIVE_FLUSH_SWEEP = None
_IDMAPPER_SWEEP_EPOCH_ATTR = "_idmapper_flush_epoch"


def _leaf_cache_models():
    """Return the existing leaf-model traversal used by global cache flushes."""

    def walk(classes):
        for cls in classes:
            subclasses = cls.__subclasses__()
            if subclasses:
                yield from walk(subclasses)
            else:
                yield cls.__dbclass__

    return list(walk([SharedMemoryModel]))


def _row_check_groups(entries):
    """Reduce retained-object row checks to worker-safe query specifications."""
    grouped = defaultdict(list)
    for _cache, key, obj in entries:
        required = getattr(obj, "_idmapper_row_check_required", None)
        if required is None or not required():
            continue
        alias = getattr(getattr(obj, "_state", None), "db", None) or router.db_for_read(
            obj._meta.concrete_model, instance=obj
        )
        grouped[(obj._meta.concrete_model, alias)].append((key, id(obj)))
    return dict(grouped)


def _query_missing_row_ids(grouped):
    """Execute bounded backing-row queries and return missing object identities."""
    missing = set()
    query_count = 0
    for (model, alias), candidates in grouped.items():
        backend_limit = connections[alias].features.max_query_params
        chunk_size = max(1, int(backend_limit or 1000))
        for offset in range(0, len(candidates), chunk_size):
            chunk = candidates[offset : offset + chunk_size]
            primary_keys = [key for key, _obj_id in chunk]
            existing = set(
                model._base_manager.using(alias)
                .filter(pk__in=primary_keys)
                .values_list("pk", flat=True)
            )
            query_count += 1
            missing.update(obj_id for key, obj_id in chunk if key not in existing)
    return missing, query_count


def _missing_row_entry_ids(entries):
    """Find retained-object rows missing from storage using bounded bulk queries.

    Args:
        entries (list[tuple]): ``(cache, primary_key, object)`` entries from one
            bounded sweep turn.

    Returns:
        tuple[set[int], int]: Object identities whose backing row is absent and
        the number of bulk queries issued.

    Raises:
        DatabaseError: If existence cannot be established. Callers must retain
            all unknown objects rather than treating the failure as deletion.

    """
    return _query_missing_row_ids(_row_check_groups(entries))


class _CacheFlushSweep:
    """Incrementally apply idmapper eviction hooks without one unbounded turn."""

    def __init__(self, *, models, batch_size, budget_ms, automatic):
        """Initialize one cache-sweep epoch.

        Args:
            models (iterable[type]): Concrete idmapper cache holders.
            batch_size (int or None): Maximum captured entries per turn. ``None``
                processes the current model synchronously.
            budget_ms (float or None): Monotonic time budget per turn.
            automatic (bool): Whether continuations may be scheduled.

        """
        self.models = list(models)
        self.batch_size = max(1, int(batch_size)) if batch_size is not None else None
        self.budget_seconds = max(0.0, float(budget_ms)) / 1000.0 if budget_ms is not None else None
        self.automatic = automatic
        self.model_index = 0
        self.remaining = None
        self.epoch = object()
        self.cancelled = False
        self.handle = None
        self.query_future = None
        self.started_at = time.monotonic()
        self.stats = {
            "batches": 0,
            "objects": 0,
            "retained": 0,
            "evicted": 0,
            "stale": 0,
            "row_queries": 0,
        }

    def _current_cache(self):
        """Return the current cache, advancing completed model epochs."""
        while self.model_index < len(self.models):
            model = self.models[self.model_index]
            cache = model.__instance_cache__
            if self.remaining is None:
                self.remaining = len(cache)
            if self.remaining > 0:
                return cache
            self.model_index += 1
            self.remaining = None
        return None

    def _capture_entries(self, cache):
        """Capture only the bounded prefix eligible in this turn."""
        limit = self.remaining
        if self.batch_size is not None:
            limit = min(limit, self.batch_size)
        return [(cache, key, obj) for key, obj in islice(cache.items(), limit)]

    def _next_entries(self):
        """Return the next captured entry batch, or ``None`` for cleanup/done."""
        if self.cancelled:
            return None
        cache = self._current_cache()
        if cache is None:
            return None
        entries = self._capture_entries(cache)
        if not entries:
            self.remaining = 0
            return None
        return entries

    def _apply_entries(self, entries, missing_ids, query_count):
        """Apply one queried entry batch on the owning IO thread."""
        turn_started = time.monotonic()
        self.stats["row_queries"] += query_count
        processed = 0
        self.stats["batches"] += 1
        for entry_cache, key, obj in entries:
            if obj.__dict__.get(_IDMAPPER_SWEEP_EPOCH_ATTR) is self.epoch:
                self.remaining = 0
                break
            if (
                processed
                and self.budget_seconds is not None
                and time.monotonic() - turn_started >= self.budget_seconds
            ):
                break
            obj.__dict__[_IDMAPPER_SWEEP_EPOCH_ATTR] = self.epoch
            processed += 1
            self.remaining -= 1
            self.stats["objects"] += 1
            if entry_cache.get(key) is not obj:
                self.stats["stale"] += 1
                continue
            if id(obj) in missing_ids:
                obj._idmapper_mark_row_missing()
                should_evict = True
            else:
                should_evict = obj.at_idmapper_flush()
            if entry_cache.get(key) is not obj:
                self.stats["stale"] += 1
                continue
            entry_cache.pop(key, None)
            if should_evict:
                self.stats["evicted"] += 1
            else:
                entry_cache[key] = obj
                self.stats["retained"] += 1

        return self._current_cache() is not None

    def run_turn(self):
        """Process one synchronous bounded turn and return whether work remains."""
        if self.cancelled:
            return False
        entries = self._next_entries()
        if entries is None:
            return self._current_cache() is not None
        missing_ids, query_count = _missing_row_entry_ids(entries)
        return self._apply_entries(entries, missing_ids, query_count)

    def run_to_completion(self):
        """Run every remaining turn synchronously."""
        while self.run_turn():
            pass

    def schedule(self):
        """Schedule the next turn through the engine's isolated callback scope."""
        if self.cancelled:
            return
        self.handle = clock.call_later(0, self._run_scheduled_turn)

    def _run_scheduled_turn(self):
        """Start one automatic turn without blocking the reactor on row I/O."""
        global _ACTIVE_FLUSH_SWEEP
        if self.cancelled or _ACTIVE_FLUSH_SWEEP is not self:
            return
        self.handle = None
        try:
            entries = self._next_entries()
            if entries is None:
                has_work = self._current_cache() is not None
                if has_work:
                    self.schedule()
                else:
                    self._finish()
                return
            grouped = _row_check_groups(entries)
            if not grouped:
                self._continue_after_entries(entries, set(), 0)
                return
            from evennia.utils import defer

            self.query_future = defer.in_thread(_query_missing_row_ids, grouped)
            self.query_future.add_done_callback(
                lambda future: self._schedule_query_result(entries, future)
            )
        except Exception:
            logger.log_trace("idmapper: incremental cache flush failed")
            self._finish(failed=True)

    def _schedule_query_result(self, entries, future):
        """Schedule worker-query completion through an isolated reactor callback."""
        if self.cancelled or _ACTIVE_FLUSH_SWEEP is not self:
            return
        try:
            self.handle = clock.call_later(0, self._resume_query_result, entries, future)
        except Exception:
            logger.log_trace("idmapper: could not schedule row-query completion")
            self._finish(failed=True)

    def _resume_query_result(self, entries, future):
        """Apply primitive worker results after epoch and identity revalidation."""
        if self.cancelled or _ACTIVE_FLUSH_SWEEP is not self:
            return
        self.handle = None
        self.query_future = None
        try:
            missing_ids, query_count = future.result()
            self._continue_after_entries(entries, missing_ids, query_count)
        except Exception:
            logger.log_trace("idmapper: backing-row query failed")
            self._finish(failed=True)

    def _continue_after_entries(self, entries, missing_ids, query_count):
        """Apply a completed batch and arrange the next bounded turn."""
        if self._apply_entries(entries, missing_ids, query_count):
            self.schedule()
        else:
            self._finish()

    def _finish(self, *, failed=False):
        """Record bounded-sweep metrics and release the active epoch."""
        global _ACTIVE_FLUSH_SWEEP
        try:
            duration = max(0.0, time.monotonic() - self.started_at)
            try:
                from evennia.server.prometheus_metrics import record_idmapper_flush

                record_idmapper_flush(self.stats, duration_seconds=duration, failed=failed)
            except Exception:
                logger.log_trace("idmapper: could not record cache-flush metrics")
        finally:
            self.handle = None
            self.query_future = None
            if _ACTIVE_FLUSH_SWEEP is self:
                _ACTIVE_FLUSH_SWEEP = None

    def cancel(self):
        """Invalidate this epoch and cancel its queued continuation."""
        global _ACTIVE_FLUSH_SWEEP
        self.cancelled = True
        handle, self.handle = self.handle, None
        if handle is not None:
            handle.cancel()
        query_future, self.query_future = self.query_future, None
        if query_future is not None:
            query_future.cancel()
        if _ACTIVE_FLUSH_SWEEP is self:
            _ACTIVE_FLUSH_SWEEP = None


def _cancel_active_cache_flush():
    """Cancel any queued automatic idmapper epoch."""
    if _ACTIVE_FLUSH_SWEEP is not None:
        _ACTIVE_FLUSH_SWEEP.cancel()


def _start_incremental_cache_flush():
    """Start one automatic pressure sweep, returning whether it was accepted."""
    global _ACTIVE_FLUSH_SWEEP
    if _ACTIVE_FLUSH_SWEEP is not None:
        return False
    sweep = _CacheFlushSweep(
        models=_leaf_cache_models(),
        batch_size=getattr(settings, "IDMAPPER_FLUSH_BATCH_SIZE", 100),
        budget_ms=getattr(settings, "IDMAPPER_FLUSH_BATCH_BUDGET_MS", 10.0),
        automatic=True,
    )
    _ACTIVE_FLUSH_SWEEP = sweep
    try:
        sweep.schedule()
    except Exception:
        _ACTIVE_FLUSH_SWEEP = None
        raise
    return True


clock.register_shutdown_hook(_cancel_active_cache_flush)


def _finalize_cache_flush():
    """Run legacy synchronous post-flush cleanup and return GC's count."""
    try:
        from evennia.typeclasses.redis_attr_cache import flush_all_keys
    except ImportError:
        flush_all_keys = None
    if flush_all_keys is not None:
        try:
            flush_all_keys()
        except Exception:
            logger.log_trace("idmapper: redis attr-cache flush failed")
    return gc.collect()


def flush_cache(**kwargs):
    """
    Flush idmapper cache. When doing so the cache will fire the
    at_idmapper_flush hook to allow the object to optionally handle
    its own flushing.

    Uses a signal so we make sure to catch cascades.

    """

    _cancel_active_cache_flush()
    sweep = _CacheFlushSweep(
        models=_leaf_cache_models(),
        batch_size=None,
        budget_ms=None,
        automatic=False,
    )
    sweep.run_to_completion()
    return _finalize_cache_flush()


# request_finished.connect(flush_cache)
post_migrate.connect(flush_cache)


def flush_cached_instance(sender, instance, **kwargs):
    """
    Flush the idmapper cache only for a given instance.

    """
    # XXX: Is this the best way to make sure we can flush?
    if not hasattr(instance, "flush_cached_instance"):
        return
    sender.flush_cached_instance(instance, force=True)


pre_delete.connect(flush_cached_instance)


def update_cached_instance(sender, instance, **kwargs):
    """
    Re-cache the given instance in the idmapper cache.

    """
    if not hasattr(instance, "cache_instance"):
        return
    if kwargs.get("created") and _jsonb_attribute_backend_active():
        try:
            instance._meta.get_field("db_attrs")
        except FieldDoesNotExist:
            pass
        else:
            from evennia.typeclasses.jsonb_handler import _retire_recreated_row_tombstone

            _retire_recreated_row_tombstone(instance)
    sender.cache_instance(instance)


post_save.connect(update_cached_instance)


LAST_FLUSH = None


def conditional_flush(max_rmem, force=False):
    """
    Flush the cache if the estimated memory usage exceeds `max_rmem`.

    The flusher has a timeout to avoid flushing over and over
    in particular situations (this means that for some setups
    the memory usage will exceed the requirement and a server with
    more memory is probably required for the given game).

    Args:
        max_rmem (int): memory-usage estimation-treshold after which
            cache is flushed.
        force (bool, optional): forces a flush, regardless of timeout.
            Defaults to `False`.

    """
    global LAST_FLUSH

    def mem2cachesize(desired_rmem):
        """
        Estimate the size of the idmapper cache based on the memory
        desired. This is used to optionally cap the cache size.

        desired_rmem - memory in MB (minimum 50MB)

        The formula is empirically estimated from usage tests (Linux)
        and is
            Ncache = RMEM - 35.0 / 0.0157
        where RMEM is given in MB and Ncache is the size of the cache
        for this memory usage. VMEM tends to be about 100MB higher
        than RMEM for large memory usage.
        """
        vmem = max(desired_rmem, 50.0)
        Ncache = int(abs(float(vmem) - 35.0) / 0.0157)
        return Ncache

    if not max_rmem:
        # auto-flush is disabled
        return

    now = time.time()
    if not LAST_FLUSH:
        # server is just starting
        LAST_FLUSH = now
        return

    if ((now - LAST_FLUSH) < AUTO_FLUSH_MIN_INTERVAL) and not force:
        # too soon after last flush.
        logger.log_warn(
            "Warning: Idmapper flush called more than once in %s min interval. Check memory usage."
            % (AUTO_FLUSH_MIN_INTERVAL / 60.0)
        )
        return

    # check actual memory usage
    Ncache_max = mem2cachesize(max_rmem)
    Ncache, _ = cache_size()
    try:
        if os.name == "nt":
            # Windows: use psutil if available, else skip the RSS check.
            import psutil

            actual_rmem = psutil.Process(os.getpid()).memory_info().rss / (1024.0 * 1024.0)
        else:
            import resource
            import sys

            rusage = resource.getrusage(resource.RUSAGE_SELF)
            if sys.platform == "darwin":
                # macOS: ru_maxrss is in bytes
                actual_rmem = rusage.ru_maxrss / (1024.0 * 1024.0)
            else:
                # Linux/other Unix: ru_maxrss is in kilobytes
                actual_rmem = rusage.ru_maxrss / 1024.0
    except Exception:
        # If memory info is unavailable, fall back to cache-count heuristic only.
        actual_rmem = max_rmem  # assume at limit so cache-count alone decides

    if Ncache >= Ncache_max and actual_rmem > max_rmem * 0.9:
        # flush cache when number of objects in cache is big enough and our
        # actual memory use is within 10% of our set max
        if clock.loop_running():
            if _start_incremental_cache_flush():
                LAST_FLUSH = now
        else:
            flush_cache()
            LAST_FLUSH = now


def cache_size(mb=True):
    """
    Calculate statistics about the cache.

    Note: we cannot get reliable memory statistics from the cache -
    whereas we could do `getsizof` each object in cache, the result is
    highly imprecise and for a large number of objects the result is
    many times larger than the actual memory usage of the entire server;
    Python is clearly reusing memory behind the scenes that we cannot
    catch in an easy way here.  Ideas are appreciated. /Griatch

    Returns:
      total_num, {objclass:total_num, ...}

    """
    numtotal = [0]  # use mutable to keep reference through recursion
    classdict = {}

    def get_recurse(submodels):
        for submodel in submodels:
            subclasses = submodel.__subclasses__()
            if not subclasses:
                num = len(submodel.__dbclass__.__instance_cache__)
                numtotal[0] += num
                classdict[submodel.__dbclass__.__name__] = num
            else:
                get_recurse(subclasses)

    get_recurse(SharedMemoryModel.__subclasses__())
    return numtotal[0], classdict
