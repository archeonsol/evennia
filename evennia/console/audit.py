"""Recording console operations.

Ordering follows the mutation bridge exactly, because that contract already got
this right and diverging would be a regression: **the domain result is written
first, the audit row second, in its own transaction.** A failure to record
cannot roll back a completed mutation or turn it into a retryable response --
the operator is warned instead.

Under the console's single-capability model the audit trail is the only
internal control that survives, so :func:`record` never raises: a caller that
has already mutated game state must not be handed an exception by its own
bookkeeping. It returns the event id on success and ``None`` on failure, and
callers surface the ``None`` as an audit warning beside an otherwise completed
operation.

"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.utils import timezone

from evennia.console.models import ConsoleAuditEvent
from evennia.console.services import freeze_plain, thaw_plain
from evennia.utils import logger

#: Panels whose rows must outlive any retention window. Moderation decisions
#: are appeal evidence; break-glass grants are what an investigation wants and
#: what an attacker would most want gone.
PERMANENT_PANELS = frozenset({"moderation", "authorization"})

#: Operations that carry executable source text and prune on their own window.
REPL_PANELS = frozenset({"repl", "sql"})


def new_event_id() -> str:
    """Return a fresh 32-character audit event id."""

    return uuid4().hex


def retention_for(panel: str, operation: str = "") -> str:
    """Classify one operation's retention.

    Args:
        panel: Panel key the operation belongs to.
        operation: Operation name, used for the break-glass exception.

    Returns:
        str: One of the :class:`~evennia.console.models.ConsoleAuditEvent`
        retention constants.
    """

    key = str(panel or "").strip().lower()
    if key in PERMANENT_PANELS or "break_glass" in str(operation or ""):
        return ConsoleAuditEvent.RETENTION_PERMANENT
    if key in REPL_PANELS:
        return ConsoleAuditEvent.RETENTION_REPL
    return ConsoleAuditEvent.RETENTION_NORMAL


def _bounded_payload(value):
    """Return one payload as bounded plain data, or an explanatory stub.

    The audit row must never be the thing that fails. If a payload cannot cross
    the codec -- too large, too deep, carrying something live -- the row still
    gets written, with a note in place of the payload.
    """

    if value is None:
        return {}
    try:
        frozen = thaw_plain(freeze_plain(value))
    except Exception as err:  # noqa: BLE001 - bounded, deliberately broad
        return {"_unrecordable": type(err).__name__}
    if isinstance(frozen, dict):
        return frozen
    return {"value": frozen}


def record(
    *,
    panel: str,
    operation: str,
    actor_id=None,
    actor_name: str = "",
    target_ref: str = "",
    outcome: str = ConsoleAuditEvent.OUTCOME_SUCCESS,
    message: str = "",
    before=None,
    after=None,
    inverse=None,
    correlation_id: str = "",
    retention: str = "",
) -> str | None:
    """Write one audit row. Never raises.

    Args:
        panel: Panel key the operation belongs to.
        operation: Operation name within that panel.
        actor_id: Primary key of the acting account, if known.
        actor_name: Display name, stored so the row outlives the account.
        target_ref: Generic reference to the affected record.
        outcome: One of the five ``ConsoleAuditEvent`` outcomes.
        message: Short human-readable detail.
        before: State before the operation, bounded plain data.
        after: State after the operation, bounded plain data.
        inverse: Payload that would undo this operation, when derivable.
            Absent means undo is genuinely unavailable, not merely unbuilt.
        correlation_id: Joins this row to render nodes and log lines.
        retention: Override the classification from :func:`retention_for`.

    Returns:
        str | None: The event id, or ``None`` if the row could not be written.
    """

    event_id = new_event_id()
    try:
        ConsoleAuditEvent.objects.create(
            event_id=event_id,
            actor_id=int(actor_id) if actor_id is not None else None,
            actor_name=str(actor_name or "")[:255],
            panel=str(panel or "")[:64],
            operation=str(operation or "")[:64],
            target_ref=str(target_ref or "")[:160],
            outcome=str(outcome or ConsoleAuditEvent.OUTCOME_SUCCESS)[:24],
            message=str(message or "")[:500],
            before=_bounded_payload(before),
            after=_bounded_payload(after),
            inverse=None if inverse is None else _bounded_payload(inverse),
            correlation_id=str(correlation_id or "")[:64],
            retention=retention or retention_for(panel, operation),
        )
    except Exception:  # noqa: BLE001 - auditing must not break the operation
        logger.log_trace(f"console audit write failed for {panel}.{operation}")
        return None
    _announce(panel, operation, event_id, actor_id, target_ref, outcome)
    return event_id


def _announce(panel, operation, event_id, actor_id, target_ref, outcome) -> None:
    """Emit one ``console.*`` subject onto the event bus.

    Decision D2: the bus is the stream and this table is the record. They are
    not redundant. The bus payload is a JSON ``TextField``; it cannot carry
    indexed before and after snapshots, an inverse for undo, the five-way
    outcome as a queryable column, or a target reference anything can filter
    on. What it can do is reach a notifier or an exporter without either of
    them polling this table.

    So the announcement carries identity, not content: enough to react to and
    to find the row, and nothing that would make the bus a second copy of the
    audit trail.

    Never raises. The row is already written and committed by the time this
    runs, and an unreachable bus must not turn a completed operation into a
    reported failure.
    """

    try:
        from evennia.eventbus import bus

        bus.emit(
            f"console.{panel}.{operation}",
            {
                "event_id": event_id,
                "actor_id": actor_id,
                "target_ref": target_ref,
                "outcome": outcome,
            },
        )
    except Exception:  # noqa: BLE001 - the record is written; the stream is best effort
        logger.log_trace(f"console bus emit failed for {panel}.{operation}")


def prune(now=None) -> dict:
    """Delete audit rows past their retention window.

    Permanent rows are never considered. Intended for a ``calendar(daily)``
    System Scheduler registration, never a per-object timer.

    Args:
        now: Reference time, for tests.

    Returns:
        dict: Deleted counts keyed by retention class.
    """

    now = now or timezone.now()
    normal_days = int(getattr(settings, "CONSOLE_AUDIT_RETENTION_DAYS", 365) or 0)
    repl_days = int(getattr(settings, "CONSOLE_AUDIT_REPL_RETENTION_DAYS", 90) or 0)
    deleted = {}
    for retention, days in (
        (ConsoleAuditEvent.RETENTION_NORMAL, normal_days),
        (ConsoleAuditEvent.RETENTION_REPL, repl_days),
    ):
        if days <= 0:
            deleted[retention] = 0
            continue
        cutoff = now - timedelta(days=days)
        count, _ = ConsoleAuditEvent.objects.filter(
            retention=retention, created_at__lt=cutoff
        ).delete()
        deleted[retention] = count
    return deleted
