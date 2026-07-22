"""
This module gathers all the essential database-creation functions for the game
engine's various object types.

Only objects created 'stand-alone' are in here. E.g. object Attributes are
always created through their respective objects handlers.

Each `creation_*` function also has an alias named for the entity being created,
such as create_object() and object(). This is for consistency with the
utils.search module and allows you to do the shorter `create.object()`.

The respective object managers hold more methods for manipulating and searching
objects already existing in the database.

"""

from django.utils.functional import SimpleLazyObject

# limit symbol import from API
__all__ = (
    "create_object",
    "create_script",
    "create_help_entry",
    "create_message",
    "create_channel",
    "create_account",
)

_GA = object.__getattribute__


# Lazy-loaded model classes
def _get_objectdb():
    from django.contrib.contenttypes.models import ContentType

    return ContentType.objects.get(app_label="objects", model="objectdb").model_class()


def _get_scriptdb():
    from django.contrib.contenttypes.models import ContentType

    return ContentType.objects.get(app_label="scripts", model="scriptdb").model_class()


def _get_accountdb():
    from django.contrib.contenttypes.models import ContentType

    return ContentType.objects.get(
        app_label="accounts", model="accountdb"
    ).model_class()


def _get_msg():
    from django.contrib.contenttypes.models import ContentType

    return ContentType.objects.get(app_label="comms", model="msg").model_class()


def _get_channeldb():
    from django.contrib.contenttypes.models import ContentType

    return ContentType.objects.get(app_label="comms", model="channeldb").model_class()


def _get_helpentry():
    from django.contrib.contenttypes.models import ContentType

    return ContentType.objects.get(app_label="help", model="helpentry").model_class()


def _get_tag():
    from django.contrib.contenttypes.models import ContentType

    return ContentType.objects.get(app_label="typeclasses", model="tag").model_class()


# Lazy model instances
ObjectDB = SimpleLazyObject(_get_objectdb)
ScriptDB = SimpleLazyObject(_get_scriptdb)
AccountDB = SimpleLazyObject(_get_accountdb)
Msg = SimpleLazyObject(_get_msg)
ChannelDB = SimpleLazyObject(_get_channeldb)
HelpEntry = SimpleLazyObject(_get_helpentry)
Tag = SimpleLazyObject(_get_tag)


def create_object(*args, **kwargs):
    """
    Create a new in-game object.

    Keyword Args:
        typeclass (class or str): Class or python path to a typeclass.
        key (str): Name of the new object. If not set, a name of
            `#dbref` will be set.
        location (Object or str): Obj or #dbref to use as the location of the new object.
        home (Object or str): Obj or #dbref to use as the object's home location.
        policies (dict): Typed operation-to-Policy overrides.
        aliases (list): A list of alternative keys or tuples (aliasstring, category).
        tags (list): List of tag keys or tuples (tagkey, category) or (tagkey, category, data).
        destination (Object or str): Obj or #dbref to use as an Exit's target.
        report_to (Object): The object to return error messages to.
        nohome (bool): This allows the creation of objects without a
            default home location; only used when creating the default
            location itself or during unittests.
        attributes (list): Tuples on the form `(key, value)` or
            `(key, value, category)` to set as Attributes on the new object.
        nattributes (list): Non-persistent tuples on the form (key, value). Note that
            adding this rarely makes sense since this data will not survive a reload.

    Returns:
        object (Object): A newly created object of the given typeclass.

    Raises:
        ObjectDB.DoesNotExist: If trying to create an Object with
            `location` or `home` that can't be found.
    """
    return ObjectDB.objects.create_object(*args, **kwargs)


def create_script(*args, **kwargs):
    """
    Create a new script. All scripts are a combination of a database
    object that communicates with the database, and a typeclass that
    'decorates' the database object. A Script is a typeclassed storage
    container with no timer component.

    Keyword Args:
        typeclass (class or str): Class or python path to a typeclass.
        key (str): Name of the new object. If not set, a name of
            #dbref will be set.
        obj (Object): The entity on which this Script sits. If this
            is `None`, we are creating a "global" script.
        account (Account): The account on which this Script sits. It is
            exclusiv to `obj`.
        policies (dict): Typed operation-to-Policy overrides.
        persistent (bool): If this Script survives a server shutdown
            or not (all Scripts will survive a reload).
        report_to (Object): The object to return error messages to.
        desc (str): Optional description of script
        tags (list): List of tags or tuples (tag, category).
        attributes (list): Tuples on the form `(key, value)` or
            `(key, value, category)`.

    Returns:
        script (obj): An instance of the script created

    See evennia.scripts.manager for methods to manipulate existing
    scripts in the database.
    """
    return ScriptDB.objects.create_script(*args, **kwargs)


