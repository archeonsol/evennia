"""

Server Configuration flags

This holds persistent server configuration flags.

Config values should usually be set through the
manager's conf() method.

"""

from django.db import models
from django.utils import timezone

from evennia.server.manager import ServerConfigManager
from evennia.utils import logger, picklefield, utils
from evennia.utils.dbserialize import from_pickle, to_pickle
from evennia.utils.idmapper.models import WeakSharedMemoryModel

# ------------------------------------------------------------
#
# ServerConfig
#
# ------------------------------------------------------------


class ServerConfig(WeakSharedMemoryModel):
    """
    On-the fly storage of global settings.

    Properties defined on ServerConfig:

      - key: Main identifier
      - value: Value stored in key. This is a pickled storage.

    """

    #
    # ServerConfig database model setup
    #
    #
    # These database fields are all set using their corresponding properties,
    # named same as the field, but without the db_* prefix.

    # main name of the database entry
    db_key = models.CharField(max_length=64, unique=True)
    # config value
    # db_value = models.BinaryField(blank=True)

    db_value = picklefield.PickledObjectField(
        "value",
        null=True,
        help_text="The data returned when the config value is accessed. Must be "
        "written as a Python literal if editing through the admin "
        "interface. Attribute values which are not Python literals "
        "cannot be edited through the admin interface.",
    )

    objects = ServerConfigManager()
    _is_deleted = False

    # Wrapper properties to easily set database fields. These are
    # @property decorators that allows to access these fields using
    # normal python operations (without having to remember to save()
    # etc). So e.g. a property 'attr' has a get/set/del decorator
    # defined that allows the user to do self.attr = value,
    # value = self.attr and del self.attr respectively (where self
    # is the object in question).

    # key property (wraps db_key)
    # @property
    def __key_get(self):
        "Getter. Allows for value = self.key"
        return self.db_key

    # @key.setter
    def __key_set(self, value):
        "Setter. Allows for self.key = value"
        self.db_key = value
        self.save()

    # @key.deleter
    def __key_del(self):
        "Deleter. Allows for del self.key. Deletes entry."
        self.delete()

    key = property(__key_get, __key_set, __key_del)

    # value property (wraps db_value)
    # @property
    def __value_get(self):
        "Getter. Allows for value = self.value"
        return from_pickle(self.db_value, db_obj=self)

    # @value.setter
    def __value_set(self, value):
        "Setter. Allows for self.value = value"
        if utils.has_parent("django.db.models.base.Model", value):
            # we have to protect against storing db objects.
            logger.log_err("ServerConfig cannot store db objects! (%s)" % value)
            return
        self.db_value = to_pickle(value)
        self.save()

    # @value.deleter
    def __value_del(self):
        "Deleter. Allows for del self.value. Deletes entry."
        self.delete()

    value = property(__value_get, __value_set, __value_del)

    class Meta:
        "Define Django meta options"

        verbose_name = "Server Config value"
        verbose_name_plural = "Server Config values"

    #
    # ServerConfig other methods
    #
    def __repr__(self):
        return "<{} {}>".format(self.__class__.__name__, self.key)

    def store(self, key, value):
        """
        Wrap the storage.

        Args:
            key (str): The name of this store.
            value (str): The data to store with this `key`.

        """
        self.key = key
        self.value = value


