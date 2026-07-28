"""Messaging mixin for DefaultObject."""

import re

from django.conf import settings
from django.utils.translation import gettext as _

from evennia.hooks import hook
from evennia.utils import funcparser, logger
from evennia.utils.utils import is_iter, make_iter, to_str

_CMDHANDLER = None
_MSG_CONTENTS_PARSER = funcparser.FuncParser(funcparser.ACTOR_STANCE_CALLABLES)
_TEMPLATE_KEY = re.compile(r"\{(\w+)\}")


def _template_plan(inmessage, mapping, outkwargs, from_obj):
    """Turn a director-stance ``{key}`` template into a canonical render plan.

    The migration lever for the bulk of existing call sites: no caller changes,
    but ``"{attacker} attacks {defender}."`` stops being flattened per receiver
    and becomes literal text around two entity references, resolved at the
    delivery boundary like any other R1 event.

    Returns:
        RenderPlan or None: ``None`` when the template cannot be structuralized
        faithfully (an unmapped key, or a mapping value that is not an entity),
        in which case the caller keeps the legacy per-receiver path.
    """
    from evennia.narrative.plan import CharRef, Line, ObjectRef, RenderPlan, TextSpan

    if not inmessage:
        return None
    spans = []
    position = 0
    for match in _TEMPLATE_KEY.finditer(inmessage):
        key = match.group(1)
        if key not in mapping:
            return None  # format_map would raise; preserve that behaviour
        target = mapping[key]
        entity_id = getattr(target, "id", None)
        if entity_id is None or not hasattr(target, "get_display_name"):
            return None  # a plain string/number: no identity to preserve
        if match.start() > position:
            spans.append(TextSpan(inmessage[position : match.start()]))
        try:
            is_character = target.is_typeclass(
                "evennia.objects.objects.DefaultCharacter", exact=False
            )
        except Exception:
            is_character = getattr(target, "account", None) is not None
        spans.append(
            CharRef(char_id=entity_id, role=key)
            if is_character
            else ObjectRef(object_id=entity_id, kind="object", role=key)
        )
        position = match.end()
    if not any(isinstance(span, (CharRef, ObjectRef)) for span in spans):
        return None  # nothing to keep unresolved; legacy path is equivalent
    if position < len(inmessage):
        spans.append(TextSpan(inmessage[position:]))
    sender = make_iter(from_obj)[0] if from_obj else None
    return RenderPlan(
        kind=str(outkwargs.get("type") or "broadcast"),
        msg_type=str(outkwargs.get("type") or "text"),
        blocks=(Line(spans=tuple(spans)),),
        subject_id=getattr(sender, "id", None),
        metadata={"surface": "msg_contents", "third_person": True},
    )


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
        # cmdhandler is now an ``async def``; bridge the coroutine back to a
        # Deferred so the documented addCallback contract keeps working.
        from evennia.utils import clock

        return clock.run_coroutine(
            _CMDHANDLER(self, raw_string, callertype="object", session=session, **kwargs),
            task_kind="command",
        )

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
        # R1 universal facade: callers may pass an immutable RenderNode directly.
        # Hooks and transforms still run exactly once before protocol fan-out.
        from evennia.narrative.rendernode import RenderNode, deliver_node, text_node

        render_delivery = bool(kwargs.pop("_render_delivery", False))

        # try send hooks once. Recursive text/structured sends from deliver_node
        # carry the private marker above and bypass these hooks.
        if from_obj and not render_delivery:
            for obj in make_iter(from_obj):
                try:
                    obj.at_msg_send(text=text, to_obj=self, **kwargs)
                except Exception:
                    logger.log_trace()
        kwargs["options"] = options
        try:
            if not render_delivery and not self.at_msg_receive(
                text=text, from_obj=from_obj, **kwargs
            ):
                # if at_msg_receive returns false, we abort message to this object
                return
        except Exception:
            logger.log_trace()

        if isinstance(text, RenderNode) and not render_delivery:
            sessions = make_iter(session) if session else None
            passthrough = {key: value for key, value in kwargs.items() if key != "options"}
            return deliver_node(
                text,
                self,
                from_obj=from_obj,
                sessions=sessions,
                options=options,
                transform_context={"hooks_applied": True},
                **passthrough,
            )

        target_sessions = list(make_iter(session) if session else self.sessions.all())
        # R1: *every* message normalizes through the node core, not only those
        # bound for a structured client. Protocol capability decides the final
        # flattening step and nothing else, so timelines, sinks, telemetry and
        # universal transforms see a telnet recipient's traffic identically.
        if text is not None and not render_delivery:
            body = text
            metadata = {}
            if isinstance(text, tuple):
                body = text[0] if text else ""
                if len(text) > 1 and isinstance(text[1], dict):
                    metadata = text[1]
            option_type = options.get("type") if isinstance(options, dict) else None
            msg_type = str(metadata.get("type") or option_type or "text")
            # Other outputfuncs in the same call (prompt, oob data) still ride
            # along; only the text argument is what the node core owns.
            passthrough = {key: value for key, value in kwargs.items() if key != "options"}
            # Any metadata beyond `type` is the caller's outputfunc contract and
            # is restored verbatim on the way out.
            extra_meta = {key: value for key, value in metadata.items() if key != "type"}
            return deliver_node(
                text_node(
                    str(body),
                    msg_type=msg_type,
                    metadata={"text_kwargs": extra_meta} if extra_meta else {},
                ),
                self,
                from_obj=from_obj,
                sessions=target_sessions,
                options=options,
                transform_context={"hooks_applied": True},
                **passthrough,
            )

        if text is not None:
            if not (isinstance(text, str) or isinstance(text, tuple)):
                # sanitize text before sending across the wire
                try:
                    text = to_str(text)
                except Exception:
                    text = repr(text)
            kwargs["text"] = text

        # relay to session(s)
        for current in target_sessions:
            current.data_out(**kwargs)

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

    @hook(
        event="msg_contents",
        phase="composite",
        actor="self",
        returns="content",
        discipline="public",
        fires_from=("MessagingMixin.msg_contents",),
        notes="Resolves the recipient set for a msg_contents broadcast.",
    )
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

        use_funcparser = settings.FUNCPARSER_START_CHAR in (inmessage or "")
        if not use_funcparser:
            # R1: a director-stance template plus a mapping *is* a span plan --
            # literal text around entity references. Build it once, resolve it
            # per recipient at delivery, and identity survives to the boundary
            # instead of being destroyed by an early format_map.
            plan = _template_plan(inmessage, mapping, outkwargs, from_obj)
            if plan is not None:
                from evennia.narrative.plan import deliver_to

                deliver_to(plan, contents, from_obj=from_obj, **kwargs)
                return plan
            # Identity-free room output is still one canonical event, not one
            # unrelated text node per recipient. This gives relays, cameras,
            # accessibility and forensic sinks the same source object even
            # when the authoring API was the old string facade.
            if isinstance(inmessage, str):
                from evennia.narrative.plan import deliver_to, text_plan

                msg_type = str(outkwargs.get("type") or "text")
                plan = text_plan(
                    inmessage,
                    kind=msg_type if msg_type != "text" else "broadcast",
                    msg_type=msg_type,
                    subject_id=getattr(make_iter(from_obj)[0] if from_obj else None, "id", None),
                    metadata={"surface": "msg_contents"},
                )
                deliver_to(plan, contents, from_obj=from_obj, **kwargs)
                return plan

        display_names_by_receiver = {}
        for receiver in contents:
            display_names_by_receiver[id(receiver)] = {
                key: (
                    obj.get_display_name(looker=receiver)
                    if hasattr(obj, "get_display_name")
                    else str(obj)
                )
                for key, obj in mapping.items()
            }

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

            from evennia.narrative.handles import handle_for
            from evennia.narrative.rendernode import EntityRef, RenderNode

            refs = []
            seen = set()
            for role, obj in mapping.items():
                entity_id = getattr(obj, "id", None)
                if entity_id is None or entity_id in seen:
                    continue
                seen.add(entity_id)
                label = names.get(role) or str(obj)
                refs.append(
                    EntityRef(
                        handle=handle_for(receiver, obj, label),
                        label=label,
                        role=str(role),
                    )
                )
            sender = make_iter(from_obj)[0] if from_obj else None
            sender_name = (
                sender.get_display_name(looker=receiver)
                if sender is not None and hasattr(sender, "get_display_name")
                else ""
            )
            node = RenderNode(
                kind=str(outkwargs.get("type") or "broadcast"),
                msg_type=str(outkwargs.get("type") or "text"),
                body=outmessage,
                from_handle=(
                    handle_for(receiver, sender, sender_name) if sender is not None else None
                ),
                refs=tuple(refs),
                metadata={"surface": "msg_contents"},
            )
            # Actor-stance ($You/$conj) output is viewer-shaped by construction,
            # so it stays per-receiver -- but it still crosses the one delivery
            # boundary, so the universal transforms apply here too.
            from evennia.narrative.plan import deliver_resolved

            deliver_resolved(node, receiver, from_obj=from_obj, **kwargs)
