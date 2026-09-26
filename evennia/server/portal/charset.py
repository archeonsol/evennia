"""
CHARSET - telnet character set negotiation

This implements the CHARSET telnet option as per
https://www.rfc-editor.org/rfc/rfc2066

Game text is Unicode and the portal sends it as UTF-8, but a telnet client
decodes in whatever encoding it was left in. Many start in ASCII or Latin-1,
and then every box-drawing frame, bar and arrow arrives as bytes they cannot
decode: replacement diamonds in Mudlet, `â”€` in others. CHARSET is how the
server asks for UTF-8. The portal offers it on connect; a client that accepts
switches its own decoding (Mudlet and TinTin++ do this without the player doing
anything), and the session is marked `UTF-8`.

A client that refuses, rejects, or never answers is left unconfirmed, and the
telnet protocol folds its text to what it can display instead (see
`evennia.utils.textfold` and `TELNET_ASCII_FALLBACK`).

"""

import weakref

CHARSET = bytes([42])  # b"\x2a"

# Subnegotiation commands (RFC 2066 section 2).
REQUEST = bytes([1])
ACCEPTED = bytes([2])
REJECTED = bytes([3])
TTABLE_IS = bytes([4])
TTABLE_REJECTED = bytes([5])

#: What the portal offers, in order of preference. Output is UTF-8, so that is
#: the only character set worth asking a client to switch to.
OFFER = (b"UTF-8",)
_SEPARATOR = b";"
_TTABLE_PREFIX = b"[TTABLE]"


def _is_utf8(name):
    """Whether a character set name names UTF-8."""
    return name.strip().upper().replace(b"_", b"-") in (b"UTF-8", b"UTF8")


class Charset:
    """
    Implements the CHARSET protocol. Add this to a variable on the telnet
    protocol to set it up.

    """

    def __init__(self, protocol):
        """
        Offer CHARSET to the client.

        Args:
            protocol (Protocol): The active protocol instance.

        """
        self.protocol = weakref.ref(protocol)
        self._handshake_counted = False
        # Our REQUEST is outstanding until the client accepts or rejects it.
        self._requesting = False
        protocol.negotiationMap[CHARSET] = self.negotiate
        protocol.will(CHARSET).addCallbacks(self.do_charset, self.no_charset)

    def _handshake_done(self):
        """Count this option's handshake once, however many answers arrive."""
        if not self._handshake_counted:
            self._handshake_counted = True
            self.protocol().handshake_done()

    def no_charset(self, option):
        """
        The client will not negotiate a character set.

        Args:
            option (Option): Not used.

        """
        self._requesting = False
        self._handshake_done()

    def do_charset(self, option):
        """
        The client agreed to negotiate: ask it to switch to UTF-8.

        Args:
            option (Option): Not used.

        """
        self._requesting = True
        self.protocol().requestNegotiation(CHARSET, REQUEST + _SEPARATOR + _SEPARATOR.join(OFFER))

    def negotiate(self, data):
        """
        Handle a CHARSET subnegotiation from the client.

        Args:
            data (list): The subnegotiation payload, as single bytes.

        """
        payload = b"".join(data)
        command, rest = payload[:1], payload[1:]
        if command == ACCEPTED:
            self._requesting = False
            if _is_utf8(rest):
                self._use_utf8()
            self._handshake_done()
        elif command in (REJECTED, TTABLE_REJECTED):
            self._requesting = False
            self._handshake_done()
        elif command == REQUEST:
            self._answer_request(rest)
        elif command == TTABLE_IS:
            # Translation tables are not supported; say so rather than hang.
            self.protocol().requestNegotiation(CHARSET, TTABLE_REJECTED)

    def _answer_request(self, rest):
        """
        The client asks the server to pick from its list.

        Args:
            rest (bytes): The request after the REQUEST byte: an optional
                `[TTABLE]` marker and version, a separator, and the names.

        """
        proto = self.protocol()
        if self._requesting:
            # Both sides asked at once. The server's request stands and the
            # client's is refused (RFC 2066 section 3).
            proto.requestNegotiation(CHARSET, REJECTED)
            return
        if rest.startswith(_TTABLE_PREFIX):
            rest = rest[len(_TTABLE_PREFIX) + 1 :]
        separator, names = rest[:1], rest[1:]
        chosen = next(
            (name for name in names.split(separator) if separator and _is_utf8(name)), None
        )
        if chosen is None:
            proto.requestNegotiation(CHARSET, REJECTED)
            return
        proto.requestNegotiation(CHARSET, ACCEPTED + chosen.strip())
        self._use_utf8()
        self._handshake_done()

    def _use_utf8(self):
        """Mark the session UTF-8 and make sure the server hears it."""
        proto = self.protocol()
        proto.protocol_flags["UTF-8"] = True
        proto.protocol_flags["ENCODING"] = "utf-8"
        note = getattr(proto, "note_negotiation", None)
        if note:
            note("CHARSET")
        if self._handshake_counted or proto.handshakes <= 0:
            # The handshake sync has already gone to the server without this.
            proto.sessionhandler.sync(proto)
