"""The moderation queue.

Not a new surface: a promotion. The engine has owned the models, the capture
path, the detection, the enforcement, the tamper-evident hash chain, and the
retention policy since ``+underspire.209``, while the only way to look at any
of it lived downstream in the game. This is that view moved up to the substrate
it belongs to.

Three properties of the game-side service are carried across deliberately.

**Withholding is not shortening.** A truncated fingerprint and a redacted one
must never look alike, or a staffer reads "withheld" as a short value and
compares two rows that were never comparable.

**Every query is capped.** These tables grow by one row per connection. A staff
page must not be the thing that discovers how large they got.

**Nothing here decides anything.** Detection writes flags; a person turns a
flag into a sanction. There is no code path from an observation to an
enforcement action that does not pass through a staff member's hands, and this
panel does not add one.

One thing changes. The game gated raw addresses behind a second capability;
the console has no capability grid (decision D1), so an address is **masked by
default and revealed by an audited action** instead. Nobody is prevented,
everybody is recorded -- which is what the split was really protecting, since
an appeal never requires that staff have seen the address.

"""

from __future__ import annotations

from django.utils import timezone

from evennia.console import audit
from evennia.console.registry import Panel, io_action

#: Row caps. Each table grows by one row per connection or per detection.
QUEUE_FLAGS = 40
QUEUE_SANCTIONS = 30
QUEUE_SESSIONS = 25

#: Key kinds whose raw value identifies a person's network or device. Masked
#: until an operator asks, and the asking is recorded.
ADDRESS_KINDS = frozenset({"ip", "ip_hash", "device_token", "client_fp", "csessid"})

#: Characters of an opaque key kept when shortening for readability.
KEEP = 8


def mask(value, reveal=False):
    """Return one opaque value shortened, or the marker that it is withheld.

    Shortening and withholding are different acts and must look different. A
    hash truncated for reading still identifies the row; a redaction does not,
    and a staffer who mistakes one for the other compares two rows that were
    never comparable.
    """

    text = str(value or "")
    if not text:
        return ""
    if not reveal:
        return "withheld"
    return text[:KEEP] + "…" if len(text) > KEEP else text


def _stamp(value):
    """Render one timestamp as plain text."""

    return value.isoformat() if value else ""


