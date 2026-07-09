"""
The client for sending data to the Evennia Game Index

"""

import platform
import urllib.error
import urllib.parse
import urllib.request

import django
from django.conf import settings

import evennia
from evennia.accounts.models import AccountDB
from evennia.utils import get_evennia_version, http, logger

_EGI_HOST = "http://evennia-game-index.appspot.com"
_EGI_REPORT_PATH = "/api/v1/game/check_in"


class EvenniaGameIndexClient:
    """
    This client class is used for gathering and sending game details to the
    Evennia Game Index. Since EGI is in the early goings, this isn't
    incredibly configurable as far as to what is being sent.
    """

    def __init__(self, on_bad_request=None):
        """
        on_bad_request (callable, optional): Callable to trigger when a bad request was sent.

        """
        self.report_host = _EGI_HOST
        self.report_path = _EGI_REPORT_PATH
        self.report_url = self.report_host + self.report_path
        self.logged_first_connect = False

        self._on_bad_request = on_bad_request

    async def send_game_details(self):
        """
        This is where the magic happens. Send details about the game to the
        Evennia Game Index.
        """
        status_code, response_body = await self._form_and_send_request()
        if status_code == 200:
            if not self.logged_first_connect:
                logger.log_infomsg("Successfully sent game details to Evennia Game Index.")
                self.logged_first_connect = True
            return
        # At this point, either EGD is having issues or the payload we sent
        # is improperly formed (probably due to mis-configuration).
        logger.log_errmsg(
            "Failed to send game details to Evennia Game Index. HTTP "
            "status code was %s. Message was: %s" % (status_code, response_body)
        )

        if status_code == 400 and self._on_bad_request:
            # Improperly formed request. Defer to the callback as far as what
            # to do. Probably not a great idea to continue attempting to send
            # to EGD, though.
            self._on_bad_request()

    def _form_and_send_request(self):
        """
        Build the request to send to the index.

        """
        headers = {
            "User-Agent": "Evennia Game Index Client",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        egi_config = settings.GAME_INDEX_LISTING
        # We are using `or` statements below with dict.get() to avoid sending
        # stringified 'None' values to the server.
        try:
            long_description = egi_config.get("long_description", "")
            if long_description:
                # The connection wizard documents using "\n" for line breaks;
                # normalize that to actual newlines before sending to EGI.
                long_description = long_description.replace("\\n", "\n")

            values = {
                # Game listing stuff
                "game_name": egi_config.get("game_name", settings.SERVERNAME),
                "game_status": egi_config["game_status"],
                "game_website": egi_config.get("game_website", ""),
                "short_description": egi_config["short_description"],
                "long_description": long_description,
                "listing_contact": egi_config["listing_contact"],
                # How to play
                "telnet_hostname": egi_config.get("telnet_hostname", ""),
                "telnet_port": egi_config.get("telnet_port", ""),
                "web_client_url": egi_config.get("web_client_url", ""),
                # Game stats
                "connected_account_count": evennia.SESSION_HANDLER.account_count(),
                "total_account_count": AccountDB.objects.num_total_accounts() or 0,
                # System info
                "evennia_version": get_evennia_version(),
                "python_version": platform.python_version(),
                "django_version": django.get_version(),
                "server_platform": platform.platform(),
            }
        except KeyError as err:
            raise KeyError(f"Error loading GAME_INDEX_LISTING: {err}")

        data = urllib.parse.urlencode(values)

        return http.request("POST", self.report_url, headers=headers, data=data).addCallback(
            self.handle_egd_response
        )

    def handle_egd_response(self, response):
        if 200 <= response.code < 300:
            return (response.code, "OK")
        return (response.code, response.content.decode("utf-8", "replace"))
