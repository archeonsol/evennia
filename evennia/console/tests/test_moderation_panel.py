"""Tests for the moderation queue.

Two properties carry the weight, and both are about what an operator is shown
rather than what the code does.

Masking and withholding must look different. A truncated fingerprint still
identifies a row; a redaction does not, and a staffer who reads one as the
other compares two rows that were never comparable.

Untrustworthy addresses must be stated as a sentence. When a proxy header was
sent but not trusted, the recorded address is the proxy's own -- and somebody
reading two booleans off a row will eventually ban a reverse proxy.

"""

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from evennia.console.models import ConsoleAuditEvent
from evennia.console.panels.moderation import ModerationPanel, mask
from evennia.console.registry import (
    CONSOLE_ACCESS,
    CONSOLE_MODERATION,
    CONSOLE_MODERATION_ADDRESS,
    CONSOLE_MODERATION_PERMANENT,
    IOContext,
    WorkerContext,
)
from evennia.server.models import ModerationFlag, Sanction, SessionRecord


def _ctx(**params):
    """Build a worker context."""
    return WorkerContext(
        actor_id=1, actor_name="t", capabilities=frozenset({CONSOLE_ACCESS}), params=params
    )


class ModerationTestCase(TestCase):
    """A staff actor the services will accept."""

    def setUp(self):
        self.panel = ModerationPanel()
        self.actor = get_user_model().objects.create(
            username="moderator", is_active=True, is_staff=True, is_superuser=True
        )

    #: Senior staff: may ban a network and may make a ban permanent. Most
    #: tests here exercise what a sanction does rather than who may issue one,
    #: so this is the default and the guards get their own fixtures below.
    SENIOR = frozenset({CONSOLE_MODERATION_ADDRESS, CONSOLE_MODERATION_PERMANENT})

    def io(self, capabilities=None):
        """Return an IO context for the acting staff account."""
        return IOContext(
            actor_id=self.actor.pk,
            actor_name="moderator",
            capabilities=self.SENIOR if capabilities is None else frozenset(capabilities),
        )

    def junior(self, actor_id=None):
        """Return an IO context with neither authority."""
        return IOContext(
            actor_id=self.actor.pk if actor_id is None else actor_id,
            actor_name="junior",
            capabilities=frozenset(),
        )

    def worker(self, capabilities=None, **params):
        """Return a worker context for the acting staff account."""
        return WorkerContext(
            actor_id=self.actor.pk,
            actor_name="moderator",
            capabilities=self.SENIOR if capabilities is None else frozenset(capabilities),
            params=params,
        )

    def make_flag(self, **kwargs):
        """Create one moderation flag."""
        defaults = {
            "kind": ModerationFlag.KIND_SHARED_DEVICE,
            "severity": 5,
            "account_name": "suspect",
            "summary": "two accounts share a device",
            "dedupe_key": f"key-{ModerationFlag.objects.count()}",
            "evidence": {"device_token": "abcdef1234567890", "accounts": "a, b"},
        }
        defaults.update(kwargs)
        return ModerationFlag.objects.create(**defaults)


class TestMasking(TestCase):
    """Shortening and withholding are different acts."""

    def test_withheld_is_not_a_short_value(self):
        self.assertEqual(mask("abcdef1234567890"), "withheld")

    def test_revealed_is_shortened_for_reading(self):
        shown = mask("abcdef1234567890", reveal=True)
        self.assertTrue(shown.startswith("abcdef12"))
        self.assertNotEqual(shown, "withheld")

    def test_a_short_value_is_not_given_an_ellipsis(self):
        self.assertEqual(mask("abc", reveal=True), "abc")

    def test_an_empty_value_stays_empty(self):
        # "" and "withheld" mean different things: nothing recorded versus
        # something recorded and not shown.
        self.assertEqual(mask(""), "")
        self.assertEqual(mask(None), "")


