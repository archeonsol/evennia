"""Retention sweeps: what is forgotten, and what must survive being forgotten."""

from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from evennia.moderation import retention
from evennia.server.models import ModerationFlag, Sanction, SessionRecord


class RetentionTest(TestCase):
    def _session(self, *, uid, age_days, **overrides):
        row = SessionRecord.objects.create(
            session_uid=uid.ljust(32, "0")[:32],
            protocol="telnet",
            account_name="Vex",
            ip="104.16.0.1",
            peer_ip="104.16.0.1",
            ip_hash="a" * 64,
            cidr="104.16.0.0/24",
            asn=13335,
            country="US",
            client_fp="b" * 64,
            connected_at=timezone.now(),
            **overrides,
        )
        # connected_at is auto-populated on insert; age it afterwards.
        SessionRecord.objects.filter(pk=row.pk).update(
            connected_at=timezone.now() - timedelta(days=age_days)
        )
        return row

    def test_old_addresses_are_cleared(self):
        self._session(uid="old", age_days=120)
        cleared = retention.purge_addresses()

        row = SessionRecord.objects.get()
        self.assertEqual(cleared, 1)
        self.assertIsNone(row.ip)
        self.assertIsNone(row.peer_ip)

    def test_what_makes_the_row_useful_survives(self):
        # The point of the hash is that history stays queryable after the
        # address is gone.
        self._session(uid="old", age_days=120)
        retention.purge_addresses()

        row = SessionRecord.objects.get()
        self.assertEqual(row.ip_hash, "a" * 64)
        self.assertEqual(row.cidr, "104.16.0.0/24")
        self.assertEqual(row.asn, 13335)
        self.assertEqual(row.client_fp, "b" * 64)

    def test_recent_addresses_are_left_alone(self):
        self._session(uid="new", age_days=10)
        self.assertEqual(retention.purge_addresses(), 0)
        self.assertEqual(SessionRecord.objects.get().ip, "104.16.0.1")

    def test_a_second_run_changes_nothing(self):
        self._session(uid="old", age_days=120)
        retention.purge_addresses()

        self.assertEqual(retention.purge_addresses(), 0)

    @override_settings(MODERATION_IP_RETENTION_DAYS=0)
    def test_zero_days_disables_the_address_purge(self):
        self._session(uid="ancient", age_days=4000)

        self.assertEqual(retention.purge_addresses(), 0)
        self.assertEqual(SessionRecord.objects.get().ip, "104.16.0.1")

    def test_very_old_rows_are_deleted(self):
        self._session(uid="ancient", age_days=400)
        self._session(uid="recent", age_days=30)

        deleted = retention.purge_sessions()

        self.assertEqual(deleted, 1)
        self.assertEqual(SessionRecord.objects.count(), 1)
        self.assertTrue(SessionRecord.objects.filter(session_uid__startswith="recent").exists())

    @override_settings(MODERATION_SESSION_RETENTION_DAYS=0)
    def test_zero_days_disables_the_row_purge(self):
        self._session(uid="ancient", age_days=4000)

        self.assertEqual(retention.purge_sessions(), 0)
        self.assertEqual(SessionRecord.objects.count(), 1)

    def test_deletion_runs_in_bounded_batches(self):
        # One statement over a year of sessions holds a long transaction and
        # bloats the table it is meant to shrink.
        original = retention._DELETE_BATCH
        retention._DELETE_BATCH = 2
        self.addCleanup(setattr, retention, "_DELETE_BATCH", original)

        for index in range(5):
            self._session(uid=f"old{index}", age_days=400)

        self.assertEqual(retention.purge_sessions(), 5)
        self.assertEqual(SessionRecord.objects.count(), 0)

    def test_decisions_a_person_made_are_not_swept(self):
        sanction = Sanction.objects.create(
            subject_type=Sanction.SUBJECT_CIDR,
            subject_value="104.16.0.0/24",
            level=Sanction.LEVEL_BAN,
            reason="evasion",
        )
        flag = ModerationFlag.objects.create(
            kind=ModerationFlag.KIND_SHARED_DEVICE,
            dedupe_key="k" * 40,
            account_name="Vex",
            summary="shared device",
        )
        SessionRecord.objects.filter(pk=self._session(uid="ancient", age_days=4000).pk).exists()

        retention.run_retention()

        self.assertTrue(Sanction.objects.filter(pk=sanction.pk).exists())
        self.assertTrue(ModerationFlag.objects.filter(pk=flag.pk).exists())

    def test_run_retention_reports_both_counts(self):
        self._session(uid="ancient", age_days=400)
        self._session(uid="middle", age_days=120)

        result = retention.run_retention()

        # The 400-day row is deleted, so only the 120-day row is left to clear.
        self.assertEqual(result["sessions_deleted"], 1)
        self.assertEqual(result["addresses_cleared"], 1)

    def test_a_failing_sweep_does_not_stop_the_other(self):
        from unittest import mock

        self._session(uid="ancient", age_days=400)
        with mock.patch.object(retention, "purge_addresses", side_effect=RuntimeError("boom")):
            result = retention.run_retention()

        self.assertEqual(result["addresses_cleared"], 0)
        self.assertEqual(result["sessions_deleted"], 1)
