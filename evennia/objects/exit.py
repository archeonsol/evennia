"""
This module defines `ExitCommand` and `DefaultExit` — the base typeclass for
in-game exits.  Re-exported via `objects.py` for full backward compatibility.

"""

import typing

from django.utils.translation import gettext as _

from evennia.commands import cmdset
from evennia.objects.models import ObjectDB
from evennia.objects.object import _COMMAND_DEFAULT_CLASS, DefaultObject
from evennia.server.signals import SIGNAL_EXIT_TRAVERSED
from evennia.utils import create, logger
from evennia.utils.utils import is_veto


class ExitCommand(_COMMAND_DEFAULT_CLASS):
    """
    This is a command that simply cause the caller to traverse
    the object it is attached to.

    """

    obj = None

    def func(self):
        """
        Default exit traverse if no syscommand is defined.
        """

        if self.obj.access(self.caller, "traverse"):
            # we may traverse the exit.
            self.obj.do_traverse(self.caller, self.obj.destination)
            SIGNAL_EXIT_TRAVERSED.send(sender=self.obj, traverser=self.caller)
        else:
            # exit is locked
            if self.obj.db.err_traverse:
                # if exit has a better error message, let's use it.
                self.caller.msg(self.obj.db.err_traverse)
            else:
                # No shorthand error message. Call hook.
                self.obj.at_failed_traverse(self.caller)

    def get_display_name(self, looker=None, **kwargs):
        return self.obj.get_display_name(looker, **kwargs)

    def get_extra_info(self, caller, **kwargs):
        """
        Shows a bit of information on where the exit leads.

        Args:
            caller (DefaultObject): The object (usually a character) that entered an ambiguous command.
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        Returns:
            str: A string with identifying information to disambiguate the command, conventionally
            with a preceding space.

        """
        if self.obj.destination:
            return _(" (exit to {destination})").format(
                destination=self.obj.destination.get_display_name(caller, **kwargs)
            )
        else:
            return _(" (exit)")