class TestQueue(ModerationTestCase):
    """The queue itself."""

    def test_open_flags_appear(self):
        self.make_flag()
        result = self.panel.rows(_ctx())
        self.assertEqual(len(result["flags"]), 1)
        self.assertEqual(result["open_flags"], 1)

    def test_resolved_flags_are_hidden_by_default(self):
        self.make_flag(state=ModerationFlag.STATE_DISMISSED)
        self.assertEqual(len(self.panel.rows(_ctx())["flags"]), 0)

    def test_a_state_filter_finds_them(self):
        self.make_flag(state=ModerationFlag.STATE_DISMISSED)
        self.assertEqual(len(self.panel.rows(_ctx(state="dismissed"))["flags"]), 1)

    def test_queries_are_capped(self):
        result = self.panel.rows(_ctx())
        self.assertEqual(result["caps"]["flags"], 40)
        self.assertEqual(result["caps"]["sessions"], 25)

    def test_the_vocabulary_is_offered(self):
        result = self.panel.rows(_ctx())
        self.assertIn("ban", result["levels"])
        self.assertIn("cidr", result["subject_types"])

    def test_the_panel_states_that_it_decides_nothing(self):
        self.assertIn("person", self.panel.rows(_ctx())["note"])


class TestSessionProvenance(ModerationTestCase):
    """Address trustworthiness is a sentence, not two booleans."""

    def _session(self, **kwargs):
        defaults = {
            "session_uid": f"uid{SessionRecord.objects.count():04d}",
            "account_name": "player",
            "protocol": "telnet",
            "ip": "203.0.113.7",
            "cidr": "203.0.113.0/24",
            "client_fp": "fingerprint0123456789",
            "device_token": "token0123456789",
        }
        defaults.update(kwargs)
        return SessionRecord.objects.create(**defaults)

    def test_addresses_are_masked_by_default(self):
        self._session()
        row = self.panel.rows(_ctx())["sessions"][0]
        self.assertEqual(row["ip"], "withheld")
        self.assertEqual(row["client_fp"], "withheld")

    def test_the_network_is_shown_without_the_address(self):
        # Most moderation work needs the network, not the address, and an
        # appeal never requires that staff saw the address.
        self._session()
        row = self.panel.rows(_ctx())["sessions"][0]
        self.assertEqual(row["cidr"], "203.0.113.0/24")

    def test_a_trusted_address_carries_no_warning(self):
        self._session(xff_present=True, xff_applied=True)
        row = self.panel.rows(_ctx())["sessions"][0]
        self.assertTrue(row["address_trustworthy"])
        self.assertEqual(row["address_warning"], "")

    def test_an_untrusted_proxy_header_is_spelled_out(self):
        self._session(xff_present=True, xff_applied=False)
        row = self.panel.rows(_ctx())["sessions"][0]
        self.assertFalse(row["address_trustworthy"])
        self.assertIn("do not sanction", row["address_warning"].lower())

    def test_recent_rows_carry_readable_client_and_geoip_context(self):
        self._session(
            client_name="Mudlet",
            term="xterm-256color",
            encoding="utf-8",
            screen_w=160,
            screen_h=48,
            user_agent="Mozilla/5.0 Test Browser",
            asn=64500,
            asn_org="Example Transit",
            country="TR",
            is_datacenter=False,
            is_tor=True,
        )

        row = self.panel.rows(_ctx())["sessions"][0]

        self.assertEqual(row["client_name"], "Mudlet")
        self.assertEqual(row["term"], "xterm-256color")
        self.assertEqual(row["encoding"], "utf-8")
        self.assertEqual(row["screen"], "160 × 48")
        self.assertEqual(row["user_agent"], "Mozilla/5.0 Test Browser")
        self.assertEqual(row["asn"], 64500)
        self.assertEqual(row["network"], "Example Transit")
        self.assertEqual(row["country"], "TR")
        self.assertFalse(row["is_datacenter"])
        self.assertTrue(row["is_tor"])


