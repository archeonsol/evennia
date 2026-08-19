"""Negotiation and HTTP handshake fingerprints.

These are the signals that separate two clients reporting the same capability
set: the order and timing in which a telnet client answers option negotiation,
and the header shape a browser presents at the websocket handshake.
"""

from types import SimpleNamespace

from django.test import TestCase

from evennia.moderation import capture
from evennia.server.models import SessionRecord

from .test_capture import make_session

_HEADERS = {
    "user-agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/141.0.0.0",
    "accept-language": "en-GB,en;q=0.9",
    "accept-encoding": "gzip, deflate, br",
    "sec-ch-ua-platform": '"Linux"',
}


class NegotiationCaptureTest(TestCase):
    def test_order_and_timing_survive_the_snapshot(self):
        session = make_session(
            protocol_flags=dict(
                capture.sanitize_flags({}),
                NEG_ORDER=["SUPPRESS_GA", "TTYPE", "NAWS", "GMCP"],
                NEG_TIMING_MS={"SUPPRESS_GA": 4.2, "TTYPE": 61.0},
            )
        )
        snap = capture.snapshot_session(session)
        self.assertEqual(snap["neg_order"], ["SUPPRESS_GA", "TTYPE", "NAWS", "GMCP"])
        self.assertEqual(snap["neg_timing_ms"], {"SUPPRESS_GA": 4.2, "TTYPE": 61.0})

    def test_order_is_not_sorted(self):
        # The whole signal is the sequence. Sorting it would destroy it.
        first = capture._neg_order({"NEG_ORDER": ["TTYPE", "NAWS"]})
        second = capture._neg_order({"NEG_ORDER": ["NAWS", "TTYPE"]})
        self.assertNotEqual(first, second)

    def test_absent_flags_yield_empty_containers(self):
        self.assertEqual(capture._neg_order({}), [])
        self.assertEqual(capture._neg_timing({}), {})

    def test_malformed_flags_do_not_raise(self):
        self.assertEqual(capture._neg_order({"NEG_ORDER": "TTYPE"}), [])
        self.assertEqual(capture._neg_timing({"NEG_TIMING_MS": ["TTYPE"]}), {})
        self.assertEqual(capture._neg_timing({"NEG_TIMING_MS": {"TTYPE": "soon"}}), {})

    def test_timings_are_bounded(self):
        # A client that answers a hundred options must not write a hundred keys.
        flags = {"NEG_ORDER": [f"OPT{i}" for i in range(100)]}
        self.assertEqual(len(capture._neg_order(flags)), 32)


class HttpFingerprintTest(TestCase):
    def test_same_headers_same_hash(self):
        self.assertEqual(
            capture.http_fingerprint({"HTTP_FP": dict(_HEADERS)}),
            capture.http_fingerprint({"HTTP_FP": dict(reversed(list(_HEADERS.items())))}),
        )

    def test_header_change_changes_hash(self):
        other = dict(_HEADERS, **{"accept-language": "tr-TR,tr;q=0.9"})
        self.assertNotEqual(
            capture.http_fingerprint({"HTTP_FP": _HEADERS}),
            capture.http_fingerprint({"HTTP_FP": other}),
        )

    def test_telnet_has_no_http_fingerprint(self):
        self.assertEqual(capture.http_fingerprint({}), "")
        self.assertEqual(capture.http_fingerprint({"HTTP_FP": {}}), "")
        self.assertEqual(capture.http_fingerprint({"HTTP_FP": "Mozilla/5.0"}), "")

    def test_user_agent_is_stored_readable(self):
        session = make_session(protocol_key="websocket", protocol_flags={"HTTP_FP": _HEADERS})
        snap = capture.snapshot_session(session)
        self.assertEqual(snap["user_agent"], _HEADERS["user-agent"])
        self.assertEqual(len(snap["http_fp"]), 64)

    def test_device_token_comes_from_flags(self):
        session = make_session(protocol_flags={"DEVICE_TOKEN": "d" * 64})
        self.assertEqual(capture.snapshot_session(session)["device_token"], "d" * 64)


class PersistFingerprintTest(TestCase):
    def test_fields_reach_the_row(self):
        session = make_session(
            protocol_key="websocket",
            protocol_flags=dict(
                HTTP_FP=_HEADERS,
                DEVICE_TOKEN="a" * 64,
                NEG_ORDER=["TTYPE"],
                NEG_TIMING_MS={"TTYPE": 12.5},
            ),
        )
        snap = capture.snapshot_session(session)
        capture.persist_snapshot(snap, phase="login")

        row = SessionRecord.objects.get(session_uid=snap["session_uid"])
        self.assertEqual(row.neg_order, ["TTYPE"])
        self.assertEqual(row.neg_timing_ms, {"TTYPE": 12.5})
        self.assertEqual(row.device_token, "a" * 64)
        self.assertEqual(row.user_agent, _HEADERS["user-agent"])
        self.assertEqual(row.http_fp, capture.http_fingerprint({"HTTP_FP": _HEADERS}))


class WebclientHeaderCollectionTest(TestCase):
    """The portal-side read, exercised without standing up a websocket."""

    def _collect(self, headers):
        from evennia.server.portal.webclient import WebSocketClient

        stub = SimpleNamespace(http_headers=headers)
        return WebSocketClient._collect_http_fingerprint(stub)

    def test_only_listed_headers_are_kept(self):
        collected = self._collect(dict(_HEADERS, cookie="sessionid=secret", host="underspire.net"))
        self.assertNotIn("cookie", collected)
        self.assertNotIn("host", collected)
        self.assertEqual(collected["user-agent"], _HEADERS["user-agent"])

    def test_values_are_truncated(self):
        collected = self._collect({"user-agent": "M" * 4000})
        self.assertEqual(len(collected["user-agent"]), 255)

    def test_missing_headers_are_omitted_not_blanked(self):
        self.assertEqual(self._collect({"user-agent": "curl/8.4.0"}), {"user-agent": "curl/8.4.0"})

    def test_broken_headers_yield_empty_dict(self):
        self.assertEqual(self._collect(None), {})
        self.assertEqual(self._collect("not-a-mapping"), {})
