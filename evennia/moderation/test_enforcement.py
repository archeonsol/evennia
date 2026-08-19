"""Connection-time enforcement and the legacy banlist import."""

from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase

from evennia.moderation import enforcement, sanctions
from evennia.moderation.legacy import ARCHIVE_KEY, MARKER_KEY, import_server_bans
from evennia.server.models import Sanction, SanctionHit, ServerConfig

STAFF = SimpleNamespace(id=7, username="Warden")


class ConnectionKeyTest(TestCase):
    def test_address_yields_both_ip_and_network(self):
        keys = enforcement.connection_keys(username="Bob", ip="203.0.113.7")
        self.assertEqual(keys[Sanction.SUBJECT_ACCOUNT], "bob")
        self.assertEqual(keys[Sanction.SUBJECT_IP], "203.0.113.7")
        self.assertEqual(keys[Sanction.SUBJECT_CIDR], "203.0.113.0/24")

    def test_unknown_values_are_dropped(self):
        self.assertEqual(enforcement.connection_keys(), {})

    def test_hostname_is_not_an_address(self):
        keys = enforcement.connection_keys(ip="underspire.net")
        self.assertNotIn(Sanction.SUBJECT_IP, keys)


class EnforcementTest(TestCase):
    def test_network_sanction_blocks_a_member_address(self):
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_CIDR,
            subject_value="203.0.113.0/24",
            actor=STAFF,
        )
        self.assertTrue(enforcement.check_login(username="bob", ip="203.0.113.7").blocks)

    def test_neighbouring_network_is_untouched(self):
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_CIDR,
            subject_value="203.0.113.0/24",
            actor=STAFF,
        )
        self.assertFalse(enforcement.check_login(username="bob", ip="198.51.100.7").blocks)

    def test_ipv6_is_matched_on_its_slash_64(self):
        # The regex list this replaced could not express IPv6 at all.
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_CIDR,
            subject_value="2001:db8:1:2::/64",
            actor=STAFF,
        )
        self.assertTrue(enforcement.check_login(ip="2001:db8:1:2:aaaa:bbbb:cccc:dddd").blocks)

    def test_non_blocking_levels_do_not_block(self):
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT,
            subject_value="bob",
            level=Sanction.LEVEL_WATCH,
            actor=STAFF,
        )
        decision = enforcement.check_login(username="bob")
        self.assertEqual(decision.level, Sanction.LEVEL_WATCH)
        self.assertFalse(decision.blocks)

    def test_lookup_failure_lets_the_player_in(self):
        # Chosen direction: a database fault must not lock out the playerbase.
        with patch.object(enforcement, "evaluate", side_effect=RuntimeError("db down")):
            self.assertFalse(enforcement.evaluate_connection(username="bob").blocks)

    def test_block_message_never_names_the_signal(self):
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_CIDR,
            subject_value="203.0.113.0/24",
            actor=STAFF,
            reason="Ban evasion.",
            staff_note="matched device token d34db33f",
        )
        decision = enforcement.check_login(ip="203.0.113.7")
        message = enforcement.block_message(decision)
        self.assertIn("Ban evasion.", message)
        self.assertNotIn("203.0.113", message)
        self.assertNotIn("d34db33f", message)
        self.assertNotIn("cidr", message)

    def test_enforcement_is_recorded(self):
        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT, subject_value="bob", actor=STAFF
        )
        decision = enforcement.check_login(username="bob", ip="203.0.113.7")
        enforcement.note_enforcement(decision, username="bob", ip="203.0.113.7")
        hit = SanctionHit.objects.get()
        self.assertEqual(hit.action_taken, SanctionHit.ACTION_BLOCKED)
        self.assertEqual(hit.cidr, "203.0.113.0/24")


