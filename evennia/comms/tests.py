from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from evennia.commands.default.comms import CmdChannel
from evennia.comms.comms import DefaultChannel
from evennia.utils.create import create_message
from evennia.utils.test_resources import BaseEvenniaTest


class TestCommsNickMatchesCommand(SimpleTestCase):
    def test(self):
        """
        Verifies that the nick being set by DefaultChannel matches the channel
        command key.
        """
        self.assertTrue(DefaultChannel.channel_msg_nick_replacement.startswith(CmdChannel.key))


class ObjectCreationTest(BaseEvenniaTest):
    def test_channel_create(self):
        description = "A place to talk about coffee."

        obj, errors = DefaultChannel.create("coffeetalk", description=description)
        self.assertTrue(obj, errors)
        self.assertFalse(errors, errors)
        self.assertEqual(description, obj.db.desc)

    def test_message_create(self):
        msg = create_message("peewee herman", "heh-heh!", header="mail time!")
        self.assertTrue(msg)
        self.assertEqual(str(msg), "peewee herman->: heh-heh!")


class ChannelSubscriptionTests(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        self.default_channel, _ = DefaultChannel.create(
            "catlovers", description="A place for feline fanciers."
        )
        self.default_channel.connect(self.obj1)

    def test_subscribe_unsubscribe(self):
        self.default_channel.connect(self.char1)
        self.assertTrue(self.default_channel.subscriptions.has(self.char1))
        self.assertEqual(
            self.char1.nicks.nickreplace("catlovers I love cats!"),
            "@channel catlovers = I love cats!",
        )
        self.default_channel.disconnect(self.char1)
        self.assertFalse(self.default_channel.subscriptions.has(self.char1))
        self.assertEqual(
            self.char1.nicks.nickreplace("catlovers I love cats!"),
            "catlovers I love cats!",
        )


class ChannelWholistTests(BaseEvenniaTest):
    def setUp(self):
        super().setUp()
        self.default_channel, _ = DefaultChannel.create(
            "coffeetalk", description="A place to talk about coffee."
        )
        self.default_channel.connect(self.obj1)

    def test_wholist_shows_subscribed_objects(self):
        expected = "Obj"
        result = self.default_channel.wholist
        self.assertEqual(expected, result)

    def test_wholist_shows_none_when_empty(self):
        # No one hates dogs
        empty_channel, _ = DefaultChannel.create(
            "doghaters", description="A place where dog haters unite."
        )
        expected = "<None>"
        result = empty_channel.wholist
        self.assertEqual(expected, result)

    def test_wholist_does_not_show_muted_objects(self):
        self.default_channel.mute(self.obj2)
        expected = "Obj"
        result = self.default_channel.wholist
        self.assertEqual(expected, result)

    def test_wholist_shows_connected_object_as_bold(self):
        self.default_channel.connect(self.char1)
        expected = "Obj, |wChar|n"
        result = self.default_channel.wholist
        self.assertEqual(expected, result)


class TestRemoveSubscriberFromAllChannels(BaseEvenniaTest):
    """F-4: pre_delete propagates ref removal across every subscribed channel."""

    def setUp(self):
        super().setUp()
        self.ch1, _ = DefaultChannel.create("ch_one", description="one")
        self.ch2, _ = DefaultChannel.create("ch_two", description="two")
        self.ch_unrelated, _ = DefaultChannel.create("ch_other", description="other")

    def _fake_redis(self):
        """Return a MagicMock with a pipeline()/execute() recorder."""
        r = MagicMock()
        pipe = MagicMock()
        r.pipeline.return_value = pipe
        return r, pipe

    def test_object_subscriber_srem_fires_for_every_subscribed_channel(self):
        from evennia.comms import channel_subscriber_cache

        self.ch1.subscriptions.add(self.char1)
        self.ch2.subscriptions.add(self.char1)

        r, pipe = self._fake_redis()
        with patch.object(channel_subscriber_cache, "_redis_conn", return_value=r):
            channel_subscriber_cache.remove_subscriber_from_all_channels(self.char1)

        expected_ref = "o:%s" % self.char1.pk
        srem_calls = {(c.args[0], c.args[1]) for c in pipe.srem.call_args_list}
        self.assertIn(("chsubs:v1:%s" % self.ch1.id, expected_ref), srem_calls)
        self.assertIn(("chsubs:v1:%s" % self.ch2.id, expected_ref), srem_calls)
        self.assertNotIn(("chsubs:v1:%s" % self.ch_unrelated.id, expected_ref), srem_calls)
        pipe.execute.assert_called_once()

    def test_account_subscriber_srem_fires_for_every_subscribed_channel(self):
        from evennia.comms import channel_subscriber_cache

        self.ch1.subscriptions.add(self.account)

        r, pipe = self._fake_redis()
        with patch.object(channel_subscriber_cache, "_redis_conn", return_value=r):
            channel_subscriber_cache.remove_subscriber_from_all_channels(self.account)

        expected_ref = "a:%s" % self.account.pk
        srem_calls = {(c.args[0], c.args[1]) for c in pipe.srem.call_args_list}
        self.assertIn(("chsubs:v1:%s" % self.ch1.id, expected_ref), srem_calls)

    def test_no_subscriptions_is_noop(self):
        from evennia.comms import channel_subscriber_cache

        r, pipe = self._fake_redis()
        with patch.object(channel_subscriber_cache, "_redis_conn", return_value=r):
            channel_subscriber_cache.remove_subscriber_from_all_channels(self.char1)

        pipe.srem.assert_not_called()
        pipe.execute.assert_not_called()

    def test_pre_delete_signal_invokes_helper(self):
        """End-to-end: deleting a subscribed entity fires the cache cleanup."""
        from evennia.comms import channel_subscriber_cache

        self.ch1.subscriptions.add(self.char1)
        self.ch2.subscriptions.add(self.char1)

        with patch.object(
            channel_subscriber_cache, "remove_subscriber_from_all_channels"
        ) as helper:
            self.char1.delete()

        helper.assert_called()
        # The character itself is the deleted instance the helper receives.
        deleted_instances = [call.args[0] for call in helper.call_args_list]
        self.assertIn(self.char1, deleted_instances)

    def test_non_subscribable_instance_skipped(self):
        """pre_delete on instances without subscription reverse managers is a no-op."""
        from evennia.comms import models as comms_models

        instance = MagicMock(spec=[])  # no account_/object_subscription_set
        with patch.object(
            comms_models,
            "_drop_channel_subscriber_cache_on_delete",
            wraps=comms_models._drop_channel_subscriber_cache_on_delete,
        ):
            with patch(
                "evennia.comms.channel_subscriber_cache.remove_subscriber_from_all_channels"
            ) as helper:
                comms_models._drop_channel_subscriber_cache_on_delete(
                    sender=type(instance), instance=instance
                )
        helper.assert_not_called()
