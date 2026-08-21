"""Session-handler integration for console watches.

All client protocols converge on these two funnels. These tests prove a watch
observes the normalized frame sent toward a client and the complete line a
client submits, without depending on telnet, WebSocket, or SSH internals.
"""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from evennia.console import watch
from evennia.server.sessionhandler import ServerSessionHandler


def _session(protocol_key="telnet"):
    """Return a session stand-in for one client protocol."""

    return Mock(
        sessid=1,
        account=SimpleNamespace(pk=7, username="player"),
        protocol_key=protocol_key,
    )


class TestSessionHandlerWatchFunnels(TestCase):
    """The shared funnels capture traffic for every client family."""

    def setUp(self):
        """Start each test with a handler and an empty watch registry."""

        self.clock_patcher = patch("evennia.utils.clock.call_later")
        self.clock_patcher.start()
        watch.WATCHES.clear()
        self.handler = ServerSessionHandler()

    def tearDown(self):
        """Do not leak process-local watches to another test."""

        watch.WATCHES.clear()
        self.clock_patcher.stop()

    def test_output_captures_the_normalized_frame_sent_to_every_protocol(self):
        for protocol_key in ("telnet", "websocket"):
            with self.subTest(protocol_key=protocol_key):
                watch.WATCHES.clear()
                session = _session(protocol_key)
                self.handler[session.sessid] = session
                entry = watch.start(1, session, 42, "staffer", "a report")
                normalized = {"text": [[f"screen via {protocol_key}"], {}]}

                with (
                    patch("evennia.utils.clock.call_later"),
                    patch.object(self.handler, "clean_senddata", return_value=normalized),
                    patch("evennia.server.sessionhandler.evennia") as mock_evennia,
                ):
                    self.handler.data_out(session, text="unprocessed")
                    self.handler._flush_outbuf(session.sessid)

                self.assertEqual(entry.frames[0]["line"], f"screen via {protocol_key}")
                bus = mock_evennia.EVENNIA_SERVER_SERVICE.portal_bus
                bus.send_MsgServer2Portal.assert_called_once_with(session, **normalized)

    def test_input_captures_the_submitted_line_before_command_handling(self):
        session = _session()
        entry = watch.start(1, session, 42, "staffer", "a report")

        self.handler.data_in(session, text=(("say something private",), {}))

        self.assertEqual(entry.frames[0]["line"], "say something private")
        self.assertEqual(entry.frames[0]["dir"], "in")
        session.data_in.assert_called_once_with(text=(("say something private",), {}))