class TestConnectionDossier(ModerationTestCase):
    """Opening a connection exposes every field the capture model stores."""

    def _session(self):
        return SessionRecord.objects.create(
            session_uid="full-record",
            sessid=42,
            protocol="webclient/websocket",
            account_id=17,
            account_name="player",
            puppet_name="Player Character",
            ip="203.0.113.7",
            ip_hash="i" * 64,
            cidr="203.0.113.0/24",
            peer_ip="127.0.0.1",
            xff_applied=True,
            xff_present=True,
            asn=64500,
            asn_org="Example Transit",
            country="TR",
            is_datacenter=False,
            is_tor=True,
            client_fp="c" * 64,
            client_name="Evennia Webclient",
            term="xterm-256color",
            encoding="utf-8",
            screen_w=160,
            screen_h=48,
            telnet_sig="n" * 64,
            neg_order=["TTYPE", "NAWS"],
            neg_timing_ms={"TTYPE": 12.4},
            flags={"CLIENTNAME": "Evennia Webclient", "ANSI": True},
            csessid="browser-session",
            device_token="device-token",
            http_fp="h" * 64,
            http_order_fp="o" * 64,
            tls_sig="t" * 64,
            user_agent="Mozilla/5.0 Test Browser",
            command_count=91,
        )

    def test_every_concrete_model_field_is_returned(self):
        session = self._session()

        result = self.panel.connection(self.worker(), session_id=session.pk)
        returned = {field["name"]: field for group in result["groups"] for field in group["fields"]}
        model_fields = {field.name for field in SessionRecord._meta.concrete_fields}

        self.assertEqual(set(returned), model_fields)

    def test_sensitive_values_are_exact_in_the_open_dossier(self):
        session = self._session()

        result = self.panel.connection(self.worker(), session_id=session.pk)
        values = {
            field["name"]: field["value"] for group in result["groups"] for field in group["fields"]
        }

        self.assertEqual(values["ip"], "203.0.113.7")
        self.assertEqual(values["device_token"], "device-token")
        self.assertEqual(values["client_fp"], "c" * 64)
        self.assertEqual(values["http_fp"], "h" * 64)
        self.assertEqual(values["http_order_fp"], "o" * 64)
        self.assertEqual(values["tls_sig"], "t" * 64)
        self.assertEqual(values["telnet_sig"], "n" * 64)

    def test_opening_the_dossier_is_audited(self):
        session = self._session()

        self.panel.connection(self.worker(), session_id=session.pk)

        event = ConsoleAuditEvent.objects.get(operation="connection_view")
        self.assertEqual(event.target_ref, f"server.sessionrecord#{session.pk}")
        self.assertEqual(event.actor_id, self.actor.pk)

    def test_an_unknown_connection_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.connection(self.worker(), session_id=999999)


class TestFlagDetail(ModerationTestCase):
    """One flag, with its evidence masked where it names a person's device."""

    def test_returns_the_flag(self):
        flag = self.make_flag()
        result = self.panel.detail(_ctx(), flag.pk)
        self.assertEqual(result["kind"], ModerationFlag.KIND_SHARED_DEVICE)

    def test_address_grade_evidence_is_masked(self):
        flag = self.make_flag()
        evidence = {
            row["key"]: row["value"] for row in self.panel.detail(_ctx(), flag.pk)["evidence"]
        }
        self.assertEqual(evidence["device_token"], "withheld")
        self.assertEqual(evidence["accounts"], "a, b")

    def test_a_missing_flag_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.detail(_ctx(), 999999)


class TestDecisions(ModerationTestCase):
    """Resolving, sanctioning, revoking -- each audited."""

    def test_resolving_a_flag(self):
        flag = self.make_flag()
        result = self.panel.resolve(
            self.io(), flag_id=flag.pk, state="dismissed", note="same household"
        )
        self.assertEqual(result["state"], "dismissed")
        self.assertTrue(
            ConsoleAuditEvent.objects.filter(panel="moderation", operation="flag_resolve").exists()
        )

    def test_resolving_rejects_an_unknown_state(self):
        flag = self.make_flag()
        with self.assertRaises(LookupError):
            self.panel.resolve(self.io(), flag_id=flag.pk, state="banana")

    def test_issuing_a_sanction(self):
        result = self.panel.sanction(
            self.io(),
            subject_type="cidr",
            subject_value="203.0.113.0/24",
            level="ban",
            reason="repeated abuse",
        )
        self.assertEqual(result["level"], "ban")
        self.assertTrue(Sanction.objects.filter(pk=result["id"]).exists())

    def test_a_sanction_from_a_flag_closes_it(self):
        flag = self.make_flag()
        result = self.panel.sanction(
            self.io(),
            subject_type="account",
            subject_value="suspect",
            level="suspend",
            reason="evidence reviewed",
            flag_id=flag.pk,
        )
        self.assertEqual(result["flag_closed"], flag.pk)
        flag.refresh_from_db()
        self.assertEqual(flag.state, ModerationFlag.STATE_ACTIONED)

    def test_a_sanction_rejects_an_unknown_subject_or_level(self):
        with self.assertRaises(LookupError):
            self.panel.sanction(self.io(), subject_type="nonsense", subject_value="x", level="ban")
        with self.assertRaises(LookupError):
            self.panel.sanction(self.io(), subject_type="account", subject_value="x", level="huge")

    def test_a_sanction_needs_a_subject_value(self):
        with self.assertRaises(LookupError):
            self.panel.sanction(self.io(), subject_type="account", subject_value=" ", level="ban")

    def test_revoking_requires_a_reason(self):
        # A lifted sanction without a stated reason is unreviewable later.
        result = self.panel.sanction(
            self.io(), subject_type="account", subject_value="x", level="ban", reason="r"
        )
        with self.assertRaises(LookupError):
            self.panel.revoke(self.io(), sanction_id=result["id"])

    def test_revoking_keeps_the_row(self):
        issued = self.panel.sanction(
            self.io(), subject_type="account", subject_value="x", level="ban", reason="r"
        )
        self.panel.revoke(self.io(), sanction_id=issued["id"], reason="appeal upheld")
        sanction = Sanction.objects.get(pk=issued["id"])
        self.assertIsNotNone(sanction.revoked_at)
        self.assertFalse(sanction.is_active)


