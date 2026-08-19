"""Address-provenance checks.

A reverse proxy that is not trusted by ``UPSTREAM_IPS`` makes every recorded web
address the proxy's own. These checks exist so that fault is visible before it is
used as the basis for a ban.
"""

from django.test import TestCase, override_settings

from evennia.moderation import checks
from evennia.server.models import SessionRecord


def make_row(uid, **overrides):
    fields = {
        "session_uid": uid,
        "protocol": "websocket",
        "ip": "203.0.113.7",
        "peer_ip": "10.0.0.1",
        "xff_applied": True,
        "xff_present": True,
    }
    fields.update(overrides)
    return SessionRecord.objects.create(**fields)


class ConfigWarningTest(TestCase):
    @override_settings(WEBSOCKET_CLIENT_URL="wss://underspire.net/ws", UPSTREAM_IPS=["127.0.0.1"])
    def test_public_host_with_default_upstreams_warns(self):
        warning = checks.config_warning()
        self.assertIn("UPSTREAM_IPS", warning)
        self.assertIn("underspire.net", warning)

    @override_settings(
        WEBSOCKET_CLIENT_URL="wss://underspire.net/ws",
        UPSTREAM_IPS=["127.0.0.1", "10.0.0.1"],
    )
    def test_configured_upstream_is_quiet(self):
        self.assertEqual(checks.config_warning(), "")

    @override_settings(WEBSOCKET_CLIENT_URL="ws://localhost:4002/ws", UPSTREAM_IPS=["127.0.0.1"])
    def test_local_development_is_quiet(self):
        self.assertEqual(checks.config_warning(), "")

    @override_settings(WEBSOCKET_CLIENT_URL="", UPSTREAM_IPS=["127.0.0.1"])
    def test_unset_url_is_quiet(self):
        self.assertEqual(checks.config_warning(), "")


class RecordedSessionTest(TestCase):
    def test_no_data(self):
        self.assertEqual(checks.check_recorded_sessions()["verdict"], "no_data")

    def test_trusted_forwarding_reads_ok(self):
        make_row("a" * 30 + "01", ip="203.0.113.7")
        make_row("a" * 30 + "02", ip="198.51.100.4")
        result = checks.check_recorded_sessions()
        self.assertEqual(result["verdict"], "ok")
        self.assertEqual(result["applied"], 2)
        self.assertEqual(result["distinct_ips"], 2)

    def test_rejected_forwarding_is_a_fault(self):
        make_row("b" * 30 + "01", xff_applied=False, xff_present=True, ip="10.0.0.1")
        result = checks.check_recorded_sessions()
        self.assertEqual(result["verdict"], "untrusted")
        self.assertEqual(result["untrusted"], 1)
        self.assertIn("FAULT", "\n".join(checks.report_lines()))

    def test_direct_connections_are_not_a_fault(self):
        make_row("c" * 30 + "01", xff_applied=False, xff_present=False, ip="203.0.113.7")
        make_row("c" * 30 + "02", xff_applied=False, xff_present=False, ip="198.51.100.4")
        result = checks.check_recorded_sessions()
        self.assertEqual(result["verdict"], "direct")
        self.assertNotIn("FAULT", "\n".join(checks.report_lines()))

    def test_every_web_session_sharing_one_address_is_a_fault(self):
        # The shape a silently-unrewritten proxy produces: no header, one address.
        for index in range(3):
            make_row(
                "d" * 30 + "0%s" % index,
                xff_applied=False,
                xff_present=False,
                ip="10.0.0.1",
            )
        self.assertIn("FAULT", "\n".join(checks.report_lines()))

    def test_telnet_rows_are_not_counted(self):
        make_row("e" * 30 + "01", protocol="telnet", xff_applied=False, xff_present=False)
        self.assertEqual(checks.check_recorded_sessions()["verdict"], "no_data")
