"""
Test eveditor

"""

import re
import unittest
from dataclasses import dataclass
from unittest.mock import Mock

from evennia.actions.action import Action
from evennia.actions.actor import Actor
from evennia.actions.context import ActionContext
from evennia.actions.engine import RuleEngine
from evennia.actions.menus import MenuInputAction
from evennia.actions.result import PASS
from evennia.commands.default.tests import BaseEvenniaCommandTest
from evennia.server import inputfuncs
from evennia.utils import ansi, eveditor
from evennia.utils.eveditor import EvEditor, EvEditorState

_ENGINE = RuleEngine()

# Mirror test_resources.call: strip EvMenu border decorations before comparing.
_RE_STRIP_EVMENU = re.compile(r"^\+|-+\+|\+-+|--+|\|(?:\s|$)", re.MULTILINE)


def _sync(d):
    """Extract an already-fired Deferred/coroutine's result, re-raising on failure."""
    import inspect

    from twisted.internet.defer import ensureDeferred

    if inspect.iscoroutine(d):
        # dispatch/engine are now `async def`; drive the (synchronous) coroutine
        # to an already-fired Deferred.
        d = ensureDeferred(d)
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
    """Drive the editor's ``:``-command dispatch through ``handle_input``.

    The editor no longer routes through the command dispatcher, so these tests
    feed raw lines to :meth:`EvEditor.handle_input` (as :class:`EvEditorState`
    does in production) and assert on the messages the caller receives, instead
    of constructing ``Command`` instances and using ``self.call``.
    """

    def _drive(self, raw, msg=None):
        """Feed a raw line to the live editor and assert on caller output.

        Mirrors ``test_resources.call``'s comparison: every ``caller.msg`` call
        made while the line runs is collected, joined with ``|``, ANSI-stripped
        and EvMenu-border-stripped, then checked with ``startswith(msg)``.

        Args:
            raw (str): the raw input line to feed to ``handle_input``.
            msg (str, optional): expected start of the joined caller output. If
                ``None``, no assertion is made.

        Returns:
            str: the joined, stripped caller output.
        """
        editor = self.char1.ndb._eveditor
        unmocked = self.char1.msg
        self.char1.msg = Mock()
        try:
            editor.handle_input(raw)
            stored = [
                args[0] if args and args[0] else kwargs.get("text", "")
                for _name, args, kwargs in self.char1.msg.mock_calls
            ]
        finally:
            self.char1.msg = unmocked
        stored = [str(smsg[0]) if isinstance(smsg, tuple) else str(smsg) for smsg in stored]
        returned = "|".join(
            _RE_STRIP_EVMENU.sub("", ansi.parse_ansi(mess, strip_ansi=True)) for mess in stored
        ).strip()
        if msg is not None and not returned.startswith(msg):
            raise AssertionError(f"Expected:\n{msg!r}\nGot:\n{returned!r}")
        return returned

    def test_eveditor_prefix_and_case_resolution(self):
        """A ``:``-command and its prefix both resolve through the matcher.

        Guards the longest-name-wins tiebreak (``:wq`` over ``:w``) and the
        case recovery that keeps ``:uu`` (redo) distinct from ``:UU`` (revert).
        """
        saved = []
        quit_called = []
        eveditor.EvEditor(
            self.char1,
            savefunc=lambda caller, buf: saved.append(buf) or True,
            quitfunc=lambda caller: quit_called.append(True),
        )
        self._drive("alpha")

        # ':w' saves without quitting; ':wq' (longer) saves and quits.
        self._drive(":w")
        self.assertEqual(len(saved), 1)
        self.assertEqual(quit_called, [])
        self._drive("beta")
        self._drive(":wq")
        self.assertEqual(len(saved), 2)
        self.assertEqual(quit_called, [True])

    def test_eveditor_uu_vs_UU_case_recovery(self):
        """``:uu`` redoes, ``:UU`` reverts: case recovery must distinguish them."""
        eveditor.EvEditor(self.char1)
        self._drive("first")
        self._drive("second")
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "first\nsecond")
        # ':UU' reverts everything to the (empty) pristine buffer.
        self._drive(":UU", msg="Reverted all changes to the buffer back to original state.")
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "")

    def test_eveditor_ranges(self):
        eveditor.EvEditor(self.char1)
        self._drive(":", msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)")
        self._drive("line 1", msg="01line 1")
        self._drive("line 2", msg="02line 2")
        self._drive("line 3", msg="03line 3")
        self._drive("line 4", msg="04line 4")
        self._drive("line 5", msg="05line 5")
        self._drive(
            ":",  # list whole buffer
            msg="Line Editor []\n01line 1\n02line 2\n"
            "03line 3\n04line 4\n05line 5\n"
            "[l:05 w:010 c:0034](:h for help)",
        )
        self._drive(
            ": :",  # list empty range
            msg="Line Editor []\n01line 1\n02line 2\n"
            "03line 3\n04line 4\n05line 5\n"
            "[l:05 w:010 c:0034](:h for help)",
        )
        self._drive(
            ": :4",  # list from start to line 4
            msg="Line Editor []\n01line 1\n02line 2\n"
            "03line 3\n04line 4\n"
            "[l:04 w:008 c:0027](:h for help)",
        )
        self._drive(
            ": 2:",  # list from line 2 to end
            msg="Line Editor []\n02line 2\n03line 3\n"
            "04line 4\n05line 5\n"
            "[l:04 w:008 c:0027](:h for help)",
        )
        self._drive(
            ": -10:10",  # try to list invalid range (too large)
            msg="Line Editor []\n01line 1\n02line 2\n"
            "03line 3\n04line 4\n05line 5\n"
            "[l:05 w:010 c:0034](:h for help)",
        )
        self._drive(
            ": 3:1",  # try to list invalid range (reversed)
            msg="Line Editor []\n03line 3\n" "[l:01 w:002 c:0006](:h for help)",
        )

    def test_eveditor_view_cmd(self):
        eveditor.EvEditor(self.char1)
        self._drive(
            ":h",
            msg="<txt>  - any non-command is appended to the end of the buffer.",
        )
        # empty buffer
        self._drive(":", msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)")
        # input a string
        self._drive("First test line", msg="01First test line")
        self._drive("Second test line", msg="02Second test line")
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "First test line\nSecond test line")

        self._drive(
            ":",  # view buffer
            msg="Line Editor []\n01First test line\n"
            "02Second test line\n[l:02 w:006 c:0032](:h for help)",
        )
        self._drive(
            "::",  # view buffer, no linenums
            msg="Line Editor []\nFirst test line\n"
            "Second test line\n[l:02 w:006 c:0032](:h for help)",
        )
        self._drive(":::", msg="Single ':' added to buffer.")  # add single : alone on row
        self._drive(
            ":",
            msg="Line Editor []\n01First test line\n"
            "02Second test line\n03:\n[l:03 w:007 c:0034](:h for help)",
        )

        self._drive(":dd", msg="Deleted line 3.")  # delete line
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "First test line\nSecond test line")
        self._drive(":u", msg="Undid one step.")  # undo
        self.assertEqual(
            self.char1.ndb._eveditor.get_buffer(), "First test line\nSecond test line\n:"
        )
        self._drive(":uu", msg="Redid one step.")  # redo
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "First test line\nSecond test line")
        self._drive(":u", msg="Undid one step.")  # undo
        self.assertEqual(
            self.char1.ndb._eveditor.get_buffer(), "First test line\nSecond test line\n:"
        )
        self._drive(
            ":",
            msg="Line Editor []\n01First test line\n"
            "02Second test line\n03:\n[l:03 w:007 c:0034](:h for help)",
        )

        self._drive(":dw Second", msg="Removed Second for lines 1-4.")  # delete by word
        self._drive(":u", msg="Undid one step.")  # undo
        self._drive(":dw 2 Second", msg="Removed Second for line 2.")  # delete by word/line
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "First test line\n test line\n:")

        self._drive(":p 2", msg="Copy buffer is empty.")  # paste
        self._drive(":y 2", msg="Line 2, [' test line'] yanked.")  # yank
        self._drive(":p 2", msg="Pasted buffer [' test line'] to line 2.")  # paste
        self.assertEqual(
            self.char1.ndb._eveditor.get_buffer(), "First test line\n test line\n test line\n:"
        )

        self._drive(":x 3", msg="Line 3, [' test line'] cut.")  # cut

        self._drive(":i 2 New Second line", msg="Inserted 1 new line(s) at line 2.")  # insert
        self._drive(":r 2 New Replaced Second line", msg="Replaced 1 line(s) at line 2.")  # replace
        self._drive(
            ":I 2 Inserted-", msg="Inserted text at beginning of line 2."  # insert beginning line
        )
        self._drive(":A 2 -End", msg="Appended text to end of line 2.")  # append end line

        self.assertEqual(
            self.char1.ndb._eveditor.get_buffer(),
            "First test line\nInserted-New Replaced Second line-End\n test line\n:",
        )

        self._drive(
            "  Whitespace   echo    test     line.",
            msg="05  Whitespace   echo    test     line.",
        )

    def test_eveditor_COLON_UU(self):
        eveditor.EvEditor(self.char1)
        self._drive(":", msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)")
        self._drive('First test "line".', msg='01First test "line".')
        self._drive("Second 'line'.", msg="02Second 'line'.")
        self.assertEqual(
            self.char1.ndb._eveditor.get_buffer(), "First test \"line\".\nSecond 'line'."
        )
        self._drive(":UU", msg="Reverted all changes to the buffer back to original state.")
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "")

    def test_eveditor_search_and_replace(self):
        eveditor.EvEditor(self.char1)
        self._drive(":", msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)")
        self._drive("line 1.", msg="01line 1.")
        self._drive("line 2.", msg="02line 2.")
        self._drive("line 3.", msg="03line 3.")
        self._drive(
            ": 2:3",
            msg="Line Editor []\n02line 2.\n03line 3.\n[l:02 w:004 c:0015](:h for help)",
        )
        self._drive(":s 1:2 line LINE", msg="Search-replaced line -> LINE for lines 1-2.")
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "LINE 1.\nLINE 2.\nline 3.")
        self._drive(":s line MINE", msg="Search-replaced line -> MINE for lines 1-3.")
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "LINE 1.\nLINE 2.\nMINE 3.")

    def test_eveditor_COLON_DD(self):
        eveditor.EvEditor(self.char1)
        self._drive(":", msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)")
        self._drive("line 1.", msg="01line 1.")
        self._drive("line 2.", msg="02line 2.")
        self._drive("line 3.", msg="03line 3.")
        self._drive(":DD", msg="Cleared 3 lines from buffer.")
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "")

    def test_eveditor_COLON_F(self):
        eveditor.EvEditor(self.char1)
        self._drive(":", msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)")
        self._drive("line 1", msg="01line 1")
        self._drive(":f 1:2", msg="Flood filled line 1.")
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "line 1")

    def test_eveditor_COLON_J(self):
        eveditor.EvEditor(self.char1)
        self._drive(":", msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)")
        self._drive("line 1", msg="01line 1")
        self._drive("l 2", msg="02l 2")
        self._drive("l 3", msg="03l 3")
        self._drive("l 4", msg="04l 4")
        self._drive(":j 2 r", msg="Right-justified line 2.")
        self._drive(":j 3 c", msg="Center-justified line 3.")
        self._drive(":j 4 f", msg="Full-justified line 4.")
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
        self._drive(":", msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)")
        text = (
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit.\n\n"
            "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua."
        )
        self.char1.ndb._eveditor.update_buffer(text)
        self._drive(":j =40", msg="Left-justified lines 1-3.")
        lines = self.char1.ndb._eveditor.get_buffer().split("\n")
        blank_line_index = lines.index(" " * 40)
        self.assertEqual(blank_line_index, 2)
        self.assertTrue(lines[blank_line_index + 1].startswith("Sed do eiusmod"))

    def test_eveditor_bad_commands(self):
        eveditor.EvEditor(self.char1)
        self._drive(":", msg="Line Editor []\n01\n[l:01 w:000 c:0000](:h for help)")
        self._drive("line 1.", msg="01line 1.")
        self._drive(":dw", msg="You must give a search word to delete.")
        self._drive(":s", msg="You must give a search word and something to replace it with.")
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "line 1.")

    def _drive_raw(self, raw):
        """Feed a line and return the caller output *without* stripping markup.

        Used to assert on clickable-command markup that ``_drive`` would strip.
        """
        editor = self.char1.ndb._eveditor
        unmocked = self.char1.msg
        self.char1.msg = Mock()
        try:
            editor.handle_input(raw)
            stored = [
                args[0] if args and args[0] else kwargs.get("text", "")
                for _name, args, kwargs in self.char1.msg.mock_calls
            ]
        finally:
            self.char1.msg = unmocked
        return "\n".join(str(smsg[0]) if isinstance(smsg, tuple) else str(smsg) for smsg in stored)

    def test_eveditor_rich_view_has_clickable_controls(self):
        """The default buffer view carries clickable-command markup that
        degrades to plain text in raw telnet (verified by the parity tests)."""
        eveditor.EvEditor(self.char1)
        out = self._drive_raw(":")
        # the :h hint and the toolbar controls are clickable commands
        self.assertIn("|lc:h|lt", out)
        self.assertIn("|lc:w|lt", out)
        self.assertIn("|lc:wq|lt", out)
        self.assertIn("|lc:paste|lt", out)

    def test_eveditor_raw_view_omits_clickable_markup(self):
        """``::`` is the copy-paste-friendly view: no clickable markup, which
        would otherwise show as literal codes in a raw-mode client."""
        eveditor.EvEditor(self.char1)
        self._drive("a line")
        out = self._drive_raw("::")
        self.assertNotIn("|lc", out)


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

    def test_paste_mode_appends_verbatim_until_endpaste(self):
        # In paste mode even a ``:``-prefixed line is buffer content, and only a
        # bare ``:endpaste`` leaves the mode.
        EvEditor(self.char1)
        actor, state = self._actor_and_state()
        _dispatch_line(actor, state, ":paste")
        self.assertTrue(state._paste_mode)
        _dispatch_line(actor, state, ":dd")  # would normally delete a line
        _dispatch_line(actor, state, "real content")
        _dispatch_line(actor, state, ":endpaste")
        self.assertFalse(state._paste_mode)
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), ":dd\nreal content")

    def test_paste_mode_applies_as_single_undo_snapshot(self):
        # A multi-line paste is applied in one buffer update, so it is one undo
        # step rather than one per line.
        EvEditor(self.char1)
        actor, state = self._actor_and_state()
        _dispatch_line(actor, state, ":paste")
        _dispatch_line(actor, state, "line one")
        _dispatch_line(actor, state, "line two")
        _dispatch_line(actor, state, "line three")
        _dispatch_line(actor, state, ":endpaste")
        editor = self.char1.ndb._eveditor
        self.assertEqual(editor.get_buffer(), "line one\nline two\nline three")
        self.assertEqual(editor._undo_buffer, ["", "line one\nline two\nline three"])

    def test_line_captured_through_real_cmdhandler_bridge(self):
        # The strongest proof against the "verification trap": a line driven
        # through the real cmdhandler -> action-engine bridge (execute_cmd), not
        # a hand-built ActionContext, must still reach the editor.
        EvEditor(self.char1)
        self.char1.execute_cmd("bridged line", session=self.session)
        self.assertIn("bridged line", self.char1.ndb._eveditor.get_buffer())


