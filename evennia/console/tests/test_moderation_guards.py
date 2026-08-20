"""Tests for the sanction guards, the collateral count, and proposals.

The console shipped able to issue a permanent ban on a whole network, with an
empty reason, from any moderation capability. The game-side form it replaced
required an address capability, a duration, and the subject typed back to
confirm an indefinite ban. Every guard below is one of those restored, or the
replacement for one.

The rule the proposal path exists to hold: **the person who investigates a case
and the person who decides it never ends do not have to be the same person.**
Nobody is refused the work. The authority to make it permanent is separate.

"""

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone

from evennia.console.models import ConsoleAuditEvent
from evennia.console.panels.moderation import ModerationPanel
from evennia.console.registry import (
    CONSOLE_MODERATION_ADDRESS,
    CONSOLE_MODERATION_PERMANENT,
    IOContext,
    WorkerContext,
)
from evennia.server.models import Sanction, SanctionProposal, SessionRecord

SENIOR = frozenset({CONSOLE_MODERATION_ADDRESS, CONSOLE_MODERATION_PERMANENT})
ADDRESS_ONLY = frozenset({CONSOLE_MODERATION_ADDRESS})


class GuardTestCase(TestCase):
    """Two staff accounts: one senior, one not."""

    def setUp(self):
        self.panel = ModerationPanel()
        model = get_user_model()
        self.senior = model.objects.create(
            username="senior", is_active=True, is_staff=True, is_superuser=True
        )
        self.junior = model.objects.create(
            username="junior", is_active=True, is_staff=True, is_superuser=True
        )

    def io(self, who=None, capabilities=SENIOR):
        """Return an IO context for one of the two accounts."""
        account = who or self.senior
        return IOContext(
            actor_id=account.pk, actor_name=account.username, capabilities=frozenset(capabilities)
        )

    def worker(self, who=None, capabilities=SENIOR, **params):
        """Return a worker context for one of the two accounts."""
        account = who or self.senior
        return WorkerContext(
            actor_id=account.pk,
            actor_name=account.username,
            capabilities=frozenset(capabilities),
            params=params,
        )

    def later(self):
        """Return a timestamp an hour from now."""
        return (timezone.now() + timezone.timedelta(hours=1)).isoformat()


class TestReasonRequired(GuardTestCase):
    """The player reads the reason. An empty one explains nothing."""

    def test_a_sanction_without_a_reason_is_refused(self):
        with self.assertRaises(PermissionDenied):
            self.panel.sanction(
                self.io(),
                subject_type="account",
                subject_value="suspect",
                level="ban",
                reason="  ",
                expires_at=self.later(),
            )

    def test_the_refusal_does_not_tell_staff_to_name_the_signal(self):
        with self.assertRaises(PermissionDenied) as caught:
            self.panel.sanction(
                self.io(), subject_type="account", subject_value="suspect", level="ban"
            )
        self.assertIn("Do not write how you found them", str(caught.exception))

    def test_a_reveal_without_a_reason_is_refused(self):
        session = SessionRecord.objects.create(session_uid="u1", ip="203.0.113.7")
        with self.assertRaises(PermissionDenied):
            self.panel.reveal(
                self.io(), record="session", record_id=session.pk, field="ip", reason=""
            )