def create_help_entry(*args, **kwargs):
    """
    Create a static help entry in the help database. Note that Command
    help entries are dynamic and directly taken from the __doc__
    entries of the command. The database-stored help entries are
    intended for more general help on the game, more extensive info,
    in-game setting information and so on.

    Args:
        key (str): The name of the help entry.
        entrytext (str): The body of te help entry
        category (str, optional): The help category of the entry.
        policies (dict, optional): Typed operation policies.
        aliases (list of str, optional): List of alternative (likely shorter) keynames.
        tags (lst, optional): List of tags or tuples `(tag, category)`.

    Returns:
        help (HelpEntry): A newly created help entry.
    """
    return HelpEntry.objects.create_help(*args, **kwargs)


def create_message(*args, **kwargs):
    """
    Create a new communication Msg. Msgs represent a unit of
    database-persistent communication between entites.

    Args:
        senderobj (Object, Account, Script, str or list): The entity (or
            entities) sending the Msg. If a `str`, this is the id-string
            for an external sender type.
        message (str): Text with the message. Eventual headers, titles
            etc should all be included in this text string. Formatting
            will be retained.
        receivers (Object, Account, Script, str or list): An Account/Object to send
            to, or a list of them. If a string, it's an identifier for an external
            receiver.
        policies (dict): Operation-to-Policy overrides. Values may be typed
            Policy nodes or serialized policy mappings.
        tags (list): A list of tags or tuples `(tag, category)`.
        header (str): Mime-type or other optional information for the message

    Notes:
        The Comm system is created to be very open-ended, so it's fully
        possible to let a message both go several receivers at the same time,
        it's up to the command definitions to limit this as desired.
    """
    return Msg.objects.create_message(*args, **kwargs)


def create_channel(*args, **kwargs):
    """
    Create A communication Channel. A Channel serves as a central hub
    for distributing Msgs to groups of people without specifying the
    receivers explicitly. Instead accounts may 'connect' to the channel
    and follow the flow of messages. By default the channel allows
    access to all old messages, but this can be turned off with the
    keep_log switch.

    Args:
        key (str): This must be unique.

    Keyword Args:
        aliases (list of str): List of alternative (likely shorter) keynames.
        desc (str): A description of the channel, for use in listings.
        policies (dict): Typed operation-to-Policy overrides.
        keep_log (bool): Log channel throughput.
        typeclass (str or class): The typeclass of the Channel (not
            often used).
        tags (list): A list of tags or tuples `(tag[,category[,data]])`.
        attrs (list): List of attributes on form `(name, value[,category[,lockstring]])`

    Returns:
        channel (Channel): A newly created channel.
    """
    return ChannelDB.objects.create_channel(*args, **kwargs)


def create_account(*args, **kwargs):
    """
    This creates a new account.

    Args:
        key (str): The account's name. This should be unique.
        email (str or None): Email on valid addr@addr.domain form. If
            the empty string, will be set to None.
        password (str): Password in cleartext.

    Keyword Args:
        typeclass (str): The typeclass to use for the account.
        is_superuser (bool): Whether or not this account is to be a superuser
        policies (dict): Typed operation-to-Policy overrides.
        tags (list): List of Tags on form `(key, category[, data])`
        attributes (list): Attributes on form `(key, value[, category])`.
        report_to (Object): An object with a msg() method to report
            errors to. If not given, errors will be logged.

    Returns:
        Account: The newly created Account.
    Raises:
        ValueError: If `key` already exists in database.

    Notes:
        ``is_superuser`` grants Django administration only. Game authority
        requires explicit capability grants.
    """
    return AccountDB.objects.create_account(*args, **kwargs)


# Aliases for the creation functions
object = create_object
script = create_script
help_entry = create_help_entry
message = create_message
channel = create_channel
account = create_account
