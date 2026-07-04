"""Unit tests for the protocol-agnostic editor core (:class:`EditCore`).

These exercise the buffer model in isolation, with no session, no caller and no
database. They pin the semantics the telnet/Mudlet/webclient frontends all rely
on: buffer mutation, undo/redo history, the copy buffer and code-mode
auto-indentation.
"""

import unittest

from evennia.utils.editor.core import (REDO_NONE, REDO_OK, UNDO_NONE, UNDO_OK,
                                       EditCore)


class TestEditCoreBuffer(unittest.TestCase):
    def test_initial_state(self):
        core = EditCore(buffer="hello")
        self.assertEqual(core.get_buffer(), "hello")
        self.assertEqual(core.pristine, "hello")
        self.assertFalse(core.unsaved)
        self.assertEqual(core.undo_buffer, ["hello"])
        self.assertEqual(core.undo_pos, 0)

    def test_set_buffer_marks_changed_and_unsaved(self):
        core = EditCore()
        self.assertTrue(core.set_buffer("line 1"))
        self.assertEqual(core.get_buffer(), "line 1")
        self.assertTrue(core.unsaved)

    def test_set_buffer_no_change_returns_false(self):
        core = EditCore(buffer="same")
        self.assertFalse(core.set_buffer("same"))
        self.assertFalse(core.unsaved)

    def test_set_buffer_accepts_iterable_of_lines(self):
        core = EditCore()
        core.set_buffer(["a", "b", "c"])
        self.assertEqual(core.get_buffer(), "a\nb\nc")

    def test_pristine_is_not_moved_by_edits(self):
        core = EditCore(buffer="orig")
        core.set_buffer("changed")
        self.assertEqual(core.pristine, "orig")


class TestEditCoreUndo(unittest.TestCase):
    def test_undo_and_redo_roundtrip(self):
        core = EditCore()
        core.set_buffer("first")
        core.set_buffer("first\nsecond")

        self.assertEqual(core.navigate_undo(-1), UNDO_OK)
        self.assertEqual(core.get_buffer(), "first")
        self.assertEqual(core.navigate_undo(1), REDO_OK)
        self.assertEqual(core.get_buffer(), "first\nsecond")

    def test_undo_past_start_reports_none(self):
        core = EditCore()
        core.set_buffer("only")
        core.navigate_undo(-1)  # back to ""
        self.assertEqual(core.navigate_undo(-1), UNDO_NONE)

    def test_redo_past_end_reports_none(self):
        core = EditCore()
        core.set_buffer("only")
        self.assertEqual(core.navigate_undo(1), REDO_NONE)

    def test_edit_after_undo_discards_redo_branch(self):
        core = EditCore()
        core.set_buffer("a")
        core.set_buffer("a\nb")
        core.navigate_undo(-1)  # back to "a"
        core.set_buffer("a\nc")  # new branch
        # the redo branch ("a\nb") is gone
        self.assertEqual(core.navigate_undo(1), REDO_NONE)
        self.assertEqual(core.get_buffer(), "a\nc")

    def test_redo_capped_by_undo_max(self):
        core = EditCore(undo_max=3)
        for n in range(6):
            core.set_buffer(f"v{n}")
        # walk all the way back, then redo forward: never exceed the cap
        while core.navigate_undo(-1) == UNDO_OK:
            pass
        redos = 0
        while core.navigate_undo(1) == REDO_OK:
            redos += 1
        self.assertLessEqual(redos, core.undo_max - 1)


class TestEditCoreIndent(unittest.TestCase):
    def test_indent_ops_noop_outside_code_mode(self):
        core = EditCore(code_mode=False)
        self.assertFalse(core.increase_indent())
        self.assertFalse(core.decrease_indent())
        self.assertFalse(core.swap_autoindent())
        self.assertEqual(core.indent, 0)

    def test_increase_and_decrease_indent(self):
        core = EditCore(code_mode=True)
        self.assertTrue(core.increase_indent())
        self.assertEqual(core.indent, 1)
        self.assertTrue(core.decrease_indent())
        self.assertEqual(core.indent, 0)
        # cannot decrease below zero
        self.assertFalse(core.decrease_indent())

    def test_swap_autoindent_toggles(self):
        core = EditCore(code_mode=True)
        self.assertTrue(core.swap_autoindent())
        self.assertEqual(core.indent, -1)  # off
        self.assertTrue(core.swap_autoindent())
        self.assertEqual(core.indent, 0)  # on again

    def test_deduce_indent_bumps_after_opening_tag(self):
        core = EditCore(code_mode=True)
        line, changed = core.deduce_indent("if x:", "")
        self.assertTrue(changed)
        self.assertEqual(core.indent, 1)
        self.assertEqual(line, "if x:")  # the opening line itself is not indented


class TestEditCoreCopyBuffer(unittest.TestCase):
    def test_copy_buffer_defaults_empty_and_is_settable(self):
        core = EditCore()
        self.assertEqual(core.copy_buffer, [])
        core.copy_buffer = ["yanked line"]
        self.assertEqual(core.copy_buffer, ["yanked line"])


if __name__ == "__main__":
    unittest.main()
