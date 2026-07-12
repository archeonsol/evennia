"""

Server Configuration flags

This holds persistent server configuration flags.

Config values should usually be set through the
manager's conf() method.

"""

from django.db import models

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
