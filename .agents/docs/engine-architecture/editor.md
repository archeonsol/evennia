# Editor: hybrid rich text editor (design)

Status: **Phase 1 + Phase 2 landed; Mudlet/GMCP tier (Phase 4) still design.**
Replaces [`evennia/utils/eveditor.py`](../../../evennia/utils/eveditor.py) as the
*primary* editing surface while keeping full parity for line-mode clients.
Sequenced with W1 (web/protocol) and dependent on R1 (display pipeline); see
[committed.md](committed.md).

## Implementation status

- **Phase 1 (core + telnet parity): done.** `EditCore` pure document model at
  [`evennia/utils/editor/core.py`](../../../evennia/utils/editor/core.py); the
  legacy `EvEditor` is now the line frontend delegating buffer state to it via
  properties (command layer + persistence path untouched). Telnet uplift shipped:
  capability-aware clickable controls (`|lc|lt|le`, degrade to plain text in raw
  telnet) and a `:paste`/`:endpaste` multi-line mode. Covered by
  `evennia/utils/tests/test_eveditor.py` + `test_editor_core.py`.
- **Phase 2 (web frontend): landed, pending live browser verification.** Editor
  OOB protocol (`editor_client` / `editor_save` / `editor_cancel` inputfuncs in
  [`evennia/server/inputfuncs.py`](../../../evennia/server/inputfuncs.py);
  `editor_open` / `editor_close` / `editor_status` outbound from `EvEditor`).
  Capability handshake: a session is web-routed only after its client sets the
  `CLIENT_EDITOR` flag, so un-upgraded clients keep the line editor. Client:
  Underspire's **custom webclient** (not Golden Layout) gets a modal overlay
  (`web/static/webclient/js/editor.js` + `css/editor.css`, wired via
  `web/templates/webclient/webclient.html`) with a textarea baseline, synced
  gutter, live Evennia-markup preview, and CodeMirror 6 as a **flag-gated**
  (`window.EDITOR_USE_CODEMIRROR`) progressive enhancement.
- **Phase 3 (web polish): landed.** Reconnect rehydration (`editor_client`
  re-announce re-sends `editor_open` to the new session via `EvEditor.reopen_web`
  - tested). Diff-before-save (client-side line-level LCS diff of the loaded vs
  edited buffer, shown on Save & Close). Per-mode snippet library. Keybindings
  (Ctrl+S save, Ctrl+Enter save & close, Esc cancel, Tab).
- **Phase 4 (Mudlet/GMCP pane): deferred, not built.** Rationale: the MXP
  clickable controls in the line editor already give Mudlet a rich experience
  with zero client setup. A GMCP editor pane needs a distributable Mudlet package
  that does not exist, so emitting server-side GMCP now would be speculative code
  with no consumer. Revisit if/when such a package is shipped.
- **Reconciliation:** the design's Golden Layout pane assumed the stock
  webclient. mootest ships a bespoke terminal that bypasses the stock plugin
  system, so the web editor is a standalone modal overlay wired to
  `Evennia.emitter`, not a GL component. Protocol and core are unchanged.

### Open follow-ups

- **Live browser verification** of the full round-trip (open/save/cancel,
  preview, modal focus) — not exercisable from the test harness.
- **`evennia collectstatic`** must run so `editor.js`/`editor.css` are served.
- **`EVENNIA_REF` bump**: the new inputfuncs are engine symbols; production must
  pin an `archeonsol/evennia` tag that ships them or the OOB handlers 404. See
  the engine-pin section below and [[feedback_engine_pin_mismatch]].
- **Reconnect rehydration** of an open web editor (re-send `editor_open`) is not
  yet handled; a browser refresh mid-edit drops the panel (editor stays on ndb,
  harmlessly replaced by the next launch).

Supersedes the standalone proposal at `D:/moo/.cursor/plans/eveditor_replacement_proposal_568451ed.plan.md`
and folds in the Mudlet/MXP tier that plan omitted. Where the two disagreed, the
resolutions are noted inline under **Reconciliation**.

## Problem

EvEditor is a 1310-line VI-style **line** editor. In this fork it has already been
migrated off cmdsets onto the action/state engine (`EvEditorState(StateProvider)`,
priority 9999, persistent across `@reload`). What it has *not* done is notice that
the portal grew a rich-client toolkit underneath it: GMCP/MSDP (`telnet_oob.py`,
`gmcp_utils.py`), MXP, the JSON wire format (`wire_formats/json_standard.py`), the
AJAX + websocket webclient, and the React `matrix` app. EvEditor speaks
lowest-common-denominator ANSI to a single session and ignores all of it.

