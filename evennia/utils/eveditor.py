"""
EvEditor (Evennia Line Editor)

This implements an advanced line editor for editing longer texts in-game. The
editor mimics the command mechanisms of the "VI" editor (a famous line-by-line
editor) as far as reasonable.

Features of the editor:

- undo/redo.
- edit/replace on any line of the buffer.
- search&replace text anywhere in buffer.
- formatting of buffer, or selection, to certain width + indentations.
- allow to echo the input or not, depending on your client.
- in-built help

To use the editor, just import EvEditor from this module and initialize it:

```python
from evennia.utils.eveditor import EvEditor

# set up an editor to edit the caller's 'desc' Attribute
def _loadfunc(caller):
    return caller.db.desc

def _savefunc(caller, buffer):
    caller.db.desc = buffer.strip()
    return True

def _quitfunc(caller):
    caller.msg("Custom quit message")

# start the editor
EvEditor(caller, loadfunc=None, savefunc=None, quitfunc=None, key="",
         persistent=True, code=False)
```

The editor can also be used to format Python code and be made to
survive a reload. See the `EvEditor` class for more details.

"""

import re
from uuid import uuid4

from django.conf import settings
from django.utils.translation import gettext as _

from evennia.actions.action import Action
from evennia.actions.menus import MenuInputAction, session_mismatch
from evennia.actions.result import CLAIM, PASS, REDIRECT
from evennia.actions.rule import rule
from evennia.actions.state import StateProvider, capture_holder, enter_state, exit_state
from evennia.utils import dedent, fill, is_iter, justify, logger, to_str, utils
from evennia.utils.ansi import raw
from evennia.utils.editor.core import REDO_NONE, REDO_OK, UNDO_NONE, UNDO_OK, EditCore

_RE_GROUP = re.compile(r"\".*?\"|\'.*?\'|\S*")

# The editor's ``:``-command tokens, sorted longest-first so the matcher in
# :func:`_match_editor_token` returns the longest match (e.g. ``:wq`` over
# ``:w``) on first hit. Authored case is preserved (``:UU``, ``:DD``, ``:I``,
# ``:A``, ``:S``) so :meth:`CmdEditorGroup.func` can recover the true case from
# ``raw_string[:len(cmdstring)]``; matching itself is case-insensitive.
_EDITOR_TOKENS = tuple(
    sorted(
        [
            ":",
            "::",
            ":::",
            ":h",
            ":w",
            ":wq",
            ":q",
            ":q!",
            ":u",
            ":uu",
            ":UU",
            ":dd",
            ":dw",
            ":DD",
            ":y",
            ":x",
            ":p",
            ":i",
            ":j",
            ":r",
            ":I",
            ":A",
            ":s",
            ":S",
            ":f",
            ":fi",
            ":fd",
            ":echo",
            ":!",
            ":<",
            ":>",
            ":=",
            ":paste",
            ":endpaste",
        ],
        key=len,
        reverse=True,
    )
)


def _clickable(cmd, text):
    """Wrap ``text`` in Evennia's clickable-command markup.

    Renders as a clickable control that sends ``cmd`` in clients that support it
    (the webclient always, and MXP-capable clients such as Mudlet). In raw
    telnet the markup is stripped and only ``text`` remains, so it degrades to a
    plain visual hint. This is how the line editor stays capability-aware
    without branching on per-session protocol flags.

    Args:
        cmd (str): the editor command to send on click (e.g. ``":w"``).
        text (str): the visible label (e.g. ``"[Save]"``).

    Returns:
        str: ``|lc<cmd>|lt<text>|le`` markup.
    """
    return "|lc%s|lt%s|le" % (cmd, text)


def _match_editor_token(raw):
    """Match a raw input line against the editor's ``:``-command tokens.

    A token matches when the line starts with it (case-insensitively) and the
    next character is whitespace or end-of-string. Because :data:`_EDITOR_TOKENS`
    is sorted longest-first, the first hit is the longest match (``:wq`` wins
    over ``:w``). Matching is case-insensitive but the authored case is
    returned, so ``:uu`` (redo) and ``:UU`` (revert) stay distinguishable
    downstream.

    Args:
        raw (str): the raw input line the player typed.

    Returns:
        str or None: the matched token in its authored case (so the caller can
            slice ``raw`` to ``len(token)`` for case recovery), or ``None`` when
            no token matches.
    """
    search = raw.lower()
    for token in _EDITOR_TOKENS:
        ltoken = token.lower()
        if search.startswith(ltoken):
            rest = search[len(ltoken) :]
            if rest == "" or rest[0].isspace():
                return token
    return None


# -------------------------------------------------------------
#
# texts
#
# -------------------------------------------------------------

_HELP_TEXT = _(f"""
 <txt>  - any non-command is appended to the end of the buffer.
 :  <l> - view buffer or only line(s) <l>
 :: <l> - raw-view buffer or only line(s) <l>
 :::    - escape - enter ':' as the only character on the line.
 :h     - this help.

 :w     - save the buffer (don't quit)
 :wq    - save buffer and quit
 :q     - quit (will be asked to save if buffer was changed)
 :q!    - quit without saving, no questions asked

 :u     - (undo) step backwards in undo history
 :uu    - (redo) step forward in undo history
 :UU    - reset all changes back to initial state

 :dd <l>     - delete last line or line(s) <l>
 :dw <l> <w> - delete word or regex <w> in entire buffer or on line <l>
 :DD         - clear entire buffer

 :y  <l>        - yank (copy) line(s) <l> to the copy buffer
 :x  <l>        - cut line(s) <l> and store it in the copy buffer
 :p  <l>        - put (paste) previously copied line(s) directly before <l>
 :i  <l> <txt>  - insert new text <txt> at line <l>. Old line will move down
 :r  <l> <txt>  - replace line <l> with text <txt>
 :I  <l> <txt>  - insert text at the beginning of line <l>
 :A  <l> <txt>  - append text after the end of line <l>

 :paste    - enter multi-line paste mode: following lines (even ':'-lines)
             are appended verbatim until you enter ':endpaste'.

 :s <l> <w> <txt> - search/replace word or regex <w> in buffer or on line <l>

 :j <l> <a> = <w> - justify buffer or line <l>. <a> is f, c, l or r. <w> is
                    width. <a> and <w> are optional and default to l (left)
                    and {settings.CLIENT_DEFAULT_WIDTH} respectively
 :f <l> = <w>     - flood-fill entire buffer or line <l> to width <w>.
                    Equivalent to :j <l> l. <w> is optional, as for :j
 :fi <l>    - indent entire buffer or line <l>
 :fd <l>    - de-indent entire buffer or line <l>

 :echo - turn echoing of the input on/off (helpful for some clients)
""")

_HELP_LEGEND = _("""
    Legend:
    <l>   - line number, like '5' or range, like '3:7'.
    <w>   - a single word, or multiple words with quotes around them.
    <txt> - longer string, usually not needing quotes.
""")

