"""The generic table lens.

Every installed model, listed, filtered, sorted, paged, and deletable, with no
registration step. This is the panel that closes the eight-versus-everything
gap against Django admin, and it is the one that must not be compromised for
elegance: being able to look at and edit the row is the baseline every other
panel is measured against.

Four rules shape the implementation, and three of them exist because these
tables get large. ``SessionRecord`` grows by one row per connection.

**Never partially load an idmapper model.** ``.only()`` and ``.defer()`` cannot
construct an uncached ``SharedMemoryModel``, and on a *cached* one they return
the cached instance with missing fields treated as "no information". The
failure is silent in development and wrong in production, so this panel reads
through ``values()`` exclusively -- which is also what keeps it working with no
IO owner at all.

**Never count to paginate.** ``COUNT(*)`` has no shortcut on PostgreSQL, so a
count per page view is a table scan per page view. Fetching one row more than
the page needs answers the only question the controls actually ask -- is there
another page -- for the cost of one row. A total is a separate, deliberate
request.

**Never page by offset.** ``OFFSET 500000`` walks half a million rows to throw
them away. Paging is by keyset: the cursor carries the last row's sort value
and primary key, and the next page is everything after it.

**Writes stay allowlisted.** Reads are generic because reading a row cannot
break an invariant. Writes are not: a model without a registered mutation
adapter is presented read-only, with the domain service that owns it named in
the response rather than hidden behind a disabled button.

"""

from __future__ import annotations

import base64
import csv
import io
import json

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, FieldError, PermissionDenied, ValidationError
from django.db.models import Q

from evennia.console import audit, spec
from evennia.console.panels.coerce import CoercionError, coerce_payload
from evennia.console.registry import Panel, io_action
from evennia.console.services import (
    AdminDeleteRequest,
    AdminMutationRequest,
    TagDelta,
    delete_admin,
    mutate_admin,
    mutation_field_types,
    mutation_relation_models,
    mutation_supports_tags,
)

#: Rows a single page may return.
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50

#: Columns a list shows before the operator picks. A model with thirty-seven
#: fields is unreadable as thirty-seven columns, and the detail view carries
#: every field anyway.
DEFAULT_COLUMN_LIMIT = 8

#: Estimated row count above which an exact count is refused unless asked for.
COUNT_CEILING = 100_000

#: Field names that identify a row to a human, in preference order. Used both
#: to choose default columns and to scope free-text search to something an
#: index can serve.
IDENTITY_NAMES = (
    "db_key",
    "username",
    "name",
    "key",
    "title",
    "slug",
    "label",
    "subject",
    "subject_value",
    "account_name",
    "capability",
    "event_id",
    "job_id",
    "grant_id",
    "session_uid",
)

#: Field classes free-text search may scan. TextField is excluded: it is
#: unbounded, and no index helps.
SEARCHABLE_KINDS = frozenset({"CharField", "SlugField", "EmailField"})

#: Comparison suffixes a filter may use, mapped to their ORM lookup.
FILTER_LOOKUPS = {
    "": "exact",
    "exact": "exact",
    "iexact": "iexact",
    "contains": "icontains",
    "startswith": "istartswith",
    "gt": "gt",
    "gte": "gte",
    "lt": "lt",
    "lte": "lte",
    "isnull": "isnull",
    "in": "in",
}


#: Rows one bulk preview will consider. A preview that walked an unbounded
#: selection would be the expensive query the preview exists to avoid.
MAX_BULK = 200


def _encode_cursor(sort_value, pk) -> str:
    """Encode one keyset position as an opaque token."""

    payload = json.dumps([None if sort_value is None else str(sort_value), pk])
    return base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii")


def _decode_cursor(token):
    """Decode one keyset cursor, or return ``None`` if it is unusable.

    A cursor is a position, not a credential. A stale or malformed one means
    "start from the beginning", never an error page.
    """

    if not token:
        return None
    try:
        raw = base64.urlsafe_b64decode(str(token).encode("ascii")).decode("utf-8")
        sort_value, pk = json.loads(raw)
        return sort_value, int(pk)
    except Exception:  # noqa: BLE001 - any malformed cursor is just no cursor
        return None


