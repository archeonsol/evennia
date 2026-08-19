"""Console audit trail.

Django admin writes ``django.contrib.admin.models.LogEntry``. That model dies
with the admin, and it cannot hold what this trail needs: a five-way outcome,
frozen before/after payloads, an inverse for undo, and a correlation id that
joins a mutation to the render nodes and log lines around it.

Under the console's single-capability model (decision D1) the audit trail is
the *only* internal control: everyone admitted to the console can already do
everything. So this table is append-only at the service layer, has no console
write path, and appears in the Records lens read-only alongside the sanction
chain.

Rows reference actors by id and name rather than by foreign key, matching the
moderation substrate's convention: an audit record has to outlive the account
it describes.

"""

from django.db import models
from django.utils import timezone


class ConsoleAuditEvent(models.Model):
    """One recorded console operation.

    Written after the domain result, in its own transaction, per the mutation
    bridge's audit-ordering rule: a failure to record must never roll back or
    invalidate a mutation that already happened. The operator is warned
    instead.
    """

    OUTCOME_CONFLICT = "conflict"
    OUTCOME_SUCCESS = "success"
    OUTCOME_PARTIAL = "partial"
    OUTCOME_RECOVERY = "recovery_required"
    OUTCOME_INDETERMINATE = "indeterminate"
    OUTCOME_CHOICES = [
        (OUTCOME_CONFLICT, "rejected before any write"),
        (OUTCOME_SUCCESS, "completed and verified"),
        (OUTCOME_PARTIAL, "wrote, then faulted; do not retry"),
        (OUTCOME_RECOVERY, "wrote, then faulted; needs operator action"),
        (OUTCOME_INDETERMINATE, "started, outcome unknown; do not retry"),
    ]
    #: Outcomes that must never be automatically retried.
    NON_RETRYABLE = frozenset({OUTCOME_PARTIAL, OUTCOME_RECOVERY, OUTCOME_INDETERMINATE})

    #: Retention class. Moderation decisions and break-glass grants outlive any
    #: configured window: appeal evidence and investigation evidence
    #: respectively, and the latter is what an attacker would most want gone.
    RETENTION_NORMAL = "normal"
    RETENTION_REPL = "repl"
    RETENTION_PERMANENT = "permanent"
    RETENTION_CHOICES = [
        (RETENTION_NORMAL, "pruned on the standard window"),
        (RETENTION_REPL, "pruned on the REPL window"),
        (RETENTION_PERMANENT, "never pruned"),
    ]

    event_id = models.CharField(max_length=32, unique=True, db_index=True)

    # --- who ------------------------------------------------------------
    actor_id = models.IntegerField(null=True, blank=True, db_index=True)
    actor_name = models.CharField(max_length=255, default="", blank=True, db_index=True)

    # --- what -----------------------------------------------------------
    panel = models.CharField(max_length=64, db_index=True)
    operation = models.CharField(max_length=64, db_index=True)
    # Generic reference, e.g. "objects.objectdb#42". Not a FK: the target may
    # be deleted by the very operation this row records.
    target_ref = models.CharField(max_length=160, default="", blank=True, db_index=True)

    # --- result ---------------------------------------------------------
    outcome = models.CharField(
        max_length=24, choices=OUTCOME_CHOICES, default=OUTCOME_SUCCESS, db_index=True
    )
    message = models.CharField(max_length=500, default="", blank=True)

    # --- payloads -------------------------------------------------------
    # Frozen plain data under the service codec budget. ``inverse`` is present
    # only when the service could build one, which is what gates undo; an
    # absent inverse means undo is genuinely unavailable, not merely unbuilt.
    before = models.JSONField(default=dict, blank=True)
    after = models.JSONField(default=dict, blank=True)
    inverse = models.JSONField(null=True, blank=True)

    # --- joins ----------------------------------------------------------
    # Ties this row to the render nodes and log lines from the same operation.
    correlation_id = models.CharField(max_length=64, default="", blank=True, db_index=True)

    retention = models.CharField(
        max_length=16, choices=RETENTION_CHOICES, default=RETENTION_NORMAL, db_index=True
    )
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "Console audit event"
        verbose_name_plural = "Console audit events"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["actor_id", "-created_at"]),
            models.Index(fields=["panel", "operation", "-created_at"]),
            models.Index(fields=["target_ref", "-created_at"]),
            models.Index(fields=["outcome", "-created_at"]),
        ]

    def __str__(self):
        who = self.actor_name or "(unknown)"
        return f"ConsoleAuditEvent({self.panel}.{self.operation}, {who}, {self.outcome})"

    @property
    def is_retryable(self) -> bool:
        """Return whether the recorded operation may be safely retried.

        Only a deterministic pre-write rejection is retryable. Everything that
        reached a write is not, regardless of how it ended.
        """

        return self.outcome == self.OUTCOME_CONFLICT

    @property
    def can_undo(self) -> bool:
        """Return whether this row carries an inverse the console can apply."""

        return self.outcome == self.OUTCOME_SUCCESS and bool(self.inverse)
