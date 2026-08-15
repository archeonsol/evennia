"""
JSONB attribute backend and public force-flush API.

Stores all of an object's attributes in a single ``db_attrs JSONField`` on
the object's own model row. Reads serve a process-local, row-owned document;
writes update that document so ``flush_all_dirty()`` (run by the
flush-attributes system on the system scheduler, plus once at shutdown)
persists it in one serialized ``UPDATE`` per dirty object.

Document layout
---------------
Top-level keys:

``"~"`` (``_NULL_CATEGORY``)
    Attributes with ``category=None``.

``"<category>"``
    Attributes in a named category.

Each category section is a dict with up to three sub-dicts:

``"_d"`` (*data*)
    ``{key: to_jsonb(value)}`` for every attribute.

``"_l"`` (*locks*, sparse)
    ``{key: lockstring}`` — only present when at least one attr has a lock.

``"_s"`` (*strvalues*, sparse)
    ``{key: strvalue}`` — only for nick attributes (strattr=True).

Protected mutation API
----------------------
Call ``obj.attributes.blocking_update(mutator)`` when authority, revision, or
lifecycle checks must read and update the current committed Attribute document
under serialization::

    def advance(attrs):
        state = attrs.get("authority", raise_exception=True)
        state["revision"] += 1
        attrs.after_commit(audit_revision, state["revision"])
        return state["revision"]

    revision = obj.attributes.blocking_update(
        advance,
        locked_fields=("db_location_id",),
    )

The callback receives an isolated, expiring view with ``has``, ``get``,
``add``, ``batch_add``, ``remove``, ``clear``, ``entries``, and
``after_commit``. Mutable values returned by ``get`` are transaction-local and
their in-place changes are captured automatically. The callback runs exactly
once and must remain synchronous and free of external effects or query
barriers. Business ordering remains the callback's responsibility; the engine
guarantees storage serialization and lost-update protection only.

``locked_fields`` exposes current raw model storage attnames read-only. The API
does not mutate model fields: generic field saves have hooks and caches that
cannot be rolled back honestly. Systems requiring a mixed Attribute/model-field
transaction need a field-specific domain primitive.

Force-flush API
---------------
Call ``force_flush(obj)`` to immediately write the object's document to DB
without waiting for the next maintenance tick.  Use for safety-critical
transitions (death state, combat end) where a crash must not lose the write.

Example::

    from evennia.typeclasses.jsonb_handler import force_flush

    character.db.death_state = INCAPACITATED
    force_flush(character)
"""

import asyncio
import contextvars
import hashlib
import inspect
import json
import os
import tempfile
import threading
import time
import uuid
import weakref
from collections.abc import Mapping
from concurrent.futures import Future
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from weakref import WeakValueDictionary

from django.conf import settings
from django.db import DEFAULT_DB_ALIAS, connections, router, transaction
from django.db.models import F, QuerySet
from twisted.internet.defer import Deferred

from evennia.typeclasses.attribute_context import current_model_save_scope, model_save_active
from evennia.typeclasses.attributes import IAttributeBackend, InMemoryAttribute
from evennia.typeclasses.jsonb_util import from_jsonb, to_jsonb

__all__ = (
    "JsonbAttributeBackend",
    "FlushResult",
    "AttributePostCommitError",
    "AttributeUpdateConflict",
    "AttributeUpdateError",
    "AttributeUpdateIndeterminate",
    "AttributeUpdateUnavailable",
    "AttributeUpdateUsageError",
    "StaleAttributeValueError",
    "force_flush",
    "has_spooled_write",
    "reclaim_spooled_writes",
    "spool_pending_count",
)

# Document sub-keys within each category section.
_NULL_CATEGORY = "~"
_DATA = "_d"
_LOCKS = "_l"
_STRV = "_s"


# ---------------------------------------------------------------------------
# Flush result and durable write spool
# ---------------------------------------------------------------------------


class FlushResult:
    """
    Typed outcome of a single attribute-document flush.

    Truthiness is *durability*: ``bool(result)`` is True when the write is
    safe somewhere — either it reached the database (:attr:`ok`) or it was
    diverted to the on-disk spool for later reclamation (:attr:`spooled`). A
    falsy result means the write is still only in the volatile L1 dict and
    would be lost on a crash; the backend stays dirty and will be retried.

    Safety-critical callers (death state, combat end) should check this and
    react to a non-durable result rather than assuming persistence.

    Attributes:
        ok (bool): The document reached the database this attempt.
        spooled (bool): The document was written to the durable spool instead
            (the DB write kept failing past the retry limit).
        error (Exception or None): The DB error, when the write did not land.
    """

    __slots__ = ("ok", "spooled", "error")

    def __init__(self, ok, spooled=False, error=None):
        self.ok = ok
        self.spooled = spooled
        self.error = error

    @property
    def durable(self):
        """True when the write is safe in the DB or the spool."""
        return bool(self.ok or self.spooled)

    def __bool__(self):
        return self.durable

    def __repr__(self):
        return f"<FlushResult ok={self.ok} spooled={self.spooled}>"


class JsonbWriteConflict(RuntimeError):
    """A stale write-behind edit conflicts with a newer database edit."""


class AttributeUpdateError(RuntimeError):
    """Base error for protected Attribute updates."""


class AttributeUpdateConflict(AttributeUpdateError):
    """Committed or durably pending state conflicts with this update."""


class AttributeUpdateUnavailable(AttributeUpdateError):
    """The requested protected update cannot safely run now."""


class _AttributeRowMissing(AttributeUpdateUnavailable):
    """Internal signal that the coordinated model row is confirmed absent."""


class AttributeUpdateUsageError(AttributeUpdateError):
    """The caller violated the synchronous protected-update contract."""


class AttributeUpdateIndeterminate(AttributeUpdateError):
    """The database commit outcome could not be established."""


class AttributePostCommitError(AttributeUpdateError):
    """One or more finalization actions failed after a committed update."""

    def __init__(self, errors):
        super().__init__(f"{len(errors)} post-commit action(s) failed after commit")
        self.committed = True
        self.errors = tuple(errors)


class StaleAttributeValueError(AttributeUpdateError):
    """A cached mutable Attribute value predates a protected adoption."""


_MISSING = object()


def _require_io_thread(where):
    """Require the process-owned IO thread, with a pre-bootstrap main-thread fallback."""
    from evennia.utils import clock

    if clock.is_io_thread():
        return
    if clock.get_bound_loop() is None and threading.current_thread() is threading.main_thread():
        return
    raise AttributeUpdateUnavailable(f"{where} must run on the Evennia IO thread")


def _database_alias(obj):
    """Resolve the write database consistently for one object row."""
    return (
        getattr(getattr(obj, "_state", None), "db", None)
        or router.db_for_write(obj._meta.concrete_model, instance=obj)
        or DEFAULT_DB_ALIAS
    )


def _row_key(obj):
    """Return the canonical process-local identity for an Attribute row."""
    return (_database_alias(obj), obj._meta.concrete_model._meta.label_lower, int(obj.pk))


class JsonbRowState:
    """Shared write-behind and durable-queue state for one model row."""

    __slots__ = (
        "__weakref__",
        "key",
        "alias",
        "model",
        "pk",
        "committed_document",
        "durable_document",
        "visible_document",
        "volatile_baseline",
        "dirty",
        "dirty_since",
        "flush_failures",
        "generation",
        "path_generations",
        "pending_checked",
        "pending_blocked",
        "row_missing",
        "sync_failed",
        "model_save_snapshots",
        "backends",
        "obj_ref",
    )

    def __init__(self, obj):
        document = deepcopy(
            obj.db_attrs if isinstance(getattr(obj, "db_attrs", None), dict) else {}
        )
        self.key = _row_key(obj)
        self.alias, _label, self.pk = self.key
        self.model = obj._meta.concrete_model
        self.committed_document = deepcopy(document)
        self.durable_document = deepcopy(document)
        self.visible_document = deepcopy(document)
        self.volatile_baseline = deepcopy(document)
        self.dirty = False
        self.dirty_since = None
        self.flush_failures = 0
        self.generation = 0
        self.path_generations = {}
        self.pending_checked = False
        self.pending_blocked = False
        self.row_missing = False
        self.sync_failed = False
        self.model_save_snapshots = []
        self.backends = weakref.WeakSet()
        self.obj_ref = weakref.ref(obj)

    def mark_dirty(self):
        """Retain this row strongly until its volatile intent is durable."""
        if not self.dirty:
            self.volatile_baseline = deepcopy(self.durable_document)
            self.dirty_since = time.monotonic()
        self.dirty = True
        _STRONG_ROW_STATES[self.key] = self

    def mark_clean(self):
        """Clear volatile dirtiness while retaining durable pending rows."""
        self.dirty = False
        self.dirty_since = None
        self.flush_failures = 0
        if not _row_has_pending_entries(self.key, strict=False):
            _STRONG_ROW_STATES.pop(self.key, None)

    def dirty_age(self):
        """Return the continuous volatile dirty age in seconds."""
        if not self.dirty or self.dirty_since is None:
            return 0.0
        return max(0.0, time.monotonic() - self.dirty_since)


_ROW_STATES = WeakValueDictionary()
_STRONG_ROW_STATES = {}
_MISSING_ROW_MARKER = "_jsonb_row_missing"


def _existing_row_state(key):
    """Return canonical process-local state without creating or locking it."""
    return _ROW_STATES.get(key) or _STRONG_ROW_STATES.get(key)


def _regular_backend_for_state(state):
    """Return a live regular-Attribute backend for one row state, if present."""
    if state is None:
        return None
    return next((backend for backend in state.backends if not backend._attrtype), None)


