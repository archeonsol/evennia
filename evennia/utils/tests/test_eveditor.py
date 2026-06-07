"""
Test eveditor

"""

import unittest
from dataclasses import dataclass

from evennia.actions.action import Action
from evennia.actions.actor import Actor
from evennia.actions.context import ActionContext
from evennia.actions.engine import RuleEngine
from evennia.actions.menus import MenuInputAction
from evennia.actions.result import PASS
from evennia.commands.default.tests import BaseEvenniaCommandTest
from evennia.utils import eveditor
from evennia.utils.eveditor import EvEditor, EvEditorState

_ENGINE = RuleEngine()


def _sync(d):
    """Extract an already-fired Deferred's result, re-raising on failure."""
    out = {}
    d.addCallbacks(lambda r: out.__setitem__("result", r), lambda f: out.__setitem__("fail", f))
    if "fail" in out:
        out["fail"].raiseException()
    if "result" not in out:
        raise AssertionError("dispatch Deferred did not fire synchronously")
    return out["result"]


@dataclass
class _Line(Action):
    """A neutral action carrying a raw input line (mimics the bridge carrier)."""


def _line(raw):
    action = _Line()
    action._raw_string = raw
    return action


def _dispatch_line(actor, state, raw):
    """Dispatch a raw line through the engine while ``state`` is active."""
    ctx = ActionContext(providers=[state])
    return _sync(_ENGINE.dispatch(_line(raw), actor, ctx))


