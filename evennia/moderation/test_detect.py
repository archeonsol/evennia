"""Detectors and the flag queue.

The load-bearing property under test is that detection never enforces: every
detector's only output is a ModerationFlag, and no code path here creates a
Sanction.
"""

from types import SimpleNamespace

from django.test import TestCase, override_settings

from evennia.moderation import detect, flags, sanctions
from evennia.server.models import ModerationFlag, Sanction, SessionRecord

STAFF = SimpleNamespace(id=7, username="Warden")


def record(uid, account_name, **overrides):
    fields = {
        "session_uid": uid,
        "account_name": account_name,
        "protocol": "websocket",
        "ip": "203.0.113.7",
        "cidr": "203.0.113.0/24",
        "ip_hash": "hash-of-203.0.113.7",
    }
    fields.update(overrides)
    return SessionRecord.objects.create(**fields)


def snapshot(account_name, **overrides):
    snap = {
        "session_uid": "live-session",
        "account_name": account_name,
        "account_id": None,
        "ip": "203.0.113.7",
        "cidr": "203.0.113.0/24",
        "ip_hash": "hash-of-203.0.113.7",
        "device_token": "",
        "csessid": "",
        "client_fp": "",
    }
    snap.update(overrides)
    return snap


class FlagQueueTest(TestCase):
    def test_repeat_observation_bumps_instead_of_duplicating(self):
        for _ in range(3):
            flags.raise_flag(
                kind=ModerationFlag.KIND_SHARED_DEVICE,
                dedupe_key="shared_device:abc",
                summary="two accounts",
                account_name="bob",
            )
        flag = ModerationFlag.objects.get()
        self.assertEqual(flag.seen_count, 3)

    def test_dedupe_key_is_order_independent(self):
        first = flags.make_dedupe_key("k", "alice", "bob")
        second = flags.make_dedupe_key("k", "bob", "alice")
        self.assertEqual(first, second)

    def test_dismissed_flag_stays_dismissed_when_seen_again(self):
        flag = flags.raise_flag(
            kind=ModerationFlag.KIND_SHARED_DEVICE, dedupe_key="k:1", account_name="bob"
        )
        flags.resolve_flag(flag, state=ModerationFlag.STATE_DISMISSED, actor=STAFF, note="siblings")

        flags.raise_flag(
            kind=ModerationFlag.KIND_SHARED_DEVICE, dedupe_key="k:1", account_name="bob"
        )
        flag.refresh_from_db()
        self.assertEqual(flag.state, ModerationFlag.STATE_DISMISSED)
        self.assertEqual(flag.seen_count, 2)

    def test_resolving_records_who_decided(self):
        flag = flags.raise_flag(
            kind=ModerationFlag.KIND_SHARED_DEVICE, dedupe_key="k:2", account_name="bob"
        )
        sanction = sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT, subject_value="bob", actor=STAFF
        )
        flags.resolve_flag(
            flag, state=ModerationFlag.STATE_ACTIONED, actor=STAFF, sanction=sanction
        )
        flag.refresh_from_db()
        self.assertEqual(flag.resolved_by_name, "Warden")
        self.assertEqual(flag.sanction_id, sanction.id)
        self.assertIsNotNone(flag.resolved_at)

    def test_unknown_state_is_rejected(self):
        flag = flags.raise_flag(
            kind=ModerationFlag.KIND_SHARED_DEVICE, dedupe_key="k:3", account_name="bob"
        )
        with self.assertRaises(ValueError):
            flags.resolve_flag(flag, state="banished")

    def test_open_flags_are_ordered_by_severity(self):
        flags.raise_flag(kind="a", dedupe_key="a:1", severity=1, account_name="x")
        flags.raise_flag(kind="b", dedupe_key="b:1", severity=4, account_name="y")
        self.assertEqual([flag.kind for flag in flags.open_flags()], ["b", "a"])


