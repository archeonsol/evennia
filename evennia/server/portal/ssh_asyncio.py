"""SSH access to the game on asyncssh (T3 replacement for the twisted.conch SSH).

``ssh.py`` implements SSH with the full ``twisted.conch`` terminal stack (Manhole
+ insults line editing + cred auth + PTY). asyncssh is a pure-asyncio SSH
implementation that provides the transport, password auth, PTY and (crucially)
readline-style line editing itself, so this port is a thin bridge: authenticate
against ``AccountDB``, then pump edited input lines into the Evennia session and
write session output back to the SSH channel.

Runs on the native asyncio loop, so like the other Portal asyncio servers it is
gated behind ``settings.PORTAL_ASYNCIO_SERVERS`` + the asyncio reactor. No
twisted import here.
"""

import os
import re

import asyncssh
from django.conf import settings

from evennia.accounts.models import AccountDB
from evennia.utils import ansi, logger
from evennia.utils.utils import class_from_module, to_str

_BASE_SESSION_CLASS = class_from_module(settings.BASE_SESSION_CLASS)

_RE_N = re.compile(r"\|n$")
_RE_SCREENREADER_REGEX = re.compile(
    r"%s" % settings.SCREENREADER_REGEX_STRIP, re.DOTALL + re.MULTILINE
)
_PRIVATE_KEY_FILE = os.path.join(settings.GAME_DIR, "server", "ssh-private.key")
_PUBLIC_KEY_FILE = os.path.join(settings.GAME_DIR, "server", "ssh-public.key")
_KEY_LENGTH = 2048


def authenticate(username, password):
    """Return the AccountDB for valid credentials, else None (shared auth)."""
    account = AccountDB.objects.get_account_from_name(username)
    if account and account.check_password(password):
        return account
    return None


def get_host_key():
    """Load the server host key, generating an OpenSSH keypair if absent."""
    if os.path.exists(_PRIVATE_KEY_FILE):
        return asyncssh.read_private_key(_PRIVATE_KEY_FILE)
    key = asyncssh.generate_private_key("ssh-rsa", key_size=_KEY_LENGTH)
    try:
        key.write_private_key(_PRIVATE_KEY_FILE)
        key.write_public_key(_PUBLIC_KEY_FILE)
        print(f"Created SSH host key in '{_PRIVATE_KEY_FILE}'")
    except OSError:
        logger.log_trace("could not persist SSH host key; using an ephemeral one")
    return key


class AsyncioSSHSession(_BASE_SESSION_CLASS):
    """An Evennia session bridged to an asyncssh interactive process."""

    def __init__(self, process, sessionhandler, account=None):
        self.protocol_key = "ssh"
        self._process = process
        self._sessionhandler = sessionhandler
        self._account = account

    async def run(self):
        """Connect the session, pump input lines, disconnect on close."""
        peer = self._process.get_extra_info("peername")
        client_address = peer[0] if peer else None
        self.init_session("ssh", client_address, self._sessionhandler)
        for color in ("ANSI", "XTERM256", "TRUECOLOR"):
            self.protocol_flags[color] = True
        if self._account:
            self.logged_in = True
            self.uid = self._account.id
        self.sessionhandler.connect(self)
        try:
            while True:
                try:
                    # asyncssh's line editor delivers whole edited lines
                    line = await self._process.stdin.readline()
                except asyncssh.TerminalSizeChanged:
                    # window resized; keep reading (size handling could sync here)
                    continue
                except asyncssh.BreakReceived:
                    continue
                if not line:  # EOF / channel closed
                    break
                self.sessionhandler.data_in(self, text=line.rstrip("\r\n"))
        except (asyncssh.ConnectionLost, asyncssh.ChannelOpenError):
            pass
        except Exception:
            logger.log_trace("ssh session read loop failed")
        finally:
            self.sessionhandler.disconnect(self)
            try:
                self._process.close()
            except Exception:
                pass

    # -- output --------------------------------------------------------

    def sendLine(self, string):
        for line in string.split("\n"):
            self._process.stdout.write(line + "\r\n")

    def at_login(self):
        pass

    def disconnect(self, reason="Connection closed. Goodbye for now."):
        if reason:
            self.data_out(text=((reason,), {}))
        try:
            self._process.close()
        except Exception:
            pass

    def data_out(self, **kwargs):
        self.sessionhandler.data_out(self, **kwargs)

    def send_text(self, *args, **kwargs):
        """Render + send text to the SSH terminal (ANSI, same policy as telnet)."""
        text = args[0] if args else ""
        if text is None:
            return
        text = to_str(text)

        options = kwargs.get("options", {})
        flags = self.protocol_flags
        xterm256 = options.get("xterm256", flags.get("XTERM256", True))
        useansi = options.get("ansi", flags.get("ANSI", True))
        raw = options.get("raw", flags.get("RAW", False))
        nocolor = options.get("nocolor", flags.get("NOCOLOR") or not (xterm256 or useansi))
        screenreader = options.get("screenreader", flags.get("SCREENREADER", False))

        if screenreader:
            text = ansi.parse_ansi(text, strip_ansi=True, xterm256=False, mxp=False)
            text = _RE_SCREENREADER_REGEX.sub("", text)

        if raw:
            self.sendLine(text)
            return
        linetosend = ansi.parse_ansi(
            _RE_N.sub("", text) + ("||n" if text.endswith("|") else "|n"),
            strip_ansi=nocolor,
            xterm256=xterm256,
            mxp=False,
        )
        self.sendLine(linetosend)

    def send_prompt(self, *args, **kwargs):
        self.send_text(*args, **kwargs)

    def send_default(self, *args, **kwargs):
        pass


class EvenniaSSHServer(asyncssh.SSHServer):
    """Per-connection SSH server: password auth against AccountDB."""

    def __init__(self):
        self._conn = None

    def connection_made(self, conn):
        self._conn = conn

    def begin_auth(self, username):
        # always require (password) authentication
        return True

    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        account = authenticate(username, password)
        if account is not None:
            # stash the account so the session handler can auto-login
            self._conn.set_extra_info(evennia_account=account)
            return True
        return False


def make_process_handler(sessionhandler):
    """Build the asyncssh process handler that runs an Evennia SSH session."""

    async def handle(process):
        account = process.get_extra_info("evennia_account")
        session = AsyncioSSHSession(process, sessionhandler, account=account)
        await session.run()

    return handle


async def start_ssh_server(sessionhandler, host, port):
    """Start the asyncssh SSH server on the running asyncio loop."""
    return await asyncssh.create_server(
        EvenniaSSHServer,
        host,
        port,
        server_host_keys=[get_host_key()],
        process_factory=make_process_handler(sessionhandler),
    )
