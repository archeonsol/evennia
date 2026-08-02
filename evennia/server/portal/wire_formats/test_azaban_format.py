"""Round-trip tests for the Azaban shell wire format (azaban.v1)."""

import json
import unittest

from django.test import override_settings

from evennia.server.portal.wire_formats.azaban import AzabanFormat


class TestAzabanFormat(unittest.TestCase):
    def setUp(self):
        self.fmt = AzabanFormat()

    def _env(self, result):
        self.assertIsNotNone(result)
        data, is_binary = result
        self.assertFalse(is_binary)  # Azaban is TEXT frames only
        # Azaban hands the transport the envelope object (it has to stamp a
        # resume seq onto it anyway); tolerate bytes so the helper still works
        # if a frame is ever pre-encoded.
        return data if isinstance(data, dict) else json.loads(data)

    # -- outgoing ----------------------------------------------------------

    def test_text_is_html_envelope(self):
        env = self._env(self.fmt.encode_text("|rhi|n"))
        self.assertEqual(env["t"], "render")
        self.assertIn("hi", env["nodes"][0]["html"])

    def test_text_carries_kind(self):
        env = self._env(self.fmt.encode_text("hi", type="pose"))
        self.assertEqual(env["nodes"][0].get("msg_type"), "pose")

    def test_prompt_envelope(self):
        env = self._env(self.fmt.encode_prompt("HP: 10"))
        self.assertEqual(env["t"], "prompt")
        self.assertIn("HP", env["html"])

    def test_narrative_list_arg(self):
        # deliver_node sends a list of node payloads (non-spread path).
        payloads = [{"kind": "emote", "body": "|rwaves|n", "refs": []}]
        env = self._env(self.fmt.encode_default("narrative", payloads))
        self.assertEqual(env["t"], "render")
        self.assertEqual(len(env["nodes"]), 1)
        self.assertIn("html", env["nodes"][0])
        self.assertIn("waves", env["nodes"][0]["html"])

    def test_narrative_spread_single_dict(self):
        # The outbound path spreads the list, so encode_default sees a single
        # dict positional arg — must still yield a one-node render envelope.
        node = {"kind": "emote", "body": "You test.", "refs": []}
        env = self._env(self.fmt.encode_default("narrative", node))
        self.assertEqual(env["t"], "render")
        self.assertEqual(len(env["nodes"]), 1)
        self.assertEqual(env["nodes"][0]["body"], "You test.")
        self.assertIn("html", env["nodes"][0])

    def test_plain_text_is_normalized_to_render_node_for_rich_protocol(self):
        env = self._env(self.fmt.encode_text("|rWarning|n", type="system"))
        self.assertEqual(env["t"], "render")
        self.assertEqual(env["nodes"][0]["schema"], "render.v1")
        self.assertEqual(env["nodes"][0]["kind"], "text")

    def test_render_payload_rejects_raw_database_identity(self):
        node = {"kind": "emote", "body": "hi", "refs": [{"char_id": 7}]}
        self.assertIsNone(self.fmt.encode_default("narrative", node))

    def test_patch_envelope(self):
        env = self._env(
            self.fmt.encode_default(
                "patch",
                target="scene",
                ops=[{"op": "set", "path": "/", "value": {"a": 1}}],
                meta={"npc_id": 71, "slot": 2, "revision": 4},
            )
        )
        self.assertEqual(env["t"], "patch")
        self.assertEqual(env["target"], "scene")
        self.assertEqual(env["ops"][0]["value"], {"a": 1})
        self.assertEqual(env["meta"], {"npc_id": 71, "slot": 2, "revision": 4})

    def test_patch_rejects_raw_database_identity(self):
        self.assertIsNone(
            self.fmt.encode_default(
                "patch",
                target="scene",
                ops=[{"op": "add", "value": {"char_id": 7}}],
            )
        )

    def test_generic_oob(self):
        env = self._env(self.fmt.encode_default("channel_msg", {"chan": "public"}))
        self.assertEqual(env["t"], "oob")
        self.assertEqual(env["event"], "channel_msg")
        self.assertEqual(env["args"], [{"chan": "public"}])

    def test_play_yt_carries_sync_offset(self):
        env = self._env(self.fmt.encode_default("play_yt", "dQw4w9WgXcQ", 47.25, 1))
        self.assertEqual(env["t"], "oob")
        self.assertEqual(env["event"], "play_yt")
        self.assertEqual(env["args"], ["dQw4w9WgXcQ", 47.25, 1])

    def test_options_command_skipped(self):
        self.assertIsNone(self.fmt.encode_default("options", {}))

    def test_empty_text_skipped(self):
        self.assertIsNone(self.fmt.encode_text(None))
        self.assertIsNone(self.fmt.encode_text())

    # -- incoming ----------------------------------------------------------

    def test_decode_cmd(self):
        out = self.fmt.decode_incoming(b'{"t":"cmd","line":"look"}', False)
        self.assertEqual(out, {"text": [["look"], {}]})

    @override_settings(AZABAN_PUBLIC_ACTIONS=None)
    def test_decode_oob(self):
        out = self.fmt.decode_incoming(
            b'{"t":"oob","action":"react","args":[1],"kwargs":{}}', False
        )
        self.assertEqual(out, {"react": [[1], {}]})

    @override_settings(AZABAN_PUBLIC_ACTIONS=None)
    def test_decode_req_carries_correlation(self):
        out = self.fmt.decode_incoming(
            b'{"t":"req","seq":3,"ns":"cg","action":"autocomplete","data":"lo"}', False
        )
        self.assertEqual(out, {"autocomplete": [["lo"], {"seq": 3, "ns": "cg"}]})

    def test_decode_hello(self):
        out = self.fmt.decode_incoming(b'{"t":"hello","caps":{"rendersNodes":true}}', False)
        self.assertEqual(out, {"azaban_hello": [[], {"caps": {"rendersNodes": True}}]})

    def test_decode_close(self):
        out = self.fmt.decode_incoming(b'{"t":"websocket_close"}', False)
        self.assertEqual(out, {"websocket_close": [[], {}]})

    def test_decode_garbage_ignored(self):
        self.assertIsNone(self.fmt.decode_incoming(b"not json", False))
        self.assertIsNone(self.fmt.decode_incoming(b'["array","not","dict"]', False))

    def test_registered_in_registry(self):
        from evennia.server.portal.wire_formats import WIRE_FORMATS

        self.assertIn("azaban.v1", WIRE_FORMATS)

    # -- incoming hardening ------------------------------------------------

    def test_always_denied_action_rejected(self):
        # An internal/introspection inputfunc must be unreachable from a frame.
        out = self.fmt.decode_incoming(
            b'{"t":"req","seq":1,"action":"get_inputfuncs","data":null}', False
        )
        self.assertIsNone(out)

    def test_oversized_frame_rejected(self):
        big = b'{"t":"cmd","line":"' + b"a" * (70 * 1024) + b'"}'
        self.assertIsNone(self.fmt.decode_incoming(big, False))

    def test_deeply_nested_payload_rejected(self):
        # build data nested past the depth limit
        nested = "null"
        for _ in range(12):
            nested = "[" + nested + "]"
        frame = ('{"t":"oob","action":"react","args":' + nested + "}").encode()
        self.assertIsNone(self.fmt.decode_incoming(frame, False))

    def test_allowlist_denies_unlisted_action(self):
        from django.test import override_settings

        with override_settings(AZABAN_PUBLIC_ACTIONS=["autocomplete"]):
            allowed = self.fmt.decode_incoming(
                b'{"t":"req","action":"autocomplete","data":"x"}', False
            )
            self.assertIsNotNone(allowed)
            denied = self.fmt.decode_incoming(
                b'{"t":"oob","action":"some_internal","args":[]}', False
            )
            self.assertIsNone(denied)

    def test_allowlist_namespace_qualified(self):
        from django.test import override_settings

        with override_settings(AZABAN_PUBLIC_ACTIONS=["cg:autocomplete"]):
            out = self.fmt.decode_incoming(
                b'{"t":"req","ns":"cg","action":"autocomplete","data":"x"}', False
            )
            self.assertEqual(out, {"autocomplete": [["x"], {"seq": None, "ns": "cg"}]})
            # same action under a different namespace is not allowed
            self.assertIsNone(
                self.fmt.decode_incoming(
                    b'{"t":"req","ns":"other","action":"autocomplete","data":"x"}', False
                )
            )

    def test_oob_rejects_non_list_args(self):
        self.assertIsNone(
            self.fmt.decode_incoming(b'{"t":"oob","action":"react","args":"notalist"}', False)
        )

    # -- caps-negotiated server-side HTML ----------------------------------

    def test_text_omits_html_when_client_renders_markup(self):
        flags = {"AZABAN_CAPS": {"rendersMarkup": True}}
        env = self._env(self.fmt.encode_text("|rhi|n", protocol_flags=flags))
        node = env["nodes"][0]
        self.assertNotIn("html", node)
        # The parity anchor still carries the text.
        self.assertIn("hi", node["body"])

    def test_text_keeps_html_without_the_cap(self):
        for flags in ({}, {"AZABAN_CAPS": {}}, {"AZABAN_CAPS": {"rendersMarkup": False}}, None):
            with self.subTest(flags=flags):
                env = self._env(self.fmt.encode_text("|rhi|n", protocol_flags=flags))
                self.assertIn("html", env["nodes"][0])

    def test_text_keeps_html_when_caps_are_malformed(self):
        env = self._env(self.fmt.encode_text("hi", protocol_flags={"AZABAN_CAPS": "nonsense"}))
        self.assertIn("html", env["nodes"][0])

    def test_narrative_omits_html_when_client_renders_markup(self):
        flags = {"AZABAN_CAPS": {"rendersMarkup": True}}
        env = self._env(
            self.fmt.encode_default("narrative", [{"body": "|rhi|n"}], protocol_flags=flags)
        )
        self.assertNotIn("html", env["nodes"][0])

    def test_narrative_keeps_html_without_the_cap(self):
        env = self._env(self.fmt.encode_default("narrative", [{"body": "|rhi|n"}]))
        self.assertIn("html", env["nodes"][0])

    # -- resume ------------------------------------------------------------

    def test_format_opts_into_resume_stamping(self):
        # Frames are JSON envelopes, so the transport may stamp and buffer them.
        self.assertTrue(self.fmt.supports_resume)

    # -- patch meta --------------------------------------------------------

    def test_patch_meta_is_passed_through_unrestricted(self):
        # The multipuppet relay routes on its own keys; an allowlist here would
        # silently break it. The identity and structure guards are the contract.
        meta = {"npc_id": 71, "slot": 2, "revision": 4, "kind": "speech"}
        env = self._env(self.fmt.encode_default("patch", target="scene", ops=[], meta=meta))
        self.assertEqual(env["meta"], meta)


if __name__ == "__main__":
    unittest.main()