class TestReveal(ModerationTestCase):
    """Revealing is permitted and permanently recorded."""

    def setUp(self):
        super().setUp()
        self.session = SessionRecord.objects.create(
            session_uid="uid-reveal", account_name="player", ip="203.0.113.7"
        )

    def test_returns_the_raw_value(self):
        result = self.panel.reveal(
            self.io(), record="session", record_id=self.session.pk, field="ip", reason="appeal"
        )
        self.assertEqual(result["value"], "203.0.113.7")

    def test_the_reveal_is_audited_permanently(self):
        self.panel.reveal(
            self.io(), record="session", record_id=self.session.pk, field="ip", reason="appeal"
        )
        row = ConsoleAuditEvent.objects.get(panel="moderation", operation="reveal")
        self.assertEqual(row.retention, ConsoleAuditEvent.RETENTION_PERMANENT)
        self.assertEqual(row.actor_name, "moderator")
        self.assertEqual(row.message, "appeal")

    def test_only_named_fields_can_be_revealed(self):
        with self.assertRaises(LookupError):
            self.panel.reveal(
                self.io(), record="session", record_id=self.session.pk, field="account_name"
            )

    def test_only_named_records_can_be_revealed(self):
        with self.assertRaises(LookupError):
            self.panel.reveal(self.io(), record="account", record_id=1, field="ip")

    def test_a_missing_record_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.reveal(
                self.io(), record="session", record_id=999999, field="ip", reason="appeal"
            )


class TestAccess(TestCase):
    """The panel is the one thing a moderation-only capability reaches."""

    def test_moderation_only(self):
        panel = ModerationPanel()
        moderator = WorkerContext(actor_id=1, capabilities=frozenset({CONSOLE_MODERATION}))
        self.assertTrue(panel.admits(moderator))

    def test_full_access_also_reaches_it(self):
        panel = ModerationPanel()
        operator = WorkerContext(actor_id=1, capabilities=frozenset({CONSOLE_ACCESS}))
        self.assertTrue(panel.admits(operator))

    def test_no_capability_reaches_nothing(self):
        self.assertFalse(ModerationPanel().admits(WorkerContext(actor_id=1)))

    def test_it_survives_degraded_mode(self):
        # The queue reads plain models, so an outage does not hide the flags.
        self.assertFalse(ModerationPanel.needs_io)

    def test_the_hash_chain_can_be_checked(self):
        # Tamper evidence nobody can check is decoration.
        self.assertTrue(hasattr(ModerationPanel, "verify_chain"))


