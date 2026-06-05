"""
This module defines `DefaultCharacter` — the base typeclass for player-controlled
characters.  It lives here to keep the per-class surface area manageable; it is
re-exported via `objects.py` for full backward compatibility.

"""

import time
import typing

from django.conf import settings
from django.utils.translation import gettext as _

from evennia.objects.object import DefaultObject
from evennia.utils import create, logger


class DefaultCharacter(DefaultObject):
    """
    This implements an Object puppeted by a Session - that is,
    a character avatar controlled by an account.

    """

    # Tuple of types used for indexing inventory contents. Characters generally wouldn't be in
    # anyone's inventory, but this also governs displays in room contents.
    _content_types = ("character",)
    # lockstring of newly created rooms, for easy overloading.
    # Will be formatted with the appropriate attributes.
    lockstring = (
        "puppet:id({character_id}) or pid({account_id}) or perm(Developer) or pperm(Developer);"
        "delete:id({account_id}) or perm(Admin);"
        "edit:pid({account_id}) or perm(Admin)"
    )

    # Used by get_display_desc when self.db.desc is None
    default_description = _("This is a character.")

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
        character = kwargs.get("character", None)
        cid = f"id({character})" if character else None

        puppet = "puppet:" + " or ".join(
            [x for x in [pid, cid, "perm(Developer)", "pperm(Developer)"] if x]
        )
        delete = "delete:" + " or ".join([x for x in [pid, "perm(Admin)"] if x])
        edit = "edit:" + " or ".join([x for x in [pid, "perm(Admin)"] if x])

        return ";".join([puppet, delete, edit])

    @classmethod
    def create(
        cls,
        key,
        account: "DefaultAccount" = None,
        caller: "DefaultObject" = None,
        method: str = "create",
        **kwargs,
    ):
        """
        Creates a basic Character with default parameters, unless otherwise
        specified or extended.

        Provides a friendlier interface to the utils.create_character() function.

        Args:
            key (str): Name of the new Character.
            account (obj, optional): Account to associate this Character with.
                If unset supplying None-- it will
                change the default lockset and skip creator attribution.

        Keyword Args:
            description (str): Brief description for this object.
            ip (str): IP address of creator (for object auditing).
            All other kwargs will be passed into the create_object call.

        Returns:
            tuple: `(new_character, errors)`. On error, the `new_character` is `None` and
            `errors` is a `list` of error strings (an empty list otherwise).

        """
        errors = []
        obj = None
        # Get IP address of creator, if available
        ip = kwargs.pop("ip", "")

        # If no typeclass supplied, use this class
        kwargs["typeclass"] = kwargs.pop("typeclass", cls)

        # Normalize to latin characters and validate, if necessary, the supplied key
        key = cls.normalize_name(key)

        if val_err := cls.validate_name(key, account=account):
            errors.append(val_err)
            return obj, errors

        # Set the supplied key as the name of the intended object
        kwargs["key"] = key

        # Get permissions
        kwargs["permissions"] = kwargs.get("permissions", settings.PERMISSION_ACCOUNT_DEFAULT)

        # Get description if provided
        description = kwargs.pop("description", "")

        # Get locks if provided
        locks = kwargs.pop("locks", "")

        try:
            # Check to make sure account does not have too many chars
            if account:
                avail = account.check_available_slots()
                if avail:
                    errors.append(avail)
                    return obj, errors

            # Create the Character
            obj = create.create_object(**kwargs)

            # Record creator id and creation IP
            if ip:
                obj.db.creator_ip = ip
            if account:
                obj.db.creator_id = account.id
                account.characters.add(obj)

            # Add locks
            if not locks:
                # Allow only the character itself and the creator account to puppet this character
                # (and Developers).
                locks = cls.get_default_lockstring(account=account, character=obj)

            if locks:
                obj.locks.add(locks)

            # Set description if provided
            if description:
                obj.db.desc = description

        except Exception as e:
            errors.append(f"An error occurred while creating '{key}' object: {e}")
            logger.log_trace()

        return obj, errors

    @classmethod
    def normalize_name(cls, name):
        """
        Normalize the character name prior to creating.

        Args:
            name (str) : The name of the character

        Returns:
            str : A valid, latinized name.

        Notes:

            The main purpose of this is to make sure that character names are not created with
            special unicode characters that look visually identical to latin charaters. This could
            be used to impersonate other characters.

            This method should be refactored to support i18n for non-latin names, but as we
            (currently) have no bug reports requesting better support of non-latin character sets,
            requiring character names to be latinified is an acceptable default option.

        """

        from evennia.utils.utils import latinify

        latin_name = latinify(name, default="X")
        return latin_name

    @classmethod
    def validate_name(cls, name, account=None) -> typing.Optional[str]:
        """
        Validate the character name prior to creating. Overload this function to add custom validators

        Args:
            name (str) : The name of the character
        Keyword Args:
            account (DefaultAccount, optional) : The account creating the character.
        Returns:
            str or None: A non-empty error message if there is a problem, otherwise `None`.

        """
        if account and cls.objects.filter_family(db_key__iexact=name):
            return _("|rA character named '|w{name}|r' already exists.|n").format(name=name)

    def basetype_setup(self):
        """
        Setup character-specific security.

        You should normally not need to overload this, but if you do,
        make sure to reproduce at least the two last commands in this
        method (unless you want to fundamentally change how a
        Character object works).

        """
        super().basetype_setup()
        self.locks.add(
            ";".join(
                [
                    "get:false()",
                    "call:false()",
                    "teleport:perm(Admin)",
                    "teleport_here:perm(Admin)",
                ]
            )  # noone can pick up the character
        )  # no commands can be called on character from outside
        # add the default cmdset
        self.cmdset.add_default(settings.CMDSET_CHARACTER, persistent=True)

    def at_post_move(self, source_location, move_type="move", **kwargs):
        """
        We make sure to look around after a move.

        """
        if self.location.access(self, "view"):
            self.msg(text=(self.at_look(self.location), {"type": "look"}))

    # deprecated
    at_after_move = at_post_move

    def at_pre_puppet(self, account, session=None, **kwargs):
        """
        Return the character from storage in None location in `at_post_unpuppet`.
        Args:
            account (DefaultAccount): This is the connecting account.
            session (Session): Session controlling the connection.

        """
        if self.location is None:
            # Make sure character's location is never None before being puppeted.
            # Return to last location (or home, which should always exist)
            location = self.db.prelogout_location if self.db.prelogout_location else self.home
            if location:
                self.location = location
                self.location.at_post_arrive(self, None)

        if self.location:
            self.db.prelogout_location = self.location  # save location again to be sure.
        else:
            account.msg(
                _("|r{obj} has no location and no home is set.|n").format(obj=self), session=session
            )

    def at_post_puppet(self, **kwargs):
        """
        Called just after puppeting has been completed and all
        Account<->Object links have been established.

        Args:
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call. The engine passes `reattach=True`
                when the call originates from `ServerSession.at_sync`
                on server reload; in that case the per-puppet "you
                become / has entered the game" echo and re-look are
                skipped (the user did not actually leave or re-arrive).
        Notes:

            You can use `self.account` and `self.sessions.get()` to get
            account and sessions at this point; the last entry in the
            list from `self.sessions.get()` is the latest Session
            puppeting this Object.

        """
        if kwargs.get("reattach"):
            return
        # Record on the *driving* account, not self.account (the durable owner):
        # during possession (staff driving an unowned NPC or another's body) the
        # owner may be None or a different account. self.puppeteer is the live
        # driver, resolved from the session already attached at this point.
        driver = self.puppeteer or self.account
        if driver:
            driver.db._last_puppet = self
        self.msg(_("\nYou become |c{name}|n.\n").format(name=self.key))
        self.msg((self.at_look(self.location), {"type": "look"}), options=None)

        def message(obj, from_obj):
            obj.msg(
                _("{name} has entered the game.").format(name=self.get_display_name(obj)),
                from_obj=from_obj,
            )

        self.location.for_contents(message, exclude=[self], from_obj=self)

    def at_post_unpuppet(self, account, session=None, **kwargs):
        """
        We stove away the character when the account goes ooc/logs off,
        otherwise the character object will remain in the room also
        after the account logged off ("headless", so to say).

        Args:
            account (DefaultAccount): The account object that just disconnected
                from this object.
            session (Session): Session controlling the connection that
                just disconnected.
        Keyword Args:
            reason (str): If given, adds a reason for the unpuppet. This
                is set when the user is auto-unpuppeted due to being link-dead.
            **kwargs: Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        if not self.sessions.count():
            # only remove this char from grid if no sessions control it anymore.
            if self.location:

                def message(obj, from_obj):
                    obj.msg(
                        _("{name} has left the game{reason}.").format(
                            name=self.get_display_name(obj),
                            reason=kwargs.get("reason", ""),
                        ),
                        from_obj=from_obj,
                    )

                self.location.for_contents(message, exclude=[self], from_obj=self)
                self.db.prelogout_location = self.location
                self.location = None

    @property
    def idle_time(self):
        """
        Returns the idle time of the least idle session in seconds. If
        no sessions are connected it returns nothing.

        """
        idle = [session.cmd_last_visible for session in self.sessions.all()]
        if idle:
            return time.time() - float(max(idle))
        return None

    @property
    def connection_time(self):
        """
        Returns the maximum connection time of all connected sessions
        in seconds. Returns nothing if there are no sessions.

        """
        conn = [session.conn_time for session in self.sessions.all()]
        if conn:
            return time.time() - float(min(conn))
        return None