class ModerationPanel(Panel):
    """The flag queue, the sanctions it produces, and the sessions behind both."""

    key = "moderation"
    label = "Moderation"
    description = "Flags awaiting a person, active sanctions, and recent connections."
    columns = ("kind", "account", "severity", "state")
    moderation_only = True
    needs_io = False

    # -- the queue -----------------------------------------------------

    def rows(self, ctx):
        """Return the queue: open flags, active sanctions, recent sessions.

        Args:
            ctx: Worker context. ``ctx.params`` may carry ``state``.

        Returns:
            dict: The three lists, each capped, plus their totals.
        """

        from evennia.server.models import ModerationFlag, Sanction, SessionRecord

        wanted = str(ctx.params.get("state") or "").strip().lower()
        flags = ModerationFlag.objects.all()
        if wanted:
            flags = flags.filter(state=wanted)
        else:
            flags = flags.filter(state__in=list(ModerationFlag.OPEN_STATES))

        flag_rows = [
            {
                "id": row["id"],
                "kind": row["kind"],
                "severity": row["severity"],
                "account": row["account_name"],
                "summary": row["summary"],
                "seen_count": row["seen_count"],
                "state": row["state"],
                "first_seen": _stamp(row["created_at"]),
                "last_seen": _stamp(row["last_seen_at"]),
            }
            for row in flags.order_by("-severity", "-last_seen_at").values(
                "id",
                "kind",
                "severity",
                "account_name",
                "summary",
                "seen_count",
                "state",
                "created_at",
                "last_seen_at",
            )[:QUEUE_FLAGS]
        ]

        sanction_rows = [
            {
                "id": row["id"],
                "subject_type": row["subject_type"],
                "subject": self._subject_display(row["subject_type"], row["subject_value"]),
                "level": row["level"],
                "reason": row["reason"],
                "actor": row["actor_name"],
                "created": _stamp(row["created_at"]),
                "expires": _stamp(row["expires_at"]) or "indefinite",
                "silent": row["silent"],
            }
            for row in Sanction.objects.active()
            .order_by("-created_at")
            .values(
                "id",
                "subject_type",
                "subject_value",
                "level",
                "reason",
                "actor_name",
                "created_at",
                "expires_at",
                "silent",
            )[:QUEUE_SANCTIONS]
        ]

        session_rows = [self._session_row(row) for row in self._recent_sessions(SessionRecord)]

        return {
            "flags": flag_rows,
            "sanctions": sanction_rows,
            "sessions": session_rows,
            "open_flags": ModerationFlag.objects.filter(
                state__in=list(ModerationFlag.OPEN_STATES)
            ).count(),
            "flag_states": [choice[0] for choice in ModerationFlag.STATE_CHOICES],
            "levels": [choice[0] for choice in Sanction.LEVEL_CHOICES],
            "subject_types": [choice[0] for choice in Sanction.SUBJECT_CHOICES],
            "caps": {
                "flags": QUEUE_FLAGS,
                "sanctions": QUEUE_SANCTIONS,
                "sessions": QUEUE_SESSIONS,
            },
            "note": ("Detection writes flags. Only a person turns one into a sanction."),
        }

    def _recent_sessions(self, model):
        """Return the most recent connection records, capped."""

        return model.objects.order_by("-connected_at").values(
            "id",
            "account_name",
            "protocol",
            "connected_at",
            "disconnected_at",
            "ip",
            "cidr",
            "asn_org",
            "country",
            "client_fp",
            "device_token",
            "xff_present",
            "xff_applied",
        )[:QUEUE_SESSIONS]

    def _session_row(self, row):
        """Render one connection record, with its address provenance stated.

        When a proxy header was sent but not trusted, the recorded address is
        the proxy's own and *every* address-based signal on the row is void.
        That has to be a sentence, not two booleans an operator has to
        reconstruct the meaning of -- otherwise somebody bans a reverse proxy.
        """

        trustworthy = not (row["xff_present"] and not row["xff_applied"])
        return {
            "id": row["id"],
            "account": row["account_name"],
            "protocol": row["protocol"],
            "connected": _stamp(row["connected_at"]),
            "disconnected": _stamp(row["disconnected_at"]),
            "ip": mask(row["ip"]),
            "cidr": row["cidr"],
            "network": row["asn_org"],
            "country": row["country"],
            "client_fp": mask(row["client_fp"]),
            "device_token": mask(row["device_token"]),
            "address_trustworthy": trustworthy,
            "address_warning": (
                ""
                if trustworthy
                else (
                    "A proxy header was sent but not trusted, so the recorded address "
                    "is the proxy's own. Do not sanction on any address signal from "
                    "this row."
                )
            ),
        }

    def _subject_display(self, subject_type, value):
        """Show a sanction subject, masking the address-grade kinds."""

        if subject_type in ADDRESS_KINDS:
            return mask(value)
        return str(value or "")

    # -- one flag ------------------------------------------------------

    def detail(self, ctx, pk):
        """Return one flag with its evidence.

        Raises:
            LookupError: No such flag.
        """

        from evennia.server.models import ModerationFlag

        try:
            row = ModerationFlag.objects.filter(pk=pk).values().first()
        except (ValueError, TypeError) as err:
            raise LookupError(f"{pk!r} is not a valid flag identifier") from err
        if row is None:
            raise LookupError(f"no moderation flag with id {pk!r}")

        evidence = row.get("evidence") or {}
        return {
            "id": row["id"],
            "kind": row["kind"],
            "severity": row["severity"],
            "account": row["account_name"],
            "session_uid": mask(row["session_uid"]),
            "summary": row["summary"],
            "state": row["state"],
            "seen_count": row["seen_count"],
            "first_seen": _stamp(row["created_at"]),
            "last_seen": _stamp(row["last_seen_at"]),
            "resolution_note": row["resolution_note"],
            "resolved_by": row["resolved_by_name"],
            "evidence": [
                {"key": key, "value": self._evidence_value(key, value)}
                for key, value in sorted(evidence.items())
            ],
        }

    def _evidence_value(self, key, value):
        """Mask evidence entries whose key names an address-grade signal."""

        if any(kind in key.lower() for kind in ("ip", "token", "fp", "csessid")):
            return mask(value)
        return str(value)[:400]

    # -- decisions -----------------------------------------------------

    @io_action
    def resolve(self, ctx, flag_id=None, state=None, note=""):
        """Record what a staff member decided about a flag.

        Args:
            ctx: IO context.
            flag_id: The flag.
            state: One of the flag's own review states.
            note: Why.

        Returns:
            dict: The flag's new state.

        Raises:
            LookupError: The flag or state is unknown.
        """

        from evennia.moderation.flags import resolve_flag
        from evennia.server.models import ModerationFlag

        flag = ModerationFlag.objects.filter(pk=flag_id).first()
        if flag is None:
            raise LookupError(f"no moderation flag with id {flag_id!r}")
        before = flag.state
        try:
            resolved = resolve_flag(
                flag, state=str(state or ""), actor=self._actor(ctx), note=str(note or "")
            )
        except ValueError as err:
            raise LookupError(str(err)) from err

        audit.record(
            panel=self.key,
            operation="flag_resolve",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"server.moderationflag#{flag.pk}",
            before={"state": before},
            after={"state": resolved.state},
            message=str(note or "")[:500],
        )
        return {"id": resolved.pk, "state": resolved.state, "note": resolved.resolution_note}

    @io_action
    def sanction(
        self,
        ctx,
        subject_type=None,
        subject_value=None,
        level=None,
        reason="",
        staff_note="",
        expires_at=None,
        silent=False,
        flag_id=None,
    ):
        """Issue a sanction, optionally closing the flag that prompted it.

        The one path that creates a sanction, and it starts with a person.

        Returns:
            dict: The sanction as recorded.

        Raises:
            LookupError: The subject type or level is unknown.
        """

        from evennia.moderation.sanctions import issue_sanction
        from evennia.server.models import ModerationFlag, Sanction

        known_types = {choice[0] for choice in Sanction.SUBJECT_CHOICES}
        known_levels = {choice[0] for choice in Sanction.LEVEL_CHOICES}
        if str(subject_type) not in known_types:
            raise LookupError(f"{subject_type!r} is not a sanction subject")
        if str(level) not in known_levels:
            raise LookupError(f"{level!r} is not a sanction level")
        if not str(subject_value or "").strip():
            raise LookupError("a subject value is required")

        flag = ModerationFlag.objects.filter(pk=flag_id).first() if flag_id else None
        evidence = dict(flag.evidence or {}) if flag is not None else {}
        if flag is not None:
            evidence.setdefault("flag_id", flag.pk)
            evidence.setdefault("flag_kind", flag.kind)

        sanction = issue_sanction(
            subject_type=str(subject_type),
            subject_value=subject_value,
            level=str(level),
            actor=self._actor(ctx),
            reason=str(reason or ""),
            staff_note=str(staff_note or ""),
            expires_at=expires_at,
            evidence=evidence,
            silent=bool(silent),
        )

        if flag is not None:
            from evennia.moderation.flags import resolve_flag

            resolve_flag(
                flag,
                state=ModerationFlag.STATE_ACTIONED,
                actor=self._actor(ctx),
                note=str(reason or ""),
                sanction=sanction,
            )

        audit.record(
            panel=self.key,
            operation="sanction_issue",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"server.sanction#{sanction.pk}",
            after={
                "subject_type": sanction.subject_type,
                "level": sanction.level,
                "silent": sanction.silent,
                "expires_at": _stamp(sanction.expires_at) or "indefinite",
            },
            message=str(reason or "")[:500],
        )
        return {
            "id": sanction.pk,
            "level": sanction.level,
            "subject_type": sanction.subject_type,
            "subject": self._subject_display(sanction.subject_type, sanction.subject_value),
            "expires": _stamp(sanction.expires_at) or "indefinite",
            "flag_closed": flag.pk if flag is not None else None,
        }

    @io_action
    def revoke(self, ctx, sanction_id=None, reason=""):
        """Lift a sanction. The row stays; history is never deleted.

        Raises:
            LookupError: No such sanction.
        """

        from evennia.moderation.sanctions import revoke_sanction
        from evennia.server.models import Sanction

        sanction = Sanction.objects.filter(pk=sanction_id).first()
        if sanction is None:
            raise LookupError(f"no sanction with id {sanction_id!r}")
        if not str(reason or "").strip():
            raise LookupError("a reason is required to lift a sanction")

        revoked = revoke_sanction(sanction, actor=self._actor(ctx), reason=str(reason))
        audit.record(
            panel=self.key,
            operation="sanction_revoke",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"server.sanction#{revoked.pk}",
            before={"active": True},
            after={"active": False, "reason": str(reason)[:200]},
            message=str(reason)[:500],
        )
        return {"id": revoked.pk, "revoked": _stamp(revoked.revoked_at)}

    @io_action
    def reveal(self, ctx, record=None, record_id=None, field=None, reason=""):
        """Show one masked value, and record that it was shown.

        The console has no address capability, so this replaces the game's
        second grant. An appeal never requires that staff have seen an address,
        so the useful property was never prevention -- it was knowing who
        looked. Nobody is blocked; everybody is recorded, permanently.

        Raises:
            LookupError: The record or field is unknown.
        """

        from evennia.server.models import ModerationFlag, Sanction, SessionRecord

        models = {
            "session": (SessionRecord, {"ip", "ip_hash", "device_token", "client_fp", "csessid"}),
            "sanction": (Sanction, {"subject_value"}),
            "flag": (ModerationFlag, {"session_uid"}),
        }
        entry = models.get(str(record or "").lower())
        if entry is None:
            raise LookupError(f"{record!r} is not a record this panel can reveal")
        model, allowed = entry
        if str(field) not in allowed:
            raise LookupError(f"{field!r} is not a revealable field on a {record}")

        row = model.objects.filter(pk=record_id).values(str(field)).first()
        if row is None:
            raise LookupError(f"no {record} with id {record_id!r}")

        audit.record(
            panel=self.key,
            operation="reveal",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"{model._meta.label_lower}#{record_id}",
            after={"field": str(field)},
            message=str(reason or "")[:500],
            retention="permanent",
        )
        return {"record": record, "id": record_id, "field": field, "value": row[str(field)]}

    @io_action
    def verify_chain(self, ctx, limit=0):
        """Check the sanction hash chain.

        Tamper evidence nobody can check is decoration.
        """

        from evennia.moderation.sanctions import verify_chain

        return dict(verify_chain(limit=int(limit or 0)))

    def _actor(self, ctx):
        """Return the acting account, freshly loaded on the owner."""

        from evennia.accounts.models import AccountDB

        return AccountDB.objects.filter(pk=ctx.actor_id).first()
