"""Tests for the telnet signature and the compound correlation flag.

Both exist because the two rules at the top of the moderation document hold:
hard signals only, and detection writes flags while people act.

The telnet signature is a hard signal in the strict sense -- it is what the
client's own stack did, reproducible and explainable to the player it is used
against. It is deliberately narrower than ``client_fp``, which folds in the
client name and terminal type a player types into a settings box.

The correlation flag is the one that needs the most care, because both halves
of it are weak. A shared network is a household or a campus. A shared client
signature is two people who both use Mudlet. Either alone would flag half the
player base, so the tests below assert that neither alone fires.

"""

from django.test import TestCase, override_settings

from evennia.moderation import capture, detect
from evennia.server.models import ModerationFlag, Sanction, SessionRecord

MUDLET = {
    "NEG_ORDER": ["TTYPE", "NAWS", "GMCP", "MCCP"],
    "GMCP": True,
    "MCCP": True,
    "TTYPE": True,
    "CLIENTNAME": "Mudlet",
    "TERM": "xterm-256color",
}

TINTIN = {
    "NEG_ORDER": ["NAWS", "TTYPE", "MSDP"],
    "MSDP": True,
    "TTYPE": True,
    "CLIENTNAME": "TinTin++",
    "TERM": "xterm",
}


class TestNegotiationSignature(TestCase):
    """What the client's stack did, not what the player typed."""

    def test_the_same_client_gives_the_same_value(self):
        first = capture.negotiation_signature(MUDLET, "telnet")
        second = capture.negotiation_signature(dict(MUDLET), "telnet")
        self.assertEqual(first, second)
        self.assertTrue(first)

    def test_two_clients_differ(self):
        self.assertNotEqual(
            capture.negotiation_signature(MUDLET, "telnet"),
            capture.negotiation_signature(TINTIN, "telnet"),
        )

    def test_renaming_the_client_does_not_change_it(self):
        # The point of this signature. client_fp moves when a player edits a
        # settings box; evading this one means changing client.
        disguised = dict(MUDLET, CLIENTNAME="TinTin++", TERM="vt100")
        self.assertEqual(
            capture.negotiation_signature(MUDLET, "telnet"),
            capture.negotiation_signature(disguised, "telnet"),
        )

    def test_client_fp_does_move_when_renamed(self):
        # Stated as a contrast, so the reason both columns exist stays visible.
        disguised = dict(MUDLET, CLIENTNAME="TinTin++", TERM="vt100")
        self.assertNotEqual(
            capture.client_fingerprint(MUDLET, "telnet"),
            capture.client_fingerprint(disguised, "telnet"),
        )

    def test_the_order_matters(self):
        # Two clients supporting the same options still ask in their own order,
        # which is the part that identifies the library.
        reordered = dict(MUDLET, NEG_ORDER=["GMCP", "TTYPE", "MCCP", "NAWS"])
        self.assertNotEqual(
            capture.negotiation_signature(MUDLET, "telnet"),
            capture.negotiation_signature(reordered, "telnet"),
        )

    def test_a_session_that_negotiated_nothing_has_no_signature(self):
        # Every web-client connection, and any raw socket.
        self.assertEqual(capture.negotiation_signature({}, "websocket"), "")
        self.assertEqual(capture.negotiation_signature({"NEG_ORDER": []}, "telnet"), "")

    def test_the_protocol_is_part_of_it(self):
        self.assertNotEqual(
            capture.negotiation_signature(MUDLET, "telnet"),
            capture.negotiation_signature(MUDLET, "ssh"),
        )


def _session(**kwargs):
    """Write one session row."""

    fields = {
        "session_uid": f"u{SessionRecord.objects.count() + 1:028d}",
        "cidr": "104.16.0.0/24",
        "telnet_sig": "sig-a",
        "account_name": "",
    }
    fields.update(kwargs)
    return SessionRecord.objects.create(**fields)


def _ban(name):
    """Block one account."""

    return Sanction.objects.create(
        subject_type=Sanction.SUBJECT_ACCOUNT,
        subject_value=name.lower(),
        level=Sanction.LEVEL_BAN,
        reason="test",
    )


