"""Portal shutdown owns and settles every asyncio listener/proxy resource."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from django.test import SimpleTestCase

from evennia.utils import clock


class PortalShutdownTest(SimpleTestCase):
    """Launcher, hook, and signal requests converge on one Portal task."""

    def setUp(self):
        super().setUp()
        self._saved = (clock._main_loop, clock._loop_thread_id, clock._default_executor)
        clock._default_executor = None
        self.loop = asyncio.new_event_loop()
        clock.bind_loop(self.loop)

    def tearDown(self):
        clock.shutdown_default_executor()
        self.loop.close()
        clock._main_loop, clock._loop_thread_id, clock._default_executor = self._saved
        super().tearDown()

    def _service(self):
        from evennia.server.portal.service import EvenniaPortalService

        with patch.object(EvenniaPortalService, "_get_backup_server_cmd", return_value=[]):
            return EvenniaPortalService()

    def test_shutdown_reuses_task_and_awaits_all_resources(self):
        service = self._service()
        order = []
        listener = MagicMock()
        listener.close.side_effect = lambda: order.append("listener-close")
        listener.wait_closed = AsyncMock(side_effect=lambda: order.append("listener-wait"))
        proxy = MagicMock()
        proxy.stop = AsyncMock(side_effect=lambda: order.append("proxy"))
        service._asyncio_servers = [listener]
        service._asyncio_proxies = [proxy]
        service.server_bus = MagicMock()
        service.server_bus.stop_server.side_effect = lambda **_kwargs: order.append("server")

        with (
            patch("evennia.server.portal.service.evennia") as evennia,
            patch(
                "evennia.server.portal.service.stop_launcher_servers",
                new=AsyncMock(side_effect=lambda: order.append("launcher")),
            ),
            patch("evennia.server.portal.service.clock.stop_loop"),
        ):
            evennia.PORTAL_SESSION_HANDLER.disconnect_all.side_effect = lambda: order.append(
                "sessions"
            )
            first = service.shutdown(_stop_server=False)
            second = service.shutdown(_reactor_stopping=True, _stop_server=True)
            self.assertIs(first, second)
            self.loop.run_until_complete(first)

        self.assertEqual(order[0], "sessions")
        self.assertLess(order.index("listener-wait"), order.index("server"))
        self.assertLess(order.index("launcher"), order.index("server"))
        self.assertLess(order.index("proxy"), order.index("server"))
        self.assertEqual(order.count("server"), 1)
        self.assertEqual(service._asyncio_servers, [])
        self.assertEqual(service._asyncio_proxies, [])

    def test_one_proxy_failure_does_not_hide_sibling_cleanup(self):
        service = self._service()
        failed = MagicMock()
        failed.stop = AsyncMock(side_effect=RuntimeError("failed"))
        sibling = MagicMock()
        sibling.stop = AsyncMock()
        service._asyncio_proxies = [failed, sibling]

        with (
            patch("evennia.server.portal.service.evennia"),
            patch(
                "evennia.server.portal.service.stop_launcher_servers",
                new=AsyncMock(),
            ),
            patch("evennia.server.portal.service.clock.stop_loop"),
            patch("evennia.server.portal.service.logger.log_err") as log_err,
        ):
            self.loop.run_until_complete(service.shutdown())

        failed.stop.assert_awaited_once()
        sibling.stop.assert_awaited_once()
        self.assertTrue(log_err.called)

    def _shutdown_with_disconnect_error(self, error):
        """Shut the Portal down when telling the Server its sessions close raises ``error``."""
        service = self._service()
        proxy = MagicMock()
        proxy.stop = AsyncMock()
        service._asyncio_proxies = [proxy]

        with (
            patch("evennia.server.portal.service.evennia") as evennia,
            patch("evennia.server.portal.service.stop_launcher_servers", new=AsyncMock()),
            patch("evennia.server.portal.service.clock.stop_loop"),
            patch("evennia.server.portal.service.logger.log_trace") as log_trace,
            patch("evennia.server.portal.service.logger.log_info") as log_info,
        ):
            evennia.PORTAL_SESSION_HANDLER.disconnect_all = AsyncMock(side_effect=error)
            self.loop.run_until_complete(service.shutdown())

        proxy.stop.assert_awaited_once()  # the rest of the shutdown ran either way
        return log_trace, log_info

    def test_a_server_that_is_already_gone_is_not_an_error(self):
        from evennia.server.bus_result import TransportUnavailable

        log_trace, log_info = self._shutdown_with_disconnect_error(
            TransportUnavailable("peer is not ready")
        )

        log_trace.assert_not_called()
        log_info.assert_called_once()
        self.assertIn("Server is not connected", log_info.call_args.args[0])

    def test_another_disconnect_failure_still_logs_its_traceback(self):
        log_trace, log_info = self._shutdown_with_disconnect_error(ValueError("boom"))

        log_trace.assert_called_once_with("portal session disconnect failed")
        log_info.assert_not_called()

    def test_cancelled_start_closes_post_bind_listener(self):
        """Cancellation between bind and publication cannot lose the socket."""
        service = self._service()
        listener = MagicMock()
        listener.wait_closed = AsyncMock()

        async def exercise():
            bound = self.loop.create_future()
            start = service._track_asyncio_start(
                service._settle_asyncio_start(bound, service._close_asyncio_listener),
                "test-listener-start",
            )
            await asyncio.sleep(0)
            bound.set_result(listener)
            start.cancel()
            await asyncio.gather(start, return_exceptions=True)
            self.assertTrue(start.cancelled())

        self.loop.run_until_complete(exercise())

        listener.close.assert_called_once()
        listener.wait_closed.assert_awaited_once()


class ReverseProxyStopTest(SimpleTestCase):
    """Proxy cleanup is repeatable and closes the client after listener failure."""

    def test_stop_closes_client_when_wait_closed_fails_and_is_idempotent(self):
        from evennia.server.portal.web_proxy import ReverseProxy

        proxy = ReverseProxy("127.0.0.1", 4005)
        server = MagicMock()
        server.wait_closed = AsyncMock(side_effect=RuntimeError("listener failed"))
        client = MagicMock()
        client.aclose = AsyncMock()
        proxy._server = server
        proxy._client = client

        async def exercise():
            with self.assertRaises(ExceptionGroup):
                await proxy.stop()
            await proxy.stop()

        asyncio.run(exercise())
        client.aclose.assert_awaited_once()
        self.assertIsNone(proxy._server)
        self.assertIsNone(proxy._client)
