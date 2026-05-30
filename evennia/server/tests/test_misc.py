# -*- coding: utf-8 -*-

"""
Testing various individual functionalities in the server package.

"""

import gc
import unittest
import weakref

from django.test import TestCase
from django.test.runner import DiscoverRunner

from evennia.server.serversession import ServerSession
from evennia.server.throttle import Throttle
from evennia.utils.test_resources import BaseEvenniaTest

from ..deprecations import check_errors


class MockSettings:
    """
    Minimal stand-in for django.conf.settings carrying defaults that
    pass every check_errors gate. Tests override one field to exercise
    a single failure mode.
    """

    WEBSERVER_ENABLED = False
    WEBSERVER_PORTS = [(4001, 4002)]
    CHANNEL_CONNECTINFO = None
    GAME_DIR = "/tmp/__nonexistent_for_test__"
    MULTISESSION_MODE = 2
    MAX_NR_SIMULTANEOUS_PUPPETS = 1

    def __init__(self, **overrides):
        for key, val in overrides.items():
            setattr(self, key, val)


class TestDeprecations(TestCase):
    """check_errors gates covering current (post-pre-1.0-cleanup) checks."""

    def test_webserver_ports_must_be_tuples(self):
        self.assertRaises(
            DeprecationWarning,
            check_errors,
            MockSettings(WEBSERVER_ENABLED=True, WEBSERVER_PORTS=["not a tuple"]),
        )

    def test_webserver_ports_accepts_tuple_form(self):
        check_errors(MockSettings(WEBSERVER_ENABLED=True, WEBSERVER_PORTS=[(4001, 4002)]))

    def test_channel_connectinfo_must_be_dict_or_none(self):
        self.assertRaises(
            DeprecationWarning,
            check_errors,
            MockSettings(CHANNEL_CONNECTINFO="not a dict"),
        )

    def test_multisession_coherence(self):
        self.assertRaises(
            DeprecationWarning,
            check_errors,
            MockSettings(MULTISESSION_MODE=1, MAX_NR_SIMULTANEOUS_PUPPETS=2),
        )


class ThrottleTest(BaseEvenniaTest):
    """
    Class for testing the connection/IP throttle.
    """

    def test_throttle(self):
        ips = ("256.256.256.257", "257.257.257.257", "258.258.258.258")
        kwargs = {"name": "testing", "limit": 5, "timeout": 15 * 60}

        throttle = Throttle(**kwargs)

        for ip in ips:
            # Throttle should not be engaged by default
            self.assertFalse(throttle.check(ip))

            # Pretend to fail a bunch of events
            for x in range(50):
                obj = throttle.update(ip)
                self.assertFalse(obj)

            # Next ones should be blocked
            self.assertTrue(throttle.check(ip))

            for x in range(throttle.cache_size * 2):
                obj = throttle.update(ip)
                self.assertFalse(obj)

            # Should still be blocked
            self.assertTrue(throttle.check(ip))

            # Number of values should be limited by cache size
            self.assertEqual(throttle.cache_size, len(throttle.get(ip)))

        cache = throttle.get()

        # Make sure there are entries for each IP
        self.assertEqual(len(ips), len(cache.keys()))

        # There should only be (cache_size * num_ips) total in the Throttle cache
        self.assertEqual(sum([len(cache[x]) for x in cache.keys()]), throttle.cache_size * len(ips))

        # Make sure the cache is populated
        self.assertTrue(throttle.get())

        # Remove the test IPs from the throttle cache
        # (in case persistent storage was configured by the user)
        for ip in ips:
            self.assertTrue(throttle.remove(ip))

        # Make sure the cache is empty
        self.assertFalse(throttle.get())


class TestServerSessionAttributeLifecycle(BaseEvenniaTest):
    """
    Regression test for the AttributeHandler/ServerSession retention discussed in #3225.
    """

    def test_serversession_attributes_do_not_prevent_gc(self):
        refs = []

        for idx in range(50):
            sess = ServerSession()
            sess.init_session("telnet", "127.0.0.1", sessionhandler=None)
            # Force-create the lazy handler and store an in-memory Attribute.
            sess.attributes.add("last_cmd", f"quit-{idx}")
            refs.append(weakref.ref(sess))
            sess = None

        gc.collect()
        gc.collect()

        self.assertFalse(
            any(ref() is not None for ref in refs),
            "ServerSession instances should be collectable after AttributeHandler use.",
        )


class TestAtSyncFiresPuppetHooks(BaseEvenniaTest):
    """`ServerSession.at_sync` fires `at_pre_puppet` / `at_post_puppet` on
    re-attach (via `reattach=True`) so non-persistent cmdset state the game
    stacks in the puppet path rebuilds after a server reload.

    Pre-`underspire.43` `at_sync` silently re-bound `session.puppet` without
    firing any hooks, leaving the cmdset stack empty for any character that
    builds its merged stack in `at_post_puppet`.
    """

    def test_reattach_fires_pre_and_post_puppet_with_reattach_kwarg(self):
        from unittest.mock import patch

        sess = ServerSession()
        sess.init_session("telnet", "127.0.0.1", sessionhandler=None)
        sess.logged_in = True
        sess.account = self.account
        sess.puid = self.char1.id

        with (
            patch.object(type(self.char1), "at_pre_puppet", return_value=None) as pre,
            patch.object(type(self.char1), "at_post_puppet") as post,
        ):
            sess.at_sync()

        pre.assert_called_once()
        self.assertEqual(pre.call_args.kwargs.get("reattach"), True)
        self.assertIs(pre.call_args.kwargs.get("session"), sess)
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs.get("reattach"), True)
        self.assertIs(sess.puppet, self.char1)

    def test_pre_puppet_veto_aborts_reattach(self):
        from unittest.mock import patch

        sess = ServerSession()
        sess.init_session("telnet", "127.0.0.1", sessionhandler=None)
        sess.logged_in = True
        sess.account = self.account
        sess.puid = self.char1.id

        with (
            patch.object(type(self.char1), "at_pre_puppet", return_value=False),
            patch.object(type(self.char1), "at_post_puppet") as post,
        ):
            sess.at_sync()

        post.assert_not_called()
        self.assertIsNone(sess.puppet)
        self.assertIsNone(sess.puid)

    def test_reattach_default_post_puppet_suppresses_echo(self):
        # `reattach=True` short-circuits the default at_post_puppet body so
        # a server reload does not spam every connected puppet.
        from unittest.mock import patch

        with patch.object(self.char1, "msg") as msg:
            self.char1.at_post_puppet(reattach=True)
        msg.assert_not_called()
