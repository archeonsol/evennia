"""
Cmdhandler signals.

Django signals fired by the cmdhandler around per-command dispatch.
Subscribe to these instead of monkey-patching ``Command`` or
``cmdhandler`` internals.

All three signals are dispatched with ``send_robust``: a receiver that
raises will have its exception returned in the response list, but will
not break command dispatch for other receivers or for the user.

The ``sender`` of every signal is the concrete ``Command`` subclass
(``type(cmd)``), so receivers can filter by Command class::

    from evennia.commands.signals import on_command_post

    @receiver(on_command_post, sender=CmdLook)
    def slow_look(sender, cmd, elapsed_ms, **kwargs):
        if elapsed_ms > 50:
            logger.log_warn(...)

Signal kwargs (always pass by name; receivers should accept ``**kwargs``
to remain forward-compatible):

``on_command_pre``
    Fired after the command's runtime attributes (``caller``, ``session``,
    ``cmdname``, ``args``, ``raw_string`` ...) are set and before
    ``at_pre_parse()`` runs. Receivers are observers only; they cannot
    abort dispatch (use ``at_pre_parse`` to gate before parse, or
    ``at_pre_cmd`` to gate after parse but before ``func``).

    - ``cmd`` (Command): the command instance about to be executed.
    - ``caller``: the caller object (Session, Account, or Object).
    - ``session``: the dispatching session, or ``None``.
    - ``trace_id`` (str | None): the current per-command trace id from
      ``evennia.utils.command_trace.get_trace_id()``.

``on_command_post``
    Fired after ``at_post_cmd()`` returns (or, for generator-based
    ``func()``, after the generator completes and ``at_post_cmd`` runs
    inside ``_progressive_cmd_run``).

    - ``cmd``, ``caller``, ``session``, ``trace_id``: as above.
    - ``elapsed_ms`` (float): wall time in milliseconds from just before
      ``at_pre_parse`` to just after ``at_post_cmd``.

``on_command_error``
    Fired inside the ``_run_command`` exception handler that converts a
    user-command exception into ``ErrorReported``. Not fired for
    cmdset-merge or cmdset-getter failures (subscribe to
    ``on_cmdset_merge_error`` for those).

    - ``cmd`` (Command): the command instance whose ``func()`` raised.
    - ``caller``, ``session``, ``trace_id``: as above.
    - ``exc`` (BaseException): the exception that was raised.
    - ``traceback_text`` (str): output of ``traceback.format_exc()`` for
      the exception, captured at signal-fire time.

``on_cmdset_merge_error``
    Fired when building or merging the effective cmdset for a caller
    fails. There is no ``cmd`` instance at this stage, so the sender is
    ``type(caller)`` instead.

    - ``caller``: the caller object whose cmdset stack failed to build.
    - ``session``: the dispatching session, or ``None``.
    - ``raw_string`` (str): the input string that triggered the dispatch.
    - ``trace_id`` (str | None): the current per-command trace id.
    - ``exc`` (BaseException): the exception that was raised.
    - ``traceback_text`` (str): output of ``traceback.format_exc()`` for
      the exception, captured at signal-fire time.

"""

from django.dispatch import Signal

__all__ = (
    "on_command_pre",
    "on_command_post",
    "on_command_error",
    "on_cmdset_merge_error",
)

on_command_pre = Signal()
on_command_post = Signal()
on_command_error = Signal()
on_cmdset_merge_error = Signal()
