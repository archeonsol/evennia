"""Address intelligence: lookups, lists, and every way they are allowed to fail.

The failure modes matter more than the happy path. A missing database, an
absent reader package and an empty list must all produce "not looked up" -- a
distinct answer from "clean" -- and none of them may raise, because this code
runs on the path that writes a session row for a player who is logging in.
"""

from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from evennia.moderation import capture, enrich
from evennia.server.models import ModerationFlag, ServerConfig, SessionRecord


class FakeReader:
    """Stands in for a maxminddb reader over a handful of addresses."""

    def __init__(self, records):
        self.records = records
        self.closed = False

    def get(self, address):
        return self.records.get(address)

    def close(self):
        self.closed = True


_ASN_RECORDS = {
    "104.16.0.1": {
        "autonomous_system_number": 13335,
        "autonomous_system_organization": "CLOUDFLARENET",
    },
    "81.2.69.142": {
        "autonomous_system_number": 5089,
        "autonomous_system_organization": "Virgin Media",
    },
}
_COUNTRY_RECORDS = {
    "104.16.0.1": {"country": {"iso_code": "US"}},
    "81.2.69.142": {"country": {"iso_code": "GB"}},
}


class EnrichBase(TestCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(self._reset)
        self._reset()

    def _reset(self):
        enrich.close_readers()
        enrich._cache_version = None
        enrich._datacenter_asns = frozenset()
        enrich._tor_exits = frozenset()

    def with_readers(self):
        """Install fake mmdb readers for the duration of one test."""
        enrich._readers_loaded = True
        enrich._asn_reader = FakeReader(_ASN_RECORDS)
        enrich._country_reader = FakeReader(_COUNTRY_RECORDS)


class LookupTest(EnrichBase):
    def test_no_database_configured_means_nothing_is_looked_up(self):
        # An empty dict, not a dict of nulls: the columns keep meaning
        # "not looked up yet" rather than "looked up and found nothing".
        self.assertEqual(enrich.describe("104.16.0.1"), {})

    def test_asn_and_country_are_recorded(self):
        self.with_readers()
        described = enrich.describe("81.2.69.142")

        self.assertEqual(described["asn"], 5089)
        self.assertEqual(described["asn_org"], "Virgin Media")
        self.assertEqual(described["country"], "GB")

    def test_a_hosting_asn_is_marked(self):
        self.with_readers()
        enrich.store_lists(datacenter_asns=[13335, 16509])

        self.assertTrue(enrich.describe("104.16.0.1")["is_datacenter"])
        self.assertFalse(enrich.describe("81.2.69.142")["is_datacenter"])

    def test_a_tor_exit_is_marked(self):
        self.with_readers()
        enrich.store_lists(tor_exits=["104.16.0.1"])

        self.assertTrue(enrich.describe("104.16.0.1")["is_tor"])
        self.assertFalse(enrich.describe("81.2.69.142")["is_tor"])

    def test_an_empty_tor_list_answers_nothing(self):
        # No list is not the same as an empty answer -- claiming every address
        # is not a Tor exit would be a lookup nobody performed.
        self.with_readers()
        self.assertNotIn("is_tor", enrich.describe("104.16.0.1"))

    def test_private_and_malformed_addresses_are_skipped(self):
        self.with_readers()
        for address in ("127.0.0.1", "10.0.0.4", "not-an-address", "", None):
            with self.subTest(address=address):
                self.assertEqual(enrich.describe(address), {})

    def test_an_unknown_address_yields_no_keys(self):
        self.with_readers()
        self.assertEqual(enrich.describe("8.8.4.4"), {})

    def test_a_raising_reader_does_not_propagate(self):
        enrich._readers_loaded = True
        enrich._asn_reader = mock.Mock(get=mock.Mock(side_effect=RuntimeError("mmdb broke")))
        enrich._country_reader = None

        self.assertEqual(enrich.describe("104.16.0.1"), {})

    @override_settings(MODERATION_GEOIP_ASN_DB="/nowhere/GeoLite2-ASN.mmdb")
    def test_a_missing_database_file_disables_lookups_quietly(self):
        self.assertEqual(enrich.describe("104.16.0.1"), {})


class ListStorageTest(EnrichBase):
    def test_storing_bumps_the_version_so_other_processes_reload(self):
        enrich.store_lists(datacenter_asns=[1])
        first = ServerConfig.objects.conf(enrich.KEY_VERSION)
        enrich.store_lists(tor_exits=["104.16.0.1"])
        second = ServerConfig.objects.conf(enrich.KEY_VERSION)

        self.assertGreater(second, first)

    def test_one_list_can_be_refreshed_without_clearing_the_other(self):
        enrich.store_lists(datacenter_asns=[13335], tor_exits=["104.16.0.1"])
        enrich.store_lists(tor_exits=["81.2.69.142"])
        state = enrich.list_state()

        self.assertEqual(state["datacenter_asns"], 1)
        self.assertEqual(state["tor_exits"], 1)

    def test_values_are_deduplicated_and_sorted(self):
        enrich.store_lists(datacenter_asns=["16509", 13335, 13335])
        self.assertEqual(ServerConfig.objects.conf(enrich.KEY_DATACENTER_ASNS), (13335, 16509))

    def test_a_never_fetched_list_reports_stale(self):
        self.assertTrue(enrich.list_state()["stale"])

    def test_a_fresh_list_does_not(self):
        enrich.store_lists(tor_exits=["104.16.0.1"])
        self.assertFalse(enrich.list_state()["stale"])

    @override_settings(MODERATION_NETWORK_LIST_MAX_AGE_DAYS=1)
    def test_an_old_list_reports_stale(self):
        enrich.store_lists(tor_exits=["104.16.0.1"])
        ServerConfig.objects.conf(
            enrich.KEY_UPDATED, (timezone.now() - timezone.timedelta(days=9)).isoformat()
        )
        self.assertTrue(enrich.list_state()["stale"])


class CaptureIntegrationTest(EnrichBase):
    def _snapshot(self, **overrides):
        snap = {
            "session_uid": "uid" + "0" * 29,
            "account_id": None,
            "account_name": "Vex",
            "protocol": "telnet",
            "ip": "104.16.0.1",
            "cidr": "104.16.0.0/24",
            "connected_at": timezone.now(),
        }
        snap.update(overrides)
        return snap

    def test_the_row_carries_the_lookup(self):
        self.with_readers()
        enrich.store_lists(datacenter_asns=[13335], tor_exits=["104.16.0.1"])

        capture.persist_snapshot(self._snapshot(), phase="login")
        row = SessionRecord.objects.get()

        self.assertEqual(row.asn, 13335)
        self.assertEqual(row.country, "US")
        self.assertTrue(row.is_datacenter)
        self.assertTrue(row.is_tor)

    def test_without_a_database_the_columns_stay_null(self):
        capture.persist_snapshot(self._snapshot(), phase="login")
        row = SessionRecord.objects.get()

        self.assertIsNone(row.asn)
        self.assertIsNone(row.is_datacenter)
        self.assertIsNone(row.is_tor)

    def test_a_broken_lookup_does_not_lose_the_row(self):
        # The session is the record that matters; enrichment is a bonus column.
        with mock.patch.object(enrich, "describe", side_effect=RuntimeError("boom")):
            capture.persist_snapshot(self._snapshot(), phase="login")

        self.assertEqual(SessionRecord.objects.count(), 1)


class DetectorTest(EnrichBase):
    def _record(self, **overrides):
        data = {
            "session_uid": "uid" + "1" * 29,
            "account_name": "Vex",
            "protocol": "telnet",
            "ip": "104.16.0.1",
            "cidr": "104.16.0.0/24",
            "connected_at": timezone.now(),
        }
        data.update(overrides)
        return SessionRecord.objects.create(**data)

    def _snapshot(self, row):
        return {
            "session_uid": row.session_uid,
            "account_id": None,
            "account_name": row.account_name,
            "cidr": row.cidr,
        }

    def test_a_hosting_address_is_flagged_not_blocked(self):
        from evennia.moderation.detect import detect_datacenter

        row = self._record(asn=13335, asn_org="CLOUDFLARENET", is_datacenter=True)
        flag = detect_datacenter(self._snapshot(row))

        self.assertIsNotNone(flag)
        self.assertEqual(flag.kind, ModerationFlag.KIND_DATACENTER)
        self.assertEqual(flag.state, ModerationFlag.STATE_OPEN)
        self.assertIn("CLOUDFLARENET", flag.summary)
        # The observation changed nothing about the account it names.
        self.assertIsNone(flag.sanction_id)

    def test_a_residential_address_is_not_flagged(self):
        from evennia.moderation.detect import detect_datacenter

        row = self._record(asn=5089, asn_org="Virgin Media", is_datacenter=False)
        self.assertIsNone(detect_datacenter(self._snapshot(row)))

    def test_an_unlooked_address_is_not_flagged(self):
        from evennia.moderation.detect import detect_datacenter, detect_tor

        row = self._record()
        self.assertIsNone(detect_datacenter(self._snapshot(row)))
        self.assertIsNone(detect_tor(self._snapshot(row)))

    def test_a_tor_exit_is_flagged(self):
        from evennia.moderation.detect import detect_tor

        row = self._record(is_tor=True)
        flag = detect_tor(self._snapshot(row))

        self.assertEqual(flag.kind, ModerationFlag.KIND_TOR)
        self.assertEqual(flag.severity, 2)

    def test_the_same_observation_twice_bumps_one_row(self):
        from evennia.moderation.detect import detect_tor

        row = self._record(is_tor=True)
        detect_tor(self._snapshot(row))
        flag = detect_tor(self._snapshot(row))

        self.assertEqual(ModerationFlag.objects.count(), 1)
        self.assertEqual(flag.seen_count, 2)


class AdvisoryTest(EnrichBase):
    @override_settings(MODERATION_GEOIP_ASN_DB="", MODERATION_GEOIP_COUNTRY_DB="")
    def test_the_advisory_names_an_unconfigured_database(self):
        lines = "\n".join(enrich_lines())
        self.assertIn("no database configured", lines)

    @override_settings(MODERATION_GEOIP_ASN_DB="/nowhere/GeoLite2-ASN.mmdb")
    def test_a_missing_file_is_a_note_not_a_fault(self):
        # A missing optional database means fewer columns. FAULT in this report
        # means the addresses already recorded are wrong.
        lines = "\n".join(enrich_lines())
        self.assertNotIn("FAULT", lines)
        # Which note depends on whether maxminddb is installed in this
        # environment; both are notes and neither is a fault.
        self.assertTrue(
            "GeoLite2 file not found" in lines or "maxminddb is not installed" in lines,
            lines,
        )

    def test_the_advisory_reports_list_freshness(self):
        enrich.store_lists(datacenter_asns=[13335])
        lines = "\n".join(enrich_lines())

        self.assertIn("1 datacenter ASNs", lines)


def enrich_lines():
    from evennia.moderation.checks import enrichment_lines

    return enrichment_lines()