The editor conflates three concerns that must separate:

1. **Buffer model** — lines, undo/redo, search/replace, fill/justify.
2. **Command grammar** — the `:w` / `:dd` / `:s` VI tokens (`_EDITOR_TOKENS`).
3. **Rendering + transport** — raw ANSI lines to one session via `caller.msg()`.

A full delete-and-replace is wrong: it discards the battle-tested persistence /
rehydration story and breaks every line-mode builder. The move is to **split the
core out and negotiate the frontend per session.**

## Shape: one core, capability-negotiated frontends

```
          EditCore  (no I/O)
          - document model (lines or rich blocks)
          - ops: insert / delete / replace / format
          - undo/redo stack, dirty tracking
          - load/save/quit hooks (existing signatures)
                     |
        emits ops + state deltas; frontend chosen from session.protocol_flags
        ┌────────────┼─────────────────────────┐
   TelnetLine     RichTelnet                WebEditor
   (VI parity,    (Mudlet: GMCP pane        (webclient/React:
    MXP links,     + MXP send-links)         structured OOB to a
    viewport)                                CodeMirror 6 panel)
```

`EditCore` owns the document and **never touches a session directly.** It emits
ops and state deltas; a frontend renders them and feeds player input back through
the *same op interface* the VI grammar drives. Undo/redo and save therefore behave
identically regardless of frontend. This is the R1 constraint applied locally:
structured all the way to the delivery boundary, string flattening only inside a
frontend.

Frontend is selected at `enter_state` time from `session.protocol_flags`
(`GMCP`, `MXP`, `CLIENTNAME`, `SCREENWIDTH`). One invocation, three experiences,
one save path.

### Frontend 1 — TelnetLine (parity, improved)

Keeps the full VI grammar so nothing regresses over raw telnet. Wire-free wins:

- **Windowed line-numbered viewport** redrawn on a range instead of dumping the
  whole buffer.
- **MXP send-links** when `protocol_flags["MXP"]`: line numbers become clickable
  `:i N` / `:dd N` targets; degrades silently to plain text when MXP is off. This
  alone makes the *same* editor far nicer in Mudlet/MUSHclient with no second
  frontend.
- Word-wrap preview at the client's real `SCREENWIDTH`.
- `:paste` sub-mode (read lines until `:endpaste`) — the biggest line-mode pain
  point, carried over from the prior plan.

### Frontend 2 — RichTelnet (Mudlet)

For `GMCP` + `CLIENTNAME=Mudlet`: push the buffer as a GMCP package
(`Editor.Open` / `Editor.Patch`) so a Mudlet package script renders it in a
miniconsole/pane, command channel still over normal input. Optional and additive;
same core, richer sink. **This tier is the addition over the prior plan**, which
treated Mudlet as just another line client.

### Frontend 3 — WebEditor (the headline)

Webclient/React session: the core streams the document as structured data over
Evennia's **native OOB outputfunc** (`editor_open`), not GMCP. GMCP is for
third-party clients; the webclient's wire format is `["cmdname", args, kwargs]`,
so the idiomatic path is a paired outbound message + inputfuncs, following the
existing `html` plugin pattern. A CodeMirror 6 panel (Golden Layout component)
gives syntax highlight, live markup preview, find/replace UI, multi-caret,
unbounded undo, and click-to-edit. Input returns as structured ops
(`editor_save` / `editor_cancel` inputfuncs), applied through the same op
interface.

**Reconciliation (transport):** my first pass folded webclient + Mudlet into one
"GMCP/JSON package." Wrong for the webclient. The prior plan's `editor_open` /
`editor_save` / `editor_cancel` outputfunc+inputfunc design is correct and is
adopted. GMCP is reserved for the Mudlet tier only.

## Wire contract

**Server to webclient** (new outbound, `html`-plugin pattern):

```python
caller.msg(editor_open=(
    session_id,          # uuid, validated against caller.ndb._editor_session
    content,             # initial buffer from loadfunc
    {"mode": "prose",    # prose | help | code
     "key": "char/desc", "title": "Editing description",
     "width": settings.CLIENT_DEFAULT_WIDTH, "readonly": False},
))
```

**Webclient to server** (new inputfuncs in
[`server/inputfuncs.py`](../../../evennia/server/inputfuncs.py), alongside
`text` / `default`):

```python
def editor_save(session, session_id, content, **kwargs): ...
def editor_cancel(session, session_id, **kwargs): ...
```