class TestEvEditor(BaseEvenniaCommandTest):
    def test_eveditor_ranges(self):
        eveditor.EvEditor(self.char1)
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":",
            msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)",
        )
        self.call(eveditor.CmdLineInput(), "line 1", raw_string="line 1", msg="01line 1")
        self.call(eveditor.CmdLineInput(), "line 2", raw_string="line 2", msg="02line 2")
        self.call(eveditor.CmdLineInput(), "line 3", raw_string="line 3", msg="03line 3")
        self.call(eveditor.CmdLineInput(), "line 4", raw_string="line 4", msg="04line 4")
        self.call(eveditor.CmdLineInput(), "line 5", raw_string="line 5", msg="05line 5")
        self.call(
            eveditor.CmdEditorGroup(),
            "",  # list whole buffer
            raw_string=":",
            msg="Line Editor []\n01line 1\n02line 2\n"
            "03line 3\n04line 4\n05line 5\n"
            "[l:05 w:010 c:0034](:h for help)",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            ":",  # list empty range
            raw_string=":",
            msg="Line Editor []\n01line 1\n02line 2\n"
            "03line 3\n04line 4\n05line 5\n"
            "[l:05 w:010 c:0034](:h for help)",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            ":4",  # list from start to line 4
            raw_string=":",
            msg="Line Editor []\n01line 1\n02line 2\n"
            "03line 3\n04line 4\n"
            "[l:04 w:008 c:0027](:h for help)",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "2:",  # list from line 2 to end
            raw_string=":",
            msg="Line Editor []\n02line 2\n03line 3\n"
            "04line 4\n05line 5\n"
            "[l:04 w:008 c:0027](:h for help)",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "-10:10",  # try to list invalid range (too large)
            raw_string=":",
            msg="Line Editor []\n01line 1\n02line 2\n"
            "03line 3\n04line 4\n05line 5\n"
            "[l:05 w:010 c:0034](:h for help)",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "3:1",  # try to list invalid range (reversed)
            raw_string=":",
            msg="Line Editor []\n03line 3\n" "[l:01 w:002 c:0006](:h for help)",
        )

    def test_eveditor_view_cmd(self):
        eveditor.EvEditor(self.char1)
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":h",
            msg="<txt>  - any non-command is appended to the end of the buffer.",
        )
        # empty buffer
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":",
            msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)",
        )
        # input a string
        self.call(
            eveditor.CmdLineInput(),
            "First test line",
            raw_string="First test line",
            msg="01First test line",
        )
        self.call(
            eveditor.CmdLineInput(),
            "Second test line",
            raw_string="Second test line",
            msg="02Second test line",
        )
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "First test line\nSecond test line")

        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":",  # view buffer
            msg="Line Editor []\n01First test line\n"
            "02Second test line\n[l:02 w:006 c:0032](:h for help)",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string="::",  # view buffer, no linenums
            msg="Line Editor []\nFirst test line\n"
            "Second test line\n[l:02 w:006 c:0032](:h for help)",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":::",  # add single : alone on row
            msg="Single ':' added to buffer.",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":",
            msg="Line Editor []\n01First test line\n"
            "02Second test line\n03:\n[l:03 w:007 c:0034](:h for help)",
        )

        self.call(
            eveditor.CmdEditorGroup(), "", raw_string=":dd", msg="Deleted line 3."  # delete line
        )
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "First test line\nSecond test line")
        self.call(eveditor.CmdEditorGroup(), "", raw_string=":u", msg="Undid one step.")  # undo
        self.assertEqual(
            self.char1.ndb._eveditor.get_buffer(), "First test line\nSecond test line\n:"
        )
        self.call(eveditor.CmdEditorGroup(), "", raw_string=":uu", msg="Redid one step.")  # redo
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "First test line\nSecond test line")
        self.call(eveditor.CmdEditorGroup(), "", raw_string=":u", msg="Undid one step.")  # undo
        self.assertEqual(
            self.char1.ndb._eveditor.get_buffer(), "First test line\nSecond test line\n:"
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":",
            msg="Line Editor []\n01First test line\n"
            "02Second test line\n03:\n[l:03 w:007 c:0034](:h for help)",
        )

        self.call(
            eveditor.CmdEditorGroup(),
            "Second",
            raw_string=":dw",  # delete by word
            msg="Removed Second for lines 1-4.",
        )
        self.call(eveditor.CmdEditorGroup(), "", raw_string=":u", msg="Undid one step.")  # undo
        self.call(
            eveditor.CmdEditorGroup(),
            "2 Second",
            raw_string=":dw",  # delete by word/line
            msg="Removed Second for line 2.",
        )
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "First test line\n test line\n:")

        self.call(
            eveditor.CmdEditorGroup(), "2", raw_string=":p", msg="Copy buffer is empty."  # paste
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "2",
            raw_string=":y",  # yank
            msg="Line 2, [' test line'] yanked.",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "2",
            raw_string=":p",  # paste
            msg="Pasted buffer [' test line'] to line 2.",
        )
        self.assertEqual(
            self.char1.ndb._eveditor.get_buffer(), "First test line\n test line\n test line\n:"
        )

        self.call(
            eveditor.CmdEditorGroup(),
            "3",
            raw_string=":x",
            msg="Line 3, [' test line'] cut.",  # cut
        )

        self.call(
            eveditor.CmdEditorGroup(),
            "2 New Second line",
            raw_string=":i",  # insert
            msg="Inserted 1 new line(s) at line 2.",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "2 New Replaced Second line",  # replace
            raw_string=":r",
            msg="Replaced 1 line(s) at line 2.",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "2 Inserted-",  # insert beginning line
            raw_string=":I",
            msg="Inserted text at beginning of line 2.",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "2 -End",  # append end line
            raw_string=":A",
            msg="Appended text to end of line 2.",
        )

        self.assertEqual(
            self.char1.ndb._eveditor.get_buffer(),
            "First test line\nInserted-New Replaced Second line-End\n test line\n:",
        )

        self.call(
            eveditor.CmdLineInput(),
            "  Whitespace   echo    test     line.",
            raw_string="  Whitespace   echo    test     line.",
            msg="05  Whitespace   echo    test     line.",
        )

    def test_eveditor_COLON_UU(self):
        eveditor.EvEditor(self.char1)
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":",
            msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)",
        )
        self.call(
            eveditor.CmdLineInput(),
            'First test "line".',
            raw_string='First test "line".',
            msg='01First test "line".',
        )
        self.call(
            eveditor.CmdLineInput(),
            "Second 'line'.",
            raw_string="Second 'line'.",
            msg="02Second 'line'.",
        )
        self.assertEqual(
            self.char1.ndb._eveditor.get_buffer(), "First test \"line\".\nSecond 'line'."
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":UU",
            msg="Reverted all changes to the buffer back to original state.",
        )
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "")

    def test_eveditor_search_and_replace(self):
        eveditor.EvEditor(self.char1)
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":",
            msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)",
        )
        self.call(eveditor.CmdLineInput(), "line 1.", raw_string="line 1.", msg="01line 1.")
        self.call(eveditor.CmdLineInput(), "line 2.", raw_string="line 2.", msg="02line 2.")
        self.call(eveditor.CmdLineInput(), "line 3.", raw_string="line 3.", msg="03line 3.")
        self.call(
            eveditor.CmdEditorGroup(),
            "2:3",
            raw_string=":",
            msg="Line Editor []\n02line 2.\n03line 3.\n[l:02 w:004 c:0015](:h for help)",
        )
        self.call(
            eveditor.CmdEditorGroup(),
            "1:2 line LINE",
            raw_string=":s",
            msg="Search-replaced line -> LINE for lines 1-2.",
        )
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "LINE 1.\nLINE 2.\nline 3.")
        self.call(
            eveditor.CmdEditorGroup(),
            "line MINE",
            raw_string=":s",
            msg="Search-replaced line -> MINE for lines 1-3.",
        )
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "LINE 1.\nLINE 2.\nMINE 3.")

    def test_eveditor_COLON_DD(self):
        eveditor.EvEditor(self.char1)
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":",
            msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)",
        )
        self.call(eveditor.CmdLineInput(), "line 1.", raw_string="line 1.", msg="01line 1.")
        self.call(eveditor.CmdLineInput(), "line 2.", raw_string="line 2.", msg="02line 2.")
        self.call(eveditor.CmdLineInput(), "line 3.", raw_string="line 3.", msg="03line 3.")
        self.call(
            eveditor.CmdEditorGroup(), "", raw_string=":DD", msg="Cleared 3 lines from buffer."
        )
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "")

    def test_eveditor_COLON_F(self):
        eveditor.EvEditor(self.char1)
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":",
            msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)",
        )
        self.call(eveditor.CmdLineInput(), "line 1", raw_string="line 1", msg="01line 1")
        self.call(eveditor.CmdEditorGroup(), "1:2", raw_string=":f", msg="Flood filled line 1.")
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "line 1")

    def test_eveditor_COLON_J(self):
        eveditor.EvEditor(self.char1)
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":",
            msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)",
        )
        self.call(eveditor.CmdLineInput(), "line 1", raw_string="line 1", msg="01line 1")
        self.call(eveditor.CmdLineInput(), "l 2", raw_string="l 2", msg="02l 2")
        self.call(eveditor.CmdLineInput(), "l 3", raw_string="l 3", msg="03l 3")
        self.call(eveditor.CmdLineInput(), "l 4", raw_string="l 4", msg="04l 4")
        self.call(eveditor.CmdEditorGroup(), "2 r", raw_string=":j", msg="Right-justified line 2.")
        self.call(eveditor.CmdEditorGroup(), "3 c", raw_string=":j", msg="Center-justified line 3.")
        self.call(eveditor.CmdEditorGroup(), "4 f", raw_string=":j", msg="Full-justified line 4.")
        l1, l2, l3, l4 = tuple(self.char1.ndb._eveditor.get_buffer().split("\n"))
        self.assertEqual(l1, "line 1")
        self.assertEqual(l2, " " * 75 + "l 2")
        self.assertEqual(l3, " " * 37 + "l 3" + " " * 38)
        self.assertEqual(l4, "l" + " " * 76 + "4")

    def test_eveditor_COLON_J_preserves_paragraph_breaks(self):
        """
        Test to verify fix of issue #3649 (:j command broke input)
        """
        eveditor.EvEditor(self.char1)
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":",
            msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)",
        )
        text = (
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit.\n\n"
            "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
        )
        self.char1.ndb._eveditor.update_buffer(text)
        self.call(
            eveditor.CmdEditorGroup(),
            "=40",
            raw_string=":j",
            msg="Left-justified lines 1-3.",
        )
        lines = self.char1.ndb._eveditor.get_buffer().split("\n")
        blank_line_index = lines.index(" " * 40)
        self.assertEqual(blank_line_index, 2)
        self.assertTrue(lines[blank_line_index + 1].startswith("Sed do eiusmod"))

    def test_eveditor_bad_commands(self):
        eveditor.EvEditor(self.char1)
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":",
            msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)",
        )
        self.call(eveditor.CmdLineInput(), "line 1.", raw_string="line 1.", msg="01line 1.")
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":dw",
            msg="You must give a search word to delete.",
        )
        # self.call(
        #     eveditor.CmdEditorGroup(),
        #     raw_string="",
        #     raw_string=":i",
        #     msg="You need to enter a new line and where to insert it.",
        # )
        # self.call(
        #     eveditor.CmdEditorGroup(),
        #     "",
        #     raw_string=":I",
        #     msg="You need to enter text to insert.",
        # )
        # self.call(
        #     eveditor.CmdEditorGroup(),
        #     "",
        #     raw_string=":r",
        #     msg="You need to enter a replacement string.",
        # )
        self.call(
            eveditor.CmdEditorGroup(),
            "",
            raw_string=":s",
            msg="You must give a search word and something to replace it with.",
        )
        # self.call(
        #     eveditor.CmdEditorGroup(),
        #     "",
        #     raw_string=":f",
        #     msg="Valid justifications are [f]ull (default), [c]enter, [r]right or [l]eft"
        # )
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "line 1.")


