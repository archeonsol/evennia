"""Registration signals: aliasing, disposable domains, deliverability, bursts.

Every one of these produces a flag and nothing else. The tests assert that as
directly as they assert the arithmetic, because the arithmetic is the easy part
and "it did not ban anybody" is the property the design rests on.
"""

from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from evennia.accounts.models import AccountDB
from evennia.moderation import enrich, registration
from evennia.server.models import ModerationFlag, Sanction, SessionRecord


class NormalizeTest(TestCase):
    def test_plus_tags_collapse_everywhere(self):
        self.assertEqual(
            registration.normalize_email("Player+underspire@fastmail.com"),
            "player@fastmail.com",
        )

    def test_dots_collapse_only_where_the_provider_ignores_them(self):
        self.assertEqual(registration.normalize_email("p.l.a.yer@gmail.com"), "player@gmail.com")
        # Everywhere else a dot is part of the address and removing it would
        # claim two different inboxes are one.
        self.assertEqual(
            registration.normalize_email("p.layer@fastmail.com"), "p.layer@fastmail.com"
        )

    def test_googlemail_is_gmail(self):
        self.assertEqual(
            registration.normalize_email("player@googlemail.com"),
            registration.normalize_email("pla.yer+3@googlemail.com"),
        )

    def test_junk_normalizes_to_nothing(self):
        for value in ("", None, "not-an-email", "@gmail.com", "player@"):
            with self.subTest(value=value):
                self.assertEqual(registration.normalize_email(value), "")

    def test_domains_are_read_case_insensitively(self):
        self.assertEqual(registration.email_domain("Player@Example.COM"), "example.com")


class MxTest(TestCase):
    def test_no_resolver_installed_is_not_an_answer(self):
        # "We could not check" must never become a flag.
        with mock.patch.dict("sys.modules", {"dns.resolver": None, "dns": None}):
            self.assertIsNone(registration.has_mx("example.com"))

    def test_a_domain_with_records_is_deliverable(self):
        resolver = mock.Mock(resolve=mock.Mock(return_value=["mx1"]))
        with mock.patch.dict(
            "sys.modules", {"dns": mock.Mock(resolver=resolver), "dns.resolver": resolver}
        ):
            self.assertTrue(registration.has_mx("example.com"))

    def test_nxdomain_means_undeliverable(self):
        class NXDOMAIN(Exception):
            pass

        resolver = mock.Mock(resolve=mock.Mock(side_effect=NXDOMAIN()))
        with mock.patch.dict(
            "sys.modules", {"dns": mock.Mock(resolver=resolver), "dns.resolver": resolver}
        ):
            self.assertFalse(registration.has_mx("nowhere.invalid"))

    def test_a_timeout_is_not_undeliverable(self):
        class Timeout(Exception):
            pass

        resolver = mock.Mock(resolve=mock.Mock(side_effect=Timeout()))
        with mock.patch.dict(
            "sys.modules", {"dns": mock.Mock(resolver=resolver), "dns.resolver": resolver}
        ):
            self.assertIsNone(registration.has_mx("example.com"))


class ScreenEmailTest(TestCase):
    def setUp(self):
        super().setUp()
        enrich._cache_version = None
        enrich._disposable_domains = frozenset()

    def _account(self, username, email):
        return AccountDB.objects.create(username=username, email=email)

    def test_an_alias_of_an_existing_inbox_is_flagged(self):
        self._account("Vex", "player@gmail.com")
        second = self._account("Ilex", "pla.yer+alt@gmail.com")

        flags = registration.screen_email(
            account_id=second.id, account_name="Ilex", email=second.email
        )

        self.assertEqual(len(flags), 1)
        self.assertEqual(flags[0].kind, ModerationFlag.KIND_EMAIL_ALIAS)
        self.assertIn("Vex", flags[0].evidence["accounts"])

    def test_the_account_does_not_flag_against_itself(self):
        account = self._account("Vex", "player@gmail.com")
        flags = registration.screen_email(
            account_id=account.id, account_name="Vex", email=account.email
        )

        self.assertEqual(flags, [])

    def test_a_similar_address_at_another_provider_is_not_an_alias(self):
        self._account("Vex", "pla.yer@fastmail.com")
        second = self._account("Ilex", "player@fastmail.com")

        self.assertEqual(
            registration.screen_email(
                account_id=second.id, account_name="Ilex", email=second.email
            ),
            [],
        )

    def test_a_disposable_domain_is_flagged(self):
        enrich.store_lists(disposable_domains=["mailinator.com", "Guerrillamail.COM"])
        account = self._account("Vex", "throwaway@guerrillamail.com")

        flags = registration.screen_email(
            account_id=account.id, account_name="Vex", email=account.email
        )

        self.assertEqual([flag.kind for flag in flags], [ModerationFlag.KIND_DISPOSABLE_EMAIL])

    def test_an_undeliverable_domain_is_flagged(self):
        account = self._account("Vex", "someone@nowhere.invalid")
        with mock.patch.object(registration, "has_mx", return_value=False):
            flags = registration.screen_email(
                account_id=account.id, account_name="Vex", email=account.email
            )

        self.assertEqual([flag.kind for flag in flags], [ModerationFlag.KIND_UNDELIVERABLE_EMAIL])

    def test_screening_never_sanctions(self):
        enrich.store_lists(disposable_domains=["mailinator.com"])
        self._account("Vex", "player@gmail.com")
        second = self._account("Ilex", "player+2@gmail.com")
        with mock.patch.object(registration, "has_mx", return_value=False):
            registration.screen_email(
                account_id=second.id, account_name="Ilex", email="player+2@mailinator.com"
            )

        self.assertTrue(ModerationFlag.objects.exists())
        self.assertFalse(Sanction.objects.exists())

    def test_the_account_survives_a_flag(self):
        account = self._account("Vex", "throwaway@mailinator.com")
        enrich.store_lists(disposable_domains=["mailinator.com"])
        registration.screen_email(account_id=account.id, account_name="Vex", email=account.email)

        self.assertTrue(AccountDB.objects.filter(pk=account.id).exists())