class TestCorrelation(TestCase):
    """Two weak signals together, pointing at a blocked account."""

    def snapshot(self, **kwargs):
        base = {
            "session_uid": "probe",
            "cidr": "104.16.0.0/24",
            "telnet_sig": "sig-a",
            "account_name": "",
        }
        base.update(kwargs)
        return base

    def test_a_shared_network_alone_does_not_flag(self):
        # A /24 is a household, a campus, or one carrier-grade NAT.
        _session(account_name="evader", telnet_sig="different")
        _ban("evader")
        self.assertIsNone(detect.detect_identity_correlation(self.snapshot()))

    def test_a_shared_signature_alone_does_not_flag(self):
        # Two people who both use Mudlet.
        _session(account_name="evader", cidr="198.51.100.0/24")
        _ban("evader")
        self.assertIsNone(detect.detect_identity_correlation(self.snapshot()))

    def test_both_together_flag(self):
        _session(account_name="evader")
        _ban("evader")
        flag = detect.detect_identity_correlation(self.snapshot())
        self.assertIsNotNone(flag)
        self.assertEqual(flag.kind, ModerationFlag.KIND_IDENTITY_CORRELATION)

    def test_an_unblocked_neighbour_does_not_flag(self):
        # The whole household shares both halves. Only a sanction makes it a
        # signal.
        _session(account_name="housemate")
        self.assertIsNone(detect.detect_identity_correlation(self.snapshot()))

    def test_it_fires_for_an_unauthenticated_session(self):
        # The reason this detector exists beside the key-reuse one: a banned
        # person reconnecting sits at the login prompt with no account name,
        # and that is the moment there is anything to see.
        _session(account_name="evader")
        _ban("evader")
        flag = detect.detect_identity_correlation(self.snapshot(account_name=""))
        self.assertIsNotNone(flag)
        self.assertIn("unauthenticated", flag.summary)

    def test_it_does_not_flag_the_account_against_itself(self):
        _session(account_name="evader")
        _ban("evader")
        self.assertIsNone(
            detect.detect_identity_correlation(self.snapshot(account_name="evader"))
        )

    def test_a_session_with_no_network_is_skipped(self):
        _session(account_name="evader")
        _ban("evader")
        self.assertIsNone(detect.detect_identity_correlation(self.snapshot(cidr="")))

    def test_a_session_with_no_signature_is_skipped(self):
        # An empty signature is no signal. Two blanks are not the same client.
        _session(account_name="evader", telnet_sig="")
        _ban("evader")
        self.assertIsNone(
            detect.detect_identity_correlation(self.snapshot(telnet_sig="", csessid=""))
        )

    def test_the_browser_session_works_as_the_second_half(self):
        # Web-client players have no telnet signature at all, so the detector
        # falls through to the key they do have.
        _session(account_name="evader", telnet_sig="", csessid="browser-1")
        _ban("evader")
        flag = detect.detect_identity_correlation(
            self.snapshot(telnet_sig="", csessid="browser-1")
        )
        self.assertIsNotNone(flag)

    def test_the_evidence_names_both_halves(self):
        # A staff member has to be able to explain the match to the player it
        # is used against.
        _session(account_name="evader")
        _ban("evader")
        flag = detect.detect_identity_correlation(self.snapshot())
        self.assertEqual(flag.evidence["cidr"], "104.16.0.0/24")
        self.assertEqual(flag.evidence["accounts"], ["evader"])
        self.assertTrue(flag.evidence["matched_on"])

    def test_it_sanctions_nobody(self):
        _session(account_name="evader")
        _ban("evader")
        before = Sanction.objects.count()
        detect.detect_identity_correlation(self.snapshot())
        self.assertEqual(Sanction.objects.count(), before)

    def test_it_is_registered(self):
        self.assertIn(detect.detect_identity_correlation, detect.DETECTORS)


class TestKeyReuseTakesTheSignature(TestCase):
    """The telnet signature joins the strong keys."""

    def test_a_shared_signature_with_a_blocked_account_flags(self):
        _session(account_name="evader", telnet_sig="sig-a", cidr="198.51.100.0/24")
        _ban("evader")
        flag = detect.detect_sanctioned_key_reuse(
            {
                "session_uid": "probe",
                "account_name": "newname",
                "telnet_sig": "sig-a",
                "cidr": "104.16.0.0/24",
            }
        )
        self.assertIsNotNone(flag)
        self.assertEqual(flag.kind, ModerationFlag.KIND_SANCTIONED_KEY)