class TestSignatureDisplay(ModerationTestCase):
    """What software a connection used, and whether it was recorded at all.

    A row that carries no signature and a row whose signature is withheld are
    different facts, and the panel must not let them render the same way. The
    first means the server saw nothing; the second means the server saw
    something and is not showing it.
    """

    def _session(self, **kwargs):
        defaults = {
            "session_uid": f"sig{SessionRecord.objects.count():04d}",
            "account_name": "player",
            "protocol": "websocket",
            "cidr": "203.0.113.0/24",
        }
        defaults.update(kwargs)
        return SessionRecord.objects.create(**defaults)

    def _marks(self, row):
        return {item["field"]: item for item in row["signatures"]}

    def test_every_signature_is_reported_on_every_row(self):
        # Absent ones included. A signal missing from the list and a signal
        # reported absent read differently to whoever is scanning the column.
        self._session()
        marks = self._marks(self.panel.rows(_ctx())["sessions"][0])
        self.assertEqual(
            set(marks),
            {
                "client_fp",
                "telnet_sig",
                "device_token",
                "http_fp",
                "tls_sig",
                "http_order_fp",
                "csessid",
            },
        )

    def test_a_recorded_signature_is_withheld_not_shown(self):
        self._session(tls_sig="a" * 64)
        mark = self._marks(self.panel.rows(_ctx())["sessions"][0])["tls_sig"]
        self.assertTrue(mark["present"])
        self.assertEqual(mark["value"], "withheld")

    def test_an_absent_signature_says_so(self):
        self._session(tls_sig="")
        mark = self._marks(self.panel.rows(_ctx())["sessions"][0])["tls_sig"]
        self.assertFalse(mark["present"])
        self.assertEqual(mark["value"], "")

    def test_the_signatures_can_be_revealed_with_a_reason(self):
        session = self._session(tls_sig="a" * 64)
        shown = self.panel.reveal(
            self.io(),
            record="session",
            record_id=session.pk,
            field="tls_sig",
            reason="checking a ban appeal",
        )
        self.assertEqual(shown["value"], "a" * 64)

    def test_revealing_one_is_recorded_permanently(self):
        session = self._session(http_order_fp="b" * 64)
        self.panel.reveal(
            self.io(),
            record="session",
            record_id=session.pk,
            field="http_order_fp",
            reason="checking a ban appeal",
        )
        event = ConsoleAuditEvent.objects.latest("id")
        self.assertEqual(event.operation, "reveal")
        self.assertEqual(event.retention, "permanent")


class TestEvidenceMasking(ModerationTestCase):
    """Which evidence keys are withheld, and which are not.

    The rule used to be a substring match. ``sig`` is a substring of ``signal``,
    which is a key on every flag and holds nothing but the detector's own name,
    so a careless rule withholds the one field that says what the flag is.
    """

    def _dossier(self, evidence):
        flag = self.make_flag(evidence=evidence)
        return {row["key"]: row["value"] for row in self.panel.detail(_ctx(), flag.pk)["evidence"]}

    def test_a_handshake_signature_is_withheld(self):
        self.assertEqual(self._dossier({"tls_sig": "a" * 64})["tls_sig"], "withheld")

    def test_a_negotiation_signature_is_withheld(self):
        self.assertEqual(self._dossier({"telnet_sig": "a" * 64})["telnet_sig"], "withheld")

    def test_the_detector_name_is_not_withheld(self):
        # The regression this class exists for.
        self.assertEqual(
            self._dossier({"signal": "identity_correlation"})["signal"], "identity_correlation"
        )

    def test_a_network_is_withheld(self):
        self.assertEqual(self._dossier({"cidr": "203.0.113.0/24"})["cidr"], "withheld")

    def test_which_signal_matched_is_not_withheld(self):
        # An operator cannot judge the flag without knowing what it matched on,
        # and a label is not an identifier.
        self.assertEqual(
            self._dossier({"matched_on": "browser handshake"})["matched_on"], "browser handshake"
        )


