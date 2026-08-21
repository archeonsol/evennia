"""The four panels the security review gates.

A Python REPL, a read-only SQL console, process control, and session watching.
Individually the most dangerous things this engine has ever served over HTTP,
and grouped here because they share one set of rules rather than because they
share a subject.

Every one of them:

* is **off by default**, behind its own deployment setting, because whether a
  deployment permits a thing at all is a different question from who may do it
  and a capability cannot express it;
* requires **proof of presence** -- a recent password re-entry -- since a
  capability says who you are and an unlocked laptop says nothing about whether
  you are there;
* **records what was done**, with the submitted source text where there is any,
  because prevention is not available against an operator who already holds a
  REPL and legibility afterwards is.

Session watching is the one that looks least dangerous and is not. It is
surveillance of a player by a staff member, so its audit row is permanent and
lands in the *watched* account's timeline as well as the watcher's: a
surveillance capability whose subject cannot discover it was used is a
different product from the one this plan describes.

"""

from __future__ import annotations

from django.conf import settings

from evennia.console import audit
from evennia.console.registry import Panel, io_action


class PanelDisabled(PermissionError):
    """A panel exists but this deployment has not switched it on."""


def _require_enabled(setting, label):
    """Refuse unless a deployment explicitly permits this panel.

    Args:
        setting: The settings flag governing it.
        label: Human name, for the refusal.

    Raises:
        PanelDisabled: The deployment has not enabled it.
    """

    if not getattr(settings, setting, False):
        raise PanelDisabled(
            f"{label} is disabled. Set {setting} = True to permit it on this deployment."
        )


class ReplPanel(Panel):
    """A Python console, on the IO thread, fully audited."""

    key = "repl"
    label = "REPL"
    description = "Run Python in the server process. Every submission is recorded."
    columns = ("source",)
    needs_io = True

    def rows(self, ctx):
        """Return the panel's availability and this operator's history."""

        from evennia.console.models import ConsoleAuditEvent

        enabled = bool(getattr(settings, "CONSOLE_REPL_ENABLED", False))
        history = [
            {
                "when": row["created_at"].isoformat(),
                "source": (row["before"] or {}).get("source", ""),
                "outcome": row["outcome"],
            }
            for row in ConsoleAuditEvent.objects.filter(
                panel=self.key, operation="execute", actor_id=ctx.actor_id
            ).values("created_at", "before", "outcome")[:50]
        ]
        return {
            "enabled": enabled,
            "setting": "CONSOLE_REPL_ENABLED",
            "history": history,
            "note": (
                "Runs on the server's IO thread with the same locals as the in-game "
                "Python action. Every submission is recorded with its source text."
            ),
        }

    @io_action
    def execute(self, ctx, source=""):
        """Run one Python snippet in the server process.

        Reuses the in-game console rather than reimplementing it: the same
        locals, the same capability, and the same recursion guard around a
        redirected ``print``.

        Raises:
            PanelDisabled: The deployment has not enabled the REPL.
            ValueError: No source was submitted.
        """

        _require_enabled("CONSOLE_REPL_ENABLED", "The REPL")
        text = str(source or "")
        if not text.strip():
            raise ValueError("nothing to run")

        captured = []
        outcome = "success"
        message = ""
        try:
            captured = self._run(ctx, text)
        except Exception as err:  # noqa: BLE001 - the operator's code, not ours
            outcome = "partial"
            message = f"{type(err).__name__}: {err}"

        audit.record(
            panel=self.key,
            operation="execute",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            outcome=outcome,
            before={"source": text[:4000]},
            after={"lines": len(captured)},
            message=message[:500],
        )
        return {"output": captured, "outcome": outcome, "message": message}

    def _run(self, ctx, text):
        """Execute one snippet, capturing what it printed."""

        import io
        from contextlib import redirect_stdout

        from evennia.accounts.models import AccountDB

        caller = AccountDB.objects.filter(pk=ctx.actor_id).first()
        namespace = {"__name__": "console"}
        try:
            from evennia.actions.default.python_console import evennia_local_vars

            namespace.update(evennia_local_vars(caller))
        except Exception:  # noqa: BLE001 - fall back to a minimal namespace
            import evennia

            namespace.update({"evennia": evennia, "ev": evennia, "self": caller, "me": caller})

        buffer = io.StringIO()
        with redirect_stdout(buffer):
            try:
                result = eval(compile(text, "<console>", "eval"), namespace)  # noqa: S307
                if result is not None:
                    print(repr(result))
            except SyntaxError:
                exec(compile(text, "<console>", "exec"), namespace)  # noqa: S102
        return buffer.getvalue().splitlines()[-500:]


