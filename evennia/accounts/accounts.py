"""
Typeclass for Account objects.

Note that this object is primarily intended to
store OOC information, not game info! This
object represents the actual user (not their
character) and has NO actual presence in the
game world (this is handled by the associated
character object, so you should customize that
instead for most things).

"""

import re
import time
import typing
from random import getrandbits

from django.conf import settings
from django.contrib.auth import authenticate, password_validation
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.utils import timezone
from django.utils.module_loading import import_string
from django.utils.translation import gettext as _

import evennia
from evennia.accounts.manager import AccountManager
from evennia.accounts.models import AccountDB
from evennia.commands.cmdsethandler import CmdSetHandler
from evennia.comms.models import ChannelDB
from evennia.hooks import hook
from evennia.objects.models import ObjectDB
from evennia.scripts.scripthandler import ScriptHandler
from evennia.server.models import ServerConfig
from evennia.server.signals import (
    SIGNAL_ACCOUNT_POST_CREATE,
    SIGNAL_ACCOUNT_POST_LOGIN_FAIL,
    SIGNAL_OBJECT_POST_PUPPET,
    SIGNAL_OBJECT_POST_UNPUPPET,
)
from evennia.server.throttle import Throttle
from evennia.typeclasses.attributes import NickHandler
from evennia.typeclasses.models import TypeclassBase
from evennia.utils import class_from_module, create, logger
from evennia.utils.optionhandler import OptionHandler
from evennia.utils.utils import (
    is_iter,
    is_veto,
    lazy_property,
    make_iter,
    to_str,
    variable_from_module,
)

__all__ = ("DefaultAccount", "DefaultGuest")

_AT_SEARCH_RESULT = variable_from_module(*settings.SEARCH_AT_RESULT.rsplit(".", 1))
_MUDINFO_CHANNEL = None
_CONNECT_CHANNEL = None
_CMDHANDLER = None


# Create throttles for too many account-creations and login attempts
CREATION_THROTTLE = Throttle(
    name="creation",
    limit=settings.CREATION_THROTTLE_LIMIT,
    timeout=settings.CREATION_THROTTLE_TIMEOUT,
)
LOGIN_THROTTLE = Throttle(
    name="login", limit=settings.LOGIN_THROTTLE_LIMIT, timeout=settings.LOGIN_THROTTLE_TIMEOUT
)


def _sync_session_bid_to_portal(session):
    """Push the session's current ``bid`` to the Portal for PSYNC/@reload survival."""
    if not session or getattr(settings, "TEST_ENVIRONMENT", False):
        return
    try:
        evennia.SESSION_HANDLER.session_portal_sync(session)
    except Exception:
        logger.log_trace("Failed to sync session bid to portal")


class AccountSessionHandler(object):
    """
    Manages the session(s) attached to an account.

    """

    def __init__(self, account):
        """
        Initializes the handler.

        Args:
            account (Account): The Account on which this handler is defined.

        """
        self.account = account

    def get(self, sessid=None):
        """
        Get the sessions linked to this object.

        Args:
            sessid (int, optional): Specify a given session by
                session id.

        Returns:
            sessions (list): A list of Session objects. If `sessid`
                is given, this is a list with one (or zero) elements.

        """
        if sessid:
            return make_iter(evennia.SESSION_HANDLER.session_from_account(self.account, sessid))
        else:
            return evennia.SESSION_HANDLER.sessions_from_account(self.account)

    def all(self):
        """
        Alias to get(), returning all sessions.

        Returns:
            sessions (list): All sessions.

        """
        return self.get()

    def count(self):
        """
        Get amount of sessions connected.

        Returns:
            sesslen (int): Number of sessions handled.

        """
        return len(self.get())


class CharactersHandler:
    """
    Handler living on DefaultAccount as ``.characters`` via @lazy_property.

    The playable set is *ownership*: the characters whose durable owner
    (``ObjectDB.db_account``) is this account. Ownership is the single source
    of truth, decoupled from the :class:`~evennia.accounts.models.ControlBinding`
    control graph (which tracks who is *driving* a body right now, not who owns
    it). A control graph is minted on first puppet, not by roster membership.
    """

    def __init__(self, owner: "DefaultAccount"):
        """
        Create the CharactersHandler.

        Args:
            owner: The Account that owns this handler.
        """
        self.owner = owner

    def _query(self):
        from evennia.objects.models import ObjectDB

        return ObjectDB.objects.filter(db_account=self.owner)

    def add(self, character: "DefaultCharacter", transfer: bool = False):
        """
        Make ``character`` a playable IC self of this account (idempotent).

        Ownership is a single field (``ObjectDB.db_account``); this is the only
        write. Refuses a character already owned by a *different* account unless
        ``transfer=True`` (staff reassignment / body-swap), so adding never
        silently steals another account's character.

        Args:
            character (DefaultCharacter): The character to add.
            transfer (bool): Allow taking a character owned by another account.
                Defaults to False.

        Raises:
            ValueError: If ``character`` is owned by another account and
                ``transfer`` is False.
        """
        current_owner_id = getattr(character, "db_account_id", None)
        if current_owner_id not in (None, self.owner.id) and not transfer:
            raise ValueError(
                f"{character} is owned by account #{current_owner_id}; "
                "pass transfer=True to reassign ownership."
            )
        already = current_owner_id == self.owner.id
        if not already:
            character.db_account = self.owner
            character.save(update_fields=["db_account"])
            self.owner.at_character_added(character)

    def remove(self, character: "DefaultCharacter"):
        """
        Drop ``character`` from this account's playable set (disown it).

        Clears the durable owner and tears down any control graph anchored on
        it, so ownership and the control graph cannot diverge.

        Args:
            character (DefaultCharacter): The character to remove.
        """
        from evennia.accounts.models import ControlBinding

        if getattr(character, "db_account_id", None) != self.owner.id:
            return
        # Detach any live session first, so disowning never leaves a session
        # half-attached to a body whose binding is about to be deleted (mirrors
        # the order LifecycleMixin.delete() uses). Each session is released
        # through its own driving account.
        for session in list(character.sessions.all()):
            (session.account or self.owner).unpuppet_object(session)
        character.db_account = None
        character.save(update_fields=["db_account"])
        ControlBinding.objects.filter(db_identity=character).delete()
        self.owner.at_character_removed(character)

    def all(self) -> list["DefaultCharacter"]:
        """
        Get all playable characters.

        Returns:
            list[DefaultCharacter]: All playable characters.
        """
        return list(self._query())

    def count(self) -> int:
        """
        Get the number of playable characters.

        Returns:
            int: The number of playable characters.
        """
        return self._query().count()

    __len__ = count

    def __iter__(self):
        return iter(self.all())


