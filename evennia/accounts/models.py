"""
Account

The account class is an extension of the default Django user class,
and is customized for the needs of Evennia.

We use the Account to store a more mud-friendly style of permission
system as well as to allow the admin more flexibility by storing
attributes on the Account.  Within the game we should normally use the
Account manager's methods to create users so that permissions are set
correctly.

To make the Account model more flexible for your own game, it can also
persistently store attributes of its own. This is ideal for extra
account info and OOC account configuration variables etc.

"""

import re

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils.encoding import smart_str

from evennia.accounts.manager import AccountDBManager
from evennia.server.signals import SIGNAL_ACCOUNT_POST_RENAME
from evennia.typeclasses.models import TypedObject
from evennia.utils.utils import make_iter

__all__ = ("AccountDB", "ControlBinding")

_GA = object.__getattribute__
_SA = object.__setattr__
_DA = object.__delattr__

_TYPECLASS = None


# ------------------------------------------------------------
#
# AccountDB
#
# ------------------------------------------------------------


class AccountDB(TypedObject, AbstractUser):
    """
    This is a special model using Django's 'profile' functionality
    and extends the default Django User model. It is defined as such
    by use of the variable AUTH_PROFILE_MODULE in the settings.
    One accesses the fields/methods. We try use this model as much
    as possible rather than User, since we can customize this to
    our liking.

    The TypedObject supplies the following (inherited) properties:

      - key - main name
      - typeclass_path - the path to the decorating typeclass
      - typeclass - auto-linked typeclass
      - date_created - time stamp of object creation
      - permissions - perm strings
      - dbref - #id of object
      - db - persistent attribute storage
      - ndb - non-persistent attribute storage

    The AccountDB adds the following properties:

      - is_connected - If any Session is currently connected to this Account
      - name - alias for user.username
      - sessions - sessions connected to this account
      - is_superuser - bool if this account is a superuser
      - is_bot - bool if this account is a bot and not a real account

    """

    #
    # AccountDB Database model setup
    #
    # inherited fields (from TypedObject):
    # db_key, db_typeclass_path, db_date_created, db_permissions

    # JSONB attribute document — one row per object (Workstream B).
    db_attrs = models.JSONField(
        "attrs",
        default=dict,
        blank=True,
        help_text="JSONB attribute document. Replaces the db_attributes M2M when the JSONB backend is active.",
    )

    # store a connected flag here too, not just in sessionhandler.
    # This makes it easier to track from various out-of-process locations
    db_is_connected = models.BooleanField(
        default=False,
        verbose_name="is_connected",
        help_text="If player is connected to game or not",
    )
    # database storage of persistant cmdsets.
    db_cmdset_storage = models.CharField(
        "cmdset",
        max_length=255,
        null=True,
        help_text=(
            "optional python path to a cmdset class. If creating a Character, this will "
            "default to settings.CMDSET_CHARACTER."
        ),
    )
    # marks if this is a "virtual" bot account object
    db_is_bot = models.BooleanField(
        default=False, verbose_name="is_bot", help_text="Used to identify irc/rss bots"
    )

    # Database manager
    objects = AccountDBManager()

    # defaults
    __defaultclasspath__ = "evennia.accounts.accounts.DefaultAccount"
    __applabel__ = "accounts"
    __settingsclasspath__ = settings.BASE_SCRIPT_TYPECLASS

    class Meta:
        verbose_name = "Account"

    # cmdset_storage property
    # This seems very sensitive to caching, so leaving it be for now /Griatch
    # @property
    def __cmdset_storage_get(self):
        """
        Getter. Allows for value = self.name. Returns a list of cmdset_storage.
        """
        storage = self.db_cmdset_storage
        # we need to check so storage is not None
        return [path.strip() for path in storage.split(",")] if storage else []

    # @cmdset_storage.setter
    def __cmdset_storage_set(self, value):
        """
        Setter. Allows for self.name = value. Stores as a comma-separated
        string.
        """
        _SA(self, "db_cmdset_storage", ",".join(str(val).strip() for val in make_iter(value)))
        _GA(self, "save")()

    # @cmdset_storage.deleter
    def __cmdset_storage_del(self):
        "Deleter. Allows for del self.name"
        _SA(self, "db_cmdset_storage", None)
        _GA(self, "save")()

    cmdset_storage = property(__cmdset_storage_get, __cmdset_storage_set, __cmdset_storage_del)

    #
    # property/field access
    #

    def __str__(self):
        return smart_str(f"{self.name}(account {self.dbid})")

    def __repr__(self):
        return f"{self.name}(account#{self.dbid})"

    # @property
    def __username_get(self):
        return self.username

    def __username_set(self, value):
        old_name = self.username
        self.username = value
        self.save(update_fields=["username"])
        SIGNAL_ACCOUNT_POST_RENAME.send(self, old_name=old_name, new_name=value)

    def __username_del(self):
        del self.username

    # aliases
    name = property(__username_get, __username_set, __username_del)
    key = property(__username_get, __username_set, __username_del)

    # @property
    def __uid_get(self):
        "Getter. Retrieves the user id"
        return self.id

    def __uid_set(self, value):
        raise Exception("User id cannot be set!")

    def __uid_del(self):
        raise Exception("User id cannot be deleted!")

    uid = property(__uid_get, __uid_set, __uid_del)


