# Editor: hybrid rich text editor (design)

Status: **Phase 1-3 landed; Mudlet/GMCP tier (Phase 4) still design.** Replaces
[`evennia/utils/eveditor.py`](../../../evennia/utils/eveditor.py) as the *primary*
editing surface while keeping full parity for line-mode clients. Sequenced with W1
(web/protocol) and dependent on R1 (display pipeline); see
[committed.md](committed.md).

## Problem

EvEditor is a ~1300-line VI-style **line** editor. In this fork it already
migrated off cmdsets onto the action/state engine (`EvEditorState(StateProvider)`,
priority 9999, persistent across `@reload`). What it never did is notice the portal
grew a rich-client toolkit underneath it: GMCP/MSDP, MXP, the JSON wire format
(`evennia/server/portal/wire_formats/json_standard.py`), the AJAX + websocket
webclient. EvEditor speaks lowest-common-denominator ANSI to a single session and
ignores all of it.

It conflates three concerns that must separate: **buffer model** (lines,
undo/redo, search/replace, fill/justify); **command grammar** (the `:w` / `:dd` /
`:s` VI tokens); **rendering + transport** (raw ANSI to one session via
`caller.msg()`). A full delete-and-replace is wrong: it discards the battle-tested
persistence / rehydration story and breaks every line-mode builder. The move is to
**split the core out and negotiate the frontend per session** (the R1 constraint
applied locally: structured to the delivery boundary, string flattening only in a
frontend).

## Shape: one core, capability-negotiated frontends

`EditCore` owns the document (lines or rich blocks, ops, undo/redo, dirty
tracking, load/save/quit hooks) and **never touches a session directly.** It emits
ops + state deltas; a frontend renders them and feeds player input back through the
*same op interface* the VI grammar drives, so undo/redo and save behave identically
regardless of frontend. The frontend is selected at `enter_state` time from
`session.protocol_flags` (`GMCP`, `MXP`, `CLIENTNAME`, `SCREENWIDTH`): one
invocation, three experiences, one save path:

- **TelnetLine (parity, improved)**: full VI grammar so nothing regresses over raw
  telnet. Windowed line-numbered viewport; **MXP send-links** (clickable `:i N` /
  `:dd N`); word-wrap preview at real `SCREENWIDTH`; `:paste`/`:endpaste` sub-mode.
- **RichTelnet (Mudlet)**, for `GMCP` + `CLIENTNAME=Mudlet`: push the buffer as a
  GMCP package (`Editor.Open` / `Editor.Patch`) rendered in a Mudlet pane. Optional,
  additive, same core.
- **WebEditor (the headline)**, a webclient session: the core streams the document
  over Evennia's **native OOB outputfunc**, not GMCP (GMCP is for third-party
  clients; the webclient wire format is `["cmdname", args, kwargs]`, so a paired
  outbound message + inputfuncs is idiomatic). A CodeMirror 6 panel gives markup
  preview, find/replace, multi-caret, unbounded undo.

## Wire contract

**Server → webclient** (new outbound, `html`-plugin pattern): a
`caller.msg(editor_open=(session_id, content, meta))` tuple, where `session_id` is
a uuid validated against `caller.ndb._editor_session`, `content` the buffer from
`loadfunc`, and `meta` carries `mode` (`prose`/`help`/`code`), `key`, `title`,
`width`, `readonly`.

**Webclient → server** (new inputfuncs in
[`evennia/server/inputfuncs.py`](../../../evennia/server/inputfuncs.py)):
`editor_save(session, session_id, content, **kwargs)` and
`editor_cancel(session, session_id, **kwargs)`.

**Mudlet tier:** `Editor.Open {id,title,mode,lines[],caret,readonly}`,
`Editor.Patch {id,ops[]}`, `Editor.Close {id}` outbound; `Editor.Op`, `Editor.Cmd`
inbound. Telnet-line clients negotiate neither and fall through to TelnetLine.

While a rich frontend is open, `EvEditorState` is **not** installed for that
session (no line capture); v1 is modal in the web pane. Other sessions on the
account get a status line.

## MUD-specific web features (the differentiator, not the widget)

**CodeMirror 6** is the single engine for prose and code (custom Lezer highlighter
for Evennia markup). The value is MUD integration: **mode-aware** (`prose`/`help`/
`code`) highlighting/panes/snippets set by the launching command; **live markup
preview through the R1 render pipeline** (not a client-side reimplementation) so
builders see what players see, falling back to `text2html.js` where R1 is not yet
available; **diff-before-save** (unified diff of `loadfunc` vs new buffer, critical
for shared descs / help; line mode keeps `:w` immediate).

## Compatibility and migration

- **Public API unchanged.** An `EvEditor(caller, loadfunc, savefunc, quitfunc,
  key, persistent, code)`-signature shim opens the core and picks the frontend. No
  call-site changes. Downstream game consumers (description editing, multi-page
  document flows) keep their `loadfunc` / `savefunc` / `quitfunc` contract
  untouched.
- **Persistence is already solved.** `EvEditorState` survives `@reload`; the core
  inherits it. Frontends are non-persistent and re-negotiate on reconnect; web
  mid-edit at `@reload` re-sends `editor_open` with the saved buffer.
- **Picklable-callback constraint** for persistent mode is unchanged (top-level
  functions only).

## Engine pin

This adds new `evennia.*` symbols (the core, new inputfuncs, an OOB outbound). When
the downstream game adopts it, its `EVENNIA_REF` pin must bump to a fork tag that
ships the core, or production silently falls back to the stock line editor with no
failure surface beyond a `server.log` traceback. See [[feedback_engine_pin_mismatch]].

## Phasing

1. **Core + parity.** Extract `EditCore`; `EvEditor` shim delegates. TelnetLine
   reaches parity (+ viewport, MXP links, `:paste`).
2. **WebEditor MVP.** `editor_open`/`editor_save`/`editor_cancel` protocol;
   CodeMirror panel; auto-route webclient → web, else → line.
3. **Mode-aware web UX.** Markup highlighter, live preview via R1, Python mode,
   diff-before-save.
4. **RichTelnet (Mudlet)** GMCP package + MXP tier. **Deferred:** MXP line-editor
   controls already give Mudlet a rich experience with zero setup, and a GMCP pane
   needs a distributable Mudlet package that does not exist, so emitting GMCP now is
   speculative code with no consumer. Revisit when such a package ships.

## Success criteria

`@desc/edit` opens a split-pane web editor with live preview (Save calls the
existing `savefunc`); the same command on telnet opens the unchanged line editor;
Mudlet gets clickable MXP line targets with no server-side branch beyond capability
negotiation; all editor tests pass with new protocol tests added; zero changes at
EvEditor call sites.
