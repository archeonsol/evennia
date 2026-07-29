"""Native implementation helpers for the developer Python action."""

from __future__ import annotations

import code
import sys
import time
import traceback

from evennia.utils import utils

__all__ = [
    "EvenniaPythonConsole",
    "py_code",
    "py_load",
    "py_quit",
    "run_code_snippet",
]


class PrintRecursionError(RecursionError):
    """Raised when redirected ``print`` recursively enters ``msg``."""


def evennia_local_vars(caller):
    """Return the standard locals exposed to native ``@py`` execution."""
    import evennia

    return {
        "self": caller,
        "me": caller,
        "here": getattr(caller, "location", None),
        "evennia": evennia,
        "ev": evennia,
        "inherits_from": utils.inherits_from,
    }


def py_load(caller):
    """Return an empty initial editor buffer."""
    return ""


def py_code(caller, buffer):
    """Execute a saved editor buffer."""
    if not caller.has_capability("engine.runtime.manage"):
        caller.msg("You no longer have permission to execute Python code.")
        return False
    measure_time = caller.db._py_measure_time
    client_raw = caller.db._py_clientraw
    caller.msg("Executing code%s ..." % (" (measure timing)" if measure_time else ""))
    run_code_snippet(
        caller,
        buffer,
        mode="exec",
        measure_time=measure_time,
        client_raw=client_raw,
        show_input=False,
    )
    return True


def py_quit(caller):
    """Clean native ``@py/edit`` state and report exit."""
    caller.attributes.remove("_py_measure_time")
    caller.attributes.remove("_py_clientraw")
    caller.msg("Exited the code editor.")


def run_code_snippet(
    caller,
    pycode,
    mode="eval",
    measure_time=False,
    client_raw=False,
    show_input=True,
):
    """Execute code with Evennia locals and route output to the caller."""
    sessions = caller.sessions.all() if hasattr(caller, "sessions") else [None]
    available_vars = evennia_local_vars(caller)
    if show_input:
        for session in sessions:
            data = {
                "text": (f">>> {pycode}", {"type": "py_input"}),
                "options": {"raw": True, "highlight": True},
            }
            try:
                caller.msg(session=session, **data)
            except TypeError:
                caller.msg(**data)

    old_stdout = sys.stdout
    old_stderr = sys.stderr

    class FakeStd:
        """File-like stream forwarding interpreter output to ``msg``."""

        def write(self, string):
            try:
                caller.msg(text=(string.rstrip("\n"), {"type": "py_output"}))
            except RecursionError as err:
                traceback_frames = traceback.extract_tb(sys.exc_info()[2])
                if any(frame.line and "print(" in frame.line for frame in traceback_frames):
                    raise PrintRecursionError from err

        def flush(self):
            """Satisfy the file-like stream protocol."""

    try:
        sys.stdout = sys.stderr = FakeStd()
        try:
            compiled = compile(pycode, "", mode)
        except Exception:
            compiled = compile(pycode, "", "exec")
        if measure_time:
            started = time.time()
            result = eval(compiled, {}, available_vars)
            caller.msg(f" (runtime ~ {(time.time() - started) * 1000:.4f} ms)")
        else:
            result = eval(compiled, {}, available_vars)
    except PrintRecursionError:
        result = (
            "<<< Error: Recursive print() found (probably in custom msg()). Since `py` "
            "reroutes `print` to `msg()`, this causes a loop. Remove `print()` from "
            "msg-related code to resolve."
        )
    except Exception:  # noqa: BLE001 - interpreter errors are user output
        errors = traceback.format_exc().split("\n")
        result = "\n".join(line for line in (errors[4:] if len(errors) > 4 else errors) if line)
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr

    if result is None:
        return
    if not client_raw or isinstance(result, tuple):
        result = str(result)
    for session in sessions:
        try:
            caller.msg(
                (result, {"type": "py_output"}),
                session=session,
                options={"raw": True, "client_raw": client_raw, "highlight": True},
            )
        except TypeError:
            caller.msg(
                (result, {"type": "py_output"}),
                options={"raw": True, "client_raw": client_raw, "highlight": True},
            )


class EvenniaPythonConsole(code.InteractiveConsole):
    """Interactive console whose stdout/stderr are caller messages."""

    def __init__(self, caller):
        super().__init__(evennia_local_vars(caller))
        self.caller = caller

    def write(self, string):
        """Send interpreter error output to the caller."""
        self.caller.msg(string)

    def push(self, line):
        """Push one complete or partial source line."""
        old_stdout, old_stderr = sys.stdout, sys.stderr

        class FakeStd:
            """Minimal console output forwarding stream."""

            def write(inner_self, string):
                for line in string.splitlines():
                    self.caller.msg(line)

            def flush(inner_self):
                """Satisfy the file-like stream protocol."""

        try:
            sys.stdout = sys.stderr = FakeStd()
            return super().push(line)
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
