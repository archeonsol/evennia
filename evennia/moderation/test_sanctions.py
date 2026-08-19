"""Sanction issue, revoke, match, and the tamper-evident chain."""

from datetime import timedelta
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone

from evennia.moderation import sanctions
from evennia.server.models import Sanction, SanctionHit

STAFF = SimpleNamespace(id=7, username="Warden")


class NormalizeTest(TestCase):
    def test_account_names_lowercase(self):
        self.assertEqual(sanctions.normalize_subject(Sanction.SUBJECT_ACCOUNT, "  BoB "), "bob")

    def test_bare_address_widens_to_its_network(self):
        # A staff member typing an address under /cidr means the network it is in.
        self.assertEqual(
            sanctions.normalize_subject(Sanction.SUBJECT_CIDR, "203.0.113.7"),
            "203.0.113.0/24",
        )

    def test_network_is_canonicalized(self):
        self.assertEqual(
            sanctions.normalize_subject(Sanction.SUBJECT_CIDR, "203.0.113.7/24"),
            "203.0.113.0/24",
        )

    def test_asn_accepts_as_prefix(self):
        self.assertEqual(sanctions.normalize_subject(Sanction.SUBJECT_ASN, "AS64512"), "64512")

    def test_bad_address_is_rejected(self):
        with self.assertRaises(sanctions.SanctionError):
            sanctions.normalize_subject(Sanction.SUBJECT_IP, "not-an-address")

    def test_empty_subject_is_rejected(self):
        with self.assertRaises(sanctions.SanctionError):
            sanctions.normalize_subject(Sanction.SUBJECT_ACCOUNT, "   ")


class IssueTest(TestCase):
    def test_issue_records_actor_and_normalizes(self):
        sanction = sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT,
            subject_value="Bob",
            actor=STAFF,
            reason="repeated harassment",
        )
        self.assertEqual(sanction.subject_value, "bob")
        self.assertEqual(sanction.actor_id, 7)
        self.assertEqual(sanction.actor_name, "Warden")
        self.assertTrue(sanction.is_active)
        self.assertTrue(sanction.blocks_connection)

    def test_unknown_level_is_rejected(self):
        with self.assertRaises(sanctions.SanctionError):
            sanctions.issue_sanction(
                subject_type=Sanction.SUBJECT_ACCOUNT,
                subject_value="bob",
                level="obliterate",
                actor=STAFF,
            )

    def test_watch_level_does_not_block(self):
        sanction = sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT,
            subject_value="bob",
            level=Sanction.LEVEL_WATCH,
            actor=STAFF,
        )
        self.assertFalse(sanction.blocks_connection)

    def test_expired_sanction_is_not_active(self):
        sanction = sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT,
            subject_value="bob",
            actor=STAFF,
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        self.assertFalse(sanction.is_active)
        self.assertEqual(Sanction.objects.active().count(), 0)


class RevokeTest(TestCase):
    def test_revoke_keeps_the_row(self):
        sanction = sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT, subject_value="bob", actor=STAFF
        )
        sanctions.revoke_sanction(sanction, actor=STAFF, reason="appeal upheld")
        sanction.refresh_from_db()
        self.assertIsNotNone(sanction.revoked_at)
        self.assertEqual(sanction.revoked_by_name, "Warden")
        self.assertFalse(sanction.is_active)
        self.assertEqual(Sanction.objects.count(), 1)

    def test_revoke_is_idempotent(self):
        sanction = sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT, subject_value="bob", actor=STAFF
        )
        sanctions.revoke_sanction(sanction, actor=STAFF)
        first = sanction.revoked_at
        sanctions.revoke_sanction(sanction, actor=STAFF)
        self.assertEqual(sanction.revoked_at, first)


class MatchTest(TestCase):
    def setUp(self):
        self.cidr = sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_CIDR,
            subject_value="203.0.113.0/24",
            level=Sanction.LEVEL_SUSPEND,
            actor=STAFF,
            reason="shared network",
        )
        self.account = sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT,
            subject_value="bob",
            level=Sanction.LEVEL_BAN,
            actor=STAFF,
            reason="told to stop",
        )

    def test_most_severe_wins(self):
        decision = sanctions.evaluate(
            {Sanction.SUBJECT_ACCOUNT: "bob", Sanction.SUBJECT_CIDR: "203.0.113.0/24"}
        )
        self.assertEqual(decision.level, Sanction.LEVEL_BAN)
        self.assertTrue(decision.blocks)
        self.assertEqual(decision.reason, "told to stop")
        self.assertEqual(len(decision.sanctions), 2)

    def test_no_keys_no_decision(self):
        self.assertFalse(sanctions.evaluate({}).blocks)

    def test_empty_value_never_matches(self):
        # An unpopulated column must not collide with a sanction on "".
        keys = sanctions.keys_from_snapshot({"account_name": "", "cidr": ""})
        self.assertEqual(keys, {})
        self.assertFalse(sanctions.evaluate(keys).blocks)

    def test_revoked_sanction_stops_matching(self):
        sanctions.revoke_sanction(self.account, actor=STAFF)
        decision = sanctions.evaluate({Sanction.SUBJECT_ACCOUNT: "bob"})
        self.assertFalse(decision.blocks)

    def test_keys_from_snapshot_reads_capture_columns(self):
        keys = sanctions.keys_from_snapshot(
            {
                "account_name": "Bob",
                "ip": "203.0.113.7",
                "cidr": "203.0.113.0/24",
                "device_token": "abc",
                "client_fp": "def",
                "csessid": "ghi",
                "asn": 64512,
            }
        )
        self.assertEqual(keys[Sanction.SUBJECT_ACCOUNT], "bob")
        self.assertEqual(keys[Sanction.SUBJECT_ASN], "64512")
        self.assertEqual(len(keys), 7)

    def test_hits_are_recorded(self):
        decision = sanctions.evaluate({Sanction.SUBJECT_ACCOUNT: "bob"})
        sanctions.record_hits(
            decision,
            {"session_uid": "u1", "account_name": "bob", "ip": "203.0.113.7"},
            action=SanctionHit.ACTION_BLOCKED,
        )
        hit = SanctionHit.objects.get()
        self.assertEqual(hit.sanction_id, self.account.id)
        self.assertEqual(hit.matched_on, Sanction.SUBJECT_ACCOUNT)


class ChainTest(TestCase):
    def test_chain_verifies_over_many_rows(self):
        for index in range(5):
            sanctions.issue_sanction(
                subject_type=Sanction.SUBJECT_ACCOUNT,
                subject_value=f"player{index}",
                actor=STAFF,
            )
        result = sanctions.verify_chain()
        self.assertTrue(result["ok"])
        self.assertEqual(result["checked"], 5)

    def test_edited_row_breaks_the_chain(self):
        for index in range(3):
            sanctions.issue_sanction(
                subject_type=Sanction.SUBJECT_ACCOUNT,
                subject_value=f"player{index}",
                actor=STAFF,
            )
        # An edit made outside issue_sanction, which is the thing being detected.
        target = Sanction.objects.order_by("id")[1]
        Sanction.objects.filter(id=target.id).update(level=Sanction.LEVEL_WATCH)

        result = sanctions.verify_chain()
        self.assertFalse(result["ok"])
        self.assertEqual(result["broken_at"], target.id)

    def test_revoking_does_not_break_the_chain(self):
        # Revocation is an expected mutation and is outside the hashed fields.
        sanction = sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT, subject_value="bob", actor=STAFF
        )
        sanctions.revoke_sanction(sanction, actor=STAFF, reason="appeal")
        self.assertTrue(sanctions.verify_chain()["ok"])
