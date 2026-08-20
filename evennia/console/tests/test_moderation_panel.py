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
