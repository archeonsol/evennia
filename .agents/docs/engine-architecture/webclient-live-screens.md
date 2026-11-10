# Azaban: live view and game screens

A game can show a running program's text screen in the shell, such as an old
arcade game. The picture is a live view, not a record, so it takes its own path
through the Portal. Part of the runtime contract in
[`webclient-protocol-runtime.md`](webclient-protocol-runtime.md).

Code: `evennia/server/portal/webclient.py` (`LIVE_EVENTS`),
`evennia/web/webclient/client/src/lib/screens.svelte.ts` (the store),
`evennia/web/webclient/client/src/components/ScreenPanel.svelte` (the panel).

## Live view frames

Most frames are a record: they carry `s`, they go into the replay buffer, and a
client that comes back gets them again. A frame that only shows what is on a
screen right now is not a record. A replayed picture of a game from ten seconds
ago, painted over the live one, is wrong, and a burst of them would push real
output out of the buffer. `LIVE_EVENTS` in `webclient.py` names the oob events
that are live view: today `screen_frame`.

A live frame is written straight to the socket. It is **not** stamped, **not**
buffered, **not** batched, and it does not move the sequence counter. Anything
batched before it is flushed first, so order holds. A socket that is gone drops
it, and the client asks again: after a reconnect the shell sends `screen_sync`
and the game answers with a full picture. The size limit
(`WEBSOCKET_MAX_OUTGOING_BYTES`) still applies. Events that mark a state change
(`screen_end`, `ui_component`, `ui_remove`) are ordinary records and replay.

To add an event, put its name in `LIVE_EVENTS`, register it like any other, and
give the client a way to ask for the current state again. A live event with no
way to ask is lost for good on every dropped socket.

## The `screen` capability

A client that declares `caps.screen` can show a program's text screen. The game
sends `ui_component` with `type: "screen"` (`id`, `title`, `mode`, `size`,
`controls`, `dismissible`), then `screen_frame` (`id`, `data`: terminal bytes as
text) as the program draws, and `screen_end` (`id`, `headline`, `why`) when the
run is over. The client sends `oob` actions in the `screen` namespace:
`screen_key` (`id`, `data`: the characters a key made) and `screen_sync` (`id`:
send the whole picture). Both actions must be on the game's
`AZABAN_PUBLIC_ACTIONS` list.

`screen_end` carries no result. The score and the places are the game's to tell
in its own log text, in the voice of the rest of the game, so the panel has no
result view of its own.

## The panel

The panel is a tile like a puppet's terminal: the game's title in the head, a
one-word state beside a Close command, and the program's screen below. The state
reads "Click to play", "Playing", or at the end the `headline`. The panel draws
the program and nothing else. A game's score and status line are the program's
own, and the panel adds no key list, card or colour of its own.

- The terminal is xterm.js at the program's size, scaled to fit the panel. It
  takes the shell's ground and ink and follows a theme change. Its sixteen
  colours are a set that reads on a dark ground, since a program picks its
  colours for black.
- While the screen has focus every key goes to the game, and the border lights.
  `Shift+Esc` moves the keyboard out of the game and is not sent.
- `controls` are read to a screen reader when the screen opens. xterm's screen
  reader mode follows the shell's screen reader setting.
- At `screen_end` the panel keeps the last picture, dims it, shows the headline
  and stops sending keys. Building the terminal must not track how the run ended:
  that would rebuild the terminal at the end and blank the picture.
  `ScreenStore.attach` reads it, so the panel calls `attach` untracked.
- A new panel and a reconnect send `screen_sync`. A finished screen is not asked
  for again.
- A panel restored from a saved layout with no game behind it closes itself
  after a few seconds.

Tests: `TestLiveFrames` in `evennia/server/portal/test_webclient_transport.py`,
`src/lib/screens.test.ts` (the store) and `tests/screen.ts` (a browser page that
runs the real panel and xterm). The runner presses real keys on that page, since
a terminal reads a space from the key press and not from the key down.
