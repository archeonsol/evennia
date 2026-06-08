"""Tests for emote segment precomputation (emitter-invariant plans)."""

from unittest.mock import patch

from evennia.narrative import emote
from evennia.narrative.emote import (
    build_emote_segment_plans,
    find_targets_in_text,
    first_to_third,
    parse_quoted_speech,
    split_emote_segments,
)
from evennia.utils.test_resources import EvenniaTest


class TestBuildEmoteSegmentPlans(EvenniaTest):
    """build_emote_segment_plans runs first_to_third + targets once per segment."""

    def test_single_segment_plan_shape(self):
        plans = build_emote_segment_plans(["I wave."], self.char1, [self.char2])
        self.assertEqual(len(plans), 1)
        sp = plans[0]
        self.assertIsInstance(sp.third_text, str)
        self.assertIsInstance(sp.lang_bits, list)
        self.assertIsInstance(sp.targets, list)

    def test_plans_match_inline_pipeline(self):
        text = "I wave at the crowd"
        segments = split_emote_segments(text)
        char_list = [self.char2]
        plans = build_emote_segment_plans(segments, self.char1, char_list)

        for seg, sp in zip(segments, plans):
            expected_third = first_to_third(seg.strip(), self.char1)
            try:
                expected_third, expected_bits = parse_quoted_speech(expected_third)
            except Exception:
                expected_bits = []
            expected_targets = find_targets_in_text(expected_third, char_list, self.char1)
            self.assertEqual(sp.third_text, expected_third)
            self.assertEqual(sp.lang_bits, expected_bits)
            self.assertEqual(sp.targets, expected_targets)

    def test_multi_segment_count(self):
        segments = split_emote_segments("I nod . I wave")
        plans = build_emote_segment_plans(segments, self.char1, [])
        self.assertEqual(len(plans), len(segments))

    @patch.object(emote, "find_targets_in_text")
    @patch.object(emote, "first_to_third", return_value="waves.")
    def test_called_once_per_segment_not_per_viewer(self, mock_third, mock_find):
        mock_find.return_value = []
        segments = ["I wave", "I nod"]
        build_emote_segment_plans(segments, self.char1, [self.char2])
        self.assertEqual(mock_third.call_count, 2)
        self.assertEqual(mock_find.call_count, 2)