def _persistent_savefunc(caller, buf):
    """Module-level (picklable) savefunc for the persistent-editor reload test."""
    caller.db.desc = buf
    return True


class _FakeNDB:
    """Bare attribute bag standing in for Evennia's non-persistent handler."""


class _FakeCaller:
    """Minimal caller exposing the .ndb a state install needs."""

    def __init__(self):
        self.account = None
        self.ndb = _FakeNDB()


class _StubEditor:
    """Records routing decisions so EvEditorState can be tested in isolation."""

    def __init__(self, session=None):
        self._session = session
        self.inputs = []
        self.calls = []

    def handle_input(self, raw):
        self.inputs.append(raw)

    def save_buffer(self):
        self.calls.append("save")

    def quit(self):
        self.calls.append("quit")


class TestEvEditorStateRouting(unittest.TestCase):
    """EvEditorState routing, driven through the real engine (no cmdset)."""

    def setUp(self):
        self.session = object()  # sentinel shared by the editor and the actor
        self.caller = _FakeCaller()
        self.actor = Actor(character=self.caller, session=self.session)
        self.editor = _StubEditor(session=None)  # session-agnostic capture
        self.state = EvEditorState(self.editor)
        self.actor.enter_state(self.state)

    def test_line_is_routed_to_handle_input(self):
        _dispatch_line(self.actor, self.state, "some buffer line")
        self.assertEqual(self.editor.inputs, ["some buffer line"])

    def test_colon_command_is_routed_verbatim(self):
        # the state forwards the raw line; command resolution is handle_input's job
        _dispatch_line(self.actor, self.state, ":wq")
        self.assertEqual(self.editor.inputs, [":wq"])

    def test_menuinput_falls_through_capture(self):
        # capture_input must PASS a MenuInputAction (else infinite REDIRECT).
        result = self.state.capture_input(MenuInputAction(raw="x", menu=self.state), self.actor)
        self.assertIs(result, PASS)

    def test_save_confirm_yes_saves_and_quits(self):
        self.state._save_confirm = True
        _dispatch_line(self.actor, self.state, "")  # empty / anything but 'no' = yes
        self.assertEqual(self.editor.calls, ["save", "quit"])
        self.assertEqual(self.editor.inputs, [])  # not routed as editor input
        self.assertFalse(self.state._save_confirm)

    def test_save_confirm_no_quits_without_saving(self):
        self.state._save_confirm = True
        _dispatch_line(self.actor, self.state, "n")
        self.assertEqual(self.editor.calls, ["quit"])
        self.assertFalse(self.state._save_confirm)


