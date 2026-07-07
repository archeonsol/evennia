"""Asyncio service lifecycle (replaces ``twisted.application.service``)."""

from __future__ import annotations


class _ImmediateResult:
    """Fire-and-forget result compatible with legacy ``.addErrback`` chains."""

    def addErrback(self, _fn, *_args, **_kwargs):
        return self

    def addCallback(self, _fn, *_args, **_kwargs):
        return self


IMMEDIATE_RESULT = _ImmediateResult()


class Service:
    """Minimal service with Twisted-compatible ``startService`` / ``stopService``."""

    name = None

    def __init__(self):
        self.running = False

    def setName(self, name):
        self.name = name

    def setServiceParent(self, parent):
        parent.addService(self)

    def startService(self):
        if self.running:
            return
        self.running = True
        self._on_start()

    def stopService(self):
        if not self.running:
            return
        self._on_stop()
        self.running = False

    def _on_start(self):
        pass

    def _on_stop(self):
        pass


class MultiService(Service):
    """Ordered child service container."""

    def __init__(self):
        super().__init__()
        self._services: list[Service] = []

    def addService(self, service):
        self._services.append(service)

    def privilegedStartService(self):
        self._privileged_start()

    def _privileged_start(self):
        pass

    def _on_start(self):
        self._privileged_start()
        for service in self._services:
            service.startService()

    def _on_stop(self):
        for service in reversed(self._services):
            service.stopService()


class ServiceCollection(MultiService):
    """Root application container (replaces ``twisted.application.service.Application``)."""

    def __init__(self, name="Evennia"):
        super().__init__()
        self.name = name