class SharedKeyDetectorTest(TestCase):
    def test_shared_device_flags_both_accounts(self):
        record("s1", "alice", device_token="dev-1")
        detect.detect_shared_device(snapshot("bob", device_token="dev-1"))

        flag = ModerationFlag.objects.get()
        self.assertEqual(flag.kind, ModerationFlag.KIND_SHARED_DEVICE)
        self.assertEqual(flag.evidence["accounts"], ["alice", "bob"])

    def test_same_account_reconnecting_is_not_a_flag(self):
        record("s1", "alice", device_token="dev-1")
        detect.detect_shared_device(snapshot("alice", device_token="dev-1"))
        self.assertEqual(ModerationFlag.objects.count(), 0)

    def test_empty_key_never_matches(self):
        record("s1", "alice", device_token="")
        detect.detect_shared_device(snapshot("bob", device_token=""))
        self.assertEqual(ModerationFlag.objects.count(), 0)

    def test_shared_browser_session_is_flagged(self):
        record("s1", "alice", csessid="sess-1")
        detect.detect_shared_csessid(snapshot("bob", csessid="sess-1"))
        self.assertEqual(ModerationFlag.objects.get().kind, ModerationFlag.KIND_SHARED_CSESSID)

    def test_shared_client_needs_the_same_network_too(self):
        record("s1", "alice", client_fp="fp-1", cidr="198.51.100.0/24")
        detect.detect_shared_client_on_network(
            snapshot("bob", client_fp="fp-1", cidr="203.0.113.0/24")
        )
        self.assertEqual(ModerationFlag.objects.count(), 0)

    def test_same_client_on_same_network_is_low_severity(self):
        record("s1", "alice", client_fp="fp-1", cidr="203.0.113.0/24")
        detect.detect_shared_client_on_network(
            snapshot("bob", client_fp="fp-1", cidr="203.0.113.0/24")
        )
        flag = ModerationFlag.objects.get()
        self.assertEqual(flag.severity, 1)

    def test_bare_shared_address_raises_nothing(self):
        # Deliberate: CGNAT and households would bury the queue.
        record("s1", "alice")
        detect.run_detectors(snapshot("bob"))
        self.assertEqual(ModerationFlag.objects.count(), 0)


class EvasionDetectorTest(TestCase):
    def test_key_shared_with_a_sanctioned_account_is_flagged(self):
        record("s1", "alice", device_token="dev-1")
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT, subject_value="alice", actor=STAFF
        )

        detect.detect_sanctioned_key_reuse(snapshot("bob", device_token="dev-1"))

        flag = ModerationFlag.objects.get()
        self.assertEqual(flag.kind, ModerationFlag.KIND_SANCTIONED_KEY)
        self.assertEqual(flag.severity, 4)
        self.assertIn("alice", flag.summary)

    def test_no_flag_when_the_other_account_is_not_sanctioned(self):
        record("s1", "alice", device_token="dev-1")
        detect.detect_sanctioned_key_reuse(snapshot("bob", device_token="dev-1"))
        self.assertEqual(ModerationFlag.objects.count(), 0)

    def test_lifted_sanction_stops_the_flag(self):
        record("s1", "alice", device_token="dev-1")
        sanction = sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT, subject_value="alice", actor=STAFF
        )
        sanctions.revoke_sanction(sanction, actor=STAFF)

        detect.detect_sanctioned_key_reuse(snapshot("bob", device_token="dev-1"))
        self.assertEqual(ModerationFlag.objects.count(), 0)

    def test_watch_level_is_not_evasion(self):
        record("s1", "alice", device_token="dev-1")
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT,
            subject_value="alice",
            level=Sanction.LEVEL_WATCH,
            actor=STAFF,
        )
        detect.detect_sanctioned_key_reuse(snapshot("bob", device_token="dev-1"))
        self.assertEqual(ModerationFlag.objects.count(), 0)

    def test_address_reuse_alone_counts_for_evasion(self):
        # Bare address sharing is not a flag on its own, but it is evidence when
        # the other party is already sanctioned.
        record("s1", "alice")
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT, subject_value="alice", actor=STAFF
        )
        detect.detect_sanctioned_key_reuse(snapshot("bob"))
        self.assertEqual(ModerationFlag.objects.count(), 1)

    def test_detection_creates_no_sanctions(self):
        record("s1", "alice", device_token="dev-1")
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT, subject_value="alice", actor=STAFF
        )
        before = Sanction.objects.count()
        detect.run_detectors(snapshot("bob", device_token="dev-1", csessid="c", client_fp="f"))
        self.assertEqual(Sanction.objects.count(), before)
        self.assertGreater(ModerationFlag.objects.count(), 0)


class DetectorRunnerTest(TestCase):
    @override_settings(MODERATION_DETECTION_ENABLED=False)
    def test_disabled_runs_nothing(self):
        record("s1", "alice", device_token="dev-1")
        self.assertEqual(detect.run_detectors(snapshot("bob", device_token="dev-1")), [])

    def test_one_failing_detector_does_not_stop_the_rest(self):
        record("s1", "alice", device_token="dev-1")

        def explode(_snapshot):
            raise RuntimeError("boom")

        original = detect.DETECTORS
        detect.DETECTORS = (explode, detect.detect_shared_device)
        try:
            raised = detect.run_detectors(snapshot("bob", device_token="dev-1"))
        finally:
            detect.DETECTORS = original
        self.assertEqual(len(raised), 1)

    @override_settings(MODERATION_DETECTION_LOOKBACK_DAYS=90)
    def test_history_outside_the_window_is_ignored(self):
        from datetime import timedelta

        from django.utils import timezone

        old = record("s1", "alice", device_token="dev-1")
        SessionRecord.objects.filter(id=old.id).update(
            connected_at=timezone.now() - timedelta(days=120)
        )
        detect.detect_shared_device(snapshot("bob", device_token="dev-1"))
        self.assertEqual(ModerationFlag.objects.count(), 0)
