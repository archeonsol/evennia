from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase
from mock import MagicMock, patch

from evennia.web.website.views.io import CharacterMenuContextDTO, CharacterMenuDTO

from . import general_context


class TestGeneralContext(SimpleTestCase):
    maxDiff = None

    @patch.object(general_context, "GAME_NAME", "test_name")
    @patch.object(general_context, "GAME_SLOGAN", "test_game_slogan")
    @patch.object(general_context, "REGISTER_ENABLED", "register_enabled_testvalue")
    @patch.object(
        general_context,
        "WEBSOCKET_CLIENT_ENABLED",
        "websocket_client_enabled_testvalue",
    )
    @patch.object(general_context, "WEBCLIENT_ENABLED", "webclient_enabled_testvalue")
    @patch.object(general_context, "WEBSOCKET_PORT", "websocket_client_port_testvalue")
    @patch.object(general_context, "WEBSOCKET_URL", "websocket_client_url_testvalue")
    @patch.object(general_context, "REST_API_ENABLED", True)
    def test_general_context(self):
        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        request.session = {"account": None, "puppet": None}

        response = general_context.general_context(request)

        self.assertEqual(
            response,
            {
                "account": None,
                "puppet": None,
                "puppet_name": None,
                "menu_characters": (),
                "game_name": "test_name",
                "game_slogan": "test_game_slogan",
                "evennia_userapps": ["Accounts"],
                "evennia_entityapps": ["Objects", "Scripts", "Comms", "Help"],
                "evennia_setupapps": ["Permissions", "Config"],
                "evennia_connectapps": ["Irc"],
                "evennia_websiteapps": ["Flatpages", "News", "Sites"],
                "register_enabled": "register_enabled_testvalue",
                "webclient_enabled": "webclient_enabled_testvalue",
                "websocket_enabled": "websocket_client_enabled_testvalue",
                "websocket_port": "websocket_client_port_testvalue",
                "websocket_url": "websocket_client_url_testvalue",
                "rest_api_enabled": True,
                "server_hostname": "localhost",
                "ssh_enabled": False,
                "ssh_ports": [4004],
                "telnet_enabled": True,
                "telnet_ports": [4000],
                "telnet_ssl_enabled": False,
                "telnet_ssl_ports": [4003],
            },
        )

    def test_authenticated_menu_uses_plain_io_context(self):
        request = RequestFactory().get("/")
        request.user = MagicMock(is_authenticated=True, pk=7)
        request.session = {"puppet": 9}
        menu = CharacterMenuContextDTO(
            characters=(CharacterMenuDTO(9, "Web Character", "/characters/puppet/web/9/"),),
            puppet_name="Web Character",
        )

        with patch.object(general_context, "run_on_io_thread", return_value=menu) as bridge:
            response = general_context.general_context(request)

        bridge.assert_called_once_with(general_context.load_character_menu, 7, 9)
        self.assertEqual(response["menu_characters"], menu.characters)
        self.assertEqual(response["puppet"], "Web Character")
        self.assertEqual(response["puppet_name"], "Web Character")

    def test_menu_timeout_fails_soft_without_live_characters(self):
        request = RequestFactory().get("/")
        request.user = MagicMock(is_authenticated=True, pk=7)
        request.session = {"puppet": 9}

        with patch.object(
            general_context,
            "run_on_io_thread",
            side_effect=general_context.IOThreadCallIndeterminate(),
        ):
            response = general_context.general_context(request)

        self.assertEqual(response["menu_characters"], ())
        self.assertIsNone(response["puppet"])