class GameEvent(models.Model):
    """
    Persisted engine event bus records (moderation audit, analytics).
    """

    subject = models.CharField(max_length=128, db_index=True)
    payload_json = models.TextField(default="{}")
    actor_ref = models.CharField(max_length=255, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Game event"
        verbose_name_plural = "Game events"
        indexes = [
            models.Index(fields=["subject", "created_at"]),
        ]


class AuthorizationGrant(models.Model):
    """One positive, scoped capability grant.

    Grants never imply hierarchy or ownership. Generic references allow
    accounts, objects, sessions, integrations, and future providers to share
    the same authorization substrate.
    """

    grant_id = models.CharField(max_length=32, unique=True, db_index=True)
    principal_ref = models.CharField(max_length=128)
    capability = models.CharField(max_length=128)
    scope_kind = models.CharField(max_length=32)
    scope_key = models.CharField(max_length=255)
    constraints = models.JSONField(default=dict)
    provenance = models.CharField(max_length=128, blank=True, default="")
    parent_grant_id = models.CharField(max_length=32, blank=True, default="")
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Declare portable grant constraints and hot-path indexes."""

        constraints = [
            models.UniqueConstraint(
                fields=["principal_ref", "capability", "scope_kind", "scope_key"],
                condition=models.Q(revoked_at__isnull=True),
                name="authgrant_active_scope_uniq",
            ),
        ]
        indexes = [
            models.Index(
                fields=["principal_ref", "capability", "revoked_at"],
                name="authgrant_principal_cap_idx",
            ),
            models.Index(fields=["expires_at"], name="authgrant_expires_idx"),
        ]


class AuthorizationScopeLabel(models.Model):
    """Materialized O(1) scope membership for one generic resource."""

    resource_ref = models.CharField(max_length=160)
    label = models.CharField(max_length=255)
    source = models.CharField(max_length=32, default="authored")
    generation = models.PositiveBigIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Declare scope uniqueness and reverse-label lookup."""

        constraints = [
            models.UniqueConstraint(
                fields=["resource_ref", "label"], name="authscope_resource_label_uniq"
            )
        ]
        indexes = [
            models.Index(fields=["resource_ref"], name="authscope_resource_idx"),
            models.Index(fields=["label", "resource_ref"], name="authscope_label_idx"),
        ]


class AuthorizationPolicyOverride(models.Model):
    """Sparse instance policy override and legacy migration checkpoint."""

    resource_ref = models.CharField(max_length=160)
    access_type = models.CharField(max_length=64)
    template_key = models.CharField(max_length=128, blank=True, default="")
    policy = models.JSONField(default=dict)
    policy_version = models.PositiveIntegerField(default=1)
    legacy_shadow = models.TextField(blank=True, default="")
    legacy_frozen = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        """Declare one override per resource operation."""

        constraints = [
            models.UniqueConstraint(
                fields=["resource_ref", "access_type"],
                name="authpolicy_resource_access_uniq",
            )
        ]
        indexes = [
            models.Index(fields=["resource_ref"], name="authpolicy_resource_idx"),
            models.Index(fields=["legacy_frozen"], name="authpolicy_frozen_idx"),
        ]


class AuthorizationPrincipalState(models.Model):
    """Universal suspension state, separate from positive grants."""

    principal_ref = models.CharField(max_length=128, unique=True)
    suspended = models.BooleanField(default=False)
    reason = models.CharField(max_length=255, blank=True, default="")
    suspended_until = models.DateTimeField(null=True, blank=True)
    generation = models.PositiveBigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)


class AuthorizationAuditEvent(models.Model):
    """Append-only audit record for sensitive authorization mutations."""

    event_id = models.CharField(max_length=32, unique=True, db_index=True)
    kind = models.CharField(max_length=64, db_index=True)
    principal_ref = models.CharField(max_length=128, blank=True, default="")
    capability = models.CharField(max_length=128, blank=True, default="")
    resource_ref = models.CharField(max_length=160, blank=True, default="")
    actor_ref = models.CharField(max_length=128, blank=True, default="")
    reason = models.CharField(max_length=255, blank=True, default="")
    data = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        """Declare audit query indexes."""

        indexes = [
            models.Index(fields=["principal_ref", "-created_at"], name="authaudit_principal_idx"),
            models.Index(fields=["kind", "-created_at"], name="authaudit_kind_idx"),
        ]


class EngineJob(models.Model):
    """
    PostgreSQL-backed durable job queue (alternative to the Redis list).

    Lifecycle: ``pending`` -> ``leased`` -> ``completed`` | (retry -> ``pending``)
    | ``dead``. A leased job whose ``lease_until`` has passed is reclaimable by
    the next dequeue (the previous worker crashed mid-run). ``attempts`` is
    bumped on each lease and, once it reaches ``max_attempts``, a failing job is
    dead-lettered rather than retried forever. ``available_at`` gates
    backoff-delayed retries. ``idempotency_key`` (when supplied) makes enqueue
    idempotent.
    """

    job_id = models.CharField(max_length=32, unique=True, db_index=True)
    job_type = models.CharField(max_length=64, db_index=True)
    payload_json = models.TextField(default="{}")
    priority = models.IntegerField(default=0, db_index=True)
    status = models.CharField(max_length=16, default="pending", db_index=True)
    # Durable-contract fields.
    attempts = models.IntegerField(default=0)
    max_attempts = models.IntegerField(default=5)
    available_at = models.DateTimeField(null=True, blank=True, db_index=True)
    lease_until = models.DateTimeField(null=True, blank=True, db_index=True)
    idempotency_key = models.CharField(
        max_length=128, null=True, blank=True, unique=True, db_index=True
    )
    last_error = models.TextField(blank=True, default="")
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Engine job"
        verbose_name_plural = "Engine jobs"
        indexes = [
            # the dequeue predicate: eligible rows ordered for claiming
            models.Index(fields=["status", "priority", "created_at"]),
        ]