class TestSignalCoverage(ModerationTestCase):
    """Whether the signals an operator configured are arriving at all.

    This is the only readout that distinguishes "nobody is evading" from "the
    server cannot see them". Both look like an empty queue.
    """

    def _web(self, **kwargs):
        defaults = {
            "session_uid": f"web{SessionRecord.objects.count():04d}",
            "protocol": "websocket",
            "cidr": "203.0.113.0/24",
            "xff_present": True,
            "xff_applied": True,
            "http_order_fp": "b" * 64,
            "csessid": "c" * 32,
        }
        defaults.update(kwargs)
        return SessionRecord.objects.create(**defaults)

    def _report(self, ctx=None):
        return {row["field"]: row for row in self.panel.signals(ctx or _ctx())["rows"]}

    def test_no_data_is_said_plainly(self):
        result = self.panel.signals(_ctx())
        self.assertEqual(result["sample"], 0)
        self.assertIn("Connect once", result["note"])

    def test_an_arriving_signal_reads_as_arriving(self):
        for _ in range(5):
            self._web(tls_sig="a" * 64)
        self.assertEqual(self._report()["tls_sig"]["state"], "ok")

    def test_every_recorded_identity_signal_has_a_coverage_row(self):
        self._web(
            client_fp="c" * 64,
            device_token="d" * 64,
            http_fp="h" * 64,
            tls_sig="t" * 64,
        )

        self.assertEqual(
            set(self._report()),
            {
                "client_fp",
                "device_token",
                "http_fp",
                "telnet_sig",
                "tls_sig",
                "http_order_fp",
                "csessid",
            },
        )

    def test_an_absent_signal_reads_as_absent(self):
        for _ in range(5):
            self._web(tls_sig="")
        self.assertEqual(self._report()["tls_sig"]["state"], "fail")

    def test_an_untrusted_proxy_is_named_as_the_cause(self):
        # The larger of the two faults: the addresses are wrong too.
        for _ in range(5):
            self._web(tls_sig="", xff_present=True, xff_applied=False)
        self.assertIn("UPSTREAM_IPS", self._report()["tls_sig"]["advice"])

    def test_a_trusted_proxy_that_sends_no_headers_is_named_differently(self):
        for _ in range(5):
            self._web(tls_sig="", xff_applied=True)
        advice = self._report()["tls_sig"]["advice"]
        self.assertIn("X-TLS", advice)
        self.assertNotIn("UPSTREAM_IPS", advice)

    def test_a_bot_link_counts_against_neither_population(self):
        # A Discord relay negotiates no telnet option and terminates no TLS.
        # Counting it as a raw socket is what made a game whose players all use
        # the web client report its client negotiation as absent: the
        # denominator was its bot links, which can never negotiate anything.
        for _ in range(3):
            SessionRecord.objects.create(
                session_uid=f"bot{SessionRecord.objects.count():04d}",
                protocol="discord",
                cidr="",
            )
        result = self.panel.signals(_ctx())
        report = {row["field"]: row for row in result["rows"]}
        self.assertEqual(report["telnet_sig"]["of"], 0)
        self.assertEqual(report["telnet_sig"]["state"], "off")
        self.assertEqual(result["other_sessions"], 3)

    def test_the_stock_web_client_protocol_name_counts_as_web(self):
        # The shipped web client records "webclient/websocket"; a game that
        # subclasses it may record "websocket" or "webclient_ajax". An exact
        # list of names gets one of them wrong, and getting it wrong reports a
        # working proxy as a broken one.
        for name in ("webclient/websocket", "webclient_ajax", "websocket"):
            self._web(protocol=name, tls_sig="a" * 64)
        report = self._report()
        self.assertEqual(report["tls_sig"]["of"], 3)
        self.assertEqual(report["tls_sig"]["state"], "ok")
        self.assertEqual(report["telnet_sig"]["of"], 0)

    def test_telnet_over_ssl_is_still_telnet(self):
        SessionRecord.objects.create(
            session_uid="tls1", protocol="telnet/ssl", telnet_sig="d" * 64, cidr=""
        )
        self.assertEqual(self._report()["telnet_sig"]["of"], 1)

    def test_telnet_sessions_are_not_counted_against_the_handshake(self):
        # A raw socket has no TLS handshake. Counting it as missing coverage
        # reports a fault that is not one.
        SessionRecord.objects.create(
            session_uid="t1", protocol="telnet", telnet_sig="d" * 64, cidr="203.0.113.0/24"
        )
        report = self._report()
        self.assertEqual(report["tls_sig"]["of"], 0)
        self.assertEqual(report["telnet_sig"]["seen"], 1)

    def test_a_partial_signal_is_not_reported_as_a_fault(self):
        # Players use different software. Some coverage is the normal state.
        for _ in range(8):
            self._web(tls_sig="a" * 64)
        for _ in range(4):
            self._web(tls_sig="")
        self.assertEqual(self._report()["tls_sig"]["state"], "attn")

    def test_no_signature_value_is_read_out_of_the_database(self):
        # The coverage report must not become a way to page through everybody's
        # fingerprints. It reports counts; the values stay in the column.
        self._web(tls_sig="a" * 64)
        rendered = repr(self.panel.signals(_ctx()))
        self.assertNotIn("a" * 64, rendered)


