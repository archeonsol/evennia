"""EditCore: protocol-agnostic document model for the in-game editor.

Holds the editor's buffer, undo/redo history, copy buffer and indentation, and
exposes them through **pure** operations that perform no session I/O and no
persistence. Mutating methods return status values (a changed-flag or a small
status token) that a *frontend* turns into player feedback and persistence
writes.

This is the shared seam described in the engine-architecture editor design
(``.agents/docs/engine-architecture/editor.md``): the telnet line frontend, the
Mudlet/GMCP frontend and the webclient frontend all drive the same core, so
undo/redo and buffer semantics are identical across transports. Frontends own
everything that needs a session (``caller.msg``, ``caller.attributes``, capture
state); the core owns only the document.

The buffer is a single string with ``\\n`` line separators, matching the legacy
:class:`~evennia.utils.eveditor.EvEditor` contract. The load/save/quit callbacks
stay on the frontend because they take a ``caller``; the core is deliberately
unaware of who is editing.
"""

from evennia.utils import is_iter

# navigate_undo status tokens. The frontend maps these to player-facing messages
# so the wording (and its i18n) stays a transport concern.
UNDO_OK = "undo"
UNDO_NONE = "none_undo"
REDO_OK = "redo"
REDO_NONE = "none_redo"

# Auto-indent deduction tables (code mode only). Kept identical to the legacy
# EvEditor behaviour.
_INDENT_KEYWORDS = {
    "elif ": ["if "],
    "else:": ["if ", "try"],
    "except": ["try:"],
    "finally:": ["try:"],
}
_INDENT_OPENING_TAGS = ("if ", "try:", "for ", "while ")


class EditCore:
    """Pure buffer model shared by every editor frontend.

    Args:
        buffer (str): initial buffer content.
        code_mode (bool): whether the editor is in code mode (enables the
            auto-indent operations). Mirrors ``bool(codefunc)`` on the frontend.
        undo_max (int): maximum number of redo steps to keep reachable.
    """

    def __init__(self, buffer="", code_mode=False, undo_max=20):
        self.buffer = buffer
        # The buffer as first loaded; :UU reverts to this.
        self.pristine = buffer
        self.code_mode = code_mode
        self.unsaved = False
        self.indent = 0
        self.undo_buffer = [buffer]
        self.undo_pos = 0
        self.undo_max = undo_max
        self.copy_buffer = []

    # -- buffer -------------------------------------------------------------

    def get_buffer(self):
        """Return the current buffer string."""
        return self.buffer

    def set_buffer(self, buf):
        """Replace the buffer content.

        Accepts a string or an iterable of lines (joined with ``\\n``). On an
        actual change this records an undo step and marks the buffer unsaved.

        Args:
            buf (str or iterable): new buffer content.

        Returns:
            bool: ``True`` if the buffer changed, ``False`` if it was identical
                (a frontend uses this to gate persistence writes and, for the
                no-op save case, feedback).
        """
        if is_iter(buf):
            buf = "\n".join(buf)
        if buf != self.buffer:
            self.buffer = buf
            self._record_undo()
            self.unsaved = True
            return True
        return False

    # -- undo / redo --------------------------------------------------------

    def _record_undo(self):
        """Append the current buffer to the undo history if it is new.

        Truncates any redo tail past the current position first, so editing
        after an undo discards the redo branch (standard undo semantics).
        """
        if not self.undo_buffer or self.buffer != self.undo_buffer[self.undo_pos]:
            self.undo_buffer = self.undo_buffer[: self.undo_pos + 1] + [self.buffer]
            # Cap history at undo_max so undo and redo share one bound: drop the
            # oldest entries rather than letting undo grow unbounded while redo
            # stays capped (which would strand the buffer mid-history).
            if len(self.undo_buffer) > self.undo_max:
                self.undo_buffer = self.undo_buffer[-self.undo_max :]
            self.undo_pos = len(self.undo_buffer) - 1

    def navigate_undo(self, step):
        """Move through the undo history.

        Args:
            step (int): negative to undo, positive to redo. ``0``/``None`` only
                re-records the current buffer (kept for symmetry; the frontend
                does not use it).

        Returns:
            str or None: one of :data:`UNDO_OK`, :data:`UNDO_NONE`,
                :data:`REDO_OK`, :data:`REDO_NONE`, or ``None`` when no
                navigation was requested. The frontend maps this to a message.
        """
        token = None
        if step and step < 0:
            # undo
            if self.undo_pos <= 0:
                token = UNDO_NONE
            else:
                self.undo_pos = max(0, self.undo_pos + step)
                self.buffer = self.undo_buffer[self.undo_pos]
                token = UNDO_OK
        elif step and step > 0:
            # redo (undo_buffer is bounded by undo_max in _record_undo, so its
            # length is the only cap needed here)
            if self.undo_pos >= len(self.undo_buffer) - 1:
                token = REDO_NONE
            else:
                self.undo_pos = min(self.undo_pos + step, len(self.undo_buffer) - 1)
                self.buffer = self.undo_buffer[self.undo_pos]
                token = REDO_OK
        self._record_undo()
        return token

    # -- indentation (code mode) -------------------------------------------

    def deduce_indent(self, line, buffer):
        """Deduce the auto-indentation for a freshly typed code line.

        Mirrors the legacy EvEditor logic: keywords like ``elif``/``else`` align
        to their opening block, and opening tags (``if``/``for``/...) bump the
        indent level for the following lines.

        Args:
            line (str): the raw line being inserted.
            buffer (str): the current buffer (searched backwards for block tags).

        Returns:
            tuple: ``(line, changed)`` where ``line`` has the deduced leading
                whitespace prepended and ``changed`` is ``True`` when the stored
                indent level moved (so the frontend can persist it).
        """
        indent = self.indent
        changed = False
        if any(line.startswith(kw) for kw in _INDENT_KEYWORDS):
            keyword = [kw for kw in _INDENT_KEYWORDS if line.startswith(kw)][0]
            begin_tags = _INDENT_KEYWORDS[keyword]
            for oline in reversed(buffer.splitlines()):
                if any(oline.lstrip(" ").startswith(tag) for tag in begin_tags):
                    indent = (len(oline) - len(oline.lstrip(" "))) // 4
                    break
            self.indent = indent + 1
            changed = True
        elif any(line.startswith(kw) for kw in _INDENT_OPENING_TAGS):
            self.indent = indent + 1
            changed = True

        line = " " * 4 * indent + line
        return line, changed

    def decrease_indent(self):
        """Decrease auto-indentation by one level (code mode only).

        Returns:
            bool: ``True`` if the level changed.
        """
        if self.code_mode and self.indent > 0:
            self.indent -= 1
            return True
        return False

    def increase_indent(self):
        """Increase auto-indentation by one level (code mode only).

        Returns:
            bool: ``True`` if the level changed.
        """
        if self.code_mode and self.indent >= 0:
            self.indent += 1
            return True
        return False

    def swap_autoindent(self):
        """Toggle auto-indentation on/off (code mode only).

        Off is represented by an indent level of ``-1``.

        Returns:
            bool: ``True`` if in code mode (and thus toggled).
        """
        if self.code_mode:
            self.indent = -1 if self.indent >= 0 else 0
            return True
        return False
