"""Owned, transport-agnostic telnet (RFC 854 / RFC 1143) parser.

This is a faithful vendoring of Twisted's ``twisted.conch.telnet`` state machine
(the ``Telnet`` class + option constants), lifted out of the ``twisted`` package
so the telnet protocol no longer depends on it. The parsing logic is copied
verbatim to preserve exact byte-level negotiation behaviour with real MUD clients
(Mudlet, TinTin++, MUSHclient, raw telnet); only the transport coupling is
removed:

    - the base class is a plain ``object`` instead of ``twisted...Protocol``
      (a transport *adapter* drives ``dataReceived``/``connectionLost`` and
      supplies ``self.transport``);
    - nothing here touches the reactor.

``LineReceiver`` is intentionally NOT vendored: the game's ``TelnetProtocol``
overrides ``applicationDataReceived`` and does its own line splitting, so the
line-mode machinery was dead weight.

Option negotiation still returns ``twisted.internet.defer.Deferred`` (reactor-
free); dropping the Deferred system is a separate, engine-wide seam.

Upstream: Twisted ``twisted/conch/telnet.py`` (MIT licensed). Keep in sync only
if a telnet correctness fix lands upstream; telnet itself is frozen.
"""

from twisted.internet import defer


def _chr(i: int) -> bytes:
    """One byte from an RFC 854 decimal code (Python literals are oct/hex)."""
    return bytes((i,))


def iterbytes(data):
    """Yield each byte of ``data`` as a length-1 ``bytes`` (py3 bytes iterate ints)."""
    return (data[i : i + 1] for i in range(len(data)))


# -- option / command constants (RFC 854 and friends) -------------------

MODE = _chr(1)
EDIT = 1
TRAPSIG = 2
MODE_ACK = 4
SOFT_TAB = 8
LIT_ECHO = 16

NULL = _chr(0)
BEL = _chr(7)
BS = _chr(8)
HT = _chr(9)
LF = _chr(10)
VT = _chr(11)
FF = _chr(12)
CR = _chr(13)

ECHO = _chr(1)
SGA = _chr(3)
NAWS = _chr(31)
LINEMODE = _chr(34)

EOR = _chr(239)
SE = _chr(240)
NOP = _chr(241)
DM = _chr(242)
BRK = _chr(243)
IP = _chr(244)
AO = _chr(245)
AYT = _chr(246)
EC = _chr(247)
EL = _chr(248)
GA = _chr(249)
SB = _chr(250)
WILL = _chr(251)
WONT = _chr(252)
DO = _chr(253)
DONT = _chr(254)
IAC = _chr(255)

LINEMODE_MODE = _chr(1)
LINEMODE_EDIT = _chr(1)
LINEMODE_TRAPSIG = _chr(2)
LINEMODE_MODE_ACK = _chr(4)
LINEMODE_SOFT_TAB = _chr(8)
LINEMODE_LIT_ECHO = _chr(16)


# -- negotiation errors -------------------------------------------------


class TelnetError(Exception):
    pass


class NegotiationError(TelnetError):
    def __str__(self) -> str:
        return self.__class__.__module__ + "." + self.__class__.__name__ + ":" + repr(self.args[0])


class OptionRefused(NegotiationError):
    pass


class AlreadyEnabled(NegotiationError):
    pass


class AlreadyDisabled(NegotiationError):
    pass


class AlreadyNegotiating(NegotiationError):
    pass