# ------------------------------------------------------------
#
# Moderation substrate
#
# Connection history, staff-issued sanctions, and the flag queue that
# automated detection writes into. Like AuthorizationGrant above, these
# reference accounts by id and name rather than by foreign key: the engine
# substrate does not depend on the account model, and moderation history has
# to outlive the account it describes.
#
# ------------------------------------------------------------


class SessionRecord(models.Model):
    """One connection, from login to disconnect.

    Written by ``evennia.moderation.capture``. Only hard, directly-observed
    signals are stored: what the connection reported about itself. Nothing here
    is inferred or scored, so any conclusion drawn from these rows can be shown
    to the player it is used against.
    """

    # --- identity of the row itself -------------------------------------
    session_uid = models.CharField(max_length=32, unique=True)
    sessid = models.IntegerField(null=True, blank=True)
    protocol = models.CharField(max_length=32, default="", db_index=True)

    # --- lifecycle ------------------------------------------------------
    connected_at = models.DateTimeField(default=timezone.now, db_index=True)
    login_at = models.DateTimeField(null=True, blank=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    disconnect_reason = models.CharField(max_length=255, default="", blank=True)

    # --- who ------------------------------------------------------------
    account_id = models.IntegerField(null=True, blank=True, db_index=True)
    account_name = models.CharField(max_length=255, default="", blank=True, db_index=True)
    puppet_name = models.CharField(max_length=255, default="", blank=True)

    # --- network --------------------------------------------------------
    # ``ip`` is purged on the retention schedule; ``ip_hash`` and ``cidr`` outlive it.
    ip = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    ip_hash = models.CharField(max_length=64, default="", blank=True, db_index=True)
    # /24 for IPv4, /64 for IPv6 -- the unit sanctions are issued against.
    cidr = models.CharField(max_length=64, default="", blank=True, db_index=True)

    # Address provenance. peer_ip is the raw TCP peer; when it differs from ip an
    # upstream proxy was correctly unwound. xff_present without xff_applied means
    # a proxy sent X-Forwarded-For that UPSTREAM_IPS did not trust -- ``ip`` is the
    # proxy, not the player, and every address-based signal on this row is void.
    peer_ip = models.GenericIPAddressField(null=True, blank=True)
    xff_applied = models.BooleanField(default=False)
    xff_present = models.BooleanField(default=False)

    # Filled by enrichment; null means "not looked up yet", not "clean".
    asn = models.IntegerField(null=True, blank=True, db_index=True)
    asn_org = models.CharField(max_length=255, default="", blank=True)
    country = models.CharField(max_length=8, default="", blank=True)
    is_datacenter = models.BooleanField(null=True, blank=True)
    is_tor = models.BooleanField(null=True, blank=True)

    # --- client ---------------------------------------------------------
    # Hash over the negotiated capability set. Deliberately excludes screen size,
    # which changes whenever the player resizes their window.
    client_fp = models.CharField(max_length=64, default="", blank=True, db_index=True)
    client_name = models.CharField(max_length=255, default="", blank=True)
    term = models.CharField(max_length=64, default="", blank=True)
    encoding = models.CharField(max_length=32, default="", blank=True)
    screen_w = models.IntegerField(null=True, blank=True)
    screen_h = models.IntegerField(null=True, blank=True)
    # Negotiation order and per-option timing, for signals not yet promoted to columns.
    neg_order = models.JSONField(default=list, blank=True)
    neg_timing_ms = models.JSONField(default=dict, blank=True)
    # Full sanitized protocol_flags snapshot.
    flags = models.JSONField(default=dict, blank=True)

    # --- web client -----------------------------------------------------
    csessid = models.CharField(max_length=64, default="", blank=True, db_index=True)
    device_token = models.CharField(max_length=64, default="", blank=True, db_index=True)
    http_fp = models.CharField(max_length=64, default="", blank=True, db_index=True)
    user_agent = models.CharField(max_length=512, default="", blank=True)

    # --- activity -------------------------------------------------------
    command_count = models.IntegerField(default=0)
    last_command_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Session record"
        ordering = ["-connected_at"]
        indexes = [
            models.Index(fields=["account_id", "-connected_at"]),
            models.Index(fields=["cidr", "-connected_at"]),
            models.Index(fields=["device_token", "-connected_at"]),
            models.Index(fields=["client_fp", "cidr"]),
            models.Index(fields=["ip_hash", "-connected_at"]),
        ]

    def __str__(self):
        who = self.account_name or "(anonymous)"
        return f"SessionRecord(#{self.pk}, {who}, {self.protocol}, {self.ip or '-'})"

    @property
    def address_is_trustworthy(self) -> bool:
        """False when a proxy header was sent but not trusted -- see ``xff_present``."""
        return not (self.xff_present and not self.xff_applied)


class SanctionQuerySet(models.QuerySet):
    def active(self, now=None):
        """Not revoked and not expired."""
        now = now or timezone.now()
        return self.filter(revoked_at__isnull=True).filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now)
        )


