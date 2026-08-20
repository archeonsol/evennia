"""Jobs and the event bus.

Both were committed by decision D6 -- "the Jobs panel and a ``GameEvent`` view
ship as planned" -- and neither was given a panel number, a phase, or an owner,
so the commitment was unreadable from either section alone and neither was
built. They are P20 and P21.

They are read-mostly on purpose. The queue drains itself and the bus is a
stream; a console that replayed events or hand-ran jobs would be inventing a
second scheduler beside the one the engine already has. The one write here is
requeue, which puts a dead-lettered job back where the existing drain will
pick it up rather than executing anything itself.

Every query is bounded and grouped on an indexed column. These tables grow by
one row per background task and one row per persisted event, so a staff page
must not be the thing that discovers how large they got.

"""

from __future__ import annotations

import json

from django.db.models import Count
from django.utils import timezone

from evennia.console import audit
from evennia.console.registry import Panel, io_action

#: Rows returned by any list here.
MAX_ROWS = 100

#: Dead-lettered jobs shown at once. Small on purpose: a dead letter is read,
#: not scrolled.
MAX_DEAD = 40

#: Characters of a payload or traceback kept for display.
MAX_TEXT = 2000


def _pretty(raw, limit: int = MAX_TEXT) -> str:
    """Render a stored JSON string readably, or return it unchanged.

    A payload is stored as text. Showing it as one long line is technically
    the stored value and practically unreadable, so it is re-indented when it
    parses and left alone when it does not -- a payload that is not valid JSON
    is itself worth seeing exactly as stored.
    """

    text = str(raw or "")
    try:
        return json.dumps(json.loads(text), indent=2, sort_keys=True)[:limit]
    except (TypeError, ValueError):
        return text[:limit]