class RecordsPanel(Panel):
    """List, inspect, and delete rows of any installed model."""

    key = "records"
    label = "Records"
    description = "Every installed model, including those the admin never registered."
    columns = ("pk", "label")
    needs_io = False

    # -- reads ---------------------------------------------------------

    def models(self, ctx):
        """Return every concrete installed model with its write policy.

        Proxies are excluded: they share their concrete model's table, so
        listing them shows one table under several names.
        """

        return [
            {
                "label": item.label,
                "app_label": item.app_label,
                "verbose_name_plural": item.verbose_name_plural,
                "storage": item.storage,
                "writable": item.writable,
                "write_via": item.write_via,
            }
            for item in spec.model_specs()
            if not item.proxy
        ]

    def rows(self, ctx):
        """Return one page of rows for the requested model.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``model``,
                ``cursor``, ``page_size``, ``search``, ``order``, ``columns``,
                and any number of ``f.<field>[__lookup]`` filters.

        Returns:
            dict: Rows, the cursor for the next page, the columns available to
            choose from, and the model's write policy.
        """

        params = dict(ctx.params)
        model_spec = self._spec(params.get("model"))
        model = apps.get_model(model_spec.label)
        pk_name = self._pk_name(model_spec)
        columns = self._columns(model_spec, params.get("columns"))
        order = self._ordering(model_spec, params.get("order"))
        page_size = self._page_size(params)

        queryset = model._base_manager.all()
        queryset = self._searched(queryset, model_spec, params.get("search"))
        queryset, applied = self._filtered(queryset, model_spec, params)
        queryset = queryset.order_by(*order)

        cursor = _decode_cursor(params.get("cursor"))
        if cursor is not None:
            queryset = self._after(queryset, order, pk_name, cursor)

        # One row more than the page needs: enough to know whether another page
        # exists, without counting anything.
        fields = self._fetch_fields(model_spec, columns)
        fetched = list(queryset.values(*fields)[: page_size + 1])
        has_more = len(fetched) > page_size
        window = fetched[:page_size]

        next_cursor = ""
        if has_more and window:
            last = window[-1]
            next_cursor = _encode_cursor(last.get(order[0].lstrip("-")), last[pk_name])

        return {
            "model": model_spec.label,
            "verbose_name_plural": model_spec.verbose_name_plural,
            "columns": columns,
            "available_columns": [field.name for field in model_spec.fields],
            "field_specs": model_spec.as_dict()["fields"],
            "rows": [self._plain(row, columns) for row in window],
            "page_size": page_size,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "order": order[0],
            "filters": applied,
            "writable": model_spec.writable,
            "write_via": model_spec.write_via,
            "storage": model_spec.storage,
            "count_note": "A page does not count rows. Ask for a count when you need one.",
        }

    def count(self, ctx, model=None, search=None, exact=False, **filters):
        """Return how many rows match, exactly or as an estimate.

        Separated from :meth:`rows` on purpose. ``COUNT(*)`` has no shortcut on
        PostgreSQL, so counting once per page view is a table scan once per
        page view, for a number that changes nothing about what the operator
        can do next.

        Args:
            ctx: Worker context.
            model: Model label.
            search: Optional free-text term, matching :meth:`rows`.
            exact: Force an exact count past the ceiling.
            **filters: ``f.<field>`` filters, matching :meth:`rows`.

        Returns:
            dict: The count, whether it is exact, and why when it is not.
        """

        model_spec = self._spec(model)
        target = apps.get_model(model_spec.label)
        queryset = target._base_manager.all()
        queryset = self._searched(queryset, model_spec, search)
        queryset, applied = self._filtered(queryset, model_spec, filters)

        estimate = self._estimate(target)
        narrowed = bool(applied) or bool(str(search or "").strip())
        if not exact and not narrowed and estimate is not None and estimate > COUNT_CEILING:
            return {
                "model": model_spec.label,
                "rows": int(estimate),
                "exact": False,
                "reason": (
                    f"The table holds roughly {int(estimate)} rows. An exact count reads "
                    "every one of them, so it runs only when asked for."
                ),
            }
        return {"model": model_spec.label, "rows": queryset.count(), "exact": True}

    def detail(self, ctx, pk):
        """Return one record in full.

        Args:
            ctx: Worker context carrying ``model`` in ``params``.
            pk: Primary key of the record.

        Returns:
            dict: The record, its field specs, and its write policy.

        Raises:
            LookupError: No such record.
        """

        model_spec = self._spec(ctx.params.get("model"))
        model = apps.get_model(model_spec.label)
        fields = [item.name for item in model_spec.fields]
        try:
            row = model._base_manager.filter(pk=pk).values(*fields).first()
        except (ValueError, ValidationError, TypeError) as err:
            raise LookupError(f"{pk!r} is not a valid identifier for {model_spec.label}") from err
        if row is None:
            raise LookupError(f"no {model_spec.label} with primary key {pk!r}")
        return {
            "model": model_spec.label,
            "record": self._plain(row, fields),
            "fields": model_spec.as_dict()["fields"],
            "writable": model_spec.writable,
            "write_via": model_spec.write_via,
        }

    # -- writes --------------------------------------------------------

    def form(self, ctx, model=None):
        """Return what a caller may write to one model, and how.

        Only the fields the mutation adapter declares. A field absent here is
        not writable through this lens, and saying so up front is better than
        a rejection after the operator has filled it in.
        """

        model_spec = self._spec(model)
        if not model_spec.writable:
            return {
                "model": model_spec.label,
                "writable": False,
                "write_via": model_spec.write_via,
                "fields": [],
            }
        accepted = mutation_field_types(model_spec.label)
        relations = mutation_relation_models(model_spec.label)
        supports_tags = mutation_supports_tags(model_spec.label)
        by_name = {field.name: field for field in model_spec.fields}
        return {
            "model": model_spec.label,
            "writable": True,
            "write_via": "",
            "fields": [
                {
                    "name": name,
                    "types": [item.__name__ for item in types],
                    "nullable": "NoneType" in {item.__name__ for item in types},
                    "kind": by_name[name].kind if name in by_name else "",
                    "blank": by_name[name].blank if name in by_name else False,
                    "relation": by_name[name].relation if name in by_name else "",
                    "primary_key": by_name[name].primary_key if name in by_name else False,
                    "has_default": apps.get_model(model_spec.label)
                    ._meta.get_field(name)
                    .has_default(),
                    "choices": [list(pair) for pair in by_name[name].choices]
                    if name in by_name
                    else [],
                }
                for name, types in sorted(accepted.items())
            ],
            "supports_password": model_spec.label == "accounts.accountdb",
            "supports_tags": supports_tags,
            "relations": [
                {"name": name, "model": related} for name, related in sorted(relations.items())
            ],
            "unsupported": [] if model_spec.label == "accounts.accountdb" else ["password"],
            "note": (
                "Relations and tags are applied through their owning handlers. Account "
                "passwords use the dedicated password control below the field form."
            ),
        }

    @io_action
    def set_password(self, ctx, account_id=None, password="", usable=True):
        """Set or disable one account password through the credential service.

        This action requires recent reauthentication at the HTTP boundary. No
        password value is copied into the console audit trail.
        """

        from evennia.web.utils.auth import PasswordMutationRequest, mutate_password

        try:
            target = int(account_id)
        except (TypeError, ValueError) as err:
            raise ValueError("Select an account before changing its password.") from err
        secret = str(password or "")
        if usable and not secret:
            raise ValueError("Enter the new password.")
        result = mutate_password(
            PasswordMutationRequest(
                account_id=target,
                mode="console_set" if usable else "console_unusable",
                new_password=secret if usable else None,
                actor_id=int(ctx.actor_id),
            )
        )
        audit.record(
            panel=self.key,
            operation="password_set" if usable else "password_disabled",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"accounts.accountdb#{target}",
            after={"status": result.status, "usable": bool(usable)},
            message=str(result.message or "")[:500],
        )
        if result.status != "changed":
            raise ValueError(result.message or "The password was not changed.")
        return {
            "status": result.status,
            "account_id": result.account_id,
            "message": result.message,
        }

    @io_action
    def save(self, ctx, model=None, pk=None, values=None, relations=None, tags=None, reason=""):
        """Create or change a non-secret record through its mutation adapter."""

        if str(model or "").lower() == "accounts.accountdb" and pk is None:
            submitted = dict(values or {})
            if "password" in submitted:
                raise PermissionDenied(
                    "Passwords are not set through the records lens. Use the password service."
                )
            coerce_payload(submitted, mutation_field_types("accounts.accountdb"))
            raise ValueError("Create an account with the presence-gated account control.")
        return self._save_record(
            ctx,
            model=model,
            pk=pk,
            values=values,
            relations=relations,
            tags=tags,
            reason=reason,
        )

    @io_action
    def create_account(self, ctx, values=None, relations=None, tags=None, password=""):
        """Create one account with a validated, repr-hidden password.

        The HTTP boundary requires recent reauthentication for this named
        action. The secret is passed only to the owner mutation request and is
        never copied into either audit payload.
        """

        secret = str(password or "")
        if not secret:
            raise ValueError("Enter the new account's password.")
        return self._save_record(
            ctx,
            model="accounts.accountdb",
            values=values,
            relations=relations,
            tags=tags,
            password=secret,
        )

    def _save_record(
        self,
        ctx,
        model=None,
        pk=None,
        values=None,
        relations=None,
        tags=None,
        reason="",
        password=None,
    ):
        """Create or change one row through the bounded mutation service.

        Runs on the IO owner: the service reloads the actor, recomputes
        permission from fresh state, resolves foreign keys, and drives the
        model's own lifecycle. The worker only coerced JSON into the exact
        types the service demands.

        Relations and tags use the same allowlisted owner handlers as Django
        admin. Passwords remain a distinct presence-gated credential action.

        Args:
            ctx: IO context.
            model: Model label.
            pk: Row to change, or ``None`` to create one.
            values: Field name to submitted value.
            relations: Complete related-ID lists for submitted M2M fields.
            tags: Complete desired owner-handler tag list.

        Returns:
            dict: The service's outcome, including its own status string.

        Raises:
            PermissionDenied: The model has no generic write path.
            CoercionError: A submitted value cannot be stored in its field.
        """

        model_spec = self._spec(model)
        if not model_spec.writable:
            raise PermissionDenied(
                f"{model_spec.label} is not generically writable. "
                f"Mutate it through {model_spec.write_via or 'its own domain service'}."
            )
        submitted = dict(values or {})
        if "password" in submitted:
            raise PermissionDenied(
                "Passwords are not set through the records lens. Use the password service."
            )
        concrete = coerce_payload(submitted, mutation_field_types(model_spec.label))

        before = {}
        target = int(pk) if pk is not None else None
        target_model = apps.get_model(model_spec.label)
        relation_payload = self._relation_payload(model_spec.label, relations)
        tag_payload = self._tag_payload(target_model, target, tags)
        audit_before = {}
        if target is not None:
            existing = (
                target_model._base_manager.filter(pk=target)
                .values(*[name for name, _ in concrete])
                .first()
            )
            before = {key: str(value) for key, value in (existing or {}).items()}
            audit_before.update(before)
            if relations is not None or tags is not None:
                existing_extras = self.extras(ctx, model=model_spec.label, pk=target)
                if relations is not None:
                    audit_before["relations"] = existing_extras["relations"]
                if tags is not None:
                    audit_before["tags"] = existing_extras["tags"]

        audit_after = {key: str(value) for key, value in concrete}
        if relations is not None:
            audit_after["relations"] = {name: list(ids) for name, ids in relation_payload}
        if tags is not None:
            audit_after["tags"] = list(tags)

        result = mutate_admin(
            AdminMutationRequest(
                actor_id=int(ctx.actor_id),
                model_label=model_spec.label,
                object_id=target,
                concrete=concrete,
                relations=relation_payload,
                tags=tag_payload,
                password=password,
                authority="console",
            )
        )
        payload = {
            "status": result.status,
            "object_id": result.object_id,
            "object_repr": getattr(result, "object_repr", ""),
            "message": getattr(result, "message", ""),
            "retryable": bool(getattr(result, "retryable", False)),
        }
        outcome = self._audit_outcome(result.status)
        audit.record(
            panel=self.key,
            operation="change" if target is not None else "add",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"{model_spec.label}#{result.object_id or target or ''}"[:160],
            outcome=outcome,
            before=audit_before,
            after=audit_after,
            inverse=self._inverse(model_spec, target, before, outcome),
            message=(str(reason or "").strip() or payload["message"])[:500],
        )
        return payload

    def related(self, ctx, model=None, field=None, search=""):
        """Return bounded candidates for one foreign-key field."""

        model_spec = self._spec(model)
        relation_model = mutation_relation_models(model_spec.label).get(str(field))
        if not relation_model:
            try:
                field_spec = next(item for item in model_spec.fields if item.name == field)
            except StopIteration as err:
                raise ValueError(f"{field!r} is not a field on {model_spec.label}.") from err
            relation_model = field_spec.relation
        if not relation_model:
            raise ValueError(f"{field!r} is not a relationship field.")
        related = apps.get_model(relation_model)
        related_spec = spec.model_spec(related)
        identities = [
            item.name
            for item in related_spec.fields
            if item.name in IDENTITY_NAMES and item.kind in SEARCHABLE_KINDS
        ]
        queryset = related._base_manager.all()
        wanted = str(search or "").strip()
        if wanted:
            query = Q()
            for name in identities:
                query |= Q(**{f"{name}__istartswith": wanted})
            try:
                related._meta.get_field("db_tags")
            except FieldDoesNotExist:
                pass
            else:
                # Typeclass-aware lookup: aliases and ordinary tags are both
                # stored in db_tags and are identifying names in operator use.
                query |= Q(db_tags__db_key__istartswith=wanted)
            if wanted.isdigit():
                query |= Q(pk=int(wanted))
            queryset = queryset.filter(query).distinct() if query else queryset.none()
        pk_name = related._meta.pk.name
        names = list(dict.fromkeys([pk_name, *identities]))
        rows = list(queryset.order_by(pk_name).values(*names)[:21])
        return {
            "model": related_spec.label,
            "rows": [
                {
                    "id": row[pk_name],
                    "label": next(
                        (str(row[name]) for name in identities if row.get(name) not in (None, "")),
                        f"{related_spec.verbose_name} #{row[pk_name]}",
                    ),
                }
                for row in rows[:20]
            ],
            "has_more": len(rows) > 20,
        }

    def extras(self, ctx, model=None, pk=None):
        """Return current editable M2M relations and owner-handler tags.

        Reads the through tables with ``values_list`` so an idmapper instance is
        never partially materialized on a web worker.
        """

        model_spec = self._spec(model)
        target = apps.get_model(model_spec.label)
        try:
            identifier = int(pk)
        except (TypeError, ValueError) as err:
            raise ValueError("Select a valid row before reading its related values.") from err
        if not target._base_manager.filter(pk=identifier).exists():
            raise LookupError(f"no {model_spec.label} with primary key {identifier!r}")
        relations = {
            name: self._m2m_ids(target, name, identifier)
            for name in mutation_relation_models(model_spec.label)
        }
        tags = (
            self._tag_values(target, identifier) if mutation_supports_tags(model_spec.label) else []
        )
        return {"model": model_spec.label, "id": identifier, "relations": relations, "tags": tags}

    def relations(self, ctx, model=None, pk=None):
        """Return a bounded, navigable relationship graph for one row."""

        model_spec = self._spec(model)
        target = apps.get_model(model_spec.label)
        try:
            identifier = int(pk)
        except (TypeError, ValueError) as err:
            raise ValueError("Select a valid row before reading its relationships.") from err
        if not target._base_manager.filter(pk=identifier).exists():
            raise LookupError(f"no {model_spec.label} with primary key {identifier!r}")

        forward = []
        for item in model_spec.fields:
            if not item.relation:
                continue
            value = (
                target._base_manager.filter(pk=identifier).values_list(item.name, flat=True).first()
            )
            if value is not None:
                forward.append({"field": item.name, "model": item.relation, "id": value})

        reverse = []
        for relation in target._meta.related_objects:
            related = relation.related_model
            field = relation.field.name
            try:
                sample = list(
                    related._base_manager.filter(**{field: identifier})
                    .order_by(related._meta.pk.name)
                    .values_list(related._meta.pk.name, flat=True)[:21]
                )
            except (FieldError, TypeError, ValueError):
                continue
            if sample:
                reverse.append(
                    {
                        "field": relation.get_accessor_name(),
                        "model": related._meta.label_lower,
                        "ids": sample[:20],
                        "has_more": len(sample) > 20,
                    }
                )
        return {"model": model_spec.label, "id": identifier, "forward": forward, "reverse": reverse}

    def preview_change(self, ctx, model=None, ids=None, values=None):
        """Return per-row before/after values for a bounded bulk change."""

        model_spec = self._spec(model)
        if not model_spec.writable:
            raise PermissionDenied(f"{model_spec.label} is not generically writable.")
        identifiers = tuple(dict.fromkeys(int(value) for value in (ids or ())))[:MAX_BULK]
        if not identifiers:
            raise ValueError("Select at least one row to change.")
        submitted = dict(values or {})
        if not submitted:
            raise ValueError("Select at least one field to change.")
        concrete = dict(coerce_payload(submitted, mutation_field_types(model_spec.label)))
        target = apps.get_model(model_spec.label)
        fields = list(concrete)
        rows = list(target._base_manager.filter(pk__in=identifiers).values("pk", *fields))
        found = {int(row["pk"]) for row in rows}
        return {
            "model": model_spec.label,
            "rows": [
                {
                    "id": row["pk"],
                    "before": {name: self._plain_value(row[name]) for name in fields},
                    "after": {name: self._plain_value(concrete[name]) for name in fields},
                }
                for row in rows
            ],
            "missing": sorted(set(identifiers) - found),
            "capped": len(identifiers) >= MAX_BULK,
            "note": "The console has changed nothing. Review every field before applying this bulk change.",
        }

    @io_action
    def bulk_save(self, ctx, model=None, ids=None, values=None, reason=""):
        """Apply one previewed field set to a bounded selection."""

        justification = str(reason or "").strip()
        if not justification:
            raise ValueError("Enter why this bulk change is needed.")
        identifiers = tuple(dict.fromkeys(int(value) for value in (ids or ())))[:MAX_BULK]
        if not identifiers:
            raise ValueError("Select at least one row to change.")
        results = []
        for identifier in identifiers:
            result = self.save(
                ctx,
                model=model,
                pk=identifier,
                values=values,
                reason=justification,
            )
            results.append({"id": identifier, **result})
        return {
            "model": self._spec(model).label,
            "changed": sum(1 for row in results if row["status"] == "changed"),
            "rows": results,
        }

    def _inverse(self, model_spec, target, before, outcome):
        """Return the payload that would reverse this write, or ``None``.

        Only a change to an existing row is reversible here, and only one that
        succeeded. Two deliberate exclusions:

        An **add** would be reversed by a delete, and delete carries a cascade
        that the mutation service preflights for a reason. Silently attaching a
        cascading delete to an undo button is the wrong shape for a control an
        operator reaches for after a mistake.

        A **failed or partial** write did not necessarily leave the row in the
        state this row records, so restoring ``before`` could overwrite a value
        nobody chose.

        Args:
            model_spec: Spec for the model written.
            target: Primary key changed, or ``None`` for a create.
            before: Field values read immediately before the write.
            outcome: The audit outcome just computed for this write.

        Returns:
            dict | None: An inverse the Audit panel can apply, or ``None``.
        """

        from evennia.console.models import ConsoleAuditEvent

        if target is None or not before:
            return None
        if outcome != ConsoleAuditEvent.OUTCOME_SUCCESS:
            return None
        return {
            "kind": "records.change",
            "model": model_spec.label,
            "pk": target,
            "values": dict(before),
        }

    def _audit_outcome(self, status):
        """Map a service status onto the audit trail's outcome vocabulary."""

        from evennia.console.models import ConsoleAuditEvent

        return {
            "created": ConsoleAuditEvent.OUTCOME_SUCCESS,
            "changed": ConsoleAuditEvent.OUTCOME_SUCCESS,
            "ok": ConsoleAuditEvent.OUTCOME_SUCCESS,
            "conflict": ConsoleAuditEvent.OUTCOME_CONFLICT,
            "partial": ConsoleAuditEvent.OUTCOME_PARTIAL,
            "recovery_required": ConsoleAuditEvent.OUTCOME_RECOVERY,
        }.get(str(status), ConsoleAuditEvent.OUTCOME_SUCCESS)

    # -- export --------------------------------------------------------

    def export(self, ctx, model=None, fmt="json", **filters):
        """Return the current view as CSV or JSON, and record that it happened.

        Bounded by the same row cap as the view it exports. An export that
        quietly returned more than the page it came from would be a different
        query wearing the same name.

        **An export is a disclosure event.** Rows leave the console and stop
        being subject to it: they land in a spreadsheet, an email, a ticket.
        The audit row records what was taken, by whom, and how much -- not the
        contents, which would put a second copy of the data in the audit table
        and defeat the retention window that governs the first.

        Args:
            ctx: Worker context carrying the same filter parameters the listing
                takes.
            model: Model label.
            fmt: ``"csv"`` or ``"json"``.
            **filters: Ignored; filters are read from ``ctx.params``.

        Returns:
            dict: The serialized body, its content type, and a filename.

        Raises:
            ValueError: The format is not one this exports.
        """

        shape = str(fmt or "json").strip().lower()
        if shape not in ("csv", "json"):
            raise ValueError(f"{fmt!r} is not an export format; use csv or json")

        listing = self.rows(ctx)
        rows = listing.get("rows") or []
        # ``columns`` is a list of field names, not of column records. Reading
        # it as records raised a TypeError on every export.
        names = [str(name) for name in (listing.get("columns") or [])]
        if not names and rows:
            names = sorted(rows[0])

        if shape == "csv":
            body = self._csv(names, rows)
            content_type = "text/csv"
        else:
            body = json.dumps(rows, indent=2, default=str)
            content_type = "application/json"

        label = self._spec(model).label if model else str(ctx.params.get("model") or "")
        audit.record(
            panel=self.key,
            operation="export",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"{label}"[:160],
            # What was taken, not what was in it. Copying the contents here
            # would put a second copy of the data in a table with its own
            # retention window, which is the opposite of what that window is
            # for.
            before={},
            after={
                "format": shape,
                "rows": len(rows),
                "columns": names,
                "filters": {
                    key: str(value)[:120]
                    for key, value in ctx.params.items()
                    if key not in ("cursor", "page_size") and value
                },
            },
            message=f"exported {len(rows)} rows of {label} as {shape}",
        )
        return {
            "model": label,
            "format": shape,
            "content_type": content_type,
            "filename": f"{label.replace('.', '-')}.{shape}",
            "rows": len(rows),
            "body": body,
            "note": ("Bounded by the same row cap as the view. Page and export again for more."),
        }

    def _csv(self, names, rows):
        """Return rows as CSV text.

        ``csv`` rather than string joining: a value containing a comma, a
        quote, or a newline has exactly one correct encoding and it is not the
        obvious one.
        """

        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(names)
        for row in rows:
            writer.writerow(["" if row.get(name) is None else str(row.get(name)) for name in names])
        return buffer.getvalue()

    # -- bulk ----------------------------------------------------------

    def preview_delete(self, ctx, model=None, ids=None):
        """Report what deleting these rows would do, without deleting anything.

        Django admin's bulk actions commit and report afterwards. This is the
        other order: the cascade is counted first, per row, and the operator
        decides against a number rather than a guess.

        Worker-side and read-only. It answers the question that decides whether
        to cross to the IO owner at all.

        Args:
            ctx: Worker context.
            model: Model label.
            ids: Row identifiers.

        Returns:
            dict: Per-row cascade counts and the totals.

        Raises:
            PermissionDenied: The model has no generic delete path.
            ValueError: No rows were named.
        """

        model_spec = self._spec(model)
        if not model_spec.writable:
            # Refused on the same gate as the delete itself. Counting a cascade
            # for a row the console will not delete implies a delete is
            # available, which is the misunderstanding the preview exists to
            # prevent rather than create.
            raise PermissionDenied(
                f"{model_spec.label} is not generically writable, so there is no delete to preview."
            )
        wanted = [int(value) for value in (ids or []) if str(value).strip().lstrip("-").isdigit()]
        if not wanted:
            raise ValueError("no rows were named")
        wanted = wanted[:MAX_BULK]

        target = apps.get_model(model_spec.label)
        found = list(target._base_manager.filter(pk__in=wanted).values_list("pk", flat=True))
        missing = sorted(set(wanted) - set(found))

        rows = []
        total = 0
        for pk in found:
            counts = self._cascade_counts(target, pk)
            reach = sum(counts.values())
            total += reach
            rows.append({"id": pk, "cascade": counts, "reach": reach})

        return {
            "model": model_spec.label,
            "rows": sorted(rows, key=lambda row: -row["reach"]),
            "requested": len(wanted),
            "found": len(found),
            "missing": missing,
            "total_cascade": total,
            "capped": len(wanted) >= MAX_BULK,
            "note": (
                "These counts show the rows that the database removes with each "
                "row you selected. The console has deleted nothing."
            ),
        }

    def _cascade_counts(self, model, pk):
        """Return how many related rows each relation would take with one row.

        Walks the concrete reverse relations that cascade. A relation that
        nulls or protects is not counted, because it does not remove anything:
        counting it would overstate the blast radius and train an operator to
        ignore the number.
        """

        from django.db.models.deletion import CASCADE

        counts = {}
        for relation in model._meta.related_objects:
            if getattr(relation, "on_delete", None) is not CASCADE:
                continue
            related = relation.related_model
            field = relation.field.name
            try:
                total = related._base_manager.filter(**{field: pk}).count()
            except Exception:  # noqa: BLE001 - one unreadable relation is not the answer
                continue
            if total:
                counts[f"{related._meta.label_lower}.{field}"] = total
        return counts

    @io_action
    def delete(self, ctx, model=None, ids=None, reason=""):
        """Delete rows through the bounded mutation service.

        Runs on the IO owner because deletion invokes lifecycle hooks on game
        objects. The service recomputes protected relations and cascade
        permissions freshly on the owner; the worker-side check here is for
        fast rejection only and cannot authorize the mutation.

        Args:
            ctx: IO context.
            model: Model label to delete from.
            ids: Primary keys to delete.
            reason: Operator justification retained with the audit event.

        Returns:
            dict: Which ids were deleted, vetoed, missing, or failed.

        Raises:
            PermissionDenied: The model has no generic write path.
        """

        model_spec = self._spec(model)
        if not model_spec.writable:
            raise PermissionDenied(
                f"{model_spec.label} is not generically writable. "
                f"Mutate it through {model_spec.write_via or 'its own domain service'}."
            )
        justification = str(reason or "").strip()
        if not justification:
            raise ValueError("Enter why these rows must be deleted.")
        justification = justification[:500]
        identifiers = tuple(int(value) for value in (ids or ()))
        if not identifiers:
            return {"deleted": [], "vetoed": [], "missing": [], "failed": []}

        result = delete_admin(
            AdminDeleteRequest(
                actor_id=int(ctx.actor_id),
                model_label=model_spec.label,
                object_ids=identifiers,
                authority="console",
            )
        )
        payload = {
            "status": result.status,
            "deleted": list(result.deleted_ids),
            "vetoed": list(getattr(result, "vetoed_ids", ()) or ()),
            "missing": list(getattr(result, "missing_ids", ()) or ()),
            "failed": list(getattr(result, "failed_ids", ()) or ()),
            "reason": justification,
            "message": result.message,
        }
        if result.status == "partial":
            payload["message"] = (
                f"Deleted {len(result.deleted_ids)}; vetoed {len(result.vetoed_ids)}; "
                f"missing {len(result.missing_ids)}; failed {len(result.failed_ids)}. "
                "Inspect the listed rows before acting again."
            )
        audit.record(
            panel=self.key,
            operation="delete",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"{model_spec.label}#{','.join(str(i) for i in identifiers)}"[:160],
            before={"ids": list(identifiers)},
            after=payload,
            message=justification,
        )
        return payload

    # -- helpers -------------------------------------------------------

    def _spec(self, label):
        """Return one model spec, or fail closed."""

        if not label:
            raise LookupError("a model label is required")
        return spec.get_model_spec(label)

    def _m2m_ids(self, model, field_name, identifier):
        """Return bounded related IDs from one concrete M2M through table."""

        field = model._meta.get_field(field_name)
        through = field.remote_field.through
        source = f"{field.m2m_field_name()}_id"
        related = f"{field.m2m_reverse_field_name()}_id"
        return list(
            through._base_manager.filter(**{source: identifier})
            .order_by(related)
            .values_list(related, flat=True)[:2000]
        )

    def _tag_values(self, model, identifier):
        """Return one owner's tags as complete handler values."""

        field = model._meta.get_field("db_tags")
        tag_model = field.remote_field.model
        tag_ids = self._m2m_ids(model, "db_tags", identifier)
        return [
            {
                "key": row["db_key"],
                "category": row["db_category"],
                "type": row["db_tagtype"],
                "data": row["db_data"],
            }
            for row in tag_model._base_manager.filter(pk__in=tag_ids)
            .order_by("db_tagtype", "db_category", "db_key")
            .values("db_key", "db_category", "db_tagtype", "db_data")
        ]

    def _relation_payload(self, model_label, relations):
        """Validate a complete related-ID mapping for the mutation service."""

        if relations is None:
            return ()
        if not isinstance(relations, dict):
            raise ValueError("Relations must be submitted by field name.")
        allowed = mutation_relation_models(model_label)
        unknown = set(relations) - set(allowed)
        if unknown:
            raise ValueError(f"Unknown relation {sorted(unknown)[0]!r}.")
        payload = []
        for name, values in sorted(relations.items()):
            try:
                identifiers = tuple(dict.fromkeys(int(value) for value in (values or ())))
            except (TypeError, ValueError) as err:
                raise ValueError(f"Relation {name!r} contains an invalid row identifier.") from err
            if any(value <= 0 for value in identifiers):
                raise ValueError(f"Relation {name!r} contains an invalid row identifier.")
            payload.append((name, identifiers))
        return tuple(payload)

    def _tag_payload(self, model, identifier, submitted):
        """Build owner-handler deltas from a complete desired tag list."""

        if submitted is None:
            return ()
        if not isinstance(submitted, list):
            raise ValueError("Tags must be submitted as a list.")

        def normalized(item):
            if not isinstance(item, dict):
                raise ValueError("Every tag must name a key, category, type, and data value.")
            key = str(item.get("key") or "").strip()
            if not key:
                raise ValueError("Every tag needs a key.")
            values = [key]
            for name in ("category", "type", "data"):
                value = item.get(name)
                values.append(None if value in (None, "") else str(value))
            if values[2] not in (None, "alias", "permission"):
                raise ValueError("Tag type must be alias, permission, or empty.")
            return tuple(values)

        desired = {normalized(item) for item in submitted}
        current = {
            (item["key"], item["category"], item["type"], item["data"])
            for item in (self._tag_values(model, identifier) if identifier is not None else [])
        }
        removed = [TagDelta(old=value, new=None) for value in sorted(current - desired, key=str)]
        added = [TagDelta(old=None, new=value) for value in sorted(desired - current, key=str)]
        return tuple([*removed, *added])

    def _pk_name(self, model_spec) -> str:
        """Return the primary key's field name."""

        for field in model_spec.fields:
            if field.primary_key:
                return field.name
        return "id"

    def _columns(self, model_spec, requested):
        """Return the columns to show, validated against the model.

        Without a request, choose a small readable set: the primary key, the
        fields that identify a row to a human, then whatever else fits. Thirty
        columns is not a richer table, it is an unreadable one, and the detail
        view carries every field.
        """

        known = {field.name: field for field in model_spec.fields}
        if requested:
            names = [name.strip() for name in str(requested).split(",") if name.strip()]
            chosen = [name for name in names if name in known]
            if chosen:
                return chosen

        pk = self._pk_name(model_spec)
        chosen = [pk]
        for name in IDENTITY_NAMES:
            if name in known and name not in chosen:
                chosen.append(name)
        for field in model_spec.fields:
            if len(chosen) >= DEFAULT_COLUMN_LIMIT:
                break
            if field.name in chosen or field.kind in {"TextField", "JSONField", "BinaryField"}:
                continue
            chosen.append(field.name)
        return chosen[:DEFAULT_COLUMN_LIMIT]

    def _fetch_fields(self, model_spec, columns):
        """Return the fields to read: the chosen columns plus what paging needs."""

        pk = self._pk_name(model_spec)
        fields = list(columns)
        if pk not in fields:
            fields.append(pk)
        return fields

    #: Most columns free-text search will scan. Bounded because each one is a
    #: separate OR'd comparison, and the cost is per column.
    SEARCH_FIELD_LIMIT = 3

    def _search_fields(self, model_spec):
        """Return the columns free-text search scans.

        Identity columns first, then whatever short text columns remain, up to
        the limit. Identity names alone are too narrow: on a model whose only
        matching name is an opaque identifier, search would appear broken
        while behaving exactly as designed.
        """

        known = [f.name for f in model_spec.fields if f.kind in SEARCHABLE_KINDS]
        names = [name for name in IDENTITY_NAMES if name in known]
        for name in known:
            if len(names) >= self.SEARCH_FIELD_LIMIT:
                break
            if name not in names:
                names.append(name)
        return names[: self.SEARCH_FIELD_LIMIT]

    def _searched(self, queryset, model_spec, search):
        """Apply a bounded free-text filter over identifying columns only.

        Deliberately not every text column. ``SessionRecord`` has seventeen,
        and OR-ing ``LIKE '%term%'`` across all of them is one unindexable scan
        per column. Prefix matching over the columns that identify a row is
        both usable and index-friendly.
        """

        term = str(search or "").strip()[:200]
        if not term:
            return queryset
        names = self._search_fields(model_spec)
        if not names:
            return queryset.none()
        clause = Q()
        for name in names:
            clause |= Q(**{f"{name}__istartswith": term})
        return queryset.filter(clause)

    def _filtered(self, queryset, model_spec, params):
        """Apply ``f.<field>[__lookup]=value`` filters, validated by the spec.

        Field-scoped filters are what an operator actually wants: "sessions
        from this network", not a text search that might happen to match it.
        Every field and comparison is checked against the model, so an unknown
        one is a stated error rather than a silently empty page.
        """

        applied = {}
        known = {field.name: field for field in model_spec.fields}
        for raw_key, value in list(params.items()):
            if not str(raw_key).startswith("f."):
                continue
            expression = str(raw_key)[2:]
            name, _, suffix = expression.partition("__")
            if name not in known:
                raise FieldError(f"{model_spec.label} has no field {name!r} to filter on")
            if suffix not in FILTER_LOOKUPS:
                raise FieldError(f"{suffix!r} is not a supported comparison")
            lookup = FILTER_LOOKUPS[suffix]
            field_spec = known[name]
            if field_spec.kind == "JSONField" and suffix in ("", "exact", "contains"):
                lookup = "contains" if suffix == "contains" else "exact"
                if isinstance(value, str):
                    try:
                        value = json.loads(value)
                    except json.JSONDecodeError as err:
                        raise ValueError(f"Filter {name!r} must contain valid JSON.") from err
            elif field_spec.kind == "JSONField" and suffix not in ("isnull",):
                raise FieldError(
                    f"JSON field {name!r} supports exact, contains, and is-empty comparisons."
                )
            if lookup == "isnull":
                value = str(value).lower() in ("1", "true", "yes")
            elif lookup == "in":
                value = [item for item in str(value).split(",") if item]
            queryset = queryset.filter(**{f"{name}__{lookup}": value})
            applied[expression] = value
        return queryset, applied

    def _ordering(self, model_spec, order):
        """Return a deterministic ordering.

        The primary key is always the final sort term. Without it, two rows
        sharing a sort value can swap places between pages, and a keyset cursor
        then skips or repeats them.

        A nullable sort column falls back to the primary key: NULL has no
        position in a range comparison, so a cursor over one would page
        wrongly rather than slowly.
        """

        pk = self._pk_name(model_spec)
        raw = str(order or "").strip()
        if not raw:
            return [f"-{pk}"]
        name = raw[1:] if raw.startswith("-") else raw
        known = {field.name: field for field in model_spec.fields}
        if name not in known:
            raise FieldError(f"{model_spec.label} has no field {name!r} to order by")
        if name == pk:
            return [raw]
        if known[name].null:
            return [f"-{pk}"]
        return [raw, f"-{pk}" if raw.startswith("-") else pk]

    def _after(self, queryset, order, pk_name, cursor):
        """Return the rows following one keyset cursor."""

        sort_value, last_pk = cursor
        primary = order[0]
        descending = primary.startswith("-")
        name = primary.lstrip("-")
        comparison = "lt" if descending else "gt"
        if len(order) == 1:
            return queryset.filter(**{f"{pk_name}__{comparison}": last_pk})
        return queryset.filter(
            Q(**{f"{name}__{comparison}": sort_value})
            | Q(**{name: sort_value, f"{pk_name}__{comparison}": last_pk})
        )

    def _estimate(self, model):
        """Return PostgreSQL's row estimate for a table, or ``None``."""

        from django.db import connection

        if connection.vendor != "postgresql":
            return None
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT reltuples::bigint FROM pg_class WHERE oid = to_regclass(%s)",
                    [model._meta.db_table],
                )
                row = cursor.fetchone()
        except Exception:  # noqa: BLE001 - an estimate must never break a page
            return None
        if not row or row[0] is None or row[0] < 0:
            return None
        return row[0]

    def _page_size(self, params):
        """Return a bounded page size."""

        try:
            size = int(params.get("page_size") or DEFAULT_PAGE_SIZE)
        except (TypeError, ValueError):
            size = DEFAULT_PAGE_SIZE
        return max(1, min(size, MAX_PAGE_SIZE))

    def _plain(self, row, columns):
        """Stringify values the JSON layer cannot carry natively."""

        plain = {}
        for key in columns:
            if key not in row:
                continue
            value = row[key]
            if isinstance(value, (str, int, float, bool, type(None))):
                plain[key] = value
            else:
                plain[key] = str(value)
        return plain

    def _plain_value(self, value):
        """Return one JSON-safe field value for a preview."""

        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        return str(value)
