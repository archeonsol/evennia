"""IO-owner services for bounded game-state mutations from web surfaces.

These services are frontend-agnostic. Django admin reaches them through
``evennia.web.admin.mixins``; the engine console reaches them through its
panel registry. Nothing here imports ``django.contrib.admin`` -- the one
place that needed it (cascade delete permissions) takes an injected checker
so each caller supplies its own authority model.

See ``docs/source/Components/Web-Mutation-Bridge.md`` for the contract.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any
from uuid import UUID

from django.apps import apps
from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DEFAULT_DB_ALIAS, IntegrityError, router
from django.http import HttpRequest

from evennia.utils import class_from_module, create
from evennia.utils.dbserialize import to_pickle

_MAX_FIELDS = 64
_MAX_RELATIONS = 16
_MAX_RELATED_IDS = 2_000
_MAX_TAGS = 1_000
_MAX_DEPTH = 12
_MAX_ITEMS = 10_000
_MAX_BYTES = 2_000_000
_MAX_ERROR = 500


@dataclass(frozen=True, slots=True)
class FrozenContainer:
    """One recursively frozen built-in container."""

    kind: str
    items: tuple[Any, ...]


@dataclass(frozen=True, slots=True)
class TagDelta:
    """One validated inline Tag mutation."""

    old: tuple[str | None, str | None, str | None, str | None] | None
    new: tuple[str | None, str | None, str | None, str | None] | None


@dataclass(frozen=True, slots=True)
class AdminMutationRequest:
    """Bounded worker-to-owner request for one admin add or change."""

    actor_id: int
    model_label: str
    object_id: int | None
    concrete: tuple[tuple[str, Any], ...]
    relations: tuple[tuple[str, tuple[int, ...]], ...]
    tags: tuple[TagDelta, ...]
    password: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class AdminMutationResult:
    """Plain owner-to-worker mutation outcome."""

    status: str
    object_id: int | None = None
    object_repr: str = ""
    message: str = ""
    retryable: bool = False
    recovery_ids: tuple[int, ...] = ()


@dataclass(frozen=True, slots=True)
class AdminDeleteRequest:
    """Bounded request for single or bulk domain deletion.

    ``authority`` names which cascade-permission model applies. It is a plain
    string rather than a callable so the request stays frozen data that can
    cross the worker-to-owner boundary; the owner resolves it against
    :data:`_CASCADE_CHECKERS`.
    """

    actor_id: int
    model_label: str
    object_ids: tuple[int, ...]
    authority: str = "model_permission"


@dataclass(frozen=True, slots=True)
class AdminDeleteResult:
    """Plain result of owner-side domain deletion."""

    status: str
    deleted_ids: tuple[int, ...] = ()
    vetoed_ids: tuple[int, ...] = ()
    missing_ids: tuple[int, ...] = ()
    failed_ids: tuple[int, ...] = ()
    object_reprs: tuple[tuple[int, str], ...] = ()
    message: str = ""


@dataclass(frozen=True, slots=True)
class _AdminSpec:
    fields: frozenset[str]
    field_types: tuple[tuple[str, tuple[type, ...]], ...]
    foreign_keys: frozenset[str] = frozenset()
    relations: tuple[tuple[str, str], ...] = ()
    tags: bool = False
    typed: bool = False


_SPECS = {
    "accounts.accountdb": _AdminSpec(
        frozenset(
            {
                "username",
                "email",
                "first_name",
                "last_name",
                "last_login",
                "date_joined",
                "is_active",
                "is_staff",
                "is_superuser",
                "db_typeclass_path",
                "db_lock_storage",
                "db_cmdset_storage",
            }
        ),
        field_types=(
            ("username", (str,)),
            ("email", (str,)),
            ("first_name", (str,)),
            ("last_name", (str,)),
            ("last_login", (datetime, type(None))),
            ("date_joined", (datetime,)),
            ("is_active", (bool,)),
            ("is_staff", (bool,)),
            ("is_superuser", (bool,)),
            ("db_typeclass_path", (str, type(None))),
            ("db_lock_storage", (str, type(None))),
            ("db_cmdset_storage", (str, type(None))),
        ),
        relations=(("groups", "auth.group"), ("user_permissions", "auth.permission")),
        tags=True,
        typed=True,
    ),
    "objects.objectdb": _AdminSpec(
        frozenset(
            {
                "db_key",
                "db_typeclass_path",
                "db_lock_storage",
                "db_cmdset_storage",
                "db_location",
                "db_home",
                "db_destination",
                "db_account",
            }
        ),
        field_types=(
            ("db_key", (str, type(None))),
            ("db_typeclass_path", (str, type(None))),
            ("db_lock_storage", (str, type(None))),
            ("db_cmdset_storage", (str, type(None))),
            ("db_location", (int, type(None))),
            ("db_home", (int, type(None))),
            ("db_destination", (int, type(None))),
            ("db_account", (int, type(None))),
        ),
        foreign_keys=frozenset({"db_location", "db_home", "db_destination", "db_account"}),
        tags=True,
        typed=True,
    ),
    "comms.channeldb": _AdminSpec(
        frozenset({"db_key", "db_typeclass_path", "db_lock_storage"}),
        field_types=(
            ("db_key", (str,)),
            ("db_typeclass_path", (str, type(None))),
            ("db_lock_storage", (str, type(None))),
        ),
        relations=(
            ("db_account_subscriptions", "accounts.accountdb"),
            ("db_object_subscriptions", "objects.objectdb"),
        ),
        tags=True,
        typed=True,
    ),
    "scripts.scriptdb": _AdminSpec(
        frozenset(
            {
                "db_key",
                "db_typeclass_path",
                "db_lock_storage",
                "db_persistent",
                "db_obj",
                "db_account",
            }
        ),
        field_types=(
            ("db_key", (str, type(None))),
            ("db_typeclass_path", (str, type(None))),
            ("db_lock_storage", (str, type(None))),
            ("db_persistent", (bool,)),
            ("db_obj", (int, type(None))),
            ("db_account", (int, type(None))),
        ),
        foreign_keys=frozenset({"db_obj", "db_account"}),
        tags=True,
        typed=True,
    ),
    "help.helpentry": _AdminSpec(
        frozenset({"db_key", "db_help_category", "db_entrytext", "db_lock_storage"}),
        field_types=(
            ("db_key", (str,)),
            ("db_help_category", (str,)),
            ("db_entrytext", (str,)),
            ("db_lock_storage", (str, type(None))),
        ),
        tags=True,
    ),
    "comms.msg": _AdminSpec(
        frozenset(
            {
                "db_sender_external",
                "db_receiver_external",
                "db_header",
                "db_message",
                "db_lock_storage",
            }
        ),
        field_types=(
            ("db_sender_external", (str, type(None))),
            ("db_receiver_external", (str, type(None))),
            ("db_header", (str, type(None))),
            ("db_message", (str,)),
            ("db_lock_storage", (str, type(None))),
        ),
        relations=(
            ("db_sender_accounts", "accounts.accountdb"),
            ("db_sender_objects", "objects.objectdb"),
            ("db_sender_scripts", "scripts.scriptdb"),
            ("db_receivers_accounts", "accounts.accountdb"),
            ("db_receivers_objects", "objects.objectdb"),
            ("db_receivers_scripts", "scripts.scriptdb"),
            ("db_hide_from_accounts", "accounts.accountdb"),
            ("db_hide_from_objects", "objects.objectdb"),
        ),
        tags=True,
    ),
    "server.serverconfig": _AdminSpec(
        frozenset({"db_key", "db_value"}),
        field_types=(
            ("db_key", (str,)),
            (
                "db_value",
                (
                    type(None),
                    bool,
                    int,
                    float,
                    str,
                    bytes,
                    date,
                    datetime,
                    time,
                    Decimal,
                    UUID,
                    FrozenContainer,
                ),
            ),
        ),
    ),
}


def writable_models() -> frozenset[str]:
    """Return the model labels with a bounded IO mutation adapter.

    A model absent from this set is readable but not generically writable.
    That is the fail-closed default the mutation bridge requires: adapters are
    registered deliberately, never derived. See ``Web-Mutation-Bridge.md``.

    Returns:
        frozenset[str]: Lowercased ``app_label.modelname`` labels.
    """

    return frozenset(_SPECS)


def admin_spec(model_label: str) -> _AdminSpec:
    """Return one explicit registry entry or reject the model."""
    try:
        return _SPECS[str(model_label).lower()]
    except KeyError as err:
        raise ValueError("This admin model has no IO mutation adapter") from err


class _CodecBudget:
    """Mutable traversal budget local to one payload build."""

    def __init__(self):
        self.items = 0
        self.bytes = 0
        self.active = set()


_SCALAR_TYPES = (
    type(None),
    bool,
    int,
    float,
    str,
    bytes,
    date,
    datetime,
    time,
    Decimal,
    UUID,
)


def freeze_plain(value: Any, *, _budget: _CodecBudget | None = None, _depth: int = 0) -> Any:
    """Freeze exact built-in values without invoking arbitrary object protocols."""
    budget = _budget or _CodecBudget()
    value_type = type(value)
    if value_type in _SCALAR_TYPES:
        if value_type is float and not math.isfinite(value):
            raise ValueError("Non-finite numbers are not accepted")
        if value_type is Decimal and not value.is_finite():
            raise ValueError("Non-finite numbers are not accepted")
        if value_type in (str, bytes):
            budget.bytes += len(value.encode("utf-8") if value_type is str else value)
            if budget.bytes > _MAX_BYTES:
                raise ValueError("Admin payload is too large")
        return value
    if value_type not in (list, tuple, set, frozenset, dict):
        raise ValueError("Admin payload contains an unsupported value")
    if _depth >= _MAX_DEPTH:
        raise ValueError("Admin payload is nested too deeply")
    identity = id(value)
    if identity in budget.active:
        raise ValueError("Admin payload contains a cycle")
    budget.active.add(identity)
    try:
        if value_type is dict:
            budget.items += len(value)
            if budget.items > _MAX_ITEMS:
                raise ValueError("Admin payload contains too many values")
            items = tuple(
                (
                    freeze_plain(key, _budget=budget, _depth=_depth + 1),
                    freeze_plain(item, _budget=budget, _depth=_depth + 1),
                )
                for key, item in dict.items(value)
            )
            return FrozenContainer("dict", items)
        budget.items += len(value)
        if budget.items > _MAX_ITEMS:
            raise ValueError("Admin payload contains too many values")
        iterator = (
            list.__iter__(value)
            if value_type is list
            else (
                tuple.__iter__(value)
                if value_type is tuple
                else (set.__iter__(value) if value_type is set else frozenset.__iter__(value))
            )
        )
        kind = {list: "list", tuple: "tuple", set: "set", frozenset: "frozenset"}[value_type]
        return FrozenContainer(
            kind,
            tuple(freeze_plain(item, _budget=budget, _depth=_depth + 1) for item in iterator),
        )
    finally:
        budget.active.remove(identity)


def thaw_plain(value: Any) -> Any:
    """Restore one trusted value produced by :func:`freeze_plain`."""
    if not isinstance(value, FrozenContainer):
        return value
    if value.kind == "dict":
        return {thaw_plain(key): thaw_plain(item) for key, item in value.items}
    items = [thaw_plain(item) for item in value.items]
    if value.kind == "list":
        return items
    if value.kind == "tuple":
        return tuple(items)
    if value.kind == "set":
        return set(items)
    if value.kind == "frozenset":
        return frozenset(items)
    raise ValueError("Unknown frozen-container kind")


def _validate_frozen(value: Any, budget: _CodecBudget, depth: int = 0) -> None:
    """Validate one frozen value without invoking attacker-controlled protocols."""
    value_type = type(value)
    if value_type in _SCALAR_TYPES:
        freeze_plain(value, _budget=budget, _depth=depth)
        return
    if value_type is not FrozenContainer or type(value.kind) is not str:
        raise ValueError("Admin payload contains an unsupported value")
    if depth >= _MAX_DEPTH or type(value.items) is not tuple:
        raise ValueError("Admin payload has an invalid frozen container")
    if value.kind not in ("dict", "list", "tuple", "set", "frozenset"):
        raise ValueError("Unknown frozen-container kind")
    budget.items += len(value.items)
    if budget.items > _MAX_ITEMS:
        raise ValueError("Admin payload contains too many values")
    if value.kind == "dict":
        for item in value.items:
            if type(item) is not tuple or len(item) != 2:
                raise ValueError("Admin payload has an invalid frozen mapping")
            _validate_frozen(item[0], budget, depth + 1)
            _validate_frozen(item[1], budget, depth + 1)
        return
    for item in value.items:
        _validate_frozen(item, budget, depth + 1)


def _validate_mutation_request(request: AdminMutationRequest) -> _AdminSpec:
    """Reject malformed protocol data before actor or target SQL."""
    if type(request) is not AdminMutationRequest:
        raise ValueError("Invalid admin request type")
    if type(request.actor_id) is not int or request.actor_id <= 0:
        raise ValueError("Invalid admin actor")
    if type(request.model_label) is not str:
        raise ValueError("Invalid admin model")
    spec = admin_spec(request.model_label)
    if request.object_id is not None and (
        type(request.object_id) is not int or request.object_id <= 0
    ):
        raise ValueError("Invalid admin target")
    if type(request.password) not in (str, type(None)):
        raise ValueError("Invalid admin secret")
    if request.password is not None and len(request.password.encode("utf-8")) > _MAX_BYTES:
        raise ValueError("Admin secret is too large")
    if type(request.concrete) is not tuple or len(request.concrete) > _MAX_FIELDS:
        raise ValueError("Admin payload has invalid concrete fields")
    field_names = []
    field_types = dict(spec.field_types)
    budget = _CodecBudget()
    for item in request.concrete:
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
            raise ValueError("Admin payload has an invalid concrete field")
        name, value = item
        if name not in spec.fields:
            raise ValueError("Unknown admin field")
        _validate_frozen(value, budget)
        if type(value) not in field_types[name]:
            raise ValueError("Admin field has an invalid value type")
        if name in spec.foreign_keys:
            thawed = thaw_plain(value)
            if thawed is not None and type(thawed) is not int:
                raise ValueError("Invalid foreign-key value")
        field_names.append(name)
    if len(set(field_names)) != len(field_names):
        raise ValueError("Admin payload repeats a concrete field")
    relation_specs = dict(spec.relations)
    if type(request.relations) is not tuple or len(request.relations) > _MAX_RELATIONS:
        raise ValueError("Admin payload has invalid relations")
    relation_names = []
    for item in request.relations:
        if type(item) is not tuple or len(item) != 2 or type(item[0]) is not str:
            raise ValueError("Admin payload has an invalid relation")
        name, ids = item
        if name not in relation_specs or type(ids) is not tuple:
            raise ValueError("Unknown admin relation")
        if len(ids) > _MAX_RELATED_IDS or any(
            type(value) is not int or value <= 0 for value in ids
        ):
            raise ValueError("Admin relation has invalid identifiers")
        relation_names.append(name)
    if len(set(relation_names)) != len(relation_names):
        raise ValueError("Admin payload repeats a relation")
    if type(request.tags) is not tuple:
        raise ValueError("Admin payload has invalid Tag operations")
    _validate_tags(request.tags)
    return spec


def _model(model_label: str):
    """Resolve one allowlisted concrete model."""
    admin_spec(model_label)
    return apps.get_model(model_label)


def _bounded_message(error: BaseException) -> str:
    """Return non-secret, bounded error classification text."""
    creation_outcome = getattr(error, "account_creation_outcome", None)
    if creation_outcome is not None and creation_outcome.issues:
        issue = creation_outcome.issues[0]
        return f"Account creation stopped at {issue.stage} ({issue.code})."[:_MAX_ERROR]
    if isinstance(error, ValidationError):
        return "The submitted values no longer validate."
    if isinstance(error, ValueError):
        return str(error)[:_MAX_ERROR]
    if isinstance(error, PermissionDenied):
        return "Admin authorization was revoked."
    return f"{type(error).__name__}: owner mutation requires review"[:_MAX_ERROR]


def _fresh_actor(actor_id: int, model, action: str):
    """Resolve an admin actor after checking fresh concrete and permission state."""
    AccountDB = apps.get_model("accounts", "AccountDB")
    fresh = (
        AccountDB.objects.filter(pk=int(actor_id))
        .values("is_active", "is_staff", "is_superuser")
        .first()
    )
    if not fresh or not fresh["is_active"] or not fresh["is_staff"]:
        raise PermissionDenied("Admin access was revoked")
    actor = AccountDB.objects.get(pk=int(actor_id))
    actor.is_active = fresh["is_active"]
    actor.is_staff = fresh["is_staff"]
    actor.is_superuser = fresh["is_superuser"]
    for cache_name in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
        actor.__dict__.pop(cache_name, None)
    codename = f"{model._meta.app_label}.{action}_{model._meta.model_name}"
    if not actor.has_perm(codename):
        raise PermissionDenied("Admin permission was revoked")
    return actor


class _CreationRecorder:
    """Keep exact references to models constructed by one adapter."""

    def __init__(self):
        self.instances = []

    def _record(self, instance):
        if instance is not None and all(item is not instance for item in self.instances):
            self.instances.append(instance)

    record_object = _record
    record_channel = _record
    record_script = _record
    record_message = _record
    record_help_entry = _record

    @property
    def ids(self):
        return tuple(int(item.pk) for item in self.instances if item.pk is not None)


def _resolve_fk_values(model, values):
    """Decode structurally validated concrete values without database access."""
    resolved = {}
    spec = admin_spec(model._meta.label_lower)
    for name, value in values.items():
        if name not in spec.fields:
            raise ValueError("Unknown admin field")
        field_object = model._meta.get_field(name)
        target_value = field_object.to_python(thaw_plain(value))
        if name in spec.foreign_keys:
            resolved[field_object.attname] = target_value
        else:
            resolved[field_object.attname] = target_value
    return resolved


def _validate_fk_targets(model, values):
    """Check related-row existence after fresh actor authorization."""
    spec = admin_spec(model._meta.label_lower)
    for name in spec.foreign_keys:
        field_object = model._meta.get_field(name)
        target_id = values.get(field_object.attname)
        if (
            target_id is not None
            and not field_object.remote_field.model.objects.filter(pk=target_id).exists()
        ):
            raise ValidationError({name: "The related row no longer exists."})


def _resolve_relations(spec, relations):
    """Validate every related ID before the first write."""
    relation_specs = dict(spec.relations)
    resolved = {}
    for name, ids in relations.items():
        try:
            related_model = apps.get_model(relation_specs[name])
        except KeyError as err:
            raise ValueError("Unknown admin relation") from err
        wanted = tuple(dict.fromkeys(ids))
        if len(wanted) > _MAX_RELATED_IDS:
            raise ValueError("Admin relation contains too many rows")
        found = related_model.objects.in_bulk(wanted)
        if set(found) != set(wanted):
            raise ValidationError({name: "A related row no longer exists."})
        resolved[name] = tuple(found[value] for value in wanted)
    return resolved


def _preflight_concrete(model, object_id, values):
    """Run uniqueness and field validation on an uncached candidate."""
    if object_id is None:
        candidate = model(**values)
    else:
        current = model.objects.filter(pk=object_id).values().first()
        if current is None:
            raise model.DoesNotExist
        candidate = model(**current)
        candidate._state.adding = False
        candidate._state.db = DEFAULT_DB_ALIAS
        for name, value in values.items():
            setattr(candidate, name, value)
    supplied = set(values)
    exclude = [
        field.name
        for field in model._meta.fields
        if (field.attname not in supplied or (field.null and values.get(field.attname) is None))
        and not field.primary_key
    ]
    candidate.clean_fields(exclude=exclude)
    candidate.clean()
    candidate.validate_unique()
    candidate.validate_constraints()


def _handler_for_tag(obj, tag_type):
    if tag_type == "alias":
        return obj.aliases
    if tag_type == "permission":
        return obj.permissions
    return obj.tags


def _apply_tags(obj, deltas):
    """Apply validated Tag deltas only through live owner handlers."""
    for delta in deltas:
        if delta.old:
            key, category, tag_type, _data = delta.old
            _handler_for_tag(obj, tag_type).remove(key, category=category)
        if delta.new:
            key, category, tag_type, data = delta.new
            _handler_for_tag(obj, tag_type).add(key, category=category, data=data)


def _validate_tags(deltas):
    """Reject malformed inline operations before the first owner write."""
    if len(deltas) > _MAX_TAGS:
        raise ValueError("Admin payload contains too many Tag operations")
    total_bytes = 0
    for delta in deltas:
        if type(delta) is not TagDelta or (delta.old is None and delta.new is None):
            raise ValueError("Malformed Tag operation")
        for value in (delta.old, delta.new):
            if value is None:
                continue
            if type(value) is not tuple or len(value) != 4:
                raise ValueError("Malformed Tag value")
            if any(type(item) not in (str, type(None)) for item in value):
                raise ValueError("Malformed Tag scalar")
            total_bytes += sum(len(item.encode("utf-8")) for item in value if item is not None)
            if total_bytes > _MAX_BYTES:
                raise ValueError("Admin Tag payload is too large")
            if value[2] not in (None, "alias", "permission"):
                raise ValueError("Unknown Tag handler type")


def _apply_relations(obj, relations):
    """Apply model-specific owner relation operations."""
    label = obj._meta.label_lower
    if label == "comms.msg":
        _apply_message_relations(obj, relations)
        return
    if label == "comms.channeldb":
        for name, desired in relations.items():
            manager = getattr(obj, name)
            current = tuple(manager.all())
            desired_ids = {item.pk for item in desired}
            remove = [item for item in current if item.pk not in desired_ids]
            current_ids = {item.pk for item in current}
            add = [item for item in desired if item.pk not in current_ids]
            if remove:
                obj.subscriptions.remove(remove)
            if add:
                obj.subscriptions.add(add)
        return
    for name, desired in relations.items():
        getattr(obj, name).set(desired)
    if label == "accounts.accountdb":
        for cache_name in ("_perm_cache", "_user_perm_cache", "_group_perm_cache"):
            obj.__dict__.pop(cache_name, None)


def _principal_refs_for(items):
    """Return the authority identities represented by model participants."""
    from evennia.authorization.storage import principal_refs

    return {principal_ref for item in items for principal_ref in principal_refs(item)}


def _apply_message_relations(obj, relations):
    """Reconcile participant M2Ms and their resource-scoped capabilities."""
    from evennia.authorization.storage import grant_capability, revoke_grant
    from evennia.server.models import AuthorizationGrant

    sender_names = (
        "db_sender_accounts",
        "db_sender_objects",
        "db_sender_scripts",
    )
    receiver_names = (
        "db_receivers_accounts",
        "db_receivers_objects",
        "db_receivers_scripts",
    )
    participant_names = sender_names + receiver_names
    current = {name: tuple(getattr(obj, name).all()) for name in participant_names}
    desired = {name: relations.get(name, current[name]) for name in participant_names}
    current_senders = _principal_refs_for(item for name in sender_names for item in current[name])
    desired_senders = _principal_refs_for(item for name in sender_names for item in desired[name])
    current_participants = _principal_refs_for(
        item for name in participant_names for item in current[name]
    )
    desired_participants = _principal_refs_for(
        item for name in participant_names for item in desired[name]
    )
    message_ref = obj.authorization_resource_ref()
    stale = {
        "engine.message.read": current_participants - desired_participants,
        "engine.message.edit": current_senders - desired_senders,
        "engine.message.delete": current_senders - desired_senders,
    }
    for capability, principal_refs in stale.items():
        grants = AuthorizationGrant.objects.filter(
            principal_ref__in=principal_refs,
            capability=capability,
            scope_kind="resource",
            scope_key=message_ref,
            revoked_at__isnull=True,
            provenance__in=("message_participant", "message_sender"),
        ).values_list("grant_id", flat=True)
        for grant_id in tuple(grants):
            revoke_grant(grant_id, reason="admin message participant changed")
    for name, desired_items in relations.items():
        getattr(obj, name).set(desired_items)

    def ensure_grant(principal_ref, capability, provenance):
        existing = AuthorizationGrant.objects.filter(
            principal_ref=principal_ref,
            capability=capability,
            scope_kind="resource",
            scope_key=message_ref,
            revoked_at__isnull=True,
        ).first()
        if existing is not None and existing.provenance not in (
            "message_participant",
            "message_sender",
        ):
            return
        grant_capability(
            principal_ref,
            capability,
            scope_kind="resource",
            scope_key=message_ref,
            provenance=provenance,
        )

    for principal_ref in desired_participants:
        ensure_grant(principal_ref, "engine.message.read", "message_participant")
    for principal_ref in desired_senders:
        for capability in ("engine.message.edit", "engine.message.delete"):
            ensure_grant(principal_ref, capability, "message_sender")


def _create_admin_object(request, model, values, relations, recorder):
    """Create one model through its established lifecycle adapter."""
    label = request.model_label
    if label == "accounts.accountdb":
        typeclass_path = values.get("db_typeclass_path", settings.BASE_ACCOUNT_TYPECLASS)
        Account = class_from_module(typeclass_path)
        outcome = Account.create_with_provenance(
            username=values.get("username", ""),
            email=values.get("email", ""),
            password=request.password,
            typeclass=typeclass_path,
        )
        for item in (*outcome.accounts, *outcome.objects):
            recorder._record(item)
        if outcome.disposition != "created" or outcome.account is None:
            error = ValidationError("Account creation did not complete")
            error.account_creation_outcome = outcome
            raise error
        return outcome.account, outcome
    if label == "objects.objectdb":
        ObjectDB = apps.get_model("objects", "ObjectDB")

        def object_or_none(field_name):
            object_id = values.get(field_name)
            return ObjectDB.objects.get(pk=object_id) if object_id is not None else None

        return (
            create.create_object(
                typeclass=values.get("db_typeclass_path", settings.BASE_OBJECT_TYPECLASS),
                key=values.get("db_key", ""),
                location=object_or_none("db_location_id"),
                home=object_or_none("db_home_id"),
                destination=object_or_none("db_destination_id"),
                nohome=values.get("db_home_id") is None,
                _creation_recorder=recorder,
            ),
            None,
        )
    if label == "comms.channeldb":
        return (
            model.objects.create_channel(
                key=values.get("db_key", ""),
                typeclass=values.get("db_typeclass_path", settings.BASE_CHANNEL_TYPECLASS),
                _creation_recorder=recorder,
            ),
            None,
        )
    if label == "scripts.scriptdb":
        ObjectDB = apps.get_model("objects", "ObjectDB")
        AccountDB = apps.get_model("accounts", "AccountDB")
        object_id = values.get("db_obj_id")
        account_id = values.get("db_account_id")
        return (
            model.objects.create_script(
                typeclass=values.get("db_typeclass_path", settings.BASE_SCRIPT_TYPECLASS),
                key=values.get("db_key"),
                obj=ObjectDB.objects.get(pk=object_id) if object_id is not None else None,
                account=(AccountDB.objects.get(pk=account_id) if account_id is not None else None),
                persistent=values.get("db_persistent"),
                _creation_recorder=recorder,
            ),
            None,
        )
    if label == "help.helpentry":
        return (
            model.objects.create_help(
                values.get("db_key", ""),
                values.get("db_entrytext", ""),
                category=values.get("db_help_category", "General"),
                _creation_recorder=recorder,
            ),
            None,
        )
    if label == "comms.msg":
        senders = []
        receivers = []
        for name in ("db_sender_accounts", "db_sender_objects", "db_sender_scripts"):
            senders.extend(relations.get(name, ()))
        if values.get("db_sender_external"):
            senders.append(values["db_sender_external"])
        for name in (
            "db_receivers_accounts",
            "db_receivers_objects",
            "db_receivers_scripts",
        ):
            receivers.extend(relations.get(name, ()))
        if values.get("db_receiver_external"):
            receivers.append(values["db_receiver_external"])
        return (
            model.objects.create_message(
                senders,
                values.get("db_message", ""),
                receivers=receivers,
                header=values.get("db_header"),
                _creation_recorder=recorder,
            ),
            None,
        )
    if label == "server.serverconfig":
        config = model(
            db_key=values.get("db_key"),
            db_value=to_pickle(values.get("db_value")),
        )
        recorder._record(config)
        config.save()
        return config, None
    raise ValueError("No creation adapter is registered")


def _compensate_account(outcome):
    """Run exact Account/Character compensation when provenance is available."""
    if outcome is None:
        return None
    from evennia.accounts.creation import compensate_account_creation

    cleanup = compensate_account_creation(outcome)
    return cleanup


def _reconcile(model, ids):
    """Refresh canonical rows and handler caches after a partial result."""
    durable = []
    for object_id in ids:
        try:
            if not model.objects.filter(pk=object_id).exists():
                continue
            durable.append(int(object_id))
            obj = model.objects.get(pk=object_id)
        except Exception:
            continue
        try:
            obj.refresh_from_db()
            for handler_name in ("attributes", "nicks", "tags", "aliases", "permissions"):
                handler = obj.__dict__.get(handler_name)
                reset = getattr(handler, "reset_cache", None)
                if callable(reset):
                    reset()
            if hasattr(obj, "at_post_load"):
                obj.at_post_load()
        except Exception:
            continue
    return tuple(durable)


def _reject_non_superuser_authority_change(model, object_id, values, relations):
    """Allow ordinary Account edits while reserving authority changes."""
    authority_fields = {"is_staff", "is_superuser"} & set(values)
    authority_relations = {"groups", "user_permissions"} & set(relations)
    if not authority_fields and not authority_relations:
        return
    if object_id is None:
        changes_authority = any(bool(values[name]) for name in authority_fields) or any(
            relations[name] for name in authority_relations
        )
    else:
        current = model.objects.filter(pk=object_id).values(*authority_fields).first()
        if current is None:
            raise model.DoesNotExist
        changes_authority = any(current[name] != values[name] for name in authority_fields)
        current_account = model.objects.get(pk=object_id) if authority_relations else None
        for name in authority_relations:
            current_ids = set(getattr(current_account, name).values_list("pk", flat=True))
            desired_ids = {item.pk for item in relations[name]}
            changes_authority = changes_authority or current_ids != desired_ids
    if changes_authority:
        raise PermissionDenied("Only a superuser may change admin authority")


def mutate_admin(request: AdminMutationRequest) -> AdminMutationResult:
    """Execute one complete add/change operation on the IO owner."""
    try:
        spec = _validate_mutation_request(request)
        model = _model(request.model_label)
        values = _resolve_fk_values(model, {name: value for name, value in request.concrete})
    except (TypeError, ValidationError, ValueError) as err:
        return AdminMutationResult("conflict", message=_bounded_message(err))
    action = "add" if request.object_id is None else "change"
    actor = _fresh_actor(request.actor_id, model, action)
    try:
        _validate_fk_targets(model, values)
        relations = _resolve_relations(spec, dict(request.relations))
        if request.tags and not spec.tags:
            raise ValueError("Tags are not supported by this adapter")
        if request.model_label == "accounts.accountdb" and not actor.is_superuser:
            _reject_non_superuser_authority_change(model, request.object_id, values, relations)
        _preflight_concrete(model, request.object_id, values)
    except model.DoesNotExist:
        return AdminMutationResult("missing", message="The target no longer exists.")
    except PermissionDenied:
        raise
    except (IntegrityError, ValidationError, ValueError) as err:
        return AdminMutationResult("conflict", message=_bounded_message(err))
    recorder = _CreationRecorder()
    creation_outcome = None
    mutation_started = False
    obj = None
    try:
        if request.object_id is None:
            obj, creation_outcome = _create_admin_object(
                request, model, values, relations, recorder
            )
            mutation_started = bool(recorder.ids or getattr(obj, "pk", None))
            if obj is None:
                raise RuntimeError("Creation helper returned no object")
        else:
            obj = model.objects.get(pk=request.object_id)
        old_typeclass = getattr(obj, "db_typeclass_path", None)
        for name, value in values.items():
            setattr(obj, name, value)
        new_typeclass = getattr(obj, "db_typeclass_path", None)
        typeclass_changed = bool(
            spec.typed and old_typeclass and new_typeclass and old_typeclass != new_typeclass
        )
        if typeclass_changed:
            obj.set_class_from_typeclass(new_typeclass)
        obj.save()
        mutation_started = True
        _apply_relations(obj, relations)
        _apply_tags(obj, request.tags)
        if request.object_id is not None and (
            typeclass_changed or request.model_label in ("objects.objectdb", "comms.channeldb")
        ):
            obj.at_post_load()
        return AdminMutationResult(
            "created" if request.object_id is None else "changed",
            object_id=int(obj.pk),
            object_repr=str(obj)[:250],
        )
    except PermissionDenied:
        raise
    except Exception as err:
        creation_outcome = getattr(err, "account_creation_outcome", creation_outcome)
        candidate_ids = recorder.ids or ((int(request.object_id),) if request.object_id else ())
        if not mutation_started and not candidate_ids:
            disposition = getattr(creation_outcome, "disposition", "")
            if disposition == "rejected" or (
                creation_outcome is None
                and isinstance(err, (IntegrityError, ValidationError, ValueError))
            ):
                return AdminMutationResult("conflict", message=_bounded_message(err))
            return AdminMutationResult("fault", message=_bounded_message(err), retryable=True)
        cleanup = _compensate_account(creation_outcome)
        if creation_outcome is not None:
            candidate_ids = cleanup.survivor_account_ids
        durable = _reconcile(model, candidate_ids)
        recovery_ids = durable
        if cleanup is not None and cleanup.survivor_object_ids:
            ObjectDB = apps.get_model("objects", "ObjectDB")
            durable_objects = _reconcile(ObjectDB, cleanup.survivor_object_ids)
            recovery_ids += durable_objects
        return AdminMutationResult(
            "recovery_required" if recovery_ids else "partial",
            object_id=durable[0] if len(durable) == 1 else None,
            message=_bounded_message(err),
            recovery_ids=recovery_ids,
        )


def _check_cascade_by_model_permission(actor, related_model, instances):
    """Require Django's per-model delete permission for one cascade branch.

    The frontend-neutral default. It asks only whether the actor may delete
    this model at all, which is the weakest claim every caller can make.
    """
    codename = f"{related_model._meta.app_label}.delete_{related_model._meta.model_name}"
    if not actor.has_perm(codename):
        raise PermissionDenied("Cascade delete permission was revoked")


def _check_cascade_by_django_admin(actor, related_model, instances):
    """Defer to a registered ``ModelAdmin.has_delete_permission`` per row.

    Preserves Django admin's exact historical behavior for the admin frontend:
    a registered admin decides per instance, and an unregistered model falls
    back to the plain model permission.
    """
    from django.contrib import admin

    model_admin = admin.site._registry.get(related_model)
    if model_admin is None:
        _check_cascade_by_model_permission(actor, related_model, instances)
        return
    request = HttpRequest()
    request.user = actor
    for instance in instances:
        if not model_admin.has_delete_permission(request, instance):
            raise PermissionDenied("Cascade delete permission was revoked")


#: Cascade-permission strategies by ``AdminDeleteRequest.authority``. Frontends
#: pick one; nothing here imports the admin site unless the admin asks for it.
_CASCADE_CHECKERS = {
    "model_permission": _check_cascade_by_model_permission,
    "django_admin": _check_cascade_by_django_admin,
}


def _cascade_checker(authority):
    """Return one registered cascade checker or fail closed."""
    try:
        return _CASCADE_CHECKERS[authority]
    except KeyError as err:
        raise ValueError("Unknown cascade authority") from err


def _preflight_delete(actor, obj, authority="model_permission"):
    """Recompute protected rows and cascade permissions on the owner.

    ``NestedObjects`` is imported here rather than at module scope: importing
    ``django.contrib.admin.utils`` pulls in ContentType, which is too early
    during app loading and would make this module unimportable from an
    ``AppConfig.ready()``. It is also the last admin-package dependency in the
    services layer, so keeping it lazy keeps the import graph honest about the
    fact that these services do not belong to the admin.
    """
    from django.contrib.admin.utils import NestedObjects

    check = _cascade_checker(authority)
    using = router.db_for_write(obj.__class__, instance=obj)
    collector = NestedObjects(using=using)
    collector.collect([obj])
    if collector.protected:
        raise ValidationError("Deletion is protected by related rows")
    for related_model, instances in collector.model_objs.items():
        check(actor, related_model, instances)


def _concrete_object_repr(obj):
    """Build bounded audit text without invoking a typeclass string hook."""
    for name in ("username", "db_key"):
        try:
            field_object = obj._meta.get_field(name)
        except Exception:
            continue
        value = obj.__dict__.get(field_object.attname)
        if value not in (None, ""):
            return f"{obj._meta.verbose_name} '{value}'"[:200]
    return f"{obj._meta.verbose_name} #{obj.pk}"[:200]


def delete_admin(request: AdminDeleteRequest) -> AdminDeleteResult:
    """Run deterministic domain deletes without an outer transaction."""
    if type(request) is not AdminDeleteRequest:
        raise ValueError("Invalid admin delete request")
    if type(request.actor_id) is not int or request.actor_id <= 0:
        raise ValueError("Invalid admin actor")
    if type(request.model_label) is not str:
        raise ValueError("Invalid admin model")
    if type(request.object_ids) is not tuple or any(
        type(value) is not int or value <= 0 for value in request.object_ids
    ):
        raise ValueError("Invalid admin delete identifiers")
    _cascade_checker(request.authority)
    model = _model(request.model_label)
    if len(request.object_ids) > _MAX_RELATED_IDS:
        raise ValueError("Too many rows were selected")
    actor = _fresh_actor(request.actor_id, model, "delete")
    deleted = []
    vetoed = []
    missing = []
    failed = []
    object_reprs = []
    targets = []
    for object_id in tuple(dict.fromkeys(request.object_ids)):
        try:
            obj = model.objects.get(pk=object_id)
        except model.DoesNotExist:
            missing.append(object_id)
            continue
        try:
            _preflight_delete(actor, obj, request.authority)
        except ValidationError:
            vetoed.append(object_id)
            continue
        object_reprs.append((object_id, _concrete_object_repr(obj)))
        targets.append((object_id, obj))
    for object_id, obj in targets:
        try:
            obj.delete()
        except Exception:
            try:
                exists = model.objects.filter(pk=object_id).exists()
            except Exception:
                failed.append(object_id)
            else:
                if exists:
                    failed.append(object_id)
                    _reconcile(model, (object_id,))
                else:
                    deleted.append(object_id)
            continue
        try:
            exists = model.objects.filter(pk=object_id).exists()
        except Exception:
            failed.append(object_id)
        else:
            if exists:
                vetoed.append(object_id)
            else:
                deleted.append(object_id)
    status = "deleted" if not vetoed and not missing and not failed else "partial"
    return AdminDeleteResult(
        status,
        deleted_ids=tuple(deleted),
        vetoed_ids=tuple(vetoed),
        missing_ids=tuple(missing),
        failed_ids=tuple(failed),
        object_reprs=tuple(object_reprs),
        message=("Deletion completed." if status == "deleted" else "Deletion completed partially."),
    )


__all__ = (
    "AdminDeleteRequest",
    "AdminDeleteResult",
    "AdminMutationRequest",
    "AdminMutationResult",
    "TagDelta",
    "admin_spec",
    "delete_admin",
    "freeze_plain",
    "mutate_admin",
)
