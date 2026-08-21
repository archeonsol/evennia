"""The Portal door limiter: what it refuses, what it never refuses, what it costs.

The bound on memory is asserted as directly as the counting. A limiter that
grows with uptime is a slow leak in the one process that must survive a flood.
"""

from django.test import TestCase, override_settings

from evennia.moderation.ratelimit import ConnectionRateLimiter


@override_settings(
    MODERATION_CONNECT_RATE_ENABLED=True,
    MODERATION_CONNECT_RATE_LIMIT=3,
    MODERATION_CONNECT_RATE_WINDOW=60,
    MODERATION_CONNECT_RATE_MAX_KEYS=4096,
)
class RateLimitTest(TestCase):
    def setUp(self):
        super().setUp()
        self.limiter = ConnectionRateLimiter()

    def _connect(self, address, times=1, now=100.0):
        refused = []
        for offset in range(times):
            refused.append(self.limiter.check(address, now=now + offset * 0.01))
        return refused

    def test_connections_under_the_limit_pass(self):
        self.assertEqual(self._connect("104.16.0.1", times=3), [False, False, False])

    def test_the_next_one_is_refused(self):
        self.assertTrue(self._connect("104.16.0.1", times=4)[-1])

    def test_the_limit_is_per_network_not_per_address(self):
        # Varying the last octet is the first thing a flood does.
        for octet in range(1, 4):
            self.limiter.check(f"104.16.0.{octet}", now=100.0)

        self.assertTrue(self.limiter.check("104.16.0.9", now=100.0))

    def test_a_different_network_is_unaffected(self):
        self._connect("104.16.0.1", times=6)
        self.assertFalse(self.limiter.check("81.2.69.142", now=100.0))

    def test_the_window_slides(self):
        self._connect("104.16.0.1", times=4)
        # Same network, one window later: the old attempts no longer count.
        self.assertFalse(self.limiter.check("104.16.0.1", now=200.0))

    def test_ipv6_is_counted_by_its_network(self):
        self.limiter.check("2606:4700:1:1::1", now=100.0)
        self.limiter.check("2606:4700:1:1::2", now=100.0)
        self.limiter.check("2606:4700:1:1::3", now=100.0)

        self.assertTrue(self.limiter.check("2606:4700:1:1::4", now=100.0))

    def test_loopback_and_private_addresses_are_never_limited(self):
        # A reverse proxy whose forwarded address is not trusted arrives wearing
        # its own address. Limiting that key refuses the entire web client.
        for address in ("127.0.0.1", "10.0.0.5", "192.168.1.20", "::1"):
            with self.subTest(address=address):
                results = [self.limiter.check(address, now=100.0) for _ in range(20)]
                self.assertNotIn(True, results)

    def test_an_unreadable_address_is_not_refused(self):
        for address in (None, "", "not-an-address", ("104.16.0.1", 4000)):
            with self.subTest(address=address):
                self.assertFalse(self.limiter.check(address, now=100.0))

    @override_settings(MODERATION_CONNECT_RATE_ENABLED=False)
    def test_the_limiter_can_be_turned_off(self):
        self.assertNotIn(True, self._connect("104.16.0.1", times=50))

    @override_settings(MODERATION_CONNECT_RATE_LIMIT=0)
    def test_a_zero_limit_disables_rather_than_refusing_everything(self):
        self.assertNotIn(True, self._connect("104.16.0.1", times=50))

    def test_one_network_holds_a_bounded_number_of_timestamps(self):
        self._connect("104.16.0.1", times=500)
        window = self.limiter._windows["104.16.0.0/24"]

        self.assertLessEqual(len(window), 4)

    @override_settings(MODERATION_CONNECT_RATE_MAX_KEYS=8)
    def test_the_map_of_networks_is_bounded(self):
        for third in range(40):
            self.limiter.check(f"104.16.{third}.1", now=100.0)

        self.assertLessEqual(self.limiter.stats()["networks"], 8)

    @override_settings(MODERATION_CONNECT_RATE_MAX_KEYS=4)
    def test_the_least_recently_seen_network_is_the_one_dropped(self):
        for third in range(4):
            self.limiter.check(f"104.16.{third}.1", now=100.0)
        # Touch the oldest, then push one more in: the second oldest goes.
        self.limiter.check("104.16.0.1", now=101.0)
        self.limiter.check("104.16.9.1", now=102.0)

        self.assertIn("104.16.0.0/24", self.limiter._windows)
        self.assertNotIn("104.16.1.0/24", self.limiter._windows)

    def test_a_broken_settings_value_does_not_refuse_anybody(self):
        with override_settings(MODERATION_CONNECT_RATE_LIMIT="not-a-number"):
            self.assertFalse(self.limiter.check("104.16.0.1", now=100.0))


