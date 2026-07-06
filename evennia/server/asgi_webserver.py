"""Serve Django over ASGI (uvicorn) as a Twisted service.

Option A of the ASGI strangler: uvicorn runs on its own asyncio loop in a worker
thread, inside the Server process (so Django keeps live game-state access). This
matches today's model, where Django already runs in threadpool threads rather
than the reactor thread. Enabled by settings.WEB_SERVER == "asgi"; otherwise the
Twisted-WSGI path is used. Reversible.

Follow-up (Option B): once the asyncio reactor is the norm on Linux, run the ASGI
server on the reactor's shared loop instead of a thread.
"""

import threading

from twisted.application.service import Service

from evennia.utils import logger


class UvicornWebService(Service):
    """Run uvicorn(evennia.server.asgi:application) in a background thread."""

    def __init__(self, port, interface="127.0.0.1"):
        self.port = port
        self.interface = interface
        self._server = None
        self._thread = None

    def startService(self):
        Service.startService(self)
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
            lifespan="off",  # Django's ASGI app doesn't implement the lifespan protocol
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
            # serve() (not run()) skips uvicorn's main-thread signal handlers.
            loop.run_until_complete(self._server.serve())
        except Exception:
            logger.log_trace("uvicorn web thread crashed")
        finally:
            try:
                loop.close()
            except Exception:
                pass

    def stopService(self):
        Service.stopService(self)
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            # Brief join so @reload/@reboot frees the port without hanging.
            self._thread.join(timeout=5)
            self._thread = None
        self._server = None