def _get_row_state(obj):
    _require_io_thread("JSONB Attribute state creation")
    key = _row_key(obj)
    state = _ROW_STATES.get(key)
    if obj.__dict__.get(_MISSING_ROW_MARKER) == key:
        if state is not None and state.row_missing:
            return state
        state = JsonbRowState(obj)
        _mark_row_missing(state, register=False)
        return state
    if state is not None:
        return state
    if protected_mutation_active():
        raise AttributeUpdateUsageError(
            "A new JSONB Attribute row state may not be created inside blocking_update."
        )
    connection = connections[key[0]]
    if _has_user_transaction(connection):
        if not model_save_active():
            raise AttributeUpdateUsageError("A new JSONB Attribute row state requires autocommit.")
        # Django emits post-save hooks from an innermost savepoint=False
        # block. Attribute handlers are part of those hooks, so construct the
        # process-local state without taking the spool lock. The first access
        # after the save transaction owns autocommit will inspect the spool.
        state = JsonbRowState(obj)
        _ROW_STATES[key] = state
        return state
    state = JsonbRowState(obj)
    with _spool_row_lock(key):
        if _list_row_entries(key):
            # A newly constructed state has no trustworthy in-memory
            # durable head. Startup reclaim must resolve or remove the
            # queue before this row can be exposed again.
            state.pending_blocked = True
            _STRONG_ROW_STATES[key] = state
        state.pending_checked = True
    _ROW_STATES[key] = state
    return state


def dirty_jsonb_row_states():
    """Return strongly retained volatile JSONB rows waiting for persistence."""
    return [state for state in _STRONG_ROW_STATES.values() if state.dirty]


def discard_jsonb_row_states():
    """Discard process-local JSONB row state during test teardown only."""
    _STRONG_ROW_STATES.clear()
    _ROW_STATES.clear()


def _mark_row_missing(state, *, register=True):
    """Quarantine live handlers after their backing row disappears."""
    obj = state.obj_ref()
    if obj is not None:
        obj.__dict__[_MISSING_ROW_MARKER] = state.key
    state.pending_checked = True
    state.pending_blocked = True
    state.row_missing = True
    state.generation += 1
    if register:
        _ROW_STATES[state.key] = state
        _STRONG_ROW_STATES[state.key] = state
    for backend in list(state.backends):
        IAttributeBackend.reset_cache(backend)


def _tombstone_idmapper_row(obj):
    """Install a strong missing-row state without materializing an Attribute handler."""
    _require_io_thread("idmapper missing-row tombstone")
    key = _row_key(obj)
    state = _existing_row_state(key)
    if state is None:
        state = JsonbRowState(obj)
        _ROW_STATES[key] = state
    _mark_row_missing(state)


def _retire_recreated_row_tombstone(obj):
    """Give a newly inserted row fresh state without reviving stale objects."""
    _require_io_thread("JSONB recreated-row initialization")
    key = _row_key(obj)
    state = _existing_row_state(key)
    if state is not None and state.row_missing:
        if _ROW_STATES.get(key) is state:
            _ROW_STATES.pop(key, None)
        if _STRONG_ROW_STATES.get(key) is state:
            _STRONG_ROW_STATES.pop(key, None)
    obj.__dict__.pop(_MISSING_ROW_MARKER, None)


def _delete_row_key(obj, using=None):
    """Resolve and validate the one database identity used for deletion."""
    alias = _database_alias(obj)
    if using is not None and using != alias:
        raise AttributeUpdateUsageError(
            "JSONB-owned model deletion cannot override the instance database alias."
        )
    return (alias, obj._meta.concrete_model._meta.label_lower, int(obj.pk))


def _preflight_row_delete(obj, using=None):
    """Reject unsafe public deletion before lifecycle hooks can have effects."""
    _require_io_thread("JSONB Attribute row deletion")
    if protected_mutation_active():
        raise AttributeUpdateUsageError(
            "Model deletion may not run inside a blocking_update callback."
        )
    key = _delete_row_key(obj, using)
    if _has_user_transaction(connections[key[0]]):
        raise AttributeUpdateUsageError("JSONB-owned model deletion requires autocommit.")
    state = _existing_row_state(key)
    if state is not None:
        _ensure_pending_checked(state)
        if state.pending_blocked:
            raise AttributeUpdateUnavailable("Quarantined Attribute state blocks model deletion.")
    with _spool_row_lock(key):
        if _list_row_entries(key):
            raise AttributeUpdateConflict(
                "Durable or indeterminate Attribute intent blocks row deletion."
            )


@contextmanager
def _coordinate_row_delete(obj, using=None):
    """Hold spool-first coordination across one supported local row delete."""
    _require_io_thread("JSONB Attribute row deletion")
    if protected_mutation_active():
        raise AttributeUpdateUsageError(
            "Model deletion may not run inside a blocking_update callback."
        )
    key = _delete_row_key(obj, using)
    if _has_user_transaction(connections[key[0]]):
        raise AttributeUpdateUsageError("JSONB-owned model deletion requires autocommit.")
    with _spool_row_lock(key):
        if _list_row_entries(key):
            raise AttributeUpdateConflict(
                "Durable or indeterminate Attribute intent blocks row deletion."
            )
        yield _existing_row_state(key)


def _retire_row_state_after_delete(state):
    """Discard deliberate local intent after the containing delete commits."""
    if state is None:
        return

    def retire():
        state.pending_checked = True
        state.pending_blocked = True
        state.row_missing = True
        state.sync_failed = False
        state.dirty = False
        state.dirty_since = None
        state.flush_failures = 0
        state.generation += 1
        state.path_generations.clear()
        state.committed_document = {}
        state.durable_document = {}
        state.visible_document = {}
        state.volatile_baseline = {}
        _ROW_STATES.pop(state.key, None)
        _STRONG_ROW_STATES.pop(state.key, None)
        for backend in list(state.backends):
            IAttributeBackend.reset_cache(backend)

    connection = connections[state.alias]
    if _has_user_transaction(connection):
        transaction.on_commit(retire, using=state.alias)
    else:
        retire()


def _ensure_pending_checked(state):
    """Reconcile state initialized by model-save hooks before later access."""
    _resolve_model_save_outcomes(state)
    if state.pending_checked:
        return
    connection = connections[state.alias]
    if _has_user_transaction(connection):
        if model_save_active():
            return
        raise AttributeUpdateUsageError(
            "Deferred JSONB Attribute state cannot be accessed inside an outer transaction."
        )
    _require_io_thread("deferred JSONB Attribute state reconciliation")
    with _spool_row_lock(state.key):
        if _list_row_entries(state.key):
            state.pending_blocked = True
            state.pending_checked = True
            _STRONG_ROW_STATES[state.key] = state
            return
        row = (
            state.model._base_manager.using(state.alias)
            .filter(pk=state.pk)
            .values("db_attrs")
            .first()
        )
        if row is None:
            state.pending_checked = True
            _mark_row_missing(state)
            return
        remote = row["db_attrs"] or {}
        if state.dirty:
            try:
                visible = _three_way_merge(
                    state.volatile_baseline,
                    state.visible_document,
                    remote,
                )
            except JsonbWriteConflict as err:
                state.pending_checked = True
                state.pending_blocked = True
                _STRONG_ROW_STATES[state.key] = state
                raise AttributeUpdateConflict(
                    "Deferred model-save Attribute state conflicts with committed data."
                ) from err
            state.committed_document = deepcopy(remote)
            state.durable_document = deepcopy(remote)
            state.visible_document = deepcopy(visible)
            state.volatile_baseline = deepcopy(remote)
            if visible == remote:
                state.mark_clean()
        else:
            state.committed_document = deepcopy(remote)
            state.durable_document = deepcopy(remote)
            state.visible_document = deepcopy(remote)
            state.volatile_baseline = deepcopy(remote)
            state.mark_clean()
        state.pending_checked = True
        try:
            _sync_live_backends(state, invalidate=True)
        except Exception as err:
            _quarantine_cache_sync(state)
            raise AttributeUpdateUnavailable(
                "Deferred JSONB Attribute state could not reconcile live handlers."
            ) from err


def _capture_model_save_mutation(state):
    """Snapshot row state before a model-save hook mutates Attributes."""
    scope = current_model_save_scope()
    if scope is None or any(
        existing is scope for existing, _snapshot in state.model_save_snapshots
    ):
        return
    snapshot = {
        "committed_document": deepcopy(state.committed_document),
        "durable_document": deepcopy(state.durable_document),
        "visible_document": deepcopy(state.visible_document),
        "volatile_baseline": deepcopy(state.volatile_baseline),
        "dirty": state.dirty,
        "dirty_since": state.dirty_since,
        "flush_failures": state.flush_failures,
        "generation": state.generation,
        "path_generations": deepcopy(state.path_generations),
        "pending_checked": state.pending_checked,
        "pending_blocked": state.pending_blocked,
        "row_missing": state.row_missing,
        "sync_failed": state.sync_failed,
    }
    state.model_save_snapshots.append((scope, snapshot))


def _resolve_model_save_outcomes(state):
    """Commit or roll back Attribute edits made by completed model-save hooks."""
    if not state.model_save_snapshots:
        return
    connection = connections[state.alias]
    pending = [
        scope for scope, _snapshot in state.model_save_snapshots if scope.status == "pending"
    ]
    if pending and not model_save_active():
        if _has_user_transaction(connection):
            raise AttributeUpdateUsageError(
                "Attribute state from a model save awaits its outer transaction outcome."
            )
        for scope in pending:
            scope.rollback()

    remaining = []
    restored = False
    for scope, snapshot in reversed(state.model_save_snapshots):
        if scope.status == "pending":
            remaining.append((scope, snapshot))
            continue
        if scope.status == "rolled_back":
            for name, value in snapshot.items():
                setattr(state, name, deepcopy(value))
            restored = True
    state.model_save_snapshots = list(reversed(remaining))
    if not restored:
        return
    if state.dirty or state.pending_blocked:
        _STRONG_ROW_STATES[state.key] = state
    else:
        _STRONG_ROW_STATES.pop(state.key, None)
    try:
        _sync_live_backends(state, invalidate=True)
    except Exception as err:
        _quarantine_cache_sync(state)
        raise AttributeUpdateUnavailable(
            "Rolled-back model-save Attribute state could not reconcile live handlers."
        ) from err


