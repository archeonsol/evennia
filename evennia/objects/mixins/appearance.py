"""Appearance mixin for DefaultObject."""

from collections import defaultdict

from django.conf import settings
from django.utils.translation import gettext as _

import inflect
from evennia.utils import ansi, logger
from evennia.utils.utils import compress_whitespace, is_iter, iter_to_str, make_iter

_INFLECT = inflect.engine()


class AppearanceMixin:
    """Mixin providing appearance and look-related methods for DefaultObject."""

    def filter_visible(self, obj_list, looker, **kwargs):
        """
        Filter a list of objects to only include those that are visible to the looker.

        Args:
            obj_list (list): List of objects to filter.
            looker (DefaultObject): Object doing the looking.
            **kwargs: Arbitrary data for use when overriding.
        Returns:
            list: The filtered list of visible objects.
        Notes:
            By default this simply checks the 'view' and 'search' locks on each object in the list.
            Override this
            method to implement custom visibility mechanics.

        """
        return [
            obj
            for obj in obj_list
            if obj != looker
            and (obj.access(looker, "view") and obj.access(looker, "search", default=True))
        ]

    # name and return_appearance hooks

    def get_display_name(self, looker=None, **kwargs):
        """
        Displays the name of the object in a viewer-aware manner.

        Args:
            looker (DefaultObject): The object or account that is looking at or getting information
                for this object.

        Returns:
            str: A name to display for this object. By default this returns the `.name` of the object.

        Notes:
            This function can be extended to change how object names appear to users in character,
            but it does not change an object's keys or aliases when searching.

        """
        return self.name

    def get_extra_display_name_info(self, looker=None, **kwargs):
        """
        Adds any extra display information to the object's name. By default this is is the
        object's dbref in parentheses, if the looker has permission to see it.

        Args:
            looker (DefaultObject): The object looking at this object.

        Returns:
            str: The dbref of this object, if the looker has permission to see it. Otherwise, an
            empty string is returned.

        Notes:
            By default, this becomes a string (#dbref) attached to the object's name.

        """
        if looker and self.locks.check_lockstring(looker, "perm(Builder)"):
            return f"(#{self.id})"
        return ""

    def get_numbered_name(self, count, looker, **kwargs):
        """
        Return the numbered (singular, plural) forms of this object's key. This is by default called
        by return_appearance and is used for grouping multiple same-named of this object. Note that
        this will be called on *every* member of a group even though the plural name will be only
        shown once. Also the singular display version, such as 'an apple', 'a tree' is determined
        from this method.

        Args:
            count (int): Number of objects of this type
            looker (DefaultObject): Onlooker. Not used by default.

        Keyword Args:
            key (str): Optional key to pluralize. If not given, the object's `.get_display_name()`
                method is used.
            return_string (bool): If `True`, return only the singular form if count is 0,1 or
                the plural form otherwise. If `False` (default), return both forms as a tuple.
            no_article (bool): If `True`, do not return an article if `count` is 1.

        Returns:
            tuple: This is a tuple `(str, str)` with the singular and plural forms of the key
            including the count.

        Examples:
        ::

            obj.get_numbered_name(3, looker, key="foo")
                  -> ("a foo", "three foos")
            obj.get_numbered_name(1, looker, key="Foobert", return_string=True)
                  -> "a Foobert"
            obj.get_numbered_name(1, looker, key="Foobert", return_string=True, no_article=True)
                  -> "Foobert"
        """
        key = kwargs.get("key", self.get_display_name(looker))
        raw_key = self.name
        key = ansi.ANSIString(key)  # this is needed to allow inflection of colored names
        try:
            plural = _INFLECT.plural(key, count)
            plural = "{} {}".format(_INFLECT.number_to_words(count, threshold=12), plural)
        except IndexError:
            # this is raised by inflect if the input is not a proper noun
            plural = key
        singular = _INFLECT.an(key)
        if not self.aliases.get(plural, category=self.plural_category):
            # we need to wipe any old plurals/an/a in case key changed in the interrim
            self.aliases.clear(category=self.plural_category)
            self.aliases.add(plural, category=self.plural_category)
            # save the singular form as an alias here too so we can display "an egg" and also
            # look at 'an egg'.
            self.aliases.add(singular, category=self.plural_category)

        if kwargs.get("no_article") and count == 1:
            if kwargs.get("return_string"):
                return key
            return key, key

        if kwargs.get("return_string"):
            return singular if count == 1 else plural

        return singular, plural

    def get_display_header(self, looker, **kwargs):
        """
        Get the 'header' component of the object description. Called by `return_appearance`.

        Args:
            looker (DefaultObject): Object doing the looking.
            **kwargs: Arbitrary data for use when overriding.
        Returns:
            str: The header display string.

        """
        return ""

    def get_display_desc(self, looker, **kwargs):
        """
        Get the 'desc' component of the object description. Called by `return_appearance`.

        Args:
            looker (DefaultObject): Object doing the looking.
            **kwargs: Arbitrary data for use when overriding.
        Returns:
            str: The desc display string.

        """
        return self.db.desc or self.default_description

    def get_display_exits(self, looker, **kwargs):
        """
        Get the 'exits' component of the object description. Called by `return_appearance`.

        Args:
            looker (DefaultObject): Object doing the looking.
            **kwargs: Arbitrary data for use when overriding.

        Keyword Args:
            exit_order (iterable of str): The order in which exits should be listed, with
                unspecified exits appearing at the end, alphabetically.

        Returns:
            str: The exits display data.

        Examples:
        ::

            For a room with exits in the order 'portal', 'south', 'north', and 'out':
                obj.get_display_name(looker, exit_order=('north', 'south'))
                    -> "Exits: north, south, out, and portal."  (markup not shown here)
        """

        def _sort_exit_names(names):
            exit_order = kwargs.get("exit_order")
            if not exit_order:
                return names
            sort_index = {name: key for key, name in enumerate(exit_order)}
            names = sorted(names)
            end_pos = len(sort_index)
            names.sort(key=lambda name: sort_index.get(name, end_pos))
            return names

        exits = self.filter_visible(self.contents_get(content_type="exit"), looker, **kwargs)
        exit_names = (exi.get_display_name(looker, **kwargs) for exi in exits)
        exit_names = iter_to_str(_sort_exit_names(exit_names), endsep=_(", and"))
        e = _("Exits")
        return f"|w{e}:|n {exit_names}" if exit_names else ""

    def get_display_characters(self, looker, **kwargs):
        """
        Get the 'characters' component of the object description. Called by `return_appearance`.

        Args:
            looker (DefaultObject): Object doing the looking.
            **kwargs: Arbitrary data for use when overriding.
        Returns:
            str: The character display data.

        """
        characters = self.filter_visible(
            self.contents_get(content_type="character"), looker, **kwargs
        )
        character_names = iter_to_str(
            (char.get_display_name(looker, **kwargs) for char in characters), endsep=_(", and")
        )
        c = _("Characters")
        return f"|w{c}:|n {character_names}" if character_names else ""

    def get_display_things(self, looker, **kwargs):
        """
        Get the 'things' component of the object description. Called by `return_appearance`.

        Args:
            looker (DefaultObject): Object doing the looking.
            **kwargs: Arbitrary data for use when overriding.
        Returns:
            str: The things display data.

        """
        # sort and handle same-named things
        things = self.filter_visible(self.contents_get(content_type="object"), looker, **kwargs)

        grouped_things = defaultdict(list)
        for thing in things:
            grouped_things[thing.get_display_name(looker, **kwargs)].append(thing)

        thing_names = []
        for thingname, thinglist in sorted(grouped_things.items()):
            nthings = len(thinglist)
            thing = thinglist[0]
            singular, plural = thing.get_numbered_name(nthings, looker, key=thingname)
            thing_names.append(singular if nthings == 1 else plural)
        thing_names = iter_to_str(thing_names, endsep=_(", and"))
        s = _("You see")
        return f"|w{s}:|n {thing_names}" if thing_names else ""

    def get_display_footer(self, looker, **kwargs):
        """
        Get the 'footer' component of the object description. Called by `return_appearance`.

        Args:
            looker (DefaultObject): Object doing the looking.
            **kwargs: Arbitrary data for use when overriding.
        Returns:
            str: The footer display string.

        """
        return ""

    def format_appearance(self, appearance, looker, **kwargs):
        """
        Final processing of the entire appearance string. Called by `return_appearance`.

        Args:
            appearance (str): The compiled appearance string.
            looker (DefaultObject): Object doing the looking.
            **kwargs: Arbitrary data for use when overriding.
        Returns:
            str: The final formatted output.

        """
        return compress_whitespace(appearance).strip()

    def return_appearance(self, looker, **kwargs):
        """
        Main callback used by 'look' for the object to describe itself.
        This formats a description. By default, this looks for the `appearance_template`
        string set on this class and populates it with formatting keys
        'name', 'desc', 'exits', 'characters', 'things' as well as
        (currently empty) 'header'/'footer'. Each of these values are
        retrieved by a matching method `.get_display_*`, such as `get_display_name`,
        `get_display_footer` etc.

        Args:
            looker (DefaultObject): Object doing the looking. Passed into all helper methods.
            **kwargs (dict): Arbitrary, optional arguments for users
                overriding the call. This is passed into all helper methods.

        Returns:
            str: The description of this entity. By default this includes
            the entity's name, description and any contents inside it.

        Notes:
            To simply change the layout of how the object displays itself (like
            adding some line decorations or change colors of different sections),
            you can simply edit `.appearance_template`. You only need to override
            this method (and/or its helpers) if you want to change what is passed
            into the template or want the most control over output.

        """

        if not looker:
            return ""

        # populate the appearance_template string.
        return self.format_appearance(
            self.appearance_template.format(
                name=self.get_display_name(looker, **kwargs),
                extra_name_info=self.get_extra_display_name_info(looker, **kwargs),
                desc=self.get_display_desc(looker, **kwargs),
                header=self.get_display_header(looker, **kwargs),
                footer=self.get_display_footer(looker, **kwargs),
                exits=self.get_display_exits(looker, **kwargs),
                characters=self.get_display_characters(looker, **kwargs),
                things=self.get_display_things(looker, **kwargs),
            ),
            looker,
            **kwargs,
        )

    def get_visible_contents(self, looker, **kwargs):
        """
        DEPRECATED
        Get all contents of this object that a looker can see (whatever that means, by default it
        checks the 'view' and 'search' locks and excludes the looker themselves), grouped by type.
        Helper method to return_appearance.

        Args:
            looker (DefaultObject): The entity looking.
            **kwargs: Passed from `return_appearance`. Unused by default.

        Returns:
            dict: A dict of lists categorized by type. Byt default this
            contains 'exits', 'characters' and 'things'. The elements of these
            lists are the actual objects.

        """

        def _filter_visible(obj_list):
            return [obj for obj in self.filter_visible(obj_list, looker, **kwargs) if obj != looker]

        return {
            "exits": _filter_visible(self.contents_get(content_type="exit")),
            "characters": _filter_visible(self.contents_get(content_type="character")),
            "things": _filter_visible(self.contents_get(content_type="object")),
        }

    def get_content_names(self, looker, **kwargs):
        """
        DEPRECATED
        Get the proper names for all contents of this object. Helper method
        for return_appearance.

        Args:
            looker (DefaultObject): The entity looking.
            **kwargs: Passed from `return_appearance`. Passed into
                `get_display_name` for each found entity.

        Returns:
            dict: A dict of lists categorized by type. Byt default this
            contains 'exits', 'characters' and 'things'. The elements
            of these lists are strings - names of the objects that
            can depend on the looker and also be grouped in the case
            of multiple same-named things etc.

        Notes:
            This method shouldn't add extra coloring to the names beyond what is
            already given by the .get_display_name() (and the .name field) already.
            Per-type coloring can be applied in `return_appearance`.

        """
        # a mapping {'exits': [...], 'characters': [...], 'things': [...]}
        contents_map = self.get_visible_contents(looker, **kwargs)

        character_names = [
            char.get_display_name(looker, **kwargs) for char in contents_map["characters"]
        ]
        exit_names = [exi.get_display_name(looker, **kwargs) for exi in contents_map["exits"]]

        # group all same-named things under one name
        things = defaultdict(list)
        for thing in contents_map["things"]:
            things[thing.get_display_name(looker, **kwargs)].append(thing)

        # pluralize same-named things
        thing_names = []
        for thingname, thinglist in sorted(things.items()):
            nthings = len(thinglist)
            thing = thinglist[0]
            singular, plural = thing.get_numbered_name(nthings, looker, key=thingname)
            thing_names.append(singular if nthings == 1 else plural)

        return {"exits": exit_names, "characters": character_names, "things": thing_names}

    def at_look(self, target, **kwargs):
        """
        Called when this object performs a look. It allows to
        customize just what this means. It will not itself
        send any data.

        Args:
            target (DefaultObject): The target being looked at. This is
                commonly an object or the current location. It will
                be checked for the "view" type access.
            **kwargs: Arbitrary, optional arguments for users
                overriding the call. This will be passed into
                return_appearance, get_display_name and at_desc but is not used
                by default.

        Returns:
            str: A ready-processed look string potentially ready to return to the looker.

        """
        if not target.access(self, "view"):
            try:
                return _("Could not view '{target_name}'.").format(
                    target_name=target.get_display_name(self, **kwargs)
                )
            except AttributeError:
                return _("Could not view '{target_name}'.").format(target_name=target.key)

        if getattr(settings, "LOOK_ATTR_PREFETCH_ENABLED", True):
            try:
                if hasattr(target, "attributes"):
                    target.attributes.get_all()
            except Exception:
                logger.log_trace("at_look attribute prefetch")

        description = target.return_appearance(self, **kwargs)

        # the target's at_desc() method.
        # this must be the last reference to target so it may delete itself when acted on.
        target.at_desc(looker=self, **kwargs)

        return description

    def at_desc(self, looker=None, **kwargs):
        """
        This is called whenever someone looks at this object.

        Args:
            looker (Object, optional): The object requesting the description.
            **kwargs: Arbitrary, optional arguments for users
                overriding the call (unused by default).

        """
        pass

    def at_pre_get(self, getter, **kwargs):
        """
        Called by the default `get` command before this object has been
        picked up.

        Args:
            getter (DefaultObject): The object about to get this object.
            **kwargs: Arbitrary, optional arguments for users
                overriding the call (unused by default).

        Returns:
            bool: If the object should be gotten or not.

        Notes:
            If this method returns False/None, the getting is cancelled
            before it is even started.
        """
        return True

    # deprecated
    at_before_get = at_pre_get

    def at_get(self, getter, **kwargs):
        """
        Called by the default `get` command when this object has been
        picked up.

        Args:
            getter (DefaultObject): The object getting this object.
            **kwargs: Arbitrary, optional arguments for users
                overriding the call (unused by default).

        Notes:
            This hook cannot stop the pickup from happening. Use
            permissions or the at_pre_get() hook for that.

        """
        pass

    def at_pre_give(self, giver, getter, **kwargs):
        """
        Called by the default `give` command before this object has been
        given.

        Args:
            giver (DefaultObject): The object about to give this object.
            getter (DefaultObject): The object about to get this object.
            **kwargs: Arbitrary, optional arguments for users
                overriding the call (unused by default).

        Returns:
            shouldgive (bool): If the object should be given or not.

        Notes:
            If this method returns `False` or `None`, the giving is cancelled
            before it is even started.

        """
        return True

    # deprecated
    at_before_give = at_pre_give

    def at_give(self, giver, getter, **kwargs):
        """
        Called by the default `give` command when this object has been
        given.

        Args:
            giver (DefaultObject): The object giving this object.
            getter (DefaultObject): The object getting this object.
            **kwargs: Arbitrary, optional arguments for users
                overriding the call (unused by default).

        Notes:
            This hook cannot stop the give from happening. Use
            permissions or the at_pre_give() hook for that.

        """
        pass

    def at_pre_drop(self, dropper, **kwargs):
        """
        Called by the default `drop` command before this object has been
        dropped.

        Args:
            dropper (DefaultObject): The object which will drop this object.
            **kwargs: Arbitrary, optional arguments for users
                overriding the call (unused by default).

        Returns:
            bool: If the object should be dropped or not.

        Notes:
            If this method returns `False` or `None`, the dropping is cancelled
            before it is even started.

        """
        if not self.access(dropper, "drop", default=False):
            dropper.msg(_("You cannot drop {obj}").format(obj=self.get_display_name(dropper)))
            return False
        return True

    # deprecated
    at_before_drop = at_pre_drop

    def at_drop(self, dropper, **kwargs):
        """
        Called by the default `drop` command when this object has been
        dropped.

        Args:
            dropper (DefaultObject): The object which just dropped this object.
            **kwargs: Arbitrary, optional arguments for users
                overriding the call (unused by default).

        Notes:
            This hook cannot stop the drop from happening. Use
            permissions or the at_pre_drop() hook for that.

        """
        pass

    def at_pre_say(self, message, **kwargs):
        """
        Before the object says something.

        This hook is by default used by the 'say' and 'whisper'
        commands as used by this command it is called before the text
        is said/whispered and can be used to customize the outgoing
        text from the object. Returning `None` aborts the command.

        Args:
            message (str): The suggested say/whisper text spoken by self.
        Keyword Args:
            whisper (bool): If True, this is a whisper rather than
                a say. This is sent by the whisper command by default.
                Other verbal commands could use this hook in similar
                ways.
            receivers (DefaultObject or iterable): If set, this is the target or targets for the
                say/whisper.

        Returns:
            str: The (possibly modified) text to be spoken.

        """
        return message

    # deprecated
    at_before_say = at_pre_say

    def at_say(
        self,
        message,
        msg_self=None,
        msg_location=None,
        receivers=None,
        msg_receivers=None,
        **kwargs,
    ):
        """
        Display the actual say (or whisper) of self.

        This hook should display the actual say/whisper of the object in its
        location.  It should both alert the object (self) and its
        location that some text is spoken.  The overriding of messages or
        `mapping` allows for simple customization of the hook without
        re-writing it completely.

        Args:
            message (str): The message to convey.
            msg_self (bool or str, optional): If boolean True, echo `message` to self. If a string,
                return that message. If False or unset, don't echo to self.
            msg_location (str, optional): The message to echo to self's location.
            receivers (DefaultObject or iterable, optional): An eventual receiver or receivers of the
                message (by default only used by whispers).
            msg_receivers(str): Specific message to pass to the receiver(s). This will parsed
                with the {receiver} placeholder replaced with the given receiver.
        Keyword Args:
            whisper (bool): If this is a whisper rather than a say. Kwargs
                can be used by other verbal commands in a similar way.
            mapping (dict): Pass an additional mapping to the message.

        Notes:


            Messages can contain {} markers. These are substituted against the values
            passed in the `mapping` argument.
            ::

                msg_self = 'You say: "{speech}"'
                msg_location = '{object} says: "{speech}"'
                msg_receivers = '{object} whispers: "{speech}"'

            Supported markers by default:

            - {self}: text to self-reference with (default 'You')
            - {speech}: the text spoken/whispered by self.
            - {object}: the object speaking.
            - {receiver}: replaced with a single receiver only for strings meant for a specific
              receiver (otherwise 'None').
            - {all_receivers}: comma-separated list of all receivers,
              if more than one, otherwise same as receiver
            - {location}: the location where object is.

        """
        msg_type = "say"
        if kwargs.get("whisper", False):
            # whisper mode
            msg_type = "whisper"
            msg_self = (
                _('{self} whisper to {all_receivers}, "|n{speech}|n"')
                if msg_self is True
                else msg_self
            )
            msg_receivers = msg_receivers or _('{object} whispers: "|n{speech}|n"')
            msg_location = None
        else:
            msg_self = _('{self} say, "|n{speech}|n"') if msg_self is True else msg_self
            msg_location = msg_location or _('{object} says, "{speech}"')
            msg_receivers = msg_receivers or message

        custom_mapping = kwargs.get("mapping", {})
        receivers = make_iter(receivers) if receivers else None
        location = self.location

        if msg_self:
            self_mapping = {
                "self": _("You"),
                "object": self.get_display_name(self),
                "location": location.get_display_name(self) if location else None,
                "receiver": None,
                "all_receivers": (
                    ", ".join(recv.get_display_name(self) for recv in receivers)
                    if receivers
                    else None
                ),
                "speech": message,
            }
            self_mapping.update(custom_mapping)
            self.msg(text=(msg_self.format_map(self_mapping), {"type": msg_type}), from_obj=self)

        if receivers and msg_receivers:
            receiver_mapping = {
                "self": _("You"),
                "object": None,
                "location": None,
                "receiver": None,
                "all_receivers": None,
                "speech": message,
            }
            for receiver in make_iter(receivers):
                individual_mapping = {
                    "object": self.get_display_name(receiver),
                    "location": location.get_display_name(receiver),
                    "receiver": receiver.get_display_name(receiver),
                    "all_receivers": (
                        ", ".join(recv.get_display_name(recv) for recv in receivers)
                        if receivers
                        else None
                    ),
                }
                receiver_mapping.update(individual_mapping)
                receiver_mapping.update(custom_mapping)
                receiver.msg(
                    text=(msg_receivers.format_map(receiver_mapping), {"type": msg_type}),
                    from_obj=self,
                )

        if self.location and msg_location:
            location_mapping = {
                "self": _("You"),
                "object": self,
                "location": location,
                "all_receivers": ", ".join(str(recv) for recv in receivers) if receivers else None,
                "receiver": None,
                "speech": message,
            }
            location_mapping.update(custom_mapping)
            exclude = []
            if msg_self:
                exclude.append(self)
            if receivers:
                exclude.extend(receivers)
            self.location.msg_contents(
                text=(msg_location, {"type": msg_type}),
                from_obj=self,
                exclude=exclude,
                mapping=location_mapping,
            )

    def at_rename(self, oldname, newname):
        """
        This Hook is called by @name on a successful rename.

        Args:
            oldname (str): The instance's original name.
            newname (str): The new name for the instance.

        """

        # Clear plural aliases set by DefaultObject.get_numbered_name
        self.aliases.clear(category=self.plural_category)