_HELP_CODE = _(
    """
 :!    - Execute code buffer without saving
 :<    - Decrease the level of automatic indentation for the next lines
 :>    - Increase the level of automatic indentation for the next lines
 :=    - Switch automatic indentation on/off
""".lstrip("\n")
)

_ERROR_LOADFUNC = _("""
{error}

|rBuffer load function error. Could not load initial data.|n
""")

_ERROR_SAVEFUNC = _("""
{error}

|rSave function returned an error. Buffer not saved.|n
""")

_ERROR_NO_SAVEFUNC = _("|rNo save function defined. Buffer cannot be saved.|n")

_MSG_SAVE_NO_CHANGE = _("No changes need saving")
_DEFAULT_NO_QUITFUNC = _("Exited editor.")

_ERROR_QUITFUNC = _("""
{error}

|rQuit function gave an error. Skipping.|n
""")

_ERROR_PERSISTENT_SAVING = _("""
{error}

|rThe editor state could not be saved for persistent mode. Switching
to non-persistent mode (which means the editor session won't survive
an eventual server reload - so save often!)|n
""")

_TRACE_PERSISTENT_SAVING = _(
    "EvEditor persistent-mode error. Commonly, this is because one or "
    "more of the EvEditor callbacks could not be pickled, for example "
    "because it's a class method or is defined inside another function."
)


_MSG_NO_UNDO = _("Nothing to undo.")
_MSG_NO_REDO = _("Nothing to redo.")
_MSG_UNDO = _("Undid one step.")
_MSG_REDO = _("Redid one step.")

# -------------------------------------------------------------
#
# Engine-native input capture
#
# -------------------------------------------------------------


class EvEditorState(StateProvider):
    """Engine-native input capture for the EvEditor line editor.

    While an editor is active this state seizes every input line through the
    action engine and hands it to :meth:`EvEditor.handle_input`, which resolves
    the editor's ``:``-commands by pure string matching. The save-on-quit
    confirmation is handled here as an in-state sub-mode (the
    :attr:`_save_confirm` flag).

    Modeled on :class:`evennia.utils.evmore.EvMoreState`.
    """

    def __init__(self, editor):
        self._editor = editor
        # When set, the next captured line is read as the save-before-quit
        # answer rather than as editor input.
        self._save_confirm = False
        # When set, captured lines are appended to the buffer verbatim (no
        # ``:``-command interpretation) until ``:endpaste``. This is the
        # multi-line paste mode; see EvEditor.request_paste_mode.
        self._paste_mode = False

    @rule(Action, phase="before", priority=9999)
    def capture_input(self, action, actor):
        """Seize the next input line, redirecting it into a MenuInputAction the
        :meth:`deliver_input` carry_out rule consumes."""
        if isinstance(action, MenuInputAction):
            return PASS
        if session_mismatch(self._editor._session, actor):
            return PASS
        return REDIRECT(MenuInputAction(raw=action._raw_string, menu=self))

    @rule(MenuInputAction, phase="carry_out", priority=9999)
    def deliver_input(self, action, actor):
        if action.menu is not self:
            return PASS
        self._route(action.raw or "")
        return CLAIM

    def _route(self, raw):
        """Route the captured line to the editor (or resolve a pending save)."""
        editor = self._editor
        if self._paste_mode:
            # In paste mode every line is buffer content, even ``:``-prefixed
            # ones. Only a bare ``:endpaste`` leaves the mode.
            if raw.strip().lower() == ":endpaste":
                self._paste_mode = False
                editor.end_paste_mode()
            else:
                editor.append_paste_line(raw)
            return
        if self._save_confirm:
            # Answering the save-before-quit prompt. Default (empty / anything
            # not 'no') is yes, matching the legacy CmdSaveYesNo behavior.
            self._save_confirm = False
            if raw.strip().lower() in ("no", "n"):
                editor.quit()
            else:
                editor.save_buffer()
                editor.quit()
            return
        editor.handle_input(raw)


# -------------------------------------------------------------
#
# Editor commands
#
# -------------------------------------------------------------


class CmdEditorBase:
    """
    Base parent for editor commands.

    These are plain value-holders, not ``Command`` subclasses: the editor no
    longer routes through the command dispatcher. :meth:`EvEditor.handle_input`
    sets the needed attributes (``caller``, ``args``, ``raw_string``,
    ``cmdstring``) imperatively and calls :meth:`parse`/``func`` directly.
    """

    editor = None

    def parse(self):
        """
        Handles pre-parsing. Editor commands are on the form

        ::

            :cmd [li] [w] [txt]

        Where all arguments are optional.

        - `li`  - line number (int), starting from 1. This could also
              be a range given as <l>:<l>.
        - `w`  - word(s) (string), could be encased in quotes.
        - `txt` - extra text (string), could be encased in quotes.

        """

        editor = self.caller.ndb._eveditor
        if not editor:
            # this will completely replace the editor
            _load_editor(self.caller)
            editor = self.caller.ndb._eveditor
        self.editor = editor

        linebuffer = self.editor.get_buffer().split("\n")

        nlines = len(linebuffer)

        # The regular expression will split the line by whitespaces,
        # stripping extra whitespaces, except if the text is
        # surrounded by single- or double quotes, in which case they
        # will be kept together and extra whitespace preserved. You
        # can input quotes on the line by alternating single and
        # double quotes.
        arglist = [part for part in _RE_GROUP.findall(self.args) if part]
        temp = []
        for arg in arglist:
            # we want to clean the quotes, but only one type,
            # in case we are nesting.
            if arg.startswith('"'):
                arg.strip('"')
            elif arg.startswith("'"):
                arg.strip("'")
            temp.append(arg)
        arglist = temp

        # A dumb split, without grouping quotes
        words = self.args.split()

        # current line number
        cline = nlines - 1

        # the first argument could also be a range of line numbers, on the
        # form <lstart>:<lend>. Either of the ends could be missing, to
        # mean start/end of buffer respectively.

        lstart, lend = cline, cline + 1
        linerange = False
        if arglist and arglist[0].count(":") == 1:
            part1, part2 = arglist[0].split(":")
            lstart = min(max(1, int(part1)), nlines) - 1 if utils.value_is_integer(part1) else 0
            lend = (
                min(max(lstart + 1, int(part2)), nlines)
                if utils.value_is_integer(part2)
                else nlines
            )
            linerange = True
        elif arglist and arglist[0].isdigit():
            lstart = min(max(0, int(arglist[0]) - 1), nlines)
            lend = lstart + 1
            linerange = True
        if linerange:
            arglist = arglist[1:]

        # nicer output formatting of the line range.
        lstr = (
            "line %i" % (lstart + 1)
            if not linerange or lstart + 1 == lend
            else "lines %i-%i" % (lstart + 1, lend)
        )

        # arg1 and arg2 is whatever arguments. Line numbers or -ranges are
        # never included here.
        args = " ".join(arglist)
        arg1, arg2 = "", ""
        if len(arglist) > 1:
            arg1, arg2 = arglist[0], " ".join(arglist[1:])
        else:
            arg1 = " ".join(arglist)

        # store for use in func()

        self.linebuffer = linebuffer
        self.nlines = nlines
        self.arglist = arglist
        self.cline = cline
        self.lstart = lstart
        self.lend = lend
        self.linerange = linerange
        self.lstr = lstr
        self.words = words
        self.args = args
        self.arg1 = arg1
        self.arg2 = arg2

    def insert_raw_string_into_buffer(self):
        """
        Insert a line into the buffer. Used by both CmdLineInput and CmdEditorGroup.

        """
        caller = self.caller
        editor = caller.ndb._eveditor
        buf = editor.get_buffer()

        # add a line of text to buffer
        line = self.raw_string.strip("\r\n")
        if editor._codefunc and editor._indent >= 0:
            # if automatic indentation is active, add spaces
            line = editor.deduce_indent(line, buf)
        buf = line if not buf else buf + "\n%s" % line
        self.editor.update_buffer(buf)
        if self.editor._echo_mode:
            # need to do it here or we will be off one line
            cline = len(self.editor.get_buffer().split("\n"))
            if editor._codefunc:
                # display the current level of identation
                indent = editor._indent
                if indent < 0:
                    indent = "off"

                self.caller.msg("|b%02i|||n (|g%s|n) %s" % (cline, indent, raw(line)))
            else:
                self.caller.msg("|b%02i|||n %s" % (cline, raw(line)))