class TestAddressRetentionState(ModerationTestCase):
    """Why a row has no address, said out loud.

    Three different facts reach the panel as the same empty string: the address
    is withheld, the address was deleted by the retention sweep, or no address
    was ever recorded. An operator reading a blank cell cannot tell them apart,
    and the third one means every address signal on the row is missing rather
    than hidden.
    """

    def _session(self, **kwargs):
        defaults = {
            "session_uid": f"ret{SessionRecord.objects.count():04d}",
            "protocol": "telnet",
            "cidr": "203.0.113.0/24",
        }
        defaults.update(kwargs)
        return SessionRecord.objects.create(**defaults)

    def _row(self):
        return self.panel.rows(_ctx())["sessions"][0]

    def test_a_recorded_address_reads_as_held(self):
        self._session(ip="203.0.113.7", ip_hash="h" * 64)
        row = self._row()
        self.assertEqual(row["address_state"], "held")
        self.assertEqual(row["ip"], "withheld")
        self.assertEqual(row["address_state_note"], "")

    def test_a_purged_address_says_the_server_deleted_it(self):
        # The hash outlives the address on purpose, so the row still matches.
        self._session(ip=None, ip_hash="h" * 64)
        row = self._row()
        self.assertEqual(row["address_state"], "purged")
        self.assertIn("deleted", row["address_state_note"])

    def test_an_address_that_was_never_recorded_says_that_instead(self):
        self._session(ip=None, ip_hash="")
        row = self._row()
        self.assertEqual(row["address_state"], "absent")
        self.assertIn("no address", row["address_state_note"])

    def test_purged_and_absent_do_not_read_the_same(self):
        # The regression this class exists for.
        self._session(ip=None, ip_hash="h" * 64)
        purged = self._row()
        SessionRecord.objects.all().delete()
        self._session(ip=None, ip_hash="")
        absent = self._row()
        self.assertNotEqual(purged["address_state"], absent["address_state"])
        self.assertNotEqual(purged["address_state_note"], absent["address_state_note"])