class TestAddressAuthority(GuardTestCase):
    """A network ban reaches everybody who shares it."""

    def test_an_account_sanction_needs_no_address_capability(self):
        result = self.panel.sanction(
            self.io(capabilities=frozenset()),
            subject_type="account",
            subject_value="suspect",
            level="suspend",
            reason="abuse",
            expires_at=self.later(),
        )
        self.assertTrue(Sanction.objects.filter(pk=result["id"]).exists())

    def test_a_network_sanction_is_ordinary_staff_work(self):
        # The line is where the game drew it. A residential address changes and
        # a /24 does not, so the network is the ban unit and staff without the
        # capability still reach it.
        result = self.panel.sanction(
            self.io(capabilities=frozenset()),
            subject_type="cidr",
            subject_value="203.0.113.0/24",
            level="ban",
            reason="abuse",
            expires_at=self.later(),
        )
        self.assertTrue(Sanction.objects.filter(pk=result["id"]).exists())

    def test_a_single_address_without_it_is_refused(self):
        with self.assertRaises(PermissionDenied) as caught:
            self.panel.sanction(
                self.io(capabilities=frozenset()),
                subject_type="ip",
                subject_value="203.0.113.7",
                level="ban",
                reason="abuse",
                expires_at=self.later(),
            )
        self.assertIn("address permission", str(caught.exception))

    def test_every_masked_subject_is_gated(self):
        for subject in ("ip", "device_token"):
            with self.assertRaises(PermissionDenied, msg=f"{subject} was not gated"):
                self.panel.sanction(
                    self.io(capabilities=frozenset()),
                    subject_type=subject,
                    subject_value="value",
                    level="ban",
                    reason="abuse",
                    expires_at=self.later(),
                )

    def test_with_the_capability_it_is_allowed(self):
        result = self.panel.sanction(
            self.io(capabilities=SENIOR),
            subject_type="cidr",
            subject_value="203.0.113.0/24",
            level="ban",
            reason="abuse",
            expires_at=self.later(),
        )
        self.assertTrue(Sanction.objects.filter(pk=result["id"]).exists())


class TestCollateral(GuardTestCase):
    """What a ban on this subject would reach, before it is issued."""

    def setUp(self):
        super().setUp()
        for index in range(3):
            SessionRecord.objects.create(
                session_uid=f"u{index}",
                cidr="203.0.113.0/24",
                account_name=f"player{index}",
                account_id=index + 10,
            )
        SessionRecord.objects.create(
            session_uid="other", cidr="198.51.100.0/24", account_name="elsewhere"
        )

    def test_it_counts_the_accounts_on_a_network(self):
        result = self.panel.collateral(
            self.worker(), subject_type="cidr", subject_value="203.0.113.0/24"
        )
        self.assertEqual(result["account_count"], 3)
        self.assertEqual(result["sessions"], 3)

    def test_it_names_them(self):
        result = self.panel.collateral(
            self.worker(), subject_type="cidr", subject_value="203.0.113.0/24"
        )
        self.assertEqual(result["accounts"], ["player0", "player1", "player2"])

    def test_another_network_is_not_counted(self):
        result = self.panel.collateral(
            self.worker(), subject_type="cidr", subject_value="198.51.100.0/24"
        )
        self.assertEqual(result["account_count"], 1)

    def test_an_address_is_matched_on_its_hash(self):
        # The raw column is cleared by the retention sweep; the hash is not, so
        # the count still works after ninety days.
        from evennia.moderation.capture import hash_value

        SessionRecord.objects.create(
            session_uid="hashed", ip_hash=hash_value("203.0.113.7"), account_name="hashed-player"
        )
        result = self.panel.collateral(
            self.worker(), subject_type="ip", subject_value="203.0.113.7"
        )
        self.assertEqual(result["accounts"], ["hashed-player"])

    def test_a_subject_with_no_history_is_refused(self):
        with self.assertRaises(LookupError):
            self.panel.collateral(
                self.worker(), subject_type="email_domain", subject_value="example.com"
            )

    def test_an_empty_value_is_refused(self):
        with self.assertRaises(LookupError):
            self.panel.collateral(self.worker(), subject_type="cidr", subject_value=" ")

    def test_it_counts_nothing_and_says_so(self):
        result = self.panel.collateral(
            self.worker(), subject_type="cidr", subject_value="192.0.2.0/24"
        )
        self.assertEqual(result["account_count"], 0)
        self.assertTrue(result["note"])

    def test_it_does_not_need_the_io_owner(self):
        from evennia.console.registry import is_io_action

        self.assertFalse(is_io_action(ModerationPanel.collateral))