class TestEvEditorStateSessionScope(unittest.TestCase):
    """A session-scoped editor would only capture its own session's input. The
    shipped editor is session-agnostic (output is broadcast), so it captures
    regardless of the dispatching session - assert that contract explicitly."""

    def setUp(self):
        self.caller = _FakeCaller()
        self.editor = _StubEditor(session=None)
        self.state = EvEditorState(self.editor)

    def test_any_session_is_captured(self):
        actor = Actor(character=self.caller, session=object())
        result = self.state.capture_input(_line("hi"), actor)
        self.assertIsNot(result, PASS)


class TestEvEditorEngineRouted(BaseEvenniaCommandTest):
    """End-to-end: a real EvEditor captures input dispatched through the engine.

    This is the test the cmdset-based editor failed: the engine bridge never
    merged the editor cmdset, so a line routed through dispatch never reached the
    editor. It must pass now that capture is an engine StateProvider.
    """

    def _actor_and_state(self):
        actor = Actor(character=self.char1, session=self.session)
        self.assertTrue(actor.has_state(EvEditorState))
        return actor, actor.state_objects[-1]

    def test_buffer_line_is_captured_through_engine(self):
        EvEditor(self.char1)
        actor, state = self._actor_and_state()
        _dispatch_line(actor, state, "hello buffer")
        self.assertIn("hello buffer", self.char1.ndb._eveditor.get_buffer())

    def test_colon_command_runs_through_engine(self):
        EvEditor(self.char1)
        actor, state = self._actor_and_state()
        _dispatch_line(actor, state, "first line")
        _dispatch_line(actor, state, ":DD")  # clear buffer
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "")

    def test_quit_removes_capture_state(self):
        EvEditor(self.char1)
        actor, _state = self._actor_and_state()
        self.char1.ndb._eveditor.quit()
        self.assertFalse(actor.has_state(EvEditorState))

    def test_line_captured_through_real_cmdhandler_bridge(self):
        # The strongest proof against the "verification trap": a line driven
        # through the real cmdhandler -> action-engine bridge (execute_cmd), not
        # a hand-built ActionContext, must still reach the editor.
        EvEditor(self.char1)
        self.char1.execute_cmd("bridged line", session=self.session)
        self.assertIn("bridged line", self.char1.ndb._eveditor.get_buffer())


class TestEvEditorReload(BaseEvenniaCommandTest):
    """A persistent editor survives a reload via the rehydrate_captures seam."""

    def test_persistent_editor_rehydrated_and_recaptures(self):
        EvEditor(self.char1, savefunc=_persistent_savefunc, persistent=True)
        _dispatch_line(*self._first(), "before reload")
        self.assertIn("before reload", self.char1.ndb._eveditor.get_buffer())

        # Simulate a reload: the non-persistent state and ndb editor are gone,
        # but the persisted Attributes remain.
        self.char1.ndb._eveditor = None
        self.char1.ndb.active_states = None
        actor = Actor(character=self.char1, session=self.session)
        self.assertFalse(actor.has_state(EvEditorState))

        # at_post_load fires on cache load after a reload; it drives the seam.
        self.char1.at_post_load()
        self.assertIsNotNone(self.char1.ndb._eveditor)
        actor = Actor(character=self.char1, session=self.session)
        self.assertTrue(actor.has_state(EvEditorState))

        # input is captured again after rehydration
        state = actor.state_objects[-1]
        _dispatch_line(actor, state, "after reload")
        self.assertIn("after reload", self.char1.ndb._eveditor.get_buffer())

    def _first(self):
        actor = Actor(character=self.char1, session=self.session)
        return actor, actor.state_objects[-1]