def _load_editor(caller):
    """
    Load persistent editor from storage.

    """
    saved_options = caller.attributes.get("_eveditor_saved")
    saved_buffer, saved_undo = caller.attributes.get("_eveditor_buffer_temp", (None, None))
    saved_undo_pos = caller.attributes.get("_eveditor_undo_pos", None)
    unsaved = caller.attributes.get("_eveditor_unsaved", False)
    indent = caller.attributes.get("_eveditor_indent", 0)
    if saved_options:
        eveditor = EvEditor(caller, **saved_options[0])
        if saved_buffer:
            # we have to re-save the buffer data so we can handle subsequent restarts
            caller.attributes.add("_eveditor_buffer_temp", (saved_buffer, saved_undo))
            undo_pos = saved_undo_pos if saved_undo_pos is not None else len(saved_undo) - 1
            setattr(eveditor, "_buffer", saved_buffer)
            setattr(eveditor, "_undo_buffer", saved_undo)
            setattr(eveditor, "_undo_pos", undo_pos)
            setattr(eveditor, "_unsaved", unsaved)
            setattr(eveditor, "_indent", indent)
            # Re-persist the restored mutable state: __init__ re-seeded these
            # Attributes to their defaults above, so a *second* reload would
            # otherwise read undo_pos/unsaved/indent as defaults, not the values
            # just restored.
            caller.attributes.add("_eveditor_undo_pos", undo_pos)
            caller.attributes.add("_eveditor_unsaved", unsaved)
            caller.attributes.add("_eveditor_indent", indent)
            # The web frontend already pushed the freshly loaded (last-saved)
            # buffer to the client in __init__; re-push now that the in-progress
            # buffer is restored, or the panel would show stale content.
            if eveditor._frontend == "web":
                eveditor._open_web()
        for key, value in saved_options[1].items():
            setattr(eveditor, key, value)
    else:
        # something went wrong. Cleanup.
        exit_state(capture_holder(caller), EvEditorState)


def rehydrate(holder):
    """Reinstall a persisted EvEditor capture after a server reload.

    Called via the :data:`evennia.actions.state._CAPTURE_REHYDRATORS` seam from
    ``at_post_load``. States are non-persistent by design, so a
    ``persistent=True`` editor opened before a ``@reload`` loses its live
    :class:`EvEditorState` even though its rebuild data survives in Attributes.
    :func:`_load_editor` rebuilds the whole ``EvEditor`` from those Attributes,
    which re-installs the capture state for free.

    Idempotent: no-ops when an editor is already live on ``holder`` (the hook
    fires on every cache load, so this must be safe to call repeatedly).

    Args:
        holder (Object or Account): the body whose persisted editor to restore.
    """
    if getattr(holder.ndb, "_eveditor", None) is not None:
        return
    _load_editor(holder)


class CmdLineInput(CmdEditorBase):
    """
    No command match - Inputs line of text into buffer.

    """

    def func(self):
        """
        Adds the line without any formatting changes.

        If the editor handles code, it might add automatic
        indentation.
        """
        self.insert_raw_string_into_buffer()


