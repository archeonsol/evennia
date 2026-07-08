import urllib.parse
from unittest.mock import patch

from django.test import TestCase, override_settings
from twisted.internet import defer

from evennia.server.game_index_client import client as game_index_client
from evennia.server.game_index_client.client import EvenniaGameIndexClient
from evennia.utils.http import Response


class _RecordingRequest:
    latest_data = None

    def __call__(self, method, url, headers=None, data=None, timeout=30):
        _RecordingRequest.latest_data = data
        return defer.succeed(Response(200, b"OK"))


@override_settings(
    GAME_INDEX_LISTING={
        "game_status": "pre-alpha",
        "game_name": "TestGame",
        "short_description": "Short",
        "long_description": "Line 1\\nLine 2",
        "listing_contact": "admin@example.com",
    }
)
class TestGameIndexClient(TestCase):
    @patch(
        "evennia.server.game_index_client.client.AccountDB.objects.num_total_accounts",
        return_value=0,
    )
    @patch(
        "evennia.server.game_index_client.client.evennia.SESSION_HANDLER.account_count",
        return_value=0,
    )
    @patch.object(game_index_client.http, "request", new_callable=_RecordingRequest)
    def test_backslash_n_in_long_description_becomes_newline(self, *_):
        client = EvenniaGameIndexClient()
        d = client._form_and_send_request()

        result = []
        d.addCallback(result.append)

        payload = urllib.parse.parse_qs(_RecordingRequest.latest_data)
        self.assertEqual(payload["long_description"][0], "Line 1\nLine 2")
        self.assertEqual(result, [(200, "OK")])