@dataclass
class _MutationContext:
    key: tuple
    phase: str = "MUTATING"
    poisoned: bool = False


_ACTIVE_MUTATION = contextvars.ContextVar("jsonb_active_mutation", default=None)


def protected_mutation_active():
    """Return whether this context is inside a protected mutation transaction."""
    active = _ACTIVE_MUTATION.get()
    return active is not None and active.phase != "POSTCOMMIT"


def _same(left, right):
    if left is _MISSING or right is _MISSING:
        return left is right
    return left == right


def _clone(value):
    return _MISSING if value is _MISSING else deepcopy(value)


def _three_way_merge(baseline, local, remote):
    """Merge non-overlapping document edits and reject exact-path conflicts."""
    if _same(local, baseline):
        return _clone(remote)
    if _same(remote, baseline) or _same(remote, local):
        return _clone(local)
    if baseline is _MISSING and isinstance(local, Mapping) and isinstance(remote, Mapping):
        baseline = {}
    if all(isinstance(value, Mapping) for value in (baseline, local, remote)):
        merged = {}
        for key in set(baseline) | set(local) | set(remote):
            value = _three_way_merge(
                baseline.get(key, _MISSING),
                local.get(key, _MISSING),
                remote.get(key, _MISSING),
            )
            if value is not _MISSING:
                merged[key] = value
        return merged
    if local is _MISSING and remote is _MISSING:
        return _MISSING
    raise JsonbWriteConflict("Attribute document changed concurrently at the same path.")


def _spool_dir():
    """
    Directory holding diverted (undurable) attribute documents.

    Configurable via ``settings.JSONB_WRITE_SPOOL_DIR``; defaults to
    ``<LOG_DIR>/jsonb_spool``.
    """
    configured = getattr(settings, "JSONB_WRITE_SPOOL_DIR", None)
    if configured:
        return configured
    log_dir = getattr(settings, "LOG_DIR", None) or tempfile.gettempdir()
    return os.path.join(log_dir, "jsonb_spool")


def _spool_prefix(key):
    _alias, model_label, pk = key
    digest = hashlib.sha256(repr(key).encode()).hexdigest()[:16]
    return f"{digest}.{model_label.replace('.', '_')}.{pk}."


def _legacy_spool_candidates(key):
    """Return exact/prefix names used before the full row key entered filenames."""
    from django.apps import apps

    _alias, model_label, pk = key
    candidates = {f"{model_label}.{pk}.json", f"{model_label.replace('.', '_')}.{pk}."}
    try:
        app_label, model_name = model_label.split(".", 1)
        label = apps.get_model(app_label, model_name)._meta.label
        candidates.add(f"{label}.{pk}.json")
    except (LookupError, ValueError):
        pass
    return candidates


_LOCK_ACQUIRE_TIMEOUT = 60.0


def _seed_lock_byte(lockfile):
    """Make sure byte 0 exists, since Windows cannot lock an empty range.

    Args:
        lockfile (file): Lock file open in append+binary mode.

    Notes:
        The byte is never read back to test for it. Windows locks are
        mandatory, so a range held by another process raises PermissionError on
        read - the check would fail exactly when another process is contending,
        which is when the lock matters. `fstat` reports the size without
        touching the locked range. Losing the race to seed is harmless: the
        winner wrote the same byte, and the failed write raises rather than
        corrupting it.

    """
    if os.fstat(lockfile.fileno()).st_size:
        return
    try:
        lockfile.write(b"0")
        lockfile.flush()
    except OSError:
        pass


def _lock_byte_blocking(lockfile):
    """Block until byte 0 of `lockfile` is held exclusively by this process.

    Args:
        lockfile (file): Lock file positioned at byte 0.

    Raises:
        OSError: If the lock is still unavailable after
            `_LOCK_ACQUIRE_TIMEOUT` seconds.

    Notes:
        `msvcrt.locking` with `LK_LOCK` gives up after ten one-second attempts
        and raises, so ordinary contention would surface as an error. Retrying
        until the timeout restores the blocking semantics `flock` has on POSIX.

    """
    import msvcrt

    deadline = time.monotonic() + _LOCK_ACQUIRE_TIMEOUT
    while True:
        try:
            msvcrt.locking(lockfile.fileno(), msvcrt.LK_LOCK, 1)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            lockfile.seek(0)


@contextmanager
def _spool_row_lock(key):
    """Acquire the stable cross-process filesystem lock for one row."""
    spool = _spool_dir()
    os.makedirs(spool, exist_ok=True)
    lock_dir = os.path.join(spool, ".locks")
    os.makedirs(lock_dir, exist_ok=True)
    digest = hashlib.sha256(repr(key).encode()).hexdigest()
    path = os.path.join(lock_dir, f"{digest}.lock")
    with open(path, "a+b") as lockfile:
        if os.name == "nt":
            import msvcrt

            _seed_lock_byte(lockfile)
            lockfile.seek(0)
            _lock_byte_blocking(lockfile)
            try:
                yield
            finally:
                lockfile.seek(0)
                msvcrt.locking(lockfile.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lockfile.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lockfile.fileno(), fcntl.LOCK_UN)


def _read_spool_payload(path):
    with open(path, encoding="utf-8") as fh:
        payload = json.load(fh)
    if payload.get("v") in (3, 4):
        return payload
    payload.setdefault("kind", "DELTA")
    payload.setdefault("sequence", 0)
    payload.setdefault("database", DEFAULT_DB_ALIAS)
    if "baseline" not in payload:
        payload["baseline"] = {}
    return payload


def _payload_key(payload):
    return (
        payload.get("database") or DEFAULT_DB_ALIAS,
        payload["model"].lower(),
        int(payload["pk"]),
    )


def _list_row_entries(key, *, strict=True):
    try:
        spool = _spool_dir()
        if not os.path.isdir(spool):
            return []
        prefix = _spool_prefix(key)
        legacy_candidates = _legacy_spool_candidates(key)
        entries = []
        for name in os.listdir(spool):
            if not name.endswith(".json"):
                continue
            if not name.startswith(prefix) and not any(
                name == candidate or name.startswith(candidate) for candidate in legacy_candidates
            ):
                continue
            path = os.path.join(spool, name)
            payload = _read_spool_payload(path)
            if _payload_key(payload) != key:
                continue
            entries.append((int(payload.get("sequence", 0)), path, payload))
        return sorted(entries, key=lambda item: (item[0], item[1]))
    except Exception:
        if strict:
            raise
        return []


def _row_has_pending_entries(key, *, strict=True):
    return bool(_list_row_entries(key, strict=strict))


def _next_spool_sequence(key):
    entries = _list_row_entries(key)
    return max((sequence for sequence, _path, _payload in entries), default=0) + 1


