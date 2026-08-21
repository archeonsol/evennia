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

import re

from django.core.exceptions import PermissionDenied
from django.utils import timezone

from evennia.console import audit
from evennia.console.registry import (
    ADDRESS_SUBJECTS,
    CONSOLE_MODERATION_ADDRESS,
    CONSOLE_MODERATION_PERMANENT,
    Panel,
    io_action,
)

#: Row caps. Each table grows by one row per connection or per detection.
QUEUE_FLAGS = 40
QUEUE_SANCTIONS = 30
QUEUE_SESSIONS = 25

#: Key kinds whose raw value identifies a person's network, device, or client
#: build. Masked until an operator asks, and the asking is recorded.
#:
#: The signature columns belong here even though none of them can be banned. An
#: opaque identifier that reaches the browser is recoverable from devtools, and
#: an audit trail that records a reveal nobody had to perform is a lie. What a
#: value can be used *for* does not decide whether it is shown; that it
#: identifies somebody does.
ADDRESS_KINDS = frozenset(
    {
        "ip",
        "ip_hash",
        "device_token",
        "client_fp",
        "csessid",
        "telnet_sig",
        "tls_sig",
        "http_fp",
        "http_order_fp",
    }
)

#: Characters of an opaque key kept when shortening for readability.
KEEP = 8

#: Sessions read when counting what an address sanction would reach. Bounded:
#: the answer is an order of magnitude, and an exact count of a busy network
#: costs a scan to change a number nobody reads differently.
COLLATERAL_SAMPLE = 5000

#: Distinct accounts listed by name in a collateral report. Past this the count
#: is what matters, not the roll call.
COLLATERAL_NAMES = 25

#: The signatures a connection can carry, and what an operator should call them.
#: Order is the order they are shown in, not a ranking: the detector's own
#: ranking lives in ``evennia.moderation.detect.CORRELATION_SIGNATURES``.
SESSION_SIGNATURES = (
    ("telnet_sig", "client negotiation"),
    ("tls_sig", "browser handshake"),
    ("http_order_fp", "header order"),
    ("csessid", "browser session"),
)

#: The identity columns an account dossier lists, and the sanction subject each
#: one can become. ``None`` means the value cannot be sanctioned: a handshake
#: and a negotiation identify a *build*, so banning one bans everybody who uses
#: that software. They are here to be compared, never to be banned.
DOSSIER_COLUMNS = (
    ("cidr", "network", "cidr"),
    ("device_token", "device token", "device_token"),
    ("client_fp", "client fingerprint", "client_fp"),
    ("csessid", "browser session", "csessid"),
    ("telnet_sig", "client negotiation", None),
    ("tls_sig", "browser handshake", None),
)

#: Distinct values listed per column, and accounts listed per value. Both are
#: caps on a staff page, not on the data: a network with two hundred names on
#: it is answered by the count, and the roll call adds nothing after the first
#: dozen.
DOSSIER_KEYS = 20
DOSSIER_MATCHES = 12

#: Sessions read when reporting which signals are arriving. Bounded for the
#: same reason every other query here is: the answer is a proportion.
SIGNAL_SAMPLE = 500

#: Protocols that carry a TLS handshake and HTTP headers. Everything else
#: reaches the portal over a raw socket and has neither, so counting it as
#: missing coverage would report a fault that is not one.
#: Substrings that identify a connection arriving over HTTP, and one that
#: identifies a raw socket speaking telnet.
#:
#: Matched as substrings rather than as an exact list of names. The recorded
#: value is the protocol's own ``protocol_key``, and that string is not stable
#: across games: the shipped web client records ``webclient/websocket`` while a
#: game that subclasses it may record ``websocket`` or ``webclient_ajax``. An
#: exact list gets one of them wrong, and getting it wrong here reports a
#: correctly configured proxy as a broken one.
WEB_PROTOCOL_MARKERS = ("webclient", "websocket", "ajax")
TELNET_PROTOCOL_MARKERS = ("telnet",)