class TestAccountDossier(ModerationTestCase):
    """Which accounts share an identity key with this one.

    Exact matches on indexed columns and nothing else. The panel must never
    report a likelihood, because the rule the package is built on is that a
    conclusion has to be showable to the player it is used against.
    """

    def _session(self, account_name, **kwargs):
        defaults = {
            "session_uid": f"dos{SessionRecord.objects.count():04d}",
            "account_name": account_name,
            "protocol": "telnet",
            "cidr": "203.0.113.0/24",
        }
        defaults.update(kwargs)
        return SessionRecord.objects.create(**defaults)

    def _keys(self, name="suspect"):
        return {row["kind"]: row for row in self.panel.account(_ctx(), name=name)["keys"]}

    def test_a_missing_name_is_refused(self):
        with self.assertRaises(LookupError):
            self.panel.account(_ctx(), name="  ")

    def test_an_account_with_no_history_reports_no_keys(self):
        result = self.panel.account(_ctx(), name="stranger")
        self.assertEqual(result["keys"], [])
        self.assertEqual(result["first_seen"], "")

    def test_a_shared_device_token_names_the_other_account(self):
        self._session("suspect", device_token="t" * 32)
        self._session("evader", device_token="t" * 32)
        self.assertEqual(self._keys()["device_token"]["shared_with"], ["evader"])

    def test_an_unshared_key_names_nobody(self):
        self._session("suspect", device_token="t" * 32)
        self.assertEqual(self._keys()["device_token"]["shared_with"], [])

    def test_the_account_itself_is_never_listed_as_a_match(self):
        self._session("suspect", device_token="t" * 32)
        self._session("Suspect", device_token="t" * 32)
        self.assertEqual(self._keys()["device_token"]["shared_with"], [])

    def test_the_new_signatures_are_correlated_too(self):
        self._session("suspect", tls_sig="a" * 64)
        self._session("evader", tls_sig="a" * 64)
        self.assertEqual(self._keys()["tls_sig"]["shared_with"], ["evader"])

    def test_a_handshake_signature_never_reaches_the_browser(self):
        # It cannot be banned, which is not a reason to show it. Anything
        # opaque that reaches the page is recoverable from devtools, and an
        # audit trail that records a reveal nobody had to perform is a lie.
        self._session("suspect", tls_sig="a" * 64, telnet_sig="b" * 64)
        keys = self._keys()
        self.assertEqual(keys["tls_sig"]["value"], "withheld")
        self.assertEqual(keys["telnet_sig"]["value"], "withheld")

    def test_no_signature_value_appears_anywhere_in_the_payload(self):
        self._session("suspect", tls_sig="a" * 64, device_token="t" * 32)
        rendered = repr(self.panel.account(_ctx(), name="suspect"))
        self.assertNotIn("a" * 64, rendered)
        self.assertNotIn("t" * 32, rendered)

    def test_a_handshake_is_marked_as_something_you_cannot_ban(self):
        # It identifies a browser build. Banning one bans everybody who uses
        # that browser, which is why it has no sanction subject at all.
        self._session("suspect", tls_sig="a" * 64)
        self.assertFalse(self._keys()["tls_sig"]["bannable"])
        self.assertEqual(self._keys()["tls_sig"]["subject_type"], "")

    def test_a_device_token_is_marked_as_something_you_can_ban(self):
        self._session("suspect", device_token="t" * 32)
        self.assertTrue(self._keys()["device_token"]["bannable"])
        self.assertEqual(self._keys()["device_token"]["subject_type"], "device_token")

    def test_an_opaque_key_is_masked(self):
        self._session("suspect", device_token="t" * 32)
        self.assertEqual(self._keys()["device_token"]["value"], "withheld")

    def test_a_network_is_readable_because_it_names_nobody(self):
        self._session("suspect")
        self.assertEqual(self._keys()["cidr"]["value"], "203.0.113.0/24")

    def test_an_active_ban_on_a_key_is_reported(self):
        self._session("suspect", device_token="t" * 32)
        Sanction.objects.create(
            subject_type=Sanction.SUBJECT_DEVICE,
            subject_value="t" * 32,
            level=Sanction.LEVEL_BAN,
            reason="test",
        )
        self.assertTrue(self._keys()["device_token"]["sanctioned"])

    def test_a_lifted_ban_is_not_reported_as_active(self):
        self._session("suspect", device_token="t" * 32)
        sanction = Sanction.objects.create(
            subject_type=Sanction.SUBJECT_DEVICE,
            subject_value="t" * 32,
            level=Sanction.LEVEL_BAN,
            reason="test",
        )
        sanction.revoked_at = timezone.now()
        sanction.save(update_fields=["revoked_at"])
        self.assertFalse(self._keys()["device_token"]["sanctioned"])

    def test_the_flags_naming_this_account_come_back_with_it(self):
        self._session("suspect")
        self.make_flag(account_name="suspect")
        self.assertEqual(len(self.panel.account(_ctx(), name="suspect")["flags"]), 1)

    def test_the_panel_states_that_it_guesses_nothing(self):
        self.assertIn("does not guess", self.panel.account(_ctx(), name="x")["note"])

    def _history(self, count, start=0):
        for index in range(start, start + count):
            self._session(
                "suspect",
                cidr=f"203.0.113.{index}/24",
                device_token=f"token{index:027d}",
                client_fp=f"fp{index:030d}",
            )

    def test_the_query_count_does_not_grow_with_the_history(self):
        # The surface this replaces ran one shared-with query per value, so an
        # account with a long history cost eighty round trips to render one
        # page. The cost here is per column, and the column list is a constant.
        #
        # Asserted as "the same for a long history as for a short one" rather
        # than as a number, because a number here would just restate the length
        # of the column list and would have to be edited every time it changes.
        self._history(2)
        short = self._measure()
        SessionRecord.objects.all().delete()
        self._history(60)
        self.assertEqual(self._measure(), short)

    def _measure(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            self.panel.account(_ctx(), name="suspect")
        return len(captured)

    def test_one_busy_account_does_not_hide_the_others_on_a_key(self):
        # The model orders by connected_at, and an ordering column joins the
        # DISTINCT. Without clearing it, thirty sessions from one account fill
        # the limit and the account that matters never appears.
        for _ in range(30):
            self._session("noisy", device_token="t" * 32)
        self._session("suspect", device_token="t" * 32)
        self._session("evader", device_token="t" * 32)
        self.assertEqual(self._keys()["device_token"]["shared_with"], ["evader", "noisy"])