def _write_spool_payload(key, kind, baseline, document):
    """Write one immutable, monotonically ordered durable row intent."""
    spool = _spool_dir()
    os.makedirs(spool, exist_ok=True)
    sequence = _next_spool_sequence(key)
    alias, model_label, pk = key
    payload = {
        "v": 4,
        "kind": kind,
        "sequence": sequence,
        "database": alias,
        "model": model_label,
        "pk": pk,
        "baseline": deepcopy(baseline),
        "db_attrs": deepcopy(document),
        "ts": time.time(),
    }
    fd, tmp = tempfile.mkstemp(dir=spool, prefix=_spool_prefix(key), suffix=".tmp")
    final = os.path.join(
        spool,
        f"{_spool_prefix(key)}{sequence:020d}.{kind.lower()}.{uuid.uuid4().hex}.json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, final)
        if os.name != "nt":
            directory_fd = os.open(spool, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        return final
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _append_state_delta_locked(state):
    _write_spool_payload(
        state.key,
        "DELTA",
        state.durable_document,
        state.visible_document,
    )
    state.durable_document = deepcopy(state.visible_document)
    state.volatile_baseline = deepcopy(state.durable_document)
    state.mark_clean()
    _STRONG_ROW_STATES[state.key] = state


def has_spooled_write(obj):
    """Return whether any durable or indeterminate intent exists for one row."""
    _require_io_thread("JSONB Attribute spool inspection")
    active = _ACTIVE_MUTATION.get()
    if active is not None and active.phase != "POSTCOMMIT":
        raise AttributeUpdateUsageError(
            "Spool inspection may not run inside a blocking_update callback."
        )
    key = _row_key(obj)
    if _has_user_transaction(connections[key[0]]):
        raise AttributeUpdateUsageError("Spool inspection requires autocommit.")
    with _spool_row_lock(key):
        return _row_has_pending_entries(key)


def spool_remaining_dirty():
    """
    Last-resort: divert every still-dirty attribute document to the durable
    spool. Call at shutdown *after* a final :func:`flush_all_dirty` so a write
    the DB refused on the way out is captured on disk instead of lost (it is
    replayed by :func:`reclaim_spooled_writes` on the next boot).

    Returns:
        int: Number of documents spooled.
    """
    from evennia.utils import logger

    _require_io_thread("JSONB Attribute shutdown spooling")
    active = _ACTIVE_MUTATION.get()
    if active is not None and active.phase != "POSTCOMMIT":
        raise AttributeUpdateUsageError(
            "Spool persistence may not run inside a blocking_update callback."
        )
    aliases = {state.alias for state in dirty_jsonb_row_states()}
    if any(_has_user_transaction(connections[alias]) for alias in aliases):
        raise AttributeUpdateUsageError("Spool persistence requires autocommit.")
    spooled = 0
    for state in dirty_jsonb_row_states():
        try:
            with _spool_row_lock(state.key):
                entries = _list_row_entries(state.key)
                if any(payload.get("kind") == "PREPARED_BLOCKING" for _, _, payload in entries):
                    raise AttributeUpdateUnavailable("protected row outcome awaits resolution")
                _append_state_delta_locked(state)
            spooled += 1
        except Exception:
            logger.log_err(
                "CRITICAL: could not spool dirty attribute doc for pk=%s at shutdown; "
                "write may be lost." % state.pk
            )
    if spooled:
        logger.log_warn(
            "jsonb spool: diverted %d undurable attribute write(s) to disk at shutdown" % spooled
        )
    return spooled


def spool_pending_count():
    """Number of undurable documents currently waiting in the spool."""
    active = _ACTIVE_MUTATION.get()
    if active is not None and active.phase != "POSTCOMMIT":
        raise AttributeUpdateUsageError(
            "Spool inspection may not run inside a blocking_update callback."
        )
    try:
        spool = _spool_dir()
        if not os.path.isdir(spool):
            return 0
        return sum(1 for name in os.listdir(spool) if name.endswith(".json"))
    except Exception:
        return 0


def reclaim_spooled_writes():
    """
    Replay any spooled attribute documents into the database.

    Call once at server start, before normal operation, so writes diverted to
    the spool during a past DB outage are not lost. Each successfully replayed
    document's spool file is removed; a file whose object no longer exists is
    dropped. Returns the number of documents reclaimed.
    """
    from django.apps import apps

    from evennia.utils import logger

    _require_io_thread("JSONB Attribute spool reclamation")
    active = _ACTIVE_MUTATION.get()
    if active is not None and active.phase != "POSTCOMMIT":
        raise AttributeUpdateUsageError(
            "Spool reclamation may not run inside a blocking_update callback."
        )
    spool = _spool_dir()
    if not os.path.isdir(spool):
        return 0
    groups = {}
    for name in os.listdir(spool):
        if not name.endswith(".json"):
            continue
        path = os.path.join(spool, name)
        try:
            payload = _read_spool_payload(path)
            groups.setdefault(_payload_key(payload), []).append(path)
        except Exception:
            logger.log_trace(f"jsonb spool: unreadable {name}; leaving it in place")
    for alias in {key[0] for key in groups}:
        if _has_user_transaction(connections[alias]):
            raise AttributeUpdateUsageError(
                "Spool reclamation requires ownership of the outer transaction."
            )
    reclaimed = 0
    for key in sorted(groups):
        alias, model_label, pk = key
        with _spool_row_lock(key):
            entries = _list_row_entries(key)
            last_committed = None
            row_missing = False
            for _sequence, path, payload in entries:
                try:
                    app_label, model_name = model_label.split(".", 1)
                    model = apps.get_model(app_label, model_name)
                    with transaction.atomic(using=alias):
                        manager = model._base_manager.using(alias)
                        connection = connections[alias]
                        if connection.vendor == "sqlite":
                            manager.filter(pk=pk).update(db_attrs=F("db_attrs"))
                        query = manager.select_for_update().filter(pk=pk)
                        row = query.values("db_attrs").first()
                        if row is None:
                            row_missing = True
                            if payload.get("kind") == "PREPARED_BLOCKING":
                                logger.log_err(
                                    "jsonb spool: missing %s row has an indeterminate protected "
                                    "outcome; retaining witness" % model_label
                                )
                                break
                            os.remove(path)
                            logger.log_warn(
                                "jsonb spool: %s no longer exists; dropping spooled write"
                                % model_label
                            )
                            continue
                        remote = row["db_attrs"] or {}
                        if payload.get("kind") == "PREPARED_BLOCKING":
                            baseline = payload["baseline"]
                            final = payload["db_attrs"]
                            if remote == baseline:
                                os.remove(path)
                            else:
                                try:
                                    already_present = _three_way_merge(baseline, final, remote)
                                except JsonbWriteConflict:
                                    already_present = None
                                if already_present == remote:
                                    os.remove(path)
                                else:
                                    break
                            last_committed = remote
                            continue
                        merged = _three_way_merge(payload["baseline"], payload["db_attrs"], remote)
                        updated = manager.filter(pk=pk).update(db_attrs=merged)
                        if updated != 1:
                            raise AttributeUpdateUnavailable("spooled row disappeared")
                    os.remove(path)
                    reclaimed += 1
                    last_committed = merged
                except Exception:
                    logger.log_trace(
                        f"jsonb spool: failed to reclaim {os.path.basename(path)}; "
                        "leaving it and later intents in place"
                    )
                    break
            state = _ROW_STATES.get(key)
            remaining = _list_row_entries(key)
            if state is not None:
                if row_missing:
                    _mark_row_missing(state)
                    continue
                state.pending_blocked = bool(remaining)
                if last_committed is not None:
                    state.committed_document = deepcopy(last_committed)
                if remaining:
                    _STRONG_ROW_STATES[key] = state
                elif last_committed is not None:
                    old_durable = deepcopy(state.durable_document)
                    state.durable_document = deepcopy(last_committed)
                    if state.dirty:
                        state.visible_document = _three_way_merge(
                            old_durable, state.visible_document, last_committed
                        )
                        state.volatile_baseline = deepcopy(last_committed)
                    else:
                        state.visible_document = deepcopy(last_committed)
                        state.volatile_baseline = deepcopy(last_committed)
                    try:
                        _sync_live_backends(state, invalidate=True)
                    except Exception:
                        _quarantine_cache_sync(state)
                        logger.log_err(
                            "jsonb spool: database replay succeeded but cache reconciliation "
                            f"failed for {model_label}#{pk}"
                        )
                    else:
                        if not state.dirty:
                            _STRONG_ROW_STATES.pop(key, None)
    if reclaimed:
        logger.log_info("jsonb spool: reclaimed %d deferred attribute write(s)" % reclaimed)
    return reclaimed


# ---------------------------------------------------------------------------
# Attribute object
# ---------------------------------------------------------------------------


class JsonbAttribute(InMemoryAttribute):
    """
    InMemoryAttribute extended with write-back to the JSONB L1 dict.

    When _Saver* proxies mutate the value in-place they call
    ``attr.value = self``.  This override intercepts that call and propagates
    the change back to the owning ``JsonbAttributeBackend``.
    """

    # Weak-ref back to the backend; set by _make_attr.
    _backend_ref = None
    _backend_generation = None

    # Override value property so mutations write back to L1.
    @property
    def value(self):
        return self.db_value

    @value.setter
    def value(self, new_value):
        backend = self._backend_ref() if self._backend_ref is not None else None
        if backend is not None:
            self._backend_generation = backend._on_attr_value_changed(
                self.db_key,
                self.db_category,
                new_value,
                expected_generation=self._backend_generation,
            )
            self.db_value = new_value
        elif self._backend_ref is not None:
            from evennia.utils import logger

            logger.log_err(
                f"JsonbAttribute: backend evicted while attr {self.db_key!r} still "
                "referenced — in-place mutation lost. Do not hold strong refs to "
                "JsonbAttribute objects after the owning object leaves the idmapper."
            )

    @value.deleter
    def value(self):
        pass  # deletion goes through handler.delete_attribute, not this path

    @property
    def lock_storage(self):
        return self.db_lock_storage

    @lock_storage.setter
    def lock_storage(self, value):
        backend = self._backend_ref() if self._backend_ref is not None else None
        if backend is not None:
            self._backend_generation = backend._on_lock_changed(
                self.db_key,
                self.db_category,
                value,
                expected_generation=self._backend_generation,
            )
            self.db_lock_storage = value
        elif self._backend_ref is not None:
            from evennia.utils import logger

            logger.log_err(
                f"JsonbAttribute: backend evicted while attr {self.db_key!r} still "
                "referenced — lock mutation lost."
            )

    @lock_storage.deleter
    def lock_storage(self):
        self.lock_storage = ""


# ---------------------------------------------------------------------------
# Backend
# ---------------------------------------------------------------------------


class JsonbAttributeBackend(IAttributeBackend):
    """
    Attribute backend that stores everything in ``obj.db_attrs`` (JSONField).

    Enabled by setting in ``settings.py``::

        ATTRIBUTE_BACKEND_CLASS = "evennia.typeclasses.jsonb_handler.JsonbAttributeBackend"

    The old ``AttributeDB`` M2M table is left intact and is used for backfill
    and parity checking (see the management commands).  It is not read or
    written once this backend is active.

    Each backend instance lives for the lifetime of the object's idmapper
    cache entry.  The L1 dict (``_l1``) is loaded once on construction from
    ``obj.db_attrs`` and stays coherent until the object is evicted from the
    idmapper.
    """

    _attrclass = JsonbAttribute

    _FLUSH_FAIL_LIMIT = 10

    def __init__(self, handler, attrtype):
        super().__init__(handler, attrtype)
        self._pk_counter = 0
        self._row_state = _get_row_state(self.obj)
        self._row_state.backends.add(self)

    @property
    def _l1(self):
        return self._row_state.visible_document

    @_l1.setter
    def _l1(self, value):
        self._row_state.visible_document = value

    @property
    def _baseline(self):
        return self._row_state.volatile_baseline

    @_baseline.setter
    def _baseline(self, value):
        self._row_state.volatile_baseline = value

    @property
    def _dirty(self):
        return self._row_state.dirty

    @_dirty.setter
    def _dirty(self, value):
        if value:
            self._row_state.mark_dirty()
        else:
            self._row_state.dirty = False

    @property
    def _dirty_since(self):
        return self._row_state.dirty_since

    @_dirty_since.setter
    def _dirty_since(self, value):
        self._row_state.dirty_since = value

    @property
    def _flush_failures(self):
        return self._row_state.flush_failures

    @_flush_failures.setter
    def _flush_failures(self, value):
        self._row_state.flush_failures = value

    # ------------------------------------------------------------------
    # Document helpers
    # ------------------------------------------------------------------

    def _cat_key(self, category):
        cat = _NULL_CATEGORY if category is None else category.lower()
        if self._attrtype:
            return f"_t_{self._attrtype}__{cat}"
        return cat

    def _get_section(self, category, *, create=False):
        key = self._cat_key(category)
        if key not in self._l1:
            if not create:
                return None
            self._l1[key] = {}
        return self._l1[key]

    def _mark_dirty(self):
        self._assert_mutation_allowed()
        self._row_state.mark_dirty()

    def _path_generation(self, key, category):
        """Return the global/path epoch guarding one mutable Attribute proxy."""
        path = (self._cat_key(category), key)
        return self._row_state.generation, self._row_state.path_generations.get(path, 0)

    def _bump_path_generation(self, key, category):
        """Invalidate older proxies for one exact Attribute path."""
        path = (self._cat_key(category), key)
        self._row_state.path_generations[path] = self._row_state.path_generations.get(path, 0) + 1
        return self._path_generation(key, category)

    def _assert_readable(self):
        """Refuse to expose a row whose durable logical head is unresolved."""
        _require_io_thread("JSONB Attribute read")
        active = _ACTIVE_MUTATION.get()
        if active is not None and active.phase != "POSTCOMMIT":
            raise AttributeUpdateUsageError(
                "Use the protected Attribute view while blocking_update() is active."
            )
        _resolve_model_save_outcomes(self._row_state)
        _ensure_pending_checked(self._row_state)
        if self._row_state.pending_blocked:
            raise AttributeUpdateUnavailable(
                "Attribute row state is unavailable until recovery or reload."
            )

    def _assert_mutation_allowed(self, *, expected_generation=None, key=None, category=None):
        """Reject stale proxies and writes bypassing an active protected view."""
        _require_io_thread("JSONB Attribute mutation")
        active = _ACTIVE_MUTATION.get()
        if active is not None and active.phase != "POSTCOMMIT":
            raise AttributeUpdateUsageError(
                "Use the protected Attribute view while blocking_update() is active."
            )
        _resolve_model_save_outcomes(self._row_state)
        _ensure_pending_checked(self._row_state)
        if self._row_state.pending_blocked:
            raise AttributeUpdateUnavailable(
                "Attribute row state is unavailable until recovery or reload."
            )
        if expected_generation is not None and expected_generation != self._path_generation(
            key, category
        ):
            raise StaleAttributeValueError(
                "This mutable Attribute value is stale; fetch it again before changing it."
            )
        _capture_model_save_mutation(self._row_state)

    # ------------------------------------------------------------------
    # Write-back callbacks (called by JsonbAttribute on mutation)
    # ------------------------------------------------------------------

    def _on_attr_value_changed(self, key, category, value, *, expected_generation=None):
        self._assert_mutation_allowed(
            expected_generation=expected_generation, key=key, category=category
        )
        section = self._get_section(category, create=True)
        section.setdefault(_DATA, {})[key] = to_jsonb(value)
        self._row_state.mark_dirty()
        return self._bump_path_generation(key, category)

    def _on_lock_changed(self, key, category, lockstring, *, expected_generation=None):
        self._assert_mutation_allowed(
            expected_generation=expected_generation, key=key, category=category
        )
        section = self._get_section(category, create=True)
        if lockstring:
            section.setdefault(_LOCKS, {})[key] = lockstring
        else:
            section.get(_LOCKS, {}).pop(key, None)
        self._row_state.mark_dirty()
        return self._bump_path_generation(key, category)

    # ------------------------------------------------------------------
    # Attribute construction
    # ------------------------------------------------------------------

    def _make_attr(self, key, category, encoded, lockstring, strvalue):
        self._pk_counter += 1
        attr = JsonbAttribute(
            pk=self._pk_counter,
            key=key,
            category=category,
            lock_storage=lockstring or "",
        )
        attr.db_strvalue = strvalue
        # Pass db_obj=attr so _Saver* write-backs call attr.value = self.
        attr.db_value = from_jsonb(encoded, db_obj=attr)
        attr._backend_ref = weakref.ref(self)
        attr._backend_generation = self._path_generation(key, category)
        return attr

    # ------------------------------------------------------------------
    # IAttributeBackend queries
    # ------------------------------------------------------------------

    def query_all(self):
        self._assert_readable()
        attrs = []
        attrtype_prefix = f"_t_{self._attrtype}__" if self._attrtype else None
        for cat_key, section in self._l1.items():
            if not isinstance(section, dict):
                continue
            if attrtype_prefix:
                # Attrtype backend: only yield sections for this attrtype.
                if not cat_key.startswith(attrtype_prefix):
                    continue
                cat_part = cat_key[len(attrtype_prefix) :]
                category = None if cat_part == _NULL_CATEGORY else cat_part
            else:
                # Regular backend: skip internal meta and attrtype sections.
                if cat_key.startswith("_"):
                    continue
                category = None if cat_key == _NULL_CATEGORY else cat_key
            data = section.get(_DATA, {})
            locks = section.get(_LOCKS, {})
            strvs = section.get(_STRV, {})
            for k, enc in data.items():
                attrs.append(self._make_attr(k, category, enc, locks.get(k, ""), strvs.get(k)))
        return attrs

    def query_key(self, key, category):
        self._assert_readable()
        section = self._get_section(category)
        if not section:
            return []
        data = section.get(_DATA, {})
        if key not in data:
            return []
        locks = section.get(_LOCKS, {})
        strvs = section.get(_STRV, {})
        return [self._make_attr(key, category, data[key], locks.get(key, ""), strvs.get(key))]

    def query_category(self, category):
        self._assert_readable()
        section = self._get_section(category)
        if not section:
            return []
        data = section.get(_DATA, {})
        locks = section.get(_LOCKS, {})
        strvs = section.get(_STRV, {})
        return [self._make_attr(k, category, data[k], locks.get(k, ""), strvs.get(k)) for k in data]

    # ------------------------------------------------------------------
    # Cache layer — override to skip the M2M connector indirection
    # ------------------------------------------------------------------

    def _get_cache_key(self, key, category):
        """
        Override base _get_cache_key to use JsonbAttribute objects directly.

        The base implementation does ``conn[0].attribute`` which assumes
        query_key returns M2M through-table objects.  We return the attribute
        itself from query_key, so skip that indirection.
        """
        self._assert_readable()
        cachekey = (key, category)
        cachefound = False
        try:
            attr = settings.TYPECLASS_AGGRESSIVE_CACHE and self._cache[cachekey]
            cachefound = True
        except KeyError:
            attr = None

        if attr and attr.pk is None:
            attr = None
            cachefound = False
            del self._cache[cachekey]
        if cachefound and settings.TYPECLASS_AGGRESSIVE_CACHE:
            return [attr] if attr else []

        attrs = self.query_key(key, category)
        if attrs:
            attr = attrs[0]
            if settings.TYPECLASS_AGGRESSIVE_CACHE:
                self._cache[cachekey] = attr
            return [attr] if attr.pk is not None else []
        else:
            if settings.TYPECLASS_AGGRESSIVE_CACHE:
                self._cache[cachekey] = None
            return []

    def _get_cache_category(self, category):
        """Refuse cached category reads while durable state is unresolved."""
        self._assert_readable()
        return super()._get_cache_category(category)

    def get_all_attributes(self):
        """Refuse cached full-document reads while durable state is unresolved."""
        self._assert_readable()
        return super().get_all_attributes()

    # ------------------------------------------------------------------
    # IAttributeBackend mutations
    # ------------------------------------------------------------------

    def create_attribute(self, key, category, lockstring, value, strvalue=False, cache=True):
        """Guard protected callbacks before the base path can warm caches."""
        self._assert_mutation_allowed()
        return super().create_attribute(key, category, lockstring, value, strvalue, cache)

    def update_attribute(self, attr, value, strattr=False):
        """Guard protected callbacks before the base update path runs."""
        self._assert_mutation_allowed(
            expected_generation=attr._backend_generation,
            key=attr.db_key,
            category=attr.db_category,
        )
        return super().update_attribute(attr, value, strattr)

    def delete_attribute(self, attr):
        """Guard protected callbacks before the base path removes cache entries."""
        if attr:
            self._assert_mutation_allowed(
                expected_generation=attr._backend_generation,
                key=attr.db_key,
                category=attr.db_category,
            )
        return super().delete_attribute(attr)

    def do_create_attribute(self, key, category, lockstring, value, strvalue):
        self._assert_mutation_allowed()
        section = self._get_section(category, create=True)
        if strvalue:
            section.setdefault(_DATA, {})[key] = to_jsonb(None)
            section.setdefault(_STRV, {})[key] = value
        else:
            section.setdefault(_DATA, {})[key] = to_jsonb(value)
        if lockstring:
            section.setdefault(_LOCKS, {})[key] = lockstring
        self._row_state.mark_dirty()
        self._bump_path_generation(key, category)
        # Return a live attribute object so the handler can cache it.
        locks = section.get(_LOCKS, {})
        strvs = section.get(_STRV, {})
        data = section.get(_DATA, {})
        return self._make_attr(key, category, data[key], locks.get(key, ""), strvs.get(key))

    def do_update_attribute(self, attr, value, strvalue):
        key = attr.db_key
        category = attr.db_category
        self._assert_mutation_allowed(
            expected_generation=attr._backend_generation, key=key, category=category
        )
        section = self._get_section(category, create=True)
        if strvalue:
            section.setdefault(_DATA, {})[key] = to_jsonb(None)
            section.setdefault(_STRV, {})[key] = value
            attr.db_strvalue = value
            attr.db_value = None
        else:
            section.setdefault(_DATA, {})[key] = to_jsonb(value)
            attr.db_value = from_jsonb(section[_DATA][key], db_obj=attr)
            attr.db_strvalue = None
        self._row_state.mark_dirty()
        attr._backend_generation = self._bump_path_generation(key, category)

    def do_batch_update_attribute(self, attr_obj, category, lock_storage, new_value, strvalue):
        self._assert_mutation_allowed(
            expected_generation=attr_obj._backend_generation,
            key=attr_obj.db_key,
            category=attr_obj.db_category,
        )
        old_cat = attr_obj.db_category
        # Move to new category if changed.
        if old_cat != category:
            old_section = self._get_section(old_cat)
            if old_section:
                old_section.get(_DATA, {}).pop(attr_obj.db_key, None)
                old_section.get(_LOCKS, {}).pop(attr_obj.db_key, None)
                old_section.get(_STRV, {}).pop(attr_obj.db_key, None)
            self._bump_path_generation(attr_obj.db_key, old_cat)
            attr_obj.db_category = category
            attr_obj._backend_generation = self._path_generation(attr_obj.db_key, category)
        attr_obj.db_lock_storage = lock_storage or ""
        self.do_update_attribute(attr_obj, new_value, strvalue)
        if lock_storage:
            section = self._get_section(category, create=True)
            section.setdefault(_LOCKS, {})[attr_obj.db_key] = lock_storage

    def do_batch_finish(self, attr_objs):
        pass  # all writes already applied in do_create / do_batch_update

    def batch_add(self, *args, **kwargs):
        """
        Override to fix a cache-coherence issue in the base implementation.

        IAttributeBackend.batch_add caches "not found" misses per key before
        calling do_create_attribute, which leaves stale None entries in _cache
        that shadow the freshly written _l1 data.  Clear the lookup cache so
        the next get() re-reads from _l1.
        """
        self._assert_mutation_allowed()
        super().batch_add(*args, **kwargs)
        self._cache = {}
        self._catcache = {}
        self._cache_complete = False

    def do_delete_attribute(self, attr):
        key = attr.db_key
        category = attr.db_category
        self._assert_mutation_allowed(
            expected_generation=attr._backend_generation, key=key, category=category
        )
        section = self._get_section(category)
        if not section:
            return
        section.get(_DATA, {}).pop(key, None)
        section.get(_LOCKS, {}).pop(key, None)
        section.get(_STRV, {}).pop(key, None)
        self._row_state.mark_dirty()
        self._bump_path_generation(key, category)

    def clear_attributes(self, category, accessing_obj, default_access):
        """Guard before the base clear path warms or mutates upper caches."""
        self._assert_mutation_allowed()
        return super().clear_attributes(category, accessing_obj, default_access)

    # ------------------------------------------------------------------
    # Flush (participates in the existing flush_all_dirty() loop)
    # ------------------------------------------------------------------

    def pending_count(self) -> int:
        return 1 if self._dirty else 0

    def dirty_age(self):
        """Seconds this backend has been continuously dirty (0.0 if clean)."""
        if not self._dirty or self._dirty_since is None:
            return 0.0
        return max(0.0, time.monotonic() - self._dirty_since)

    def flush_dirty(self):
        """Write the L1 dict to ``db_attrs`` and save.  Called by the tick.

        Returns:
            FlushResult or None: The flush outcome, or ``None`` when there was
            nothing dirty to flush.
        """
        if not self._dirty:
            return None
        return self._do_flush()

    def _do_flush(self):
        """
        Persist the L1 document to the DB.

        Never marks the backend clean without durability: on repeated DB
        failure the write is diverted to the on-disk spool (durable) before
        the dirty flag is cleared; if even the spool write fails, the backend
        stays dirty so the write is retried rather than lost.

        Returns:
            FlushResult: The outcome (see :class:`FlushResult`).
        """
        return _persist_row_state(self._row_state, allow_spool=True)

    def reset_cache(self):
        """Reload committed state under coordination, or retain dirty/pending state."""
        _require_io_thread("JSONB Attribute cache reset")
        if _has_user_transaction(connections[self._row_state.alias]):
            raise AttributeUpdateUsageError("JSONB Attribute cache reset requires autocommit.")
        active = _ACTIVE_MUTATION.get()
        if active is not None and active.phase != "POSTCOMMIT":
            raise AttributeUpdateUsageError(
                "reset_cache may not run inside a blocking_update callback."
            )
        state = self._row_state
        _ensure_pending_checked(state)
        if state.pending_blocked and not state.sync_failed:
            IAttributeBackend.reset_cache(self)
            return
        with _spool_row_lock(state.key):
            entries = _list_row_entries(state.key)
            if entries:
                state.pending_blocked = True
                _STRONG_ROW_STATES[state.key] = state
                IAttributeBackend.reset_cache(self)
                return
            if state.sync_failed:
                try:
                    _sync_live_backends(state, invalidate=True)
                except Exception as err:
                    _quarantine_cache_sync(state)
                    raise AttributeUpdateUnavailable(
                        "JSONB Attribute cache reconciliation failed."
                    ) from err
                state.pending_blocked = False
                state.sync_failed = False
                if not state.dirty:
                    _STRONG_ROW_STATES.pop(state.key, None)
                return
            if state.pending_blocked or state.dirty:
                IAttributeBackend.reset_cache(self)
                return
            with transaction.atomic(using=state.alias):
                _manager, row = _locked_row_query(state)
                document = row["db_attrs"] or {}
            state.committed_document = deepcopy(document)
            state.durable_document = deepcopy(document)
            state.visible_document = deepcopy(document)
            state.volatile_baseline = deepcopy(document)
            state.mark_clean()
            try:
                _sync_live_backends(state, invalidate=True)
            except Exception as err:
                _quarantine_cache_sync(state)
                raise AttributeUpdateUnavailable(
                    "JSONB Attribute cache reset could not reconcile live handlers."
                ) from err

    def idmapper_trim_cache(self):
        """Drop derived handler caches without reconciling durable row state."""
        _require_io_thread("JSONB idmapper cache trim")
        for backend in list(self._row_state.backends):
            IAttributeBackend.reset_cache(backend)


def _sync_live_backends(state, *, invalidate):
    """Synchronize every same-process handler after an established state change."""
    _require_io_thread("JSONB Attribute cache synchronization")
    if invalidate:
        state.generation += 1
    document = deepcopy(state.visible_document)
    for backend in list(state.backends):
        backend.obj.db_attrs = deepcopy(document)
        IAttributeBackend.reset_cache(backend)
    state.sync_failed = False


def _quarantine_cache_sync(state):
    """Block every live view after a partial cache-adoption failure."""
    state.pending_blocked = True
    state.sync_failed = True
    _STRONG_ROW_STATES[state.key] = state
    for backend in list(state.backends):
        try:
            IAttributeBackend.reset_cache(backend)
        except Exception:
            pass


def _write_locked_document(manager, pk, document):
    """Update exactly one already-serialized row; isolated for failure tests."""
    updated = manager.filter(pk=pk).update(db_attrs=document)
    if updated != 1:
        raise AttributeUpdateUnavailable("Attribute row disappeared during persistence")


def _locked_row_query(state, fields=()):
    """Return the manager/query metadata used inside an active transaction."""
    connection = connections[state.alias]
    if connection.vendor not in ("postgresql", "mysql", "sqlite"):
        raise AttributeUpdateUnavailable(
            f"blocking JSONB updates do not support database vendor {connection.vendor!r}"
        )
    manager = state.model._base_manager.using(state.alias)
    if connection.vendor == "sqlite":
        # SQLite has no row-level SELECT FOR UPDATE. Taking its write lock before
        # the read provides the equivalent serialization at database scope.
        updated = manager.filter(pk=state.pk).update(db_attrs=F("db_attrs"))
        if updated != 1:
            _mark_row_missing(state)
            raise _AttributeRowMissing("Attribute row no longer exists")
    query = manager.select_for_update().filter(pk=state.pk)
    names = ("db_attrs", *fields)
    row = query.values(*names).first()
    if row is None:
        _mark_row_missing(state)
        raise _AttributeRowMissing("Attribute row no longer exists")
    return manager, row


def _persist_row_state(state, *, allow_spool):
    """Persist one shared row state without exposing backend-level races."""
    from evennia.utils import logger

    _require_io_thread("JSONB Attribute persistence")
    active = _ACTIVE_MUTATION.get()
    if active is not None and active.phase != "POSTCOMMIT":
        # Skipping avoids nested-transaction clean lies and cross-process
        # lock-order inversions from persistence barriers inside the callback.
        return FlushResult(
            ok=False,
            error=AttributeUpdateUsageError(
                "JSONB Attribute persistence may not run inside blocking_update."
            ),
        )
    if _has_user_transaction(connections[state.alias]):
        return FlushResult(
            ok=False,
            error=AttributeUpdateUsageError(
                "JSONB Attribute persistence requires ownership of the outer transaction."
            ),
        )
    try:
        _ensure_pending_checked(state)
    except AttributeUpdateError as err:
        return FlushResult(ok=False, error=err)
    if state.pending_blocked:
        return FlushResult(
            ok=False,
            error=AttributeUpdateUnavailable(
                "JSONB Attribute state is quarantined until recovery or process restart."
            ),
        )
    try:
        return _persist_row_state_locked(state, allow_spool=allow_spool)
    except Exception as err:
        logger.log_trace(
            f"JsonbAttributeBackend: could not coordinate persistence for pk={state.pk}."
        )
        return FlushResult(ok=False, error=err)


def _persist_row_state_locked(state, *, allow_spool):
    """Run row persistence, converting expected database failures to outcomes."""
    from evennia.utils import logger

    if not state.dirty:
        return FlushResult(ok=True)
    local = deepcopy(state.visible_document)
    baseline = deepcopy(state.volatile_baseline)
    with _spool_row_lock(state.key):
        try:
            entries = _list_row_entries(state.key)
        except Exception as err:
            return FlushResult(ok=False, error=err)
        if entries:
            if any(payload.get("kind") == "PREPARED_BLOCKING" for _, _, payload in entries):
                state.pending_blocked = True
                _STRONG_ROW_STATES[state.key] = state
                return FlushResult(
                    ok=False,
                    error=AttributeUpdateUnavailable(
                        "A protected Attribute outcome awaits manual resolution."
                    ),
                )
            if allow_spool:
                try:
                    _append_state_delta_locked(state)
                    return FlushResult(ok=False, spooled=True)
                except Exception as err:
                    return FlushResult(ok=False, error=err)
            return FlushResult(
                ok=False,
                error=AttributeUpdateConflict("Durable Attribute deltas are pending."),
            )
        state.pending_blocked = False
        try:
            with transaction.atomic(using=state.alias):
                manager, row = _locked_row_query(state)
                remote = row["db_attrs"] or {}
                final = _three_way_merge(baseline, local, remote)
                _write_locked_document(manager, state.pk, final)
        except JsonbWriteConflict as err:
            logger.log_warn(
                "JsonbAttributeBackend: refusing stale conflicting write for pk=%s." % state.pk
            )
            return FlushResult(ok=False, error=err)
        except Exception as err:
            state.flush_failures += 1
            past_limit = state.flush_failures >= JsonbAttributeBackend._FLUSH_FAIL_LIMIT
            logger.log_trace(
                f"JsonbAttributeBackend flush attempt #{state.flush_failures} failed "
                f"for pk={state.pk}; "
                f"{'diverting to durable spool' if past_limit else 'will retry next tick'}."
            )
            if allow_spool and past_limit:
                try:
                    _append_state_delta_locked(state)
                    return FlushResult(ok=False, spooled=True, error=err)
                except Exception:
                    logger.log_trace("JSONB Attribute spool append also failed")
            return FlushResult(ok=False, error=err)
        state.committed_document = deepcopy(final)
        state.durable_document = deepcopy(final)
        state.visible_document = deepcopy(final)
        state.volatile_baseline = deepcopy(final)
        state.mark_clean()
        try:
            _sync_live_backends(state, invalidate=final != local)
        except Exception as err:
            _quarantine_cache_sync(state)
            return FlushResult(ok=True, error=AttributePostCommitError([err]))
        return FlushResult(ok=True)


@dataclass(frozen=True)
class AttributeEntry:
    """Immutable snapshot returned by :meth:`AttributeMutationView.entries`."""

    key: str
    category: str | None
    value: object


class AttributeMutationView:
    """Isolated, expiring Attribute working set for one protected callback."""

    def __init__(self, document, row):
        self._document = deepcopy(document)
        self._working = {}
        self._active = True
        self._after_commit = []
        self.row = _freeze_snapshot(row)

    def _require_active(self):
        if not self._active:
            raise AttributeUpdateUsageError("The protected Attribute view has expired.")

    @staticmethod
    def _normalize_category(category):
        """Return the canonical category used by every view operation."""
        if category is None:
            return None
        if not isinstance(category, str) or not category.strip():
            raise AttributeUpdateUsageError(
                "Attribute categories must be non-empty strings or None."
            )
        return category.strip().lower()

    @classmethod
    def _normalize(cls, key, category=None):
        if not isinstance(key, str) or not key.strip():
            raise AttributeUpdateUsageError("Attribute keys must be non-empty strings.")
        key = key.strip().lower()
        category = cls._normalize_category(category)
        return key, category

    @staticmethod
    def _cat_key(category):
        return _NULL_CATEGORY if category is None else category

    def _section(self, category, *, create=False):
        cat_key = self._cat_key(category)
        if cat_key not in self._document:
            if not create:
                return None
            self._document[cat_key] = {}
        return self._document[cat_key]

    def has(self, key, category=None):
        """Return whether a transaction-local Attribute exists."""
        self._require_active()
        key, category = self._normalize(key, category)
        marker = self._working.get((key, category), _MISSING)
        if marker is not _MISSING:
            return marker is not _MISSING_DELETED
        section = self._section(category)
        return bool(section and key in section.get(_DATA, {}))

    def get(self, key, default=None, category=None, *, raise_exception=False):
        """Return one shared transaction-local plain working value."""
        self._require_active()
        key, category = self._normalize(key, category)
        cache_key = (key, category)
        marker = self._working.get(cache_key, _MISSING)
        if marker is _MISSING_DELETED:
            if raise_exception:
                raise AttributeError(key)
            return deepcopy(default)
        if marker is not _MISSING:
            return marker
        section = self._section(category)
        if not section or key not in section.get(_DATA, {}):
            if raise_exception:
                raise AttributeError(key)
            return deepcopy(default)
        if key in section.get(_STRV, {}):
            value = deepcopy(section[_STRV][key])
        else:
            value = deepcopy(from_jsonb(section[_DATA][key]))
        self._working[cache_key] = value
        return value

    def add(self, key, value, category=None, *, strattr=False):
        """Create or replace one transaction-local Attribute."""
        self._require_active()
        key, category = self._normalize(key, category)
        section = self._section(category, create=True)
        encoded = to_jsonb(None if strattr else value)
        section.setdefault(_DATA, {})[key] = encoded
        if strattr:
            section.setdefault(_STRV, {})[key] = value
            working_value = deepcopy(value)
        else:
            section.get(_STRV, {}).pop(key, None)
            working_value = from_jsonb(encoded)
        self._working[(key, category)] = working_value

    def batch_add(self, *attributes, strattr=False):
        """Add ``(key, value[, category])`` tuples to the working document."""
        self._require_active()
        for attribute in attributes:
            if not isinstance(attribute, (tuple, list)) or len(attribute) not in (2, 3):
                raise AttributeUpdateUsageError(
                    "batch_add expects (key, value[, category]) tuples."
                )
            category = attribute[2] if len(attribute) == 3 else None
            self.add(attribute[0], attribute[1], category, strattr=strattr)

    def remove(self, key, category=None, *, raise_exception=False):
        """Remove one transaction-local Attribute."""
        self._require_active()
        key, category = self._normalize(key, category)
        section = self._section(category)
        existed = bool(section and key in section.get(_DATA, {}))
        if section:
            section.get(_DATA, {}).pop(key, None)
            section.get(_LOCKS, {}).pop(key, None)
            section.get(_STRV, {}).pop(key, None)
        self._working[(key, category)] = _MISSING_DELETED
        if not existed and raise_exception:
            raise AttributeError(key)

    def clear(self, category=None):
        """Remove normal Attributes in one category, or all normal categories."""
        self._require_active()
        category = self._normalize_category(category)
        categories = (
            [self._cat_key(category)]
            if category is not None
            else [key for key in self._document if not key.startswith("_")]
        )
        for cat_key in categories:
            section = self._document.get(cat_key)
            if not isinstance(section, dict):
                continue
            normalized_category = None if cat_key == _NULL_CATEGORY else cat_key
            for key in list(section.get(_DATA, {})):
                self.remove(key, normalized_category)

    def entries(self, category=None):
        """Return immutable snapshots of transaction-local normal Attributes."""
        self._require_active()
        category = self._normalize_category(category)
        categories = (
            [self._cat_key(category)]
            if category is not None
            else [key for key in self._document if not key.startswith("_")]
        )
        entries = []
        for cat_key in categories:
            section = self._document.get(cat_key, {})
            normalized_category = None if cat_key == _NULL_CATEGORY else cat_key
            for key in sorted(section.get(_DATA, {})):
                value = _freeze_snapshot(self.get(key, category=normalized_category))
                entries.append(AttributeEntry(key, normalized_category, value))
        return tuple(entries)

    def after_commit(self, callback, *args, **kwargs):
        """Queue a best-effort callback after commit and cache synchronization."""
        self._require_active()
        if not callable(callback):
            raise AttributeUpdateUsageError("after_commit requires a callable.")
        if any(
            predicate(callback)
            for predicate in (
                inspect.iscoroutinefunction,
                inspect.isgeneratorfunction,
                inspect.isasyncgenfunction,
            )
        ):
            raise AttributeUpdateUsageError("after_commit callbacks must be synchronous.")
        self._after_commit.append((callback, args, kwargs))

    def _finalize(self):
        self._require_active()
        for (key, category), value in self._working.items():
            if value is _MISSING_DELETED:
                continue
            section = self._section(category, create=True)
            if key in section.get(_STRV, {}):
                section.setdefault(_DATA, {})[key] = to_jsonb(None)
                section[_STRV][key] = value
            else:
                section.setdefault(_DATA, {})[key] = to_jsonb(value)
        return deepcopy(self._document)

    def _expire(self):
        self._active = False


_MISSING_DELETED = object()


def _freeze_snapshot(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_snapshot(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_snapshot(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_snapshot(item) for item in value)
    return deepcopy(value)


def _validate_locked_fields(state, names):
    fields = {field.attname: field for field in state.model._meta.concrete_fields}
    validated = []
    for name in names:
        field = fields.get(name)
        if field is None or field.primary_key or getattr(field, "generated", False):
            raise AttributeUpdateUsageError(
                f"{name!r} is not a readable concrete storage field on {state.model._meta.label}."
            )
        if field.attname == "db_attrs":
            raise AttributeUpdateUsageError("db_attrs is exposed through the Attribute view.")
        validated.append(field.attname)
    return tuple(dict.fromkeys(validated))


def _is_lazy_result(value):
    return (
        inspect.isawaitable(value)
        or inspect.isgenerator(value)
        or inspect.isasyncgen(value)
        or isinstance(value, (Deferred, Future, asyncio.Future, QuerySet))
    )


def _dispose_lazy_result(value):
    """Cancel or close a rejected lazy result without executing its body."""
    if isinstance(value, (Future, asyncio.Future)):
        value.cancel()
    elif isinstance(value, Deferred):
        value.addErrback(lambda _failure: None)
        value.cancel()
    if inspect.iscoroutine(value) or inspect.isgenerator(value):
        value.close()


def _reject_mutator_sql(mutation_context, _execute, _sql, _params, _many, _context):
    """Reject every ORM/database operation attempted by a protected callback."""
    mutation_context.poisoned = True
    raise AttributeUpdateUsageError(
        "blocking_update mutators may not perform database queries or writes."
    )


def _has_user_transaction(connection):
    blocks = getattr(connection, "atomic_blocks", ())
    user_blocks = [block for block in blocks if not getattr(block, "_from_testcase", False)]
    if user_blocks:
        return True
    return not connection.get_autocommit() and not blocks


def _rebase_after_rollback(state, remote, staged):
    state.committed_document = deepcopy(remote)
    state.durable_document = deepcopy(remote)
    state.visible_document = deepcopy(staged)
    state.volatile_baseline = deepcopy(remote)
    state.dirty = staged != remote
    if state.dirty:
        state.dirty_since = state.dirty_since or time.monotonic()
        _STRONG_ROW_STATES[state.key] = state
    else:
        state.dirty_since = None
        if _row_has_pending_entries(state.key, strict=False):
            _STRONG_ROW_STATES[state.key] = state
        else:
            _STRONG_ROW_STATES.pop(state.key, None)
    try:
        _sync_live_backends(state, invalidate=True)
    except Exception as err:
        _quarantine_cache_sync(state)
        raise AttributeUpdateUnavailable(
            "The transaction rolled back but cache reconciliation failed."
        ) from err


def blocking_update(backend, mutator, *, locked_fields=()):
    """Run one synchronous isolated Attribute mutation under row serialization."""
    _require_io_thread("blocking_update")
    if backend._attrtype:
        raise AttributeUpdateUnavailable(
            "blocking_update is supported only by the regular Attribute handler."
        )
    if not callable(mutator):
        raise AttributeUpdateUsageError("blocking_update requires a callable mutator.")
    if any(
        predicate(mutator)
        for predicate in (
            inspect.iscoroutinefunction,
            inspect.isgeneratorfunction,
            inspect.isasyncgenfunction,
        )
    ):
        raise AttributeUpdateUsageError("blocking_update mutators must be synchronous functions.")
    active = _ACTIVE_MUTATION.get()
    if active is not None:
        if active.phase == "MUTATING":
            active.poisoned = True
        raise AttributeUpdateUsageError("blocking_update calls may not be nested.")
    state = backend._row_state
    connection = connections[state.alias]
    if _has_user_transaction(connection):
        raise AttributeUpdateUsageError(
            "blocking_update must own the outer transaction; call it with autocommit enabled."
        )
    _ensure_pending_checked(state)
    if state.pending_blocked:
        raise AttributeUpdateUnavailable(
            "JSONB Attribute state is quarantined until recovery or process restart."
        )
    locked_fields = _validate_locked_fields(state, tuple(locked_fields))
    context = _MutationContext(state.key)
    token = _ACTIVE_MUTATION.set(context)
    view = None
    remote = staged = None
    prepared_path = None
    callback_error = None
    work_error = None
    final = None
    result = None
    try:
        with _spool_row_lock(state.key):
            entries = _list_row_entries(state.key)
            if entries:
                raise AttributeUpdateConflict(
                    "Durable or indeterminate Attribute intent is pending for this row."
                )
            state.pending_blocked = False
            try:
                with transaction.atomic(using=state.alias):
                    manager, row = _locked_row_query(state, locked_fields)
                    remote = row.pop("db_attrs") or {}
                    try:
                        staged = (
                            _three_way_merge(
                                state.volatile_baseline,
                                state.visible_document,
                                remote,
                            )
                            if state.dirty
                            else deepcopy(remote)
                        )
                    except JsonbWriteConflict as err:
                        raise AttributeUpdateConflict(str(err)) from err
                    view = AttributeMutationView(staged, row)
                    try:
                        with ExitStack() as query_guards:
                            for guarded_connection in connections.all():
                                query_guards.enter_context(
                                    guarded_connection.execute_wrapper(
                                        lambda execute, sql, params, many, execute_context: (
                                            _reject_mutator_sql(
                                                context,
                                                execute,
                                                sql,
                                                params,
                                                many,
                                                execute_context,
                                            )
                                        )
                                    )
                                )
                            result = mutator(view)
                        if _is_lazy_result(result):
                            _dispose_lazy_result(result)
                            raise AttributeUpdateUsageError(
                                "blocking_update mutators may not return lazy or asynchronous values."
                            )
                        if context.poisoned:
                            raise AttributeUpdateUsageError(
                                "A nested blocking_update attempt poisoned this mutation."
                            )
                        final = view._finalize()
                    except Exception as err:
                        callback_error = err
                        raise
                    finally:
                        view._expire()
                    if final != remote:
                        prepared_path = _write_spool_payload(
                            state.key, "PREPARED_BLOCKING", remote, final
                        )
                        context.phase = "COMMITTING"
                        try:
                            _write_locked_document(manager, state.pk, final)
                        except Exception as err:
                            work_error = err
                            raise
            except Exception as transaction_error:
                if callback_error is not None:
                    _rebase_after_rollback(state, remote, staged)
                    raise callback_error
                if work_error is not None and transaction_error is work_error:
                    _rebase_after_rollback(state, remote, staged)
                    if prepared_path and os.path.exists(prepared_path):
                        try:
                            os.remove(prepared_path)
                        except OSError as cleanup_error:
                            state.pending_blocked = True
                            raise AttributeUpdateUnavailable(
                                "The update rolled back but its non-replayable witness "
                                "requires cleanup."
                            ) from cleanup_error
                    raise AttributeUpdateUnavailable(
                        "The protected row write failed."
                    ) from work_error
                if prepared_path:
                    _STRONG_ROW_STATES[state.key] = state
                    state.pending_blocked = True
                    state.generation += 1
                    try:
                        _sync_live_backends(state, invalidate=False)
                    except Exception:
                        _quarantine_cache_sync(state)
                    raise AttributeUpdateIndeterminate(
                        "The protected Attribute commit outcome is indeterminate."
                    ) from transaction_error
                if isinstance(transaction_error, AttributeUpdateError):
                    raise
                if remote is not None and staged is not None:
                    _rebase_after_rollback(state, remote, staged)
                raise AttributeUpdateUnavailable(
                    "The protected Attribute transaction could not run safely."
                ) from transaction_error
            context.phase = "SYNCHRONIZING"
            try:
                state.committed_document = deepcopy(final)
                state.durable_document = deepcopy(final)
                state.visible_document = deepcopy(final)
                state.volatile_baseline = deepcopy(final)
                state.mark_clean()
                _sync_live_backends(state, invalidate=True)
            except Exception as err:
                _quarantine_cache_sync(state)
                raise AttributePostCommitError([err]) from err
            cleanup_error = None
            if prepared_path and os.path.exists(prepared_path):
                try:
                    os.remove(prepared_path)
                except OSError as err:
                    cleanup_error = err
                    _STRONG_ROW_STATES[state.key] = state
            state.pending_blocked = cleanup_error is not None
            if cleanup_error is None and not state.dirty:
                _STRONG_ROW_STATES.pop(state.key, None)
            callbacks = list(view._after_commit)
        context.phase = "POSTCOMMIT"
        _ACTIVE_MUTATION.reset(token)
        token = None
        errors = []
        if cleanup_error is not None:
            errors.append(cleanup_error)
        for callback, args, kwargs in callbacks:
            try:
                callback_result = callback(*args, **kwargs)
                if _is_lazy_result(callback_result):
                    _dispose_lazy_result(callback_result)
                    raise AttributeUpdateUsageError(
                        "after_commit callbacks may not return asynchronous or lazy values."
                    )
            except Exception as err:
                errors.append(err)
        if errors:
            raise AttributePostCommitError(errors)
        return result
    finally:
        context.phase = "DONE"
        if view is not None:
            view._expire()
        if token is not None:
            _ACTIVE_MUTATION.reset(token)


# ---------------------------------------------------------------------------
# Public force-flush API
# ---------------------------------------------------------------------------


def force_flush(obj):
    """
    Immediately persist *obj*'s attribute document to DB.

    Use for safety-critical writes (death state, combat end) that must not
    be lost if the server crashes before the next maintenance tick.

    No-op when the JSONB backend is not active or the object has no dirty
    attribute changes.

    Args:
        obj (TypedObject): The object to flush.

    Returns:
        FlushResult: The flush outcome. A falsy result means the write did
        NOT reach durable storage (neither DB nor spool) and safety-critical
        callers should react (retry, alert, refuse to proceed). Returns a
        durable ``FlushResult(ok=True)`` no-op when the backend is inactive or
        there is nothing dirty to flush.
    """
    _require_io_thread("JSONB Attribute force flush")
    try:
        backend = obj.attributes.backend
    except AttributeError:
        return FlushResult(ok=True)
    if isinstance(backend, JsonbAttributeBackend):
        state = backend._row_state
        active = _ACTIVE_MUTATION.get()
        if active is not None and active.phase != "POSTCOMMIT":
            return FlushResult(
                ok=False,
                error=AttributeUpdateUsageError(
                    "force_flush may not run inside a blocking_update callback."
                ),
            )
        if _has_user_transaction(connections[state.alias]):
            return FlushResult(
                ok=False,
                error=AttributeUpdateUsageError("force_flush requires autocommit."),
            )
        try:
            _ensure_pending_checked(state)
        except AttributeUpdateError as err:
            return FlushResult(ok=False, error=err)
        if state.pending_blocked:
            return FlushResult(
                ok=False,
                error=AttributeUpdateUnavailable(
                    "JSONB Attribute state is quarantined until recovery or process restart."
                ),
            )
        try:
            with _spool_row_lock(state.key):
                entries = _list_row_entries(state.key)
                if entries and any(
                    payload.get("kind") == "PREPARED_BLOCKING" for _, _, payload in entries
                ):
                    return FlushResult(
                        ok=False,
                        error=AttributeUpdateUnavailable(
                            "A protected Attribute outcome awaits resolution."
                        ),
                    )
                if entries and not backend._dirty:
                    return FlushResult(ok=False, spooled=True)
        except Exception as err:
            return FlushResult(ok=False, error=err)
        if not backend._dirty:
            return FlushResult(ok=True)
        return backend._do_flush()
    return FlushResult(ok=True)