**Mudlet tier:** `Editor.Open {id,title,mode,lines[],caret,readonly}`,
`Editor.Patch {id,ops[]}`, `Editor.Close {id}` outbound; `Editor.Op {id,ops[]}`,
`Editor.Cmd {id,name,args}` inbound. Telnet-line clients never negotiate either
package and fall through to Frontend 1.

While a rich frontend is open, `EvEditorState` is **not** installed for that
session (no line capture); v1 is modal in the web pane to avoid stray commands
mid-edit. Other sessions on the account get a status line.

## Editor engine and MUD-specific features (web)

**CodeMirror 6** is the single engine for prose and code both: modular (~150-300 KB),
`@codemirror/lang-python` for `@py`-style code, custom Lezer highlighter for
Evennia markup (`|r`, `|n`, `|b`). Monaco is a later lazy-load for code only if
IntelliSense against Evennia stubs is ever wanted; not phase 1.

The differentiator is MUD integration, not the widget:

- **Mode-aware** (`prose` / `help` / `code`): highlighting, right-pane, and
  snippets switch on mode. `@desc/edit` opens `prose`, `@py/edit` opens `code`,
  set automatically by the launching command.
- **Live markup preview** through the **R1 render pipeline**, not a client-side
  reimplementation. The buffer renders to the same `RenderNode` a player would
  receive, so builders see exactly what players see. When R1 is not yet available
  for a given path, fall back to the existing `text2html.js` logic, but the target
  is one renderer.
- **Diff-before-save**: unified diff of `loadfunc` result vs new buffer on the web
  Save step (critical for shared descs / help). Line mode keeps `:w` immediate.
- Multi-line paste, find/replace with live highlight, undo beyond the line
  editor's 20-step cap.

## Compatibility and migration

- **Public API unchanged.** Wrap the core in an `EvEditor(caller, loadfunc,
  savefunc, quitfunc, key, persistent, code)`-signature shim. The shim opens the
  core and picks the frontend. No call site changes.
- **Real consumers in mootest** keep their `loadfunc` / `savefunc` / `quitfunc`
  contract untouched:
  - [`world/building/desc.py`](../../../../mootest/world/building/desc.py)
    (`_desc_load` / `_desc_save`, roomstate-aware `add_desc`).
  - [`world/documents/document_flow.py`](../../../../mootest/world/documents/document_flow.py)
    (per-page add/edit, `writing_session` on `char.ndb`, quitfunc re-enters the
    menu).
- **Persistence is already solved.** `EvEditorState` survives `@reload` today; the
  core inherits it. Frontends are non-persistent and re-negotiate on reconnect
  (correct: a browser tab or Mudlet pane must re-handshake regardless). Web
  mid-edit at `@reload` re-sends `editor_open` with the saved buffer on reconnect.
- **Picklable-callback constraint** for persistent mode is unchanged (top-level
  functions only).

## Engine pin

Per the mootest `CLAUDE.md` engine-pin rule: this adds new `evennia.*` symbols
(the core, new inputfuncs, an OOB outbound, possibly new session hooks). When
mootest adopts it, `EVENNIA_REF` in `.github/workflows/deploy.yml` must bump to a
fork tag that actually ships the core, or production silently falls back to the
stock line editor with no failure surface beyond a `server.log` traceback. This is
exactly the class of change that pin guards. See [[feedback_engine_pin_mismatch]].

## Phasing

1. **Core + parity.** Extract `EditCore` from EvEditor internals; `EvEditor` shim
   delegates. TelnetLine frontend reaches parity (+ viewport, MXP links, `:paste`).
   No web yet. Existing `test_eveditor.py` stays green.
2. **WebEditor MVP.** `editor_open` / `editor_save` / `editor_cancel` protocol;
   CodeMirror 6 panel (plain text, Save/Cancel); auto-route webclient -> web,
   else -> line. Protocol round-trip tests.
3. **Mode-aware web UX.** Evennia-markup highlighter, live preview via R1, Python
   mode, diff-before-save.
4. **RichTelnet (Mudlet)** GMCP package + MXP tier; snippet library; reconnection
   rehydration; building/document flows tested end to end.

## Success criteria

1. `@desc/edit` on the webclient opens a split-pane editor with live preview; Save
   calls the existing `savefunc`.
2. The same command on telnet opens the line editor with unchanged `:w` / `:q`.
3. Mudlet gets clickable line targets (MXP) with zero server-side branch beyond
   capability negotiation.
4. All `test_eveditor.py` tests pass; new protocol tests added.
5. Zero changes at EvEditor call sites.