def protocol_family(protocol):
    """Return which signals a connection of this protocol could carry.

    Three answers, not two. A Discord relay, an IRC bot and an SSH session are
    neither web nor telnet: they carry no TLS handshake and negotiate no telnet
    option, so counting them in either population measures nothing. Treating
    "not web" as "raw socket" is what made a game whose players all use the web
    client report its client negotiation as absent -- the denominator was its
    Discord links, which can never negotiate anything.

    Args:
        protocol (str): The recorded ``protocol_key``.

    Returns:
        str: ``"web"``, ``"telnet"``, or ``""`` for neither.
    """

    name = str(protocol or "").lower()
    if any(marker in name for marker in WEB_PROTOCOL_MARKERS):
        return "web"
    if any(marker in name for marker in TELNET_PROTOCOL_MARKERS):
        return "telnet"
    return ""


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
            "ip_hash",
            "telnet_sig",
            "tls_sig",
            "http_order_fp",
            "csessid",
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
        address_state, address_state_note = self._address_state(row)
        return {
            "id": row["id"],
            "account": row["account_name"],
            "protocol": row["protocol"],
            "connected": _stamp(row["connected_at"]),
            "disconnected": _stamp(row["disconnected_at"]),
            "ip": mask(row["ip"]),
            "address_state": address_state,
            "address_state_note": address_state_note,
            "cidr": row["cidr"],
            "network": row["asn_org"],
            "country": row["country"],
            "client_fp": mask(row["client_fp"]),
            "device_token": mask(row["device_token"]),
            "signatures": [
                {
                    "field": field,
                    "label": label,
                    "value": mask(row[field]),
                    "present": bool(row[field]),
                }
                for field, label in SESSION_SIGNATURES
            ],
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

    def _address_state(self, row):
        """Return why this row has no address, when it has none.

        Three different facts arrive at the panel as the same empty string, and
        an operator reading a blank cell cannot tell them apart:

        ``held`` -- the address is recorded and is being withheld.

        ``purged`` -- the address was recorded and the retention sweep cleared
        it. The hash outlived it, so the row still matches and still bans.

        ``absent`` -- no address was ever recorded for this connection.

        The discriminator is the hash, not the row's age. Retention windows are
        settings that change, and a row purged under an old window would be
        described wrongly by any calculation from today's.

        Returns:
            tuple: The state, and a sentence for an operator, empty when the
            address is simply being withheld.
        """

        if row.get("ip"):
            return "held", ""
        if row.get("ip_hash"):
            return (
                "purged",
                (
                    "The server deleted this address after the retention period. "
                    "The network and the hash remain, so this row still matches "
                    "and you can still ban it."
                ),
            )
        return "absent", "The server recorded no address for this connection."

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

    #: Evidence keys whose value identifies a network, a device, or a client
    #: build. Matched exactly or by suffix, never by substring: ``signal`` is a
    #: key on every flag and carries only the detector's own name, and a
    #: substring rule on ``sig`` would withhold it from every dossier.
    SECRET_EVIDENCE_KEYS = frozenset({"ip", "cidr", "csessid", "matches"})
    SECRET_EVIDENCE_SUFFIXES = ("_ip", "_hash", "_token", "_fp", "_sig")

    def _evidence_value(self, key, value):
        """Mask evidence entries whose key names an address-grade signal."""

        name = str(key).lower()
        if name in self.SECRET_EVIDENCE_KEYS or name.endswith(self.SECRET_EVIDENCE_SUFFIXES):
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

    # -- guards --------------------------------------------------------

    #: One duration word. The same grammar the ``@ban`` command and the staff
    #: web form use, so a ban issued from a browser and one issued from the
    #: command line cannot mean different things by ``7d``.
    DURATION = re.compile(r"^(\d+)([mhdw])$")
    DURATION_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}

    def _expiry(self, expires_at):
        """Return ``(datetime_or_None, is_permanent)`` from what was submitted.

        Accepts a duration word (``30m``, ``12h``, ``7d``, ``2w``), the word
        ``perm``, an ISO timestamp, or nothing.

        This exists because the console previously passed the submitted value
        straight through to ``issue_sanction``, which needs a datetime. A value
        arriving as JSON is a string, so **every timed sanction the console
        tried to issue raised inside the hash chain**. Nothing caught it,
        because no test issued one with an expiry.

        Args:
            expires_at: Submitted duration, timestamp, or empty value.

        Returns:
            tuple: The expiry, and whether the request is for an indefinite
            sanction.

        Raises:
            ValueError: The value is neither a duration nor a timestamp.
        """

        from datetime import datetime, timedelta

        text = str(expires_at or "").strip().lower()
        if not text or text == "perm":
            return None, True

        match = self.DURATION.match(text)
        if match:
            amount, unit = match.groups()
            delta = timedelta(**{self.DURATION_UNITS[unit]: int(amount)})
            return timezone.now() + delta, False

        try:
            parsed = datetime.fromisoformat(str(expires_at))
        except (TypeError, ValueError) as err:
            raise ValueError(
                f"{expires_at!r} is not a duration. Enter 30m, 12h, 7d, or 2w. "
                "Enter perm for a ban that never ends."
            ) from err
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed, False

    def _require_look_reason(self, reason):
        """Return the reason for looking at a masked value, or refuse.

        An audit row that says somebody looked and not why is a log line, not
        an audit. The reason is the part an appeal or a review actually reads.
        """

        text = str(reason or "").strip()
        if not text:
            raise PermissionDenied(
                "Enter why you need this value. The console keeps this record permanently."
            )
        return text

    def _require_reason(self, reason):
        """Return the reason text, or refuse.

        The reason is shown to the player. A sanction with an empty one tells
        somebody they cannot play and does not tell them anything else, which
        makes an appeal impossible to write and impossible to answer.
        """

        text = str(reason or "").strip()
        if not text:
            raise PermissionDenied(
                "Enter the reason. The player reads this text. Do not write how you found them."
            )
        return text

    def _require_address_authority(self, ctx, subject_type):
        """Refuse an address-like subject without the address capability.

        A sanction on an account reaches one person. A sanction on a network,
        an autonomous system, or a device token reaches everybody who shares
        it, and behind a carrier-grade NAT that can be thousands of people who
        did nothing. The two are not the same decision and do not carry the
        same authority.
        """

        if str(subject_type) in ADDRESS_SUBJECTS and not ctx.has(CONSOLE_MODERATION_ADDRESS):
            raise PermissionDenied(
                "You cannot ban a single address or a device. Ask a staff member "
                "who has the address permission. You can ban the network instead, "
                "which is what most moderation work needs."
            )

    def _permanent_requested(self, expires_at) -> bool:
        """Return whether this request asks for a sanction that never expires."""

        return self._expiry(expires_at)[1]

    def collateral(self, ctx, subject_type=None, subject_value=None):
        """Report how many accounts a sanction on this subject would reach.

        Worker-side and read-only. This is the number the console never showed
        at the moment of decision: a /24 can be one household or a whole
        campus, and the two look identical in a form field.

        Counted from the retained session history, which is bounded by the
        retention window. An address purged by the ninety-day sweep leaves its
        network behind, so the count still works after the address is gone.

        Args:
            ctx: Worker context.
            subject_type: Sanction subject type.
            subject_value: Sanction subject value.

        Returns:
            dict: Distinct accounts seen on this subject, and how many.

        Raises:
            LookupError: The subject type carries no session history.
        """

        from evennia.server.models import SessionRecord

        column = {
            "ip": "ip_hash",
            "cidr": "cidr",
            "asn": "asn",
            "device_token": "device_token",
            "client_fp": "client_fp",
            "csessid": "csessid",
            "account": "account_name",
        }.get(str(subject_type or ""))
        if column is None:
            raise LookupError(
                f"The console cannot count the players that a ban of the type "
                f"{subject_type!r} affects."
            )

        value = str(subject_value or "").strip()
        if not value:
            raise LookupError("Enter the subject value.")
        # An address subject is matched on its hash, because the raw column is
        # cleared by the retention sweep and the hash is not.
        if str(subject_type) == "ip":
            from evennia.moderation.capture import hash_value

            value = hash_value(value)

        rows = (
            SessionRecord.objects.filter(**{column: value})
            .order_by("-id")
            .values("account_name", "account_id")[:COLLATERAL_SAMPLE]
        )
        accounts, sessions = {}, 0
        for row in rows:
            sessions += 1
            name = row["account_name"] or ""
            if name:
                accounts[name] = row["account_id"]

        names = sorted(accounts)
        return {
            "subject_type": str(subject_type),
            "subject_value": str(subject_value),
            "accounts": names[:COLLATERAL_NAMES],
            "account_count": len(names),
            "sessions": sessions,
            "capped": sessions >= COLLATERAL_SAMPLE,
            "note": (
                "This count uses the connections that the server still stores. It "
                "does not include connections that the server deleted."
            ),
        }

    def account(self, ctx, name=""):
        """One account's identity keys, and which other accounts share them.

        The alt-correlation view. Exact matches on indexed columns and nothing
        else: either two accounts used the same device token or they did not.
        No score is computed and none would be usable, because the package's
        rule is that any conclusion has to be showable to the player it is used
        against, and "87% likely" cannot be shown to anybody.

        Two queries per column regardless of how many keys come back. The
        surface this replaces ran one query per *value*, which on an account
        with a long history was eighty round trips to render one page. Grouping
        the shared-with lookup by column costs one ``IN`` clause on an index and
        answers the same question.

        Args:
            ctx: Worker context.
            name: The account name to look up.

        Returns:
            dict: The keys, who shares each, the account's sanctions, and the
            flags that name it.

        Raises:
            LookupError: No name was given.
        """

        from django.db.models import Count, Max, Min

        from evennia.server.models import ModerationFlag, Sanction, SessionRecord

        name = str(name or "").strip()
        if not name:
            raise LookupError("Enter the account name.")

        mine = SessionRecord.objects.filter(account_name__iexact=name)
        bounds = mine.aggregate(first=Min("connected_at"), last=Max("connected_at"))

        keys = []
        for column, label, subject in DOSSIER_COLUMNS:
            grouped = list(
                mine.exclude(**{column: ""})
                .values(column)
                .annotate(
                    sessions=Count("id"),
                    first_seen=Min("connected_at"),
                    last_seen=Max("connected_at"),
                )
                .order_by("-last_seen")[:DOSSIER_KEYS]
            )
            values = [group[column] for group in grouped if group[column]]
            if not values:
                continue

            shared = {}
            for value, other in (
                SessionRecord.objects.filter(**{f"{column}__in": values})
                .exclude(account_name__iexact=name)
                .exclude(account_name="")
                # The model orders by connected_at, and an ordering column
                # joins the DISTINCT. That would make each *session* distinct
                # rather than each pair, and one busy account would fill the
                # limit with itself and hide everybody else on the key.
                .order_by()
                .values_list(column, "account_name")
                .distinct()[: DOSSIER_MATCHES * len(values)]
            ):
                shared.setdefault(value, []).append(other)

            banned = set()
            if subject:
                banned = set(
                    Sanction.objects.active()
                    .filter(subject_type=subject, subject_value__in=values)
                    .values_list("subject_value", flat=True)
                )

            for group in grouped:
                value = group[column]
                others = sorted(set(shared.get(value, [])))
                keys.append(
                    {
                        "kind": column,
                        "label": label,
                        "value": self._subject_display(column, value),
                        "sessions": group["sessions"],
                        "first_seen": _stamp(group["first_seen"]),
                        "last_seen": _stamp(group["last_seen"]),
                        "shared_with": others[:DOSSIER_MATCHES],
                        "shared_count": len(others),
                        "sanctioned": value in banned,
                        # Stated per row rather than left to be inferred from a
                        # missing button. An absent control explains nothing.
                        "bannable": bool(subject),
                        "subject_type": subject or "",
                    }
                )

        return {
            "account": name,
            "first_seen": _stamp(bounds["first"]),
            "last_seen": _stamp(bounds["last"]),
            "keys": keys,
            "sanctions": [
                {
                    "id": row["id"],
                    "level": row["level"],
                    "reason": row["reason"],
                    "created": _stamp(row["created_at"]),
                    "expires": _stamp(row["expires_at"]) or "indefinite",
                }
                for row in Sanction.objects.active()
                .filter(subject_type=Sanction.SUBJECT_ACCOUNT, subject_value=name.lower())
                .order_by("-created_at")
                .values("id", "level", "reason", "created_at", "expires_at")[:QUEUE_SANCTIONS]
            ],
            "flags": [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "severity": row["severity"],
                    "state": row["state"],
                    "summary": row["summary"],
                    "last_seen": _stamp(row["last_seen_at"]),
                }
                for row in ModerationFlag.objects.filter(account_name__iexact=name)
                .order_by("-severity", "-last_seen_at")
                .values("id", "kind", "severity", "state", "summary", "last_seen_at")[:QUEUE_FLAGS]
            ],
            "caps": {"keys": DOSSIER_KEYS, "shared": DOSSIER_MATCHES},
            "note": (
                "Two accounts share a key or they do not. The console does not "
                "guess that two players are the same person."
            ),
        }

    def signals(self, ctx):
        """Report which identity signals are actually arriving, and which are not.

        A signal that is configured and silently absent is worse than one that
        was never set up, because the queue looks calm for the wrong reason.
        This is the readout that says so.

        It exists mostly for ``tls_sig``, which needs a reverse proxy to report
        the handshake it terminated. Nothing in the engine can tell whether that
        proxy was configured; only the arriving rows can. The two ways it fails
        look identical in the flag queue and completely different here:

        The proxy is trusted and sends no TLS headers -- the proxy configuration
        is incomplete.

        The proxy is not trusted at all -- ``UPSTREAM_IPS`` is wrong, and the
        recorded addresses are the proxy's own, which is the larger fault.

        Counts only. No fingerprint value is read out of the database: the
        columns are turned into booleans by the query itself, so a coverage
        report cannot become a way to page through everybody's fingerprints.

        Args:
            ctx: Worker context.

        Returns:
            dict: One entry per signal, with a verdict and what to do about it.
        """

        from django.db.models import BooleanField, Case, Value, When

        from evennia.server.models import SessionRecord

        fields = [field for field, _ in SESSION_SIGNATURES] + ["http_fp", "device_token"]
        annotations = {
            f"has_{field}": Case(
                When(**{field: ""}, then=Value(False)),
                default=Value(True),
                output_field=BooleanField(),
            )
            for field in fields
        }
        rows = list(
            SessionRecord.objects.order_by("-connected_at")
            .annotate(**annotations)
            .values("protocol", "xff_present", "xff_applied", *annotations)[:SIGNAL_SAMPLE]
        )
        if not rows:
            return {
                "sample": 0,
                "web_sessions": 0,
                "socket_sessions": 0,
                "rows": [],
                "note": "No connection is recorded yet. Connect once, then look again.",
            }

        web, socket_rows, other = [], [], []
        for row in rows:
            family = protocol_family(row["protocol"])
            target = web if family == "web" else socket_rows if family == "telnet" else other
            target.append(row)
        trusted = sum(1 for row in web if row["xff_applied"])

        def count(population, field):
            return sum(1 for row in population if row[f"has_{field}"])

        report = []
        for field, label in SESSION_SIGNATURES:
            population = socket_rows if field == "telnet_sig" else web
            seen = count(population, field)
            report.append(
                {
                    "field": field,
                    "label": label,
                    "seen": seen,
                    "of": len(population),
                    "state": self._signal_state(seen, len(population)),
                    "advice": self._signal_advice(field, seen, len(population), trusted),
                }
            )

        return {
            "sample": len(rows),
            "web_sessions": len(web),
            "socket_sessions": len(socket_rows),
            # Bot links and anything else that carries no identity signal at
            # all. Reported so the sample and the two populations add up on
            # screen, rather than leaving rows silently unaccounted for.
            "other_sessions": len(other),
            "web_addresses_trusted": trusted,
            "rows": report,
            "note": (
                "These counts cover the last %s connections, of which %s are web "
                "and %s are raw sockets. A signal that shows zero against a "
                "population above zero is not arriving at the server. Bot links "
                "carry no identity signal and are counted in neither."
                % (len(rows), len(web), len(socket_rows))
            ),
        }

    def _signal_state(self, seen, total):
        """Return the lamp state for one coverage proportion."""

        if not total:
            return "off"
        if not seen:
            return "fail"
        return "ok" if seen >= total * 0.8 else "attn"

    def _signal_advice(self, field, seen, total, trusted):
        """Return one sentence about what an absent signal means.

        Written for the person who has to fix it, not for the person who built
        it. Each sentence names the file or the setting to change.
        """

        if not total:
            return "No connection of this kind is recorded yet."
        if seen:
            if seen >= total * 0.8:
                return ""
            return "Some connections carry this signal and some do not. This is normal when players use different software."

        if field == "telnet_sig":
            return (
                "No connection over a raw socket negotiated any option. This is "
                "normal if every player uses the web client."
            )
        if field == "tls_sig":
            if not trusted:
                return (
                    "The server does not trust the proxy in front of it, so it "
                    "reads neither the player address nor the handshake. Add the "
                    "proxy address to UPSTREAM_IPS first. Every address signal is "
                    "wrong until you do."
                )
            return (
                "The server trusts the proxy, but the proxy sends no handshake "
                "headers. Add the four X-TLS headers to the proxy configuration, "
                "in the same block as the other proxy headers."
            )
        if field == "http_order_fp":
            return (
                "No web connection recorded its header order. This is a fault in "
                "the server, not in the proxy. Report it."
            )
        if field == "csessid":
            return "No web connection carried a browser session id."
        return ""

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
            raise LookupError("Enter the subject value.")

        reason = self._require_reason(reason)
        self._require_address_authority(ctx, subject_type)

        # A permanent sanction from somebody without the authority becomes a
        # proposal. It is not refused: the person who found the case is usually
        # the right person to make it, and the wrong person to be the only one
        # who decides it never ends.
        if self._permanent_requested(expires_at) and not ctx.has(CONSOLE_MODERATION_PERMANENT):
            return self._propose(
                ctx,
                subject_type=subject_type,
                subject_value=subject_value,
                level=level,
                reason=reason,
                staff_note=staff_note,
                flag_id=flag_id,
            )

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
            expires_at=self._expiry(expires_at)[0],
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

    # -- proposals -----------------------------------------------------

    def _propose(
        self,
        ctx,
        subject_type=None,
        subject_value=None,
        level=None,
        reason="",
        staff_note="",
        flag_id=None,
    ):
        """Record a request for a permanent sanction, and change nothing else.

        Runs when somebody without ``engine.console.moderation.permanent``
        asks for a sanction that never expires. The proposal has no effect on
        the subject: it is an observation with a name attached, and it follows
        the same rule the flag queue follows.

        The collateral count is stored with the proposal rather than recomputed
        when it is read. A reviewer should see the number the requester saw,
        and a network's population changes between the two.
        """

        from evennia.server.models import ModerationFlag, SanctionProposal

        try:
            reach = self.collateral(ctx, subject_type=subject_type, subject_value=subject_value)
        except LookupError:
            reach = {}

        flag = None
        if flag_id:
            flag = ModerationFlag.objects.filter(pk=flag_id).first()

        proposal = SanctionProposal.objects.create(
            subject_type=str(subject_type),
            subject_value=str(subject_value)[:255],
            level=str(level),
            reason=str(reason)[:500],
            staff_note=str(staff_note or ""),
            collateral={
                "account_count": reach.get("account_count", 0),
                "sessions": reach.get("sessions", 0),
                "accounts": reach.get("accounts", []),
            },
            flag=flag,
            proposed_by_id=ctx.actor_id,
            proposed_by_name=str(ctx.actor_name or "")[:255],
        )
        audit.record(
            panel=self.key,
            operation="propose",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"server.sanctionproposal#{proposal.pk}",
            after={
                "subject_type": proposal.subject_type,
                "subject_value": mask(proposal.subject_value),
                "level": proposal.level,
            },
            message=str(reason)[:500],
            retention="permanent",
        )
        return {
            "proposed": True,
            "id": proposal.pk,
            "state": proposal.state,
            "subject_type": proposal.subject_type,
            "level": proposal.level,
            "message": (
                "You cannot make a ban that never ends. The console saved your "
                "proposal. A senior staff member accepts it or refuses it. "
                "Nothing has changed for this player yet."
            ),
        }

    def proposals(self, ctx, state=""):
        """Return the sanction proposals awaiting a decision.

        Args:
            ctx: Worker context.
            state: One proposal state, or empty for the open ones.

        Returns:
            dict: Proposals, and whether this caller may decide them.
        """

        from evennia.server.models import SanctionProposal

        queryset = SanctionProposal.objects.all()
        wanted = str(state or "").strip()
        if wanted:
            queryset = queryset.filter(state=wanted)
        else:
            queryset = queryset.filter(state__in=SanctionProposal.OPEN_STATES)

        rows = []
        for row in queryset.values(
            "id",
            "subject_type",
            "subject_value",
            "level",
            "reason",
            "staff_note",
            "collateral",
            "state",
            "proposed_by_name",
            "proposed_by_id",
            "created_at",
            "decided_by_name",
            "decided_at",
            "decision_note",
        )[:QUEUE_SANCTIONS]:
            reveal = ctx.has(CONSOLE_MODERATION_ADDRESS)
            rows.append(
                {
                    "id": row["id"],
                    "subject_type": row["subject_type"],
                    "subject_value": self._subject_display(
                        row["subject_type"], row["subject_value"]
                    )
                    if not reveal
                    else row["subject_value"],
                    "level": row["level"],
                    "reason": row["reason"],
                    "staff_note": row["staff_note"][:2000],
                    "collateral": row["collateral"] or {},
                    "state": row["state"],
                    "proposed_by": row["proposed_by_name"] or "(unknown)",
                    "proposed_by_id": row["proposed_by_id"],
                    "created_at": _stamp(row["created_at"]),
                    "decided_by": row["decided_by_name"] or "",
                    "decided_at": _stamp(row["decided_at"]),
                    "decision_note": row["decision_note"],
                    # A person must not be the one who decides their own
                    # request. That is the whole point of the split.
                    "yours": row["proposed_by_id"] == ctx.actor_id,
                }
            )

        return {
            "rows": rows,
            "states": [
                {"value": value, "meaning": meaning}
                for value, meaning in SanctionProposal.STATE_CHOICES
            ],
            "may_decide": ctx.has(CONSOLE_MODERATION_PERMANENT),
            "note": (
                "A proposal does nothing to the player. It shows that a staff member "
                "asked for a ban that never ends."
            ),
        }

    @io_action
    def approve(self, ctx, proposal_id=None, note=""):
        """Approve one proposal and issue the sanction it asks for.

        Raises:
            PermissionDenied: The caller may not decide proposals, or is the
                person who made this one.
            LookupError: No such proposal.
            ValueError: The proposal is already decided, or no note was given.
        """

        from evennia.server.models import SanctionProposal

        proposal = self._decidable(ctx, proposal_id)
        text = str(note or "").strip()
        if not text:
            raise ValueError("Enter the reason for your decision.")

        result = self.sanction(
            ctx,
            subject_type=proposal.subject_type,
            subject_value=proposal.subject_value,
            level=proposal.level,
            reason=proposal.reason,
            staff_note=proposal.staff_note,
            expires_at=None,
            flag_id=proposal.flag_id,
        )

        from evennia.server.models import Sanction

        proposal.state = SanctionProposal.STATE_APPROVED
        proposal.decided_by_id = ctx.actor_id
        proposal.decided_by_name = str(ctx.actor_name or "")[:255]
        proposal.decided_at = timezone.now()
        proposal.decision_note = text[:500]
        proposal.sanction = Sanction.objects.filter(pk=result.get("id")).first()
        proposal.save(
            update_fields=[
                "state",
                "decided_by_id",
                "decided_by_name",
                "decided_at",
                "decision_note",
                "sanction",
            ]
        )

        audit.record(
            panel=self.key,
            operation="approve",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"server.sanctionproposal#{proposal.pk}",
            before={"state": SanctionProposal.STATE_PENDING},
            after={"state": proposal.state, "sanction": result.get("id")},
            message=text[:500],
            retention="permanent",
        )
        return {"id": proposal.pk, "state": proposal.state, "sanction": result}

    def decline(self, ctx, proposal_id=None, note=""):
        """Refuse one proposal, and keep the record of refusing it.

        Kept rather than deleted. "We considered this and said no" is what
        stops the same case being re-argued from scratch in six months, and it
        is what an appeal needs.

        Raises:
            PermissionDenied: The caller may not decide proposals, or is the
                person who made this one.
            LookupError: No such proposal.
            ValueError: The proposal is already decided, or no note was given.
        """

        from evennia.server.models import SanctionProposal

        proposal = self._decidable(ctx, proposal_id)
        text = str(note or "").strip()
        if not text:
            raise ValueError("Enter the reason for your decision.")

        proposal.state = SanctionProposal.STATE_DECLINED
        proposal.decided_by_id = ctx.actor_id
        proposal.decided_by_name = str(ctx.actor_name or "")[:255]
        proposal.decided_at = timezone.now()
        proposal.decision_note = text[:500]
        proposal.save(
            update_fields=[
                "state",
                "decided_by_id",
                "decided_by_name",
                "decided_at",
                "decision_note",
            ]
        )
        audit.record(
            panel=self.key,
            operation="decline",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"server.sanctionproposal#{proposal.pk}",
            before={"state": SanctionProposal.STATE_PENDING},
            after={"state": proposal.state},
            message=text[:500],
            retention="permanent",
        )
        return {"id": proposal.pk, "state": proposal.state}

    def withdraw(self, ctx, proposal_id=None, note=""):
        """Take back a proposal you made.

        Only the person who asked may withdraw, and only while it is open. A
        reviewer who wants it gone declines it, which leaves the reason on the
        record.

        Raises:
            LookupError: No such proposal.
            PermissionDenied: The proposal belongs to somebody else.
            ValueError: The proposal is already decided.
        """

        from evennia.server.models import SanctionProposal

        proposal = SanctionProposal.objects.filter(pk=self._proposal_id(proposal_id)).first()
        if proposal is None:
            raise LookupError(f"no proposal with id {proposal_id!r}")
        if proposal.proposed_by_id != ctx.actor_id:
            raise PermissionDenied(
                "You can cancel only a proposal that you made. Another staff member "
                "refuses a proposal instead, and the console saves the reason."
            )
        if not proposal.is_open:
            raise ValueError(f"This proposal is already {proposal.state}.")

        proposal.state = SanctionProposal.STATE_WITHDRAWN
        proposal.decided_at = timezone.now()
        proposal.decision_note = str(note or "")[:500]
        proposal.save(update_fields=["state", "decided_at", "decision_note"])
        audit.record(
            panel=self.key,
            operation="withdraw",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"server.sanctionproposal#{proposal.pk}",
            after={"state": proposal.state},
            message=str(note or "")[:500],
        )
        return {"id": proposal.pk, "state": proposal.state}

    def _proposal_id(self, value):
        """Return one proposal identifier, or refuse."""

        try:
            return int(value)
        except (TypeError, ValueError) as err:
            raise LookupError(f"{value!r} is not a valid proposal id") from err

    def _decidable(self, ctx, proposal_id):
        """Return one proposal this caller may decide, or refuse.

        Raises:
            PermissionDenied: The caller lacks the authority, or made it.
            LookupError: No such proposal.
            ValueError: It is already decided.
        """

        from evennia.server.models import SanctionProposal

        if not ctx.has(CONSOLE_MODERATION_PERMANENT):
            raise PermissionDenied(
                "You cannot accept or refuse a proposal. This needs the permanent-ban permission."
            )
        proposal = SanctionProposal.objects.filter(pk=self._proposal_id(proposal_id)).first()
        if proposal is None:
            raise LookupError(f"no proposal with id {proposal_id!r}")
        if not proposal.is_open:
            raise ValueError(f"This proposal is already {proposal.state}.")
        if proposal.proposed_by_id == ctx.actor_id:
            raise PermissionDenied(
                "You cannot decide your own proposal. Ask another staff member who "
                "has the permanent-ban permission."
            )
        return proposal

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

        Looking is not the same act as banning, and the two are gated
        differently on purpose.

        **Banning a network needs the address capability.** It stops every
        player who shares that network, and the person who finds a case is
        often not the person who should decide that.

        **Looking at one needs a reason and a password.** Nobody is prevented,
        because an investigation sometimes needs the raw value and refusing it
        only moves the work somewhere with no record. Everybody is recorded,
        permanently, and the record says why.

        Raises:
            LookupError: The record or field is unknown.
            PermissionDenied: No reason was given.
        """

        from evennia.server.models import ModerationFlag, Sanction, SessionRecord

        models = {
            "session": (
                SessionRecord,
                {
                    "ip",
                    "ip_hash",
                    "device_token",
                    "client_fp",
                    "csessid",
                    "telnet_sig",
                    "tls_sig",
                    "http_order_fp",
                    "http_fp",
                },
            ),
            "sanction": (Sanction, {"subject_value"}),
            "flag": (ModerationFlag, {"session_uid"}),
        }
        entry = models.get(str(record or "").lower())
        if entry is None:
            raise LookupError(f"{record!r} is not a record this panel can reveal")
        model, allowed = entry
        if str(field) not in allowed:
            raise LookupError(f"{field!r} is not a revealable field on a {record}")

        # Asked for after the request is known to be well formed. Demanding a
        # justification for a request that names no real field would be asking
        # a person to explain a mistake rather than a decision.
        reason = self._require_look_reason(reason)

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