class CmdEditorGroup(CmdEditorBase):
    """
    Commands for the editor. Its tokens live in :data:`_EDITOR_TOKENS` and are
    matched by :func:`_match_editor_token`.
    """

    def func(self):
        """
        This command handles all the in-editor :-style commands. Since
        each command is small and very limited, this makes for a more
        efficient presentation.

        """
        caller = self.caller
        editor = caller.ndb._eveditor

        linebuffer = self.linebuffer
        lstart, lend = self.lstart, self.lend
        # preserve the cmdname including case (otherwise uu and UU would be the same)
        cmd = self.raw_string[: len(self.cmdstring)]
        echo_mode = self.editor._echo_mode

        if cmd == ":":
            # Echo buffer
            if self.linerange:
                buf = linebuffer[lstart:lend]
                editor.display_buffer(buf=buf, offset=lstart)
            else:
                editor.display_buffer()
        elif cmd == "::":
            # Echo buffer without the line numbers and syntax parsing
            if self.linerange:
                buf = linebuffer[lstart:lend]
                editor.display_buffer(buf=buf, offset=lstart, linenums=False, options={"raw": True})
            else:
                editor.display_buffer(linenums=False, options={"raw": True})
        elif cmd == ":::":
            # Insert single colon alone on a line
            editor.update_buffer([":"] if lstart == 0 else linebuffer + [":"])
            if echo_mode:
                caller.msg(_("Single ':' added to buffer."))
        elif cmd == ":h":
            # help entry
            editor.display_help()
        elif cmd == ":w":
            # save without quitting
            editor.save_buffer()
        elif cmd == ":wq":
            # save and quit
            editor.save_buffer()
            editor.quit()
        elif cmd == ":q":
            # quit. If not saved, will ask
            if self.editor._unsaved:
                editor.request_save_confirm()
                caller.msg(_("Save before quitting?") + " |lcyes|lt[Y]|le/|lcno|ltN|le")
            else:
                editor.quit()
        elif cmd == ":q!":
            # force quit, not checking saving
            editor.quit()
        elif cmd == ":u":
            # undo
            editor.update_undo(-1)
        elif cmd == ":uu":
            # redo
            editor.update_undo(1)
        elif cmd == ":UU":
            # reset buffer
            editor.update_buffer(editor._pristine_buffer)
            caller.msg(_("Reverted all changes to the buffer back to original state."))
        elif cmd == ":dd":
            # :dd <l> - delete line <l>
            buf = linebuffer[:lstart] + linebuffer[lend:]
            editor.update_buffer(buf)
            caller.msg(_("Deleted {string}.").format(string=self.lstr))
        elif cmd == ":dw":
            # :dw <w> - delete word in entire buffer
            # :dw <l> <w> delete word only on line(s) <l>
            if not self.arg1:
                caller.msg(_("You must give a search word to delete."))
            else:
                if not self.linerange:
                    lstart = 0
                    lend = self.cline + 1
                    caller.msg(
                        _("Removed {arg1} for lines {l1}-{l2}.").format(
                            arg1=self.arg1, l1=lstart + 1, l2=lend + 1
                        )
                    )
                else:
                    caller.msg(
                        _("Removed {arg1} for {line}.").format(arg1=self.arg1, line=self.lstr)
                    )
                sarea = "\n".join(linebuffer[lstart:lend])
                sarea = re.sub(r"%s" % self.arg1.strip("'").strip('"'), "", sarea, re.MULTILINE)
                buf = linebuffer[:lstart] + sarea.split("\n") + linebuffer[lend:]
                editor.update_buffer(buf)
        elif cmd == ":DD":
            # clear buffer
            editor.update_buffer("")

            # Reset indentation level to 0
            if editor._codefunc:
                if editor._indent >= 0:
                    editor._indent = 0
                    if editor._persistent:
                        caller.attributes.add("_eveditor_indent", 0)
            caller.msg(_("Cleared {nlines} lines from buffer.").format(nlines=self.nlines))
        elif cmd == ":y":
            # :y <l> - yank line(s) to copy buffer
            cbuf = linebuffer[lstart:lend]
            editor._copy_buffer = cbuf
            caller.msg(_("{line}, {cbuf} yanked.").format(line=self.lstr.capitalize(), cbuf=cbuf))
        elif cmd == ":x":
            # :x <l> - cut line to copy buffer
            cbuf = linebuffer[lstart:lend]
            editor._copy_buffer = cbuf
            buf = linebuffer[:lstart] + linebuffer[lend:]
            editor.update_buffer(buf)
            caller.msg(_("{line}, {cbuf} cut.").format(line=self.lstr.capitalize(), cbuf=cbuf))
        elif cmd == ":p":
            # :p <l> paste line(s) from copy buffer
            if not editor._copy_buffer:
                caller.msg(_("Copy buffer is empty."))
            else:
                buf = linebuffer[:lstart] + editor._copy_buffer + linebuffer[lstart:]
                editor.update_buffer(buf)
                caller.msg(
                    _("Pasted buffer {cbuf} to {line}.").format(
                        cbuf=editor._copy_buffer, line=self.lstr
                    )
                )
        elif cmd == ":i":
            # :i <l> <txt> - insert new line
            new_lines = self.args.split("\n")
            if not new_lines:
                caller.msg(_("You need to enter a new line and where to insert it."))
            else:
                buf = linebuffer[:lstart] + new_lines + linebuffer[lstart:]
                editor.update_buffer(buf)
                caller.msg(
                    _("Inserted {num} new line(s) at {line}.").format(
                        num=len(new_lines), line=self.lstr
                    )
                )
        elif cmd == ":r":
            # :r <l> <txt> - replace lines
            new_lines = self.args.split("\n")
            if not new_lines:
                caller.msg(_("You need to enter a replacement string."))
            else:
                buf = linebuffer[:lstart] + new_lines + linebuffer[lend:]
                editor.update_buffer(buf)
                caller.msg(
                    _("Replaced {num} line(s) at {line}.").format(
                        num=len(new_lines), line=self.lstr
                    )
                )
        elif cmd == ":I":
            # :I <l> <txt> - insert text at beginning of line(s) <l>
            if not self.raw_string and not editor._codefunc:
                caller.msg(_("You need to enter text to insert."))
            else:
                buf = (
                    linebuffer[:lstart]
                    + ["%s%s" % (self.args, line) for line in linebuffer[lstart:lend]]
                    + linebuffer[lend:]
                )
                editor.update_buffer(buf)
                caller.msg(_("Inserted text at beginning of {line}.").format(line=self.lstr))
        elif cmd == ":A":
            # :A <l> <txt> - append text after end of line(s)
            if not self.args:
                caller.msg(_("You need to enter text to append."))
            else:
                buf = (
                    linebuffer[:lstart]
                    + ["%s%s" % (line, self.args) for line in linebuffer[lstart:lend]]
                    + linebuffer[lend:]
                )
                editor.update_buffer(buf)
                caller.msg(_("Appended text to end of {line}.").format(line=self.lstr))
        elif cmd == ":s":
            # :s <li> <w> <txt> - search and replace words
            # in entire buffer or on certain lines
            if not self.arg1 or not self.arg2:
                caller.msg(_("You must give a search word and something to replace it with."))
            else:
                if not self.linerange:
                    lstart = 0
                    lend = self.cline + 1
                sarea = "\n".join(linebuffer[lstart:lend])

                regex = r"%s|^%s(?=\s)|(?<=\s)%s(?=\s)|^%s$|(?<=\s)%s$"
                regarg = self.arg1.strip("'").strip('"')
                if " " in regarg:
                    regarg = regarg.replace(" ", " +")
                try:
                    sarea = re.sub(
                        regex % (regarg, regarg, regarg, regarg, regarg),
                        self.arg2.strip("'").strip('"'),
                        sarea,
                        re.MULTILINE,
                    )
                except re.error as e:
                    caller.msg(_("Invalid regular expression."))
                else:
                    if not self.linerange:
                        caller.msg(
                            _("Search-replaced {arg1} -> {arg2} for lines {l1}-{l2}.").format(
                                arg1=raw(self.arg1), arg2=raw(self.arg2), l1=lstart + 1, l2=lend
                            )
                        )
                    else:
                        caller.msg(
                            _("Search-replaced {arg1} -> {arg2} for {line}.").format(
                                arg1=raw(self.arg1), arg2=raw(self.arg2), line=self.lstr
                            )
                        )
                buf = linebuffer[:lstart] + sarea.split("\n") + linebuffer[lend:]
                editor.update_buffer(buf)
        elif cmd == ":f":
            # :f <l> flood-fill buffer or <l> lines of buffer.
            # :f <l> =<w> flood-fill buffer or <l> lines of buffer to width <w>.
            width = settings.CLIENT_DEFAULT_WIDTH
            if self.arg1:
                value = self.arg1.lstrip("=")
                if not value.isdigit():
                    self.caller.msg("Width must be a number.")
                    return
                width = int(value)
            if not self.linerange:
                lstart = 0
                lend = self.cline + 1
                caller.msg(_("Flood filled lines {l1}-{l2}.").format(l1=lstart + 1, l2=lend))
            else:
                caller.msg(_("Flood filled {line}.").format(line=self.lstr))
            fbuf = "\n".join(linebuffer[lstart:lend])
            fbuf = fill(fbuf, width=width)
            buf = linebuffer[:lstart] + fbuf.split("\n") + linebuffer[lend:]
            editor.update_buffer(buf)
        elif cmd == ":j":
            # :j <l> <a> =<w> justify buffer of <l> to width <w> with <a> as align (one of
            # f(ull), c(enter), r(ight) or l(left). Default is full.
            align_map = {
                "full": "f",
                "f": "f",
                "center": "c",
                "c": "c",
                "right": "r",
                "r": "r",
                "left": "l",
                "l": "l",
            }
            align_name = {"f": "Full", "c": "Center", "l": "Left", "r": "Right"}
            # shift width arg right if no alignment specified
            if self.arg1.startswith("="):
                self.arg2 = self.arg1
                self.arg1 = None
            if self.arg1 and self.arg1.lower() not in align_map:
                self.caller.msg(
                    _("Valid justifications are")
                    + " [f]ull (default), [c]enter, [r]right or [l]eft"
                )
                return
            align = align_map[self.arg1.lower()] if self.arg1 else "l"
            width = settings.CLIENT_DEFAULT_WIDTH
            if self.arg2:
                value = self.arg2.lstrip("=")
                if not value.isdigit():
                    self.caller.msg("Width must be a number.")
                    return
                width = int(value)
            if not self.linerange:
                lstart = 0
                lend = self.cline + 1
                self.caller.msg(
                    _("{align}-justified lines {l1}-{l2}.").format(
                        align=align_name[align], l1=lstart + 1, l2=lend
                    )
                )
            else:
                self.caller.msg(
                    _("{align}-justified {line}.").format(align=align_name[align], line=self.lstr)
                )
            jbuf = "\n".join(linebuffer[lstart:lend])
            jbuf = justify(jbuf, width=width, align=align)
            buf = linebuffer[:lstart] + jbuf.split("\n") + linebuffer[lend:]
            editor.update_buffer(buf)
        elif cmd == ":fi":
            # :fi <l> indent buffer or lines <l> of buffer.
            indent = " " * 4
            if not self.linerange:
                lstart = 0
                lend = self.cline + 1
                caller.msg(_("Indented lines {l1}-{l2}.").format(l1=lstart + 1, l2=lend))
            else:
                caller.msg(_("Indented {line}.").format(line=self.lstr))
            fbuf = [indent + line for line in linebuffer[lstart:lend]]
            buf = linebuffer[:lstart] + fbuf + linebuffer[lend:]
            editor.update_buffer(buf)
        elif cmd == ":fd":
            # :fi <l> indent buffer or lines <l> of buffer.
            if not self.linerange:
                lstart = 0
                lend = self.cline + 1
                caller.msg(
                    _("Removed left margin (dedented) lines {l1}-{l2}.").format(
                        l1=lstart + 1, l2=lend
                    )
                )
            else:
                caller.msg(_("Removed left margin (dedented) {line}.").format(line=self.lstr))
            fbuf = "\n".join(linebuffer[lstart:lend])
            fbuf = dedent(fbuf)
            buf = linebuffer[:lstart] + fbuf.split("\n") + linebuffer[lend:]
            editor.update_buffer(buf)
        elif cmd == ":paste":
            # enter multi-line paste mode (bulk input, ``:``-lines included)
            editor.request_paste_mode()
        elif cmd == ":endpaste":
            # only meaningful inside paste mode, where EvEditorState consumes it
            caller.msg(_("Not in paste mode."))
        elif cmd == ":echo":
            # set echoing on/off
            editor._echo_mode = not editor._echo_mode
            caller.msg(_("Echo mode set to {mode}").format(mode=editor._echo_mode))
        elif cmd == ":!":
            if editor._codefunc:
                editor._codefunc(caller, editor._buffer)
            else:
                caller.msg(_("This command is only available in code editor mode."))
        elif cmd == ":<":
            # :<
            if editor._codefunc:
                editor.decrease_indent()
                indent = editor._indent
                if indent >= 0:
                    caller.msg(
                        _("Decreased indentation: new indentation is {indent}.").format(
                            indent=indent
                        )
                    )
                else:
                    caller.msg(_("|rManual indentation is OFF.|n Use := to turn it on."))
            else:
                caller.msg(_("This command is only available in code editor mode."))
        elif cmd == ":>":
            # :>
            if editor._codefunc:
                editor.increase_indent()
                indent = editor._indent
                if indent >= 0:
                    caller.msg(
                        _("Increased indentation: new indentation is {indent}.").format(
                            indent=indent
                        )
                    )
                else:
                    caller.msg(_("|rManual indentation is OFF.|n Use := to turn it on."))
            else:
                caller.msg(_("This command is only available in code editor mode."))
        elif cmd == ":=":
            # :=
            if editor._codefunc:
                editor.swap_autoindent()
                indent = editor._indent
                if indent >= 0:
                    caller.msg(_("Auto-indentation turned on."))
                else:
                    caller.msg(_("Auto-indentation turned off."))
            else:
                caller.msg(_("This command is only available in code editor mode."))
        else:
            # no match - insert as line in buffer
            self.insert_raw_string_into_buffer()


