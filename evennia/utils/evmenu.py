"""
Input-prompt helpers (`get_input`, `ask_yes_no`).

These are the surviving, engine-native pieces of the old ``evmenu`` module. The
legacy ``EvMenu`` node-graph menu (and its ``EvMenuCmdSet`` input-capture cmdset)
was removed: under the action engine the menu cmdset is never merged, so the
node graph could not receive input. Branching, interactive flows are now written
as ``@interactive`` generator screens that ``yield`` an
:class:`evennia.actions.menus.MenuPrompt` (see :mod:`evennia.actions.menus`).

``get_input`` and ``ask_yes_no`` remain because they are simple, broadly used
input prompts. Both install an engine ``StateProvider``
(:class:`~evennia.actions.menus.GetInputState` /
:class:`~evennia.actions.menus.YesNoState`) on the caller's focus body, so the
action engine routes the next input line to the waiting callback.
"""


def get_input(caller, prompt, callback, session=None, *args, **kwargs):
    """
    This is a helper function for easily request input from the caller.

    Args:
        caller (Account or Object): The entity being asked the question. This
            should usually be an object controlled by a user.
        prompt (str): This text will be shown to the user, in order to let them
            know their input is needed.
        callback (callable): A function that will be called
            when the user enters a reply. It must take three arguments: the
            `caller`, the `prompt` text and the `result` of the input given by
            the user. If the callback doesn't return anything or return False,
            the input prompt will be cleaned up and exited. If returning True,
            the prompt will remain and continue to accept input.
        session (Session, optional): This allows to specify the
            session to send the prompt to. It's usually only needed if `caller`
            is an Account in multisession modes greater than 2. Pass it through
            `kwargs` if the callback itself needs the answering session.
        *args (any): Extra arguments to pass to `callback`.  To utilise `*args`
            (and `**kwargs`), a value for the `session` argument must also be
            provided.
        **kwargs (any): Extra kwargs to pass to `callback`.

    Raises:
        RuntimeError: If the given callback is not callable.

    Notes:
        The result value sent to the callback is raw and not processed in any
        way. This means that you will get the ending line return character from
        most types of client inputs. So make sure to strip that before doing a
        comparison.

        While the prompt is running it is backed by a `GetInputState`
        capturing state on the caller's actor (not an ndb attribute or a
        cmdset); the action engine routes the next input line to it.

        A new `get_input` on the same caller replaces any still-active one
        (exit-before-enter), so it will not stack.

    """
    if not callable(callback):
        raise RuntimeError("get_input: input callback is not callable.")
    from evennia.actions.menus import GetInputState
    from evennia.actions.state import capture_holder, enter_state, exit_state

    # Install on the focus body the engine will read for the next line (not the
    # raw caller, which can differ from the focus - e.g. an account caller while
    # a character is puppeted). See capture_holder.
    holder = capture_holder(caller, session)
    # Avoid stacking; the legacy InputCmdSet used Replace for the same reason.
    exit_state(holder, GetInputState)
    enter_state(
        holder,
        GetInputState(caller, prompt, callback, session=session, args=args, kwargs=kwargs),
    )
    caller.msg(prompt, session=session)


def ask_yes_no(
    caller,
    prompt="Yes or No {options}?",
    yes_action="Yes",
    no_action="No",
    default=None,
    allow_abort=False,
    session=None,
    *args,
    **kwargs,
):
    """
    A helper function for asking a simple yes/no question. This will cause
    the system to pause and wait for input from the player.

    Args:
        caller (Object): The entity being asked.
        prompt (str): The yes/no question to ask. This takes an optional formatting
            marker `{options}` which will be filled with 'Y/N', '[Y]/N' or
            'Y/[N]' depending on the setting of `default`. If `allow_abort` is set,
            then the 'A(bort)' option will also be available.
        yes_action (callable or str): If a callable, this will be called
            with `(caller, *args, **kwargs)` when the Yes-choice is made.
            If a string, this string will be echoed back to the caller.
        no_action (callable or str): If a callable, this will be called
            with `(caller, *args, **kwargs)` when the No-choice is made.
            If a string, this string will be echoed back to the caller.
        default (str optional): This is what the user will get if they just press the
            return key without giving any input. One of 'N', 'Y', 'A' or `None`
            for no default (an explicit choice must be given). If 'A' (abort)
            is given, `allow_abort` kwarg is ignored and assumed set.
        allow_abort (bool, optional): If set, the 'A(bort)' option is available
            (a third option meaning neither yes or no but just exits the prompt).
        session (Session, optional): This allows to specify the
            session to send the prompt to. It's usually only needed if `caller`
            is an Account in multisession modes greater than 2. The answering
            session is passed to callbacks as `kwargs["caller_session"]`.
        *args: Additional arguments passed on into callables.
        **kwargs: Additional keyword args passed on into callables.

    Raises:
        RuntimeError, FooError: If default and `allow_abort` clashes.

    Example:
        ::

            # just returning strings
            ask_yes_no(caller, "Are you happy {options}?",
                       "you answered yes", "you answered no")
            # trigger callables
            ask_yes_no(caller, "Are you sad {options}?",
                       _callable_yes, _callable_no, allow_abort=True)

    """

    def _callable_yes_txt(caller, *args, **kwargs):
        yes_txt = kwargs["yes_txt"]
        session = kwargs["caller_session"]
        caller.msg(yes_txt, session=session)

    def _callable_no_txt(caller, *args, **kwargs):
        no_txt = kwargs["no_txt"]
        session = kwargs["caller_session"]
        caller.msg(no_txt, session=session)

    if not callable(yes_action):
        kwargs["yes_txt"] = str(yes_action)
        yes_action = _callable_yes_txt

    if not callable(no_action):
        kwargs["no_txt"] = str(no_action)
        no_action = _callable_no_txt

    # prepare the prompt with options
    options = "Y/N"
    abort_txt = "/Abort" if allow_abort else ""
    if default:
        default = default.lower()
        if default == "y":
            options = "[Y]/N"
        elif default == "n":
            options = "Y/[N]"
        elif default == "a":
            allow_abort = True
            abort_txt = "/[A]bort"
    options += abort_txt
    prompt = prompt.format(options=options)

    from evennia.actions.menus import YesNoState
    from evennia.actions.state import capture_holder, enter_state, exit_state

    # Install on the focus body the engine will read for the next line; see
    # capture_holder (and get_input above) for why the raw caller is wrong.
    holder = capture_holder(caller, session)
    # Avoid stacking; the legacy YesNoQuestionCmdSet used Replace for the same reason.
    exit_state(holder, YesNoState)
    enter_state(
        holder,
        YesNoState(
            caller,
            prompt,
            yes_action,
            no_action,
            default=default,
            allow_abort=allow_abort,
            session=session,
            args=args,
            kwargs=kwargs,
        ),
    )
    caller.msg(prompt, session=session)