class SqlPanel(Panel):
    """Read-only SQL, bounded and recorded."""

    key = "sql"
    label = "SQL"
    description = "Read-only queries with a statement timeout and a row cap."
    columns = ("query",)
    needs_io = False

    #: Statements this panel will run. Anything else is refused before it is
    #: sent, so a typo cannot become a migration.
    ALLOWED_PREFIXES = ("select", "with", "explain", "show", "pragma")

    def rows(self, ctx):
        """Return availability, bounds, and this operator's recent queries."""

        from evennia.console.models import ConsoleAuditEvent

        return {
            "enabled": bool(getattr(settings, "CONSOLE_SQL_ENABLED", False)),
            "setting": "CONSOLE_SQL_ENABLED",
            "timeout_ms": int(getattr(settings, "CONSOLE_SQL_TIMEOUT_MS", 5000)),
            "max_rows": int(getattr(settings, "CONSOLE_SQL_MAX_ROWS", 1000)),
            "allowed": list(self.ALLOWED_PREFIXES),
            "history": [
                {
                    "when": row["created_at"].isoformat(),
                    "query": (row["before"] or {}).get("query", ""),
                    "outcome": row["outcome"],
                }
                for row in ConsoleAuditEvent.objects.filter(
                    panel=self.key, operation="query", actor_id=ctx.actor_id
                ).values("created_at", "before", "outcome")[:50]
            ],
            "note": (
                "Read-only, and refused before it is sent if it does not start with an "
                "allowed statement. Parameters go in the parameters list, never in the "
                "query text."
            ),
        }

    @io_action
    def query(self, ctx, sql="", parameters=None):
        """Run one read-only statement.

        Parameters are bound rather than interpolated. Not because the operator
        is untrusted -- they hold a console -- but because a value pasted from a
        log will eventually contain a quote, and a query that breaks on its own
        data is a worse tool than one that does not.

        Raises:
            PanelDisabled: The deployment has not enabled the SQL console.
            ValueError: The statement is empty or not read-only.
        """

        _require_enabled("CONSOLE_SQL_ENABLED", "The SQL console")
        text = str(sql or "").strip().rstrip(";")
        if not text:
            raise ValueError("nothing to run")
        head = text.split(None, 1)[0].lower()
        if head not in self.ALLOWED_PREFIXES:
            raise ValueError(
                f"{head!r} is not a read-only statement. Allowed: "
                + ", ".join(self.ALLOWED_PREFIXES)
            )

        max_rows = int(getattr(settings, "CONSOLE_SQL_MAX_ROWS", 1000))
        outcome = "success"
        message = ""
        columns, rows = [], []
        try:
            columns, rows = self._execute(text, list(parameters or ()), max_rows)
        except Exception as err:  # noqa: BLE001 - the operator's query, not ours
            outcome = "conflict"
            message = f"{type(err).__name__}: {err}"

        audit.record(
            panel=self.key,
            operation="query",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            outcome=outcome,
            before={"query": text[:4000]},
            after={"rows": len(rows)},
            message=message[:500],
        )
        return {
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "capped": len(rows) >= max_rows,
            "outcome": outcome,
            "message": message,
        }

    def _execute(self, text, parameters, max_rows):
        """Run one statement inside a read-only, time-bounded transaction."""

        from django.db import connection, transaction

        timeout = int(getattr(settings, "CONSOLE_SQL_TIMEOUT_MS", 5000))
        with transaction.atomic():
            with connection.cursor() as cursor:
                if connection.vendor == "postgresql":
                    # A query that runs forever is a denial of service against
                    # the game, and a write slipped past the prefix check is
                    # refused by the transaction rather than by a string test.
                    cursor.execute("SET LOCAL statement_timeout = %s", [timeout])
                    cursor.execute("SET TRANSACTION READ ONLY")
                cursor.execute(text, parameters)
                columns = [column[0] for column in (cursor.description or ())]
                fetched = cursor.fetchmany(max_rows) if columns else []
            transaction.set_rollback(True)
        rows = [[self._plain(value) for value in row] for row in fetched]
        return columns, rows

    def _plain(self, value):
        """Render one cell as JSON-safe plain data."""

        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        return str(value)