class Sanction(models.Model):
    """One moderation decision, always made by a person.

    Nothing in the engine creates a Sanction automatically. Detection writes
    :class:`ModerationFlag` rows for staff to read; only a staff action turns a
    flag into a sanction. That separation is the point: a system that can ban on
    its own is a system that bans the wrong player unattended.
    """

    SUBJECT_ACCOUNT = "account"
    SUBJECT_IP = "ip"
    SUBJECT_CIDR = "cidr"
    SUBJECT_ASN = "asn"
    SUBJECT_DEVICE = "device_token"
    SUBJECT_CLIENT_FP = "client_fp"
    SUBJECT_CSESSID = "csessid"
    SUBJECT_EMAIL_DOMAIN = "email_domain"
    SUBJECT_CHOICES = [
        (SUBJECT_ACCOUNT, "account name"),
        (SUBJECT_IP, "single address"),
        (SUBJECT_CIDR, "network"),
        (SUBJECT_ASN, "autonomous system"),
        (SUBJECT_DEVICE, "device token"),
        (SUBJECT_CLIENT_FP, "client fingerprint"),
        (SUBJECT_CSESSID, "browser session"),
        (SUBJECT_EMAIL_DOMAIN, "email domain"),
    ]

    # Ordered least to most severe; LEVEL_ORDER below depends on this order.
    LEVEL_WATCH = "watch"
    LEVEL_FRICTION = "friction"
    LEVEL_MUTE = "mute"
    LEVEL_SUSPEND = "suspend"
    LEVEL_BAN = "ban"
    LEVEL_CHOICES = [
        (LEVEL_WATCH, "watch only, no effect"),
        (LEVEL_FRICTION, "extra verification required"),
        (LEVEL_MUTE, "cannot speak"),
        (LEVEL_SUSPEND, "cannot connect, time-limited"),
        (LEVEL_BAN, "cannot connect"),
    ]
    LEVEL_ORDER = [LEVEL_WATCH, LEVEL_FRICTION, LEVEL_MUTE, LEVEL_SUSPEND, LEVEL_BAN]
    # Levels that refuse a connection outright.
    BLOCKING_LEVELS = frozenset({LEVEL_SUSPEND, LEVEL_BAN})

    subject_type = models.CharField(max_length=32, choices=SUBJECT_CHOICES, db_index=True)
    # Normalized at write time: lowercased account names, canonical network text.
    subject_value = models.CharField(max_length=255, db_index=True)

    level = models.CharField(max_length=16, choices=LEVEL_CHOICES, default=LEVEL_BAN)

    # Shown to the sanctioned player. Must never name the signal that matched.
    reason = models.TextField(blank=True, default="")
    # Internal only.
    staff_note = models.TextField(blank=True, default="")

    actor_id = models.IntegerField(null=True, blank=True, db_index=True)
    actor_name = models.CharField(max_length=255, default="", blank=True)

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    # Null means indefinite, which staff tooling requires be chosen deliberately.
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by_id = models.IntegerField(null=True, blank=True)
    revoked_by_name = models.CharField(max_length=255, default="", blank=True)
    revoked_reason = models.CharField(max_length=255, default="", blank=True)

    # Flags, sessions, and prior sanctions this decision was based on.
    evidence = models.JSONField(default=dict, blank=True)
    # Enforce without telling the target it is a sanction.
    silent = models.BooleanField(default=False)

    # Append-only hash chain. Tamper-evidence protects staff from accusations as
    # much as it protects players.
    prev_hash = models.CharField(max_length=64, default="", blank=True)
    row_hash = models.CharField(max_length=64, default="", blank=True, db_index=True)

    objects = SanctionQuerySet.as_manager()

    class Meta:
        verbose_name = "Sanction"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["subject_type", "subject_value"]),
            models.Index(fields=["level", "-created_at"]),
            models.Index(fields=["expires_at"]),
        ]

    def __str__(self):
        return f"Sanction({self.level}, {self.subject_type}={self.subject_value})"

    @property
    def is_active(self) -> bool:
        if self.revoked_at is not None:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    @property
    def blocks_connection(self) -> bool:
        return self.is_active and self.level in self.BLOCKING_LEVELS

    def severity(self) -> int:
        try:
            return self.LEVEL_ORDER.index(self.level)
        except ValueError:
            return 0


