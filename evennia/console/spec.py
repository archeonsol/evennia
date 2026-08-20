"""Runtime introspection for the console.

Django admin builds its UI from ``Model._meta``. That reflects the storage
layer, which in this engine is no longer where the interesting structure lives:
attributes are a JSONB document rather than rows, Script is storage-only, and
authority is capabilities rather than lock strings. This module reflects the
*runtime* instead, and the model half below is only its first slice.

Everything here is read-only, pure, and safe on a web worker: it inspects
classes and the app registry, never instances, so it holds to the boundary
rules in ``docs/source/Components/Web-IO-Boundary.md`` without needing the IO
owner at all. That is what lets the Records, Migrations, and Settings panels
keep working in degraded mode when the game server is down.

"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from django.apps import apps

from evennia.console.services import writable_models

#: Models mutated only through their own domain service, never generically.
#:
#: These are not "not yet adapted" -- each has a real reason a generic writer
#: would corrupt something: a hash chain, a cache-generation contract, an
#: append-only guarantee, or a shared row cached by many owners. The console
#: surfaces them as read plus domain actions. See decision D3 in
#: ``.agents/prompts/W1-console-implementation-plan.md``.
DOMAIN_OWNED = {
    "server.sanction": (
        "evennia.moderation.sanctions.issue_sanction / revoke_sanction "
        "(a generic write breaks the tamper-evident hash chain)"
    ),
    "server.sanctionhit": "written by enforcement as it happens; never edited",
    "server.moderationflag": "evennia.moderation.flags.raise_flag / resolve_flag",
    "server.sessionrecord": "evennia.moderation.capture; a connection record is not editable",
    "server.authorizationgrant": (
        "the authorization service (grants serialize on a per-principal row and "
        "cascade revocation through delegation descendants)"
    ),
    "server.authorizationscopelabel": "the authorization service; materialized from policy",
    "server.authorizationpolicyoverride": "the authorization service; typed policy nodes only",
    "server.authorizationprincipalstate": "the authorization service",
    "server.authorizationauditevent": "append-only by definition",
    "server.gameevent": "evennia.eventbus.emit",
    "server.enginejob": "evennia.jobs.queue; use the queue's own requeue and dead-letter paths",
    "console.consoleerrorstate": (
        "the errors panel's own review action; a judgement is recorded, not edited"
    ),
    "console.consoleauditevent": (
        "append-only; the console has no write path to its own audit trail "
        "because under one capability that trail is the only internal control"
    ),
    "typeclasses.tag": (
        "one shared Tag row may be cached by many owners; edit tags through the "
        "owning object's handler, not the table (see Web-Mutation-Bridge.md)"
    ),
}


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """One concrete field on a model.

    Attributes:
        name: Field attribute name.
        kind: Django field class name, e.g. ``CharField``.
        primary_key: Whether this is the primary key.
        null: Whether the column is nullable.
        blank: Whether forms may leave it empty.
        editable: Whether Django considers the field editable.
        relation: Related model label for FK/M2M fields, else ``""``.
        choices: Available ``(value, label)`` pairs, if the field is a choice.
    """

    name: str
    kind: str
    primary_key: bool = False
    null: bool = False
    blank: bool = False
    editable: bool = True
    relation: str = ""
    choices: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """One installed model as the console sees it.

    Attributes:
        label: Lowercased ``app_label.modelname``.
        app_label: Django app label.
        model_name: Lowercased model name.
        verbose_name: Human-readable singular name.
        verbose_name_plural: Human-readable plural name.
        proxy: Whether this is a Django proxy model. Proxies share their
            concrete model's table, so listing them alongside it shows one
            table many times and makes navigation worse, not richer.
        concrete_label: The label of the model that owns the table.
        storage: ``"idmapper"`` for identity-cached typeclass models,
            ``"plain"`` for ordinary Django models. The distinction drives the
            partial-load rule: idmapper models must never be read with
            ``.only()`` or ``.defer()``.
        writable: Whether the generic write lens may mutate this model.
        write_via: When not writable, the domain service that owns mutation, or
            ``""`` when the model simply has no adapter yet.
        default_ordering: Model ``Meta.ordering``, if declared.
        fields: Concrete fields in declaration order.
    """

    label: str
    app_label: str
    model_name: str
    verbose_name: str
    verbose_name_plural: str
    storage: str
    writable: bool
    proxy: bool = False
    concrete_label: str = ""
    write_via: str = ""
    default_ordering: tuple[str, ...] = ()
    fields: tuple[FieldSpec, ...] = field(default_factory=tuple)

    @property
    def read_only(self) -> bool:
        """Return whether the generic lens must present this model read-only."""

        return not self.writable

    def as_dict(self) -> dict:
        """Return the spec as plain JSON-safe data."""

        return {
            "label": self.label,
            "app_label": self.app_label,
            "model_name": self.model_name,
            "verbose_name": self.verbose_name,
            "verbose_name_plural": self.verbose_name_plural,
            "storage": self.storage,
            "proxy": self.proxy,
            "concrete_label": self.concrete_label,
            "writable": self.writable,
            "write_via": self.write_via,
            "default_ordering": list(self.default_ordering),
            "fields": [
                {
                    "name": spec.name,
                    "kind": spec.kind,
                    "primary_key": spec.primary_key,
                    "null": spec.null,
                    "blank": spec.blank,
                    "editable": spec.editable,
                    "relation": spec.relation,
                    "choices": [list(pair) for pair in spec.choices],
                }
                for spec in self.fields
            ],
        }


def _is_idmapper(model) -> bool:
    """Return whether a model participates in the idmapper identity cache."""

    from evennia.utils.idmapper.models import SharedMemoryModel

    try:
        return issubclass(model, SharedMemoryModel)
    except TypeError:
        return False


def _field_spec(model_field) -> FieldSpec:
    """Build one :class:`FieldSpec` from a Django field."""

    relation = ""
    remote = getattr(model_field, "remote_field", None)
    if remote is not None and getattr(remote, "model", None) is not None:
        related = remote.model
        if isinstance(related, str):
            relation = related.lower()
        else:
            relation = related._meta.label_lower
    raw_choices = getattr(model_field, "choices", None) or ()
    choices = tuple((str(value), str(label)) for value, label in raw_choices)
    return FieldSpec(
        name=model_field.name,
        kind=type(model_field).__name__,
        primary_key=bool(getattr(model_field, "primary_key", False)),
        null=bool(getattr(model_field, "null", False)),
        blank=bool(getattr(model_field, "blank", False)),
        editable=bool(getattr(model_field, "editable", True)),
        relation=relation,
        choices=choices,
    )


def model_spec(model) -> ModelSpec:
    """Describe one model for the console.

    Args:
        model: A Django model class.

    Returns:
        ModelSpec: The console's view of that model.
    """

    meta = model._meta
    label = meta.label_lower
    writable = label in writable_models()
    write_via = "" if writable else DOMAIN_OWNED.get(label, "")
    return ModelSpec(
        label=label,
        app_label=meta.app_label,
        model_name=meta.model_name,
        verbose_name=str(meta.verbose_name),
        verbose_name_plural=str(meta.verbose_name_plural),
        storage="idmapper" if _is_idmapper(model) else "plain",
        writable=writable,
        proxy=bool(meta.proxy),
        concrete_label=meta.concrete_model._meta.label_lower,
        write_via=write_via,
        default_ordering=tuple(str(value) for value in (meta.ordering or ())),
        fields=tuple(_field_spec(item) for item in meta.concrete_fields),
    )


@lru_cache(maxsize=1)
def _model_specs_cached() -> tuple[ModelSpec, ...]:
    """Build every model spec once per process."""

    return tuple(
        sorted(
            (model_spec(model) for model in apps.get_models()),
            key=lambda spec: spec.label,
        )
    )


def model_specs(refresh: bool = False) -> tuple[ModelSpec, ...]:
    """Return every installed model, in label order.

    Args:
        refresh: Rebuild the cache. Only useful in tests; the model set does
            not change during a process's life.

    Returns:
        tuple[ModelSpec, ...]: One spec per installed model.
    """

    if refresh:
        _model_specs_cached.cache_clear()
    return _model_specs_cached()


def get_model_spec(label: str) -> ModelSpec:
    """Return one model's spec by label.

    Args:
        label: Lowercased ``app_label.modelname``.

    Returns:
        ModelSpec: The matching spec.

    Raises:
        LookupError: No installed model carries that label.
    """

    wanted = str(label).strip().lower()
    for spec in model_specs():
        if spec.label == wanted:
            return spec
    raise LookupError(f"no installed model labelled {label!r}")
