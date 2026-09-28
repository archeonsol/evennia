"""Saved protocol flags reach a session as plain data the Portal sync can pack."""

from evennia.server.amp_serde import pack_admin_message
from evennia.utils.test_resources import BaseEvenniaTest

_SAVED = {"SCREENWIDTH": {0: 100}, "SCREENREADER": True}


class SavedProtocolFlagsTest(BaseEvenniaTest):
    evennia_fixtures = {"account", "char1", "room1", "session"}

    def setUp(self):
        super().setUp()
        self.account.attributes.add("_saved_protocol_flags", _SAVED)
        # A fresh read is what login and reload see: a _SaverDict, nested too.
        self.saved = self.account.attributes.get("_saved_protocol_flags")

    def _pack_sync(self):
        sessiondata = {self.session.sessid: self.session.get_sync_data()}
        return pack_admin_message(0, {"operation": "SSYNC", "sessiondata": sessiondata})

    def test_restored_flags_pack_for_the_portal(self):
        self.session.update_flags(**self.saved)
        self.assertTrue(self._pack_sync().startswith(b"A1"))

    def test_restored_flags_do_not_write_back_to_the_account(self):
        self.session.update_flags(**self.saved)
        self.session.protocol_flags["SCREENWIDTH"][0] = 80
        saved = self.account.attributes.get("_saved_protocol_flags")
        self.assertEqual(saved["SCREENWIDTH"][0], 100)

    def test_reload_sync_restores_plain_flags(self):
        self.session.at_sync()
        self.assertEqual(self.session.protocol_flags["SCREENWIDTH"], {0: 100})
        self.assertTrue(self._pack_sync().startswith(b"A1"))
        self.session.protocol_flags["SCREENWIDTH"][0] = 80
        saved = self.account.attributes.get("_saved_protocol_flags")
        self.assertEqual(saved["SCREENWIDTH"][0], 100)