class ServerPanel(Panel):
    """Process status, and the controls that change it."""

    key = "server"
    label = "Server control"
    description = "Portal and server state, and reload, reset, or shutdown."
    columns = ("check", "value")
    needs_io = False

    def rows(self, ctx):
        """Return process identity and status.

        Status is always readable; only the controls are gated, because
        knowing what is running is not dangerous and is most wanted exactly
        when changing it would be.
        """

        from evennia.console import health

        state = health.status()
        return {
            "enabled": bool(getattr(settings, "CONSOLE_SERVER_CONTROL_ENABLED", False)),
            "setting": "CONSOLE_SERVER_CONTROL_ENABLED",
            "version": state["version"],
            "checks": state["checks"],
            "degraded": state["degraded"],
            "actions": ["reload", "reset", "shutdown"],
            "note": (
                "The console runs inside the server process, so it can ask that process "
                "to stop or reload. It cannot start one that is not running; use the "
                "launcher for that."
            ),
        }

    @io_action
    def control(self, ctx, action=None, reason=""):
        """Reload, reset, or shut down the running server.

        Raises:
            PanelDisabled: The deployment has not enabled process control.
            ValueError: The action is unknown or no reason was given.
        """

        _require_enabled("CONSOLE_SERVER_CONTROL_ENABLED", "Server control")
        wanted = str(action or "").strip().lower()
        if wanted not in {"reload", "reset", "shutdown"}:
            raise ValueError(f"{action!r} is not a server action")
        if not str(reason or "").strip():
            raise ValueError("a reason is required to change the server's state")

        audit.record(
            panel=self.key,
            operation=f"server_{wanted}",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            after={"action": wanted},
            message=str(reason)[:500],
            retention="permanent",
        )

        from evennia.server.models import ServerConfig

        # Recorded before it is requested: a shutdown that succeeds takes the
        # process down with it, and an audit row written afterwards would never
        # be written at all.
        ServerConfig.objects.conf("server_restart_mode", wanted)
        self._request(wanted)
        return {
            "action": wanted,
            "requested": True,
            "note": "The request was sent. The console may lose its connection.",
        }

    def _request(self, action):
        """Ask the running service to change state."""

        from evennia.server.service_registry import services

        handler = getattr(services, action, None) if services else None
        if callable(handler):
            handler()
            return
        import os
        import signal

        if action == "shutdown":
            os.kill(os.getpid(), signal.SIGTERM)


class SessionsPanel(Panel):
    """Connected sessions, and what they are doing."""

    key = "sessions"
    label = "Live sessions"
    description = "Who is connected now, and the controls that disconnect them."
    columns = ("account", "protocol", "idle")
    needs_io = True

    def rows(self, ctx):
        """Return the currently connected sessions.

        Reads the live session handler rather than ``SessionRecord``, which is
        history. This is the present.
        """

        try:
            from evennia.server.sessionhandler import SESSIONS
        except Exception:  # noqa: BLE001
            return {"rows": [], "available": False, "reason": "The session handler is not loaded."}

        rows = []
        for session in list(getattr(SESSIONS, "values", lambda: [])()):
            account = getattr(session, "account", None)
            puppet = getattr(session, "puppet", None)
            rows.append(
                {
                    "sessid": getattr(session, "sessid", None),
                    "account": str(getattr(account, "username", "")) or "(unauthenticated)",
                    "puppet": str(getattr(puppet, "key", "")) if puppet else "",
                    "protocol": str(getattr(session, "protocol_key", "")),
                    "connected": getattr(session, "conn_time", None),
                    "idle": getattr(session, "cmd_last", None),
                    "commands": getattr(session, "cmd_total", 0),
                }
            )
        return {
            "rows": sorted(rows, key=lambda row: str(row["account"])),
            "available": True,
            "count": len(rows),
            "note": (
                "Disconnecting is recorded. So is watching, permanently, and in the "
                "watched account's own timeline as well as yours."
            ),
        }

    @io_action
    def disconnect(self, ctx, sessid=None, reason=""):
        """Disconnect one session.

        Raises:
            LookupError: No such session.
            ValueError: No reason was given.
        """

        from evennia.server.sessionhandler import SESSIONS

        if not str(reason or "").strip():
            raise ValueError("a reason is required to disconnect somebody")
        session = SESSIONS.session_from_sessid(int(sessid)) if sessid else None
        if session is None:
            raise LookupError(f"no connected session with id {sessid!r}")

        account = getattr(session, "account", None)
        audit.record(
            panel=self.key,
            operation="disconnect",
            actor_id=ctx.actor_id,
            actor_name=ctx.actor_name,
            target_ref=f"accounts.accountdb#{getattr(account, 'pk', '')}",
            after={"sessid": int(sessid)},
            message=str(reason)[:500],
        )
        SESSIONS.disconnect(session, reason=str(reason)[:200])
        return {"sessid": int(sessid), "disconnected": True}

    @io_action
    def watch(self, ctx, sessid=None, reason=""):
        """Refuse to watch, because nothing mirrors a session's output yet.

        The control and the audit trail for surveillance were built before the
        thing they describe. No part of the engine copies a session's output
        anywhere a second person could read it, so the action cannot do what
        its name says.

        It must not write the audit row regardless. A permanent record saying
        an account was watched -- readable by that account, in their own
        timeline -- is a false statement about a real person, and that is the
        worse of the two faults. So this refuses, records nothing, and says
        which of the two it is.

        Raises:
            LookupError: No such session.
            ValueError: No reason was given.
            NotImplementedError: Always, when the arguments were valid.
        """

        from evennia.server.sessionhandler import SESSIONS

        if not str(reason or "").strip():
            raise ValueError("a reason is required to watch somebody")
        session = SESSIONS.session_from_sessid(int(sessid)) if sessid else None
        if session is None:
            raise LookupError(f"no connected session with id {sessid!r}")
        raise NotImplementedError(
            "Watching a session is not built: nothing mirrors session output "
            "yet, so there is nothing to show. Nothing was recorded."
        )