class TestProposals(GuardTestCase):
    """Permanent bans need senior staff; everybody else may ask."""

    def test_a_permanent_request_without_the_capability_becomes_a_proposal(self):
        result = self.panel.sanction(
            self.io(self.junior, capabilities=frozenset()),
            subject_type="account",
            subject_value="suspect",
            level="ban",
            reason="repeated abuse",
        )
        self.assertTrue(result["proposed"])
        self.assertEqual(SanctionProposal.objects.count(), 1)

    def test_the_proposal_sanctions_nobody(self):
        self.panel.sanction(
            self.io(self.junior, capabilities=frozenset()),
            subject_type="account",
            subject_value="suspect",
            level="ban",
            reason="repeated abuse",
        )
        self.assertFalse(Sanction.objects.exists())

    def test_a_timed_sanction_needs_no_proposal(self):
        result = self.panel.sanction(
            self.io(self.junior, capabilities=frozenset()),
            subject_type="account",
            subject_value="suspect",
            level="suspend",
            reason="abuse",
            expires_at=self.later(),
        )
        self.assertNotIn("proposed", result)
        self.assertTrue(Sanction.objects.filter(pk=result["id"]).exists())

    def test_senior_staff_issue_a_permanent_ban_directly(self):
        result = self.panel.sanction(
            self.io(capabilities=SENIOR),
            subject_type="account",
            subject_value="suspect",
            level="ban",
            reason="repeated abuse",
        )
        self.assertNotIn("proposed", result)
        self.assertIsNone(Sanction.objects.get(pk=result["id"]).expires_at)

    def test_the_collateral_is_stored_with_the_proposal(self):
        # The reviewer should see the number the requester saw. A network's
        # population changes between the two.
        SessionRecord.objects.create(
            session_uid="a", cidr="203.0.113.0/24", account_name="bystander"
        )
        self.panel.sanction(
            self.io(self.junior, capabilities=ADDRESS_ONLY),
            subject_type="cidr",
            subject_value="203.0.113.0/24",
            level="ban",
            reason="abuse",
        )
        proposal = SanctionProposal.objects.get()
        self.assertEqual(proposal.collateral["account_count"], 1)

    def test_proposals_are_listed_for_a_reviewer(self):
        self.panel.sanction(
            self.io(self.junior, capabilities=frozenset()),
            subject_type="account",
            subject_value="suspect",
            level="ban",
            reason="abuse",
        )
        result = self.panel.proposals(self.worker())
        self.assertEqual(len(result["rows"]), 1)
        self.assertTrue(result["may_decide"])

    def test_a_reviewer_without_the_capability_is_told_so(self):
        self.assertFalse(self.panel.proposals(self.worker(capabilities=frozenset()))["may_decide"])

    def test_a_proposal_marks_whether_it_is_yours(self):
        self.panel.sanction(
            self.io(self.junior, capabilities=frozenset()),
            subject_type="account",
            subject_value="suspect",
            level="ban",
            reason="abuse",
        )
        mine = self.panel.proposals(self.worker(self.junior))["rows"][0]
        theirs = self.panel.proposals(self.worker(self.senior))["rows"][0]
        self.assertTrue(mine["yours"])
        self.assertFalse(theirs["yours"])

    def _pending(self):
        """Make one pending proposal from the junior account."""
        self.panel.sanction(
            self.io(self.junior, capabilities=frozenset()),
            subject_type="account",
            subject_value="suspect",
            level="ban",
            reason="repeated abuse",
        )
        return SanctionProposal.objects.get()

    def test_approving_issues_the_sanction(self):
        proposal = self._pending()
        result = self.panel.approve(self.io(), proposal_id=proposal.pk, note="agreed")
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, SanctionProposal.STATE_APPROVED)
        self.assertTrue(Sanction.objects.filter(pk=result["sanction"]["id"]).exists())

    def test_the_issued_sanction_never_expires(self):
        proposal = self._pending()
        result = self.panel.approve(self.io(), proposal_id=proposal.pk, note="agreed")
        self.assertIsNone(Sanction.objects.get(pk=result["sanction"]["id"]).expires_at)

    def test_the_proposal_keeps_a_link_to_the_sanction(self):
        proposal = self._pending()
        self.panel.approve(self.io(), proposal_id=proposal.pk, note="agreed")
        proposal.refresh_from_db()
        self.assertIsNotNone(proposal.sanction_id)

    def test_declining_leaves_the_record(self):
        # "We considered this and said no" is what stops the same case being
        # re-argued from scratch, and it is what an appeal reads.
        proposal = self._pending()
        self.panel.decline(self.io(), proposal_id=proposal.pk, note="not enough evidence")
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, SanctionProposal.STATE_DECLINED)
        self.assertEqual(proposal.decision_note, "not enough evidence")
        self.assertFalse(Sanction.objects.exists())

    def test_a_decision_needs_a_note(self):
        proposal = self._pending()
        with self.assertRaises(ValueError):
            self.panel.approve(self.io(), proposal_id=proposal.pk, note="  ")

    def test_deciding_without_the_capability_is_refused(self):
        proposal = self._pending()
        with self.assertRaises(PermissionDenied):
            self.panel.approve(
                self.io(capabilities=ADDRESS_ONLY), proposal_id=proposal.pk, note="agreed"
            )

    def test_you_cannot_decide_your_own_proposal(self):
        # The whole point of the split. Otherwise a junior grants themselves
        # the authority by holding both capabilities for one moment.
        proposal = self._pending()
        with self.assertRaises(PermissionDenied) as caught:
            self.panel.approve(
                self.io(self.junior, capabilities=SENIOR), proposal_id=proposal.pk, note="agreed"
            )
        self.assertIn("your own proposal", str(caught.exception))

    def test_a_decided_proposal_cannot_be_decided_again(self):
        proposal = self._pending()
        self.panel.decline(self.io(), proposal_id=proposal.pk, note="no")
        with self.assertRaises(ValueError):
            self.panel.approve(self.io(), proposal_id=proposal.pk, note="changed my mind")

    def test_the_requester_may_withdraw(self):
        proposal = self._pending()
        self.panel.withdraw(self.io(self.junior), proposal_id=proposal.pk, note="mistake")
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, SanctionProposal.STATE_WITHDRAWN)

    def test_somebody_else_may_not_withdraw(self):
        proposal = self._pending()
        with self.assertRaises(PermissionDenied):
            self.panel.withdraw(self.io(), proposal_id=proposal.pk, note="no")

    def test_an_unknown_proposal_is_a_lookup_error(self):
        with self.assertRaises(LookupError):
            self.panel.approve(self.io(), proposal_id=999999, note="agreed")

    def test_every_step_is_recorded_permanently(self):
        proposal = self._pending()
        self.panel.decline(self.io(), proposal_id=proposal.pk, note="no")
        rows = ConsoleAuditEvent.objects.filter(panel="moderation")
        operations = set(rows.values_list("operation", flat=True))
        self.assertEqual(operations, {"propose", "decline"})
        for row in rows:
            self.assertEqual(row.retention, ConsoleAuditEvent.RETENTION_PERMANENT)

    def test_a_proposal_masks_an_address_from_a_reader_without_the_capability(self):
        self.panel.sanction(
            self.io(self.junior, capabilities=ADDRESS_ONLY),
            subject_type="ip",
            subject_value="203.0.113.7",
            level="ban",
            reason="abuse",
        )
        masked = self.panel.proposals(self.worker(capabilities=frozenset()))["rows"][0]
        shown = self.panel.proposals(self.worker(capabilities=SENIOR))["rows"][0]
        self.assertEqual(shown["subject_value"], "203.0.113.7")
        self.assertNotEqual(masked["subject_value"], "203.0.113.7")

    def test_a_network_is_never_masked(self):
        # Staff without the capability still see which network a session came
        # from. That is stated in the moderation document and relied on.
        self.panel.sanction(
            self.io(self.junior, capabilities=frozenset()),
            subject_type="cidr",
            subject_value="203.0.113.0/24",
            level="ban",
            reason="abuse",
        )
        row = self.panel.proposals(self.worker(capabilities=frozenset()))["rows"][0]
        self.assertEqual(row["subject_value"], "203.0.113.0/24")