class LegacyImportTest(TestCase):
    def test_name_and_address_bans_convert(self):
        ServerConfig.objects.conf(
            "server_bans",
            [
                ("badguy", "", None, "Mon Jan 1 00:00:00 2024", "spam"),
                ("", "203.0.113.7", None, "Mon Jan 1 00:00:00 2024", "raiding"),
            ],
        )
        summary = import_server_bans()

        self.assertEqual(summary["imported"], 2)
        self.assertEqual(
            set(Sanction.objects.values_list("subject_type", "subject_value")),
            {("account", "badguy"), ("ip", "203.0.113.7")},
        )

    def test_wildcard_widens_to_a_network(self):
        ServerConfig.objects.conf("server_bans", [("", "203.0.113.*", None, "", "range ban")])
        import_server_bans()
        sanction = Sanction.objects.get()
        self.assertEqual(sanction.subject_type, Sanction.SUBJECT_CIDR)
        self.assertEqual(sanction.subject_value, "203.0.113.0/24")

    def test_two_octet_wildcard_widens_to_slash_16(self):
        ServerConfig.objects.conf("server_bans", [("", "203.0.*.*", None, "", "")])
        import_server_bans()
        self.assertEqual(Sanction.objects.get().subject_value, "203.0.0.0/16")

    def test_non_contiguous_wildcard_is_skipped_not_guessed(self):
        ServerConfig.objects.conf("server_bans", [("", "203.*.113.7", None, "", "")])
        summary = import_server_bans()
        self.assertEqual(summary["imported"], 0)
        self.assertEqual(summary["skipped"], 1)

    def test_imported_bans_have_no_actor_and_no_expiry(self):
        ServerConfig.objects.conf("server_bans", [("badguy", "", None, "", "spam")])
        import_server_bans()
        sanction = Sanction.objects.get()
        self.assertIsNone(sanction.actor_id)
        self.assertIsNone(sanction.expires_at)
        self.assertEqual(sanction.evidence["source"], "legacy_server_bans")

    def test_original_list_is_archived_then_cleared(self):
        original = [("badguy", "", None, "", "spam")]
        ServerConfig.objects.conf("server_bans", original)
        import_server_bans()
        self.assertEqual(ServerConfig.objects.conf("server_bans"), [])
        self.assertEqual(len(ServerConfig.objects.conf(ARCHIVE_KEY)), 1)

    def test_import_is_idempotent(self):
        ServerConfig.objects.conf("server_bans", [("badguy", "", None, "", "")])
        import_server_bans()
        ServerConfig.objects.conf("server_bans", [("otherguy", "", None, "", "")])
        summary = import_server_bans()
        self.assertTrue(summary["already_done"])
        self.assertEqual(Sanction.objects.count(), 1)

    def test_empty_banlist_just_marks_done(self):
        summary = import_server_bans()
        self.assertEqual(summary["imported"], 0)
        self.assertTrue(ServerConfig.objects.conf(MARKER_KEY))

    def test_imported_ban_enforces(self):
        ServerConfig.objects.conf("server_bans", [("badguy", "", None, "", "spam")])
        import_server_bans()
        self.assertTrue(enforcement.check_login(username="BadGuy").blocks)


class IsBannedTest(TestCase):
    """DefaultAccount.is_banned is the engine's authentication-time gate."""

    def test_sanction_is_seen(self):
        from evennia.accounts.accounts import DefaultAccount

        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_ACCOUNT, subject_value="bob", actor=STAFF
        )
        self.assertTrue(DefaultAccount.is_banned(username="Bob", ip="203.0.113.7"))
        self.assertFalse(DefaultAccount.is_banned(username="alice", ip="203.0.113.7"))

    def test_address_tuple_form_is_accepted(self):
        from evennia.accounts.accounts import DefaultAccount

        sanctions.issue_sanction(
            subject_type=Sanction.SUBJECT_IP, subject_value="203.0.113.7", actor=STAFF
        )
        self.assertTrue(DefaultAccount.is_banned(ip=("203.0.113.7", 4000)))

    def test_unmigrated_legacy_list_still_enforces(self):
        # A deployment that has not yet run the import must not be silently
        # unbanned by the upgrade.
        import re

        from evennia.accounts.accounts import DefaultAccount

        ServerConfig.objects.conf("server_bans", [("legacyguy", "", re.compile(r"$^"), "", "")])
        self.assertTrue(DefaultAccount.is_banned(username="legacyguy"))
