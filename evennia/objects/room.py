"""
This module defines `DefaultRoom` — the base typeclass for in-game rooms.
Re-exported via `objects.py` for full backward compatibility.

"""

from django.utils.translation import gettext as _

from evennia.authorization.policy import Always, Never
from evennia.objects.object import DefaultObject
from evennia.utils import create, logger


class DefaultRoom(DefaultObject):
    """
    This is the base room object. It's just like any Object except its
    location is always `None`.
    """

    # A tuple of strings used for indexing this object inside an inventory.
    # Generally, a room isn't expected to HAVE a location, but maybe in some games?
    _content_types = ("room",)
    authorization_policies = {
        **DefaultObject.authorization_policies,
        "get": Never(),
        "puppet": Never(),
        "teleport": Never(),
        "teleport_here": Always(),
    }

    # Used by get_display_desc when self.db.desc is None
    default_description = _("This is a room.")

    @classmethod
    def create(
        cls,
        key: str,
        account: "DefaultAccount" = None,
        caller: DefaultObject = None,
        method: str = "create",
        **kwargs,
    ):
        """
        Creates a basic Room with default parameters, unless otherwise
        specified or extended.

        Provides a friendlier interface to the utils.create_object() function.

        Args:
            key (str): Name of the new Room.

        Keyword Args:
            account (DefaultAccount, optional): Account to associate this Room with. If
                given, it will be given specific control/edit permissions to this
                object (along with normal Admin perms). If not given, default
            caller (DefaultObject): The object which is creating this one.
            description (str): Brief description for this object.
            ip (str): IP address of creator (for object auditing).
            method (str): The method used to create the room. Defaults to "create".

        Returns:
            tuple: A tuple `(Object, error)` with the newly created Room of the given typeclass,
            or `None` if there was an error. If there was an error, `error` will be a list of
            error strings.

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

        # Get description, if provided
        description = kwargs.pop("description", "")

        try:
            # Create the Room
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

    def basetype_setup(self):
        """
        Simple room setup setting locks to make sure the room cannot be picked up.

        """

        super().basetype_setup()
        self.location = None
