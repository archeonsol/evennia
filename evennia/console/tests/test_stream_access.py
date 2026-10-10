"""Tests for who the live feed admits, and what it says when it refuses.

The feed is a plain async Django view, not a DRF one, so the exceptions the
console raises for an idle or insecure session are not translated for it.
``ConsoleIdle`` and ``ConsoleInsecure`` derive from DRF's ``PermissionDenied``,
which is not Django's. The feed once caught Django's, so a console tab left open
past the idle bound answered every reconnect with an uncaught error: a 500 and
a traceback, about every second, for as long as the tab stayed open.

These tests pin the behaviour a client can act on: a refusal that says why, in
the same shape the REST views use, and an open stream that closes with that
reason instead of raising.

"""

import json
import time
from unittest.mock import patch

from asgiref.sync import async_to_sync
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from evennia.console.registry import CONSOLE_ACCESS
from evennia.console.tests.test_api import HEADERS, ConsoleAPITestCase
from evennia.web.console import auth as console_auth
from evennia.web.console import stream as console_stream


class TestFeedRefusals(ConsoleAPITestCase):
    """An idle or insecure caller is refused with a reason, not an error."""

    def setUp(self):
        super().setUp()
        patcher = patch.object(
            console_stream, "live_capabilities", return_value=frozenset({CONSOLE_ACCESS})
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def _go_idle(self):
        """Age the console's activity stamp past the idle bound."""

        session = self.client.session
        session[console_auth.IDLE_KEY] = int(time.time()) - 100_000
        session.save()

    def test_an_active_session_is_admitted(self):
        """The ordinary request still opens a stream."""

        response = self.client.get(reverse("console:feed"), **HEADERS)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        response.close()

    def test_an_idle_session_is_told_to_sign_in_again(self):
        """Idle is a 403 that names the reason and asks for a fresh sign-in."""

        self._go_idle()
        response = self.client.get(reverse("console:feed"), **HEADERS)
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertTrue(body["reauthenticate"])
        self.assertIn("idle", body["detail"])
        self.assertEqual(response["X-Console-Retryable"], "false")
        self.assertEqual(response["X-Console-Outcome"], "conflict")

    def test_an_idle_refusal_does_not_end_the_website_session(self):
        """Only the console lapses; the player stays signed in to the site."""

        self._go_idle()
        self.client.get(reverse("console:feed"), **HEADERS)
        self.assertIn("_auth_user_id", self.client.session)

    @override_settings(CONSOLE_ALLOW_INSECURE=False)
    def test_an_insecure_request_is_refused_with_a_reason(self):
        """A plain connection is refused, and is not asked to sign in again."""

        response = self.client.get(reverse("console:feed"), HTTP_X_EVENNIA_CONSOLE="1")
        self.assertEqual(response.status_code, 403)
        body = response.json()
        self.assertFalse(body["reauthenticate"])
        self.assertIn("TLS", body["detail"])

    def test_a_missing_header_is_still_a_plain_refusal(self):
        """The old refusals are unchanged."""

        response = self.client.get(reverse("console:feed"), secure=True)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.content, b"The console feed requires console access.")


class TestStreamRecheck(SimpleTestCase):
    """An open stream closes with the reason instead of raising."""

    def test_access_that_stands_is_not_a_refusal(self):
        with patch.object(console_stream, "_authorize", return_value=frozenset({CONSOLE_ACCESS})):
            self.assertIsNone(console_stream._stream_check(object()))

    def test_revoked_access_says_so(self):
        with patch.object(console_stream, "_authorize", return_value=frozenset()):
            refusal = console_stream._stream_check(object())
        self.assertEqual(
            refusal, {"reason": "Console access was revoked.", "reauthenticate": False}
        )

    def test_an_idle_session_asks_for_a_fresh_sign_in(self):
        error = console_auth.ConsoleIdle("This console session sat idle.")
        with patch.object(console_stream, "_authorize", side_effect=error):
            refusal = console_stream._stream_check(object())
        self.assertEqual(
            refusal, {"reason": "This console session sat idle.", "reauthenticate": True}
        )

    def test_an_insecure_session_does_not(self):
        error = console_auth.ConsoleInsecure("The console refuses a non-TLS connection.")
        with patch.object(console_stream, "_authorize", side_effect=error):
            refusal = console_stream._stream_check(object())
        self.assertFalse(refusal["reauthenticate"])

    def test_the_stream_closes_with_the_reason(self):
        """The closing frame carries what the client needs to show."""

        class Quiet:
            """A stream with nothing to say."""

            def replay(self, last_seq):
                return []

            def due(self):
                return []

        refusal = {"reason": "This console session sat idle.", "reauthenticate": True}

        async def collect():
            return [
                frame
                async for frame in console_stream._events(Quiet(), None, recheck=lambda: refusal)
            ]

        with patch.object(console_stream, "RECHECK_SECONDS", -1.0):
            frames = async_to_sync(collect)()

        self.assertEqual(frames[0], ": open\n\n")
        self.assertEqual(len(frames), 2)
        head, _, data = frames[1].partition("\n")
        self.assertEqual(head, "event: closed")
        payload = json.loads(data.removeprefix("data: ").strip())
        self.assertEqual(payload, {"t": "closed", **refusal})
