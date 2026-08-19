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
import json

from django.apps import apps
from django.core.exceptions import FieldError, PermissionDenied, ValidationError
from django.db.models import Q

from evennia.console import audit, spec
from evennia.console.panels.coerce import CoercionError, coerce_payload
from evennia.console.registry import Panel, io_action
from evennia.console.services import (
    AdminDeleteRequest,
    AdminMutationRequest,
    delete_admin,
    mutate_admin,
    mutation_field_types,
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
                    "choices": [list(pair) for pair in by_name[name].choices]
                    if name in by_name
                    else [],
                }
                for name, types in sorted(accepted.items())
            ],
            "unsupported": [
                "relations",
                "tags",
                "password",
            ],
            "note": (
                "Relations, tags, and passwords are not written here. Each has its own "
                "service with rules a generic form cannot honour."
            ),
        }

    @io_action
    def save(self, ctx, model=None, pk=None, values=None):
        """Create or change one row through the bounded mutation service.

        Runs on the IO owner: the service reloads the actor, recomputes
        permission from fresh state, resolves foreign keys, and drives the
        model's own lifecycle. The worker only coerced JSON into the exact
        types the service demands.

        Deliberately narrow. Relations, inline tags, and passwords are refused
        rather than half-supported: a shared Tag row is cached by many owners,
        account creation has a provenance contract, and a password has its own
        service. Refusing with a reason beats a form that appears to save them.

        Args:
            ctx: IO context.
            model: Model label.
            pk: Row to change, or ``None`` to create one.
            values: Field name to submitted value.

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
        if target is not None:
            existing = (
                apps.get_model(model_spec.label)
                ._base_manager.filter(pk=target)
                .values(*[name for name, _ in concrete])
                .first()
            )
            before = {key: str(value) for key, value in (existing or {}).items()}

        result = mutate_admin(
            AdminMutationRequest(
                actor_id=int(ctx.actor_id),
                model_label=model_spec.label,
                object_id=target,
                concrete=concrete,
                relations=(),
                tags=(),
            )
        )
        payload = {
            "status": result.status,
            "object_id": result.object_id,
            "object_repr": getattr(result, "object_repr", ""),
            "message": getattr(result, "message", ""),
            "retryable": bool(getattr(result, "retryable", False)),
        }
        audit.record(
            panel=self.key,
            operation="change" if target is not None else "add",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"{model_spec.label}#{result.object_id or target or ''}"[:160],
            outcome=self._audit_outcome(result.status),
            before=before,
            after={key: str(value) for key, value in concrete},
            message=payload["message"][:500],
        )
        return payload

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

    @io_action
    def delete(self, ctx, model=None, ids=None):
        """Delete rows through the bounded mutation service.

        Runs on the IO owner because deletion invokes lifecycle hooks on game
        objects. The service recomputes protected relations and cascade
        permissions freshly on the owner; the worker-side check here is for
        fast rejection only and cannot authorize the mutation.

        Args:
            ctx: IO context.
            model: Model label to delete from.
            ids: Primary keys to delete.

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
        identifiers = tuple(int(value) for value in (ids or ()))
        if not identifiers:
            return {"deleted": [], "vetoed": [], "missing": [], "failed": []}

        result = delete_admin(
            AdminDeleteRequest(
                actor_id=int(ctx.actor_id),
                model_label=model_spec.label,
                object_ids=identifiers,
            )
        )
        payload = {
            "status": result.status,
            "deleted": list(result.deleted_ids),
            "vetoed": list(getattr(result, "vetoed_ids", ()) or ()),
            "missing": list(getattr(result, "missing_ids", ()) or ()),
            "failed": list(getattr(result, "failed_ids", ()) or ()),
        }
        audit.record(
            panel=self.key,
            operation="delete",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"{model_spec.label}#{','.join(str(i) for i in identifiers)}"[:160],
            before={"ids": list(identifiers)},
            after=payload,
            message=f"deleted {len(payload['deleted'])} of {len(identifiers)}",
        )
        return payload

    # -- helpers -------------------------------------------------------

    def _spec(self, label):
        """Return one model spec, or fail closed."""

        if not label:
            raise LookupError("a model label is required")
        return spec.get_model_spec(label)

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

        known = {field.name for field in model_spec.fields}
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
        known = {field.name for field in model_spec.fields}
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