# -------------------------------------------------------------
#
# Main Editor object
#
# -------------------------------------------------------------


class EvEditor:
    """
    This defines a line editor object. It creates all relevant commands
    and tracks the current state of the buffer. It also cleans up after
    itself.

    """

    def __init__(
        self,
        caller,
        loadfunc=None,
        savefunc=None,
        quitfunc=None,
        key="",
        persistent=False,
        codefunc=False,
    ):
        """
        Launches a full in-game line editor, mimicking the functionality of VIM.

        Args:
            caller (Object): Who is using the editor.
            loadfunc (callable, optional): This will be called as
                `loadfunc(caller)` when the editor is first started. Its
                return will be used as the editor's starting buffer.
            savefunc (callable, optional): This will be called as
                `savefunc(caller, buffer)` when the save-command is given and
                is used to actually determine where/how result is saved.
                It should return `True` if save was successful and also
                handle any feedback to the user.
            quitfunc (callable, optional): This will optionally be
                called as `quitfunc(caller)` when the editor is
                exited. If defined, it should handle all wanted feedback
                to the user.
            quitfunc_args (tuple, optional): Optional tuple of arguments to
                supply to `quitfunc`.
            key (str, optional): An optional key for naming this
                session and make it unique from other editing sessions.
            persistent (bool, optional): Make the editor survive a reboot. Note
                that if this is set, all callables must be possible to pickle
            codefunc (bool, optional): If given, will run the editor in code mode.
                This will be called as `codefunc(caller, buf)`.

        Notes:
            In persistent mode, all the input callables (savefunc etc)
            must be possible to be *pickled*, this excludes e.g.
            callables that are class methods or functions defined
            dynamically or as part of another function. In
            non-persistent mode no such restrictions exist.



        """
        self._key = key
        self._caller = caller
        self._caller.ndb._eveditor = self
        # Input is captured engine-side (EvEditorState), not via a cmdset. The
        # capture installs on the focus body the engine reads for the next line.
        # Session-agnostic (state.session left None): EvEditor output is
        # broadcast to all of the caller's sessions, so input is not scoped to
        # one session - body scoping (the holder) is enough for the realistic
        # multi-puppet cases.
        self._session = None
        self._holder = capture_holder(caller)
        self._state = None
        # Reusable command instances driven by handle_input via pure string
        # matching of the ":"-commands; neither is merged into dispatch.
        self._group_cmd = CmdEditorGroup()
        self._nomatch_cmd = CmdLineInput()
        self._persistent = persistent
        self._codefunc = codefunc

        if loadfunc:
            self._loadfunc = loadfunc
        else:
            self._loadfunc = lambda caller: ""
        if savefunc:
            self._savefunc = savefunc
        else:
            self._savefunc = lambda caller, buffer: caller.msg(_ERROR_NO_SAVEFUNC)
        if quitfunc:
            self._quitfunc = quitfunc
        else:
            self._quitfunc = lambda caller: caller.msg(_DEFAULT_NO_QUITFUNC)

        # The protocol-agnostic document model (buffer, undo/redo, copy,
        # indentation). This frontend owns display, capture and persistence and
        # delegates all buffer state to the core; the ``_buffer``/``_undo_*``/
        # ``_copy_buffer``/``_pristine_buffer``/``_indent``/``_unsaved``
        # attributes below are properties bound to it, so the command layer and
        # the persistence rehydration path stay unchanged.
        self._core = EditCore(buffer=self.load_buffer(), code_mode=bool(codefunc))

        self._sep = "-"

        # Persistence seeding runs before the frontend fork so a persistent
        # editor records its rehydration substrate regardless of frontend: the
        # ``_eveditor_saved`` marker is what ``rehydrate_captures`` gates on, so
        # a web editor that skipped this block would silently survive nothing
        # across ``@reload``.
        if persistent:
            # save in tuple {kwargs, other options}
            try:
                caller.attributes.add(
                    "_eveditor_saved",
                    (
                        dict(
                            loadfunc=loadfunc,
                            savefunc=savefunc,
                            quitfunc=quitfunc,
                            codefunc=codefunc,
                            key=key,
                            persistent=persistent,
                        ),
                        dict(_pristine_buffer=self._pristine_buffer, _sep=self._sep),
                    ),
                )
                caller.attributes.add("_eveditor_buffer_temp", (self._buffer, self._undo_buffer))
                caller.attributes.add("_eveditor_undo_pos", self._undo_pos)
                caller.attributes.add("_eveditor_unsaved", False)
                caller.attributes.add("_eveditor_indent", 0)
            except Exception as err:
                caller.msg(_ERROR_PERSISTENT_SAVING.format(error=err))
                logger.log_trace(_TRACE_PERSISTENT_SAVING)
                persistent = False
                self._persistent = False

        # Pick the frontend from client capability. A session that announced
        # editor support (see the ``editor_client`` inputfunc) gets the rich web
        # editor: we push the buffer over OOB and do NOT install line capture
        # (the client is modal). Every other client - raw telnet, Mudlet, or a
        # webclient that has not announced support - keeps the line editor, so
        # this is strictly additive.
        self._session_id = None
        self._web_session = self._detect_web_session()
        if self._web_session is not None:
            self._frontend = "web"
            self._session_id = uuid4().hex
            self._open_web()
            return
        self._frontend = "line"

        # Install the engine-native input capture. exit_state first to avoid
        # stacking if a prior editor was still active on this body.
        exit_state(self._holder, EvEditorState)
        self._state = enter_state(self._holder, EvEditorState(self))

        # echo inserted text back to caller
        self._echo_mode = True

        # show the buffer ui
        self.display_buffer()

    # -- document state (delegated to the EditCore) -------------------------
    # These properties keep the legacy attribute surface the command layer
    # (CmdEditorGroup/CmdLineInput) and the persistence rehydration path
    # (_load_editor) read and write directly, while the state itself lives in
    # the protocol-agnostic core.

    @property
    def _buffer(self):
        return self._core.buffer

    @_buffer.setter
    def _buffer(self, value):
        self._core.buffer = value

    @property
    def _pristine_buffer(self):
        return self._core.pristine

    @_pristine_buffer.setter
    def _pristine_buffer(self, value):
        self._core.pristine = value

    @property
    def _unsaved(self):
        return self._core.unsaved

    @_unsaved.setter
    def _unsaved(self, value):
        self._core.unsaved = value

    @property
    def _indent(self):
        return self._core.indent

    @_indent.setter
    def _indent(self, value):
        self._core.indent = value

    @property
    def _copy_buffer(self):
        return self._core.copy_buffer

    @_copy_buffer.setter
    def _copy_buffer(self, value):
        self._core.copy_buffer = value

    @property
    def _undo_buffer(self):
        return self._core.undo_buffer

    @_undo_buffer.setter
    def _undo_buffer(self, value):
        self._core.undo_buffer = value

    @property
    def _undo_pos(self):
        return self._core.undo_pos

    @_undo_pos.setter
    def _undo_pos(self, value):
        self._core.undo_pos = value

    def handle_input(self, raw):
        """Resolve a captured input line and run it against the editor.

        Drives the editor's plain command objects with pure string matching via
        :func:`_match_editor_token` (no cmdset is merged into dispatch). A
        matched ``:``-command runs :class:`CmdEditorGroup`; any other line falls
        through to :class:`CmdLineInput`, which appends it to the buffer.

        Args:
            raw (str): the raw input line the player typed.
        """
        token = _match_editor_token(raw)
        if token is not None:
            cmd = self._group_cmd
            cmdstring = token
            # args sliced from the case-preserving raw line, not the lowercased copy
            args = raw[len(token) :]
        else:
            cmd = self._nomatch_cmd
            cmdstring = ""
            args = ""
        cmd.caller = self._caller
        cmd.cmdstring = cmdstring
        cmd.args = args
        cmd.raw_string = raw
        cmd.editor = None
        cmd.parse()
        cmd.func()

    def request_save_confirm(self):
        """Enter the save-before-quit sub-mode.

        The next captured line is read by :meth:`EvEditorState._route` as the
        yes/no answer instead of as editor input.
        """
        if self._state is not None:
            self._state._save_confirm = True

    def request_paste_mode(self):
        """Enter multi-line paste mode.

        While active, :meth:`EvEditorState._route` appends every captured line
        to the buffer verbatim (bypassing ``:``-command matching and
        auto-indent) until the player enters ``:endpaste``. This is the reliable
        way to bulk-paste text whose lines may start with ``:`` without them
        being read as editor commands, and it silences per-line echo.
        """
        if self._state is None:
            self._caller.msg(_("Paste mode is unavailable right now."))
            return
        self._state._paste_mode = True
        self._paste_lines = []
        self._caller.msg(
            _(
                "|gPaste mode.|n Paste or type your text; lines are added "
                "as-is. Enter |w:endpaste|n on its own line to finish."
            )
        )

    def append_paste_line(self, raw):
        """Collect one raw line during paste mode; applied in end_paste_mode.

        Accumulating and applying the whole block at once keeps paste O(N) rather
        than rebuilding the buffer (and taking an undo snapshot + persistence
        write) per line, which is the point of ``:paste`` for bulk input.
        """
        self._paste_lines.append(raw.rstrip("\r\n"))

    def end_paste_mode(self):
        """Leave paste mode: apply the pasted block in one update and redraw."""
        lines = self._paste_lines
        self._paste_lines = []
        before = self.get_buffer()
        if lines:
            pasted = "\n".join(lines)
            self.update_buffer(pasted if not before else before + "\n" + pasted)
        # count only what actually changed the buffer (a lone empty line into an
        # empty buffer is a no-op).
        count = len(lines) if self.get_buffer() != before else 0
        self._caller.msg(_("Added {count} pasted line(s). Back to the editor.").format(count=count))
        self.display_buffer()

    # -- web frontend -------------------------------------------------------
    # Driven over the editor OOB protocol (editor_open/editor_save/
    # editor_cancel; see evennia/server/inputfuncs.py). The document lives in
    # the shared EditCore, so a web save goes through the same savefunc as ``:w``
    # and telnet parity is preserved.

    def _detect_web_session(self):
        """Return a session that can render the rich editor, or ``None``.

        A session qualifies only once its client has announced editor support
        via the ``editor_client`` inputfunc (the ``CLIENT_EDITOR`` protocol
        flag). Until then every client uses the line editor, so shipping this
        never regresses an un-upgraded client.
        """
        handler = getattr(self._caller, "sessions", None)
        if handler is None:
            return None
        try:
            sessions = list(handler.all())
        except Exception:
            # The no-handler case is guarded above; a fault here is genuine (e.g.
            # a DB error during session recache). Log it, then fall back to the
            # line editor rather than bury it.
            logger.log_trace()
            return None
        for sess in sessions:
            flags = getattr(sess, "protocol_flags", None) or {}
            if flags.get("CLIENT_EDITOR"):
                return sess
        return None

    def _editor_mode(self):
        """Editing mode hint for the client (drives highlighting/preview)."""
        return "code" if self._codefunc else "prose"

    def reopen_web(self, session):
        """Re-push the buffer to a reconnected web client.

        Called from the ``editor_client`` handshake when a client re-announces
        support while a web editor is still live on this body (e.g. after a
        browser refresh). Retargets the frontend at the new session and resends
        ``editor_open`` so the panel returns with its content intact.
        """
        if self._frontend != "web":
            return
        self._web_session = session
        # Mint a fresh id so a stale pre-reopen panel (whose id the client no
        # longer holds) can no longer save or cancel this buffer.
        self._session_id = uuid4().hex
        self._open_web()

    def _open_web(self):
        """Push the buffer to the web client, opening its editor panel."""
        meta = {
            "key": self._key,
            "title": (_("Editing {key}").format(key=self._key) if self._key else _("Editor")),
            "mode": self._editor_mode(),
            "width": settings.CLIENT_DEFAULT_WIDTH,
        }
        self._caller.msg(
            editor_open=([self._session_id, self.get_buffer(), meta], {}),
            session=self._web_session,
        )

    def _close_web(self):
        """Tell the web client to dismiss its editor panel."""
        if self._web_session is not None:
            self._caller.msg(editor_close=([self._session_id], {}), session=self._web_session)

    def _valid_web(self, session_id):
        """Guard: the request must be the web frontend and match its exact id.

        A web editor always mints a non-``None`` id and pushes it to the client
        in ``editor_open``, so a conforming client always echoes it; a missing or
        mismatched id (a stale or second panel) is rejected.
        """
        return self._frontend == "web" and session_id == self._session_id

    def web_save(self, content, session_id=None, close=False):
        """Save buffer content received from the web client.

        Runs the same buffer update and savefunc as the line editor, so the
        two frontends save identically. Optionally closes the panel afterwards.

        A web editor is scoped to one session at a time (``reopen_web`` retargets
        and re-ids on reconnect), so saving is last-write-wins with the panel
        holding the current id: the client sends the full buffer and it replaces
        the server copy wholesale.

        Returns:
            bool: ``True`` if the request was accepted (valid frontend + id),
                ``False`` if it was rejected, so the caller can reconcile a
                drifted client.
        """
        if not self._valid_web(session_id):
            return False
        self.update_buffer(content)
        self.save_buffer()
        if close:
            self.web_cancel(session_id=session_id)
        else:
            self._caller.msg(editor_status=(["saved"], {}), session=self._web_session)
        return True

    def web_cancel(self, session_id=None, discard=False):
        """Close the web editor, running the quit hook (no save).

        Mirrors the line editor's ``:q`` unsaved-guard: with unsaved changes and
        no explicit ``discard``, this does not tear down the editor but sends an
        ``unsaved`` status so the client can confirm before discarding.

        Returns:
            bool: ``True`` if the request was accepted (valid frontend + id),
                ``False`` if it was rejected.
        """
        if not self._valid_web(session_id):
            return False
        if self._unsaved and not discard:
            self._caller.msg(editor_status=(["unsaved"], {}), session=self._web_session)
            return True
        self._close_web()
        self.quit()
        return True

    def load_buffer(self):
        """
        Load the buffer using the load function hook.

        Returns:
            str: the loaded buffer content (used to seed the :class:`EditCore`).
                Returns an empty string if the load hook raised.
        """
        try:
            buffer = self._loadfunc(self._caller)
            if not isinstance(buffer, str):
                self._caller.msg(
                    f"|rBuffer is of type |w{type(buffer)})|r. "
                    "Continuing, it is converted to a string "
                    "(and will be saved as such)!|n"
                )
                buffer = to_str(buffer)
            return buffer
        except Exception as e:
            from evennia.utils import logger

            logger.log_trace()
            self._caller.msg(_ERROR_LOADFUNC.format(error=e))
            return ""

    def get_buffer(self):
        """
        Return:
            buffer (str): The current buffer.

        """
        return self._core.get_buffer()

    def _persist_editor_state(self):
        """Write the mutable editor state to Attributes (persistent editors only).

        Captures the buffer, undo history *and position*, unsaved flag and indent
        so a reload restores the editor exactly where it was, mid-undo-history
        included. A no-op for non-persistent editors.
        """
        if not self._persistent:
            return
        self._caller.attributes.add(
            "_eveditor_buffer_temp", (self._core.buffer, self._core.undo_buffer)
        )
        self._caller.attributes.add("_eveditor_undo_pos", self._core.undo_pos)
        self._caller.attributes.add("_eveditor_unsaved", self._core.unsaved)
        self._caller.attributes.add("_eveditor_indent", self._core.indent)

    def update_buffer(self, buf):
        """
        This should be called when the buffer has been changed
        somehow.  It will handle unsaved flag and undo updating.

        Args:
            buf (str): The text to update the buffer with.

        """
        if self._core.set_buffer(buf):
            self._persist_editor_state()

    def quit(self):
        """
        Cleanly exit the editor.

        """
        try:
            self._quitfunc(self._caller)
        except Exception as e:
            self._caller.msg(_ERROR_QUITFUNC.format(error=e))
        self._caller.nattributes.remove("_eveditor")
        self._caller.attributes.remove("_eveditor_buffer_temp")
        self._caller.attributes.remove("_eveditor_undo_pos")
        self._caller.attributes.remove("_eveditor_saved")
        self._caller.attributes.remove("_eveditor_unsaved")
        self._caller.attributes.remove("_eveditor_indent")
        exit_state(self._holder, EvEditorState)

    def save_buffer(self):
        """
        Saves the content of the buffer.

        """
        if self._unsaved or self._codefunc:
            # always save code - this allows us to tie execution to
            # saving if we want.
            try:
                if self._savefunc(self._caller, self._buffer):
                    # Save codes should return a true value to indicate
                    # save worked. The saving function is responsible for
                    # any status messages.
                    self._unsaved = False
            except Exception as e:
                self._caller.msg(_ERROR_SAVEFUNC.format(error=e))
        else:
            self._caller.msg(_MSG_SAVE_NO_CHANGE)

    def update_undo(self, step=None):
        """
        This updates the undo position.

        Args:
            step (int, optional): The amount of steps
                to progress the undo position to. This
                may be a negative value for undo and
                a positive value for redo.

        """
        token = self._core.navigate_undo(step)
        if token in (UNDO_OK, REDO_OK):
            # the buffer moved to a historical position; persist it so a reload
            # restores that position, not the last recorded tip.
            self._persist_editor_state()
        if token == UNDO_NONE:
            self._caller.msg(_MSG_NO_UNDO)
        elif token == UNDO_OK:
            self._caller.msg(_MSG_UNDO)
        elif token == REDO_NONE:
            self._caller.msg(_MSG_NO_REDO)
        elif token == REDO_OK:
            self._caller.msg(_MSG_REDO)

    def display_buffer(self, buf=None, offset=0, linenums=True, options={"raw": False}):
        """
        This displays the line editor buffer, or selected parts of it.

        Args:
            buf (str, optional): The buffer or part of buffer to display.
            offset (int, optional): If `buf` is set and is not the full buffer,
                `offset` should define the actual starting line number, to
                get the linenum display right.
            linenums (bool, optional): Show line numbers in buffer.
            options: raw (bool, optional): Tell protocol to not parse
                formatting information.

        """
        if buf is None:
            buf = self._buffer
        if is_iter(buf):
            buf = "\n".join(buf)

        lines = buf.split("\n")
        nlines = len(lines)
        nwords = len(buf.split())
        nchars = len(buf)

        # The ``::`` command asks for an unparsed, copy-paste-friendly view; in
        # that mode we emit no clickable markup (it would show as literal codes).
        rich = not options.get("raw")

        sep = self._sep
        header = (
            "|n"
            + sep * 10
            + _("Line Editor [{name}]").format(name=self._key)
            + sep * (settings.CLIENT_DEFAULT_WIDTH - 24 - len(self._key))
        )
        help_hint = _("(:h for help)")
        footer = (
            "|n"
            + sep * 10
            + "[l:%02i w:%03i c:%04i]" % (nlines, nwords, nchars)
            + sep * 12
            + (_clickable(":h", help_hint) if rich else help_hint)
            + sep * (settings.CLIENT_DEFAULT_WIDTH - 54)
        )
        if linenums:
            main = "\n".join(
                "|b%02i|||n %s" % (iline + 1 + offset, raw(line))
                for iline, line in enumerate(lines)
            )
        else:
            main = "\n".join([raw(line) for line in lines])
        string = "%s\n%s\n%s" % (header, main, footer)
        if rich:
            string += "\n" + self._toolbar()
        self._caller.msg(string, options=options)

    def _toolbar(self):
        """Build the clickable control bar shown under the buffer.

        Each control sends a complete editor command, so it is safe to fire on a
        single click. In raw telnet the :func:`_clickable` markup strips away and
        the controls read as a plain ``[Save] [Quit] ...`` hint line.
        """
        sep = self._sep
        controls = [
            _clickable(":w", _("[Save]")),
            _clickable(":wq", _("[Save&Quit]")),
            _clickable(":q", _("[Quit]")),
            _clickable(":u", _("[Undo]")),
            _clickable(":uu", _("[Redo]")),
            _clickable(":paste", _("[Paste]")),
            _clickable(":h", _("[Help]")),
        ]
        return "|n" + sep * 3 + " " + "  ".join(controls) + " " + sep * 3

    def display_help(self):
        """
        Shows the help entry for the editor.

        """
        string = self._sep * settings.CLIENT_DEFAULT_WIDTH + _HELP_TEXT
        if self._codefunc:
            string += _HELP_CODE
        string += _HELP_LEGEND + self._sep * settings.CLIENT_DEFAULT_WIDTH
        self._caller.msg(string)

    def deduce_indent(self, line, buffer):
        """
        Try to deduce the level of indentation of the given line.

        """
        line, changed = self._core.deduce_indent(line, buffer)
        if changed and self._persistent:
            self._caller.attributes.add("_eveditor_indent", self._core.indent)
        return line

    def decrease_indent(self):
        """Decrease automatic indentation by 1 level."""
        if self._core.decrease_indent() and self._persistent:
            self._caller.attributes.add("_eveditor_indent", self._core.indent)

    def increase_indent(self):
        """Increase automatic indentation by 1 level."""
        if self._core.increase_indent() and self._persistent:
            self._caller.attributes.add("_eveditor_indent", self._core.indent)

    def swap_autoindent(self):
        """Swap automatic indentation on or off."""
        if self._core.swap_autoindent() and self._persistent:
            self._caller.attributes.add("_eveditor_indent", self._core.indent)