class TestIPv6SiteBucket(TestCase):
    """One IPv6 subscriber holds many /64 networks."""

    def setUp(self):
        self.limiter = ConnectionRateLimiter()

    @override_settings(
        MODERATION_CONNECT_RATE_LIMIT=2,
        MODERATION_CONNECT_RATE_WINDOW=60,
        MODERATION_CONNECT_RATE_SITE_FACTOR=2,
    )
    def test_walking_subnets_no_longer_multiplies_the_limit(self):
        # The defect: a customer is assigned a /48 or /56, so counting only
        # /64 lets one subscriber open the limit once per subnet they hold.
        refused = 0
        for index in range(12):
            address = f"2606:4700:0:{index:x}::1"
            if self.limiter.check(address):
                refused += 1
        self.assertTrue(refused, "every /64 was allowed its own full limit")

    @override_settings(
        MODERATION_CONNECT_RATE_LIMIT=2,
        MODERATION_CONNECT_RATE_WINDOW=60,
        MODERATION_CONNECT_RATE_SITE_FACTOR=4,
    )
    def test_the_site_allowance_is_wider_than_one_network(self):
        # A shared site must not be refused at the same count as one household.
        self.assertFalse(self.limiter.check("2606:4700:0:1::1", now=100.0))
        self.assertFalse(self.limiter.check("2606:4700:0:1::1", now=100.0))
        self.assertTrue(self.limiter.check("2606:4700:0:1::1", now=100.0))
        # A different /64 in the same /48 still has room under the site bucket.
        self.assertFalse(self.limiter.check("2606:4700:0:2::1", now=100.0))

    @override_settings(
        MODERATION_CONNECT_RATE_LIMIT=2,
        MODERATION_CONNECT_RATE_WINDOW=60,
        MODERATION_CONNECT_RATE_SITE_FACTOR=0,
    )
    def test_the_site_bucket_can_be_turned_off(self):
        for index in range(6):
            self.limiter.check(f"2606:4700:0:{index:x}::1", now=100.0)
        self.assertFalse(self.limiter.check("2606:4700:0:99::1", now=100.0))

    @override_settings(
        MODERATION_CONNECT_RATE_LIMIT=2,
        MODERATION_CONNECT_RATE_WINDOW=60,
        MODERATION_CONNECT_RATE_SITE_FACTOR=8,
    )
    def test_ipv4_is_counted_once(self):
        # IPv4 gets one bucket. A second /24 has to be rented.
        self.assertFalse(self.limiter.check("104.16.0.7", now=100.0))
        self.assertFalse(self.limiter.check("104.16.0.8", now=100.0))
        self.assertTrue(self.limiter.check("104.16.0.9", now=100.0))
        self.assertFalse(self.limiter.check("104.17.0.1", now=100.0))

    @override_settings(MODERATION_CONNECT_RATE_LIMIT=2, MODERATION_CONNECT_RATE_WINDOW=60)
    def test_a_private_address_is_still_exempt(self):
        for _ in range(20):
            self.assertFalse(self.limiter.check("fd00::1", now=100.0))
