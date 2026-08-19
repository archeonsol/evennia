"""Portal blocklist snapshot and the connection guard."""

from datetime import timedelta
from types import SimpleNamespace

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from evennia.moderation import blocklist, portal_guard, sanctions
from evennia.server.models import Sanction

STAFF = SimpleNamespace(id=7, username="Warden")


class BlocklistTest(TestCase):
    def setUp(self):
        cache.delete(blocklist.CACHE_KEY)
        blocklist.reset_local_cache()

    def tearDown(self):
        cache.delete(blocklist.CACHE_KEY)
        blocklist.reset_local_cache()

    def test_issuing_publishes_the_address(self):
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_IP, subject_value="203.0.113.7", actor=STAFF
        )
        self.assertTrue(blocklist.is_blocked_address("203.0.113.7"))

    def test_network_sanction_covers_its_members(self):
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_CIDR,
            subject_value="203.0.113.0/24",
            actor=STAFF,
        )
        self.assertTrue(blocklist.is_blocked_address("203.0.113.200"))
        self.assertFalse(blocklist.is_blocked_address("198.51.100.1"))

    def test_account_sanctions_are_not_published(self):
        # The Portal does not know who is connecting, only from where.
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT, subject_value="bob", actor=STAFF
        )
        snapshot = blocklist.build_snapshot()
        self.assertEqual(snapshot["ips"], {})
        self.assertEqual(snapshot["cidrs"], {})

    def test_non_blocking_levels_are_not_published(self):
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_IP,
            subject_value="203.0.113.7",
            level=Sanction.LEVEL_WATCH,
            actor=STAFF,
        )
        self.assertFalse(blocklist.is_blocked_address("203.0.113.7"))

    def test_revoking_republishes(self):
        sanction = sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_IP, subject_value="203.0.113.7", actor=STAFF
        )
        sanctions.revoke_sanction(sanction, actor=STAFF)
        self.assertFalse(blocklist.is_blocked_address("203.0.113.7"))

    def test_expiry_is_honoured_without_a_refresh(self):
        # A published entry carries its own expiry, so it stops enforcing on time
        # rather than whenever the Portal next re-reads the cache.
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_IP,
            subject_value="203.0.113.7",
            actor=STAFF,
            expires_at=timezone.now() + timedelta(seconds=1),
        )
        snapshot = blocklist.get_snapshot()
        self.assertIn("203.0.113.7", snapshot["ips"])

        past = timezone.now() - timedelta(hours=1)
        snapshot["ips"]["203.0.113.7"] = past.timestamp()
        self.assertFalse(blocklist.is_blocked_address("203.0.113.7"))

    def test_indefinite_sanction_wins_over_an_expiring_one(self):
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_IP,
            subject_value="203.0.113.7",
            actor=STAFF,
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_IP, subject_value="203.0.113.7", actor=STAFF
        )
        self.assertEqual(blocklist.build_snapshot()["ips"]["203.0.113.7"], 0.0)

    def test_missing_cache_fails_open(self):
        cache.delete(blocklist.CACHE_KEY)
        blocklist.reset_local_cache()
        self.assertFalse(blocklist.is_blocked_address("203.0.113.7"))

    def test_junk_address_is_not_blocked(self):
        self.assertFalse(blocklist.is_blocked_address("underspire.net"))
        self.assertFalse(blocklist.is_blocked_address(None))


class PortalGuardTest(TestCase):
    def setUp(self):
        cache.delete(blocklist.CACHE_KEY)
        blocklist.reset_local_cache()

    def test_guard_refuses_a_sanctioned_address(self):
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_CIDR,
            subject_value="203.0.113.0/24",
            actor=STAFF,
        )
        self.assertTrue(portal_guard.refuses("203.0.113.7"))

    @override_settings(MODERATION_PORTAL_BLOCK_ENABLED=False)
    def test_guard_can_be_switched_off(self):
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_IP, subject_value="203.0.113.7", actor=STAFF
        )
        self.assertFalse(portal_guard.refuses("203.0.113.7"))

    def test_refusal_text_names_no_mechanism(self):
        text = portal_guard.REFUSAL_TEXT.lower()
        for leak in ("ip", "cidr", "network", "device", "fingerprint", "sanction"):
            self.assertNotIn(leak, text)
        self.assertIn("staff", text)