class JobsPanel(Panel):
    """The durable job queue: depth, rates, dead letters, and requeue."""

    key = "jobs"
    label = "Jobs"
    description = "Queue depth by status and type, dead letters, and the requeue action."
    columns = ("job_type", "status", "attempts", "created_at")
    needs_io = False

    def rows(self, ctx):
        """Return the queue picture.

        Depth is grouped on ``status`` and ``job_type``, both indexed columns,
        so this stays cheap on a queue that has been running for months.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``status`` and
                ``job_type``.

        Returns:
            dict: Depth by status and type, the dead letters, and the backlog.
        """

        from evennia.server.models import EngineJob

        params = ctx.params
        by_status = {
            row["status"]: row["total"]
            for row in EngineJob.objects.values("status").annotate(total=Count("pk")).order_by()
        }
        by_type = [
            {"job_type": row["job_type"], "total": row["total"], "status": row["status"]}
            for row in EngineJob.objects.values("job_type", "status")
            .annotate(total=Count("pk"))
            .order_by("job_type")[:MAX_ROWS]
        ]

        now = timezone.now()
        overdue = EngineJob.objects.filter(status="leased", lease_until__lt=now).count()

        queryset = EngineJob.objects.all()
        status = str(params.get("status") or "").strip()
        if status:
            queryset = queryset.filter(status=status)
        job_type = str(params.get("job_type") or "").strip()
        if job_type:
            queryset = queryset.filter(job_type=job_type)

        rows = [
            self._job(row)
            for row in queryset.order_by("-id").values(
                "id",
                "job_id",
                "job_type",
                "status",
                "attempts",
                "max_attempts",
                "available_at",
                "lease_until",
                "last_error",
                "created_at",
            )[:MAX_ROWS]
        ]

        return {
            "rows": rows,
            "by_status": [
                {"status": name, "total": total} for name, total in sorted(by_status.items())
            ],
            "by_type": by_type,
            "dead": by_status.get("dead", 0),
            "pending": by_status.get("pending", 0),
            "leased": by_status.get("leased", 0),
            "overdue_leases": overdue,
            "types": sorted(
                {
                    value
                    for value in EngineJob.objects.order_by()
                    .values_list("job_type", flat=True)
                    .distinct()[:200]
                    if value
                }
            ),
            "backend": self._backend(),
            "note": (
                "A lease that is out of date shows that the worker stopped. The next "
                "queue run takes the job again. You do not need to do this."
            ),
        }

    def _backend(self):
        """Return which queue backend is configured, without importing it hard."""

        try:
            from evennia.jobs import queue

            return {"backend": str(queue._backend()), "enabled": bool(queue._enabled())}
        except Exception as err:  # noqa: BLE001
            return {"backend": "", "enabled": False, "reason": str(err)[:200]}

    def _job(self, row):
        """Return one job row as plain data."""

        return {
            "id": row["id"],
            "job_id": row["job_id"],
            "job_type": row["job_type"],
            "status": row["status"],
            "attempts": row["attempts"],
            "max_attempts": row["max_attempts"],
            "available_at": row["available_at"].isoformat() if row["available_at"] else "",
            "lease_until": row["lease_until"].isoformat() if row["lease_until"] else "",
            "last_error": (row["last_error"] or "")[:300],
            "created_at": row["created_at"].isoformat() if row["created_at"] else "",
        }

    def detail(self, ctx, pk):
        """Return one job with its payload and its failure.

        Args:
            ctx: Worker context.
            pk: Job row id.

        Returns:
            dict: The job, its payload, and the error that dead-lettered it.

        Raises:
            LookupError: No such job.
        """

        from evennia.server.models import EngineJob

        try:
            job = EngineJob.objects.filter(pk=int(pk)).first()
        except (TypeError, ValueError) as err:
            raise LookupError(f"{pk!r} is not a valid job id") from err
        if job is None:
            raise LookupError(f"no job with id {pk!r}")

        return {
            "id": job.id,
            "job_id": job.job_id,
            "job_type": job.job_type,
            "status": job.status,
            "attempts": job.attempts,
            "max_attempts": job.max_attempts,
            "priority": job.priority,
            "idempotency_key": job.idempotency_key or "",
            "available_at": job.available_at.isoformat() if job.available_at else "",
            "lease_until": job.lease_until.isoformat() if job.lease_until else "",
            "completed_at": job.completed_at.isoformat() if job.completed_at else "",
            "created_at": job.created_at.isoformat() if job.created_at else "",
            "payload": _pretty(job.payload_json),
            "last_error": (job.last_error or "")[:MAX_TEXT],
            "can_requeue": job.status == "dead",
        }

    @io_action
    def requeue(self, ctx, job_id=None, reason=""):
        """Return one dead-lettered job to the queue.

        Runs on the IO owner because the handler this makes eligible touches
        game state, and because the drain that will pick it up runs there. This
        does not execute the job: it resets the row so the existing drain can,
        which keeps one path to running a job rather than two.

        The attempt counter is reset. A job that dead-lettered after five
        failures and is requeued without a reset would dead-letter again on its
        first attempt, which looks like the requeue silently failed.

        Args:
            ctx: IO context.
            job_id: Job row id.
            reason: Why, recorded in the audit row.

        Returns:
            dict: The job's new state.

        Raises:
            LookupError: No such job.
            ValueError: No reason given, or the job is not dead.
        """

        from evennia.server.models import EngineJob

        note = str(reason or "").strip()
        if not note:
            raise ValueError("a requeue needs a reason")

        try:
            job = EngineJob.objects.filter(pk=int(job_id)).first()
        except (TypeError, ValueError) as err:
            raise LookupError(f"{job_id!r} is not a valid job id") from err
        if job is None:
            raise LookupError(f"no job with id {job_id!r}")
        if job.status != "dead":
            raise ValueError(
                f"This job has the status {job.status!r}. You can requeue only a job "
                "with the status 'dead'. A pending job or a leased job runs again "
                "without your help."
            )

        before = {"status": job.status, "attempts": job.attempts, "error": job.last_error[:300]}
        job.status = "pending"
        job.attempts = 0
        job.available_at = timezone.now()
        job.lease_until = None
        job.save(update_fields=["status", "attempts", "available_at", "lease_until"])

        audit.record(
            panel=self.key,
            operation="requeue",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"server.enginejob#{job.id}"[:160],
            before=before,
            after={"status": job.status, "attempts": job.attempts},
            message=f"requeue of {job.job_type}: {note}"[:500],
        )
        return {"id": job.id, "job_id": job.job_id, "status": job.status, "attempts": 0}


