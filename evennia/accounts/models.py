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
from evennia.utils.idmapper.models import SharedMemoryModel
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


class ControlBinding(SharedMemoryModel):
    """One control graph: an account driving a chain of bodies (I1).

    Anchored (OneToOne) on ``db_identity`` — the *root driven body* of this
    graph (a player's character, or an NPC a builder is possessing). It holds
    an **ordered focus stack** of the bodies layered above that root::

        []                                  # OOC — focus rests on the controller
        [["object", 18]]                    # @ic — driving character #18
        [["object", 18], ["object", 73]]    # jacked into avatar #73

    Three distinct facts, each with a single home (none duplicated here):

    - **Ownership** ("who this character belongs to") lives on the identity's
      ``ObjectDB.db_account``, *not* here.
    - **Controller** — the account this graph's floor rests on, i.e. where
      ``focus`` resolves when the stack is empty — is ``db_account``. It is the
      account currently *driving*: equal to the owner for a player on their own
      character, but different when staff possess an NPC or another's body.
      ``for_identity`` (re)points it at the current driver; ownership is never
      touched.
    - **Live driving** is ``ObjectDB.sessions`` at runtime; never stored here.

    The stack stores only the bodies *above* the floor; the floor is derived
    (``focus`` returns ``db_account`` on an empty stack), so it is never
    persisted twice. As a :class:`SharedMemoryModel` the binding is
    idmapper-cached: every session driving the same row resolves the one shared
    instance, so a co-session's push/pop is seen with no refresh.

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
        help_text="The account currently driving this graph (the stack floor).",
    )
    db_identity = models.OneToOneField(
        "objects.ObjectDB",
        related_name="control_binding",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        help_text="The root driven body (character/NPC) this graph is anchored on.",
    )
    db_focus_stack = models.JSONField(
        default=list,
        blank=True,
        help_text="Ordered [[kind, id], ...] of bodies ABOVE the floor; top=active body.",
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

    # -- queries ------------------------------------------------------------
    @property
    def controller(self):
        """The account driving this graph (the stack floor)."""
        return self.db_account

    @property
    def focus(self):
        """The active body — top of the stack, resolved to a live object, or
        the controller account when the stack is empty (OOC). Never ``None``
        for a live row."""
        if self.db_focus_stack:
            return self._resolve(self.db_focus_stack[-1])
        return self.db_account

    @property
    def stack_objects(self):
        """The bodies above the floor, resolved bottom→top (dead rows skipped)."""
        return [obj for obj in (self._resolve(e) for e in self.db_focus_stack) if obj is not None]

    def contains(self, obj):
        """True if ``obj`` is a body anywhere in the focus stack."""
        return self._entry_for(obj) in self.db_focus_stack

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
        self.db_focus_stack.append(self._entry_for(body))
        self._bump()
        return body

    def pop(self):
        """Pop the top body. Returns the dropped object, or ``None`` if the
        stack was already empty (focus already on the controller floor)."""
        if not self.db_focus_stack:
            return None
        dropped = self._resolve(self.db_focus_stack.pop())
        self._bump()
        return dropped

    def collapse_to(self, body):
        """Pop bodies until ``body`` is the active focus, returning the dropped
        objects **top-first** (so callers can fire one teardown per level in
        unwind order). If ``body`` is not a body in the stack (e.g. the
        controller account), collapses all the way to the floor (empty stack).

        The single generation bump covers the whole collapse.
        """
        target = self._entry_for(body)
        dropped = []
        while self.db_focus_stack and self.db_focus_stack[-1] != target:
            dropped.append(self._resolve(self.db_focus_stack.pop()))
        if dropped:
            self._bump()
        return dropped

    def collapse_to_floor(self):
        """Go fully OOC: pop every body so ``focus`` resolves to the controller.
        Returns the dropped objects top-first."""
        return self.collapse_to(self.db_account)

    def _bump(self):
        self.db_generation = (self.db_generation or 0) + 1
        self.save(update_fields=["db_focus_stack", "db_generation"])

    # -- construction -------------------------------------------------------
    @classmethod
    def for_identity(cls, account, identity):
        """Get-or-create the control graph anchored on ``identity``, with
        ``account`` as its controller (the driver / stack floor).

        The identity is the durable anchor (OneToOne), so a body has exactly
        one control graph. Attaching points the **controller** at the current
        driver — a possession or takeover repoints it — but never changes the
        identity's **ownership** (``ObjectDB.db_account``); route ownership
        through the owning account's ``characters`` handler.
        """
        binding, created = cls.objects.get_or_create(
            db_identity=identity, defaults={"db_account": account, "db_focus_stack": []}
        )
        if created:
            # save() does not auto-cache; make the creator's instance the
            # canonical idmapper one so every later objects.get() shares it
            # (and a co-session never resolves a divergent copy).
            cls.cache_instance(binding)
        account_id = getattr(account, "id", None) or getattr(account, "pk", None)
        if account_id and binding.db_account_id != account_id:
            binding.db_account_id = account_id
            binding.save(update_fields=["db_account"])
        return binding

    @classmethod
    def ensure_playable(cls, account, identity):
        """Idempotent ownership backfill: set the identity's durable owner
        (``ObjectDB.db_account``) to ``account`` if not already so.

        Migration scaffolding only — ownership is otherwise written through the
        account's ``characters`` handler. Does not create a binding (control
        graphs are minted on first puppet). Returns True if newly assigned.
        """
        if account is None or identity is None:
            return False
        identity_id = getattr(identity, "id", None) or getattr(identity, "pk", None)
        account_id = getattr(account, "id", None) or getattr(account, "pk", None)
        if not identity_id or not account_id:
            return False
        if getattr(identity, "db_account_id", None) == account_id:
            return False
        identity.db_account_id = account_id
        identity.save(update_fields=["db_account"])
        return True

    @classmethod
    def _identity_ids_for_account(cls, account):
        """Ids of the characters already owned by this account."""
        from evennia.objects.models import ObjectDB

        return set(ObjectDB.objects.filter(db_account=account).values_list("id", flat=True))

    @classmethod
    def reconcile_account(cls, account, *, scan_locks=True):
        """Backfill one account's character ownership from legacy signals.

        Sets ``ObjectDB.db_account`` for not-yet-owned characters named by
        ``_last_puppet``, ``_playable_characters``, and (optional) ``pid()``
        puppet locks. Characters the account already owns are skipped.

        Returns:
            int: number of characters newly owned by this account.
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
        """Global idempotent ownership backfill (all accounts).

        Sets ``ObjectDB.db_account`` from legacy signals; never touches
        ``ControlBinding.db_account`` (the controller is a separate fact from
        ownership and would be corrupted by a sync). Safe on every start and
        reload. Returns a stats dict for logging.
        """
        from evennia.objects.models import ObjectDB

        stats = {"accounts": 0, "puppet_lock": 0}

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
            identity = ObjectDB.objects.filter(id=identity_id).first()
            if identity and cls.ensure_playable(account, identity):
                stats["puppet_lock"] += 1

        stats["created"] = stats["accounts"] + stats["puppet_lock"]
        return stats