# ------------------------------------------------------------
#
# ControlBinding (I1) — the durable control graph
#
# ------------------------------------------------------------

#: stack-entry kinds (the floor is always an account; everything above is an
#: in-world body — the IC character, then any nested bodies it drives).
CONTROL_ACCOUNT = "account"
CONTROL_OBJECT = "object"

# Player-character puppet locks from DefaultAccount.at_post_create_character.
_PID_LOCK_RE = re.compile(r"pid\((\d+)\)")


class ControlBinding(models.Model):
    """One identity's durable control graph (I1).

    Replaces the ``session.puppet`` / ``obj.account`` pointer pair. A binding
    holds an **ordered focus stack** — the chain of things the controller is
    currently driving::

        [["account", 4]]                       # OOC / character-select
        [["account", 4], ["object", 18]]       # @ic — driving the character
        [["account", 4], ["object", 18], ["object", 73]]   # jacked into avatar #73

    The top of the stack is the *active body* (``focus``); the floor is always
    the controlling account, so ``focus`` is never empty and never ``None``.
    Sessions are runtime-only and re-attach at connect — they are never stored
    here.

    ``db_generation`` bumps on every structural mutation; an in-flight dispatch
    captures it and re-validates on resume so a co-session's push/pop across a
    suspend point can never act on a body that has since been popped (the
    multi-session race guard).
    """

    db_account = models.ForeignKey(
        "accounts.AccountDB",
        related_name="control_bindings",
        on_delete=models.CASCADE,
        db_index=True,
        help_text="The account that owns this control graph.",
    )
    db_identity = models.OneToOneField(
        "objects.ObjectDB",
        related_name="control_binding",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The persistent IC self (the character) this binding is for.",
    )
    db_focus_stack = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered [[kind, id], ...]; floor=account, top=active body.",
    )
    db_generation = models.PositiveIntegerField(
        default=0,
        help_text="Bumps on every push/pop (the multi-session race guard).",
    )

    class Meta:
        verbose_name = "Control Binding"
        verbose_name_plural = "Control Bindings"

    def __str__(self):
        return f"ControlBinding(account#{self.db_account_id}, identity#{self.db_identity_id})"

    # -- stack encoding -----------------------------------------------------
    @staticmethod
    def _entry_for(obj):
        """Encode a live object as a ``[kind, id]`` stack entry."""
        if isinstance(obj, AccountDB):
            return [CONTROL_ACCOUNT, obj.id]
        return [CONTROL_OBJECT, obj.id]

    @staticmethod
    def _resolve(entry):
        """Resolve a ``[kind, id]`` entry back to a live typeclassed object
        (``None`` if the row was deleted out from under us)."""
        if not entry:
            return None
        kind, pk = entry
        if kind == CONTROL_ACCOUNT:
            return AccountDB.objects.filter(id=pk).first()
        from evennia.objects.models import ObjectDB

        return ObjectDB.objects.filter(id=pk).first()

    def _ensure_floor(self):
        """Guarantee the account sits at the stack floor (idempotent)."""
        floor = [CONTROL_ACCOUNT, self.db_account_id]
        if not self.db_focus_stack:
            self.db_focus_stack = [floor]
        elif self.db_focus_stack[0] != floor:
            self.db_focus_stack.insert(0, floor)

    # -- queries ------------------------------------------------------------
    @property
    def focus(self):
        """The active body — top of stack, resolved to a live object."""
        self._ensure_floor()
        return self._resolve(self.db_focus_stack[-1])

    @property
    def stack_objects(self):
        """The whole focus stack resolved bottom→top (skipping dead rows)."""
        self._ensure_floor()
        return [obj for obj in (self._resolve(e) for e in self.db_focus_stack) if obj is not None]

    def contains(self, obj):
        """True if ``obj`` is anywhere in the focus stack."""
        entry = self._entry_for(obj)
        return entry in self.db_focus_stack

    # -- DB-authoritative reads (cross-session race guard) ------------------
    def current_generation(self):
        """The generation straight from the DB, so another session's bump on a
        *different* in-memory instance of this row is seen. ``None`` if the row
        was deleted. Cheap: a single indexed-PK ``values_list``."""
        return type(self).objects.filter(pk=self.pk).values_list("db_generation", flat=True).first()

    def live_contains(self, obj):
        """True if ``obj`` is on the *persisted* stack (re-read from the DB),
        not just this instance's possibly-stale copy. ``False`` if the row is
        gone."""
        fresh = (
            type(self).objects.filter(pk=self.pk).values_list("db_focus_stack", flat=True).first()
        )
        if not fresh:
            return False
        return self._entry_for(obj) in fresh

    # -- mutations (each bumps generation + saves) --------------------------
    def push(self, body):
        """Push ``body`` as the new active focus. Returns the pushed object."""
        self._ensure_floor()
        self.db_focus_stack.append(self._entry_for(body))
        self._bump()
        return body

    def pop(self):
        """Pop the top body (never below the account floor). Returns the
        dropped object, or ``None`` if only the floor remained."""
        self._ensure_floor()
        if len(self.db_focus_stack) <= 1:
            return None
        dropped = self._resolve(self.db_focus_stack.pop())
        self._bump()
        return dropped

    def collapse_to(self, body):
        """Pop bodies until ``body`` is the active focus, returning the dropped
        objects **top-first** (so callers can fire one teardown per level in
        unwind order). If ``body`` is not in the stack, collapses to the floor.

        The single generation bump covers the whole collapse.
        """
        self._ensure_floor()
        target = self._entry_for(body)
        dropped = []
        while len(self.db_focus_stack) > 1 and self.db_focus_stack[-1] != target:
            dropped.append(self._resolve(self.db_focus_stack.pop()))
        if dropped:
            self._bump()
        return dropped

    def _bump(self):
        self.db_generation = (self.db_generation or 0) + 1
        self.save(update_fields=["db_focus_stack", "db_generation"])

    # -- construction -------------------------------------------------------
    @classmethod
    def for_identity(cls, account, identity):
        """Get-or-create the binding for a persistent IC self (``identity``).

        The identity is the durable anchor (OneToOne), so one character has
        exactly one control graph regardless of which account currently drives
        it. The floor is (re)asserted to the *current* controlling account, so
        an ownership transfer is reflected without orphaning the stack.
        """
        binding, _created = cls.objects.get_or_create(
            db_identity=identity, defaults={"db_account": account}
        )
        if binding.db_account_id != account.id:
            binding.db_account = account
            binding.save(update_fields=["db_account"])
        binding._ensure_floor()
        return binding

    @classmethod
    def ensure_playable(cls, account, identity):
        """Idempotent: durable ``ControlBinding`` + ``ObjectDB.db_account`` sync.

        Mirrors :meth:`CharactersHandler.add` ownership writes without firing
        ``at_character_added`` (safe for bulk backfill).
        """
        if account is None or identity is None:
            return False
        identity_id = getattr(identity, "id", None) or getattr(identity, "pk", None)
        account_id = getattr(account, "id", None) or getattr(account, "pk", None)
        if not identity_id or not account_id:
            return False
        had_binding = cls.objects.filter(db_identity_id=identity_id).exists()
        cls.for_identity(account, identity)
        if getattr(identity, "db_account_id", None) != account_id:
            identity.db_account_id = account_id
            identity.save(update_fields=["db_account"])
        return not had_binding

    @classmethod
    def _identity_ids_for_account(cls, account):
        """Character ids already bound to this account in the control graph."""
        return set(
            cls.objects.filter(db_account=account)
            .exclude(db_identity__isnull=True)
            .values_list("db_identity_id", flat=True)
        )

    @classmethod
    def reconcile_account(cls, account, *, scan_locks=True):
        """Repair one account's playable set from legacy ownership signals.

        Sources (in order): ``ObjectDB.db_account``, ``_last_puppet``,
        ``_playable_characters``, and (optional) ``pid()`` puppet locks on
        character typeclasses with no ``db_account``.

        Returns:
            int: number of identities newly linked to this account.
        """
        from evennia.objects.models import ObjectDB

        if account is None:
            return 0
        linked = cls._identity_ids_for_account(account)
        created = 0

        def _try(identity):
            nonlocal created
            if identity is None:
                return
            identity_id = getattr(identity, "id", None)
            if not identity_id or identity_id in linked:
                return
            if cls.ensure_playable(account, identity):
                created += 1
            linked.add(identity_id)

        for identity_id in ObjectDB.objects.filter(db_account_id=account.id).values_list(
            "id", flat=True
        ):
            _try(ObjectDB.objects.filter(id=identity_id).first())

        last = getattr(getattr(account, "db", None), "_last_puppet", None)
        _try(last)

        playable = getattr(getattr(account, "db", None), "_playable_characters", None) or []
        for entry in make_iter(playable):
            _try(entry)

        if scan_locks:
            char_marker = (
                getattr(settings, "BASE_CHARACTER_TYPECLASS", "") or "characters"
            ).rsplit(".", 1)[-1]
            qs = ObjectDB.objects.filter(db_account__isnull=True).exclude(db_lock_storage="")
            if char_marker:
                qs = qs.filter(db_typeclass_path__icontains=char_marker)
            for identity_id, lock_storage in qs.values_list("id", "db_lock_storage"):
                if identity_id in linked:
                    continue
                match = _PID_LOCK_RE.search(lock_storage or "")
                if not match or int(match.group(1)) != account.id:
                    continue
                _try(ObjectDB.objects.filter(id=identity_id).first())

        return created

    @classmethod
    def reconcile_ownership(cls):
        """Global idempotent I1 ownership backfill (all accounts).

        Safe on every server start and reload. Returns a stats dict for logging.
        """
        from evennia.objects.models import ObjectDB

        stats = {"accounts": 0, "puppet_lock": 0, "db_account_resync": 0}

        for account in AccountDB.objects.all():
            stats["accounts"] += cls.reconcile_account(account, scan_locks=False)

        char_marker = (getattr(settings, "BASE_CHARACTER_TYPECLASS", "") or "characters").rsplit(
            ".", 1
        )[-1]
        qs = ObjectDB.objects.filter(db_account__isnull=True).exclude(db_lock_storage="")
        if char_marker:
            qs = qs.filter(db_typeclass_path__icontains=char_marker)
        for identity_id, lock_storage in qs.values_list("id", "db_lock_storage"):
            match = _PID_LOCK_RE.search(lock_storage or "")
            if not match:
                continue
            account = AccountDB.objects.filter(id=int(match.group(1))).first()
            if not account:
                continue
            if identity_id in cls._identity_ids_for_account(account):
                continue
            identity = ObjectDB.objects.filter(id=identity_id).first()
            if identity and cls.ensure_playable(account, identity):
                stats["puppet_lock"] += 1

        for binding in cls.objects.exclude(db_identity__isnull=True).select_related(
            "db_account", "db_identity"
        ):
            identity = binding.db_identity
            if identity is None:
                continue
            if identity.db_account_id != binding.db_account_id:
                identity.db_account_id = binding.db_account_id
                identity.save(update_fields=["db_account"])
                stats["db_account_resync"] += 1

        stats["created"] = sum(stats.values())
        return stats

    @classmethod
    def populate_missing(cls):
        """Boot bulk-job: reconcile legacy ownership into ``ControlBinding`` rows.

        Idempotent — safe on every server start **and** reload. Returns the
        number of new links plus ``db_account`` resyncs (same as
        ``reconcile_ownership()['created']``).
        """
        return cls.reconcile_ownership().get("created", 0)
