"""Serve Django over ASGI (uvicorn) as an asyncio service."""

import threading

from evennia.server.service_registry import Service
from evennia.utils import logger


class UvicornWebService(Service):
    """Run uvicorn(evennia.server.asgi:application) in a background thread."""

    def __init__(self, port, interface="127.0.0.1"):
        super().__init__()
        self.port = port
        self.interface = interface
        self._server = None
        self._thread = None

    def _on_start(self):
        try:
            import uvicorn
        except ImportError:
            logger.log_err("WEB_SERVER='asgi' but uvicorn is not installed.")
            return

        config = uvicorn.Config(
            "evennia.server.asgi:application",
            host=self.interface,
            port=int(self.port),
            log_level="warning",
            lifespan="off",
            access_log=False,
        )
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(
            target=self._run, name=f"uvicorn-web-{self.port}", daemon=True
        )
        self._thread.start()
        logger.log_info(f"ASGI webserver (uvicorn) starting on {self.interface}:{self.port}")

    def _run(self):
        import asyncio

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._server.serve())
        except Exception:
            logger.log_trace("uvicorn serve loop exited")

    async def empty_threadpool(self):
        """No-op compatibility hook for graceful shutdown."""
        return None

    def _on_stop(self):
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=5)