class SignupBurstTest(TestCase):
    def _account_with_session(self, username, *, cidr, joined=None):
        account = AccountDB.objects.create(username=username, email=f"{username}@example.com")
        if joined is not None:
            AccountDB.objects.filter(pk=account.id).update(date_joined=joined)
        SessionRecord.objects.create(
            session_uid=f"uid-{username}".ljust(32, "0")[:32],
            protocol="telnet",
            account_id=account.id,
            account_name=username,
            cidr=cidr,
            connected_at=timezone.now(),
        )
        return account

    def _snapshot(self, account, cidr):
        return {
            "session_uid": f"uid-{account.username}".ljust(32, "0")[:32],
            "account_id": account.id,
            "account_name": account.username,
            "cidr": cidr,
        }

    def test_three_new_accounts_on_one_network_raise_one_flag(self):
        from evennia.moderation.detect import detect_signup_burst

        for name in ("Vex", "Ilex"):
            self._account_with_session(name, cidr="203.0.113.0/24")
        third = self._account_with_session("Sorrel", cidr="203.0.113.0/24")

        flag = detect_signup_burst(self._snapshot(third, "203.0.113.0/24"))

        self.assertIsNotNone(flag)
        self.assertEqual(flag.kind, ModerationFlag.KIND_SIGNUP_BURST)
        self.assertEqual(len(flag.evidence["accounts"]), 3)
        self.assertFalse(Sanction.objects.exists())

    def test_a_household_of_two_is_not_a_burst(self):
        from evennia.moderation.detect import detect_signup_burst

        self._account_with_session("Vex", cidr="203.0.113.0/24")
        second = self._account_with_session("Ilex", cidr="203.0.113.0/24")

        self.assertIsNone(detect_signup_burst(self._snapshot(second, "203.0.113.0/24")))

    def test_accounts_on_different_networks_are_not_a_burst(self):
        from evennia.moderation.detect import detect_signup_burst

        self._account_with_session("Vex", cidr="203.0.113.0/24")
        self._account_with_session("Ilex", cidr="198.51.100.0/24")
        third = self._account_with_session("Sorrel", cidr="192.0.2.0/24")

        self.assertIsNone(detect_signup_burst(self._snapshot(third, "192.0.2.0/24")))

    def test_an_old_account_is_not_part_of_a_burst(self):
        from evennia.moderation.detect import detect_signup_burst

        old = timezone.now() - timezone.timedelta(days=400)
        for name in ("Vex", "Ilex"):
            self._account_with_session(name, cidr="203.0.113.0/24", joined=old)
        third = self._account_with_session("Sorrel", cidr="203.0.113.0/24", joined=old)

        self.assertIsNone(detect_signup_burst(self._snapshot(third, "203.0.113.0/24")))

    @override_settings(MODERATION_SIGNUP_BURST_ACCOUNTS=2)
    def test_the_threshold_is_configurable(self):
        from evennia.moderation.detect import detect_signup_burst

        self._account_with_session("Vex", cidr="203.0.113.0/24")
        second = self._account_with_session("Ilex", cidr="203.0.113.0/24")

        self.assertIsNotNone(detect_signup_burst(self._snapshot(second, "203.0.113.0/24")))