class EventBusPanel(Panel):
    """Persisted event-bus records.

    Labelled **Event bus**, never a bare "Events", so it stays distinguishable
    from ``evennia.actions.events`` -- the live in-process registry -- after the
    ``evennia.events`` deprecation shim is dropped.
    """

    key = "eventbus"
    label = "Event bus"
    description = "Persisted bus records by subject, with the payload rendered."
    columns = ("created_at", "subject", "actor_ref")
    needs_io = False

    def rows(self, ctx):
        """Return one page of persisted bus records.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``subject``,
                ``prefix``, ``actor``, and ``before``.

        Returns:
            dict: Rows, the subjects present, and the prefixes they group into.
        """

        from evennia.server.models import GameEvent

        params = ctx.params
        queryset = GameEvent.objects.all()

        subject = str(params.get("subject") or "").strip()
        if subject:
            queryset = queryset.filter(subject=subject[:128])
        prefix = str(params.get("prefix") or "").strip()
        if prefix:
            queryset = queryset.filter(subject__startswith=prefix[:128])
        actor = str(params.get("actor") or "").strip()
        if actor:
            queryset = queryset.filter(actor_ref=actor[:255])

        # Keyset paging on the indexed timestamp. Counting the table is
        # deliberately never done: it grows by one row per persisted event.
        before = str(params.get("before") or "").strip()
        if before:
            queryset = queryset.filter(created_at__lt=before)

        fetched = list(
            queryset.order_by("-created_at", "-id").values(
                "id", "subject", "actor_ref", "payload_json", "created_at"
            )[: MAX_ROWS + 1]
        )
        page, more = fetched[:MAX_ROWS], len(fetched) > MAX_ROWS

        subjects = sorted(
            {
                value
                for value in GameEvent.objects.order_by()
                .values_list("subject", flat=True)
                .distinct()[:400]
                if value
            }
        )
        return {
            "rows": [
                {
                    "id": row["id"],
                    "subject": row["subject"],
                    "actor_ref": row["actor_ref"],
                    "payload": _pretty(row["payload_json"], 600),
                    "created_at": row["created_at"].isoformat() if row["created_at"] else "",
                }
                for row in page
            ],
            "next_before": page[-1]["created_at"].isoformat() if more and page else "",
            "subjects": subjects,
            "prefixes": sorted({name.split(".", 1)[0] for name in subjects if name}),
            "bus": self._bus(),
            "note": (
                "This list shows only the subjects that the game saves. The event bus "
                "sends more subjects than it saves."
            ),
        }

    def _bus(self):
        """Return the bus configuration, without importing it hard."""

        try:
            from evennia.eventbus import bus

            return {
                "backend": str(bus._backend()),
                "enabled": bool(bus._enabled()),
                "persisted": sorted(str(item) for item in bus._persist_subjects()),
            }
        except Exception as err:  # noqa: BLE001
            return {"backend": "", "enabled": False, "persisted": [], "reason": str(err)[:200]}

    def detail(self, ctx, pk):
        """Return one bus record with its payload rendered.

        Args:
            ctx: Worker context.
            pk: Record id.

        Returns:
            dict: The record.

        Raises:
            LookupError: No such record.
        """

        from evennia.server.models import GameEvent

        try:
            row = GameEvent.objects.filter(pk=int(pk)).first()
        except (TypeError, ValueError) as err:
            raise LookupError(f"{pk!r} is not a valid record id") from err
        if row is None:
            raise LookupError(f"no bus record with id {pk!r}")
        return {
            "id": row.id,
            "subject": row.subject,
            "actor_ref": row.actor_ref,
            "payload": _pretty(row.payload_json),
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }
