"""Tests for textfold: Unicode text fitted to what a client can display."""

import unittest

from evennia.utils.textfold import fold_text


class TestFoldToAscii(unittest.TestCase):
    """A client that has not confirmed UTF-8 gets plain ASCII."""

    def test_ascii_is_untouched(self):
        text = "\x1b[1mHello|n, world! [ok]\r\n"
        self.assertIs(fold_text(text), text)

    def test_box_drawing_becomes_a_plain_frame(self):
        frame = "┌──────┐\n│ HP 9 │\n├──────┤\n└──────┘"
        self.assertEqual(fold_text(frame), "+------+\n| HP 9 |\n+------+\n+------+")

    def test_double_and_heavy_lines(self):
        self.assertEqual(fold_text("╔══╗║"), "+==+|")
        self.assertEqual(fold_text("━┃┏┛"), "-|++")
        self.assertEqual(fold_text("╭─╮╰╯"), "+-+++")
        self.assertEqual(fold_text("╱╲╳"), "/\\X")
        # Dashed light lines are not double lines.
        self.assertEqual(fold_text("╌┄┈"), "---")

    def test_bars_and_shapes(self):
        self.assertEqual(fold_text("■■■□□"), "###..")
        self.assertEqual(fold_text("▰▰▱"), "##.")
        self.assertEqual(fold_text("████░░"), "####..")
        self.assertEqual(fold_text("▒▓"), ":#")
        self.assertEqual(fold_text("●○◆◇"), "*o*o")
        self.assertEqual(fold_text("▲▼►◄"), "^v><")

    def test_typography(self):
        self.assertEqual(fold_text("“Stay,” she said — ‘now’…"), "\"Stay,\" she said - 'now'.")
        self.assertEqual(fold_text("10–20 • a · b"), "10-20 * a . b")
        self.assertEqual(fold_text("a\u00a0b\u2009c"), "a b c")
        self.assertEqual(fold_text("→ ← ↑ ↓ ⇒"), "> < ^ v >")

    def test_letters_keep_their_base(self):
        self.assertEqual(fold_text("Café Müller, naïve Ångström"), "Cafe Muller, naive Angstrom")
        self.assertEqual(fold_text("ｆｕｌｌ ²"), "full 2")

    def test_every_character_folds_to_one(self):
        # Screens are laid out one column per character, so a fold that turned
        # "—" into "--" pushed every border after it out of line.
        rows = [
            "│ Hair    —     │",
            "│ North → Dock  │",
            "│ Wait…  ½ ß Æ  │",
            "│ 日本 🙂 ±5°    │",
        ]
        for row in rows:
            self.assertEqual(len(fold_text(row)), len(row), fold_text(row))

    def test_what_cannot_be_folded_is_one_question_mark(self):
        # One mark per character, never the two or three a client shows for the
        # bytes of a multi-byte character it cannot decode.
        self.assertEqual(fold_text("a🙂b"), "a?b")
        self.assertEqual(fold_text("日本"), "??")

    def test_colour_codes_survive(self):
        text = "\x1b[38;5;214m──\x1b[0m │"
        self.assertEqual(fold_text(text), "\x1b[38;5;214m--\x1b[0m |")


class TestFoldToEncoding(unittest.TestCase):
    """A player who chose a legacy encoding keeps what it can carry."""

    def test_cp437_keeps_its_box_drawing(self):
        text = "┌─┐ café →"
        self.assertEqual(fold_text(text, encoding="cp437"), "┌─┐ café >")

    def test_latin1_keeps_accents_and_folds_the_frame(self):
        self.assertEqual(fold_text("│ café °", encoding="latin-1"), "| café °")

    def test_unknown_encoding_falls_back_to_ascii(self):
        self.assertEqual(fold_text("│ café", encoding="no-such-codec"), "| cafe")

    def test_utf8_needs_no_folding(self):
        text = "┌─┐ 🙂"
        self.assertIs(fold_text(text, encoding="utf-8"), text)
        self.assertIs(fold_text(text, encoding="UTF8"), text)
