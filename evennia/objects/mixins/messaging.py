"""Messaging mixin for DefaultObject."""

from django.conf import settings
from django.utils.translation import gettext as _

from evennia.utils import funcparser, logger
from evennia.utils.utils import is_iter, make_iter, to_str

_CMDHANDLER = None
_MSG_CONTENTS_PARSER = funcparser.FuncParser(funcparser.ACTOR_STANCE_CALLABLES)


class MessagingMixin:
    """Mixin providing messaging-related methods for DefaultObject."""

    def execute_cmd(self, raw_string, session=None, **kwargs):
        """
        Do something as this object. This is never called normally,
        it's only used when wanting specifically to let an object be
        the caller of a command. It makes use of nicks of eventual
        connected accounts as well.

        Args:
            raw_string (string): Raw command input
            session (Session, optional): Session to
                return results to

        Keyword Args:
            Other keyword arguments will be added to the found command
            object instace as variables before it executes.  This is
            unused by default Evennia but may be used to set flags and
            change operating paramaters for commands at run-time.

        Returns:
            Deferred: This is an asynchronous Twisted object that
            will not fire until the command has actually finished
            executing. To overload this one needs to attach
            callback functions to it, with addCallback(function).
            This function will be called with an eventual return
            value from the command execution. This return is not
            used at all by Evennia by default, but might be useful
            for coders intending to implement some sort of nested
            command structure.

        """
        # break circular import issues
        global _CMDHANDLER
        if not _CMDHANDLER:
            from evennia.commands.cmdhandler import cmdhandler as _CMDHANDLER

        # nick replacement - we require full-word matching.
        # do text encoding conversion
        raw_string = self.nicks.nickreplace(
            raw_string, categories=("inputline", "channel"), include_account=True
        )
        return _CMDHANDLER(self, raw_string, callertype="object", session=session, **kwargs)

    def msg(self, text=None, from_obj=None, session=None, options=None, **kwargs):
        """
        Emits something to a session attached to the object.

        Keyword Args:
            text (str or tuple): The message to send. This
                is treated internally like any send-command, so its
                value can be a tuple if sending multiple arguments to
                the `text` oob command.
            from_obj (DefaultObject, DefaultAccount, Session, or list): object that is sending. If
                given, at_msg_send will be called. This value will be
                passed on to the protocol. If iterable, will execute hook
                on all entities in it.
            session (Session or list): Session or list of
                Sessions to relay data to, if any. If set, will force send
                to these sessions. If unset, who receives the message
                depends on the MULTISESSION_MODE.
            options (dict): Message-specific option-value
                pairs. These will be applied at the protocol level.
            **kwargs (string or tuples): All kwarg keys not listed above
                will be treated as send-command names and their arguments
                (which can be a string or a tuple).

        Notes:
            The `at_msg_receive` method will be called on this Object.
            All extra kwargs will be passed on to the protocol.

        """
        # try send hooks
        if from_obj:
            for obj in make_iter(from_obj):
                try:
                    obj.at_msg_send(text=text, to_obj=self, **kwargs)
                except Exception:
                    logger.log_trace()
        kwargs["options"] = options
        try:
            if not self.at_msg_receive(text=text, from_obj=from_obj, **kwargs):
                # if at_msg_receive returns false, we abort message to this object
                return
        except Exception:
            logger.log_trace()

        if text is not None:
            if not (isinstance(text, str) or isinstance(text, tuple)):
                # sanitize text before sending across the wire
                try:
                    text = to_str(text)
                except Exception:
                    text = repr(text)
            kwargs["text"] = text

        # relay to session(s)
        sessions = make_iter(session) if session else self.sessions.all()
        for session in sessions:
            session.data_out(**kwargs)

    def for_contents(self, func, exclude=None, **kwargs):
        """
        Runs a function on every object contained within this one.

        Args:
            func (callable): Function to call. This must have the
                formal call sign func(obj, **kwargs), where obj is the
                object currently being processed and `**kwargs` are
                passed on from the call to `for_contents`.
            exclude (list, optional): A list of object not to call the
                function on.

        Keyword Args:
            Keyword arguments will be passed to the function for all objects.

        """
        contents = self.contents
        if exclude:
            exclude = make_iter(exclude)
            contents = [obj for obj in contents if obj not in exclude]
        for obj in contents:
            func(obj, **kwargs)

    def get_message_recipients(self, exclude=None):
        """
        Objects that receive ``msg_contents`` broadcasts from this location.

        Override in game rooms to filter by distance, subzones, etc.

        Args:
            exclude (list, optional): Objects to omit (same as ``msg_contents``).

        Returns:
            list: Recipients (defaults to ``self.contents`` minus ``exclude``).
        """
        recipients = self.contents
        if exclude:
            exclude = make_iter(exclude)
            recipients = [obj for obj in recipients if obj not in exclude]
        return recipients

    def msg_contents(
        self,
        text=None,
        exclude=None,
        from_obj=None,
        mapping=None,
        raise_funcparse_errors=False,
        **kwargs,
    ):
        """
        Emits a message to all objects inside this object.

        Args:
            text (str or tuple): Message to send. If a tuple, this should be
                on the valid OOB outmessage form `(message, {kwargs})`,
                where kwargs are optional data passed to the `text`
                outputfunc. The message will be parsed for `{key}` formatting and
                `$You/$you()/$You()`, `$obj(name)`, `$conj(verb)` and `$pron(pronoun, option)`
                inline function callables.
                The `name` is taken from the `mapping` kwarg {"name": object, ...}`.
                The `mapping[key].get_display_name(looker=recipient)` will be called
                for that key for every recipient of the string.
            exclude (list, optional): A list of objects not to send to.
            from_obj (Object, optional): An object designated as the
                "sender" of the message. See `DefaultObject.msg()` for
                more info. This will be used for `$You/you` if using funcparser inlines.
            mapping (dict, optional): A mapping of formatting keys
                `{"key":<object>, "key2":<object2>,...}.
                The keys must either match `{key}` or `$You(key)/$you(key)` markers
                in the `text` string. If `<object>` doesn't have a `get_display_name`
                method, it will be returned as a string. Pass "you" to represent the caller,
                this can be skipped if `from_obj` is provided (that will then act as 'you').
            raise_funcparse_errors (bool, optional): If set, a failing `$func()` will
                lead to an outright error. If unset (default), the failing `$func()`
                will instead appear in output unparsed.

            **kwargs: Keyword arguments will be passed on to `obj.msg()` for all
                messaged objects.

        Notes:
            For 'actor-stance' reporting (You say/Name says), use the
            `$You()/$you()/$You(key)` and `$conj(verb)` (verb-conjugation)
            inline callables. This will use the respective `get_display_name()`
            for all onlookers except for `from_obj or self`, which will become
            'You/you'. If you use `$You/you(key)`, the key must be in `mapping`.

            For 'director-stance' reporting (Name says/Name says), use {key}
            syntax directly. For both `{key}` and `You/you(key)`,
            `mapping[key].get_display_name(looker=recipient)` may be called
            depending on who the recipient is.

        Examples:

            Let's assume:

            - `player1.key` -> "Player1",
            - `player1.get_display_name(looker=player2)` -> "The First girl"
            - `player2.key` -> "Player2",
            - `player2.get_display_name(looker=player1)` -> "The Second girl"

            Actor-stance:
            ::

                char.location.msg_contents(
                    "$You() $conj(attack) $you(defender).",
                    from_obj=player1,
                    mapping={"defender": player2})

            - player1 will see `You attack The Second girl.`
            - player2 will see 'The First girl attacks you.'

            Director-stance:
            ::

                char.location.msg_contents(
                    "{attacker} attacks {defender}.",
                    mapping={"attacker":player1, "defender":player2})

            - player1 will see: 'Player1 attacks The Second girl.'
            - player2 will see: 'The First girl attacks Player2'

        """
        # we also accept an outcommand on the form (message, {kwargs})
        is_outcmd = text and is_iter(text)
        inmessage = text[0] if is_outcmd else text
        outkwargs = text[1] if is_outcmd and len(text) > 1 else {}
        mapping = mapping or {}
        you = from_obj or self

        if "you" not in mapping:
            mapping["you"] = you

        contents = self.get_message_recipients(exclude=exclude)

        try:
            from evennia.utils.display_name_cache import cached_get_display_name
        except Exception:

            def cached_get_display_name(obj, looker, **kw):
                return (
                    obj.get_display_name(looker=looker, **kw)
                    if hasattr(obj, "get_display_name")
                    else str(obj)
                )

        display_names_by_receiver = {}
        for receiver in contents:
            display_names_by_receiver[id(receiver)] = {
                key: (
                    cached_get_display_name(obj, receiver)
                    if hasattr(obj, "get_display_name")
                    else str(obj)
                )
                for key, obj in mapping.items()
            }

        use_funcparser = settings.FUNCPARSER_START_CHAR in (inmessage or "")

        for receiver in contents:
            names = display_names_by_receiver[id(receiver)]

            if use_funcparser:
                outmessage = _MSG_CONTENTS_PARSER.parse(
                    inmessage,
                    raise_errors=raise_funcparse_errors,
                    return_string=True,
                    caller=you,
                    receiver=receiver,
                    mapping=mapping,
                    display_names=names,
                )
            else:
                outmessage = inmessage

            outmessage = outmessage.format_map(names)

            receiver.msg(text=(outmessage, outkwargs), from_obj=from_obj, **kwargs)
