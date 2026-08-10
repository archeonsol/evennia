"""
Outbound-buffer behaviour of ``ServerSessionHandler`` around disconnect.

``data_out`` defers its AMP send to the next reactor iteration, so a caller that
sends and then disconnects in the same iteration leaves messages in the buffer.
Those must reach the Portal, not be dropped: the webclient's quit menu is raised
by a ``logout`` OOB sent immediately before the session is torn down.
"""

from unittest import TestCase
from unittest.mock import Mock, patch

from evennia.server.sessionhandler import ServerSessionHandler


def _session(sessid=1):
    """A session stub with only what ``disconnect`` touches.

    ``account`` is None so the logout-logging branch (and its account signal)
    stays out of the way; the buffer handling under test is the same either way.
    """
    return Mock(sessid=sessid, account=None, address="testaddress")


class TestDisconnectFlushesOutbuf(TestCase):
    """Pending output must be flushed by disconnect, never discarded."""

    def setUp(self):
        self.handler = ServerSessionHandler()
        self.session = _session()
        self.handler[self.session.sessid] = self.session
        patcher = patch("evennia.server.sessionhandler._send_admin_to_portal")
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_pending_output_is_flushed(self):
        self.handler._outbuf[1] = [{"logout": ("quit",)}]
        with patch.object(self.handler, "_flush_outbuf") as flush:
            self.handler.disconnect(self.session)
        flush.assert_called_once_with(1)

    def test_nothing_pending_means_no_flush(self):
        with patch.object(self.handler, "_flush_outbuf") as flush:
            self.handler.disconnect(self.session)
        flush.assert_not_called()

    def test_buffer_is_empty_afterwards(self):
        self.handler._outbuf[1] = [{"logout": ("quit",)}]
        with patch("evennia.server.sessionhandler.evennia") as mock_evennia:
            self.handler.disconnect(self.session)
        self.assertNotIn(1, self.handler._outbuf)
        bus = mock_evennia.EVENNIA_SERVER_SERVICE.portal_bus
        self.assertTrue(bus.send_MsgServer2Portal.called)
