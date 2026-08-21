"""Server health, and the degraded-mode probe the whole console depends on.

The web worker and the Server process fail independently. When the game server
is down but the web server is up, a console that returns 500 everywhere is
useless in precisely the situation an operator most needs it. So the console
degrades instead: panels that read plain models keep working, and IO actions
report themselves disabled with a reason.

:func:`io_available` is the probe that decides. It is deliberately cheap and
non-blocking -- it asks whether an IO loop is bound, not whether it is
responsive -- because it runs on every request that renders the nav.

"""

from __future__ import annotations

from evennia.utils import logger


def io_available() -> bool:
    """Return whether the IO owner can currently service a worker call.

    Mirrors the precondition in ``evennia.web.utils.io.run_on_io_thread``: a
    worker call needs a bound loop, and raises ``IOThreadCallUnavailable``
    without one. Checking the same condition up front lets the console disable
    an action with an explanation instead of surfacing an exception.

    Returns:
        bool: True when an IO owner is reachable.
    """

    try:
        from evennia.utils import clock

        return clock.is_io_owner() or clock.get_bound_loop() is not None
    except Exception:  # noqa: BLE001 - a probe must never be the failure
        logger.log_trace("console io_available probe failed")
        return False


def database_available() -> bool:
    """Return whether the default database answers a trivial query."""

    try:
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception:  # noqa: BLE001 - reporting health must not raise
        return False
    return True


def engine_version() -> str:
    """Return the running engine version, including its git revision.

    The single most common question during an incident, and currently
    answerable only from a shell.
    """

    try:
        import evennia

        return str(evennia.__version__)
    except Exception:  # noqa: BLE001
        return ""


def status() -> dict:
    """Return a plain health summary.

    Green or not green per line, with the underlying value rather than an
    interpretation. Served as JSON so an external uptime check can consume the
    same judgement instead of inventing its own.

    Returns:
        dict: Health checks plus identity, all JSON-safe.
    """

    io_ok = io_available()
    db_ok = database_available()
    checks = {
        "database": db_ok,
        "io_owner": io_ok,
    }
    return {
        "healthy": all(checks.values()),
        "degraded": not io_ok,
        "checks": checks,
        "version": engine_version(),
    }