class Telnet:
    """Telnet option-negotiation state machine (transport-agnostic).

    A transport adapter feeds bytes to ``dataReceived`` and provides
    ``self.transport`` (needing only ``.write(bytes)``). ``commandMap`` /
    ``negotiationMap`` map command bytes to handlers; ``enableRemote`` /
    ``enableLocal`` / ``disableRemote`` / ``disableLocal`` are the option hooks
    a subclass overrides.
    """

    # One of "data", "escaped", "command", "newline", "subnegotiation",
    # "subnegotiation-escaped".
    state = "data"

    def __init__(self):
        self.options = {}
        self.negotiationMap = {}
        self.commandMap = {
            WILL: self.telnet_WILL,
            WONT: self.telnet_WONT,
            DO: self.telnet_DO,
            DONT: self.telnet_DONT,
        }

    def _write(self, data):
        self.transport.write(data)

    class _OptionState:
        class _Perspective:
            state = "no"
            negotiating = False
            onResult = None

            def __str__(self) -> str:
                return self.state + ("*" * self.negotiating)

        def __init__(self):
            self.us = self._Perspective()
            self.him = self._Perspective()

        def __repr__(self) -> str:
            return f"<_OptionState us={self.us} him={self.him}>"

    def getOptionState(self, opt):
        return self.options.setdefault(opt, self._OptionState())

    def _do(self, option):
        self._write(IAC + DO + option)

    def _dont(self, option):
        self._write(IAC + DONT + option)

    def _will(self, option):
        self._write(IAC + WILL + option)

    def _wont(self, option):
        self._write(IAC + WONT + option)

    def will(self, option):
        """Indicate our willingness to enable an option."""
        s = self.getOptionState(option)
        if s.us.negotiating or s.him.negotiating:
            return defer.fail(AlreadyNegotiating(option))
        elif s.us.state == "yes":
            return defer.fail(AlreadyEnabled(option))
        else:
            s.us.negotiating = True
            s.us.onResult = d = defer.Deferred()
            self._will(option)
            return d

    def wont(self, option):
        """Indicate we are not willing to enable an option."""
        s = self.getOptionState(option)
        if s.us.negotiating or s.him.negotiating:
            return defer.fail(AlreadyNegotiating(option))
        elif s.us.state == "no":
            return defer.fail(AlreadyDisabled(option))
        else:
            s.us.negotiating = True
            s.us.onResult = d = defer.Deferred()
            self._wont(option)
            return d

    def do(self, option):
        s = self.getOptionState(option)
        if s.us.negotiating or s.him.negotiating:
            return defer.fail(AlreadyNegotiating(option))
        elif s.him.state == "yes":
            return defer.fail(AlreadyEnabled(option))
        else:
            s.him.negotiating = True
            s.him.onResult = d = defer.Deferred()
            self._do(option)
            return d

    def dont(self, option):
        s = self.getOptionState(option)
        if s.us.negotiating or s.him.negotiating:
            return defer.fail(AlreadyNegotiating(option))
        elif s.him.state == "no":
            return defer.fail(AlreadyDisabled(option))
        else:
            s.him.negotiating = True
            s.him.onResult = d = defer.Deferred()
            self._dont(option)
            return d

    def requestNegotiation(self, about, data):
        """Send a subnegotiation for option ``about`` with ``data`` payload."""
        data = data.replace(IAC, IAC * 2)
        self._write(IAC + SB + about + data + IAC + SE)

    def dataReceived(self, data):
        appDataBuffer = []

        for b in iterbytes(data):
            if self.state == "data":
                if b == IAC:
                    self.state = "escaped"
                elif b == b"\r":
                    self.state = "newline"
                else:
                    appDataBuffer.append(b)
            elif self.state == "escaped":
                if b == IAC:
                    appDataBuffer.append(b)
                    self.state = "data"
                elif b == SB:
                    self.state = "subnegotiation"
                    self.commands = []
                elif b in (EOR, NOP, DM, BRK, IP, AO, AYT, EC, EL, GA):
                    self.state = "data"
                    if appDataBuffer:
                        self.applicationDataReceived(b"".join(appDataBuffer))
                        del appDataBuffer[:]
                    self.commandReceived(b, None)
                elif b in (WILL, WONT, DO, DONT):
                    self.state = "command"
                    self.command = b
                else:
                    raise ValueError("Stumped", b)
            elif self.state == "command":
                self.state = "data"
                command = self.command
                del self.command
                if appDataBuffer:
                    self.applicationDataReceived(b"".join(appDataBuffer))
                    del appDataBuffer[:]
                self.commandReceived(command, b)
            elif self.state == "newline":
                self.state = "data"
                if b == b"\n":
                    appDataBuffer.append(b"\n")
                elif b == b"\0":
                    appDataBuffer.append(b"\r")
                elif b == IAC:
                    # IAC isn't really allowed after \r, but handling it this way
                    # is less surprising than delivering it as application data.
                    appDataBuffer.append(b"\r")
                    self.state = "escaped"
                else:
                    appDataBuffer.append(b"\r" + b)
            elif self.state == "subnegotiation":
                if b == IAC:
                    self.state = "subnegotiation-escaped"
                else:
                    self.commands.append(b)
            elif self.state == "subnegotiation-escaped":
                if b == SE:
                    self.state = "data"
                    commands = self.commands
                    del self.commands
                    if appDataBuffer:
                        self.applicationDataReceived(b"".join(appDataBuffer))
                        del appDataBuffer[:]
                    self.negotiate(commands)
                else:
                    self.state = "subnegotiation"
                    self.commands.append(b)
            else:
                raise ValueError("How'd you do this?")

        if appDataBuffer:
            self.applicationDataReceived(b"".join(appDataBuffer))

    def connectionLost(self, reason):
        for state in self.options.values():
            if state.us.onResult is not None:
                d = state.us.onResult
                state.us.onResult = None
                d.errback(reason)
            if state.him.onResult is not None:
                d = state.him.onResult
                state.him.onResult = None
                d.errback(reason)

    def applicationDataReceived(self, data):
        """Called with application-level data."""

    def unhandledCommand(self, command, argument):
        """Called for commands for which no handler is installed."""

    def commandReceived(self, command, argument):
        cmdFunc = self.commandMap.get(command)
        if cmdFunc is None:
            self.unhandledCommand(command, argument)
        else:
            cmdFunc(argument)

    def unhandledSubnegotiation(self, command, data):
        """Called for subnegotiations for which no handler is installed."""

    def negotiate(self, data):
        command, data = data[0], data[1:]
        cmdFunc = self.negotiationMap.get(command)
        if cmdFunc is None:
            self.unhandledSubnegotiation(command, data)
        else:
            cmdFunc(data)

    def telnet_WILL(self, option):
        s = self.getOptionState(option)
        self.willMap[s.him.state, s.him.negotiating](self, s, option)

    def will_no_false(self, state, option):
        # He is unilaterally offering to enable an option.
        if self.enableRemote(option):
            state.him.state = "yes"
            self._do(option)
        else:
            self._dont(option)

    def will_no_true(self, state, option):
        # Peer agreed to enable an option in response to our request.
        state.him.state = "yes"
        state.him.negotiating = False
        d = state.him.onResult
        state.him.onResult = None
        d.callback(True)
        assert self.enableRemote(option), "enableRemote must return True in this context (for option {!r})".format(
            option
        )

    def will_yes_false(self, state, option):
        # He is unilaterally offering to enable an already-enabled option. Ignore.
        pass

    def will_yes_true(self, state, option):
        # Bogus state, here for completeness. Never entered.
        assert False, "will_yes_true can never be entered, but was called with {!r}, {!r}".format(state, option)

    willMap = {
        ("no", False): will_no_false,
        ("no", True): will_no_true,
        ("yes", False): will_yes_false,
        ("yes", True): will_yes_true,
    }

    def telnet_WONT(self, option):
        s = self.getOptionState(option)
        self.wontMap[s.him.state, s.him.negotiating](self, s, option)

    def wont_no_false(self, state, option):
        # He demands an already-disabled option stay disabled. Ignore.
        pass

    def wont_no_true(self, state, option):
        # Peer refused to enable an option in response to our request.
        state.him.negotiating = False
        d = state.him.onResult
        state.him.onResult = None
        d.errback(OptionRefused(option))

    def wont_yes_false(self, state, option):
        # Peer is unilaterally demanding that an option be disabled.
        state.him.state = "no"
        self.disableRemote(option)
        self._dont(option)

    def wont_yes_true(self, state, option):
        # Peer agreed to disable an option at our request.
        state.him.state = "no"
        state.him.negotiating = False
        d = state.him.onResult
        state.him.onResult = None
        d.callback(True)
        self.disableRemote(option)

    wontMap = {
        ("no", False): wont_no_false,
        ("no", True): wont_no_true,
        ("yes", False): wont_yes_false,
        ("yes", True): wont_yes_true,
    }

    def telnet_DO(self, option):
        s = self.getOptionState(option)
        self.doMap[s.us.state, s.us.negotiating](self, s, option)

    def do_no_false(self, state, option):
        # Peer is unilaterally requesting that we enable an option.
        if self.enableLocal(option):
            state.us.state = "yes"
            self._will(option)
        else:
            self._wont(option)

    def do_no_true(self, state, option):
        # Peer agreed to allow us to enable an option at our request.
        state.us.state = "yes"
        state.us.negotiating = False
        d = state.us.onResult
        state.us.onResult = None
        d.callback(True)
        self.enableLocal(option)

    def do_yes_false(self, state, option):
        # Peer requests we enable an already-enabled option. Ignore.
        pass

    def do_yes_true(self, state, option):
        # Bogus state, here for completeness. Never entered.
        assert False, "do_yes_true can never be entered, but was called with {!r}, {!r}".format(state, option)

    doMap = {
        ("no", False): do_no_false,
        ("no", True): do_no_true,
        ("yes", False): do_yes_false,
        ("yes", True): do_yes_true,
    }

    def telnet_DONT(self, option):
        s = self.getOptionState(option)
        self.dontMap[s.us.state, s.us.negotiating](self, s, option)

    def dont_no_false(self, state, option):
        # Peer demands we disable an already-disabled option. Ignore.
        pass

    def dont_no_true(self, state, option):
        # Offered option was refused. Fail the Deferred from the will() call.
        state.us.negotiating = False
        d = state.us.onResult
        state.us.onResult = None
        d.errback(OptionRefused(option))

    def dont_yes_false(self, state, option):
        # Peer is unilaterally demanding we disable an option.
        state.us.state = "no"
        self.disableLocal(option)
        self._wont(option)

    def dont_yes_true(self, state, option):
        # Peer acknowledged our notice that we will disable an option.
        state.us.state = "no"
        state.us.negotiating = False
        d = state.us.onResult
        state.us.onResult = None
        d.callback(True)
        self.disableLocal(option)

    dontMap = {
        ("no", False): dont_no_false,
        ("no", True): dont_no_true,
        ("yes", False): dont_yes_false,
        ("yes", True): dont_yes_true,
    }

    def enableLocal(self, option):
        return False

    def enableRemote(self, option):
        return False

    def disableLocal(self, option):
        raise NotImplementedError(
            "You must override this method (for option %r)" % (option,)
        )

    def disableRemote(self, option):
        raise NotImplementedError(
            "You must override this method (for option %r)" % (option,)
        )
