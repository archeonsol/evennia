"""Session capture: derivation, fingerprinting, and the two-phase write.

The capture path runs on every login and every disconnect, so its failure mode
matters as much as its output: a broken snapshot must never break a session.
"""

from types import SimpleNamespace

from django.test import TestCase, override_settings

from evennia.moderation import capture
from evennia.server.models import SessionRecord

_TELNET_FLAGS = {
    "CLIENTNAME": "Mudlet",
    "TERM": "xterm-256color",
    "ENCODING": "utf-8",
    "ANSI": True,
    "XTERM256": True,
    "TRUECOLOR": True,
    "MCCP": True,
    "MXP": False,
    "OOB": True,
    "OOB_GMCP": True,
    "OOB_MSDP": False,
    "SCREENWIDTH": {0: 132},
    "SCREENHEIGHT": {0: 43},
    "PEER_IP": "203.0.113.7",
    "XFF_APPLIED": False,
    "XFF_PRESENT": False,
}


def make_session(**overrides):
    """A stand-in with only the attributes capture actually reads."""
    data = {
        "moderation_uid": "uid0000000000000000000000000001",
        "moderation_connected_at": None,
        "sessid": 5,
        "protocol_key": "telnet",
        "address": "203.0.113.7",
        "protocol_flags": dict(_TELNET_FLAGS),
        "account": None,
        "csessid": None,
        "telemetry": {"command_count": 12},
        "get_puppet": lambda: None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


class DeriveTest(TestCase):
    def test_ipv4_collapses_to_slash_24(self):
        self.assertEqual(capture.derive_cidr("203.0.113.7"), "203.0.113.0/24")

    def test_ipv6_collapses_to_slash_64(self):
        # The low 64 bits rotate per lease, so they must not survive derivation.
        self.assertEqual(
            capture.derive_cidr("2001:db8:1:2:aaaa:bbbb:cccc:dddd"),
            "2001:db8:1:2::/64",
        )

    def test_junk_yields_empty_rather_than_raising(self):
        self.assertEqual(capture.derive_cidr("not-an-address"), "")
        self.assertEqual(capture.derive_cidr(None), "")

    def test_address_tuple_form_is_accepted(self):
        self.assertEqual(capture.normalize_address(("198.51.100.4", 4000)), "198.51.100.4")

    def test_hostnames_are_rejected(self):
        self.assertIsNone(capture.normalize_address("underspire.net"))

    @override_settings(MODERATION_HASH_SALT="salt-a")
    def test_hash_is_salted_and_stable(self):
        first = capture.hash_value("203.0.113.7")
        self.assertEqual(first, capture.hash_value("203.0.113.7"))
        self.assertNotIn("203.0.113.7", first)
        with override_settings(MODERATION_HASH_SALT="salt-b"):
            self.assertNotEqual(first, capture.hash_value("203.0.113.7"))


class FingerprintTest(TestCase):
    def test_same_client_same_fingerprint(self):
        self.assertEqual(
            capture.client_fingerprint(dict(_TELNET_FLAGS), "telnet"),
            capture.client_fingerprint(dict(_TELNET_FLAGS), "telnet"),
        )

    def test_window_resize_does_not_change_fingerprint(self):
        resized = dict(_TELNET_FLAGS, SCREENWIDTH={0: 80}, SCREENHEIGHT={0: 24})
        self.assertEqual(
            capture.client_fingerprint(dict(_TELNET_FLAGS), "telnet"),
            capture.client_fingerprint(resized, "telnet"),
        )

    def test_different_client_changes_fingerprint(self):
        other = dict(_TELNET_FLAGS, CLIENTNAME="TinTin++")
        self.assertNotEqual(
            capture.client_fingerprint(dict(_TELNET_FLAGS), "telnet"),
            capture.client_fingerprint(other, "telnet"),
        )

    def test_capability_change_changes_fingerprint(self):
        other = dict(_TELNET_FLAGS, MCCP=False)
        self.assertNotEqual(
            capture.client_fingerprint(dict(_TELNET_FLAGS), "telnet"),
            capture.client_fingerprint(other, "telnet"),
        )

    def test_same_flags_on_different_protocol_differ(self):
        self.assertNotEqual(
            capture.client_fingerprint(dict(_TELNET_FLAGS), "telnet"),
            capture.client_fingerprint(dict(_TELNET_FLAGS), "websocket"),
        )

    def test_empty_flags_yield_empty(self):
        self.assertEqual(capture.client_fingerprint({}, "telnet"), "")


class SnapshotTest(TestCase):
    def test_snapshot_reads_expected_fields(self):
        snap = capture.snapshot_session(make_session())
        self.assertEqual(snap["ip"], "203.0.113.7")
        self.assertEqual(snap["cidr"], "203.0.113.0/24")
        self.assertEqual(snap["peer_ip"], "203.0.113.7")
        self.assertEqual(snap["client_name"], "Mudlet")
        self.assertEqual(snap["term"], "xterm-256color")
        self.assertEqual(snap["screen_w"], 132)
        self.assertEqual(snap["screen_h"], 43)
        self.assertEqual(snap["command_count"], 12)
        self.assertTrue(snap["client_fp"])
        self.assertTrue(snap["ip_hash"])

    def test_flags_snapshot_is_json_safe(self):
        import json

        snap = capture.snapshot_session(make_session())
        # SCREENWIDTH arrives keyed by int; JSONField cannot store that.
        json.dumps(snap["flags"])
        self.assertEqual(snap["flags"]["SCREENWIDTH"], {"0": 132})

    def test_broken_session_yields_empty_instead_of_raising(self):
        class Hostile:
            def __getattr__(self, name):
                raise RuntimeError("boom")

        self.assertEqual(capture.snapshot_session(Hostile()), {})

    def test_missing_puppet_is_not_fatal(self):
        def explode():
            raise RuntimeError("no puppet")

        snap = capture.snapshot_session(make_session(get_puppet=explode))
        self.assertEqual(snap["puppet_name"], "")


class PersistTest(TestCase):
    def test_login_then_disconnect_updates_one_row(self):
        snap = capture.snapshot_session(make_session())
        capture.persist_snapshot(snap, "login")
        capture.persist_snapshot(dict(snap, disconnect_reason="quit"), "disconnect")

        self.assertEqual(SessionRecord.objects.count(), 1)
        row = SessionRecord.objects.get()
        self.assertIsNotNone(row.login_at)
        self.assertIsNotNone(row.disconnected_at)
        self.assertEqual(row.disconnect_reason, "quit")
        self.assertEqual(row.cidr, "203.0.113.0/24")

    def test_session_that_never_logged_in_still_gets_a_row(self):
        snap = capture.snapshot_session(make_session())
        capture.persist_snapshot(dict(snap, disconnect_reason="probe"), "disconnect")

        row = SessionRecord.objects.get()
        self.assertIsNone(row.login_at)
        self.assertEqual(row.account_name, "")
        self.assertEqual(row.disconnect_reason, "probe")

    def test_snapshot_without_uid_writes_nothing(self):
        capture.persist_snapshot({"ip": "203.0.113.7"}, "login")
        self.assertEqual(SessionRecord.objects.count(), 0)

    def test_address_trust_flag(self):
        snap = capture.snapshot_session(make_session())
        capture.persist_snapshot(snap, "login")
        self.assertTrue(SessionRecord.objects.get().address_is_trustworthy)

    def test_untrusted_forward_is_marked(self):
        flags = dict(_TELNET_FLAGS, XFF_PRESENT=True, XFF_APPLIED=False, PEER_IP="10.0.0.9")
        snap = capture.snapshot_session(make_session(protocol_flags=flags))
        capture.persist_snapshot(snap, "login")
        row = SessionRecord.objects.get()
        self.assertFalse(row.address_is_trustworthy)
