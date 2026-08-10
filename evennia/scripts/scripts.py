"""
This module defines Scripts, out-of-character entities that store data on
themselves and on other objects through the typeclass system.

A Script has no in-game presence and no timer component: it is a persistent
(or non-persistent) typeclassed storage container. Recurring work belongs to
the System Scheduler (`evennia.utils.systems`); one-shot delayed callbacks
belong to `evennia.utils.delay`.

"""

from evennia.hooks import hook
from evennia.scripts.manager import ScriptManager
from evennia.scripts.models import ScriptDB
from evennia.typeclasses.models import TypeclassBase
from evennia.utils import create, logger

__all__ = ["DefaultScript", "DoNothing", "Store"]


class ScriptBase(ScriptDB, metaclass=TypeclassBase):
    """
    Base class for scripts. Don't inherit from this, inherit from the
    class `DefaultScript` below instead.

    """

    objects = ScriptManager()

    def __str__(self):
        return "<{cls} {key}>".format(cls=self.__class__.__name__, key=self.key)

    def __repr__(self):
        return str(self)

    @hook(
        event="creation",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="internal",
        fires_from=(),
        notes="Driven by Django post_save signal (created=True). Override at_script_creation instead.",
    )
    def at_first_save(self, **kwargs):
        """
        This is called after very first time this object is saved.
        Generally, you don't need to overload this, but only the hooks
        called by this method.

        Args:
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        self.basetype_setup()
        self.at_script_creation()
        # initialize Attribute/TagProperties
        self.init_evennia_properties()

        if hasattr(self, "_createdict"):
            # this will only be set if the utils.create_script
            # function was used to create the object. We want
            # the create call's kwargs to override the values
            # set by hooks.
            cdict = self._createdict
            updates = []
            if not cdict.get("key"):
                if not self.db_key:
                    if hasattr(self, "key"):
                        # take key from the object typeclass
                        self.db_key = self.key
                    else:
                        # no key set anywhere, use class+dbid as key
                        self.db_key = f"{self.__class__.__name__}(#{self.dbid})"
                    updates.append("db_key")
            elif self.db_key != cdict["key"]:
                self.db_key = cdict["key"]
                updates.append("db_key")
            if cdict.get("persistent") and self.persistent != cdict["persistent"]:
                self.db_persistent = cdict["persistent"]
                updates.append("db_persistent")
            if cdict.get("desc") and self.desc != cdict["desc"]:
                self.db_desc = cdict["desc"]
                updates.append("db_desc")
            if updates:
                self.save(update_fields=updates)

            if cdict.get("policies"):
                for operation, policy in cdict["policies"].items():
                    self.policies.set(operation, policy)
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

        self.at_script_post_creation()

    @hook(
        event="script_creation",
        phase="post",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=("ScriptBase.at_first_save",),
        notes="Fires after at_script_creation and _createdict processing. Symmetric to at_object_post_creation.",
    )
    def at_script_post_creation(self):
        """
        Called once, after `at_script_creation` and _createdict processing.
        Override for game-side initialization that needs to run after all
        engine-side creation steps complete.
        """
        pass

    def delete(self):
        """
        Delete the Script. This fires at_pre_delete before deletion.

        Returns:
            bool: If deletion was successful or not. Only time this can fail would be if
                the script was already previously deleted, or `at_pre_delete` returns
                False.

        """
        from evennia.utils.idmapper.models import _preflight_model_delete

        _preflight_model_delete(self)
        if not self.pk or not self.at_pre_delete():
            return False

        # Match ObjectDB/AccountDB/ChannelDB: route attribute removal through
        # the AttributeHandler so backend invalidation (Redis L2) fires
        # per-attr rather than leaving keys orphaned until TTL expiry.
        self.attributes.clear()
        super().delete()
        return True

    def basetype_setup(self):
        """
        Changes fundamental aspects of the type. Usually changes are made in at_script creation
        instead.

        """
        pass

    def at_post_load(self):
        """
        Called when the Script is cached in the idmapper. This is usually more reliable
        than overriding `__init__` since the latter can be called at unexpected times.

        """
        pass

    @hook(
        event="script_creation",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=("ScriptBase.at_first_save",),
        notes="One-shot creation hook. Fires once per script via at_first_save.",
    )
    def at_script_creation(self):
        """
        Should be overridden in child.

        """
        pass

    @hook(
        event="script_delete",
        phase="pre",
        actor="self",
        returns="veto",
        discipline="public",
        fires_from=("ScriptBase.delete",),
        notes="Return False to abort delete().",
    )
    def at_pre_delete(self):
        """
        Called when script is deleted, before deletion proceeds.

        Returns:
            bool: If False, deletion is aborted.

        """
        return True


class DefaultScript(ScriptBase):
    """
    This is the base TypeClass for all Scripts. Scripts describe
    all entities/systems without a physical existence in the game world
    that require database storage (like an economic system or a combat
    tracker). A Script is a typeclassed storage container; it has no timer
    component.

    A script type is customized by redefining some or all of its hook
    methods and variables.

    * available properties (check docs for full listing, this could be
      outdated).

     key (string) - name of object
     name (string)- same as key
     aliases (list of strings) - aliases to the object. Will be saved
              to database as AliasDB entries but returned as strings.
     dbref (int, read-only) - unique #id-number. Also "id" can be used.
     date_created (string) - time stamp of object creation
     permissions (list of strings) - list of permission strings

     desc (string)      - optional description of script, shown in listings
     obj (Object)       - optional object that this script is connected to
                          and acts on (set automatically by obj.scripts.add())
     persistent (bool)  - if script should survive a server shutdown or not

    * Handlers

     locks - lock-handler: use locks.add() to add new lock strings
     db - attribute-handler: store/retrieve database attributes on this
                        self.db.myattr=val, val=self.db.myattr
     ndb - non-persistent attribute handler: same as db but does not
                        create a database entry when storing data

    * Helper methods

     create(key, **kwargs)
     delete() - delete the script

    * Hook methods (should also include self as the first argument):

     at_script_creation() - called only once, when an object of this
                            class is first created.
     at_pre_delete()
     at_server_reload() - Called when server reloads. Can be used to
                 save temporary variables you want should survive a reload.
     at_server_shutdown() - called at a full server shutdown.
     at_server_start()

    """

    from evennia.authorization.policy import RequiresCapability

    authorization_policies = {
        "control": RequiresCapability("engine.script.control"),
        "edit": RequiresCapability("engine.script.control"),
        "delete": RequiresCapability("engine.script.control"),
    }

    @classmethod
    def create(cls, key, **kwargs):
        """
        Provides a passthrough interface to the utils.create_script() function.

        Args:
            key (str): Name of the new object.

        Returns:
            object (Object): A newly created object of the given typeclass.
            errors (list): A list of errors in string form, if any.

        """
        errors = []
        obj = None

        kwargs["key"] = key

        # If no typeclass supplied, use this class
        kwargs["typeclass"] = kwargs.pop("typeclass", cls)

        try:
            obj = create.create_script(**kwargs)
        except Exception:
            logger.log_trace()
            errors.append("The script '%s' encountered errors and could not be created." % key)

        return obj, errors

    def at_script_creation(self):
        """
        Only called once, when script is first created.

        """
        pass

    def at_pre_delete(self):
        """
        Called when the Script is deleted, before deletion proceeds.

        Returns:
            bool: If False, the deletion is aborted.

        """
        return True

    @hook(
        event="server_lifecycle",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Fired from EvenniaServerService.shutdown on reload-style stops; persist non-persistent state here.",
    )
    def at_server_reload(self):
        """
        This hook is called whenever the server is shutting down for
        restart/reboot. If you want to, for example, save
        non-persistent properties across a restart, this is the place
        to do it.
        """
        pass

    @hook(
        event="server_lifecycle",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Fired from EvenniaServerService.shutdown on full-shutdown stops.",
    )
    def at_server_shutdown(self):
        """
        This hook is called whenever the server is shutting down fully
        (i.e. not for a restart).
        """
        pass

    @hook(
        event="server_lifecycle",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Fired from EvenniaServerService.run_init_hooks.",
    )
    def at_server_start(self):
        """
        This hook is called after the server has started. It can be used to add
        post-startup setup for Scripts.

        """
        pass


# Some useful default Script types used by Evennia.


class DoNothing(DefaultScript):
    """
    A script that does nothing. Used as default fallback.
    """

    def at_script_creation(self):
        """
        Setup the script
        """
        self.key = "sys_do_nothing"
        self.desc = "This is an empty placeholder script."


class Store(DefaultScript):
    """
    Simple storage script
    """

    def at_script_creation(self):
        """
        Setup the script
        """
        self.key = "sys_storage"
        self.desc = "This is a generic storage container."