class DefaultExit(DefaultObject):
    """
    This is the base exit object - it connects a location to another.
    This is done by the exit assigning a "command" on itself with the
    same name as the exit object (to do this we need to remember to
    re-create the command when the object is cached since it must be
    created dynamically depending on what the exit is called). This
    command (which has a high priority) will thus allow us to traverse
    exits simply by giving the exit-object's name on its own.

    """

    _content_types = ("exit",)
    exit_command = ExitCommand
    priority = 101

    # Used by get_display_desc when self.db.desc is None
    default_description = _("This is an exit.")

    # Helper classes and methods to implement the Exit. These need not
    # be overloaded unless one want to change the foundation for how
    # Exits work. See the end of the class for hook methods to overload.

    def create_exit_cmdset(self, exidbobj):
        """
        Helper function for creating an exit command set + command.

        The command of this cmdset has the same name as the Exit
        object and allows the exit to react when the account enter the
        exit's name, triggering the movement between rooms.

        Args:
            exidbobj (DefaultObject): The DefaultExit object to base the command on.

        """

        # create an exit command. We give the properties here,
        # to always trigger metaclass preparations
        cmd = self.exit_command(
            key=exidbobj.db_key.strip().lower(),
            aliases=exidbobj.aliases.all(),
            locks=str(exidbobj.locks),
            auto_help=False,
            destination=exidbobj.db_destination,
            arg_regex=r"^$",
            is_exit=True,
            obj=exidbobj,
        )
        # create a cmdset
        exit_cmdset = cmdset.CmdSet(None)
        exit_cmdset.key = "ExitCmdSet"
        exit_cmdset.priority = self.priority
        exit_cmdset.duplicates = True
        # add command to cmdset
        exit_cmdset.add(cmd)
        return exit_cmdset

    # Command hooks

    @classmethod
    def create(
        cls,
        key: str,
        location: "DefaultRoom" = None,
        destination: "DefaultRoom" = None,
        account: "DefaultAccount" = None,
        caller: DefaultObject = None,
        method: str = "create",
        **kwargs,
    ) -> tuple[typing.Optional["DefaultExit"], list[str]]:
        """
        Creates a basic Exit with default parameters, unless otherwise
        specified or extended.

        Provides a friendlier interface to the utils.create_object() function.

        Args:
            key (str): Name of the new Exit, as it should appear from the
                source room.
            location (Room): The room to create this exit in.

        Keyword Args:
            account (DefaultAccountDB): Account to associate this Exit with.
            caller (DefaultObject): The Object creating this Object.
            description (str): Brief description for this object.
            ip (str): IP address of creator (for object auditing).
            destination (Room): The room to which this exit should go.

        Returns:
            tuple: A tuple `(Object, errors)`, where the object is the newly
            created exit of the given typeclass, or `None` if there was an error.
            If there was an error, `errors` will be a list of error strings.

        """
        errors = []
        obj = None

        # Get IP address of creator, if available
        ip = kwargs.pop("ip", "")

        # If no typeclass supplied, use this class
        kwargs["typeclass"] = kwargs.pop("typeclass", cls)

        # Set the supplied key as the name of the intended object
        kwargs["key"] = key

        # Get who to send errors to
        kwargs["report_to"] = kwargs.pop("report_to", account)

        # Set to/from rooms
        kwargs["location"] = location
        kwargs["destination"] = destination

        description = kwargs.pop("description", "")

        locks = kwargs.get("locks", "")

        try:
            # Create the Exit
            obj = create.create_object(**kwargs)

            # Set appropriate locks
            if not locks:
                locks = cls.get_default_lockstring(account=account, caller=caller, exit=obj)
            if locks:
                obj.locks.add(locks)

            # Record creator id and creation IP
            if ip:
                obj.db.creator_ip = ip
            if account:
                obj.db.creator_id = account.id

            # Set description if provided
            if description:
                obj.db.desc = description

        except Exception as e:
            errors.append(f"An error occurred while creating this '{key}' object: {e}")
            logger.log_err(e)

        return obj, errors

    def basetype_setup(self):
        """
        Setup exit-security.

        You should normally not need to overload this - if you do make
        sure you include all the functionality in this method.

        """
        super().basetype_setup()

        # setting default locks (overload these in at_object_creation()
        self.locks.add(
            ";".join(
                [
                    "puppet:false()",  # would be weird to puppet an exit ...
                    "traverse:all()",  # who can pass through exit by default
                    "get:false()",  # noone can pick up the exit
                    "teleport:false()",
                    "teleport_here:false()",
                ]
            )
        )

        # an exit should have a destination - try to make sure it does
        if self.location and not self.destination:
            self.destination = self.location

    def at_cmdset_get(self, **kwargs):
        """
        Called just before cmdsets on this object are requested by the
        command handler. If changes need to be done on the fly to the
        cmdset before passing them on to the cmdhandler, this is the
        place to do it. This is called also if the object currently
        has no cmdsets.

        Keyword Args:
            caller (DefaultObject, DefaultAccount or Session): The object requesting the cmdsets.
            current (CmdSet): The current merged cmdset.
            force_init (bool): If `True`, force a re-build of the cmdset
                (for example to update aliases).

        """

        if "force_init" in kwargs or not self.cmdset.has_cmdset("ExitCmdSet", must_be_default=True):
            # we are resetting, or no exit-cmdset was set. Create one dynamically.
            self.cmdset.add_default(self.create_exit_cmdset(self), persistent=False)

    def at_post_load(self):
        """
        This is called when this objects is re-loaded from cache. When
        that happens, we make sure to remove any old ExitCmdSet cmdset
        (this most commonly occurs when renaming an existing exit)

        """
        self.cmdset.remove_default()

    def do_traverse(self, traversing_object, target_location, **kwargs):
        """
        This implements the actual traversal. The traverse lock has
        already been checked (in the Exit command) at this point.

        Calls `at_pre_traverse` first; if that returns False, the traverse
        is aborted and `at_failed_traverse` is fired. On success the move
        is performed and `at_post_traverse` is fired. This is normally the
        method to override on Exit subclasses; for hook-style side effects
        prefer `at_pre_traverse`, `at_post_traverse`, or `at_failed_traverse`.

        Args:
            traversing_object (DefaultObject): Object traversing us.
            target_location (DefaultObject): Where target is going.
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        if is_veto(self.at_pre_traverse(traversing_object, target_location, **kwargs)):
            self.at_failed_traverse(traversing_object)
            return
        source_location = traversing_object.location
        if traversing_object.move_to(target_location, move_type="traverse", exit_obj=self):
            self.at_post_traverse(traversing_object, source_location)
        else:
            if self.db.err_traverse:
                # if exit has a better error message, let's use it.
                traversing_object.msg(self.db.err_traverse)
            else:
                # No shorthand error message. Call hook.
                self.at_failed_traverse(traversing_object)

    def at_failed_traverse(self, traversing_object, **kwargs):
        """
        Overloads the default hook to implement a simple default error message.

        Args:
            traversing_object (DefaultObject): The object that failed traversing us.
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        Notes:
            Using the default exits, this hook will not be called if an
            Attribute `err_traverse` is defined - this will in that case be
            read for an error string instead.

        """
        traversing_object.msg(_("You cannot go there."))

    def get_return_exit(self, return_all=False):
        """
        Get the exits that pair with this one in its destination room
        (i.e. returns to its location)

        Args:
            return_all (bool): Whether to return available results as a
                               queryset or single matching exit.

        Returns:
            Exit or queryset: The matching exit(s). If `return_all` is `True`, this
            will be a queryset of all matching exits. Otherwise, it will be the first Exit matched.

        """
        query = ObjectDB.objects.filter(db_location=self.destination, db_destination=self.location)
        if return_all:
            return query
        return query.first()
