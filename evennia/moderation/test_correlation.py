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
        self.assertIsNone(detect.detect_identity_correlation(self.snapshot(account_name="evader")))

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
        flag = detect.detect_identity_correlation(self.snapshot(telnet_sig="", csessid="browser-1"))
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


class TestTLSSignature(TestCase):
    """The handshake a reverse proxy terminated, as it reported it."""

    def tls(self, **kwargs):
        base = {
            "x-tls-version": "TLSv1.3",
            "x-tls-ciphers": "1301:1302:1303:c02b:c02f",
            "x-tls-curves": "001d:0017:0018",
            "x-tls-alpn": "h2",
        }
        base.update(kwargs)
        return {"TLS_FP": base}

    def test_the_same_handshake_gives_the_same_value(self):
        first = capture.tls_signature(self.tls())
        self.assertTrue(first)
        self.assertEqual(first, capture.tls_signature(self.tls()))

    def test_a_different_cipher_list_differs(self):
        self.assertNotEqual(
            capture.tls_signature(self.tls()),
            capture.tls_signature(self.tls(**{"x-tls-ciphers": "1301:c030"})),
        )

    def test_grease_is_stripped(self):
        # The whole reason JA3 aged badly. Chrome inserts a random reserved
        # value on every connection; a fingerprint that keeps it changes every
        # time, and staff read "different fingerprint" as "different person".
        plain = capture.tls_signature(self.tls())
        greased = capture.tls_signature(
            self.tls(**{"x-tls-ciphers": "3a3a:1301:1302:1303:c02b:c02f"})
        )
        other_grease = capture.tls_signature(
            self.tls(**{"x-tls-ciphers": "1301:1302:baba:1303:c02b:c02f"})
        )
        self.assertEqual(plain, greased)
        self.assertEqual(plain, other_grease)

    def test_grease_in_the_curves_is_stripped_too(self):
        self.assertEqual(
            capture.tls_signature(self.tls()),
            capture.tls_signature(self.tls(**{"x-tls-curves": "caca:001d:0017:0018"})),
        )

    def test_a_real_value_that_looks_close_is_kept(self):
        # 0x0a0a is GREASE; 0x0a0b is not. The rule is both bytes equal and
        # both nibbles A, not "contains an a".
        self.assertFalse(capture._is_grease("0a0b"))
        self.assertFalse(capture._is_grease("1a2a"))
        self.assertTrue(capture._is_grease("0a0a"))
        self.assertTrue(capture._is_grease("fafa"))

    def test_the_order_the_proxy_reports_does_not_matter(self):
        # A client's cipher order is stable per build, but the proxy is free to
        # report them however it read them, and a fingerprint that depends on
        # the proxy breaks when the proxy is upgraded.
        self.assertEqual(
            capture.tls_signature(self.tls()),
            capture.tls_signature(self.tls(**{"x-tls-ciphers": "c02f:1303:1301:c02b:1302"})),
        )

    def test_no_headers_means_no_signature(self):
        # Which is every deployment that has not configured its proxy, and
        # every connection whose peer was not trusted.
        self.assertEqual(capture.tls_signature({}), "")
        self.assertEqual(capture.tls_signature({"TLS_FP": {}}), "")


class TestHeaderOrderSignature(TestCase):
    """Which headers a client sent, and in what order."""

    def test_order_changes_the_value(self):
        first = capture.header_order_signature(
            {"HTTP_ORDER": ["Host", "User-Agent", "Accept-Language"]}
        )
        second = capture.header_order_signature(
            {"HTTP_ORDER": ["Host", "Accept-Language", "User-Agent"]}
        )
        self.assertTrue(first)
        self.assertNotEqual(first, second)

    def test_casing_is_part_of_it(self):
        self.assertNotEqual(
            capture.header_order_signature({"HTTP_ORDER": ["User-Agent"]}),
            capture.header_order_signature({"HTTP_ORDER": ["user-agent"]}),
        )

    def test_proxy_headers_are_ignored(self):
        # A proxy adds and reorders these, so including them would fingerprint
        # the hop rather than the client.
        bare = capture.header_order_signature({"HTTP_ORDER": ["Host", "User-Agent"]})
        proxied = capture.header_order_signature(
            {
                "HTTP_ORDER": [
                    "Host",
                    "X-Forwarded-For",
                    "User-Agent",
                    "X-Real-IP",
                    "Connection",
                    "X-TLS-Ciphers",
                ]
            }
        )
        self.assertEqual(bare, proxied)

    def test_nothing_sent_means_no_signature(self):
        self.assertEqual(capture.header_order_signature({}), "")
        self.assertEqual(capture.header_order_signature({"HTTP_ORDER": []}), "")

    def test_a_list_of_only_proxy_headers_is_no_signature(self):
        self.assertEqual(
            capture.header_order_signature({"HTTP_ORDER": ["X-Forwarded-For", "Connection"]}), ""
        )


