"""Cmdset merge cache warmup.

Prime Evennia's cmdset merge cache so the first typed command after login or
reload does not pay a multi-second cold merge in `get_and_merge_cmdsets`. The
warmup runs the same provider-generation + merge pipeline a normal command
would trigger, with no command name and no NOINPUT message.

Two entry points fire automatically:

- `schedule_cmdset_merge_warmup_for_character(character)` is called from
  `AccountDB.puppet_object` after `at_post_puppet`, warming the merge cache for
  every session the puppet just attached to.
- `warm_all_logged_in_puppet_sessions()` is called from the server's
  post-portal-sync hook on reload, warming every already-puppeted session
  before the user's next command lands.

Perception is the reason the warmup runs eagerly rather than lazily: users
tolerate a noticeable pause at login/reload but find the same pause on the
first typed command jarring (it reads as a hung server).
"""

from __future__ import annotations

from evennia.utils import clock, delay, logger


async def warm_cmdset_merge_for_session(session, *, callertype: str = "session"):
    """
    Prime the cmdset merge cache for a single session.

    Runs `generate_cmdset_providers` + `get_and_merge_cmdsets` only. No command
    is parsed or executed, no NOINPUT message is sent.

    Args:
        session: A logged-in `ServerSession`.
        callertype (str): The caller type passed to `get_and_merge_cmdsets`.
            Defaults to `"session"`, matching the normal command dispatch path.

    """
    from evennia.commands.cmdhandler import generate_cmdset_providers, get_and_merge_cmdsets

    try:
        (
            _cmdset_providers,
            cmdset_providers_list,
            _cmdset_providers_errors_list,
            merge_caller,
            _error_to,
        ) = generate_cmdset_providers(session, session=session)
        merged = await get_and_merge_cmdsets(
            merge_caller, cmdset_providers_list, callertype, "", cmdid=None
        )
        commands = getattr(merged, "commands", None)
        if isinstance(commands, (list, tuple)) and commands:
            from evennia.authorization.storage import preload_policy_packages

            preload_policy_packages(commands)
    except Exception as exc:
        logger.log_trace(f"cmdset merge warmup: {exc}")


def schedule_cmdset_merge_warmup_for_character(character) -> None:
    """
    Defer to the next reactor tick, then warm the merge cache for every session
    currently puppeting `character`.

    No-op when the character has no sessions or has been deleted.

    Args:
        character: A `DefaultCharacter` instance that just had a session
            attached via `puppet_object`.

    """
    sessions = getattr(character, "sessions", None)
    if not sessions or not sessions.count():
        return

    def _fire():
        try:
            sessions_iter = character.sessions.all()
        except Exception:
            logger.log_trace("cmdset merge warmup: sessions.all() failed")
            return
        for sess in sessions_iter:
            clock.run_coroutine(warm_cmdset_merge_for_session(sess), task_kind="warmup")

    delay(0, _fire)


def warm_all_logged_in_puppet_sessions() -> None:
    """
    Warm the merge cache for every logged-in, puppeted session.

    Called once after the server's post-portal-sync on reload so the next typed
    command from any active player does not pay the cold merge.

    """
    from evennia import SESSION_HANDLER

    for session in SESSION_HANDLER.get_sessions():
        if not getattr(session, "logged_in", False):
            continue
        get_puppet = getattr(session, "get_puppet", None)
        if not (get_puppet and get_puppet()):
            continue
        clock.run_coroutine(warm_cmdset_merge_for_session(session), task_kind="warmup")
