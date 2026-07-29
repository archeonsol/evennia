"""
This module defines `DefaultObject` and the supporting `ObjectSessionHandler`.

Subclasses (`DefaultCharacter`, `DefaultRoom`, `DefaultExit`) live in their
own modules (`character.py`, `room.py`, `exit.py`). Everything is re-exported
from `objects.py` for backward compatibility.

"""

import time
import typing
from collections import defaultdict

import inflect
from django.conf import settings
from django.utils.translation import gettext as _

import evennia
from evennia.commands import cmdset
from evennia.commands.cmdsethandler import CmdSetHandler
from evennia.hooks import hook
from evennia.objects.manager import ObjectManager
from evennia.objects.mixins.appearance import AppearanceMixin
from evennia.objects.mixins.lifecycle import LifecycleMixin
from evennia.objects.mixins.messaging import MessagingMixin
from evennia.objects.mixins.movement import MovementMixin
from evennia.objects.mixins.search import SearchMixin
from evennia.objects.models import ObjectDB
from evennia.scripts.scripthandler import ScriptHandler
from evennia.server.signals import SIGNAL_EXIT_TRAVERSED
from evennia.typeclasses.attributes import NickHandler
from evennia.typeclasses.models import TypeclassBase
from evennia.utils import ansi, create, funcparser, logger, search
from evennia.utils.multimatch import (
    narrow_candidates,
    parse_search_qualifiers,
    resolve_multimatch_index,
    try_autopick,
)
from evennia.utils.utils import (
    class_from_module,
    compress_whitespace,
    dbref,
    is_iter,
    iter_to_str,
    lazy_property,
    make_iter,
    to_str,
    variable_from_module,
)

_INFLECT = inflect.engine()

_ScriptDB = None
_CMDHANDLER = None

_AT_SEARCH_RESULT = variable_from_module(*settings.SEARCH_AT_RESULT.rsplit(".", 1))
_COMMAND_DEFAULT_CLASS = class_from_module(settings.COMMAND_DEFAULT_CLASS)


def _sessid_max():
    # The sessid_max is based on the length of the db_sessid csv field
    # (excluding commas). Multisession modes 1 and 3 allow multiple sessions
    # per object; modes 0 and 2 allow only one.
    return 16 if settings.MULTISESSION_MODE in (1, 3) else 1


# init the actor-stance funcparser for msg_contents
_MSG_CONTENTS_PARSER = funcparser.FuncParser(funcparser.ACTOR_STANCE_CALLABLES)


class ObjectSessionHandler:
    """
    Handles the get/setting of the sessid comma-separated integer field

    """

    def __init__(self, obj):
        """
        Initializes the handler.

        Args:
            obj (DefaultObject): The object on which the handler is defined.

        """
        self.obj = obj
        self._sessid_cache = []
        self._recache()

    def _recache(self):
        self._sessid_cache = list(
            set(int(val) for val in (self.obj.db_sessid or "").split(",") if val)
        )
        if any(sessid for sessid in self._sessid_cache if sessid not in evennia.SESSION_HANDLER):
            # cache is out of sync with sessionhandler! Only retain the ones in the handler.
            self._sessid_cache = [
                sessid for sessid in self._sessid_cache if sessid in evennia.SESSION_HANDLER
            ]
            self.obj.db_sessid = ",".join(str(val) for val in self._sessid_cache)
            self.obj.save(update_fields=["db_sessid"])

    def get(self, sessid=None):
        """
        Get the sessions linked to this Object.

        Args:
            sessid (int, optional): A specific session id.

        Returns:
            list: The sessions connected to this object. If `sessid` is given,
                this is a list of one (or zero) elements.

        Notes:
            Aliased to `self.all()`.

        """

        if sessid:
            sessions = (
                [(evennia.SESSION_HANDLER[sessid] if sessid in evennia.SESSION_HANDLER else None)]
                if sessid in self._sessid_cache
                else []
            )
        else:
            sessions = [
                (evennia.SESSION_HANDLER[ssid] if ssid in evennia.SESSION_HANDLER else None)
                for ssid in self._sessid_cache
            ]
        if None in sessions:
            # this happens only if our cache has gone out of sync with the SessionHandler.
            self._recache()
            return self.get(sessid=sessid)
        return sessions

    def all(self):
        """
        Alias to get(), returning all sessions.

        Returns:
            list: All sessions.

        """
        return self.get()

    def add(self, session):
        """
        Add session to handler.

        Args:
            session (Session or int): Session or session id to add.

        Notes:
            We will only add a session/sessid if this actually also exists
            in the the core sessionhandler.

        """
        try:
            sessid = session.sessid
        except AttributeError:
            sessid = session

        sessid_cache = self._sessid_cache
        if sessid in evennia.SESSION_HANDLER and sessid not in sessid_cache:
            if len(sessid_cache) >= _sessid_max():
                return
            sessid_cache.append(sessid)
            self.obj.db_sessid = ",".join(str(val) for val in sessid_cache)
            self.obj.save(update_fields=["db_sessid"])

    def remove(self, session):
        """
        Remove session from handler.

        Args:
            Session or int: Session or session id to remove.

        """
        try:
            sessid = session.sessid
        except AttributeError:
            sessid = session

        sessid_cache = self._sessid_cache
        if sessid in sessid_cache:
            sessid_cache.remove(sessid)
            self.obj.db_sessid = ",".join(str(val) for val in sessid_cache)
            self.obj.save(update_fields=["db_sessid"])

    def clear(self):
        """
        Clear all handled sessids.

        """
        self._sessid_cache = []
        self.obj.db_sessid = None
        self.obj.save(update_fields=["db_sessid"])

    def count(self):
        """
        Get amount of sessions connected.

        Returns:
            int: Number of sessions handled.

        """
        return len(self._sessid_cache)


