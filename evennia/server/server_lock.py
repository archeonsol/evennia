"""Per-game singleton guard for the Server process.

Only one Server process may serve one game database at a time. Two Servers
against one database defeat the JSONB write-behind layer's cross-process
guarantees: both processes carry row baselines, lose-update refusals cascade
into undurable rows, and the Portal<->Server transport cannot pair with both
peers. The systemd unit and the launcher each try to keep the pair singular,
but concurrent lifecycle commands (a service restart racing ``evennia reload``)
can outrun their PID-file checks.

This module owns the authoritative guard: the Server process takes an exclusive
advisory lock adjacent to its pidfile (``<pidfile>.lock``) for its whole
lifetime, so a second Server refuses to start instead of running the game.
Advisory locks are released by the operating system on process exit, so a crash
can never leave the game permanently locked.

The Portal probes the same lock before spawning a Server, and the launcher
probes it before asking the Portal to spawn one, so a refused duplicate is
reported to the operator instead of waiting out a startup timeout.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time

__all__ = [
    "ServerAlreadyRunning",
    "ServerLock",
    "acquire_server_lock",
    "pidfile_from_cmd",
    "refuse_start",
    "server_lock_held",
]


class ServerAlreadyRunning(RuntimeError):
    """Another process already holds this game's server lock."""

    def __init__(self, pid: int | None, path: str):
        """Bind the holder pid (when readable) and the contended lock path."""
        self.pid = pid
        self.path = path
        super().__init__(
            "another Server process (pid %s) already owns %s" % (pid or "unknown", path)
        )


class ServerLock:
    """A held exclusive lock proving this process is the game's only Server."""

    def __init__(self, path: str, handle):
        """Wrap the open lock file ``handle`` at ``path``."""
        self.path = path
        self._handle = handle

    def release(self) -> None:
        """Release the lock (idempotent; the OS also releases on exit)."""
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _unlock(handle)
        except OSError:
            pass
        try:
            handle.close()
        except OSError:
            pass


def _lock_path(pidfile: str | None) -> str:
    """Return the lock path for ``pidfile``, or the log-dir default."""
    if pidfile:
        return f"{pidfile}.lock"
    from django.conf import settings

    log_dir = getattr(settings, "LOG_DIR", None) or tempfile.gettempdir()
    return os.path.join(log_dir, "server.lock")


def _try_acquire(handle) -> bool:
    """Try an exclusive non-blocking advisory lock; False when already held."""
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle) -> None:
    """Release the advisory lock held on ``handle``."""
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _read_holder_pid(handle) -> int | None:
    """Read the holder pid recorded after the locked sentinel byte."""
    try:
        handle.seek(1)
        raw = handle.read().strip().split()
        return int(raw[0]) if raw else None
    except (OSError, ValueError):
        return None


def acquire_server_lock(pidfile: str | None = None) -> ServerLock:
    """Acquire this game's exclusive Server lock.

    Args:
        pidfile (str, optional): The process pidfile the Server was launched
            with; the lock lives beside it as ``<pidfile>.lock``.

    Returns:
        ServerLock: The held lock, to be released at shutdown.

    Raises:
        ServerAlreadyRunning: Another live process already holds the lock.

    """
    path = _lock_path(pidfile)
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    if not _try_acquire(handle):
        pid = _read_holder_pid(handle)
        handle.close()
        raise ServerAlreadyRunning(pid, path)
    # The lock itself is the authority; the recorded pid is diagnostic only.
    # Byte 0 is the locked sentinel and stays off-limits to other handles
    # (Windows denies reads of a locked range), so the metadata starts at 1.
    try:
        handle.truncate(0)
        handle.write(" ")
        handle.write("%d\t%f\n" % (os.getpid(), time.time()))
        handle.flush()
        os.fsync(handle.fileno())
    except OSError:
        pass
    return ServerLock(path, handle)


def server_lock_held(pidfile: str | None = None) -> int | None:
    """Return the pid holding this game's Server lock, or None when free.

    A returned pid (possibly 0 when the holder is unreadable) means a live
    Server owns the game, so no other Server may be spawned.
    """
    path = _lock_path(pidfile)
    if not os.path.exists(path):
        return None
    try:
        handle = open(path, "a+", encoding="utf-8")
    except OSError:
        return None
    try:
        if _try_acquire(handle):
            _unlock(handle)
            return None
        return _read_holder_pid(handle) or 0
    finally:
        try:
            handle.close()
        except OSError:
            pass


def pidfile_from_cmd(cmd) -> str | None:
    """Return the ``--pidfile`` argument of a Server launch command, if any."""
    args = [str(part) for part in (cmd or ())]
    for index, part in enumerate(args):
        if part == "--pidfile" and index + 1 < len(args):
            return args[index + 1]
        if part.startswith("--pidfile="):
            return part.split("=", 1)[1]
    return None


def refuse_start(reason: str) -> None:
    """Report a refused Server start to stderr and the server log.

    Startup refusal happens before logging is configured, so the message goes
    to both places directly: the terminal/systemd journal and the server log
    file operators tail when the service fails.
    """
    message = f"FATAL: {reason}. Refusing to start a second Server for this game."
    print(message, file=sys.stderr, flush=True)
    try:
        from django.conf import settings

        logfile = getattr(settings, "SERVER_LOG_FILE", None)
        if logfile:
            os.makedirs(os.path.dirname(logfile) or ".", exist_ok=True)
            with open(logfile, "a", encoding="utf-8") as handle:
                handle.write("%s [EE] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except Exception:
        pass