class TestEvEditorWebFrontend(BaseEvenniaCommandTest):
    """The rich web frontend: capability routing + the editor OOB protocol.

    A session is web-routed only after its client announces support (the
    ``CLIENT_EDITOR`` protocol flag). The web frontend shares the same
    ``EditCore`` and savefunc as the line editor, so saving is identical.
    """

    def _mark_web(self):
        """Flag every session puppeting char1 as editor-capable."""
        for sess in self.char1.sessions.all():
            sess.protocol_flags["CLIENT_EDITOR"] = True

    def test_non_web_session_uses_line_editor(self):
        editor = EvEditor(self.char1)
        self.assertEqual(editor._frontend, "line")
        actor = Actor(character=self.char1, session=self.session)
        self.assertTrue(actor.has_state(EvEditorState))

    def test_web_session_routes_to_web_frontend(self):
        self._mark_web()
        editor = EvEditor(self.char1)
        self.assertEqual(editor._frontend, "web")
        self.assertTrue(editor._session_id)
        # the web frontend is modal client-side: no line capture is installed
        actor = Actor(character=self.char1, session=self.session)
        self.assertFalse(actor.has_state(EvEditorState))

    def test_editor_save_inputfunc_saves_via_savefunc(self):
        self._mark_web()
        saved = []
        editor = EvEditor(self.char1, savefunc=lambda c, b: saved.append(b) or True)
        inputfuncs.editor_save(self.session, session_id=editor._session_id, content="hello web")
        self.assertEqual(saved, ["hello web"])
        self.assertEqual(self.char1.ndb._eveditor.get_buffer(), "hello web")

    def test_editor_save_wrong_session_id_is_ignored(self):
        self._mark_web()
        saved = []
        EvEditor(self.char1, savefunc=lambda c, b: saved.append(b) or True)
        inputfuncs.editor_save(self.session, session_id="bogus", content="nope")
        self.assertEqual(saved, [])

    def test_editor_save_with_close_quits(self):
        self._mark_web()
        quit_called = []
        editor = EvEditor(
            self.char1,
            savefunc=lambda c, b: True,
            quitfunc=lambda c: quit_called.append(True),
        )
        inputfuncs.editor_save(self.session, session_id=editor._session_id, content="x", close=True)
        self.assertEqual(quit_called, [True])
        self.assertIsNone(self.char1.ndb._eveditor)

    def test_editor_client_reopens_live_web_editor(self):
        # A reconnecting client re-announces support; a live web editor must be
        # re-sent (editor_open) and retargeted at the new session.
        self._mark_web()
        editor = EvEditor(self.char1)
        self.assertEqual(editor._frontend, "web")

        unmocked = self.char1.msg
        self.char1.msg = Mock()
        try:
            inputfuncs.editor_client(self.session, supported=True)
            reopened = any(
                "editor_open" in kwargs for _name, _args, kwargs in self.char1.msg.mock_calls
            )
        finally:
            self.char1.msg = unmocked
        self.assertTrue(reopened)
        self.assertIs(editor._web_session, self.session)

    def test_editor_client_ignores_when_no_editor(self):
        # No live editor: the handshake just records support, no crash.
        inputfuncs.editor_client(self.session, supported=True)
        self.assertTrue(self.session.protocol_flags.get("CLIENT_EDITOR"))

    def test_editor_cancel_inputfunc_quits_without_saving(self):
        self._mark_web()
        saved = []
        quit_called = []
        editor = EvEditor(
            self.char1,
            savefunc=lambda c, b: saved.append(b) or True,
            quitfunc=lambda c: quit_called.append(True),
        )
        inputfuncs.editor_cancel(self.session, session_id=editor._session_id)
        self.assertEqual(saved, [])
        self.assertEqual(quit_called, [True])
        self.assertIsNone(self.char1.ndb._eveditor)

    def _oob(self, mock, name):
        """Collect the payloads of OOB sends named ``name`` on a mocked msg."""
        return [kwargs[name][0] for _n, _a, kwargs in mock.mock_calls if name in kwargs]

    def test_web_save_sends_saved_status(self):
        self._mark_web()
        editor = EvEditor(self.char1, savefunc=lambda c, b: True)
        self.char1.msg = Mock()
        inputfuncs.editor_save(self.session, session_id=editor._session_id, content="hi")
        self.assertIn(["saved"], self._oob(self.char1.msg, "editor_status"))

    def test_web_cancel_sends_editor_close(self):
        self._mark_web()
        editor = EvEditor(self.char1, savefunc=lambda c, b: True, quitfunc=lambda c: None)
        self.char1.msg = Mock()
        inputfuncs.editor_cancel(self.session, session_id=editor._session_id)
        self.assertTrue(self._oob(self.char1.msg, "editor_close"))

    def test_web_cancel_keeps_unsaved_buffer_without_discard(self):
        self._mark_web()
        quit_called = []
        editor = EvEditor(
            self.char1,
            savefunc=lambda c, b: True,
            quitfunc=lambda c: quit_called.append(True),
        )
        editor.update_buffer("dirty")  # now unsaved
        self.char1.msg = Mock()
        inputfuncs.editor_cancel(self.session, session_id=editor._session_id)
        # unsaved work is not torn down without an explicit discard
        self.assertIsNotNone(self.char1.ndb._eveditor)
        self.assertEqual(quit_called, [])
        self.assertIn(["unsaved"], self._oob(self.char1.msg, "editor_status"))

    def test_web_cancel_discard_quits_unsaved_buffer(self):
        self._mark_web()
        quit_called = []
        editor = EvEditor(
            self.char1,
            savefunc=lambda c, b: True,
            quitfunc=lambda c: quit_called.append(True),
        )
        editor.update_buffer("dirty")
        inputfuncs.editor_cancel(self.session, session_id=editor._session_id, discard=True)
        self.assertEqual(quit_called, [True])
        self.assertIsNone(self.char1.ndb._eveditor)

    def test_web_save_rejects_missing_session_id(self):
        self._mark_web()
        saved = []
        editor = EvEditor(self.char1, savefunc=lambda c, b: saved.append(b) or True)
        # an omitted id (None) must not pass the handshake
        self.assertFalse(editor.web_save("nope", session_id=None))
        self.assertEqual(saved, [])

    def test_reopen_web_rotates_session_id_rejecting_stale_panel(self):
        self._mark_web()
        editor = EvEditor(self.char1)
        old_id = editor._session_id
        editor.reopen_web(self.session)
        self.assertNotEqual(editor._session_id, old_id)
        # a save carrying the pre-reopen id is now rejected
        self.assertFalse(editor.web_save("x", session_id=old_id))

    def test_editor_save_no_editor_dismisses_client_panel(self):
        # no live editor: reconcile the drifted client rather than staying silent
        self.session.msg = Mock()
        inputfuncs.editor_save(self.session, session_id="whatever", content="x")
        self.assertTrue(self._oob(self.session.msg, "editor_close"))

    def test_reopen_web_noop_on_line_editor(self):
        # reopen_web is a web-only re-announce; on a line editor it must no-op.
        editor = EvEditor(self.char1)
        self.assertEqual(editor._frontend, "line")
        self.char1.msg = Mock()
        editor.reopen_web(self.session)
        self.assertEqual(editor._frontend, "line")
        self.assertFalse(self._oob(self.char1.msg, "editor_open"))


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

    def test_persistent_web_editor_rehydrated_and_buffer_restored(self):
        # A persistent editor on a web-capable session must record its
        # rehydration substrate (despite the web frontend's early return) and,
        # after a reload, restore the in-progress buffer and re-push the panel
        # with the restored content, not the stale last-saved buffer.
        for sess in self.char1.sessions.all():
            sess.protocol_flags["CLIENT_EDITOR"] = True
        editor = EvEditor(self.char1, savefunc=_persistent_savefunc, persistent=True)
        self.assertEqual(editor._frontend, "web")
        editor.update_buffer("web draft")
        # the persistence substrate is present even though the frontend is web
        self.assertIsNotNone(self.char1.attributes.get("_eveditor_saved"))
        buf, _undo = self.char1.attributes.get("_eveditor_buffer_temp")
        self.assertEqual(buf, "web draft")

        # simulate a reload: live editor gone, persisted Attributes remain
        self.char1.ndb._eveditor = None
        self.char1.ndb.active_states = None
        self.char1.msg = Mock()
        self.char1.at_post_load()

        restored = self.char1.ndb._eveditor
        self.assertIsNotNone(restored)
        self.assertEqual(restored._frontend, "web")
        self.assertEqual(restored.get_buffer(), "web draft")
        # the panel is re-pushed with the restored buffer (last editor_open)
        opens = [
            kwargs["editor_open"][0]
            for _n, _a, kwargs in self.char1.msg.mock_calls
            if "editor_open" in kwargs
        ]
        self.assertTrue(opens)
        self.assertEqual(opens[-1][1], "web draft")
