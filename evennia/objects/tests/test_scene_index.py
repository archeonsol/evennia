"""
Tests for room scene index.
"""

from unittest.mock import MagicMock, patch

from django.test import override_settings

from evennia.utils.test_resources import BaseEvenniaTest


class TestRoomSceneIndex(BaseEvenniaTest):
    @override_settings(ROOM_SCENE_INDEX_ENABLED=True)
    @patch("evennia.objects.scene_index._redis_conn")
    def test_add_and_get_ids(self, mock_redis):
        from evennia.objects.scene_index import add_to_room, get_member_ids, sync_room

        r = MagicMock()
        r.exists.return_value = True
        r.smembers.return_value = {str(self.char1.id)}
        mock_redis.return_value = r

        add_to_room(self.room1, self.char1)
        ids = get_member_ids(self.room1)
        self.assertIn(self.char1.id, ids)
