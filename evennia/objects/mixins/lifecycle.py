"""Lifecycle mixin for DefaultObject."""

from django.conf import settings
from django.utils.translation import gettext as _

from evennia.hooks import hook
from evennia.utils import create, logger
from evennia.utils.utils import make_iter

_ScriptDB = None


class LifecycleMixin:
    """Mixin providing lifecycle-related methods for DefaultObject."""

    @hook(
        event="lockstring",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=("DefaultObject.create",),
        notes="Per-class default lockstring used during basetype_setup.",
    )
    @classmethod
    def get_default_lockstring(
        cls, account: "DefaultAccount" = None, caller: "DefaultObject" = None, **kwargs
    ):
        """
        Classmethod called during .create() to determine default locks for the object.

        Args:
            account (DefaultAccount): Account to attribute this object to.
            caller (DefaultObject): The object which is creating this one.
            **kwargs: Arbitrary input.

        Returns:
            str: A lockstring to use for this object.
        """
        pid = f"pid({account.id})" if account else None
        cid = f"id({caller.id})" if caller else None
        admin = "perm(Admin)"
        trio = " or ".join([x for x in [pid, cid, admin] if x])
        return ";".join([f"{x}:{trio}" for x in ["control", "delete", "edit"]])

    @classmethod
    def create(
        cls,
        key: str,
        account: "DefaultAccount" = None,
        caller: "DefaultObject" = None,
        method: str = "create",
        **kwargs,
    ):
        """
        Creates a basic object with default parameters, unless otherwise
        specified or extended.

        Provides a friendlier interface to the utils.create_object() function.

        Args:
            key (str): Name of the new object.


        Keyword Args:
            account (DefaultAccount): Account to attribute this object to.
            caller (DefaultObject): The object which is creating this one.
            description (str): Brief description for this object.
            ip (str): IP address of creator (for object auditing).
            method (str): The method of creation. Defaults to "create".

        Returns:
            tuple: A tuple (Object, errors): A newly created object of the given typeclass. This
            will be `None` if there are errors. The second element is then a list of errors that
            occurred during creation. If this is empty, it's safe to assume the object was created
            successfully.

        """
        errors = []
        obj = None

        # Get IP address of creator, if available
        ip = kwargs.pop("ip", "")

        # If no typeclass supplied, use this class
        kwargs["typeclass"] = kwargs.pop("typeclass", cls)

        # Set the supplied key as the name of the intended object
        kwargs["key"] = key

        # Get a supplied description, if any
        description = kwargs.pop("description", "")

        # Create a sane lockstring if one wasn't supplied
        lockstring = kwargs.get("locks")
        if (account or caller) and not lockstring:
            lockstring = cls.get_default_lockstring(account=account, caller=caller, **kwargs)
            kwargs["locks"] = lockstring

        # Create object
        try:
            obj = create.create_object(**kwargs)

            # Record creator id and creation IP
            if ip:
                obj.db.creator_ip = ip
            if account:
                obj.db.creator_id = account.id

            # Set description if provided
            if description:
                obj.db.desc = description

        except Exception as e:
            errors.append(f"An error occurred while creating '{key}' object: {e}")
            logger.log_trace()

        return obj, errors

    def copy(self, new_key=None, **kwargs):
        """
        Makes an identical copy of this object, identical except for a
        new dbref in the database. If you want to customize the copy
        by changing some settings, use ObjectDB.object.copy_object()
        directly.

        Args:
            new_key (string): New key/name of copied object. If new_key is not
                specified, the copy will be named <old_key>_copy by default.
        Returns:
            Object: A copy of this object.

        """
        from evennia.objects.models import ObjectDB

        def find_clone_key():
            """
            Append 01, 02 etc to obj.key. Checks next higher number in the
            same location, then adds the next number available

            Returns the new clone name on the form keyXX
            """
            key = self.key
            if not self.location:
                # no location means no clone numbering
                return key
            suffixes = [obj.key.removeprefix(key) for obj in self.location.contents]
            num = 1
            if nums := [int(suffix) for suffix in suffixes if suffix.isdigit()]:
                num = max(nums) + 1
            return f"{key}{num:03d}"

        new_key = new_key or find_clone_key()
        new_obj = ObjectDB.objects.copy_object(self, new_key=new_key, **kwargs)
        self.at_object_post_copy(new_obj, **kwargs)
        return new_obj

    @hook(
        event="copy",
        phase="post",
        actor="source",
        returns="ignored",
        discipline="public",
        fires_from=("LifecycleMixin.copy",),
        notes="Fires on the SOURCE object (not the new copy).",
    )
    def at_object_post_copy(self, new_obj, **kwargs):
        """
        Called by DefaultObject.copy(). Meant to be overloaded. In case there's extra data not
        covered by .copy(), this can be used to deal with it.

        Args:
            new_obj (DefaultObject): The new Copy of this object.

        """
        pass

    def delete(self):
        """
        Deletes this object.  Before deletion, this method makes sure
        to move all contained objects to their respective home
        locations, as well as clean up all exits to/from the object.

        Returns:
            bool: Whether or not the delete completed successfully or not.

        """
        global _ScriptDB
        if not _ScriptDB:
            from evennia.scripts.models import ScriptDB as _ScriptDB

        if not self.pk or not self.at_pre_delete():
            # This object has already been deleted,
            # or the pre-delete check return False
            return False

        # See if we need to kick the account off.

        for session in self.sessions.all():
            session.msg(_("Your character {key} has been destroyed.").format(key=self.key))
            # no need to disconnect, Account just jumps to OOC mode.
        # sever the connection (important!)
        if self.account:
            # Remove the object from playable characters list
            self.account.characters.remove(self)
            for session in self.sessions.all():
                self.account.unpuppet_object(session)

        # unlink account/home to avoid issues with saving
        self.db_account = None
        self.db_home = None

        for script in _ScriptDB.objects.get_all_scripts_on_obj(self):
            script.delete()

        # Destroy any exits to and from this room, if any
        self.clear_exits()
        # Clear out any non-exit objects located within the object
        self.clear_contents()
        self.attributes.clear()
        self.nicks.clear()
        self.aliases.clear()
        # Invalidate the location-cmdset cache before nulling the
        # location: removing this object removes its cmdset from the
        # room's available command pool.
        try:
            from evennia.commands.location_cmdset_cache import \
                bump_cmdset_generation

            if self.location is not None:
                bump_cmdset_generation(self.location)
        except Exception:
            logger.log_trace("delete: cmdset-cache invalidation failed")
        self.location = None  # this updates contents_cache for our location

        # Perform the deletion of the object
        super().delete()
        return True

    def access(
        self, accessing_obj, access_type="read", default=False, no_superuser_bypass=False, **kwargs
    ):
        """
        Determines if another object has permission to access this object
        in whatever way.

        Args:
          accessing_obj (DefaultObject): Object trying to access this one.
          access_type (str, optional): Type of access sought.
          default (bool, optional): What to return if no lock of access_type was found.
          no_superuser_bypass (bool, optional): If `True`, don't skip
            lock check for superuser (be careful with this one).
          **kwargs: Passed on to the at_post_access hook along with the result of the access check.

        """
        result = super().access(
            accessing_obj,
            access_type=access_type,
            default=default,
            no_superuser_bypass=no_superuser_bypass,
        )
        self.at_post_access(result, accessing_obj, access_type, **kwargs)
        return result

    @hook(
        event="creation",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="internal",
        fires_from=(),
        notes="Driven by Django post_save signal (created=True). Override at_object_creation instead.",
    )
    def at_first_save(self, **kwargs):
        """
        This is called by the typeclass system whenever an instance of
        this class is saved for the first time. It is a generic hook
        for calling the startup hooks for the various game entities.
        When overloading you generally don't overload this but
        overload the hooks called by this method.

        Args:
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        self.basetype_setup()
        self.at_object_creation()
        # initialize Attribute/TagProperties
        self.init_evennia_properties()

        if hasattr(self, "_createdict"):
            # this will be set if the object was created by the utils.create function
            # or the spawner. We want these kwargs to override the values set by
            # the initial hooks.
            cdict = self._createdict
            updates = []
            if not cdict.get("key"):
                if not self.db_key:
                    self.db_key = "#%i" % self.dbid
                    updates.append("db_key")
            elif self.key != cdict.get("key"):
                updates.append("db_key")
                self.db_key = cdict["key"]
            if cdict.get("location") and self.location != cdict["location"]:
                self.db_location = cdict["location"]
                updates.append("db_location")
            if cdict.get("home") and self.home != cdict["home"]:
                self.home = cdict["home"]
                updates.append("db_home")
            if cdict.get("destination") and self.destination != cdict["destination"]:
                self.destination = cdict["destination"]
                updates.append("db_destination")
            if updates:
                self.save(update_fields=updates)

            if cdict.get("permissions"):
                self.permissions.batch_add(*cdict["permissions"])
            if cdict.get("locks"):
                self.locks.add(cdict["locks"])
            if cdict.get("aliases"):
                self.aliases.batch_add(*cdict["aliases"])
            if cdict.get("location"):
                cdict["location"].at_post_arrive(self, None)
                self.at_post_move(None)
            if cdict.get("tags"):
                # this should be a list of tags, tuples (key, category) or (key, category, data)
                self.tags.batch_add(*cdict["tags"])
            if cdict.get("attributes"):
                # this should be tuples (key, val, ...)
                self.attributes.batch_add(*cdict["attributes"])
            if cdict.get("nattributes"):
                # this should be a dict of nattrname:value
                for key, value in cdict["nattributes"].items():
                    self.nattributes.add(key, value)

            del self._createdict

        # run the post-setup hook
        self.at_object_post_creation()

        self.basetype_posthook_setup()

    # hooks called by the game engine #

    def basetype_setup(self):
        """
        This sets up the default properties of an Object, just before
        the more general at_object_creation.

        You normally don't need to change this unless you change some
        fundamental things like names of permission groups.

        """
        # the default security setup fallback for a generic
        # object. Overload in child for a custom setup. Also creation
        # commands may set this (create an item and you should be its
        # controller, for example)

        self.locks.add(
            ";".join(
                [
                    "control:perm(Developer)",  # edit locks/permissions, delete
                    "examine:perm(Builder)",  # examine properties
                    "view:all()",  # look at object (visibility)
                    "edit:perm(Admin)",  # edit properties/attributes
                    "delete:perm(Admin)",  # delete object
                    "get:all()",  # pick up object
                    "drop:holds()",  # drop only that which you hold
                    "call:true()",  # allow to call commands on this object
                    "tell:perm(Admin)",  # allow emits to this object
                    "puppet:pperm(Developer)",
                    "teleport:true()",
                    "teleport_here:true()",
                ]
            )
        )  # lock down puppeting only to staff by default

    def basetype_posthook_setup(self):
        """
        Called once, after basetype_setup and at_object_creation. This
        should generally not be overloaded unless you are redefining
        how a room/exit/object works. It allows for basetype-like
        setup after the object is created. An example of this is
        EXITs, who need to know keys, aliases, locks etc to set up
        their exit-cmdsets.

        """
        pass

    @hook(
        event="object_creation",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=("LifecycleMixin.at_first_save",),
        notes="One-shot creation hook. Fires once per object via at_first_save.",
    )
    def at_object_creation(self):
        """
        Called once, when this object is first created. This is the
        normal hook to overload for most object types.

        """
        pass

    @hook(
        event="object_creation",
        phase="post",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=("LifecycleMixin.at_first_save",),
        notes="Fires after at_object_creation, lets game-side code run final setup.",
    )
    def at_object_post_creation(self):
        """
        Called once, when this object is first created and after any attributes, tags, etc.
        that were passed to the `create_object` function or defined in a prototype have been
        applied.

        """
        pass

    @hook(
        event="object_delete",
        phase="pre",
        actor="self",
        returns="veto",
        discipline="public",
        fires_from=("LifecycleMixin.delete",),
        notes="Return False to abort delete().",
    )
    def at_pre_delete(self):
        """
        Called just before the database object is persistently
        delete()d from the database. If this method returns False,
        deletion is aborted.

        """
        return True

    @hook(
        event="prototype_spawn",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Spawner-only. Fires when an object is created via a prototype, after at_object_creation.",
    )
    def at_prototype_spawn(self, prototype=None):
        """
        Called when this object is spawned or updated from a prototype, after all other
        hooks have been run.

        Keyword Args:
            prototype (dict):  The prototype that was used to spawn or update this object.
        """
        pass

    @hook(
        event="cache_load",
        phase="post",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=("SharedMemoryModel.__init__",),
        state_cache_state="rehydrated",
        notes="Stub override of TypedObject.at_post_load. Fires on every cache load; overrides must be idempotent.",
    )
    def at_post_load(self):
        """
        This is always called whenever this object is initiated --
        that is, whenever it its typeclass is cached from memory. This
        happens on-demand first time the object is used or activated
        in some way after being created but also after each server
        restart or reload.

        """
        pass

    @hook(
        event="cmdset",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=("CmdSetHandler.get",),
        notes="Last-second mutation of the merged cmdset. See command-system.md.",
    )
    def at_cmdset_get(self, **kwargs):
        """
        Called just before cmdsets on this object are requested by the
        command handler. If changes need to be done on the fly to the
        cmdset before passing them on to the cmdhandler, this is the
        place to do it. This is called also if the object currently
        have no cmdsets.

        Keyword Args:
            caller (DefaultObject, DefaultAccount or Session): The object requesting the cmdsets.
            current (CmdSet): The current merged cmdset.
            force_init (bool): If `True`, force a re-build of the cmdset. (seems unused)
            **kwargs: Arbitrary input for overloads.

        """
        pass

    @hook(
        event="cmdset",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=("CmdSetHandler._get_cmdsets",),
        notes="Returns the per-class cmdset stack as (current, cmdsets). See command-system.md.",
    )
    def get_cmdsets(self, caller, current, **kwargs):
        """
        Called by the CommandHandler to get a list of cmdsets to merge.

        Args:
            caller (DefaultObject): The object requesting the cmdsets.
            current (cmdset): The current merged cmdset.
            **kwargs: Arbitrary input for overloads.

        Returns:
            tuple: A tuple of (current, cmdsets), which is probably self.cmdset.current and self.cmdset.cmdset_stack
        """
        return self.cmdset.current, list(self.cmdset.cmdset_stack)

    @hook(
        event="puppet",
        phase="pre",
        actor="target",
        returns="veto",
        discipline="public",
        fires_from=(
            "DefaultAccount.puppet_object",
            "ServerSession.at_sync",
        ),
        state_pk=True,
        state_db_row=True,
        state_init_done=True,
        state_cache_state="rehydrated",
        state_mid_transaction=False,
        notes="Reattach path fires with reattach=True kwarg.",
    )
    def at_pre_puppet(self, account, session=None, **kwargs):
        """
        Called just before an Account connects to this object to puppet it.

        Veto rule: return `False` (or any non-None falsy value) to abort
        the puppet attach; the session will be left unpuppeted with no
        engine-side error message (the override should `account.msg(...)`
        an explanation before returning). Return `True`, `None`, or any
        other truthy value to allow the attach. See
        `evennia.utils.utils.is_veto` for the canonical rule.

        Args:
            account (DefaultAccount): The connecting account.
            session (Session): Session controlling the connection.
            **kwargs: Arbitrary, optional arguments for users overriding
                the call (unused by default).

        Returns:
            bool or None: `False` (or non-None falsy) to abort the puppet,
            otherwise allow it.

        """
        pass

    @hook(
        event="puppet",
        phase="post",
        actor="target",
        returns="ignored",
        discipline="public",
        fires_from=(
            "DefaultAccount.puppet_object",
            "ServerSession.at_sync",
        ),
        notes="Reattach path fires with reattach=True kwarg.",
    )
    def at_post_puppet(self, **kwargs):
        """
        Called just after puppeting has been completed and all
        Account<->Object links have been established.

        Args:
            **kwargs: Arbitrary, optional arguments for users
                overriding the call. The engine passes `reattach=True`
                when the call originates from `ServerSession.at_sync`
                on server reload (the session is re-binding to a
                puppet it already controlled before the reload, not
                puppeting fresh). Default behavior swallows the
                user-visible echo in that case so reloads don't spam
                every connected player.
        Notes:
            You can use `self.account` and `self.sessions.get()` to get account and sessions at this
            point; the last entry in the list from `self.sessions.get()` is the latest Session
            puppeting this Object.

        """
        if kwargs.get("reattach"):
            return
        self.msg(_("You become |w{key}|n.").format(key=self.key))
        self.account.db._last_puppet = self

    @hook(
        event="unpuppet",
        phase="pre",
        actor="target",
        returns="veto",
        discipline="public",
        fires_from=("DefaultAccount.unpuppet_object",),
        notes="Veto aborts detach. Puppet stays attached; session.puppet/puid stay set.",
    )
    def at_pre_unpuppet(self, **kwargs):
        """
        Called just before beginning to un-connect a puppeting from this
        Account.

        **Not vetoable.** Return value is ignored. Unpuppet runs during
        session disconnect and server shutdown paths; blocking it would
        strand state between the engine and the underlying transport.
        Raise if you genuinely need to abort, but expect the caller's
        cleanup path to handle the exception.

        Args:
            **kwargs: Arbitrary, optional arguments for users overriding
                the call (unused by default).
        Notes:
            You can use `self.account` and `self.sessions.get()` to get
            account and sessions at this point; the last entry in the
            list from `self.sessions.get()` is the latest Session
            puppeting this Object.

        """
        pass

    @hook(
        event="unpuppet",
        phase="post",
        actor="target",
        returns="ignored",
        discipline="public",
        fires_from=("DefaultAccount.unpuppet_object",),
        notes="Fires after the session detaches. session.puppet/puid have been cleared.",
    )
    def at_post_unpuppet(self, account, session=None, **kwargs):
        """
        Called just after the Account successfully disconnected from
        this object, severing all connections.

        Args:
            account (DefaultAccount): The account object that just disconnected
                from this object. This can be `None` if this is called
                automatically (such as after a cleanup operation).
            session (Session): Session id controlling the connection that
                just disconnected.
            **kwargs: Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        pass

    @hook(
        event="server_lifecycle",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Fires from EvenniaServerService.shutdown on reload-style stops.",
    )
    def at_server_reload(self):
        """
        This hook is called whenever the server is shutting down for
        restart/reboot. If you want to, for example, save non-persistent
        properties across a restart, this is the place to do it.

        """
        pass

    @hook(
        event="server_lifecycle",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Fires from EvenniaServerService.shutdown on full-shutdown stops.",
    )
    def at_server_shutdown(self):
        """
        This hook is called whenever the server is fully shut down (i.e. not for a restart).

        """
        pass

    @hook(
        event="access_check",
        phase="post",
        actor="target",
        returns="ignored",
        discipline="public",
        fires_from=("LockHandler.check",),
        notes="Fires after a lock check resolves. Gets the result and the accessing object.",
    )
    def at_post_access(self, result, accessing_obj, access_type, **kwargs):
        """
        This is called with the result of an access call, along with
        any kwargs used for that call. The return of this method does
        not affect the result of the lock check. It can be used e.g. to
        customize error messages in a central location or other effects
        based on the access result.

        Args:
            result (bool): The outcome of the access call.
            accessing_obj (Object or Account): The entity trying to gain access.
            access_type (str): The type of access that was requested.
            **kwargs: Arbitrary, optional arguments. Unused by default.

        """
        pass
