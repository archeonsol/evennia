"""The device token: minting, verification, and what the portal makes of it."""

from types import SimpleNamespace

from django.test import TestCase, override_settings

from evennia.moderation import device


class MintTest(TestCase):
    def test_a_minted_token_verifies_to_a_bare_value(self):
        token = device.mint()
        value = device.verify(token)

        self.assertEqual(len(value), 32)
        self.assertNotEqual(value, token)
        # The column it lands in is 64 characters; the bare value must fit.
        self.assertLessEqual(len(value), 64)

    def test_two_mints_differ(self):
        self.assertNotEqual(device.mint(), device.mint())

    def test_an_edited_token_is_discarded(self):
        token = device.mint()
        tampered = "f" * 32 + token[32:]

        self.assertEqual(device.verify(tampered), "")

    def test_an_unsigned_value_is_discarded(self):
        # Otherwise a player could name their own device and choose to share it.
        self.assertEqual(device.verify("a" * 32), "")

    def test_junk_is_discarded_rather_than_raising(self):
        for value in ("", None, "  ", "not-a-token", "x" * 500, 12345):
            with self.subTest(value=value):
                self.assertEqual(device.verify(value), "")

    @override_settings(SECRET_KEY="a-different-server-key")
    def test_another_server_key_does_not_validate(self):
        # Signed under the test key in setUp's process, checked under another.
        self.assertEqual(device.verify(_TOKEN_FROM_DEFAULT_KEY), "")


class CookieParsingTest(TestCase):
    def test_the_token_is_read_from_the_handshake_cookies(self):
        token = device.mint()
        headers = {"cookie": f"csrftoken=abc; {device.cookie_name()}={token}; theme=dark"}

        self.assertEqual(device.token_from_headers(headers), device.verify(token))

    def test_a_quoted_cookie_value_is_handled(self):
        token = device.mint()
        headers = {"cookie": f'{device.cookie_name()}="{token}"'}

        self.assertEqual(device.token_from_headers(headers), device.verify(token))

    def test_no_cookie_header_is_no_device(self):
        for headers in ({}, None, {"cookie": ""}, {"cookie": "sessionid=x"}):
            with self.subTest(headers=headers):
                self.assertEqual(device.token_from_headers(headers), "")

    @override_settings(MODERATION_DEVICE_COOKIE="underspire_device")
    def test_the_cookie_name_is_configurable(self):
        token = device.mint()

        self.assertEqual(
            device.token_from_headers({"cookie": f"underspire_device={token}"}),
            device.verify(token),
        )
        self.assertEqual(device.token_from_headers({"cookie": f"device_id={token}"}), "")


class PortalReadTest(TestCase):
    """The websocket protocol's own read, without standing up a connection."""

    def _read(self, headers):
        from evennia.server.portal.webclient import WebSocketClient

        return WebSocketClient._device_token(SimpleNamespace(http_headers=headers))

    def test_a_present_token_reaches_the_protocol(self):
        token = device.mint()
        self.assertEqual(
            self._read({"cookie": f"{device.cookie_name()}={token}"}), device.verify(token)
        )

    def test_a_broken_header_set_is_no_device(self):
        self.assertEqual(self._read(None), "")
        self.assertEqual(self._read("not-a-mapping"), "")


# Signed with the settings in force when this module is imported, so the
# override in MintTest reads it as a token from a different server.
_TOKEN_FROM_DEFAULT_KEY = device.mint()
