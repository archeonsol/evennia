"""Tests for viewer-scoped entity handles (identity-leak fix)."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from evennia.narrative.handles import handle_for, resolve_handle


class _NDB:
    pass


class _Viewer:
    def __init__(self):
        self.ndb = _NDB()


def _char(cid):
    return SimpleNamespace(id=cid)


class TestEntityHandles(unittest.TestCase):
    def test_handle_is_opaque_not_the_db_id(self):
        v = _Viewer()
        h = handle_for(v, _char(42), "Kade")
        self.assertTrue(h.startswith("e"))
        self.assertNotIn("42", h)

    def test_same_perceived_name_stable_handle(self):
        v = _Viewer()
        self.assertEqual(handle_for(v, _char(42), "Kade"), handle_for(v, _char(42), "Kade"))

    def test_lookalikes_receive_distinct_handles(self):
        v = _Viewer()
        self.assertNotEqual(
            handle_for(v, _char(42), "a hooded figure"),
            handle_for(v, _char(43), "a hooded figure"),
        )

    def test_disguise_change_rotates_handle(self):
        # same character, different perceived name -> uncorrelatable handles
        v = _Viewer()
        seen_real = handle_for(v, _char(42), "Kade")
        seen_disguised = handle_for(v, _char(42), "a hooded figure")
        self.assertNotEqual(seen_real, seen_disguised)

    def test_handles_are_per_viewer(self):
        v1, v2 = _Viewer(), _Viewer()
        # different per-viewer salt -> same name yields different handles
        self.assertNotEqual(handle_for(v1, _char(42), "Kade"), handle_for(v2, _char(42), "Kade"))

    def test_forged_handle_does_not_resolve(self):
        v = _Viewer()
        handle_for(v, _char(42), "Kade")
        self.assertIsNone(resolve_handle(v, "edeadbeef"))

    def test_registry_is_bounded(self):
        from evennia.narrative import handles

        v = _Viewer()
        for i in range(handles._MAX_HANDLES + 50):
            handle_for(v, _char(i), "name-%d" % i)
        self.assertLessEqual(len(v.ndb._entity_handles), handles._MAX_HANDLES)

    def test_expired_handle_no_longer_resolves(self):
        from evennia.narrative import handles

        v = _Viewer()
        with patch.object(handles.time, "monotonic", return_value=10.0):
            handle = handle_for(v, _char(42), "Kade")
        with patch.object(
            handles.time,
            "monotonic",
            return_value=10.0 + handles.HANDLE_TTL_SECONDS + 1,
        ):
            self.assertIsNone(resolve_handle(v, handle))