class DefaultAccount(AccountDB, metaclass=TypeclassBase):
    """
    This is the base Typeclass for all Accounts. Accounts represent
    the person playing the game and tracks account info, password
    etc. They are OOC entities without presence in-game. An Account
    can connect to a Character Object in order to "enter" the
    game.

    Account Typeclass API:

    * Available properties (only available on initiated typeclass objects)

     - key (string) - name of account
     - name (string)- wrapper for user.username
     - aliases (list of strings) - aliases to the object. Will be saved to
            database as AliasDB entries but returned as strings.
     - dbref (int, read-only) - unique #id-number. Also "id" can be used.
     - date_created (string) - time stamp of object creation
     - permissions (list of strings) - list of permission strings
     - user (User, read-only) - django User authorization object
     - obj (Object) - game object controlled by account. 'character' can also
                     be used.
     - is_superuser (bool, read-only) - if the connected user is a superuser

    * Handlers

     - locks - lock-handler: use locks.add() to add new lock strings
     - db - attribute-handler: store/retrieve database attributes on this
                              self.db.myattr=val, val=self.db.myattr
     - ndb - non-persistent attribute handler: same as db but does not
                                  create a database entry when storing data
     - scripts - script-handler. Add new scripts to object with scripts.add()
     - cmdset - cmdset-handler. Use cmdset.add() to add new cmdsets to object
     - nicks - nick-handler. New nicks with nicks.add().
     - sessions - session-handler. Use session.get() to see all sessions connected, if any
     - options - option-handler. Defaults are taken from settings.OPTIONS_ACCOUNT_DEFAULT
     - characters - handler for listing the account's playable characters

    * Helper methods (check autodocs for full updated listing)

     - msg(text=None, from_obj=None, session=None, options=None, **kwargs)
     - execute_cmd(raw_string)
     - search(searchdata, return_puppet=False, search_object=False, typeclass=None,
                      not_found=None, ambiguous=None, use_nicks=True, **kwargs)
     - search_for(searchdata, search_object=False, typeclass=None, use_nicks=True, **kwargs)
     - is_typeclass(typeclass, exact=False)
     - swap_typeclass(new_typeclass, clean_attributes=False, no_default=True)
     - access(accessing_obj, access_type='read', default=False, no_superuser_bypass=False, **kwargs)
     - check_permstring(permstring)
     - get_cmdsets(caller, current, **kwargs)
     - get_cmdset_providers()
     - uses_screenreader(session=None)
     - get_display_name(looker, **kwargs)
     - get_extra_display_name_info(looker, **kwargs)
     - disconnect_session_from_account()
     - puppet_object(session, obj)
     - unpuppet_object(session)
     - unpuppet_all()
     - get_puppet(session)
     - get_all_puppets()
     - is_banned(**kwargs)
     - get_username_validators(validator_config=settings.AUTH_USERNAME_VALIDATORS)
     - authenticate(username, password, ip="", **kwargs)
     - normalize_username(username)
     - validate_username(username)
     - validate_password(password, account=None)
     - set_password(password, **kwargs)
     - get_character_slots()
     - get_available_character_slots()
     - create_character(*args, **kwargs)
     - create(*args, **kwargs)
     - delete(*args, **kwargs)
     - channel_msg(message, channel, senders=None, **kwargs)
     - idle_time()
     - connection_time()

    * Hook methods

     basetype_setup()
     at_account_creation()

     > note that the following hooks are also found on Objects and are
       usually handled on the character level:

     - at_post_load()
     - at_first_save()
     - at_post_access()
     - at_cmdset_get(**kwargs)
     - at_post_password_change(**kwargs)
     - at_first_login()
     - at_pre_login()
     - at_post_login(session=None)
     - at_failed_login(session, **kwargs)
     - at_disconnect(reason=None, **kwargs)
     - at_post_disconnect(**kwargs)
     - at_message_receive()
     - at_message_send()
     - at_server_reload()
     - at_server_shutdown()
     - at_look(target=None, session=None, **kwargs)
     - at_post_create_character(character, **kwargs)
     - at_character_added(char)
     - at_character_removed(char)
     - at_puppet_added(character, session=None, **kwargs)
     - at_puppet_removed(character, session=None, **kwargs)
     - at_pre_channel_msg(message, channel, senders=None, **kwargs)
     - at_post_chnnel_msg(message, channel, senders=None, **kwargs)

    """

    # Determines which order command sets begin to be assembled from.
    # Accounts are usually second.
    cmdset_provider_order = 50
    cmdset_provider_error_order = 0
    cmdset_provider_type = "account"

    objects = AccountManager()

    # Used by account.create_character() to choose default typeclass for characters.
    default_character_typeclass = settings.BASE_CHARACTER_TYPECLASS

    lockstring = (
        "examine:perm(Admin);edit:perm(Admin);"
        "delete:perm(Admin);boot:perm(Admin);msg:all();"
        "noidletimeout:perm(Builder) or perm(noidletimeout)"
    )

    # properties
    @lazy_property
    def cmdset(self):
        return CmdSetHandler(self, True)

    @lazy_property
    def scripts(self):
        return ScriptHandler(self)

    @lazy_property
    def nicks(self):
        return NickHandler(self, import_string(settings.ATTRIBUTE_BACKEND_CLASS))

    @lazy_property
    def sessions(self):
        return AccountSessionHandler(self)

    @lazy_property
    def options(self):
        return OptionHandler(
            self,
            options_dict=settings.OPTIONS_ACCOUNT_DEFAULT,
            savefunc=self.attributes.add,
            loadfunc=self.attributes.get,
            save_kwargs={"category": "option"},
            load_kwargs={"category": "option"},
        )

    @lazy_property
    def characters(self):
        return CharactersHandler(self)

    @hook(
        event="cmdset",
        phase="composite",
        actor="self",
        returns="content",
        discipline="internal",
        fires_from=(),
        notes="Returns dict[str, CmdSetProvider]. Account version: includes self.",
    )
    def get_cmdset_providers(self) -> dict[str, "CmdSetProvider"]:
        """
        Overrideable method which returns a dictionary of every kind of object which
        has a cmdsethandler linked to this Account, and should participate in cmdset
        merging.

        Accounts have no way of being aware of anything besides themselves, unfortunately.

        Returns:
            dict[str, CmdSetProvider]: The CmdSetProviders linked to this Object.
        """
        return {"account": self}

    @hook(
        event="character_membership",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Notification: character added to the persistent characters-list.",
    )
    def at_character_added(self, character: "DefaultCharacter"):
        """
        Called after a character is added to this account's list of playable characters.

        Use it to easily implement custom logic when a character is added to an account.

        Args:
            character (DefaultCharacter): The character that was added.
        """
        pass

    @hook(
        event="character_membership",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Notification: character removed from the persistent characters-list.",
    )
    def at_character_removed(self, character: "DefaultCharacter"):
        """
        Called after a character is removed from this account's list of playable characters.

        Use it to easily implement custom logic when a character is removed from an account.

        Args:
            character (DefaultCharacter): The character that was removed.
        """
        pass

    @hook(
        event="puppet_membership",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=("DefaultAccount.puppet_object",),
        notes="Notification: puppet added to the live puppet-set. Errors here are swallowed by puppet_object.",
    )
    def at_puppet_added(self, character, session=None, **kwargs):
        """
        Called when a character enters this account's active puppet set.

        Fires on first-attach only: when a previously-unpuppeted character
        becomes puppeted by any session of this account. It does *not* fire
        on additional sessions attaching to a character this account is
        already puppeting (sharing in `MULTISESSION_MODE` 1/3, or session
        takeover) — those are per-session events already covered by
        `Object.at_post_puppet`. The semantics match set membership in
        `get_all_puppets()`.

        Args:
            character (DefaultObject): The character newly entering the
                puppet set.
            session (Session, optional): The session that triggered the
                attach. Useful for multi-session bookkeeping.
            **kwargs: Reserved for future use.

        Notes:
            Pair with `at_puppet_removed` for puppet-set lifecycle.
        """
        pass

    @hook(
        event="puppet_membership",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=("DefaultAccount.unpuppet_object",),
        notes="Notification: puppet removed from the live puppet-set.",
    )
    def at_puppet_removed(self, character, session=None, **kwargs):
        """
        Called when a character leaves this account's active puppet set.

        Fires on last-detach only: when the final session puppeting a
        character disconnects from it. It does *not* fire when one of
        several sessions detaches but others remain attached. The
        semantics match set membership in `get_all_puppets()`.

        Args:
            character (DefaultObject): The character leaving the puppet set.
            session (Session, optional): The session that triggered the
                detach.
            **kwargs: Reserved for future use.

        Notes:
            Pair with `at_puppet_added` for puppet-set lifecycle.
        """
        pass

    def uses_screenreader(self, session=None):
        """
        Shortcut to determine if a session uses a screenreader. If no session given,
        will return true if any of the sessions use a screenreader.

        Args:
            session (Session, optional): The session to check for screen reader.

        """
        if session:
            return bool(session.protocol_flags.get("SCREENREADER", False))
        else:
            return any(
                session.protocol_flags.get("SCREENREADER") for session in self.sessions.all()
            )

    @hook(
        event="look",
        phase="composite",
        actor="target",
        returns="content",
        discipline="public",
        fires_from=(),
        notes="Account override of AppearanceMixin's get_extra_display_name_info.",
    )
    def get_extra_display_name_info(self, looker, **kwargs):
        """
        Used in .get_display_name() to provide extra information to the looker. We split this
        to be consistent with the Object version of this method.

        This is used e.g. by the `find` command by default.

        """
        if looker and self.locks.check_lockstring(looker, "perm(Admin)"):
            return f"(#{self.id})"
        return ""

    def get_display_name(self, looker, **kwargs):
        """
        This is used by channels and other OOC communications methods to give a
        custom display of this account's input.

        Args:
            looker (Account): The one that will see this name.
            **kwargs: Unused by default, can be used to pass game-specific data.

        Returns:
            str: The name, possibly modified.

        """
        return f"|c{self.key}|n"

    # session-related methods

    def disconnect_session_from_account(self, session, reason=None):
        """
        Access method for disconnecting a given session from the
        account (connection happens automatically in the
        sessionhandler)

        Args:
            session (Session): Session to disconnect.
            reason (str, optional): Eventual reason for the disconnect.

        """
        evennia.SESSION_HANDLER.disconnect(session, reason)

    # puppeting operations

    def puppet_object(self, session, obj, push=False):
        """
        Use the given session to control (puppet) the given object (usually
        a Character type).

        Args:
            session (Session): session to use for puppeting
            obj (Object): the object to start puppeting
            push (bool): Focus-stack semantics. When True and the session is
                already driving a body, ``obj`` is *pushed* on top of that
                body's existing ControlBinding (the identity — the character —
                is preserved at the floor) rather than swapping the puppet and
                minting a fresh per-body binding. This is the engine primitive
                behind jack-in / rig / dive: the meat body stays in the focus
                stack beneath the avatar/vehicle and ``pop_focus`` returns to
                it. The inverse is :meth:`pop_focus`.

        Raises:
            RuntimeError: If puppeting is not possible, the
                `exception.msg` will contain the reason.


        """
        # safety checks
        if not obj:
            raise RuntimeError("Object not found")
        if not session:
            raise RuntimeError("Session not found")
        if self.get_puppet(session) == obj:
            # already puppeting this object
            self.msg(_("You are already puppeting this object."))
            return
        # Who, if anyone, is *currently driving* this body? Control is now a
        # live property of the focus body's attached sessions, not of the
        # durable ``obj.account`` ownership pointer.
        live_sessions = list(obj.sessions.all())
        driving_account = live_sessions[0].account if live_sessions else None
        # First-attach detection for the at_puppet_added hook. Captured before
        # any takeover-unpuppet runs, so a session swap on a currently-driven
        # character is not seen as a fresh attach.
        was_already_live = driving_account == self and bool(live_sessions)
        if not obj.access(self, "puppet"):
            # no access
            self.msg(_("You don't have permission to puppet '{key}'.").format(key=obj.key))
            return
        # If a takeover detaches another of our sessions, the new session
        # reattaches to that session's existing binding (preserving any pushed
        # avatar/vehicle stack) rather than building a fresh one.
        takeover_binding = None
        if driving_account is not None:
            # body already driven by a live session
            if driving_account == self:
                # we may take over another of our sessions
                # output messages to the affected sessions
                if settings.MULTISESSION_MODE in (1, 3):
                    txt1 = _("Sharing |c{name}|n with another of your sessions.").format(
                        name=obj.name
                    )
                    txt2 = _("|c{name}|n|G is now shared from another of your sessions.|n").format(
                        name=obj.name
                    )
                    self.msg(txt1, session=session)
                    self.msg(txt2, session=obj.sessions.all())
                else:
                    txt1 = _("Taking over |c{name}|n from another of your sessions.").format(
                        name=obj.name
                    )
                    txt2 = _("|c{name}|n|R is now acted from another of your sessions.|n").format(
                        name=obj.name
                    )
                    self.msg(txt1, session=session)
                    self.msg(txt2, session=obj.sessions.all())
                    # Detach the old session WITHOUT collapsing so a pushed
                    # avatar/vehicle stack survives; capture its binding so the
                    # new session reattaches to the same graph below.
                    takeover_sessions = list(make_iter(obj.sessions.get()))
                    takeover_binding = takeover_sessions[0].binding if takeover_sessions else None
                    self.unpuppet_object(takeover_sessions, collapse=False)
            elif driving_account.is_connected:
                # controlled by another account
                self.msg(_("|c{key}|R is already puppeted by another Account.").format(key=obj.key))
                return

        # A focus push keeps the session's current body in the stack (the meat
        # body the avatar/vehicle layers on top of), so skip the swap-unpuppet.
        pushing = bool(push) and session.get_puppet() is not None
        if not pushing and session.get_puppet():
            # cleanly unpuppet eventual previous object puppeted by this session
            self.unpuppet_object(session)
        # if we get to this point the character is ready to puppet or it
        # was left with a lingering account/session reference from an unclean
        # server kill or similar.
        #
        # The old MAX_NR_SIMULTANEOUS_PUPPETS cap is retired: a session drives a
        # single body (the ControlBinding focus top), and layering another body
        # is a *push* that swaps the live session rather than adding a parallel
        # puppet — so the cap not only no longer applies, it would wrongly block
        # a focus push (the meat body still holds the session at this point).

        # do the puppeting. at_pre_puppet may veto by returning False (or
        # other non-None falsy); None / True allow the attach.
        if is_veto(obj.at_pre_puppet(self, session=session)):
            return

        # do the connection: drive ``obj`` through this account's durable
        # ControlBinding (focus stack), then attach the runtime session to the
        # body for msg routing. ``obj.account`` (ownership) is left untouched.
        from evennia.accounts.models import ControlBinding

        if pushing:
            # Layer ``obj`` on top of the body the session already drives,
            # reusing that body's binding so the identity (character) stays at
            # the floor. The underlying body keeps its place in the stack and
            # only sheds its live session (it goes dormant, not unpuppeted).
            old = session.get_puppet()
            binding = session.binding or ControlBinding.for_identity(self, obj)
            if old is not None and old is not obj:
                old.sessions.remove(session)
            if binding.focus is not obj:
                binding.push(obj)
        else:
            # On takeover, reattach to the detached session's binding so a
            # pushed avatar/vehicle survives; obj is already its focus top.
            binding = takeover_binding or ControlBinding.for_identity(self, obj)
            if binding.focus is not obj:
                binding.push(obj)
        session.bid = binding.pk
        obj.sessions.add(session)
        _sync_session_bid_to_portal(session)

        # re-cache locks to make sure superuser bypass is updated
        obj.locks.cache_lock_bypass(obj)
        # final hook
        obj.at_post_puppet()
        SIGNAL_OBJECT_POST_PUPPET.send(sender=obj, account=self, session=session)

        if not was_already_live:
            # Puppet-set membership change; fires once per first-attach.
            try:
                self.at_puppet_added(obj, session=session)
            except Exception:
                logger.log_trace("at_puppet_added hook failed")

        if pushing:
            # A real focus push (jack-in / rig / dive) — notify subscribers. A
            # plain login (push=False) sets the floor body and does not emit.
            self._emit_focus_changed(session, obj, "push")

    def unpuppet_object(self, session, collapse=True):
        """
        Disengage control over an object.

        Args:
            session (Session or list): The session or a list of
                sessions to disengage from their puppets.
            collapse (bool): When True (the default — a deliberate go-OOC),
                the durable focus stack is collapsed back to the account floor
                once the last live session detaches. When False (a network
                drop or server shutdown), the stack is left intact so the body
                the session was driving — including any pushed avatar/vehicle —
                survives the disconnect and is restored on next login by
                :meth:`at_post_login`. Only the runtime session is detached.

        Raises:
            RuntimeError With message about error.

        """
        for session in make_iter(session):
            obj = session.get_puppet()
            if obj:
                # at_pre_unpuppet may veto by returning False (or other non-None
                # falsy); None / True allow the detach. Veto leaves the puppet
                # attached; the override is responsible for messaging the caller.
                if is_veto(obj.at_pre_unpuppet()):
                    continue
                obj.sessions.remove(session)
                last_session = not obj.sessions.count()
                # Collapse the focus stack back to the account floor only when
                # no co-driving session remains on this body (co-perception:
                # other sessions sharing this binding keep their focus) and the
                # caller asked for a collapse. A network drop / shutdown passes
                # collapse=False to keep the durable stack for login restore.
                # Ownership (``obj.account``) is durable and left intact.
                binding = session.binding
                if collapse and last_session and binding is not None:
                    binding.collapse_to_floor()
                obj.at_post_unpuppet(self, session=session)
                SIGNAL_OBJECT_POST_UNPUPPET.send(sender=obj, session=session, account=self)
                if last_session:
                    # Puppet-set membership change; fires once per last-detach.
                    try:
                        self.at_puppet_removed(obj, session=session)
                    except Exception:
                        logger.log_trace("at_puppet_removed hook failed")
            # Just to be sure we're always clear.
            session.bid = None
            _sync_session_bid_to_portal(session)

    def pop_focus(self, session):
        """
        Drop one focus level: tear down the body the session is currently
        driving and re-attach the session to the body now on top of the stack.

        This is the inverse of ``puppet_object(session, obj, push=True)`` — the
        jack-out / un-rig / un-dive primitive. Unlike :meth:`unpuppet_object`
        it does not collapse to the account floor or clear the binding; it pops
        a single layer so control returns to the body beneath (the meat
        character), re-priming that body with a lightweight ``reattach`` puppet
        rather than a fresh login.

        Args:
            session (Session): the session to pop a focus level for.

        Returns:
            Object or None: the body control returned to, or None if the stack
            popped all the way to the account floor (session left fully OOC).
        """
        obj = session.get_puppet()
        binding = session.binding
        if obj is None or binding is None:
            return None
        # at_pre_unpuppet may veto the teardown.
        if is_veto(obj.at_pre_unpuppet()):
            return None
        obj.sessions.remove(session)
        binding.pop()
        obj.at_post_unpuppet(self, session=session)
        SIGNAL_OBJECT_POST_UNPUPPET.send(sender=obj, session=session, account=self)
        try:
            self.at_puppet_removed(obj, session=session)
        except Exception:
            logger.log_trace("at_puppet_removed hook failed")

        new_focus = binding.focus
        if isinstance(new_focus, AccountDB):
            # Popped to the account floor — fully OOC.
            session.bid = None
            _sync_session_bid_to_portal(session)
            self._emit_focus_changed(session, obj, "pop")
            return None

        # Re-attach to the body now on top (the meat character) without
        # re-running its full login pipeline.
        new_focus.sessions.add(session)
        session.bid = binding.pk
        _sync_session_bid_to_portal(session)
        new_focus.locks.cache_lock_bypass(new_focus)
        new_focus.at_post_puppet(reattach=True, session=session)
        # Pop emitted after re-attach so the actor's focus resolves to the body
        # control returned to (the meat character now on top).
        self._emit_focus_changed(session, obj, "pop")
        return new_focus

    def reattach_focus(self, session, binding):
        """
        Re-attach ``session`` to the body on top of ``binding``'s durable focus
        stack without running a fresh puppet/login pipeline.

        Used by :meth:`at_post_login` to restore a pushed body (a Matrix avatar
        or rigged vehicle) that survived a disconnect: the focus stack is
        durable, so we just resolve the active body and wire this runtime
        session to it, firing at_pre/at_post_puppet with ``reattach=True`` so
        non-persistent cmdset/state rebuilds while login-only echoes stay
        suppressed. Mirrors the reload path in ``ServerSession.at_sync``.

        Returns:
            Object or None: the restored body, or None if the stack resolved to
            the account floor or the reattach was vetoed.
        """
        obj = binding.focus
        if obj is None or isinstance(obj, AccountDB):
            return None
        if is_veto(obj.at_pre_puppet(self, session=session, reattach=True)):
            return None
        obj.sessions.add(session)
        session.bid = binding.pk
        _sync_session_bid_to_portal(session)
        obj.locks.cache_lock_bypass(obj)
        obj.at_post_puppet(reattach=True, session=session)
        SIGNAL_OBJECT_POST_PUPPET.send(sender=obj, account=self, session=session)
        return obj

    @hook(
        event="session_sync",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=(),
        notes="Reattach puppet after server reload when bid-based at_sync could not restore focus.",
    )
    def at_sync_restore_puppet(self, session):
        """
        Last-resort puppet restore after PSYNC when ``session.bid`` was missing
        or stale on the Portal. Uses the durable ControlBinding graph and
        ``db._last_puppet`` rather than running the full login pipeline.

        Returns:
            bool: True if the session now has an in-game puppet.

        """
        if not session or session.get_puppet():
            return bool(session and session.get_puppet())

        from evennia.accounts.models import ControlBinding

        identity = getattr(self.db, "_last_puppet", None)
        if identity:
            binding = ControlBinding.objects.filter(db_identity=identity).first()
            if binding:
                focus = binding.focus
                if focus is not None and not isinstance(focus, AccountDB):
                    return self.reattach_focus(session, binding) is not None

        # Reload-restore always re-enters the body (no AUTO_PUPPET_ON_LOGIN gate:
        # the focus/binding model has no char-select to fall back to).
        if identity:
            try:
                self.puppet_object(session, identity)
            except RuntimeError:
                logger.log_trace("at_sync_restore_puppet puppet_object failed")
            return session.get_puppet() is not None
        return False

    def _emit_focus_changed(self, session, body, change):
        """Emit a :class:`~evennia.actions.actor.FocusChanged` for a focus-stack
        mutation driven through the engine puppet flow (jack-in push / jack-out
        pop), so ``@subscribe(FocusChanged)`` handlers fire on the same single
        path the action-system ``Actor.push_focus``/``pop_focus`` use. Emitted
        only for real pushes/pops — a plain login (push=False) or a disconnect
        collapse does not change focus *level* and stays on the puppet hooks.

        A buggy emit must never break the puppet transition, so it is fully
        guarded.
        """
        try:
            from evennia.actions.actor import Actor, FocusChanged
            from evennia.actions.engine import engine as _engine

            actor = Actor.from_caller(session, callertype="session")
            _engine.emit(FocusChanged(actor=actor, body=body, change=change, focus=actor.focus))
        except Exception:
            logger.log_trace("account._emit_focus_changed failed")

    def unpuppet_all(self):
        """
        Disconnect all puppets. This is called by server before a
        reset/shutdown.
        """
        # Shutdown must not collapse the durable focus stack — a player jacked
        # into the Matrix at restart should still be jacked in afterwards and
        # have their avatar restored on next login.
        self.unpuppet_object(self.sessions.all(), collapse=False)

    @hook(
        event="account_query",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=(),
        notes="Returns the puppet attached to the given session, or None.",
    )
    def get_puppet(self, session):
        """
        Get an object puppeted by this session through this account. This is
        the main method for retrieving the puppeted object from the
        account's end.

        Args:
            session (Session): Find puppeted object based on this session

        Returns:
            puppet (Object): The matching puppeted object, if any.

        """
        return session.get_puppet() if session else None

    @hook(
        event="account_query",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=(),
        notes="Returns all currently puppeted characters on the account.",
    )
    def get_all_puppets(self):
        """
        Get all currently puppeted objects.

        Returns:
            puppets (list): All puppeted objects currently controlled
                by this Account.

        """
        return list(
            {session.get_puppet() for session in self.sessions.all() if session.get_puppet()}
        )

    def __get_single_puppet(self):
        """
        This is a legacy convenience link for use with `MULTISESSION_MODE`.

        Returns:
            puppets (Object or list): Users of `MULTISESSION_MODE` 0 or 1 will
                always get the first puppet back. Users of higher `MULTISESSION_MODE`s will
                get a list of all puppeted objects.

        """
        puppets = self.get_all_puppets()
        if settings.MULTISESSION_MODE in (0, 1):
            return puppets and puppets[0] or None
        return puppets

    character = property(__get_single_puppet)
    puppet = property(__get_single_puppet)

    # utility methods
    @classmethod
    def is_banned(cls, **kwargs):
        """
        Checks if a given username or IP is banned.

        Keyword Args:
            ip (str, optional): IP address.
            username (str, optional): Username.

        Returns:
            is_banned (bool): Whether either is banned or not.

        """

        ip = kwargs.get("ip", "")
        if isinstance(ip, (tuple, list)):
            ip = ip[0]
        ip = ip.strip()
        username = kwargs.get("username", "").lower().strip()

        # Check IP and/or name bans
        bans = ServerConfig.objects.conf("server_bans")
        if bans and (
            any(tup[0] == username for tup in bans if username)
            or any(tup[2].match(ip) for tup in bans if ip and tup[2])
        ):
            return True

        return False

    @classmethod
    @hook(
        event="account_query",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=(),
        notes="Returns Django username validators. Override to relax/tighten allowed names.",
    )
    def get_username_validators(
        cls, validator_config=getattr(settings, "AUTH_USERNAME_VALIDATORS", [])
    ):
        """
        Retrieves and instantiates validators for usernames.

        Args:
            validator_config (list): List of dicts comprising the battery of
                validators to apply to a username.

        Returns:
            validators (list): List of instantiated Validator objects.
        """
        objs = []
        for validator in validator_config:
            try:
                klass = import_string(validator["NAME"])
            except ImportError:
                msg = (
                    f"The module in NAME could not be imported: {validator['NAME']}. "
                    "Check your AUTH_USERNAME_VALIDATORS setting."
                )
                raise ImproperlyConfigured(msg)
            objs.append(klass(**validator.get("OPTIONS", {})))
        return objs

    @classmethod
    def authenticate(cls, username, password, ip="", **kwargs):
        """
        Checks the given username/password against the database to see if the
        credentials are valid.

        Note that this simply checks credentials and returns a valid reference
        to the user-- it does not log them in!

        To finish the job:
        After calling this from a Command, associate the account with a Session:
        - session.sessionhandler.login(session, account)

        ...or after calling this from a View, associate it with an HttpRequest:
        - django.contrib.auth.login(account, request)

        Args:
            username (str): Username of account
            password (str): Password of account
            ip (str, optional): IP address of client

        Keyword Args:
            session (Session, optional): Session requesting authentication

        Returns:
            account (DefaultAccount, None): Account whose credentials were
                provided if not banned.
            errors (list): Error messages of any failures.

        """
        errors = []
        if ip:
            ip = str(ip)

        # See if authentication is currently being throttled
        if ip and LOGIN_THROTTLE.check(ip):
            errors.append(_("Too many login failures; please try again in a few minutes."))

            # With throttle active, do not log continued hits-- it is a
            # waste of storage and can be abused to make your logs harder to
            # read and/or fill up your disk.
            return None, errors

        # Check IP and/or name bans
        banned = cls.is_banned(username=username, ip=ip)
        if banned:
            # this is a banned IP or name!
            errors.append(
                _(
                    "|rYou have been banned and cannot continue from here."
                    "\nIf you feel this ban is in error, please email an admin.|x"
                )
            )
            logger.log_sec(f"Authentication Denied (Banned): {username} (IP: {ip}).")
            LOGIN_THROTTLE.update(ip, "Too many sightings of banned artifact.")
            return None, errors

        # Authenticate and get Account object
        account = authenticate(username=username, password=password)
        if not account:
            # User-facing message
            errors.append(_("Username and/or password is incorrect."))

            # Log auth failures while throttle is inactive
            logger.log_sec(f"Authentication Failure: {username} (IP: {ip}).")

            # Update throttle
            if ip:
                LOGIN_THROTTLE.update(ip, _("Too many authentication failures."))

            # Try to call post-failure hook
            session = kwargs.get("session", None)
            if session:
                account = AccountDB.objects.get_account_from_name(username)
                if account:
                    SIGNAL_ACCOUNT_POST_LOGIN_FAIL.send(sender=account, session=session)
                    account.at_failed_login(session)

            return None, errors

        # Account successfully authenticated
        logger.log_sec(f"Authentication Success: {account} (IP: {ip}).")
        return account, errors

    @classmethod
    def normalize_username(cls, username):
        """
        Django: Applies NFKC Unicode normalization to usernames so that visually
        identical characters with different Unicode code points are considered
        identical.

        (This deals with the Turkish "i" problem and similar
        annoyances. Only relevant if you go out of your way to allow Unicode
        usernames though-- Evennia accepts ASCII by default.)

        In this case we're simply piggybacking on this feature to apply
        additional normalization per Evennia's standards.
        """
        if not isinstance(username, str):
            username = str(username)

        username = super(DefaultAccount, cls).normalize_username(username)

        # strip excessive spaces in accountname
        username = re.sub(r"\s+", " ", username).strip()

        return username

    @classmethod
    def validate_username(cls, username):
        """
        Checks the given username against the username validator associated with
        Account objects, and also checks the database to make sure it is unique.

        Args:
            username (str): Username to validate

        Returns:
            valid (bool): Whether or not the password passed validation
            errors (list): Error messages of any failures

        """
        valid = []
        errors = []

        # Make sure we're at least using the default validator
        validators = cls.get_username_validators()
        if not validators:
            validators = [cls.username_validator]

        # Try username against all enabled validators
        for validator in validators:
            try:
                valid.append(not validator(username))
            except ValidationError as e:
                valid.append(False)
                errors.extend(e.messages)

        # Disqualify if any check failed
        if False in valid:
            valid = False
        else:
            valid = True

        return valid, errors

    @classmethod
    def validate_password(cls, password, account=None):
        """
        Checks the given password against the list of Django validators enabled
        in the server.conf file.

        Args:
            password (str): Password to validate

        Keyword Args:
            account (DefaultAccount, optional): Account object to validate the
                password for. Optional, but Django includes some validators to
                do things like making sure users aren't setting passwords to the
                same value as their username. If left blank, these user-specific
                checks are skipped.

        Returns:
            valid (bool): Whether or not the password passed validation
            error (ValidationError, None): Any validation error(s) raised. Multiple
                errors can be nested within a single object.

        """
        valid = False
        error = None

        # Validation returns None on success; invert it and return a more sensible bool
        try:
            valid = not password_validation.validate_password(password, user=account)
        except ValidationError as e:
            error = e

        return valid, error

    def set_password(self, password, **kwargs):
        """
        Applies the given password to the account. Logs and triggers the `at_post_password_change` hook.

        Args:
            password (str): Password to set.

        Notes:
            This is called by Django also when logging in; it should not be mixed up with
            validation, since that would mean old passwords in the database (pre validation checks)
            could get invalidated.

        """
        super().set_password(password)
        logger.log_sec(f"Password successfully changed for {self}.")
        self.at_post_password_change()

    @hook(
        event="account_query",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=(),
        notes="Returns the max number of characters this account may have. None = unlimited.",
    )
    def get_character_slots(self) -> typing.Optional[int]:
        """
        Returns the number of character slots this account has, or
        None if there are no limits.

        By default, that's settings.MAX_NR_CHARACTERS but this makes it easy to override.
        Maybe for your game, players can be rewarded with more slots, somehow.

        Returns:
            int (optional): The number of character slots this account has, or None
                if there are no limits.
        """
        return settings.MAX_NR_CHARACTERS

    @hook(
        event="account_query",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=(),
        notes="Returns remaining character slots. None = unlimited.",
    )
    def get_available_character_slots(self) -> typing.Optional[int]:
        """
        Returns the number of character slots this account has available, or None if
        there are no limits.

        Returns:
            int (optional): The number of open character slots this account has, or None
                if there are no limits.
        """
        if (slots := self.get_character_slots()) is None:
            return None
        return max(0, slots - len(self.characters))

    def check_available_slots(self, **kwargs) -> typing.Optional[str]:
        """
        Helper method used to determine if an account can create additional characters using
        the character slot system.

        Returns:
            str (optional): An error message regarding the status of slots. If present, this
               will halt character creation. If not, character creation can proceed.
        """
        if (slots := self.get_available_character_slots()) is not None:
            if slots <= 0:
                if not (self.is_superuser or self.check_permstring("Developer")):
                    plural = "" if (max_slots := self.get_character_slots()) == 1 else "s"
                    return f"You may only have a maximum of {max_slots} character{plural}."

    def create_character(self, *args, **kwargs):
        """
        Create a character linked to this account.

        Args:
            key (str, optional): If not given, use the same name as the account.
            typeclass (str, optional): Typeclass to use for this character. If
                not given, use self.default_character_class.
            permissions (list, optional): If not given, use the account's permissions.
            ip (str, optional): The client IP creating this character. Will fall back to the
                one stored for the account if not given.
            kwargs (any): Other kwargs will be used in the create_call.
        Returns:
            Object: A new character of the `character_typeclass` type. None on an error.
            list or None: A list of errors, or None.

        """
        # check character slot usage.
        if slot_check := self.check_available_slots():
            return None, [slot_check]

        # parse inputs
        character_key = kwargs.pop("key", self.key)
        character_ip = kwargs.pop("ip", self.db.creator_ip)
        character_permissions = kwargs.pop("permissions", self.permissions.all())

        # Load the appropriate Character class
        character_typeclass = kwargs.pop("typeclass", self.default_character_typeclass)
        Character = class_from_module(character_typeclass)

        if "location" not in kwargs:
            kwargs["location"] = ObjectDB.objects.get_id(settings.START_LOCATION)

        # Create the character
        character, errs = Character.create(
            character_key,
            self,
            ip=character_ip,
            typeclass=character_typeclass,
            permissions=character_permissions,
            **kwargs,
        )
        if character:
            self.at_post_create_character(character, ip=character_ip)

        return character, errs

    @hook(
        event="character_creation",
        phase="post",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Fires after a character is created and attached to the account.",
    )
    def at_post_create_character(self, character, **kwargs):
        """
        An overloadable hook method that allows for further customization of newly created characters.
        """
        if character not in self.characters:
            self.characters.add(character)

        # We need to set this to have @ic auto-connect to this character
        if len(self.characters) == 1:
            self.db._last_puppet = character

        character.locks.add(
            f"puppet:id({character.id}) or pid({self.id}) or perm(Developer) or"
            f" pperm(Developer);delete:id({self.id}) or perm(Admin)"
        )

        logger.log_sec(
            f"Character Created: {character} (Caller: {self}, IP: {kwargs.get('ip', None)})."
        )

    @classmethod
    def create(cls, *args, **kwargs):
        """
        Creates an Account (or Account/Character pair for MULTISESSION_MODE<2)
        with default (or overridden) permissions and having joined them to the
        appropriate default channels.

        Keyword Args:
            username (str): Username of Account owner
            password (str): Password of Account owner
            email (str, optional): Email address of Account owner
            ip (str, optional): IP address of requesting connection
            guest (bool, optional): Whether or not this is to be a Guest account

            permissions (str, optional): Default permissions for the Account
            typeclass (str, optional): Typeclass to use for new Account
            character_typeclass (str, optional): Typeclass to use for new char
                when applicable.

        Returns:
            account (Account): Account if successfully created; None if not
            errors (list): List of error messages in string form

        """

        account = None
        errors = []

        username = kwargs.get("username", "")
        password = kwargs.get("password", "")
        email = kwargs.get("email", "").strip()
        guest = kwargs.get("guest", False)

        permissions = kwargs.get("permissions", settings.PERMISSION_ACCOUNT_DEFAULT)
        typeclass = kwargs.get("typeclass", cls)

        ip = kwargs.get("ip", "")
        if isinstance(ip, (tuple, list)):
            ip = ip[0]

        if ip and CREATION_THROTTLE.check(ip):
            errors.append(
                _("You are creating too many accounts. Please log into an existing account.")
            )
            return None, errors

        # Normalize username
        username = cls.normalize_username(username)

        # Validate username
        if not guest:
            valid, errs = cls.validate_username(username)
            if not valid:
                # this echoes the restrictions made by django's auth
                # module (except not allowing spaces, for convenience of
                # logging in).
                errors.extend(errs)
                return None, errors

        # Validate password
        # Have to create a dummy Account object to check username similarity
        valid, errs = cls.validate_password(password, account=cls(username=username))
        if not valid:
            errors.extend(errs)
            return None, errors

        # Check IP and/or name bans
        banned = cls.is_banned(username=username, ip=ip)
        if banned:
            # this is a banned IP or name!
            string = _(
                "|rYou have been banned and cannot continue from here."
                "\nIf you feel this ban is in error, please email an admin.|x"
            )
            errors.append(string)
            return None, errors

        # everything's ok. Create the new account.
        try:
            try:
                account = create.create_account(
                    username, email, password, permissions=permissions, typeclass=typeclass
                )
                logger.log_sec(f"Account Created: {account} (IP: {ip}).")

            except Exception:
                errors.append(
                    _(
                        "There was an error creating the Account. "
                        "If this problem persists, contact an admin."
                    )
                )
                logger.log_trace()
                return None, errors

            # This needs to be set so the engine knows this account is
            # logging in for the first time. (so it knows to call the right
            # hooks during login later)
            account.db.FIRST_LOGIN = True

            # Record IP address of creation, if available
            if ip:
                account.db.creator_ip = ip

            # join the new account to the public channels
            for chan_info in settings.DEFAULT_CHANNELS:
                if chankey := chan_info.get("key"):
                    channel = ChannelDB.objects.get_channel(chankey)
                    if not channel or not (
                        channel.access(account, "listen") and channel.connect(account)
                    ):
                        string = (
                            f"New account '{account.key}' could not connect to default channel"
                            f" '{chankey}'!"
                        )
                        logger.log_err(string)
                else:
                    logger.log_err(f"Default channel '{chan_info}' is missing a 'key' field!")

            if account and settings.AUTO_CREATE_CHARACTER_WITH_ACCOUNT:
                # Auto-create a character to go with this account

                character, errs = account.create_character(
                    typeclass=kwargs.get("character_typeclass", account.default_character_typeclass)
                )
                if errs:
                    errors.extend(errs)

        except Exception:
            # We are in the middle between logged in and -not, so we have
            # to handle tracebacks ourselves at this point. If we don't,
            # we won't see any errors at all.
            errors.append(_("An error occurred. Please e-mail an admin if the problem persists."))
            logger.log_trace()

        # Update the throttle to indicate a new account was created from this IP
        if ip and not guest:
            CREATION_THROTTLE.update(ip, "Too many accounts being created.")
        SIGNAL_ACCOUNT_POST_CREATE.send(sender=account, ip=ip)
        return account, errors

    def delete(self, *args, **kwargs):
        """
        Deletes the account persistently.

        Notes:
            `*args` and `**kwargs` are passed on to the base delete
             mechanism (these are usually not used).

        Return:
            bool: If deletion was successful. Only time it fails would be
                if the Account was already deleted. Note that even on a failure,
                connected resources (nicks/aliases etc) will still have been
                deleted.

        """
        for session in self.sessions.all():
            # unpuppeting all objects and disconnecting the user, if any
            # sessions remain (should usually be handled from the
            # deleting command)
            try:
                self.unpuppet_object(session)
            except RuntimeError:
                # no puppet to disconnect from
                pass
            session.sessionhandler.disconnect(session, reason=_("Account being deleted."))
        self.scripts.delete()
        self.attributes.clear()
        self.nicks.clear()
        self.aliases.clear()
        if not self.pk:
            return False
        super().delete(*args, **kwargs)
        return True

    # methods inherited from database model

    def msg(self, text=None, from_obj=None, session=None, options=None, **kwargs):
        """
        Evennia -> User
        This is the main route for sending data back to the user from the
        server.

        Args:
            text (str or tuple, optional): The message to send. This
                is treated internally like any send-command, so its
                value can be a tuple if sending multiple arguments to
                the `text` oob command.
            from_obj (Object or Account or list, optional): Object sending. If given, its
                at_msg_send() hook will be called. If iterable, call on all entities.
            session (Session or list, optional): Session object or a list of
                Sessions to receive this send. If given, overrules the
                default send behavior for the current
                MULTISESSION_MODE.
            options (list): Protocol-specific options. Passed on to the protocol.
        Keyword Args:
            any (dict): All other keywords are passed on to the protocol.

        """
        if from_obj:
            # call hook
            for obj in make_iter(from_obj):
                try:
                    obj.at_msg_send(text=text, to_obj=self, **kwargs)
                except Exception:
                    # this may not be assigned.
                    logger.log_trace()
        try:
            if not self.at_msg_receive(text=text, **kwargs):
                # abort message to this account
                return
        except Exception:
            # this may not be assigned.
            pass

        kwargs["options"] = options

        if text is not None:
            if not (isinstance(text, str) or isinstance(text, tuple)):
                # sanitize text before sending across the wire
                try:
                    text = to_str(text)
                except Exception:
                    text = repr(text)
            kwargs["text"] = text

        # session relay
        sessions = make_iter(session) if session else self.sessions.all()
        for session in sessions:
            session.data_out(**kwargs)

    def execute_cmd(self, raw_string, session=None, **kwargs):
        """
        Do something as this account. This method is never called normally,
        but only when the account object itself is supposed to execute the
        command. It takes account nicks into account, but not nicks of
        eventual puppets.

        Args:
            raw_string (str): Raw command input coming from the command line.
            session (Session, optional): The session to be responsible
                for the command-send

        Keyword Args:
            kwargs (any): Other keyword arguments will be added to the
                found command object instance as variables before it
                executes. This is unused by default Evennia but may be
                used to set flags and change operating parameters for
                commands at run-time.

        """
        # break circular import issues
        global _CMDHANDLER
        if not _CMDHANDLER:
            from evennia.commands.cmdhandler import cmdhandler as _CMDHANDLER
        raw_string = self.nicks.nickreplace(
            raw_string, categories=("inputline", "channel"), include_account=False
        )
        if not session and settings.MULTISESSION_MODE in (0, 1):
            # for these modes we use the first/only session
            sessions = self.sessions.get()
            session = sessions[0] if sessions else None

        from evennia.utils import clock

        # cmdhandler is `async def` now; run the coroutine on the loop as a Deferred.
        return clock.run_coroutine(
            _CMDHANDLER(self, raw_string, callertype="account", session=session, **kwargs)
        )

    # channel receive hooks

    @hook(
        event="channel_msg",
        phase="pre",
        actor="self",
        returns="transform",
        discipline="public",
        fires_from=("DefaultChannel.msg",),
        notes="Receiver-side transform: non-empty string replaces, False/empty aborts for this receiver, None falls back.",
    )
    def at_pre_channel_msg(self, message, channel, senders=None, **kwargs):
        """
        Called by the Channel just before passing a message into `channel_msg`.
        Allows tweaking the message per-recipient and aborting the receive
        on the receiver-level.

        **Transform hook with the symmetric None-rule (see
        `evennia.utils.utils.resolve_transform`).** This is a transform
        hook: it returns the (possibly modified) message string.

        Return rule:

        - Return a non-empty string to replace the message for this recipient.
        - Return `False` (or `""`) to explicitly abort the receive for this
          recipient.
        - Return `None` (including the implicit return from a side-effect-
          only override) to use the original `message` unchanged. **This is
          a `+underspire.41` change**: previously `None` aborted the
          receive, which silently dropped every channel message for any
          recipient whose override forgot the explicit `return message`.

        Args:
            message (str): The message sent to the channel.
            channel (Channel): The sending channel.
            senders (list, optional): Accounts or Objects acting as senders.
                For most normal messages, there is only a single sender. If
                there are no senders, this may be a broadcasting message.
            **kwargs: These are additional keywords passed into `channel_msg`.
                If `no_prefix=True` or `emit=True` are passed, the channel
                prefix will not be added (`[channelname]: ` by default)

        Returns:
            str, False, or None: The (possibly modified) message string,
            `False`/empty to abort the receive, `None` to fall back to
            the original message.

        Notes:
            This support posing/emotes by starting channel-send with : or ;.

        """
        if senders:
            sender_string = ", ".join(sender.get_display_name(self) for sender in senders)
            message_lstrip = message.lstrip()
            if message_lstrip.startswith((":", ";")):
                # this is a pose, should show as e.g. "User1 smiles to channel"
                spacing = "" if message_lstrip[1:].startswith((":", "'", ",")) else " "
                message = f"{sender_string}{spacing}{message_lstrip[1:]}"
            else:
                # normal message
                message = f"{sender_string}: {message}"

        if not kwargs.get("no_prefix") and not kwargs.get("emit"):
            message = channel.channel_prefix() + message

        return message

    def channel_msg(self, message, channel, senders=None, **kwargs):
        """
        This performs the actions of receiving a message to an un-muted
        channel.

        Args:
            message (str): The message sent to the channel.
            channel (Channel): The sending channel.
            senders (list, optional): Accounts or Objects acting as senders.
                For most normal messages, there is only a single sender. If
                there are no senders, this may be a broadcasting message or
                similar.
            **kwargs: These are additional keywords originally passed into
                `Channel.msg`.

        Notes:
            Before this, `Channel.at_pre_channel_msg` will fire, which offers a way
            to customize the message for the receiver on the channel-level.

        """
        self.msg(
            text=(message, {"from_channel": channel.id}),
            from_obj=senders,
            options={"from_channel": channel.id},
        )

    @hook(
        event="channel_msg",
        phase="post",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=("DefaultChannel.msg",),
        notes="Receiver-side post-delivery hook.",
    )
    def at_post_channel_msg(self, message, channel, senders=None, **kwargs):
        """
        Called by `self.channel_msg` after message was received.

        Args:
            message (str): The message sent to the channel.
            channel (Channel): The sending channel.
            senders (list, optional): Accounts or Objects acting as senders.
                For most normal messages, there is only a single sender. If
                there are no senders, this may be a broadcasting message.
            **kwargs: These are additional keywords passed into `channel_msg`.

        """
        pass

    # search method

    def search_for(
        self,
        searchdata,
        search_object=False,
        typeclass=None,
        use_nicks=True,
        **kwargs,
    ):
        """
        Run the account search pipeline and return a typed `SearchResult`.

        This is the primitive: it never emits messages. The result is one of
        `Found`, `Ambiguous`, or `NotFound` (see `evennia.objects.search_result`).

        For the common "find one or fail with a default prompt" case, use
        `.search()` instead.

        Args:
            searchdata (str or int): The Account's key or dbref. The strings
                "me"/"self" (with optional "*" prefix) short-circuit to `self`.
            search_object (bool): Search for Objects instead of Accounts.
            typeclass: Limit the search to this typeclass.
            use_nicks (bool): Use account-level nick replacement.

        Returns:
            SearchResult: One of `Found(obj)`, `Ambiguous(candidates,
            search_string)`, or `NotFound(search_string)`.

        """
        from evennia.objects.search_result import Ambiguous, Found, NotFound

        original_searchdata = searchdata

        if isinstance(searchdata, str):
            if searchdata.lower() in ("me", "*me", "self", "*self"):
                return Found(obj=self)
            if use_nicks:
                searchdata = self.nicks.nickreplace(
                    searchdata, categories=("account",), include_account=False
                )

        if search_object:
            matches = list(ObjectDB.objects.object_search(searchdata, typeclass=typeclass))
        else:
            matches = list(AccountDB.objects.account_search(searchdata, typeclass=typeclass))

        if not matches:
            return NotFound(search_string=original_searchdata)
        if len(matches) == 1:
            return Found(obj=matches[0])
        return Ambiguous(candidates=matches, search_string=original_searchdata)

    def search(
        self,
        searchdata,
        return_puppet=False,
        search_object=False,
        typeclass=None,
        not_found=None,
        ambiguous=None,
        use_nicks=True,
        **kwargs,
    ):
        """
        Search for an Account (or Object) and return it (or None on failure).

        Convenience wrapper around `.search_for()`. On `Found`, returns the
        matched account/object (or its puppet if `return_puppet=True`). On
        `Ambiguous` or `NotFound`, emits the default prompt via
        `settings.SEARCH_AT_RESULT` and returns `None`.

        Args:
            searchdata (str or int): Search criterion, the Account's
                key or dbref to search for.
            return_puppet (bool, optional): Return the puppet of the matched
                Account rather than the Account itself (or None if not puppeted).
            search_object (bool, optional): Search for Objects instead of
                Accounts. Used e.g. by `@examine` when OOC.
            typeclass (Account typeclass, optional): Limit the search
                to this particular typeclass.
            not_found (str, optional): Custom not-found prompt.
            ambiguous (str, optional): Custom multimatch prompt header.
            use_nicks (bool, optional): Use account-level nick replacement.

        Returns:
            Account, Object or None: A single match (or its puppet if
            `return_puppet=True`), or None when no/multi-match.

        Notes:
            Extra keywords are ignored, allowed for API consistency with
            `DefaultObject.search`.

        """
        from evennia.objects.search_result import Ambiguous, Found

        result = self.search_for(
            searchdata,
            search_object=search_object,
            typeclass=typeclass,
            use_nicks=use_nicks,
        )

        if isinstance(result, Found):
            match = result.obj
            if return_puppet:
                puppets = match.get_all_puppets() if hasattr(match, "get_all_puppets") else []
                return puppets[0] if puppets else None
            return match

        if kwargs.get("quiet"):
            # quiet: suppress the not-found / multimatch prompt (mirrors
            # DefaultObject.search's quiet contract) and just return None.
            return None
        matches = result.candidates if isinstance(result, Ambiguous) else []
        return _AT_SEARCH_RESULT(
            matches,
            self,
            query=result.search_string,
            nofound_string=not_found,
            multimatch_string=ambiguous,
        )

    def access(
        self, accessing_obj, access_type="read", default=False, no_superuser_bypass=False, **kwargs
    ):
        """
        Determines if another object has permission to access this
        object in whatever way.

        Args:
          accessing_obj (Object): Object trying to access this one.
          access_type (str, optional): Type of access sought.
          default (bool, optional): What to return if no lock of
            access_type was found
          no_superuser_bypass (bool, optional): Turn off superuser
            lock bypassing. Be careful with this one.

        Keyword Args:
          kwargs (any): Passed to the at_post_access hook along with the result.

        Returns:
            result (bool): Result of access check.

        """
        result = super().access(
            accessing_obj,
            access_type=access_type,
            default=default,
            no_superuser_bypass=no_superuser_bypass,
        )
        self.at_post_access(result, accessing_obj, access_type, **kwargs)
        return result

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

    # account hooks

    def basetype_setup(self):
        """
        This sets up the basic properties for an account. Overload this
        with at_account_creation rather than changing this method.

        """
        # A basic security setup
        self.locks.add(self.lockstring)

        # The ooc account cmdset
        self.cmdset.add_default(settings.CMDSET_ACCOUNT, persistent=True)

    @hook(
        event="account_creation",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=("DefaultAccount.at_first_save",),
        notes="One-shot account creation. Fires once via at_first_save.",
    )
    def at_account_creation(self):
        """
        This is called once, the very first time the account is created
        (i.e. first time they register with the game). It's a good
        place to store attributes all accounts should have, like
        configuration values etc.

        """
        # Playable characters are now derived from the ControlBinding graph
        # (see CharactersHandler); no ``_playable_characters`` attribute is set.
        lockstring = "attrread:perm(Admins);attredit:perm(Admins);attrcreate:perm(Admins);"
        self.attributes.add("_saved_protocol_flags", {}, lockstring=lockstring)

    def at_post_load(self):
        """
        This is always called whenever this object is initiated --
        that is, whenever it its typeclass is cached from memory. This
        happens on-demand first time the object is used or activated
        in some way after being created but also after each server
        restart or reload. In the case of account objects, this usually
        happens the moment the account logs in or reconnects after a
        reload.

        """
        from evennia.actions.state import rehydrate_captures

        rehydrate_captures(self)

    # Note that the hooks below also exist in the character object's
    # typeclass. You can often ignore these and rely on the character
    # ones instead, unless you are implementing a multi-character game
    # and have some things that should be done regardless of which
    # character is currently connected to this account.

    @hook(
        event="creation",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="internal",
        fires_from=(),
        notes="Driven by Django post_save signal (created=True). Override at_account_creation instead.",
    )
    def at_first_save(self, **kwargs):
        """
        This is a generic hook called by Evennia when this object is
        saved to the database the very first time.  You generally
        don't override this method but the hooks called by it.

        Args:
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        self.basetype_setup()
        self.at_account_creation()
        # initialize Attribute/TagProperties
        self.init_evennia_properties()

        permissions = [settings.PERMISSION_ACCOUNT_DEFAULT]
        if hasattr(self, "_createdict"):
            # this will only be set if the utils.create_account
            # function was used to create the object.
            cdict = self._createdict
            updates = []
            if not cdict.get("key"):
                if not self.db_key:
                    self.db_key = f"#{self.dbid}"
                    updates.append("db_key")
            elif self.key != cdict.get("key"):
                updates.append("db_key")
                self.db_key = cdict["key"]
            if updates:
                self.save(update_fields=updates)

            if cdict.get("locks"):
                self.locks.add(cdict["locks"])
            if cdict.get("permissions"):
                permissions = cdict["permissions"]
            if cdict.get("tags"):
                # this should be a list of tags, tuples (key, category) or (key, category, data)
                self.tags.batch_add(*cdict["tags"])
            if cdict.get("attributes"):
                # this should be tuples (key, val, ...)
                self.attributes.batch_add(*cdict["attributes"])
            if cdict.get("nattributes"):
                # this should be a dict of nattrname:value
                for key, value in cdict["nattributes"]:
                    self.nattributes.add(key, value)
            del self._createdict

        self.permissions.batch_add(*permissions)

        self.at_account_post_creation()

    @hook(
        event="account_creation",
        phase="post",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=("DefaultAccount.at_first_save",),
        notes="Fires after at_account_creation and _createdict processing. Symmetric to at_object_post_creation.",
    )
    def at_account_post_creation(self):
        """
        Called once, after `at_account_creation`, _createdict processing, and
        permissions setup. Override for game-side initialization that needs
        to run after all engine-side creation steps complete.
        """
        pass

    @hook(
        event="access_check",
        phase="post",
        actor="target",
        returns="ignored",
        discipline="public",
        fires_from=("LockHandler.check",),
        notes="Account override of LifecycleMixin.at_post_access.",
    )
    def at_post_access(self, result, accessing_obj, access_type, **kwargs):
        """
        This is triggered after an access-call on this Account has
            completed.

        Args:
            result (bool): The result of the access check.
            accessing_obj (any): The object requesting the access
                check.
            access_type (str): The type of access checked.

        Keyword Args:
            kwargs (any): These are passed on from the access check
                and can be used to relay custom instructions from the
                check mechanism.

        Notes:
            This method cannot affect the result of the lock check and
            its return value is not used in any way. It can be used
            e.g.  to customize error messages in a central location or
            create other effects based on the access result.

        """
        pass

    @hook(
        event="cmdset",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Account-side cmdset mutation. Mirrors LifecycleMixin.at_cmdset_get.",
    )
    def at_cmdset_get(self, **kwargs):
        """
        Called just before cmdsets on this object are requested by the
        command handler. If changes need to be done on the fly to the
        cmdset before passing them on to the cmdhandler, this is the
        place to do it. This is called also if the object currently
        have no cmdsets.

        Keyword Args:
            caller (Object, Account or Session): The object requesting the cmdsets.
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
        fires_from=(),
        notes="Account-side cmdset stack. Mirrors LifecycleMixin.get_cmdsets.",
    )
    def get_cmdsets(self, caller, current, **kwargs):
        """
        Called by the CommandHandler to get a list of cmdsets to merge.

        Args:
            caller (obj): The object requesting the cmdsets.
            current (cmdset): The current merged cmdset.
            **kwargs: Arbitrary input for overloads.

        Returns:
            tuple: A tuple of (current, cmdsets), which is probably self.cmdset.current and self.cmdset.cmdset_stack
        """
        return self.cmdset.current, list(self.cmdset.cmdset_stack)

    @hook(
        event="login",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=("DefaultAccount.at_pre_login",),
        notes="One-shot first-login hook. Fires once via at_pre_login when last_login is unset.",
    )
    def at_first_login(self, **kwargs):
        """
        Called the very first time this account logs into the game.
        Note that this is called *before* at_pre_login, so no session
        is established and usually no character is yet assigned at
        this point. This hook is intended for account-specific setup
        like configurations.

        Args:
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        pass

    @hook(
        event="password_change",
        phase="post",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Fires after a successful password change.",
    )
    def at_post_password_change(self, **kwargs):
        """
        Called after a successful password set/modify.

        Args:
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        pass

    @hook(
        event="login",
        phase="pre",
        actor="self",
        returns="veto",
        discipline="public",
        fires_from=(),
        notes="Veto disconnects the session ('Login refused.'). Fires at_first_login on the first successful login.",
    )
    def at_pre_login(self, **kwargs):
        """
        Called every time the user logs in, just before the actual
        login-state is set.

        **Not vetoable.** Return value is ignored. Authentication has
        already succeeded by this point; this hook exists for pre-login
        side effects (state warming, audit logs), not gating. Block at
        the authenticate stage if you need to refuse a login.

        Args:
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        pass

    def _send_to_connect_channel(self, message):
        """
        Helper method for loading and sending to the comm channel dedicated to
        connection messages. This will also be sent to the mudinfo channel.

        Args:
            message (str): A message to send to the connect channel.

        """
        global _MUDINFO_CHANNEL, _CONNECT_CHANNEL
        if _MUDINFO_CHANNEL is None:
            if settings.CHANNEL_MUDINFO:
                try:
                    _MUDINFO_CHANNEL = ChannelDB.objects.get(db_key=settings.CHANNEL_MUDINFO["key"])
                except ChannelDB.DoesNotExist:
                    logger.log_trace()
            else:
                _MUDINFO_CHANNEL = False
        if _CONNECT_CHANNEL is None:
            if settings.CHANNEL_CONNECTINFO:
                try:
                    _CONNECT_CHANNEL = ChannelDB.objects.get(
                        db_key=settings.CHANNEL_CONNECTINFO["key"]
                    )
                except ChannelDB.DoesNotExist:
                    logger.log_trace()
            else:
                _CONNECT_CHANNEL = False

        if settings.USE_TZ:
            now = timezone.localtime()
        else:
            now = timezone.now()
        now = "%02i-%02i-%02i(%02i:%02i)" % (now.year, now.month, now.day, now.hour, now.minute)
        if _MUDINFO_CHANNEL:
            _MUDINFO_CHANNEL.msg(f"[{now}]: {message}")
        if _CONNECT_CHANNEL:
            _CONNECT_CHANNEL.msg(f"[{now}]: {message}")

    @hook(
        event="login",
        phase="post",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Fires after the login completes and the session is fully attached.",
    )
    def at_post_login(self, session=None, **kwargs):
        """
        Called at the end of the login process, just before letting
        the account loose.

        Args:
            session (Session, optional): Session logging in, if any.
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        Notes:
            This is called *before* an eventual Character's
            `at_post_login` hook. By default it is used to set up
            auto-puppeting based on `MULTISESSION_MODE`

        """
        # if we have saved protocol flags on ourselves, load them here.
        protocol_flags = self.attributes.get("_saved_protocol_flags", {})
        if session and protocol_flags:
            session.update_flags(**protocol_flags)

        # inform the client that we logged in through an OOB message
        if session:
            session.msg(logged_in={})

        self._send_to_connect_channel(_("|G{key} connected|n").format(key=self.key))
        if settings.AUTO_PUPPET_ON_LOGIN:
            # in this mode we try to auto-connect to our last connected object, if any
            try:
                identity = self.db._last_puppet
                # If a body was pushed onto this identity's durable focus stack
                # (jacked into the Matrix / rigged a vehicle) and survived the
                # last disconnect, restore that body rather than re-puppeting
                # the meat character beneath it.
                from evennia.accounts.models import ControlBinding

                binding = (
                    ControlBinding.objects.filter(db_identity=identity).first()
                    if identity
                    else None
                )
                focus = binding.focus if binding else None
                if focus is not None and focus is not identity and not isinstance(focus, AccountDB):
                    self.reattach_focus(session, binding)
                else:
                    self.puppet_object(session, identity)
            except RuntimeError:
                logger.log_trace("Error during auto-puppet on login")
                self.msg(_("The Character does not exist."))
                return
        else:
            # In this mode we don't auto-connect but by default end up at a character selection
            # screen. We execute look on the account.
            self.msg(self.at_look(target=self.characters, session=session), session=session)

    @hook(
        event="login",
        phase="failed",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Fires when authentication or at_pre_login rejects a login attempt.",
    )
    def at_failed_login(self, session, **kwargs):
        """
        Called by the login process if a user account is targeted correctly
        but provided with an invalid password. By default it does nothing,
        but exists to be overridden.

        Args:
            session (session): Session logging in.
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).
        """
        pass

    @hook(
        event="disconnect",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Account-side disconnect hook.",
    )
    def at_disconnect(self, reason=None, **kwargs):
        """
        Called just before user is disconnected.

        Args:
            reason (str, optional): The reason given for the disconnect,
                (echoed to the connection channel by default).
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).


        """
        reason = f" ({reason if reason else ''})"
        self._send_to_connect_channel(
            _("|R{key} disconnected{reason}|n").format(key=self.key, reason=reason)
        )

    @hook(
        event="disconnect",
        phase="post",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Fires after disconnect completes. No messaging here; session is gone.",
    )
    def at_post_disconnect(self, **kwargs):
        """
        This is called *after* disconnection is complete. No messages
        can be relayed to the account from here. After this call, the
        account should not be accessed any more, making this a good
        spot for deleting it (in the case of a guest account account,
        for example).

        Args:
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        pass

    @hook(
        event="msg",
        phase="composite",
        actor="self",
        returns="veto",
        discipline="public",
        fires_from=(),
        notes="Misshapen veto: at_<event> name. Falsy-not-None aborts delivery.",
    )
    def at_msg_receive(self, text=None, from_obj=None, **kwargs):
        """
        This hook is called whenever someone sends a message to this
        object using the `msg` method.

        Note that from_obj may be None if the sender did not include
        itself as an argument to the obj.msg() call - so you have to
        check for this. .

        Consider this a pre-processing method before msg is passed on
        to the user session. If this method returns False, the msg
        will not be passed on.

        Args:
            text (str, optional): The message received.
            from_obj (any, optional): The object sending the message.

        Keyword Args:
            This includes any keywords sent to the `msg` method.

        Returns:
            receive (bool): If this message should be received.

        Notes:
            If this method returns False, the `msg` operation
            will abort without sending the message.

        """
        return True

    @hook(
        event="msg",
        phase="composite",
        actor="self",
        returns="veto",
        discipline="public",
        fires_from=(),
        notes="Misshapen veto: at_<event> name. Falsy-not-None aborts the send.",
    )
    def at_msg_send(self, text=None, to_obj=None, **kwargs):
        """
        This is a hook that is called when *this* object sends a
        message to another object with `obj.msg(text, to_obj=obj)`.

        Args:
            text (str, optional): Text to send.
            to_obj (any, optional): The object to send to.

        Keyword Args:
            Keywords passed from msg()

        Notes:
            Since this method is executed by `from_obj`, if no `from_obj`
            was passed to `DefaultCharacter.msg` this hook will never
            get called.

        """
        pass

    @hook(
        event="server_lifecycle",
        phase="composite",
        actor="self",
        returns="ignored",
        discipline="public",
        fires_from=(),
        notes="Account-side reload hook. Fires from EvenniaServerService.shutdown on reload-style stops.",
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
        notes="Account-side shutdown hook. Fires from EvenniaServerService.shutdown on full-shutdown stops.",
    )
    def at_server_shutdown(self):
        """
        This hook is called whenever the server is shutting down fully
        (i.e. not for a restart).
        """
        pass

    ooc_appearance_template = """
--------------------------------------------------------------------
{header}

{sessions}

  |whelp|n - more commands
  |wpublic <text>|n - talk on public channel
  |wcharcreate <name> [=description]|n - create new character
  |wchardelete <name>|n - delete a character
  |wic <name>|n - enter the game as character (|wooc|n to get back here)
  |wic|n - enter the game as latest character controlled.

{characters}
{footer}
--------------------------------------------------------------------
""".strip()

    @hook(
        event="ooc_look",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=(),
        notes="Distinct from Object.at_look. Account.at_look is the OOC character picker.",
    )
    def at_look(self, target=None, session=None, **kwargs):
        """
        Called when this object executes a look. It allows to customize
        just what this means.

        Args:
            target (Object or list, optional): An object or a list
                objects to inspect. This is normally a list of characters.
            session (Session, optional): The session doing this look.
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        Returns:
            look_string (str): A prepared look string, ready to send
                off to any recipient (usually to ourselves)

        """

        if target and not is_iter(target):
            # single target - just show it
            if hasattr(target, "return_appearance"):
                return target.return_appearance(self)
            else:
                return f"{target} has no in-game appearance."

        # multiple targets - this is a list of characters
        characters = list(tar for tar in target if tar) if target else []
        ncars = len(characters)
        sessions = self.sessions.all()
        nsess = len(sessions)

        if not nsess:
            # no sessions, nothing to report
            return ""

        # header text
        txt_header = f"Account |g{self.name}|n (you are Out-of-Character)"

        # sessions
        sess_strings = []
        for isess, sess in enumerate(sessions):
            ip_addr = sess.address[0] if isinstance(sess.address, tuple) else sess.address
            addr = f"{sess.protocol_key} ({ip_addr})"
            sess_str = (
                f"|w* {isess + 1}|n"
                if session and session.sessid == sess.sessid
                else f"  {isess + 1}"
            )

            sess_strings.append(f"{sess_str} {addr}")

        txt_sessions = "|wConnected session(s):|n\n" + "\n".join(sess_strings)

        if not characters:
            txt_characters = "You don't have a character yet. Use |wcharcreate|n."
        else:
            _max_chars = settings.MAX_NR_CHARACTERS
            max_chars = "unlimited" if self.is_superuser or _max_chars is None else _max_chars

            char_strings = []
            for char in characters:
                csessions = char.sessions.all()
                if csessions:
                    for sess in csessions:
                        # character is already puppeted
                        sid = sess in sessions and sessions.index(sess) + 1
                        if sess and sid:
                            char_strings.append(
                                f" - |G{char.name}|n [{', '.join(char.permissions.all())}] "
                                f"(played by you in session {sid})"
                            )
                        else:
                            char_strings.append(
                                f" - |R{char.name}|n [{', '.join(char.permissions.all())}] "
                                "(played by someone else)"
                            )
                else:
                    # character is "free to puppet"
                    char_strings.append(f" - {char.name} [{', '.join(char.permissions.all())}]")

            txt_characters = (
                f"Available character(s) ({ncars}/{max_chars}, |wic <name>|n to play):|n\n"
                + "\n".join(char_strings)
            )
        return self.ooc_appearance_template.format(
            header=txt_header,
            sessions=txt_sessions,
            characters=txt_characters,
            footer="",
        )