class TestCorrelationSignatures(TestCase):
    """Which signatures raise a correlation flag, and which deliberately do not.

    The handshake exists for the case cookies cannot cover: somebody who was
    banned clears their browser data and comes back. Everything they cleared is
    gone; the handshake their browser performs is not.
    """

    def snapshot(self, **kwargs):
        base = {
            "session_uid": "probe",
            "cidr": "104.16.0.0/24",
            "telnet_sig": "",
            "csessid": "",
            "tls_sig": "",
            "http_order_fp": "",
            "account_name": "",
        }
        base.update(kwargs)
        return base

    def test_a_shared_handshake_on_a_shared_network_flags(self):
        _session(account_name="evader", telnet_sig="", tls_sig="tls-a")
        _ban("evader")
        flag = detect.detect_identity_correlation(self.snapshot(tls_sig="tls-a"))
        self.assertIsNotNone(flag)
        self.assertEqual(flag.kind, ModerationFlag.KIND_IDENTITY_CORRELATION)
        self.assertIn("browser handshake", flag.evidence["matched_on"])

    def test_a_different_handshake_does_not_flag(self):
        _session(account_name="evader", telnet_sig="", tls_sig="tls-a")
        _ban("evader")
        self.assertIsNone(detect.detect_identity_correlation(self.snapshot(tls_sig="tls-b")))

    def test_a_cleared_cookie_no_longer_hides_the_match(self):
        # The whole reason every signature is checked instead of the first one
        # present. Before this, a session with a csessid that matched nothing
        # was never compared on its handshake.
        _session(account_name="evader", telnet_sig="", tls_sig="tls-a", csessid="old-browser")
        _ban("evader")
        flag = detect.detect_identity_correlation(
            self.snapshot(tls_sig="tls-a", csessid="new-browser")
        )
        self.assertIsNotNone(flag)

    def test_a_shared_header_order_alone_never_flags(self):
        # Header order identifies a browser build, not a person. On any busy
        # network it would match most web players at once, and a flag that
        # fires for everybody teaches staff to skip the queue.
        _session(account_name="evader", telnet_sig="", http_order_fp="order-a")
        _ban("evader")
        self.assertIsNone(
            detect.detect_identity_correlation(self.snapshot(http_order_fp="order-a"))
        )

    def test_header_order_is_not_in_the_trigger_list(self):
        # Stated directly, so removing it from the docstring cannot quietly
        # remove it from the rule.
        self.assertNotIn("http_order_fp", [field for field, _ in detect.CORRELATION_SIGNATURES])

    def test_two_signatures_matching_are_both_named(self):
        _session(account_name="evader", telnet_sig="sig-a", tls_sig="tls-a")
        _ban("evader")
        flag = detect.detect_identity_correlation(
            self.snapshot(telnet_sig="sig-a", tls_sig="tls-a")
        )
        self.assertEqual(
            sorted(flag.evidence["matched_on"]), ["browser handshake", "client negotiation"]
        )

    def test_an_unblocked_neighbour_does_not_flag(self):
        # A household on one address using the same browser is the normal case,
        # and it must stay silent.
        _session(account_name="sibling", telnet_sig="", tls_sig="tls-a")
        self.assertIsNone(detect.detect_identity_correlation(self.snapshot(tls_sig="tls-a")))

    def test_the_handshake_works_for_a_session_with_no_account(self):
        # Somebody banned reconnects and sits at the login prompt. There is no
        # account name to match on, which is exactly when this has to work.
        _session(account_name="evader", telnet_sig="", tls_sig="tls-a")
        _ban("evader")
        flag = detect.detect_identity_correlation(self.snapshot(tls_sig="tls-a", account_name=""))
        self.assertIsNotNone(flag)
        self.assertIn("unauthenticated", flag.summary)
