"""The console's own record.

The console has written a full audit trail since phase 1 -- frozen before and
after payloads, an inverse where one is derivable, a five-way outcome, and a
correlation id -- and until now offered no way to read a single row of it. The
only audit reads anywhere were the REPL and SQL panels showing an operator
their own last fifty submissions.

That is not a small omission. Under decision D1 there is one capability and
everyone admitted can already do everything, which makes the audit trail *the*
internal control. An unreadable control satisfies an auditor on paper and helps
nobody at 03:00.

Two properties this panel must not break.

**The table is append-only.** There is no edit, no delete, and no retention
override. Undo does not rewrite the row it reverses; it writes a new row that
points back at it, so the trail records the mistake and the correction as two
facts rather than one revised one.

**The five outcomes must not render alike.** ``partial`` and
``recovery_required`` both mean "it wrote, then faulted", and the difference
between them is whether a person has to go fix something. A UI that blurs them
turns a taxonomy that exists for exactly this moment back into "it failed".

"""

from __future__ import annotations

import base64
import json

from django.core.exceptions import PermissionDenied

from evennia.console import audit
from evennia.console.models import ConsoleAuditEvent
from evennia.console.registry import Panel, io_action

#: Rows per page. The trail grows by one row per console operation, so this is
#: a table that gets large in normal use rather than as a symptom.
PAGE = 50

#: Hard ceiling regardless of what the caller asks for.
MAX_PAGE = 200

#: Payload entries rendered in a diff before it is cut short. A frozen payload
#: is already bounded by the codec budget; this bounds the *rendering*.
MAX_DIFF_ENTRIES = 200


def _encode_cursor(created_at, pk) -> str:
    """Encode one keyset position as an opaque token."""

    payload = json.dumps([created_at.isoformat() if created_at else None, pk])
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
        stamp, pk = json.loads(raw)
        return stamp, int(pk)
    except Exception:  # noqa: BLE001 - any malformed cursor is just no cursor
        return None


def _diff(before, after):
    """Return one field-by-field comparison of two frozen payloads.

    Both sides are shown for every key present in either, including keys that
    did not change. An operator reading an audit row is asking "what did this
    do", and a diff that hides the unchanged fields cannot answer "was this
    field already wrong before the change".
    """

    before = before if isinstance(before, dict) else {}
    after = after if isinstance(after, dict) else {}
    keys = sorted(set(before) | set(after))
    entries = []
    for key in keys[:MAX_DIFF_ENTRIES]:
        old = before.get(key)
        new = after.get(key)
        entries.append(
            {
                "field": str(key)[:120],
                "before": "" if old is None else str(old)[:400],
                "after": "" if new is None else str(new)[:400],
                "changed": old != new,
                "only_in": ("after" if key not in before else "before" if key not in after else ""),
            }
        )
    return {
        "entries": entries,
        "truncated": max(0, len(keys) - MAX_DIFF_ENTRIES),
        "changed_count": sum(1 for entry in entries if entry["changed"]),
    }


