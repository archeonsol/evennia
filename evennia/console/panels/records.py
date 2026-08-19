"""The generic table lens.

Every installed model, listed, filtered, sorted, paged, and deletable, with no
registration step. This is the panel that closes the eight-versus-everything
gap against Django admin, and it is the one that must not be compromised for
elegance: being able to look at and edit the row is the baseline every other
panel is measured against.

Two rules shape the implementation.

**Never partially load an idmapper model.** ``.only()`` and ``.defer()`` cannot
construct an uncached ``SharedMemoryModel``, and on a *cached* one they return
the cached instance with missing fields treated as "no information". The
failure is silent in development and wrong in production, so this panel reads
through ``values()`` exclusively -- which is also what keeps it working with no
IO owner at all.

**Writes stay allowlisted.** Reads are generic because reading a row cannot
break an invariant. Writes are not: a model without a registered mutation
adapter is presented read-only, with the domain service that owns it named in
the response rather than hidden behind a disabled button.

"""

from __future__ import annotations

from django.apps import apps
from django.core.exceptions import FieldError, PermissionDenied, ValidationError
from django.db.models import Q

from evennia.console import audit, spec
from evennia.console.registry import Panel, io_action
from evennia.console.services import AdminDeleteRequest, delete_admin

#: Rows a single page may return. The tables behind this panel grow by one row
#: per connection in the moderation case, and a console page must not be the
#: thing that discovers how large they got.
MAX_PAGE_SIZE = 200
DEFAULT_PAGE_SIZE = 50

#: Field classes a free-text search may scan. Deliberately narrow: searching a
#: JSONField across a large table is a full scan with no index to help it.
_SEARCHABLE_KINDS = frozenset({"CharField", "TextField", "SlugField", "EmailField"})


class RecordsPanel(Panel):
    """List, inspect, and delete rows of any installed model."""

    key = "records"
    label = "Records"
    description = "Every installed model, including those the admin never registered."
    columns = ("pk", "label")
    needs_io = False

    # -- reads ---------------------------------------------------------

    def models(self, ctx):
        """Return every installed model with its write policy.

        Used by the panel's own model picker, so the frontend does not need a
        second request to know which models it may offer an edit button for.
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
            ctx: Worker context. ``ctx.params`` may carry ``model``, ``page``,
                ``page_size``, ``search``, and ``order``.

        Returns:
            dict: Rows, the page window, the total count, and the model's
            write policy so the caller knows what it may offer.
        """

        params = ctx.params
        model_spec = self._spec(params.get("model"))
        model = apps.get_model(model_spec.label)
        fields = self._visible_fields(model_spec)

        queryset = model._base_manager.all()
        queryset = self._filtered(queryset, model_spec, params.get("search"))
        order = self._ordering(model_spec, params.get("order"))
        if order:
            queryset = queryset.order_by(*order)

        page, page_size = self._page(params)
        offset = (page - 1) * page_size
        total = queryset.count()
        # values() rather than a partial load: see the module docstring.
        window = list(queryset.values(*fields)[offset : offset + page_size])

        return {
            "model": model_spec.label,
            "verbose_name_plural": model_spec.verbose_name_plural,
            "fields": fields,
            "rows": [self._plain(row) for row in window],
            "page": page,
            "page_size": page_size,
            "total": total,
            "writable": model_spec.writable,
            "write_via": model_spec.write_via,
            "storage": model_spec.storage,
        }

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
        except (ValueError, ValidationError) as err:
            raise LookupError(f"{pk!r} is not a valid identifier for {model_spec.label}") from err
        if row is None:
            raise LookupError(f"no {model_spec.label} with primary key {pk!r}")
        return {
            "model": model_spec.label,
            "record": self._plain(row),
            "fields": model_spec.as_dict()["fields"],
            "writable": model_spec.writable,
            "write_via": model_spec.write_via,
        }

    # -- writes --------------------------------------------------------

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
        """Return one model spec, or fail closed.

        Raises:
            LookupError: The label names no installed model.
        """

        if not label:
            raise LookupError("a model label is required")
        return spec.get_model_spec(label)

    def _visible_fields(self, model_spec):
        """Return the field names a list view reads.

        Large text and JSON columns are omitted from the list: they inflate
        every page for a value nobody reads at list level, and the detail view
        returns them anyway.
        """

        skipped = {"TextField", "JSONField", "BinaryField"}
        names = [
            item.name for item in model_spec.fields if item.kind not in skipped or item.primary_key
        ]
        return names or [item.name for item in model_spec.fields[:1]]

    def _filtered(self, queryset, model_spec, search):
        """Apply a bounded free-text filter across indexable text fields."""

        term = str(search or "").strip()
        if not term:
            return queryset
        if len(term) > 200:
            term = term[:200]
        clause = Q()
        matched = False
        for item in model_spec.fields:
            if item.kind in _SEARCHABLE_KINDS:
                clause |= Q(**{f"{item.name}__icontains": term})
                matched = True
        return queryset.filter(clause) if matched else queryset.none()

    def _ordering(self, model_spec, order):
        """Validate an ordering request against the model's own fields."""

        raw = str(order or "").strip()
        if not raw:
            return tuple(model_spec.default_ordering)
        name = raw[1:] if raw.startswith("-") else raw
        known = {item.name for item in model_spec.fields}
        if name not in known:
            raise FieldError(f"{model_spec.label} has no field {name!r} to order by")
        return (raw,)

    def _page(self, params):
        """Return a bounded ``(page, page_size)`` pair."""

        try:
            page = max(1, int(params.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            size = int(params.get("page_size") or DEFAULT_PAGE_SIZE)
        except (TypeError, ValueError):
            size = DEFAULT_PAGE_SIZE
        return page, max(1, min(size, MAX_PAGE_SIZE))

    def _plain(self, row):
        """Stringify values the JSON layer cannot carry natively.

        ``values()`` returns dates, decimals, and UUIDs as Python objects. The
        boundary codec would reject or mangle some of them, so they are
        rendered here where the model context is still available.
        """

        plain = {}
        for key, value in row.items():
            if isinstance(value, (str, int, float, bool, type(None))):
                plain[key] = value
            else:
                plain[key] = str(value)
        return plain