class DefaultGuest(DefaultAccount):
    """
    This class is used for guest logins. Unlike Accounts, Guests and
    their characters are deleted after disconnection.

    """

    @classmethod
    def create(cls, **kwargs):
        """
        Forwards request to cls.authenticate(); returns a DefaultGuest object
        if one is available for use.

        """
        return cls.authenticate(**kwargs)

    @classmethod
    def authenticate(cls, **kwargs):
        """
        Gets or creates a Guest account object.

        Keyword Args:
            ip (str, optional): IP address of requester; used for ban checking,
                throttling and logging

        Returns:
            account (Object): Guest account object, if available
            errors (list): List of error messages accrued during this request.

        """
        errors = []
        account = None
        username = None
        ip = kwargs.get("ip", "").strip()

        # check if guests are enabled.
        if not settings.GUEST_ENABLED:
            errors.append(_("Guest accounts are not enabled on this server."))
            return None, errors

        try:
            # Find an available guest name.
            for name in settings.GUEST_LIST:
                if not AccountDB.objects.filter(username__iexact=name).exists():
                    username = name
                    break
            if not username:
                errors.append(_("All guest accounts are in use. Please try again later."))
                if ip:
                    LOGIN_THROTTLE.update(ip, "Too many requests for Guest access.")
                return None, errors
            else:
                # build a new account with the found guest username
                password = "%016x" % getrandbits(64)
                home = settings.GUEST_HOME
                permissions = settings.PERMISSION_GUEST_DEFAULT
                typeclass = settings.BASE_GUEST_TYPECLASS

                # Call parent class creator
                account, errs = super(DefaultGuest, cls).create(
                    guest=True,
                    username=username,
                    password=password,
                    permissions=permissions,
                    typeclass=typeclass,
                    home=home,
                    ip=ip,
                )
                errors.extend(errs)

                if not account.characters:
                    # this can happen for multisession_mode > 1. For guests we
                    # always auto-create a character, regardless of multi-session-mode.
                    character, errs = account.create_character()

                if errs:
                    errors.extend(errs)

                return account, errors

        except Exception:
            # We are in the middle between logged in and -not, so we have
            # to handle tracebacks ourselves at this point. If we don't,
            # we won't see any errors at all.
            errors.append(_("An error occurred. Please e-mail an admin if the problem persists."))
            logger.log_trace()
            return None, errors

        return account, errors

    def at_post_login(self, session=None, **kwargs):
        """
        By default, Guests only have one character regardless of which
        MAX_NR_CHARACTERS we use. They also always auto-puppet a matching
        character and don't get a choice.

        Args:
            session (Session, optional): Session connecting.
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        self._send_to_connect_channel(_("|G{key} connected|n").format(key=self.key))
        self.puppet_object(session, self.db._last_puppet)

    def at_server_shutdown(self):
        """
        We repeat the functionality of `at_disconnect()` here just to
        be on the safe side.
        """
        super().at_server_shutdown()
        for character in self.characters:
            character.delete()

    def at_post_disconnect(self, **kwargs):
        """
        Once having disconnected, destroy the guest's characters and

        Args:
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        super().at_post_disconnect()
        for character in self.characters:
            character.delete()
        self.delete()