class AuditPanel(Panel):
    """Read the console's own audit trail, and undo what carries an inverse."""

    key = "audit"
    label = "Audit"
    description = "Every recorded console operation, its outcome, and its before/after state."
    columns = ("created_at", "actor_name", "panel", "operation", "outcome", "target_ref")
    needs_io = False

    def rows(self, ctx):
        """Return one page of the audit trail.

        Filtered on indexed columns only. Every filter here matches an index on
        ``ConsoleAuditEvent``, so a staff page cannot be the thing that
        discovers how large the table got.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``actor``, ``panel``,
                ``operation``, ``outcome``, ``target``, ``retention``,
                ``search``, ``cursor``, and ``page_size``.

        Returns:
            dict: Rows, the cursor for the next page, and the filter
            vocabularies the station offers.
        """

        params = ctx.params
        queryset = ConsoleAuditEvent.objects.all()

        actor = str(params.get("actor") or "").strip()
        if actor:
            if actor.isdigit():
                queryset = queryset.filter(actor_id=int(actor))
            else:
                queryset = queryset.filter(actor_name=actor)
        for field in ("panel", "operation", "outcome", "retention"):
            value = str(params.get(field) or "").strip()
            if value:
                queryset = queryset.filter(**{field: value})
        target = str(params.get("target") or "").strip()
        if target:
            queryset = queryset.filter(target_ref__startswith=target[:160])
        correlation = str(params.get("correlation") or "").strip()
        if correlation:
            queryset = queryset.filter(correlation_id=correlation[:64])

        # Message is not indexed. Searching it is offered because an operator
        # who remembers the wording and nothing else has no other way in, but
        # it is applied last, after the indexed filters have already narrowed
        # the scan.
        search = str(params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(message__icontains=search[:200])

        cursor = _decode_cursor(params.get("cursor"))
        if cursor is not None:
            stamp, last_pk = cursor
            if stamp:
                queryset = queryset.filter(created_at__lte=stamp).exclude(
                    created_at=stamp, id__gte=last_pk
                )

        size = self._page_size(params)
        fetched = list(
            queryset.order_by("-created_at", "-id").values(
                "id",
                "event_id",
                "created_at",
                "actor_id",
                "actor_name",
                "panel",
                "operation",
                "target_ref",
                "outcome",
                "message",
                "inverse",
                "retention",
                "correlation_id",
            )[: size + 1]
        )
        page, more = fetched[:size], len(fetched) > size
        next_cursor = ""
        if more and page:
            last = page[-1]
            next_cursor = _encode_cursor(last["created_at"], last["id"])

        return {
            "rows": [self._row(row) for row in page],
            "next_cursor": next_cursor,
            "page_size": size,
            "outcomes": [
                {"value": value, "meaning": meaning}
                for value, meaning in ConsoleAuditEvent.OUTCOME_CHOICES
            ],
            "retentions": [
                {"value": value, "meaning": meaning}
                for value, meaning in ConsoleAuditEvent.RETENTION_CHOICES
            ],
            "panels": self._distinct("panel"),
            "operations": self._distinct("operation"),
            "note": (
                "This list does not change. An undo makes a new record that refers "
                "to the record it reverses. It does not change that record."
            ),
        }

    def _row(self, row):
        """Return one listing row as plain data."""

        return {
            "id": row["id"],
            "event_id": row["event_id"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else "",
            "actor_id": row["actor_id"],
            "actor_name": row["actor_name"] or "(unknown)",
            "panel": row["panel"],
            "operation": row["operation"],
            "target_ref": row["target_ref"],
            "outcome": row["outcome"],
            "message": row["message"],
            "correlation_id": row["correlation_id"],
            "retention": row["retention"],
            # Reported from the stored inverse rather than from can_undo, which
            # needs a model instance. The rule is the same one can_undo applies.
            "can_undo": bool(row["inverse"])
            and row["outcome"] == ConsoleAuditEvent.OUTCOME_SUCCESS,
        }

    def _distinct(self, field):
        """Return the values actually present in one indexed column.

        Offering a filter for a panel that has never written a row is a filter
        that returns nothing and teaches nothing.
        """

        try:
            return sorted(
                value
                for value in ConsoleAuditEvent.objects.order_by()
                .values_list(field, flat=True)
                .distinct()[:200]
                if value
            )
        except Exception:  # noqa: BLE001 - a filter vocabulary must not break the page
            return []

    def _page_size(self, params):
        """Return the bounded page size."""

        try:
            size = int(params.get("page_size") or PAGE)
        except (TypeError, ValueError):
            size = PAGE
        return max(1, min(size, MAX_PAGE))

    def detail(self, ctx, pk):
        """Return one audit row with its payloads rendered as a diff.

        Args:
            ctx: Worker context.
            pk: Row id.

        Returns:
            dict: The row, the before/after diff, and what undo would do.

        Raises:
            LookupError: No such row.
        """

        try:
            row = ConsoleAuditEvent.objects.filter(pk=int(pk)).first()
        except (TypeError, ValueError) as err:
            raise LookupError(f"{pk!r} is not a valid audit row id") from err
        if row is None:
            raise LookupError(f"no audit row with id {pk!r}")

        outcome_meaning = dict(ConsoleAuditEvent.OUTCOME_CHOICES).get(row.outcome, "")
        return {
            "id": row.id,
            "event_id": row.event_id,
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "actor_id": row.actor_id,
            "actor_name": row.actor_name or "(unknown)",
            "panel": row.panel,
            "operation": row.operation,
            "target_ref": row.target_ref,
            "outcome": row.outcome,
            "outcome_meaning": outcome_meaning,
            "retryable": row.is_retryable,
            "message": row.message,
            "correlation_id": row.correlation_id,
            "retention": row.retention,
            "retention_meaning": dict(ConsoleAuditEvent.RETENTION_CHOICES).get(row.retention, ""),
            "diff": _diff(row.before, row.after),
            "can_undo": row.can_undo,
            "undo_reason": self._undo_reason(row),
            "inverse": row.inverse if row.can_undo else None,
        }

    def _undo_reason(self, row):
        """Return why undo is unavailable, or an empty string when it is not.

        A control that is merely absent tells an operator nothing. The reason
        distinguishes "this operation cannot be reversed" from "this one
        failed, so there is nothing to reverse".
        """

        if row.can_undo:
            return ""
        if row.outcome != ConsoleAuditEvent.OUTCOME_SUCCESS:
            return (
                f"This operation ended with the result {row.outcome!r}. The recorded "
                "values are not safe to restore."
            )
        return (
            "The console did not record how to reverse this operation. The console cannot undo it."
        )

    @io_action
    def undo(self, ctx, audit_id=None, reason=""):
        """Apply one recorded inverse.

        Runs on the IO owner because it drives the same mutation service the
        original operation did, with the same permission recomputation and the
        same lifecycle hooks. Undo is not a database rollback; it is a second
        forward operation whose values happen to be the previous ones.

        The row being reversed is never modified. A new row is written with
        ``operation="undo"``, carrying the reversed row's event id, so the
        trail holds the mistake and the correction as two facts.

        Args:
            ctx: IO context.
            audit_id: Row to reverse.
            reason: Why, recorded in the new row's message.

        Returns:
            dict: The service outcome, and the new audit event id.

        Raises:
            LookupError: No such row.
            PermissionDenied: The row carries no applicable inverse.
            ValueError: No reason was given.
        """

        note = str(reason or "").strip()
        if not note:
            raise ValueError("an undo needs a reason")

        try:
            row = ConsoleAuditEvent.objects.filter(pk=int(audit_id)).first()
        except (TypeError, ValueError) as err:
            raise LookupError(f"{audit_id!r} is not a valid audit row id") from err
        if row is None:
            raise LookupError(f"no audit row with id {audit_id!r}")
        if not row.can_undo:
            raise PermissionDenied(self._undo_reason(row))

        inverse = row.inverse or {}
        kind = str(inverse.get("kind") or "")
        if kind != "records.change":
            raise PermissionDenied(
                f"The console cannot reverse an operation of the type {kind!r}. "
                "The console reverses only a field change to a writable model."
            )

        from evennia.console.panels.records import RecordsPanel

        # io_action stamps a flag on the function; it does not wrap it, so the
        # method is called directly. Reached through the panel rather than
        # reimplemented here so an undo goes through exactly the permission
        # recomputation, coercion, and lifecycle the original write did.
        records = RecordsPanel()
        result = records.save(
            ctx,
            model=inverse.get("model"),
            pk=inverse.get("pk"),
            values=dict(inverse.get("values") or {}),
        )

        event_id = audit.record(
            panel=self.key,
            operation="undo",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=row.target_ref,
            outcome=records._audit_outcome(result.get("status")),
            before={"reversed_event": row.event_id, "reversed_operation": row.operation},
            after={"values": dict(inverse.get("values") or {})},
            message=f"undo of {row.event_id}: {note}"[:500],
            correlation_id=row.correlation_id,
        )
        return {
            "status": result.get("status", ""),
            "message": result.get("message", ""),
            "reversed_event": row.event_id,
            "audit_event": event_id,
        }