class SanctionHit(models.Model):
    """One firing of a sanction, kept so enforcement can be audited afterwards."""

    ACTION_BLOCKED = "blocked"
    ACTION_FLAGGED = "flagged"
    ACTION_ALLOWED = "allowed"

    sanction = models.ForeignKey(Sanction, on_delete=models.CASCADE, related_name="hits")
    occurred_at = models.DateTimeField(default=timezone.now, db_index=True)
    action_taken = models.CharField(max_length=16, default=ACTION_BLOCKED)
    matched_on = models.CharField(max_length=32, default="", blank=True)

    session_uid = models.CharField(max_length=32, default="", blank=True, db_index=True)
    account_name = models.CharField(max_length=255, default="", blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    cidr = models.CharField(max_length=64, default="", blank=True)
    device_token = models.CharField(max_length=64, default="", blank=True)
    client_fp = models.CharField(max_length=64, default="", blank=True)

    class Meta:
        verbose_name = "Sanction hit"
        ordering = ["-occurred_at"]
        indexes = [models.Index(fields=["sanction", "-occurred_at"])]

    def __str__(self):
        return f"SanctionHit(sanction={self.sanction_id}, {self.action_taken})"


class ModerationFlag(models.Model):
    """Something worth a person's attention. Never an enforcement decision.

    Every automated signal in the moderation substrate terminates here. A flag
    has no effect on the player it names until staff read it and act on it.
    """

    STATE_OPEN = "open"
    STATE_ACKNOWLEDGED = "acknowledged"
    STATE_DISMISSED = "dismissed"
    STATE_ACTIONED = "actioned"
    STATE_CHOICES = [
        (STATE_OPEN, "awaiting review"),
        (STATE_ACKNOWLEDGED, "seen, still open"),
        (STATE_DISMISSED, "reviewed, no action"),
        (STATE_ACTIONED, "sanction issued"),
    ]
    OPEN_STATES = frozenset({STATE_OPEN, STATE_ACKNOWLEDGED})

    KIND_SHARED_DEVICE = "shared_device"
    KIND_SHARED_CSESSID = "shared_csessid"
    KIND_SHARED_CLIENT_CIDR = "shared_client_cidr"
    KIND_SANCTIONED_KEY = "sanctioned_key_match"
    KIND_DATACENTER = "datacenter_address"
    KIND_TOR = "tor_exit"
    KIND_SIGNUP_BURST = "signup_burst"
    KIND_DISPOSABLE_EMAIL = "disposable_email"
    KIND_EMAIL_ALIAS = "email_alias_reuse"
    KIND_UNDELIVERABLE_EMAIL = "undeliverable_email"
    #: Volume, not content. A text game floods with text rather than with
    #: sockets, and the connection limiter cannot see one logged-in account
    #: sending three hundred tells a minute.
    KIND_MESSAGE_BURST = "message_burst"

    kind = models.CharField(max_length=48, db_index=True)
    severity = models.IntegerField(default=0, db_index=True)

    account_id = models.IntegerField(null=True, blank=True, db_index=True)
    account_name = models.CharField(max_length=255, default="", blank=True, db_index=True)
    session_uid = models.CharField(max_length=32, default="", blank=True, db_index=True)

    # One line a staff member can act on without opening the evidence.
    summary = models.CharField(max_length=512, default="", blank=True)
    evidence = models.JSONField(default=dict, blank=True)

    # Collapses repeats of the same observation into one row with a bumped count.
    dedupe_key = models.CharField(max_length=128, unique=True)
    seen_count = models.IntegerField(default=1)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    last_seen_at = models.DateTimeField(default=timezone.now, db_index=True)

    state = models.CharField(
        max_length=16, choices=STATE_CHOICES, default=STATE_OPEN, db_index=True
    )
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by_id = models.IntegerField(null=True, blank=True)
    resolved_by_name = models.CharField(max_length=255, default="", blank=True)
    resolution_note = models.CharField(max_length=512, default="", blank=True)
    sanction = models.ForeignKey(
        Sanction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="flags",
    )

    class Meta:
        verbose_name = "Moderation flag"
        ordering = ["-last_seen_at"]
        indexes = [
            models.Index(fields=["state", "-severity", "-last_seen_at"]),
            models.Index(fields=["kind", "state"]),
            models.Index(fields=["account_id", "-last_seen_at"]),
        ]

    def __str__(self):
        return f"ModerationFlag({self.kind}, {self.state}, {self.account_name or '-'})"


class SanctionProposal(models.Model):
    """A request for a sanction that the requester may not issue alone.

    Two things separate here on purpose: the work of investigating a case, and
    the authority to make a decision permanent. A staff member who reads a
    queue all day is the right person to find an evader and the wrong person to
    be the only one who decides that a ban never ends.

    So a permanent sanction asked for by somebody without
    ``engine.console.moderation.permanent`` becomes one of these instead of a
    sanction. It has no effect on anybody until a holder approves it, which is
    the same rule a ``ModerationFlag`` follows: an observation is not an
    enforcement decision.

    A declined proposal is kept rather than deleted. "We considered this and
    said no" is the record that stops the same case being re-argued from
    scratch every few months, and it is the record an appeal needs.
    """

    STATE_PENDING = "pending"
    STATE_APPROVED = "approved"
    STATE_DECLINED = "declined"
    STATE_WITHDRAWN = "withdrawn"
    STATE_CHOICES = [
        (STATE_PENDING, "awaiting a decision"),
        (STATE_APPROVED, "approved, sanction issued"),
        (STATE_DECLINED, "reviewed, refused"),
        (STATE_WITHDRAWN, "withdrawn by the person who asked"),
    ]
    OPEN_STATES = frozenset({STATE_PENDING})

    subject_type = models.CharField(max_length=32, db_index=True)
    subject_value = models.CharField(max_length=255, db_index=True)
    level = models.CharField(max_length=16, db_index=True)

    #: Shown to the player if the proposal becomes a sanction, so it carries
    #: the same rule: it must not name the signal that matched.
    reason = models.CharField(max_length=500, default="", blank=True)
    #: Staff-only. This is where the signal belongs.
    staff_note = models.TextField(default="", blank=True)
    evidence = models.JSONField(default=dict, blank=True)

    #: What the sanction would reach, counted when the proposal was made. Kept
    #: rather than recomputed: the reviewer should see the number the requester
    #: saw, and a network's population changes.
    collateral = models.JSONField(default=dict, blank=True)

    flag = models.ForeignKey(
        ModerationFlag,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="proposals",
    )

    proposed_by_id = models.IntegerField(null=True, blank=True, db_index=True)
    proposed_by_name = models.CharField(max_length=255, default="", blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    state = models.CharField(
        max_length=16, choices=STATE_CHOICES, default=STATE_PENDING, db_index=True
    )
    decided_by_id = models.IntegerField(null=True, blank=True)
    decided_by_name = models.CharField(max_length=255, default="", blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.CharField(max_length=500, default="", blank=True)
    sanction = models.ForeignKey(
        Sanction,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="proposals",
    )

    class Meta:
        verbose_name = "Sanction proposal"
        verbose_name_plural = "Sanction proposals"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["state", "-created_at"]),
            models.Index(fields=["subject_type", "subject_value"]),
        ]

    def __str__(self):
        return f"SanctionProposal({self.level} {self.subject_type}:{self.subject_value})"

    @property
    def is_open(self) -> bool:
        """Return whether this proposal still awaits a decision."""

        return self.state in self.OPEN_STATES
