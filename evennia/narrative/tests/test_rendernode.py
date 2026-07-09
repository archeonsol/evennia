"""Unit tests for RenderNode v0 and capability-gated deliver_node (R1 slice).

Pure: fake viewers/sessions, no DB. Pins the two contracts the slice rests on -
byte-parity on the text path, and a structured payload only for a session that
announced support.
"""

import unittest

from evennia.narrative.rendernode import CLIENT_NARRATIVE_FLAG, RenderNode, deliver_node


class _Sessions:
    def __init__(self, sessions):
        self._s = sessions

    def all(self):
        return list(self._s)


class _Session:
    def __init__(self, flags):
        self.protocol_flags = flags


class _Viewer:
    """Records msg calls. Omit ``sessions`` to model a session-less recipient."""

    def __init__(self, sessions=None):
        self.calls = []
        if sessions is not None:
            self.sessions = _Sessions(sessions)

    def msg(self, *args, **kwargs):
        self.calls.append((args, kwargs))


def _node(body="does a thing", refs=None):
    return RenderNode(kind="emote", msg_type="pose", body=body, from_id=5, refs=refs or [])


class TestDeliverNode(unittest.TestCase):
    def test_no_sessions_uses_text_facade(self):
        v = _Viewer()  # no .sessions at all
        deliver_node(_node(), v, from_obj="EMITTER")
        self.assertEqual(len(v.calls), 1)
        args, kwargs = v.calls[0]
        self.assertEqual(args[0], ("does a thing", {"type": "pose"}))
        self.assertEqual(kwargs.get("from_obj"), "EMITTER")
        self.assertNotIn("narrative", kwargs)

    def test_incapable_session_uses_text(self):
        v = _Viewer(sessions=[_Session({})])
        deliver_node(_node(), v)
        args, kwargs = v.calls[0]
        self.assertEqual(args[0], ("does a thing", {"type": "pose"}))
        self.assertNotIn("narrative", kwargs)
        self.assertNotIn("session", kwargs)  # parity: no session kwarg either

    def test_capable_session_gets_structured_payload(self):
        cap = _Session({CLIENT_NARRATIVE_FLAG: True})
        v = _Viewer(sessions=[cap])
        deliver_node(_node(refs=[{"name": "Kade", "char_id": 42}]), v, from_obj="E")
        self.assertEqual(len(v.calls), 1)
        _args, kwargs = v.calls[0]
        self.assertIn("narrative", kwargs)
        payload = kwargs["narrative"][0][0]  # (=> [payload], {}) -> payload
        self.assertEqual(payload["body"], "does a thing")
        self.assertEqual(payload["msg_type"], "pose")
        self.assertEqual(payload["from_id"], 5)
        self.assertEqual(payload["refs"], [{"name": "Kade", "char_id": 42}])
        self.assertIs(kwargs["session"], cap)

    def test_mixed_sessions_structured_to_capable_text_to_rest(self):
        cap = _Session({CLIENT_NARRATIVE_FLAG: True})
        tel = _Session({})
        v = _Viewer(sessions=[cap, tel])
        deliver_node(_node(), v)
        self.assertEqual(len(v.calls), 2)
        # structured to the capable session
        self.assertIn("narrative", v.calls[0][1])
        self.assertIs(v.calls[0][1]["session"], cap)
        # text to the remaining (telnet) session(s)
        args, kwargs = v.calls[1]
        self.assertEqual(args[0], ("does a thing", {"type": "pose"}))
        self.assertEqual(kwargs["session"], [tel])

    def test_refs_builder_not_called_for_incapable_viewer(self):
        # The per-viewer resolver behind refs must not run for a text-only viewer.
        calls = []
        v = _Viewer(sessions=[_Session({})])
        deliver_node(_node(), v, refs_builder=lambda: calls.append(True) or [])
        self.assertEqual(calls, [])

    def test_refs_builder_populates_refs_for_capable_viewer(self):
        calls = []

        def builder():
            calls.append(True)
            return [{"name": "Kade", "char_id": 42}]

        cap = _Session({CLIENT_NARRATIVE_FLAG: True})
        v = _Viewer(sessions=[cap])
        deliver_node(_node(), v, refs_builder=builder)
        self.assertEqual(calls, [True])
        payload = v.calls[0][1]["narrative"][0][0]
        self.assertEqual(payload["refs"], [{"name": "Kade", "char_id": 42}])


class TestRenderNodePayload(unittest.TestCase):
    def test_payload_serializes_self_echo(self):
        node = RenderNode(kind="emote", msg_type="pose", body="b", from_id=1, self_echo=True)
        self.assertIs(node.payload()["self_echo"], True)

    def test_payload_serializes_spans_as_list_of_lists(self):
        from evennia.narrative.render import TextSpan

        node = RenderNode(
            kind="emote",
            msg_type="pose",
            body="hi there",
            spans=[[TextSpan("hi")], [TextSpan("there")]],
        )
        payload = node.payload()
        self.assertEqual(len(payload["spans"]), 2)
        self.assertEqual(payload["spans"][0][0]["text"], "hi")
        self.assertEqual(payload["spans"][1][0]["text"], "there")

    def test_payload_omits_spans_when_absent(self):
        node = RenderNode(kind="emote", msg_type="pose", body="b")
        self.assertNotIn("spans", node.payload())


if __name__ == "__main__":
    unittest.main()
