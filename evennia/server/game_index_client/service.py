"""
Service for integrating the Evennia Game Index client into Evennia.
"""

from evennia.server.service_registry import Service
from evennia.utils import clock, logger

from .client import EvenniaGameIndexClient

_FIRST_UPDATE_DELAY = 10
_CLIENT_UPDATE_RATE = 60 * 30


class EvenniaGameIndexService(Service):
    """Service that periodically sends game details to the Evennia Game Index."""

    name = "GameIndexClient"

    def __init__(self):
        super().__init__()
        self.client = EvenniaGameIndexClient(on_bad_request=self._die_on_bad_request)
        self.loop = clock.make_looping(
            lambda: clock.run_coroutine(self.client.send_game_details())
        )

    def _on_start(self):
        clock.call_later(_FIRST_UPDATE_DELAY, self.loop.start, _CLIENT_UPDATE_RATE)

    def _on_stop(self):
        if self.loop.running:
            self.loop.stop()

    def _die_on_bad_request(self):
        logger.log_infomsg(
            "Shutting down Evennia Game Index client service due to invalid configuration."
        )
        self.stopService()