#
# Base class to inherit from.


class DefaultObject(
    MessagingMixin,
    SearchMixin,
    MovementMixin,
    AppearanceMixin,
    LifecycleMixin,
    ObjectDB,
    metaclass=TypeclassBase,
):
    """
    This is the root Object typeclass, representing all entities that
    have an actual presence in-game. DefaultObjects generally have a
    location. They can also be manipulated and looked at. Game
    entities you define should inherit from DefaultObject at some distance.

    It is recommended to create children of this class using the
    `evennia.create_object()` function rather than to initialize the class
    directly - this will both set things up and efficiently save the object
    without `obj.save()` having to be called explicitly.

    Note: Check the autodocs for complete class members, this may not always
    be up-to date.

    * Base properties defined/available on all Objects

     key (string) - name of object
     name (string)- same as key
     dbref (int, read-only) - unique #id-number. Also "id" can be used.
     date_created (string) - time stamp of object creation

     account (Account) - controlling account (if any, only set together with
                       sessid below)
     sessid (int, read-only) - session id (if any, only set together with
                       account above). Use `sessions` handler to get the
                       Sessions directly.
     location (Object) - current location. Is None if this is a room
     home (Object) - safety start-location
     is_puppeted (bool, read-only) - True if a live session is currently driving
                            this body (someone is playing it right now)
     contents (list, read only) - returns all objects inside this object
     exits (list of Objects, read-only) - returns all exits from this
                       object, if any
     destination (Object) - only set if this object is an exit.
     is_superuser (bool, read-only) - True/False if this user is a superuser
     is_connected (bool, read-only) - True if this object is associated with
                            an Account with any connected sessions.
     has_account (bool, read-only) - True if this object has an associated account
                        (durable ownership, online or offline).
     is_superuser (bool, read-only): True if this object has an account and that
                        account is a superuser.
     plural_category (string) - Alias category for the plural strings of this object

    * Handlers available

     aliases - alias-handler: use aliases.add/remove/get() to use.
     permissions - permission-handler: use permissions.add/remove() to
                   add/remove new perms.
     locks - lock-handler: use locks.add() to add new lock strings
     scripts - script-handler. Add new scripts to object with scripts.add()
     cmdset - cmdset-handler. Use cmdset.add() to add new cmdsets to object
     nicks - nick-handler. New nicks with nicks.add().
     sessions - sessions-handler. Get Sessions connected to this
                object with sessions.get()
     attributes - attribute-handler. Use attributes.add/remove/get.
     db - attribute-handler: Shortcut for attribute-handler. Store/retrieve
            database attributes using self.db.myattr=val, val=self.db.myattr
     ndb - non-persistent attribute handler: same as db but does not create
            a database entry when storing data

    * Helper methods (see src.objects.objects.py for full headers)

     get_search_query_replacement(searchdata, **kwargs)
     get_search_direct_match(searchdata, **kwargs)
     get_search_candidates(searchdata, **kwargs)
     get_search_result(searchdata, attribute_name=None, typeclass=None,
                       candidates=None, exact=False, use_dbref=None, tags=None, **kwargs)
     get_stacked_result(results, **kwargs)
     search_for(searchdata, global_search=False, use_nicks=True, typeclass=None,
                location=None, attribute_name=None, exact=False, candidates=None,
                use_locks=True, use_dbref=None, tags=None, stacked=0)
     search(searchdata, global_search=False, use_nicks=True, typeclass=None,
            location=None, attribute_name=None, exact=False, candidates=None,
            use_locks=True, not_found=None, ambiguous=None, use_dbref=None,
            tags=None, stacked=0)
     search_account(searchdata, quiet=False)
     execute_cmd(raw_string, session=None, **kwargs))
     msg(text=None, from_obj=None, session=None, options=None, **kwargs)
     for_contents(func, exclude=None, **kwargs)
     msg_contents(message, exclude=None, from_obj=None, mapping=None,
                  raise_funcparse_errors=False, **kwargs)
     move_to(destination, quiet=False, emit_to_obj=None, use_destination=True)
     clear_contents()
     create(key, account, caller, method, **kwargs)
     copy(new_key=None)
     at_object_post_copy(new_obj, **kwargs)
     delete()
     is_typeclass(typeclass, exact=False)
     swap_typeclass(new_typeclass, clean_attributes=False, no_default=True)
     access(accessing_obj, access_type='read', default=False,
            no_superuser_bypass=False, **kwargs)
     filter_visible(obj_list, looker, **kwargs)
     get_default_lockstring()
     get_cmdsets(caller, current, **kwargs)
     has_capability(capability)
     get_cmdset_providers()
     get_display_name(looker=None, **kwargs)
     get_extra_display_name_info(looker=None, **kwargs)
     get_numbered_name(count, looker, **kwargs)
     get_display_header(looker, **kwargs)
     get_display_desc(looker, **kwargs)
     get_display_exits(looker, **kwargs)
     get_display_characters(looker, **kwargs)
     get_display_things(looker, **kwargs)
     get_display_footer(looker, **kwargs)
     format_appearance(appearance, looker, **kwargs)
     return_apperance(looker, **kwargs)

    * Hooks (these are class methods, so args should start with self):

     basetype_setup()     - only called once, used for behind-the-scenes
                            setup. Normally not modified.
     basetype_posthook_setup() - customization in basetype, after the object
                            has been created; Normally not modified.

     at_object_creation() - only called once, when object is first created.
                            Object customizations go here.
     at_object_post_creation() - only called once, when object is first created.
                            Additional setup involving e.g. prototype-set attributes can go here.
     at_pre_delete() - called just before deleting an object. If returning
                            False, deletion is aborted. Note that all objects
                            inside a deleted object are automatically moved
                            to their <home>, they don't need to be removed here.
     at_prototype_spawn() - called when object is spawned from a prototype or updated
                            by the spawner to apply prototype changes.
     at_post_load()            - called whenever typeclass is cached from memory,
                            at least once every server restart/reload
     at_first_save()
     at_cmdset_get(**kwargs) - this is called just before the command handler
                            requests a cmdset from this object. The kwargs are
                            not normally used unless the cmdset is created
                            dynamically (see e.g. Exits).
     at_pre_puppet(account)- (account-controlled objects only) called just
                            before puppeting
     at_post_puppet()     - (account-controlled objects only) called just
                            after completing connection account<->object
     at_pre_unpuppet()    - (account-controlled objects only) called just
                            before un-puppeting
     at_post_unpuppet(account) - (account-controlled objects only) called just
                            after disconnecting account<->object link
     at_server_reload()   - called before server is reloaded
     at_server_shutdown() - called just before server is fully shut down

     at_post_access(result, accessing_obj, access_type) - called with the result
                            of a lock access check on this object. Return value
                            does not affect check result.

     at_pre_move(destination)             - called just before moving object
                        to the destination. If returns False, move is cancelled.
     announce_move_from(destination)         - called in old location, just
                        before move, if obj.move_to() has quiet=False
     announce_move_to(source_location)       - called in new location, just
                        after move, if obj.move_to() has quiet=False
     at_post_move(source_location)          - always called after a move has
                        been successfully performed.
     at_pre_leave(leaving_object, destination, **kwargs) - source room veto and
                       pre-move side effects; return True to allow the move.
     at_pre_arrive(arriving_object, source_location, **kwargs) - destination room
                       veto and pre-move side effects.
     at_post_leave(moved_obj, target_location, move_type="move", **kwargs) - source
                       room notification after the object has left.
     at_post_arrive(moved_obj, source_location, move_type="move", **kwargs) -
                       destination room notification after the object has arrived.

     do_traverse(traversing_object, target_location, **kwargs) - (exit-objects only)
                              handles all moving across the exit, including
                              calling the other exit hooks. Use super() to retain
                              the default functionality.
     at_post_traverse(traversing_object, source_location) - (exit-objects only)
                              called just after a traversal has happened.
     at_failed_traverse(traversing_object)      - (exit-objects only) called if
                       traversal fails and property err_traverse is not defined.

     at_msg_receive(self, msg, from_obj=None, **kwargs) - called when a message
                             (via self.msg()) is sent to this obj.
                             If returns false, aborts send.
     at_msg_send(self, msg, to_obj=None, **kwargs) - called when this objects
                             sends a message to someone via self.msg().

     return_appearance(looker) - describes this object. Used by "look"
                                 command by default
     at_desc(looker=None)      - called by 'look' whenever the
                                 appearance is requested.
     at_pre_get(getter, **kwargs)
     at_post_get(getter)            - called after object has been picked up.
                                 Does not stop pickup.
     at_pre_give(giver, getter, **kwargs)
     at_post_give(giver, getter, **kwargs)
     at_pre_drop(dropper, **kwargs)
     at_post_drop(dropper, **kwargs)          - called when this object has been dropped.
     at_pre_say(speaker, message, **kwargs)
     at_say(message, msg_self=None, msg_location=None, receivers=None, msg_receivers=None, **kwargs)

     at_look(target, **kwargs)
     at_desc(looker=None)
     at_post_rename(oldname, newname)


    """

    # Determines which order command sets begin to be assembled from.
    # Objects are usually third.
    cmdset_provider_order = 100
    cmdset_provider_error_order = 100
    cmdset_provider_type = "object"

    # Used for sorting / filtering in inventories / room contents.
    _content_types = ("object",)

    objects = ObjectManager()

    # Used by get_display_desc when self.db.desc is None
    default_description = _("You see nothing special.")

    # populated by `return_appearance`
    appearance_template = """
{header}
|c{name}{extra_name_info}|n{extra_state}
{desc}
{exits}
{characters}
{things}
{footer}
    """

    plural_category = "plural_key"
    default_placement = "portable"

    @property
    def placement(self):
        """Return this object's canonical physical-placement category.

        Typeclasses may set ``default_placement``. An explicit ``db.placement``
        of ``"portable"``, ``"fixture"``, or ``"anchored"`` overrides that
        class default.

        Returns:
            str: ``"portable"``, ``"fixture"``, or ``"anchored"``.
        """
        explicit = getattr(self.db, "placement", None)
        if explicit in ("portable", "fixture", "anchored"):
            return explicit
        if self.default_placement in ("fixture", "anchored"):
            return self.default_placement
        return "portable"

    @property
    def is_fixture(self):
        """Return whether this object is installed rather than portable."""
        return self.placement == "fixture"

    @property
    def is_fixed(self):
        """Return whether ordinary containment moves must be denied."""
        return self.placement in ("fixture", "anchored")

    # on-object properties

    @lazy_property
    def cmdset(self):
        """CmdSetHandler"""
        return CmdSetHandler(self, True)

    @lazy_property
    def scripts(self):
        """ScriptHandler"""
        return ScriptHandler(self)

    @lazy_property
    def nicks(self):
        """NickHandler"""
        return NickHandler(self, class_from_module(settings.ATTRIBUTE_BACKEND_CLASS))

    @lazy_property
    def sessions(self):
        """SessionHandler"""
        return ObjectSessionHandler(self)

    @property
    def is_connected(self):
        """True if this object is associated with an Account with any connected sessions."""
        if self.account:  # seems sane to pass on the account
            return self.account.is_connected
        else:
            return False

    @property
    def has_account(self):
        """True if this object has an associated account (online or offline).

        This is *durable ownership*, not live control. For "is someone playing
        this body right now" use :attr:`is_puppeted`; for "who is driving it"
        (whose permissions apply) use :attr:`puppeteer`.
        """
        return bool(self.account)

    @property
    def is_puppeted(self):
        """True if at least one live session is currently driving this object
        (someone is actively playing it right now).

        Distinct from :attr:`has_account` (durable ownership, online or offline)
        and :attr:`is_connected` (the owning account has a session connected
        somewhere, not necessarily on this body).
        """
        return bool(self.sessions.count())

    @property
    def puppeteer(self):
        """The account *currently driving* this body (its live controller), or
        ``None`` if nothing is driving it.

        This is the account whose permissions apply when this body acts, and is
        deliberately distinct from :attr:`account` (the durable *owner*): when
        staff possess an NPC or another player's body, the driver is the staff
        account while ownership is unchanged. Resolved from the live sessions,
        so it is never the stale owner.

        All sessions on a body belong to one account (``puppet_object`` blocks a
        connected foreign account from co-driving), so the ``[0]`` pick is
        unambiguous in normal play; the only window for a foreign session is a
        not-yet-reaped straggler from an unclean session kill.
        """
        sessions = self.sessions.all()
        return sessions[0].account if sessions else None

    @hook(
        event="cmdset",
        phase="composite",
        actor="self",
        returns="content",
        discipline="internal",
        fires_from=(),
        notes="Duck-typed by cmdhandler. Returns dict[str, CmdSetProvider]. See command-system.md.",
    )
    def get_cmdset_providers(self) -> dict[str, "CmdSetProvider"]:
        """
        Overrideable method which returns a dictionary of every kind of object which
        has a cmdsethandler linked to this Object, and should participate in cmdset
        merging.

        Objects might be aware of an Account. Otherwise, just themselves, by default.

        Returns:
            dict[str, CmdSetProvider]: The CmdSetProviders linked to this Object.
        """
        out = {"object": self}
        # The acting account's cmdsets come from the live driver (puppeteer),
        # not the durable owner: when a body is possessed, the driver's commands
        # apply, not the absent owner's. (The driving session also supplies this
        # provider, so for a player on their own character the two agree.)
        driver = self.puppeteer
        if driver:
            out["account"] = driver
        return out

    @property
    def is_superuser(self):
        """True if a superuser account is *currently driving* this object.

        Follows the live driver (:attr:`puppeteer`), not the durable owner, so a
        superuser owner's bypass never leaks to a different account driving the
        body (and an idle, undriven body is never superuser).
        """
        driver = self.puppeteer
        return bool(driver and driver.is_superuser and not driver.attributes.get("_quell"))

    def contents_get(self, exclude=None, content_type=None):
        """
        Returns the contents of this object, i.e. all
        objects that has this object set as its location.
        This should be publically available.

        Args:
            exclude (DefaultObject): Object to exclude from returned
                contents list
            content_type (str): A content_type to filter by. None for no
                filtering.

        Returns:
            list: List of contents of this Object.

        Notes:
            Also available as the `.contents` property, but that doesn't allow for exclusion and
            filtering on content-types.

        """
        return self.contents_cache.get(exclude=exclude, content_type=content_type)

    def contents_set(self, *args):
        "Makes sure `.contents` is read-only. Raises `AttributeError` if trying to set it."
        raise AttributeError(
            "{}.contents is read-only. Use obj.move_to or "
            "obj.location to move an object here.".format(self.__class__)
        )

    contents = property(contents_get, contents_set, contents_set)

    @property
    def exits(self):
        """
        Returns all exits from this object, i.e. all objects at this
        location having the property .destination != `None`.

        """
        return [exi for exi in self.contents if exi.destination]

    from evennia.authorization.policy import Always, PredicateRequirement, RequiresCapability

    authorization_policies = {
        "view": Always(),
        "search": Always(),
        "get": Always(),
        "drop": Always(),
        "call": Always(),
        "craft": Always(),
        "puppet": PredicateRequirement("principal.controls_resource"),
        "attrread": Always(),
        "attrcreate": RequiresCapability("engine.object.edit"),
        "attredit": RequiresCapability("engine.object.edit"),
        "control": RequiresCapability("engine.object.control"),
        "edit": RequiresCapability("engine.object.edit"),
        "delete": RequiresCapability("engine.object.delete"),
        "move": RequiresCapability("engine.object.move"),
        "examine": RequiresCapability("engine.object.examine"),
        "teleport": RequiresCapability("engine.object.teleport"),
        "teleport_here": RequiresCapability("engine.object.teleport_here"),
        "tell": RequiresCapability("engine.object.tell"),
    }
